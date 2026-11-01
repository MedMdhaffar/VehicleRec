"""Append-only prediction/review storage and verified training-data export."""
from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field

from run_video_pipeline.schemas import PlateCategory, UnifiedPlateResult
from run_video_pipeline.policies import compact_result


class HumanReview(BaseModel):
    sample_id: str
    verified_text: str = ""
    verified_category: PlateCategory = "unknown"
    human_verified: bool = False
    reviewer: str = ""
    review_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    review_notes: str = ""
    review_action: Literal["accepted_as_correct", "corrected", "skipped"] = "corrected"


class ReviewStore:
    """Simple event store: original predictions are immutable; reviews append."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.crops = self.root / "crops"
        self.predictions_path = self.root / "predictions.jsonl"
        self.debug_path = self.root / "prediction_debug.jsonl"
        self.reviews_path = self.root / "reviews.jsonl"
        self._lock = threading.Lock()
        self.crops.mkdir(parents=True, exist_ok=True)
        self._sample_ids = self._load_sample_ids()

    def _load_sample_ids(self) -> set[str]:
        if not self.predictions_path.is_file(): return set()
        output = set()
        with self.predictions_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip(): output.add(json.loads(line)["sample_id"])
        return output

    @staticmethod
    def _append(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def capture(self, result: UnifiedPlateResult, crop: np.ndarray) -> UnifiedPlateResult:
        with self._lock:
            if result.sample_id in self._sample_ids:
                raise ValueError(f"duplicate sample_id: {result.sample_id}")
            crop_path = self.crops / f"{result.sample_id}.jpg"
            if crop is None or crop.size == 0 or not cv2.imwrite(str(crop_path), crop):
                raise ValueError("could not persist plate crop")
            stored = result.model_copy(update={"crop_path": str(crop_path)})
            compact = {
                "schema_version": 2,
                "sample_id": stored.sample_id,
                "timestamp": stored.timestamp,
                "crop_path": str(crop_path),
                **compact_result(stored),
                "plate_category": stored.plate_category,
                "confidence": stored.category_confidence,
                "route": stored.route,
                "source": stored.source,
                "status": stored.status,
                "camera_id": stored.camera_id,
                "track_id": stored.track_id,
                "original_image_ref": stored.original_image_ref,
                "model_version": stored.model_version,
            }
            quality = stored.evidence.get("quality", {})
            debug = {
                "schema_version": 2,
                "record_type": "prediction_debug",
                "sample_id": stored.sample_id,
                "plate_text_raw": stored.plate_text_raw,
                "plate_text_normalized": stored.plate_text_normalized,
                "text_confidence": stored.text_confidence,
                "category_confidence": stored.category_confidence,
                "route_reason": stored.route_reason,
                "detector_confidence": stored.detector_confidence,
                "bbox": stored.bbox,
                "frame_index": quality.get("frame_index"),
                "image_width": quality.get("image_width"),
                "image_height": quality.get("image_height"),
                "bbox_coordinate_context": quality.get("bbox_coordinate_context"),
                "best_crop_score": quality.get("best_crop_score"),
                "crop_area": quality.get("crop_area"),
                "sharpness": quality.get("sharpness"),
                "recognition_trigger_reason": quality.get("recognition_trigger_reason"),
                "attempt_number": quality.get("attempt_number"),
                "final_for_track": bool(quality.get("final_for_track", False)),
                "previous_sample_id": quality.get("previous_sample_id"),
                "supersedes_sample_id": quality.get("supersedes_sample_id"),
                "classification": stored.classification.model_dump(mode="json") if stored.classification else None,
                "number_recognition": stored.number.model_dump(mode="json") if stored.number else None,
                "marker": stored.marker.model_dump(mode="json"),
                "warning": stored.warning,
                "timing": stored.timing.model_dump(mode="json"),
                "evidence": stored.evidence,
            }
            try:
                self._append(self.debug_path, debug)
                self._append(self.predictions_path, compact)
            except Exception:
                crop_path.unlink(missing_ok=True)
                raise
            self._sample_ids.add(stored.sample_id)
            return stored

    def record_event(self, payload: dict) -> None:
        with self._lock:
            self._append(self.debug_path, payload)

    def mark_track_final(self, sample_id: str) -> None:
        """Append the final selection for a track without rewriting its prediction."""
        with self._lock:
            self._append(self.debug_path, {
                "schema_version": 2,
                "record_type": "track_finalization",
                "sample_id": sample_id,
                "final_for_track": True,
                "finalized_at": datetime.now(timezone.utc).isoformat(),
            })

    def add_review(self, review: HumanReview) -> None:
        with self._lock:
            # Predictions may be appended by a CLI or another API worker after
            # this store instance starts. Refresh before rejecting a sample that
            # is visible in the disk-backed review queue.
            if review.sample_id not in self._sample_ids:
                self._sample_ids.update(self._load_sample_ids())
            if review.sample_id not in self._sample_ids:
                raise KeyError(f"unknown sample_id: {review.sample_id}")
            self._append(self.reviews_path, review.model_dump(mode="json"))

    @staticmethod
    def _canonical_prediction(payload: dict) -> dict:
        """Return one compact view from legacy v1 or compact v2 storage."""
        if payload.get("schema_version") == 2:
            return dict(payload)
        prediction = payload.get("prediction", {})
        return {
            "schema_version": 1,
            "sample_id": payload.get("sample_id") or prediction.get("sample_id"),
            "timestamp": payload.get("timestamp") or prediction.get("timestamp"),
            "crop_path": prediction.get("crop_path"),
            "plate_text": prediction.get("plate_text_normalized") or prediction.get("plate_text_raw", ""),
            "plate_category": prediction.get("plate_category", "unknown"),
            "confidence": prediction.get("category_confidence", 0.0),
            "route": prediction.get("route", "unknown"),
            "source": prediction.get("source", "none"),
            "status": prediction.get("status", "unknown"),
            "camera_id": prediction.get("camera_id"),
            "track_id": prediction.get("track_id"),
            "original_image_ref": prediction.get("original_image_ref"),
            "model_version": prediction.get("model_version", ""),
        }

    def _predictions(self) -> dict[str, dict]:
        output = {}
        if self.predictions_path.is_file():
            with self.predictions_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        payload = json.loads(line)
                        canonical = self._canonical_prediction(payload)
                        output[canonical["sample_id"]] = canonical
        return output

    def _latest_reviews(self) -> dict[str, dict]:
        output = {}
        if self.reviews_path.is_file():
            with self.reviews_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        payload = json.loads(line); output[payload["sample_id"]] = payload
        return output

    def _debug_records(self) -> dict[str, dict]:
        output: dict[str, dict] = {}
        if not self.debug_path.is_file():
            return output
        with self.debug_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                sample_id = payload.get("sample_id")
                if not sample_id:
                    continue
                if payload.get("record_type") == "track_decision":
                    output.setdefault(sample_id, {}).update({key: payload.get(key) for key in ("identity_conflict", "category_conflict", "category_conflict_samples", "replacement_reason", "attempt_sample_ids")})
                elif payload.get("record_type") == "track_finalization":
                    output.setdefault(sample_id, {})["final_for_track"] = True
                else:
                    final = output.get(sample_id, {}).get("final_for_track", False)
                    output[sample_id] = dict(payload)
                    output[sample_id]["final_for_track"] = bool(
                        payload.get("final_for_track") or final
                    )
        return output

    def list_predictions(self) -> list[dict]:
        predictions, reviews = self._predictions(), self._latest_reviews()
        output = []
        for prediction in predictions.values():
            item = dict(prediction)
            review = reviews.get(prediction["sample_id"])
            item["latest_review"] = review
            item["review_state"] = review.get("review_action", "corrected" if review.get("human_verified") else "skipped") if review else "unreviewed"
            output.append(item)
        return sorted(output, key=lambda item: (item.get("timestamp") or "", item["sample_id"]))

    def get_prediction(self, sample_id: str) -> dict | None:
        return self._predictions().get(sample_id)

    def review_prediction(
        self,
        sample_id: str,
        action: Literal["accepted_as_correct", "corrected", "skipped"],
        *,
        reviewer: str = "",
        verified_text: str = "",
        verified_category: PlateCategory = "unknown",
        review_notes: str = "",
    ) -> HumanReview:
        prediction = self.get_prediction(sample_id)
        if prediction is None:
            raise KeyError(f"unknown sample_id: {sample_id}")
        if action == "accepted_as_correct":
            verified_text = prediction.get("plate_text", "")
            verified_category = prediction.get("plate_category", "unknown")
        review = HumanReview(
            sample_id=sample_id,
            verified_text=verified_text if action != "skipped" else "",
            verified_category=verified_category if action != "skipped" else "unknown",
            human_verified=action != "skipped",
            reviewer=reviewer,
            review_notes=review_notes,
            review_action=action,
        )
        self.add_review(review)
        return review

    def export_verified(self, output: Path) -> int:
        predictions, reviews = self._predictions(), self._latest_reviews()
        debug_records = self._debug_records()
        fields = [
            "sample_id", "crop_path", "verified_text", "verified_category", "source",
            "predicted_text", "predicted_category", "original_predicted_text",
            "original_predicted_category", "confidence", "original_text_confidence",
            "original_category_confidence", "camera_id", "track_id", "original_image_ref",
            "route", "model_version", "frame_index", "image_width", "image_height",
            "bbox", "bbox_coordinate_context", "detector_confidence", "best_crop_score",
            "crop_area", "sharpness", "recognition_trigger_reason", "attempt_number",
            "final_for_track", "previous_sample_id", "supersedes_sample_id", "identity_conflict", "category_conflict", "replacement_reason",
            "quality_metadata", "plugin_category", "category_decision_source", "recognition_policy",
            "registration_fields", "normalized_number", "recognition_status", "unresolved_reason", "reviewer", "review_timestamp", "review_notes",
        ]
        rows = []
        for sample_id, review in reviews.items():
            if not review.get("human_verified") or sample_id not in predictions: continue
            prediction = predictions[sample_id]
            debug = debug_records.get(sample_id, {})
            quality = {
                key: debug.get(key) for key in (
                    "frame_index", "image_width", "image_height", "bbox",
                    "bbox_coordinate_context", "detector_confidence", "best_crop_score",
                    "crop_area", "sharpness", "recognition_trigger_reason", "attempt_number",
                    "final_for_track", "previous_sample_id", "supersedes_sample_id", "identity_conflict", "category_conflict", "replacement_reason",
                )
            }
            rows.append({
                "sample_id": sample_id, "crop_path": prediction.get("crop_path", ""),
                "verified_text": review.get("verified_text", ""),
                "verified_category": review.get("verified_category", "unknown"),
                "source": prediction.get("source", ""),
                "predicted_text": prediction.get("plate_text", ""),
                "predicted_category": prediction.get("plate_category", "unknown"),
                "original_predicted_text": prediction.get("plate_text", ""),
                "original_predicted_category": prediction.get("plate_category", "unknown"),
                "confidence": prediction.get("confidence", 0.0),
                "original_text_confidence": debug.get("text_confidence", 0.0),
                "original_category_confidence": debug.get(
                    "category_confidence", prediction.get("confidence", 0.0)
                ),
                "camera_id": prediction.get("camera_id", ""), "track_id": prediction.get("track_id", ""),
                "original_image_ref": prediction.get("original_image_ref", ""),
                "route": prediction.get("route", ""), "model_version": prediction.get("model_version", ""),
                "frame_index": debug.get("frame_index", ""),
                "image_width": debug.get("image_width", ""),
                "image_height": debug.get("image_height", ""),
                "bbox": json.dumps(debug.get("bbox", []), separators=(",", ":")),
                "bbox_coordinate_context": debug.get("bbox_coordinate_context", ""),
                "detector_confidence": debug.get("detector_confidence", ""),
                "best_crop_score": debug.get("best_crop_score", ""),
                "crop_area": debug.get("crop_area", ""),
                "sharpness": debug.get("sharpness", ""),
                "recognition_trigger_reason": debug.get("recognition_trigger_reason", ""),
                "attempt_number": debug.get("attempt_number", ""),
                "final_for_track": debug.get("final_for_track", False),
                "previous_sample_id": debug.get("previous_sample_id", ""),
                "supersedes_sample_id": debug.get("supersedes_sample_id", ""),
                "identity_conflict": debug.get("identity_conflict", False),
                "category_conflict": debug.get("category_conflict", False),
                "replacement_reason": debug.get("replacement_reason"),
                "quality_metadata": json.dumps(quality, ensure_ascii=False, separators=(",", ":")),
                "plugin_category": prediction.get("predicted_category"),
                **{key: (json.dumps(prediction.get(key), ensure_ascii=False) if key == "registration_fields" else prediction.get(key))
                   for key in ("category_decision_source", "recognition_policy", "registration_fields", "normalized_number", "recognition_status", "unresolved_reason")},
                "reviewer": review.get("reviewer", ""), "review_timestamp": review.get("review_timestamp", ""),
                "review_notes": review.get("review_notes", ""),
            })
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        return len(rows)
