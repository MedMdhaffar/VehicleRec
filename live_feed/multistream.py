"""multistream.py — one process, all cameras, batched GPU inference.

Promotes spike_batched_cam.py into production:
  - Models: loads vehicle TRT engine + plate/تونس YOLO + PaddleOCR once
  - MultiStreamDriver: owns a FrameSource + BYTETracker + CameraPipeline per camera,
    runs the 8-step batched tick loop
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

import cv2
import numpy as np

import live_feed.config as cfg
import plate_recognition.config as pcfg
from common.config import TAXONOMY
from live_feed.dataset_collector import DatasetCollector
from live_feed.livelog import LiveLogger
from live_feed.perf import PerfMeter
from live_feed.pipeline import CameraPipeline, PlateResult
from live_feed.roi import RoiConfig
from live_feed.vehicle_runtime import (
    IMGSZ,
    TrackBoxes as _TrackBoxes,
    create_vehicle_tracker,
    load_vehicle_model,
)
from vms_client import open_source, FrameSource
from live_feed.live_sources import build_vms_config, resolve_live_specs

log = logging.getLogger(__name__)

# ── OCR batch helper (returns text + confidence per crop) ─────────────────────

def _parse_ocr_result(r) -> tuple[str, float]:
    """Extract (normalized_text, mean_confidence) from one PaddleOCR output."""
    d = r if isinstance(r, dict) else (getattr(r, "json", None) or {})
    if isinstance(d, dict) and isinstance(d.get("res"), dict):
        d = d["res"]
    text = ""
    conf = 0.0
    if isinstance(d, dict):
        if d.get("rec_text"):
            text = d["rec_text"]
            conf = float(d.get("rec_score", 0.0) or d.get("score", 0.0) or 0.0)
        elif d.get("rec_texts"):
            texts = d["rec_texts"]
            scores = d.get("rec_scores", [])
            text = " ".join(texts)
            conf = float(sum(scores) / len(scores)) if scores else 0.0
        elif d.get("text"):
            text = d["text"]
            conf = float(d.get("score", 0.0) or 0.0)
    elif isinstance(r, (list, tuple)) and len(r) >= 1:
        item = r[0]
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = str(item[0])
            try:
                conf = float(item[1])
            except (TypeError, ValueError):
                conf = 0.0
    normalized = re.sub(r"[^0-9A-Za-z]", "", text or "")
    return normalized, conf


def _ocr_batch(ocr_reader, crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """Run OCR on a batch of crops; return (text, conf) per crop."""
    if not crops:
        return []
    try:
        out = list(ocr_reader.predict(crops, batch_size=len(crops)))
    except TypeError:
        out = [list(ocr_reader.predict(c))[0] for c in crops]
    return [_parse_ocr_result(o) for o in out]


# ── Models — loaded once, shared across all cameras ──────────────────────────

class Models:
    def __init__(self) -> None:
        from ultralytics import YOLO

        self.vehicle, _ = load_vehicle_model()

        self.plate  = YOLO(str(pcfg.PLATE_DETECTOR_PATH), task="detect")
        self.tounes = YOLO(str(pcfg.WORD_DETECTOR_PATH),  task="detect")
        self.ocr    = self._load_ocr()
        log.info("[models] all models loaded")

    @staticmethod
    def _load_ocr():
        try:
            from paddleocr import TextRecognition
            eng = TextRecognition(model_name=pcfg.OCR_REC_MODEL, engine="transformers")
            log.info("[models] OCR: PaddleOCR TextRecognition (transformers)")
            return eng
        except Exception as exc:
            log.info("[models] transformers OCR unavailable (%s); PaddleOCR pipeline fallback", exc)
            from paddleocr import PaddleOCR
            return PaddleOCR(
                text_recognition_model_name=pcfg.OCR_REC_MODEL,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )



# ── MultiStreamDriver ─────────────────────────────────────────────────────────

class MultiStreamDriver:
    """Owns ONE set of models and runs all cameras batched in a single loop."""

    def __init__(
        self,
        cameras: list[dict],
        models: Models,
        on_emit: Callable[[str, list, list], None],
    ) -> None:
        self._cameras = cameras
        self._models  = models
        self._on_emit = on_emit

        self._sources:   dict[str, FrameSource]       = {}
        self._vms_cfg = build_vms_config()
        self._specs   = resolve_live_specs(cameras, self._vms_cfg)
        self._trackers:  dict[str, BYTETracker]      = {}
        self._pipelines: dict[str, CameraPipeline]   = {}
        self._perfs:     dict[str, PerfMeter]         = {}
        self._livelogs:  dict[str, LiveLogger]        = {}
        self._frame_idx: dict[str, int]               = {}

        # Published state read by encoder threads / endpoints
        self._latest_frames: dict[str, np.ndarray | None] = {}
        self._frame_locks:   dict[str, threading.Lock]    = {}
        self._profile_locks: dict[str, threading.Lock]    = {}

        for cam in cameras:
            cam_id = cam["id"]
            perf = PerfMeter()
            pipeline = CameraPipeline(
                cam_id=cam_id,
                perf=perf,
                plate_model=models.plate,
                ocr_reader=models.ocr,
                tounes_model=models.tounes,
            )
            # Load ROI config if it exists
            roi_cfg = RoiConfig.load(cam_id)
            pipeline.set_roi(roi_cfg, time.monotonic())

            self._perfs[cam_id]     = perf
            self._livelogs[cam_id]  = LiveLogger(cam_id)
            self._pipelines[cam_id] = pipeline
            self._trackers[cam_id]  = create_vehicle_tracker(frame_rate=cfg.FPS)
            self._frame_idx[cam_id] = 0
            self._latest_frames[cam_id] = None
            self._frame_locks[cam_id]   = threading.Lock()
            self._profile_locks[cam_id] = threading.Lock()

        # Training-data collector (shared, one writer thread for all cameras)
        self._collector = DatasetCollector(
            service=cfg.DATASET_SERVICE_TAG,
            root=cfg.DATASET_ROOT,
            enabled=cfg.DATASET_COLLECT_ENABLED,
            interval_sec=cfg.DATASET_INTERVAL_SEC,
            jpeg_quality=cfg.DATASET_JPEG_QUALITY,
            class_names=TAXONOMY,
        )

    def start(self) -> None:
        """Open and start a vms_client FrameSource per camera."""
        for cam in self._cameras:
            cam_id = cam["id"]
            try:
                src = open_source(self._specs[cam_id], self._vms_cfg)
                src.start()
                self._sources[cam_id] = src
                log.info("[driver] cam %s (%s) source started", cam_id, cam["type"])
            except Exception as exc:
                log.error("[driver] cam %s source failed to start: %s", cam_id, exc)

    def stop(self) -> None:
        """Stop all frame sources (each tears down its own stream + VMS session)."""
        for cam_id, src in self._sources.items():
            try:
                src.stop()
            except Exception:
                pass
        try:
            self._collector.stop()
        except Exception:
            pass

    def run(self, stop_evt: threading.Event) -> None:
        """The main tick loop. Call from a dedicated thread."""
        log.info("[driver] tick loop started (%d cameras)", len(self._cameras))
        while not stop_evt.is_set():
            # ── Step 1: grab latest frame per camera ─────────────────────────
            ready: dict[str, tuple[np.ndarray, float]] = {}
            for cam_id, src in self._sources.items():
                try:
                    frame, ts = src.get_latest()
                    if frame is not None:
                        ready[cam_id] = (frame, ts)
                except Exception as exc:
                    log.debug("[driver] cam %s get_latest error: %s", cam_id, exc)
            if not ready:
                time.sleep(0.005)
                continue

            cam_ids = list(ready.keys())
            frames  = [ready[c][0] for c in cam_ids]

            # ── Step 2: BATCHED vehicle detect ────────────────────────────────
            try:
                dets = self._models.vehicle.predict(
                    frames, conf=cfg.TRACK_CONF, imgsz=IMGSZ, verbose=False,
                )
            except Exception as exc:
                log.warning("[driver] vehicle predict failed: %s", exc)
                continue

            # ── Step 3: per-camera BYTETracker update ─────────────────────────
            tracked: dict[str, _TrackBoxes] = {}
            for cam_id, frame, det in zip(cam_ids, frames, dets):
                try:
                    boxes = self._trackers[cam_id].update(det.boxes.cpu().numpy(), frame)
                    tracked[cam_id] = _TrackBoxes(boxes)
                except Exception as exc:
                    log.debug("[driver] cam %s tracker error: %s", cam_id, exc)
                    tracked[cam_id] = _TrackBoxes(np.zeros((0, 8)))
                # dataset: full frame + vehicle boxes (throttled per camera)
                try:
                    self._collector.save_vehicle_frame(
                        cam_id, frame, det.boxes, self._frame_idx[cam_id],
                    )
                except Exception:
                    log.debug("[driver] cam %s vehicle dataset save failed", cam_id)

            # ── Step 4: per-camera ingest → collect eligible crops ─────────────
            all_eligible: list[tuple[str, int, np.ndarray]] = []  # (cam_id, track_id, vcrop)
            for cam_id in cam_ids:
                frame_idx = self._frame_idx[cam_id]
                try:
                    eligible = self._pipelines[cam_id].ingest(
                        ready[cam_id][0], tracked[cam_id], frame_idx,
                    )
                    for track_id, vcrop in eligible:
                        all_eligible.append((cam_id, track_id, vcrop))
                except Exception as exc:
                    log.warning("[driver] cam %s ingest error: %s", cam_id, exc)

            # ── Step 5: BATCHED plate detect over all eligible vehicle crops ───
            plate_items: list[tuple[str, int, np.ndarray]] = []  # (cam_id, track_id, plate_crop)
            if all_eligible:
                vcrops = [vc for _, _, vc in all_eligible]
                try:
                    plate_dets = self._models.plate.predict(
                        vcrops, conf=pcfg.PLATE_CONF, imgsz=pcfg.DETECT_IMGSZ, verbose=False,
                    )
                    for (cam_id, track_id, vcrop), pr in zip(all_eligible, plate_dets):
                        plate_box = None   # for the plate-detect dataset (None → negative example)
                        if pr.boxes is not None and len(pr.boxes):
                            bi = int(pr.boxes.conf.argmax())
                            px1, py1, px2, py2 = (int(v) for v in pr.boxes.xyxy[bi].tolist())
                            if (px2 - px1) >= cfg.MIN_PLATE_W:
                                px1, py1 = max(0, px1), max(0, py1)
                                pc = vcrop[py1:py2, px1:px2]
                                if pc.size:
                                    plate_items.append((cam_id, track_id, pc))
                                    plate_box = (px1, py1, px2, py2)
                        # dataset: vehicle crop (plate model input) + plate box, every read_zone capture
                        try:
                            self._collector.save_plate_detect(cam_id, vcrop, plate_box, track_id)
                        except Exception:
                            log.debug("[driver] cam %s plate dataset save failed", cam_id)
                except Exception as exc:
                    log.warning("[driver] plate detect failed: %s", exc)

            # ── Step 6: BATCHED OCR + BATCHED تونس ────────────────────────────
            plate_result_map: dict[str, dict[int, PlateResult]] = {c: {} for c in cam_ids}
            if plate_items:
                pcs = [pc for _, _, pc in plate_items]
                try:
                    ocr_results = _ocr_batch(self._models.ocr, pcs)
                except Exception as exc:
                    log.warning("[driver] OCR batch failed: %s", exc)
                    ocr_results = [("", 0.0)] * len(pcs)
                try:
                    stretched = [
                        cv2.resize(pc, (pcfg.WORD_IMGSZ, pcfg.WORD_IMGSZ)) for pc in pcs
                    ]
                    tounes_dets = self._models.tounes.predict(
                        stretched, conf=pcfg.WORD_CONF, imgsz=pcfg.WORD_IMGSZ, verbose=False,
                    )
                except Exception as exc:
                    log.warning("[driver] تونس batch failed: %s", exc)
                    tounes_dets = [None] * len(pcs)

                for (cam_id, track_id, pc), (text, dconf), tr in zip(
                    plate_items, ocr_results, tounes_dets
                ):
                    if tr is not None and tr.boxes is not None and len(tr.boxes):
                        ptype      = "tounes"
                        type_conf  = float(tr.boxes.conf.max())
                    else:
                        ptype      = "other"
                        type_conf  = 0.0
                    # dataset: plate crop → tounes/ or other/ (classification)
                    try:
                        self._collector.save_plate_type(cam_id, pc, ptype, track_id)
                    except Exception:
                        log.debug("[driver] cam %s plate-type dataset save failed", cam_id)
                    plate_result_map[cam_id][track_id] = PlateResult(
                        text=text, digits_conf=dconf,
                        plate_type=ptype, type_conf=type_conf,
                        plate_crop=pc,            # carry the plate image → profile → review agent
                    )

            # ── Step 7: per-camera apply_plate_results + log + emit ──────────
            for cam_id in cam_ids:
                frame_idx = self._frame_idx[cam_id]
                try:
                    with self._profile_locks[cam_id]:
                        emitted, zone_events = self._pipelines[cam_id].apply_plate_results(
                            plate_result_map.get(cam_id, {}), frame_idx,
                        )
                    self._perfs[cam_id].tick(ready[cam_id][1])
                    for p in emitted:
                        try:
                            self._livelogs[cam_id].log_event(p, p.status)
                        except Exception:
                            pass
                    self._on_emit(cam_id, emitted, zone_events)
                except Exception as exc:
                    log.warning("[driver] cam %s apply_plate_results error: %s", cam_id, exc)

            # ── Step 8: publish latest frame + advance frame index ─────────────
            for cam_id in cam_ids:
                with self._frame_locks[cam_id]:
                    self._latest_frames[cam_id] = ready[cam_id][0]
                self._frame_idx[cam_id] += 1

    # ── Public API for encoder threads / endpoints ────────────────────────────

    def latest_frame(self, cam_id: str) -> np.ndarray | None:
        with self._frame_locks[cam_id]:
            return self._latest_frames.get(cam_id)

    def active_profiles(self, cam_id: str) -> dict:
        with self._profile_locks[cam_id]:
            return dict(self._pipelines[cam_id].active_profiles)

    def perf(self, cam_id: str) -> PerfMeter:
        return self._perfs[cam_id]

    def reload_roi(self, cam_id: str, started_at: float) -> None:
        roi_cfg = RoiConfig.load(cam_id)
        with self._profile_locks[cam_id]:
            self._pipelines[cam_id].set_roi(roi_cfg, started_at)

    def set_roi(self, cam_id: str, roi_config: RoiConfig, started_at: float) -> None:
        with self._profile_locks[cam_id]:
            self._pipelines[cam_id].set_roi(roi_config, started_at)

    def get_roi_analytics(self, cam_id: str):
        """Return the RoiAnalytics instance for this camera (may be None)."""
        return self._pipelines[cam_id]._roi

    def get_roi_config(self, cam_id: str):
        """Return the RoiConfig for this camera (from the analytics instance, or None)."""
        roi = self._pipelines[cam_id]._roi
        return roi.config if roi else None
