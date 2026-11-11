"""Track plates in a prerecorded video and recognize the best crop once per track."""
from __future__ import annotations

import math
import os
import statistics
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import yaml

import run_video_pipeline.config as config
from run_video_pipeline.ocr import PlateReader
from run_video_pipeline.policies import compact_result
from run_video_pipeline.plate_detector import (
    PlateCrop,
    PlateDetector,
    crop_detections,
    deduplicate_detections,
)
from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
from run_video_pipeline.schemas import UnifiedPlateResult

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACKER = Path(__file__).resolve().parent / "trackers/bytetrack_plates.yaml"
DEFAULT_ASSOCIATION_PADDING = 0.18


class TrackerDetections:
    """Small adapter from canonical detector tuples to Ultralytics BYTETracker."""

    def __init__(self, xyxy: np.ndarray, confidence: np.ndarray):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(confidence, dtype=np.float32).reshape(-1)
        self.cls = np.zeros(len(self.conf), dtype=np.float32)
        if len(self.xyxy):
            widths = self.xyxy[:, 2] - self.xyxy[:, 0]
            heights = self.xyxy[:, 3] - self.xyxy[:, 1]
            centers_x = self.xyxy[:, 0] + widths / 2
            centers_y = self.xyxy[:, 1] + heights / 2
            self.xywh = np.column_stack((centers_x, centers_y, widths, heights))
        else:
            self.xywh = np.empty((0, 4), dtype=np.float32)

    @classmethod
    def from_tuples(
        cls, detections, *, association_padding: float = 0.0
    ) -> "TrackerDetections":
        if association_padding < 0:
            raise ValueError("association padding must be non-negative")
        rows = list(detections)
        if not rows:
            return cls(np.empty((0, 4)), np.empty(0))
        values = np.asarray(rows, dtype=np.float32)
        boxes = values[:, :4].copy()
        if association_padding:
            widths = boxes[:, 2] - boxes[:, 0]
            heights = boxes[:, 3] - boxes[:, 1]
            boxes[:, 0] -= widths * association_padding
            boxes[:, 2] += widths * association_padding
            boxes[:, 1] -= heights * association_padding
            boxes[:, 3] += heights * association_padding
        return cls(boxes, values[:, 4])

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, item) -> "TrackerDetections":
        return TrackerDetections(self.xyxy[item], self.conf[item])


def _detection_for_track(
    tracked_box: np.ndarray,
    detections: list[tuple[int, int, int, int, float]],
    association_padding: float,
    used: set[int],
) -> tuple[int, int, int, int, float] | None:
    best_index = None
    best_iou = -1.0
    tx1, ty1, tx2, ty2 = (float(value) for value in tracked_box[:4])
    for index, detection in enumerate(detections):
        if index in used:
            continue
        x1, y1, x2, y2, _ = detection
        width, height = x2 - x1, y2 - y1
        candidate = (
            x1 - width * association_padding,
            y1 - height * association_padding,
            x2 + width * association_padding,
            y2 + height * association_padding,
        )
        intersection = max(0.0, min(tx2, candidate[2]) - max(tx1, candidate[0])) * max(
            0.0, min(ty2, candidate[3]) - max(ty1, candidate[1])
        )
        tracked_area = max(0.0, tx2 - tx1) * max(0.0, ty2 - ty1)
        candidate_area = max(0.0, candidate[2] - candidate[0]) * max(
            0.0, candidate[3] - candidate[1]
        )
        union = tracked_area + candidate_area - intersection
        iou = intersection / union if union else 0.0
        if iou > best_iou:
            best_index, best_iou = index, iou
    if best_index is None:
        return None
    used.add(best_index)
    return detections[best_index]


def crop_quality_evidence(
    crop: np.ndarray, detector_confidence: float, *, border_contact: bool = False
) -> tuple[float, int, float]:
    """Rank crops within one track using only cheap geometry/image statistics."""
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return float("-inf"), 0, 0.0
    height, width = crop.shape[:2]
    if width < 1 or height < 1:
        return float("-inf"), 0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(gray.std())
    brightness = float(gray.mean())
    exposure = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
    sharpness_factor = 1.0 + 0.25 * min(sharpness / 400.0, 1.0)
    contrast_factor = 0.9 + 0.1 * min(contrast / 64.0, 1.0)
    exposure_factor = 0.9 + 0.1 * exposure
    confidence_factor = 0.5 + max(0.0, min(float(detector_confidence), 1.0))
    border_factor = 0.60 if border_contact else 1.0
    score = width * height * confidence_factor * sharpness_factor * contrast_factor
    score *= exposure_factor * border_factor
    return float(score), width * height, sharpness


def crop_quality(crop: np.ndarray, detector_confidence: float) -> float:
    return crop_quality_evidence(crop, detector_confidence)[0]


@dataclass
class RecognitionAttempt:
    result: UnifiedPlateResult
    low_level_ocr_calls: int
    queue_wait_ms: float = 0.0
    crop_score: float = 0.0
    crop_area: int = 0
    crop_sharpness: float = 0.0
    crop_frame: int = 0
    trigger_reason: str = "test"


@dataclass
class TrackState:
    track_id: int
    first_frame: int
    last_frame: int
    candidate_count: int = 0
    best_updates: int = 0
    best_score: float = float("-inf")
    best_crop: np.ndarray | None = None
    best_bbox: tuple[int, int, int, int] | None = None
    best_confidence: float = 0.0
    best_frame: int = 0
    best_area: int = 0
    best_sharpness: float = 0.0
    best_image_width: int = 0
    best_image_height: int = 0
    best_border_contact: bool = False
    observations_since_best: int = 0
    first_crop: np.ndarray | None = None
    first_score: float | None = None
    first_crop_frame: int | None = None
    observed_frames: list[int] = field(default_factory=list)
    attempts: list[RecognitionAttempt] = field(default_factory=list)
    submitted_qualities: list[float] = field(default_factory=list)
    submitted_frames: list[int] = field(default_factory=list)
    completed_frames: list[int | None] = field(default_factory=list)
    pending: Future | None = None
    deferred_reason: str | None = None
    deferred_since: float | None = None
    deferred_source_ref: str | Path | None = None
    deferred_count: int = 0
    scheduling_result: UnifiedPlateResult | None = None
    category_conflict: bool = False
    category_conflict_samples: list[str] = field(default_factory=list)
    identity_conflict: bool = False
    identity_conflict_samples: list[str] = field(default_factory=list)
    cached_attempt_index: int | None = None
    result_replaced: bool = False
    replacement_reason: str | None = None
    final_metadata_written: bool = False

    def consider(
        self, plate: PlateCrop, frame_index: int, image_shape: tuple[int, ...] | None = None
    ) -> bool:
        self.last_frame = frame_index
        self.candidate_count += 1
        self.observed_frames.append(frame_index)
        border_contact = False
        if image_shape is not None:
            image_height, image_width = image_shape[:2]
            x1, y1, x2, y2 = plate.bbox
            border_contact = x1 <= 0 or y1 <= 0 or x2 >= image_width or y2 >= image_height
        score, area, sharpness = crop_quality_evidence(
            plate.crop, plate.confidence or 0.0, border_contact=border_contact
        )
        if self.first_crop is None:
            self.first_crop = plate.crop.copy()
            self.first_score = score
            self.first_crop_frame = frame_index
        if score <= self.best_score:
            self.observations_since_best += 1
            return False
        self.best_score = score
        self.best_crop = plate.crop.copy()
        self.best_bbox = plate.bbox
        self.best_confidence = float(plate.confidence or 0.0)
        self.best_frame = frame_index
        self.best_area = area
        self.best_sharpness = sharpness
        if image_shape is not None:
            self.best_image_height, self.best_image_width = image_shape[:2]
        self.best_border_contact = border_contact
        self.observations_since_best = 0
        self.best_updates += 1
        return True

    @property
    def final_result(self) -> UnifiedPlateResult | None:
        if self.cached_attempt_index is None:
            return self.scheduling_result
        return self.attempts[self.cached_attempt_index].result

    @property
    def cached_attempt(self) -> RecognitionAttempt | None:
        if self.cached_attempt_index is None:
            return None
        return self.attempts[self.cached_attempt_index]

    def initial_trigger_reason(
        self,
        *,
        strong_quality: float,
        strong_min_candidates: int,
        stable_min_candidates: int,
        stable_observations: int,
    ) -> str | None:
        if self.attempts or self.pending is not None or self.best_crop is None:
            return None
        if (
            not self.best_border_contact
            and self.candidate_count >= strong_min_candidates
            and self.best_score >= strong_quality
        ):
            return "strong_quality"
        if (
            not self.best_border_contact
            and self.candidate_count >= stable_min_candidates
            and self.observations_since_best >= stable_observations
        ):
            return "stable_best"
        return None

    def recheck_is_warranted(
        self, improvement: float, area_or_sharpness_improvement: float
    ) -> bool:
        attempt = self.attempts[0] if self.attempts else None
        return (
            attempt is not None
            and len(self.attempts) == 1
            and self.pending is None
            and self.best_score >= attempt.crop_score * improvement
            and (
                self.best_area >= attempt.crop_area * area_or_sharpness_improvement
                or self.best_sharpness
                >= attempt.crop_sharpness * area_or_sharpness_improvement
            )
        )

    def record_attempt(self, attempt: RecognitionAttempt, confidence_gain: float) -> None:
        self.attempts.append(attempt)
        if self.cached_attempt_index is None:
            self.cached_attempt_index = 0
            self.replacement_reason = "initial result cached"
            return
        current = self.final_result
        candidate = attempt.result
        if current and current.predicted_category and candidate.predicted_category and current.predicted_category != candidate.predicted_category:
            self.category_conflict = True
            self.category_conflict_samples.extend([current.sample_id, candidate.sample_id])
        if candidate.status != "accepted":
            self.replacement_reason = "recheck not accepted; kept initial result"
            return
        if current is None or current.status != "accepted":
            self.cached_attempt_index = len(self.attempts) - 1
            self.result_replaced = True
            self.replacement_reason = "accepted recheck replaced unresolved initial result"
            return
        current_identity = (current.predicted_category, tuple(sorted(current.registration_fields.items())))
        candidate_identity = (candidate.predicted_category, tuple(sorted(candidate.registration_fields.items())))
        if candidate_identity != current_identity:
            self.identity_conflict = True
            self.identity_conflict_samples.extend([current.sample_id, candidate.sample_id])
            self.replacement_reason = "identity conflict; retained accepted initial result for review evidence"
        elif candidate.recognition_policy == current.recognition_policy and candidate.text_confidence >= current.text_confidence - confidence_gain:
            self.cached_attempt_index = len(self.attempts) - 1
            self.result_replaced = True
            self.replacement_reason = "same category and validated fields confirmed on better crop"
        else:
            self.replacement_reason = "recheck has weaker or incomparable registration evidence"



class CountingReader:
    def __init__(self, delegate=None, factory=PlateReader):
        self.delegate = delegate
        self.factory = factory
        self._lock = threading.Lock()
        self.calls = 0

    def initialize(self):
        if self.delegate is None:
            self.delegate = self.factory()

    def close(self):
        self.delegate = None

    def read_result(self, crop):
        with self._lock:
            self.calls += 1
        return self.delegate.read_result(crop)


def _summary(values: list[float]) -> dict:
    if not values:
        return {
            "count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0,
            "min": 0.0, "max": 0.0,
        }
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": round(float(np.percentile(values, 50)), 3),
        "p95": round(float(np.percentile(values, 95)), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def _frame_ranges(frames: list[int]) -> list[list[int]]:
    if not frames:
        return []
    ranges: list[list[int]] = []
    start = previous = frames[0]
    for frame in frames[1:]:
        if frame != previous + 1:
            ranges.append([start, previous])
            start = frame
        previous = frame
    ranges.append([start, previous])
    return ranges


def _gpu_memory() -> dict:
    report: dict[str, object] = {}
    try:
        import torch
        if torch.cuda.is_available():
            report["device"] = torch.cuda.get_device_name(0)
            report["total_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 3)
            report["torch_peak_allocated_gb"] = round(torch.cuda.max_memory_allocated(0) / 1e9, 3)
            report["torch_peak_reserved_gb"] = round(torch.cuda.max_memory_reserved(0) / 1e9, 3)
    except (ImportError, RuntimeError):
        pass
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda():
            report["paddle_allocated_gb"] = round(paddle.device.cuda.memory_allocated() / 1e9, 3)
            report["paddle_reserved_gb"] = round(paddle.device.cuda.memory_reserved() / 1e9, 3)
            report["paddle_peak_allocated_gb"] = round(
                paddle.device.cuda.max_memory_allocated() / 1e9, 3
            )
            report["paddle_peak_reserved_gb"] = round(
                paddle.device.cuda.max_memory_reserved() / 1e9, 3
            )
    except (AttributeError, ImportError, OSError, RuntimeError):
        pass
    return report


def save_track_crops(directory: Path, tracks: dict[int, TrackState]) -> dict[int, dict[str, str]]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[int, dict[str, str]] = {}
    for track_id, state in tracks.items():
        track_paths: dict[str, str] = {}
        for label, crop in (("early", state.first_crop), ("final_best", state.best_crop)):
            if crop is None:
                continue
            path = directory / f"track_{track_id}_{label}.jpg"
            if cv2.imwrite(str(path), crop):
                track_paths[label] = str(path)
        result = state.final_result
        if result is not None and result.crop_path:
            recognized = cv2.imread(result.crop_path)
            path = directory / f"track_{track_id}_recognized.jpg"
            if recognized is not None and recognized.size and cv2.imwrite(str(path), recognized):
                track_paths["recognized"] = str(path)
        paths[track_id] = track_paths
    return paths


def _safe_ascii(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("تونس", "T").encode("ascii", "ignore").decode("ascii").split())


def annotate_frame(
    frame: np.ndarray,
    annotations: list[tuple[int, tuple[int, int, int, int]]],
    tracks: dict[int, TrackState],
    *,
    track_id_prefix: str = "",
) -> np.ndarray:
    """Draw current cached track results on one frame in place."""
    for track_id, bbox in annotations:
        result = tracks[track_id].final_result
        if result is None:
            text, category, source_name, status, confidence = "", "unknown", "none", "pending", 0.0
        else:
            text = _safe_ascii(result.normalized_number if result.status == "accepted" else result.unresolved_reason)
            category, source_name, status = result.plate_category, result.category_decision_source or result.source, result.status
            confidence = result.text_confidence
        x1, y1, x2, y2 = bbox
        color = (20, 190, 20) if status == "accepted" else (0, 165, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = (
            f"T{track_id_prefix}{track_id} | {text or '<no text>'} | {category} | "
            f"{confidence:.2f} | {source_name} | {status}"
            + (f" | {result.sample_id[:8]} | {result.timing.total_ms:.0f}ms" if result else "")
            + (" | IDENTITY CONFLICT" if tracks[track_id].identity_conflict else "")
            + (" | CATEGORY CONFLICT" if tracks[track_id].category_conflict else "")
        )
        (label_width, label_height), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1
        )
        top = max(label_height + 6, y1)
        cv2.rectangle(
            frame, (x1, top - label_height - 6),
            (x1 + label_width + 4, top), color, -1,
        )
        cv2.putText(
            frame, label, (x1 + 2, top - 4), cv2.FONT_HERSHEY_SIMPLEX,
            0.48, (0, 0, 0), 1, cv2.LINE_AA,
        )
    return frame


def annotate_video(
    source: Path,
    output: Path,
    frame_tracks: list[list[tuple[int, tuple[int, int, int, int]]]],
    tracks: dict[int, TrackState],
    fps: float,
    size: tuple[int, int],
) -> float:
    started = time.perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise RuntimeError(f"could not open output video writer: {output}")
    capture = cv2.VideoCapture(str(source))
    try:
        frame_index = 0
        while frame_index < len(frame_tracks):
            ok, frame = capture.read()
            if not ok:
                break
            annotate_frame(frame, frame_tracks[frame_index], tracks)
            writer.write(frame)
            frame_index += 1
    finally:
        capture.release()
        writer.release()
    return time.perf_counter() - started


class VideoPrototypeRunner:
    def __init__(
        self,
        pipeline: PrototypePlatePipeline,
        detector: PlateDetector,
        tracker,
        counting_reader: CountingReader,
        *,
        association_padding: float = DEFAULT_ASSOCIATION_PADDING,
        strong_quality: float = 12000.0,
        strong_min_candidates: int = 3,
        min_candidates: int = 12,
        stable_observations: int = 5,
        disappearance_frames: int = 5,
        minimum_exit_candidates: int = 8,
        retry_improvement: float = 1.50,
        recheck_detail_improvement: float = 1.25,
        replacement_confidence_gain: float = 0.02,
        camera_id: str = "static-video",
        track_id_prefix: str = "",
        max_pending: int | None = None,
    ):
        if min_candidates < 1:
            raise ValueError("min_candidates must be positive")
        if minimum_exit_candidates < 1:
            raise ValueError("minimum exit candidates must be positive")
        if retry_improvement <= 1.0:
            raise ValueError("retry_improvement must be greater than 1")
        if association_padding < 0:
            raise ValueError("association padding must be non-negative")
        self.max_pending = max_pending if max_pending is not None else int(os.environ.get("PLATE_MAX_PENDING", "4"))
        if self.max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.deferred_events = 0
        self._source_ref = None
        self.initialization_ms = 0.0
        self.pipeline = pipeline
        self.detector = detector
        self.tracker = tracker
        self.reader = counting_reader
        self.association_padding = association_padding
        self.strong_quality = strong_quality
        self.strong_min_candidates = strong_min_candidates
        self.min_candidates = min_candidates
        self.stable_observations = stable_observations
        self.disappearance_frames = disappearance_frames
        self.minimum_exit_candidates = minimum_exit_candidates
        self.retry_improvement = retry_improvement
        self.recheck_detail_improvement = recheck_detail_improvement
        self.replacement_confidence_gain = replacement_confidence_gain
        self.camera_id = camera_id
        self.track_id_prefix = track_id_prefix
        self.tracks: dict[int, TrackState] = {}
        self._finished = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plate-recognition")
        try:
            # Fail before frame processing, and initialize on the actual inference thread.
            initialization_started = time.perf_counter()
            self.executor.submit(self._initialize_models).result()
            self.initialization_ms = (time.perf_counter() - initialization_started) * 1000
        except BaseException:
            self.executor.shutdown(wait=True)
            raise
        self.pending_snapshots: list[set[int]] = []
        self.detection_times_ms: list[float] = []
        self.tracking_times_ms: list[float] = []
        self.crop_scoring_times_ms: list[float] = []
        self.detections_per_frame: list[int] = []
        self.tracked_outputs_per_frame: list[int] = []

    def _initialize_models(self):
        started = time.perf_counter()
        self.pipeline.initialize_classifier()
        self.classifier_initialization_ms = (time.perf_counter() - started) * 1000
        try:
            self.reader.initialize()
            self.pipeline.marker_localizer.load()
            if self.detector is not None:
                self.detector.load()
        except Exception:
            self._release_models()
            raise

    def _release_models(self):
        try:
            self.pipeline.release_classifier()
        finally:
            self.reader.close()

    def _recognize(
        self,
        track_id: int,
        plate: PlateCrop,
        crop_score: float,
        crop_area: int,
        crop_sharpness: float,
        crop_frame: int,
        trigger_reason: str,
        source_ref: str | Path,
        image_width: int = 0,
        image_height: int = 0,
        attempt_number: int = 1,
        previous_sample_id: str | None = None,
        eligible_at: float | None = None,
    ) -> RecognitionAttempt:
        before_ocr = self.reader.calls
        queue_wait_ms = (time.perf_counter() - eligible_at) * 1000 if eligible_at is not None else 0.0
        result = self.pipeline.process_crop(
            plate,
            original_image_ref=f"{source_ref}#frame={crop_frame}",
            camera_id=self.camera_id,
            track_id=f"{self.track_id_prefix}{track_id}",
            quality={
                "queue_wait_ms": queue_wait_ms,
                "frame_index": crop_frame,
                "image_width": image_width,
                "image_height": image_height,
                "bbox_coordinate_context": "source_frame_pixels_xyxy",
                "best_crop_score": crop_score,
                "crop_area": crop_area,
                "sharpness": crop_sharpness,
                "recognition_trigger_reason": trigger_reason,
                "attempt_number": attempt_number,
                "final_for_track": False,
                "previous_sample_id": previous_sample_id,
                "supersedes_sample_id": previous_sample_id,
            },
        )
        return RecognitionAttempt(
            result=result,
            low_level_ocr_calls=self.reader.calls - before_ocr,
            queue_wait_ms=queue_wait_ms,
            crop_score=crop_score,
            crop_area=crop_area,
            crop_sharpness=crop_sharpness,
            crop_frame=crop_frame,
            trigger_reason=trigger_reason,
        )

    def _submit(self, state: TrackState, source_ref: str | Path, trigger_reason: str) -> None:
        if state.pending is not None or state.best_crop is None or len(state.attempts) >= 2:
            return
        self._source_ref = source_ref
        if state.deferred_since is None:
            state.deferred_source_ref = source_ref
            state.deferred_since = time.perf_counter()
        if sum(track.pending is not None for track in self.tracks.values()) >= self.max_pending:
            if state.deferred_reason is None:
                state.deferred_count += 1
                self.deferred_events += 1
            state.deferred_reason = state.deferred_reason or trigger_reason
            return
        source_ref = state.deferred_source_ref or source_ref
        trigger_reason = state.deferred_reason or trigger_reason
        eligible_at = state.deferred_since
        state.deferred_reason = None
        state.deferred_since = None
        state.deferred_source_ref = None
        state.submitted_qualities.append(state.best_score)
        state.submitted_frames.append(state.best_frame)
        plate = PlateCrop(state.best_crop.copy(), state.best_bbox, state.best_confidence)
        state.pending = self.executor.submit(
            self._recognize,
            state.track_id,
            plate,
            state.best_score,
            state.best_area,
            state.best_sharpness,
            state.best_frame,
            trigger_reason,
            source_ref,
            state.best_image_width,
            state.best_image_height,
            len(state.attempts) + 1,
            state.attempts[-1].result.sample_id if state.attempts else None,
            eligible_at,
        )

    def _drain(self, source_ref: str | Path, frame_index: int | None) -> None:
        for state in self.tracks.values():
            if state.pending is not None and state.pending.done():
                state.record_attempt(state.pending.result(), self.replacement_confidence_gain)
                state.completed_frames.append(frame_index)
                state.pending = None
            if state.deferred_reason and state.pending is None:
                self._submit(state, source_ref, state.deferred_reason)
            if state.recheck_is_warranted(
                self.retry_improvement, self.recheck_detail_improvement
            ):
                self._submit(state, source_ref, "substantially_better_crop_recheck")

    def record_capacity_expiry(self, state):
        """Persist unscheduled stale work explicitly instead of retaining crops forever."""
        import uuid
        from datetime import datetime, timezone
        from run_video_pipeline.schemas import NumberRecognition, PlateTiming
        reason = "queue_capacity_retention_limit"
        self.pipeline.store.record_event({
            "record_type": "scheduling_expiry", "track_id": f"{self.track_id_prefix}{state.track_id}",
            "reason": reason, "deferred_trigger": state.deferred_reason,
            "deferred_wait_ms": (time.perf_counter() - state.deferred_since) * 1000 if state.deferred_since else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(), "bbox": list(state.best_bbox),
        })
        if state.final_result is None:
            state.scheduling_result = self.pipeline.store.capture(UnifiedPlateResult(
                sample_id=str(uuid.uuid4()), timestamp=datetime.now(timezone.utc).isoformat(),
                route="unknown", route_confidence=0, route_reason=reason, source="none", model_version="not_run",
                bbox=list(state.best_bbox), detector_confidence=state.best_confidence,
                camera_id=self.camera_id, track_id=f"{self.track_id_prefix}{state.track_id}",
                original_image_ref=f"{state.deferred_source_ref}#frame={state.best_frame}",
                timing=PlateTiming(queue_wait_ms=(time.perf_counter() - state.deferred_since) * 1000 if state.deferred_since else 0.0),
                status="unresolved", recognition_status="unresolved", unresolved_reason=reason,
                number=NumberRecognition(policy="unselected", outcome="unresolved", reason=reason),
                evidence={"quality": {"recognition_trigger_reason": state.deferred_reason, "frame_index": state.best_frame}},
            ), state.best_crop)
        state.deferred_reason = None
        state.deferred_since = None

    def finalize_track(self, state: TrackState) -> None:
        """Append which immutable attempt became the final cached track result."""
        if state.final_metadata_written or state.final_result is None:
            return
        self.pipeline.store.mark_track_final(state.final_result.sample_id)
        self.pipeline.store.record_event({
            "schema_version": 2, "record_type": "track_decision", "sample_id": state.final_result.sample_id,
            "identity_conflict": state.identity_conflict,
                "category_conflict": state.category_conflict,
                "category_conflict_samples": state.category_conflict_samples, "replacement_reason": state.replacement_reason,
            "attempt_sample_ids": [attempt.result.sample_id for attempt in state.attempts],
        })
        state.final_metadata_written = True

    def process_frame(
        self,
        frame: np.ndarray,
        frame_index: int,
        source_ref: str | Path,
        *,
        conf: float | None = None,
        imgsz: int | None = None,
    ) -> list[tuple[int, tuple[int, int, int, int]]]:
        """Run the validated detector/tracker/best-crop policy on one frame."""
        detection_started = time.perf_counter()
        detections = deduplicate_detections(
            self.detector.predict(frame, conf=conf, imgsz=imgsz),
            iou_threshold=config.PLATE_DEDUP_IOU,
        )
        self.detections_per_frame.append(len(detections))
        self.detection_times_ms.append((time.perf_counter() - detection_started) * 1000)

        tracking_started = time.perf_counter()
        tracker_detections = TrackerDetections.from_tuples(
            detections, association_padding=self.association_padding
        )
        tracked = self.tracker.update(tracker_detections, frame)
        self.tracked_outputs_per_frame.append(len(tracked))
        self.tracking_times_ms.append((time.perf_counter() - tracking_started) * 1000)

        annotations: list[tuple[int, tuple[int, int, int, int]]] = []
        used_detections: set[int] = set()
        for row in tracked:
            track_id = int(row[4])
            detection = _detection_for_track(
                row, detections, self.association_padding, used_detections
            )
            if detection is None:
                continue
            safe = crop_detections(frame, [detection], min_width=4, min_height=4)
            if not safe:
                continue
            plate = safe[0]
            state = self.tracks.setdefault(
                track_id, TrackState(track_id, frame_index, frame_index)
            )
            scoring_started = time.perf_counter()
            state.consider(plate, frame_index, frame.shape)
            self.crop_scoring_times_ms.append((time.perf_counter() - scoring_started) * 1000)
            annotations.append((track_id, plate.bbox))

        self._drain(source_ref, frame_index)
        for state in self.tracks.values():
            trigger_reason = state.initial_trigger_reason(
                strong_quality=self.strong_quality,
                strong_min_candidates=self.strong_min_candidates,
                stable_min_candidates=self.min_candidates,
                stable_observations=self.stable_observations,
            )
            if trigger_reason:
                self._submit(state, source_ref, trigger_reason)
            elif (
                not state.attempts
                and state.pending is None
                and state.candidate_count >= self.minimum_exit_candidates
                and frame_index - state.last_frame >= self.disappearance_frames
            ):
                self._submit(state, source_ref, "track_disappeared")
        self.pending_snapshots.append({
            track_id for track_id, state in self.tracks.items() if state.pending is not None
        })
        return annotations

    def close(self) -> None:
        """Release worker-owned model references and join queued recognition work."""
        if self._finished:
            return
        try:
            while any(state.pending is not None or state.deferred_reason for state in self.tracks.values()):
                self._drain(self._source_ref or "processing_shutdown", None)
                time.sleep(0.01)
        finally:
            self.executor.submit(self._release_models).result()
            self.executor.shutdown(wait=True)
            self._finished = True

    def finish(self, source_ref: str | Path, *, end_trigger: str = "video_end") -> float:
        """Submit eligible end-of-source tracks and drain the recognition worker."""
        if self._finished:
            return 0.0
        drain_started = time.perf_counter()
        try:
            for state in self.tracks.values():
                if (
                    not state.attempts
                    and state.pending is None
                    and state.candidate_count >= self.minimum_exit_candidates
                ):
                    self._submit(state, source_ref, end_trigger)
            self.pending_snapshots.append({
                track_id for track_id, state in self.tracks.items() if state.pending is not None
            })
            while any(state.pending is not None or state.deferred_reason for state in self.tracks.values()):
                self._drain(source_ref, None)
                self.pending_snapshots.append({
                    track_id for track_id, state in self.tracks.items() if state.pending is not None
                })
                time.sleep(0.01)
            for state in self.tracks.values():
                self.finalize_track(state)
        finally:
            self.close()
        return time.perf_counter() - drain_started

    def run(
        self,
        video: Path,
        output: Path,
        *,
        conf: float | None = None,
        imgsz: int | None = None,
        max_frames: int | None = None,
        selected_crops_dir: Path | None = None,
    ) -> dict:
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            capture.release()
            self.close()
            raise ValueError(f"Could not open video: {video}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_tracks: list[list[tuple[int, tuple[int, int, int, int]]]] = []
        frame_index = 0
        loop_started = time.perf_counter()
        try:
            while max_frames is None or frame_index < max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                annotations = self.process_frame(
                    frame, frame_index, video, conf=conf, imgsz=imgsz
                )
                frame_tracks.append(annotations)
                frame_index += 1
        except BaseException:
            self.close()
            raise
        finally:
            capture.release()
        frame_loop_seconds = time.perf_counter() - loop_started

        recognition_drain_seconds = self.finish(video)
        analysis_seconds = time.perf_counter() - loop_started

        annotation_seconds = annotate_video(
            video, output, frame_tracks, self.tracks, fps, (width, height)
        )
        crop_paths = save_track_crops(
            selected_crops_dir or output.with_suffix("").with_name(f"{output.stem}_selected_crops"),
            self.tracks,
        )
        duration_seconds = frame_index / fps if fps > 0 else 0.0
        attempts = [attempt for state in self.tracks.values() for attempt in state.attempts]
        results = [state.final_result for state in self.tracks.values() if state.final_result is not None]
        accepted = [result for result in results if result.status == "accepted"]
        route_counts = {
            route: sum(result.route == route for result in results)
            for route in ("private_local", "local", "unknown")
        }
        total_observations = sum(state.candidate_count for state in self.tracks.values())
        pipeline_attempts = len(attempts)
        low_level_ocr_calls = sum(attempt.low_level_ocr_calls for attempt in attempts)
        local_latencies = [attempt.result.timing.local_total_ms for attempt in attempts]
        total_latencies = [attempt.result.timing.total_ms for attempt in attempts]
        routing_latencies = [attempt.result.timing.routing_ms for attempt in attempts]
        track_metrics = {}
        for track_id, state in sorted(self.tracks.items()):
            result = state.final_result
            selected_attempt = state.cached_attempt
            selected_frame = selected_attempt.crop_frame if selected_attempt else None
            selected_score = selected_attempt.crop_score if selected_attempt else None
            track_metrics[str(track_id)] = {
                "frames_with_candidate": state.candidate_count,
                "first_frame": state.first_frame,
                "last_frame": state.last_frame,
                "frame_ranges": _frame_ranges(state.observed_frames),
                "track_duration_seconds": round((state.last_frame - state.first_frame + 1) / fps, 3),
                "best_crop_updates": state.best_updates,
                "selected_best_crops": len(state.submitted_qualities),
                "recognition_attempts": len(state.attempts),
                "low_level_ocr_calls": sum(item.low_level_ocr_calls for item in state.attempts),
                **(compact_result(result) if result else {}),
                "identity_conflict": state.identity_conflict,
                "category_conflict": state.category_conflict,
                "category_conflict_samples": state.category_conflict_samples,
                "identity_conflict_samples": state.identity_conflict_samples,
                "deferred_attempts": state.deferred_count,
                "selected_crop_frame": selected_frame,
                "selected_crop_score": round(selected_score, 3) if selected_score is not None else None,
                "selected_best_crop_path": crop_paths.get(track_id, {}).get("recognized"),
                "final_best_frame": state.best_frame,
                "final_best_quality": round(state.best_score, 3) if math.isfinite(state.best_score) else None,
                "final_best_crop_path": crop_paths.get(track_id, {}).get("final_best"),
                "early_frame": state.first_crop_frame,
                "early_quality": round(state.first_score, 3) if state.first_score is not None else None,
                "best_to_early_quality_ratio": (
                    round(state.best_score / state.first_score, 3)
                    if state.first_score not in (None, 0.0) else None
                ),
                "early_crop_path": crop_paths.get(track_id, {}).get("early"),
                "capture_crop_path": result.crop_path if result else None,
                "submitted_frames": state.submitted_frames,
                "completed_frames": state.completed_frames,
                "attempt_log": [
                    {
                        "attempt": index + 1,
                        "frame": attempt.crop_frame,
                        "crop_score": round(attempt.crop_score, 3),
                        "trigger_reason": attempt.trigger_reason,
                        "text": attempt.result.normalized_number if attempt.result.status == "accepted" else "",
                        "sample_id": attempt.result.sample_id,
                        "recognition_policy": attempt.result.recognition_policy,
                        "unresolved_reason": attempt.result.unresolved_reason,
                        "category": attempt.result.plate_category,
                        "confidence": round(attempt.result.category_confidence, 4),
                        "route": attempt.result.route,
                        "source": attempt.result.source,
                        "status": attempt.result.status,
                    }
                    for index, attempt in enumerate(state.attempts)
                ],
                "result_replaced": state.result_replaced,
                "replacement_reason": state.replacement_reason,
                "text": result.normalized_number if result and result.status == "accepted" else "",
                "category": result.plate_category if result else "unknown",
                "confidence": round(result.category_confidence, 4) if result else 0.0,
                "route": result.route if result else "unknown",
                "source": result.source if result else "none",
                "status": result.status if result else "unresolved",
                "local_latency_ms": round(result.timing.local_total_ms, 3) if result else 0.0,
                "total_recognition_latency_ms": round(result.timing.total_ms, 3) if result else 0.0,
            }
        unresolved = [
            track_id for track_id, state in self.tracks.items()
            if state.final_result is None or state.final_result.status != "accepted"
        ]
        unconfirmed_frames = [
            index for index, (detections, tracked) in enumerate(
                zip(self.detections_per_frame, self.tracked_outputs_per_frame)
            )
            if detections > tracked
        ]
        return {
            "detector": self.detector.metadata if self.detector is not None and isinstance(self.detector.metadata, dict) else {},
            "video": str(video),
            "output_video": str(output),
            "frames": frame_index,
            "source_fps": round(fps, 3),
            "duration_seconds": round(duration_seconds, 3),
            "frame_loop_seconds": round(frame_loop_seconds, 3),
            "recognition_drain_seconds": round(recognition_drain_seconds, 3),
            "analysis_seconds": round(analysis_seconds, 3),
            "annotation_seconds": round(annotation_seconds, 3),
            "total_wall_seconds": round(analysis_seconds + annotation_seconds, 3),
            "frame_loop_fps": round(frame_index / frame_loop_seconds, 3) if frame_loop_seconds else 0.0,
            "processing_fps": round(frame_index / analysis_seconds, 3) if analysis_seconds else 0.0,
            "real_time_factor": round(analysis_seconds / duration_seconds, 3) if duration_seconds else 0.0,
            "unique_tracks": len(self.tracks),
            "recognized_tracks": len(accepted),
            "unresolved_tracks": unresolved,
            "route_counts": route_counts,
            "local_percent": round(route_counts["private_local"] / len(results) * 100, 3) if results else 0.0,
            "recognition_attempts": pipeline_attempts,
            "low_level_ocr_calls": low_level_ocr_calls,
            "local_recognition_latency_ms": _summary(local_latencies),
            "total_recognition_latency_ms": _summary(total_latencies),
            "routing_latency_ms": _summary(routing_latencies),
            "plate_detection_time_ms": _summary(self.detection_times_ms),
            "plate_detection_warm_time_ms": _summary(self.detection_times_ms[1:]),
            "tracking_time_ms": _summary(self.tracking_times_ms),
            "crop_scoring_time_ms": _summary(self.crop_scoring_times_ms),
            "amortized_recognition_cost_per_frame_ms": round(sum(total_latencies) / frame_index, 3) if frame_index else 0.0,
            "candidate_plate_observations": total_observations,
            "deduplicated_detections": sum(self.detections_per_frame),
            "tracked_plate_outputs": sum(self.tracked_outputs_per_frame),
            "tracker_association_padding": self.association_padding,
            "unconfirmed_detection_count": sum(
                max(0, detections - tracked) for detections, tracked in
                zip(self.detections_per_frame, self.tracked_outputs_per_frame)
            ),
            "frames_with_unconfirmed_detections": len(unconfirmed_frames),
            "unconfirmed_detection_frame_ranges": _frame_ranges(unconfirmed_frames),
            "recognition_calls_avoided_vs_every_observation": max(0, total_observations - pipeline_attempts),
            "retry_count": sum(max(0, len(state.attempts) - 1) for state in self.tracks.values()),
            "replacement_count": sum(state.result_replaced for state in self.tracks.values()),
            "maximum_attempts_per_track": max(
                (len(state.attempts) for state in self.tracks.values()), default=0
            ),
            "recognition_policy": {
                "strong_quality": self.strong_quality,
                "strong_min_candidates": self.strong_min_candidates,
                "stable_min_candidates": self.min_candidates,
                "stable_observations": self.stable_observations,
                "disappearance_frames": self.disappearance_frames,
                "minimum_exit_candidates": self.minimum_exit_candidates,
                "recheck_quality_improvement": self.retry_improvement,
                "recheck_area_or_sharpness_improvement": self.recheck_detail_improvement,
                "replacement_confidence_gain": self.replacement_confidence_gain,
            },
            "recognition_worker_concurrency": 1,
            "max_pending_recognition_tasks": max(
                (len(snapshot) for snapshot in self.pending_snapshots), default=0
            ),
            "mean_pending_recognition_tasks": round(
                statistics.mean(len(snapshot) for snapshot in self.pending_snapshots), 3
            ) if self.pending_snapshots else 0.0,
            "initialization_ms": self.initialization_ms,
            "classification_initialization_ms": self.classifier_initialization_ms,
            "max_pending_capacity": self.max_pending,
            "deferred_attempts": self.deferred_events,
            "queue_wait_ms": _summary([item.queue_wait_ms for item in attempts]),
            "classification_latency_ms": _summary([item.result.timing.classification_ms for item in attempts]),
            "gpu_memory": _gpu_memory(),
            "ignored_short_tracks": [
                track_id for track_id, state in self.tracks.items()
                if not state.attempts and state.candidate_count < self.minimum_exit_candidates
            ],
            "tracks": track_metrics,
        }


def _build_tracker(path: Path, fps: float):
    from ultralytics.trackers.byte_tracker import BYTETracker

    with path.open(encoding="utf-8") as handle:
        arguments = SimpleNamespace(**yaml.safe_load(handle))
    return BYTETracker(arguments, frame_rate=max(1, int(round(fps))))
