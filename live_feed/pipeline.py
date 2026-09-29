from __future__ import annotations

import logging
import math
import time
from collections import Counter
from typing import NamedTuple

import numpy as np

from common.colour import estimate_colour_detailed
from common.config import TAXONOMY
from live_feed.colour_temporal import (
    compute_colour_crop_quality,
    compute_colour_crop_quality_details,
    decide_colour,
    make_colour_observation,
)
from live_feed.config import (
    ANCHOR_EXPIRE_FRAMES, DEDUP_ENABLED, DEDUP_IOU,
    K_CLASSIFY, LOST_FRAMES, MAX_OCR, MIN_PLATE_W, MOVE_THRESH_PX,
    OCR_COOLDOWN, OCR_LOCK_CONF, STATIONARY_MAX_OCR, STATIONARY_N,
)
from live_feed.perf import PerfMeter
from live_feed.roi import RoiAnalytics, RoiConfig, foot_point, iou, point_in_polygon
from live_feed.schemas import PlateInfo, VehicleProfile

log = logging.getLogger(__name__)


class PlateResult(NamedTuple):
    text: str
    digits_conf: float
    plate_type: str   # "tounes" | "other"
    type_conf: float
    plate_crop: "np.ndarray | None" = None   # the plate image — saved for the review agent


def _crop(frame: np.ndarray, bbox: list[int]) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    return frame[y1:y2, x1:x2]


def _majority(votes: list[str]) -> str:
    if not votes:
        return "unknown"
    return Counter(votes).most_common(1)[0][0]


def _apply_colour_decision(profile: VehicleProfile, decision) -> None:
    evidence = decision.evidence
    profile._colour_candidate = evidence.winner
    profile._colour_winner_share = evidence.winner_share
    profile._colour_runner_up = evidence.runner_up
    profile._colour_runner_up_share = evidence.runner_up_share
    profile._colour_margin = evidence.winner_margin
    profile._colour_decision = decision.reason
    if decision.finalized:
        profile.colour = decision.label
        profile.colour_conf = round(decision.confidence, 4)
        profile._colour_finalized = True


def _observe_colour(
    profile: VehicleProfile,
    crop: np.ndarray,
    frame: np.ndarray,
    perf: PerfMeter,
) -> None:
    quality_details = compute_colour_crop_quality_details(crop, frame.shape, profile.bbox)
    quality = quality_details.combined
    profile._colour_attempts += 1
    try:
        with perf.stage("colour"):
            estimate = estimate_colour_detailed(crop)
            label, confidence = estimate.label, estimate.confidence
    except Exception as exc:
        log.warning("HSV colour failed track_id=%s: %s", profile.track_id, exc)
        label, confidence = "error", 0.0

    if not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
        confidence = 0.0
    diagnostics = estimate.diagnostics() if "estimate" in locals() else {}
    diagnostics["crop_quality"] = {
        "crop_size": [int(crop.shape[1]), int(crop.shape[0])],
        **quality_details.__dict__,
    }
    observation = make_colour_observation(label, float(confidence), quality, diagnostics)
    profile._last_colour_attempt = observation
    if not observation.valid:
        if label == "unknown":
            profile._unknown_colour_samples += 1
        return

    profile._colour_samples.append(observation)
    _apply_colour_decision(profile, decide_colour(profile._colour_samples))


def _finalize_colour(profile: VehicleProfile) -> None:
    if not profile._colour_finalized:
        _apply_colour_decision(profile, decide_colour(profile._colour_samples, finalizing=True))


class CameraPipeline:
    """Per-camera tracking state + plate cascade.

    In the batched architecture:
      - ingest() runs the per-box logic and returns vehicle crops eligible for plate detect.
      - apply_plate_results() receives the batched OCR/plate-type results from the driver,
        finalizes plate state, runs ROI analytics, and emits finalized profiles.
    """

    def __init__(
        self,
        cam_id: str,
        perf: PerfMeter,
        plate_model,
        ocr_reader,
        tounes_model,
        review_writer=None,
    ) -> None:
        self._cam_id = cam_id
        self._perf = perf
        self._plate_model = plate_model   # stored for reference; not called here — driver batches
        self._ocr_reader = ocr_reader
        self._tounes_model = tounes_model
        self._active: dict[int, VehicleProfile] = {}
        self._roi: RoiAnalytics | None = None
        self._anchors: list[dict] = []
        # state carried from ingest → apply_plate_results within one tick
        self._last_frame: np.ndarray | None = None
        self._last_seen_ids: set[int] = set()
        self._last_ts: float = 0.0
        self._eligible_parked: dict[int, bool] = {}  # track_id → was_parked
        if review_writer is None:
            from live_feed.review_agent.dataset import ReviewInputWriter
            review_writer = ReviewInputWriter(cam_id=cam_id)
        self._review_writer = review_writer

    @property
    def active_profiles(self) -> dict[int, VehicleProfile]:
        return self._active

    def set_roi(self, config: RoiConfig, started_at: float) -> None:
        if self._roi is None:
            self._roi = RoiAnalytics(config, started_at)
        else:
            self._roi.reload(config, started_at)

    # ── Tick split: ingest → (driver batches plate/OCR/تونس) → apply_plate_results ──

    def ingest(
        self,
        frame: np.ndarray,
        tracked_boxes,
        frame_idx: int,
    ) -> list[tuple[int, np.ndarray]]:
        """Process tracked boxes for one frame.

        Updates per-track profiles (best-crop, motion, classify-once, eligibility).
        Returns list of (track_id, vehicle_crop) for tracks eligible for plate detect
        this tick. The driver collects these across all cameras and runs batched
        plate detect + OCR + تونس, then calls apply_plate_results.
        """
        now = time.monotonic()
        self._last_frame = frame
        self._last_ts = now
        seen_ids: set[int] = set()
        eligible: list[tuple[int, np.ndarray]] = []
        self._eligible_parked = {}

        if tracked_boxes is not None and tracked_boxes.id is not None and len(tracked_boxes) > 0:
            ids   = tracked_boxes.id.int().tolist()
            xyxy  = tracked_boxes.xyxy.int().tolist()
            clss  = tracked_boxes.cls.int().tolist()
            confs = (tracked_boxes.conf.tolist()
                     if hasattr(tracked_boxes, "conf") else [1.0] * len(ids))

            for track_id, (x1, y1, x2, y2), cls_id, conf_val in zip(ids, xyxy, clss, confs):
                bbox = [x1, y1, x2, y2]
                seen_ids.add(track_id)

                if track_id not in self._active:
                    self._active[track_id] = VehicleProfile(
                        track_id=track_id,
                        bbox=bbox,
                        first_seen=now,
                        last_seen=now,
                        _last_seen_frame=frame_idx,
                    )

                profile = self._active[track_id]
                profile.bbox      = bbox
                profile.last_seen = now
                profile.frames_seen += 1
                profile._last_seen_frame = frame_idx

                crop = _crop(frame, bbox)
                if crop.size == 0:
                    continue

                # ── Best-crop capture ─────────────────────────────────────────
                score = (x2 - x1) * (y2 - y1)
                if score > profile._best_score:
                    profile._best_score = score
                    profile._best_crop  = crop.copy()

                # ── Dedup anchor refresh ──────────────────────────────────────
                if DEDUP_ENABLED:
                    for a in self._anchors:
                        if iou(bbox, a["bbox"]) >= DEDUP_IOU:
                            a["last_frame"] = frame_idx

                # ── Motion: stationary detection + re-arm ─────────────────────
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                if profile._last_centroid is not None:
                    disp = ((cx - profile._last_centroid[0]) ** 2
                            + (cy - profile._last_centroid[1]) ** 2) ** 0.5
                    if disp < MOVE_THRESH_PX:
                        profile._stationary_frames += 1
                    else:
                        if profile._stationary_frames >= STATIONARY_N and not profile.plate.locked:
                            profile.plate.ocr_attempts = 0
                        profile._stationary_frames = 0
                profile._last_centroid = (cx, cy)

                # ── Vehicle type: preserve the existing two-frame behavior ────
                if not profile._type_locked:
                    profile._type_votes.append(TAXONOMY.get(cls_id, "unknown"))
                    if profile.frames_seen >= K_CLASSIFY:
                        profile.type = _majority(profile._type_votes)
                        profile.type_conf = conf_val
                        profile._type_locked = True
                else:
                    profile.type_conf = conf_val

                # ── Colour: valid HSV observations, independent of frames ─────
                if not profile._colour_finalized:
                    _observe_colour(profile, crop, frame, self._perf)

                # ── Plate eligibility (state machine gate) ────────────────────
                parked      = profile._stationary_frames >= STATIONARY_N
                eff_max_ocr = STATIONARY_MAX_OCR if parked else MAX_OCR
                resolved    = profile.plate.locked or profile.plate.ocr_attempts >= eff_max_ocr
                if self._roi is not None:
                    read_zones = self._roi.config.polygons_with_function("read_zone")
                    in_read_zone = (not read_zones) or any(
                        point_in_polygon(foot_point(bbox), z.points) for z in read_zones
                    )
                else:
                    in_read_zone = True
                ocr_eligible = (
                    in_read_zone
                    and not resolved
                    and (frame_idx - profile._last_ocr_frame) >= OCR_COOLDOWN
                )
                if ocr_eligible:
                    profile._last_ocr_frame = frame_idx
                    eligible.append((track_id, crop.copy()))
                    self._eligible_parked[track_id] = parked

        self._last_seen_ids = seen_ids
        return eligible

    def apply_plate_results(
        self,
        plate_results: dict[int, PlateResult],
        frame_idx: int,
    ) -> tuple[list[VehicleProfile], list[dict]]:
        """Consume batched plate/OCR/تونس results, finalize tracks, run ROI.

        plate_results maps track_id → PlateResult for eligible tracks where the
        driver found a plate crop and ran OCR. Tracks absent from plate_results
        but present in _eligible_parked got no readable plate this tick.
        """
        emitted: list[VehicleProfile] = []
        now = self._last_ts

        # ── Apply plate/OCR results ───────────────────────────────────────────
        for track_id, result in plate_results.items():
            profile = self._active.get(track_id)
            if profile is None:
                continue
            profile.plate.ocr_attempts += 1
            self._perf.inc_plate_call()
            self._perf.inc_ocr()

            # keep the widest plate crop for the review agent (independent of OCR conf,
            # so even an unreadable-but-detected plate gets an image for human review)
            if result.plate_crop is not None and result.plate_crop.size:
                w = result.plate_crop.shape[1]
                if w > profile._best_plate_w:
                    profile._best_plate_w    = float(w)
                    profile._best_plate_crop = result.plate_crop.copy()   # detach from the vehicle-crop array

            if result.digits_conf > profile.plate.digits_conf:
                profile.plate.number      = result.text or None
                profile.plate.digits_conf = result.digits_conf
                profile.plate.type        = result.plate_type
                profile.plate.type_conf   = result.type_conf
            if result.digits_conf >= OCR_LOCK_CONF:
                profile.plate.locked = True

        # Parked tracks that were eligible but got no plate → spend the attempt
        for track_id, parked in self._eligible_parked.items():
            if parked and track_id not in plate_results:
                profile = self._active.get(track_id)
                if profile is not None:
                    profile.plate.ocr_attempts += 1

        # Early emit on plate lock
        for profile in self._active.values():
            if profile.plate.locked and not profile._plate_early_emitted:
                profile._plate_early_emitted = True
                emitted.append(profile)

        # ── ROI analytics (occupancy + line counting) ─────────────────────────
        zone_events: list[dict] = []
        if self._roi is not None:
            zone_events = self._roi.process_frame(self._active, frame_idx, now)

        # ── Finalize lost tracks ──────────────────────────────────────────────
        seen_ids = self._last_seen_ids
        lost_ids = [
            tid for tid, p in self._active.items()
            if tid not in seen_ids and (frame_idx - p._last_seen_frame) > LOST_FRAMES
        ]
        for tid in lost_ids:
            profile = self._active.pop(tid)
            _finalize_colour(profile)
            profile.status = "final"

            is_dup = False
            if DEDUP_ENABLED:
                for a in self._anchors:
                    if (frame_idx - a["last_frame"]) <= ANCHOR_EXPIRE_FRAMES \
                            and a["type"] == profile.type \
                            and iou(profile.bbox, a["bbox"]) >= DEDUP_IOU:
                        a["last_frame"] = frame_idx
                        is_dup = True
                        break

            if not is_dup:
                rid = None
                try:
                    rid = self._review_writer.write(profile)
                except Exception:
                    log.warning("review_writer failed track_id=%s cam=%s", tid, self._cam_id)
                if DEDUP_ENABLED and rid is not None:
                    self._anchors.append(
                        {"bbox": profile.bbox, "type": profile.type, "last_frame": frame_idx}
                    )
            emitted.append(profile)

        # Prune expired anchors
        if DEDUP_ENABLED and self._anchors:
            self._anchors = [
                a for a in self._anchors
                if (frame_idx - a["last_frame"]) <= ANCHOR_EXPIRE_FRAMES
            ]

        self._perf.set_active_tracks(len(self._active))
        self._perf.frame_done()
        return emitted, zone_events
