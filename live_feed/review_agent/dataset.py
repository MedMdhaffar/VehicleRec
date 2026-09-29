"""dataset.py — two responsibilities:

1. ReviewInputWriter  (called by pipeline on every finalize)
   Saves the best vehicle/plate crops and appends one ReviewInput JSONL line.

2. write_outputs / read_review_input  (called by worker after LLM review)
   Routes ReviewedRecord to corrected_events / review_queue and builds
   the retraining dataset tree.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import json
import cv2

from live_feed.review_agent import config as cfg
from live_feed.review_agent.cursor import next_record_id
from live_feed.review_agent.schemas import ReviewedRecord, ReviewInput

if TYPE_CHECKING:
    from live_feed.schemas import VehicleProfile

log = logging.getLogger(__name__)


# ── ReviewInputWriter ─────────────────────────────────────────────────────────

class ReviewInputWriter:
    """Called by CameraPipeline on every finalized track. Must never raise."""

    def __init__(self, cam_id: str) -> None:
        self._cam_id = cam_id
        self._jsonl = cfg.LOGS_DIR / f"ch{cam_id}_review_input.jsonl"
        self._crops_dir = cfg.CROPS_DIR / f"ch{cam_id}"
        self._crops_dir.mkdir(parents=True, exist_ok=True)

    def write(self, profile: "VehicleProfile") -> int | None:
        """Write a review-input record. Returns the record_id, or None if skipped
        (too few frames / no crop) — the caller uses this to anchor only
        actually-written tracks for dedup."""
        if profile.frames_seen < cfg.REVIEW_MIN_FRAMES:
            return None
        if profile._best_crop is None:
            return None

        rid = next_record_id(self._cam_id)

        veh_path = str(self._crops_dir / f"{rid}_veh.jpg")
        cv2.imwrite(veh_path, profile._best_crop)

        plate_path: str | None = None
        if profile._best_plate_crop is not None:
            plate_path = str(self._crops_dir / f"{rid}_plate.jpg")
            cv2.imwrite(plate_path, profile._best_plate_crop)

        rec = ReviewInput(
            record_id=rid,
            channel=self._cam_id,  # cam_id string ("4"/"1"/"hik1")
            track_id=profile.track_id,
            finalized_at=time.time(),
            vehicle_crop_path=veh_path,
            plate_crop_path=plate_path,
            vehicle_class_predicted=profile.type,      # locked value at finalize (not stale snapshot)
            colour_predicted=profile.colour,
            ocr_predicted=profile.plate.number,
            plate_type_predicted=profile.plate.type,
            type_conf=profile.type_conf,
            colour_conf=profile.colour_conf,
            digits_conf=profile.plate.digits_conf,
            plate_type_conf=profile.plate.type_conf,
        )
        with open(self._jsonl, "a", encoding="utf-8") as fh:
            fh.write(rec.model_dump_json() + "\n")
        return rid


# ── Review-input reader ───────────────────────────────────────────────────────

def read_review_input(cam_id: str) -> list[ReviewInput]:
    path = cfg.LOGS_DIR / f"ch{cam_id}_review_input.jsonl"
    rows: list[ReviewInput] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(ReviewInput.model_validate_json(line))
                except Exception:
                    log.debug("Skipping malformed review_input line: %.80s", line)
    except FileNotFoundError:
        pass
    return rows


# ── Output writers ────────────────────────────────────────────────────────────

def _corrected_path(cam_id: str) -> Path:
    return cfg.OUT_DIR / f"ch{cam_id}_corrected_events.jsonl"


def _queue_path(cam_id: str) -> Path:
    return cfg.OUT_DIR / f"ch{cam_id}_review_queue.jsonl"


def _append_jsonl(path: Path, rec: ReviewedRecord) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(rec.model_dump_json() + "\n")
        os.fsync(fh.fileno())


def _copy_crop(src: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def write_outputs(rec: ReviewedRecord, cam_id: str) -> None:
    rid = rec.record_id

    # Always write the full corrected-events log
    _append_jsonl(_corrected_path(cam_id), rec)

    # Mirror in reviews.jsonl
    reviews_path = cfg.DATASET_DIR / "reviews.jsonl"
    _append_jsonl(reviews_path, rec)

    if rec.routed_to == "review_queue":
        _append_jsonl(_queue_path(cam_id), rec)
        return

    # ── Trusted path: populate dataset tree ──────────────────────────────────
    veh_src = rec.predicted.vehicle_crop_path
    ch_rid = f"ch{cam_id}_{rid}"

    # Maker classifier (folder-per-class). Persons/bicycles have no maker → keep
    # them out of makers/ (would pollute it) and curate them under non_vehicle/.
    vclass = rec.vehicle.vehicle_class.lower().strip()
    if vclass in ("person", "bicycle"):
        _copy_crop(veh_src, cfg.DATASET_DIR / "non_vehicle" / vclass / f"{ch_rid}.jpg")
    else:
        maker = rec.vehicle.vehicle_maker.lower().strip() or "unknown"
        _copy_crop(veh_src, cfg.DATASET_DIR / "makers" / maker / f"{ch_rid}.jpg")

    # Plate-type classifier
    if rec.plate is not None:
        plate_src = rec.predicted.plate_crop_path
        if plate_src:
            pt = rec.plate.plate_type.lower().strip()
            if pt not in ("tounes", "other"):
                pt = "other"
            _copy_crop(plate_src, cfg.DATASET_DIR / "plate_type" / pt / f"{ch_rid}.jpg")

            # Observed-category classifier (future fine plate model)
            cat = rec.plate.observed_category.lower().strip() or "unknown"
            _copy_crop(plate_src, cfg.DATASET_DIR / "plate_observed" / cat / f"{ch_rid}.jpg")

            # OCR labels
            if rec.plate.ocr:
                ocr_labels = cfg.DATASET_DIR / "ocr" / "labels.jsonl"
                ocr_labels.parent.mkdir(parents=True, exist_ok=True)
                entry = {
                    "crop_path": plate_src,
                    "ocr_corrected": rec.plate.ocr,
                    "record_id": rid,
                    "channel": cam_id,
                }
                with open(ocr_labels, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    os.fsync(fh.fileno())


# ── Human verification ────────────────────────────────────────────────────────

def read_corrected_events(cam_id: str) -> list[ReviewedRecord]:
    path = _corrected_path(cam_id)
    rows: list[ReviewedRecord] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(ReviewedRecord.model_validate_json(line))
                except Exception:
                    log.debug("Skipping malformed corrected_events line: %.80s", line)
    except FileNotFoundError:
        pass
    return rows


def _find_record(record_id: int, cam_id: str) -> ReviewedRecord:
    for rec in read_corrected_events(cam_id):
        if rec.record_id == record_id:
            return rec
    raise KeyError(f"record_id={record_id} not found in corrected_events for ch{cam_id}")


def write_human_verified(record_id: int, corrections: dict, cam_id: str) -> None:
    rec = _find_record(record_id, cam_id)
    entry = {
        "record_id":  record_id,
        "channel":    cam_id,
        "track_id":   rec.track_id,
        "verified_by": "human",
        "verified_at": time.time(),
        **corrections,
        "original_gemini": {
            "vehicle": rec.vehicle.model_dump(),
            "plate":   rec.plate.model_dump() if rec.plate else None,
        },
    }
    path = cfg.OUT_DIR / f"ch{cam_id}_human_verified.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.fsync(fh.fileno())

    ch_rid = f"ch{cam_id}_{record_id}"
    maker = (corrections.get("vehicle_maker") or "unknown").lower().strip() or "unknown"
    _copy_crop(
        rec.predicted.vehicle_crop_path,
        cfg.DATASET_DIR / "human_verified" / "makers" / maker / f"{ch_rid}.jpg",
    )
    if rec.predicted.plate_crop_path:
        pt = corrections.get("plate_type") or "other"
        _copy_crop(
            rec.predicted.plate_crop_path,
            cfg.DATASET_DIR / "human_verified" / "plate_type" / pt / f"{ch_rid}.jpg",
        )
        cat = corrections.get("observed_category") or "unknown"
        _copy_crop(
            rec.predicted.plate_crop_path,
            cfg.DATASET_DIR / "human_verified" / "plate_observed" / cat / f"{ch_rid}.jpg",
        )
