"""Track plates in a prerecorded video and recognize the best crop once per track."""
from __future__ import annotations

import argparse
import json
import math
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
from run_video_pipeline.agent import OpenRouterPlateAgent
from run_video_pipeline.data_capture import ReviewStore
from run_video_pipeline.marker_localizer import TounesMarkerLocalizer
from run_video_pipeline.ocr import get_reader
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
# Keep generated video artifacts together in the directory from which the
# runner was launched. Individual CLI output options can still override this.
DEFAULT_OUTPUT_ROOT = Path.cwd() / "debug"
from run_video_pipeline.crop_geometry import (
    DEFAULT_ASSOCIATION_PADDING, TrackerDetections, _detection_for_track,
    crop_quality_evidence, crop_quality,
)


@dataclass
class RecognitionAttempt:
    result: UnifiedPlateResult
    low_level_ocr_calls: int
    agent_calls: int
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
            return None
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
        from .scalar_policy import replace_cached_read
        replace, reason = replace_cached_read(
            current.status if current else None,
            (current.plate_text_normalized or current.plate_text_raw) if current else None,
            current.category_confidence if current else 0,
            candidate.status, candidate.plate_text_normalized or candidate.plate_text_raw,
            candidate.category_confidence, confidence_gain)
        if replace:
            self.cached_attempt_index = len(self.attempts) - 1
            self.result_replaced = True
        self.replacement_reason = reason


class CountingReader:
    def __init__(self, delegate):
        self.delegate = delegate
        self._lock = threading.Lock()
        self.calls = 0

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
    return " ".join(value.encode("ascii", "ignore").decode("ascii").split())


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
            text = _safe_ascii(result.plate_text_normalized or result.plate_text_raw)
            category, source_name, status = result.plate_category, result.source, result.status
            confidence = result.category_confidence
            if status not in {"accepted", "rejected", "unavailable"}:
                status = "unavailable"
        x1, y1, x2, y2 = bbox
        color = (20, 190, 20) if status == "accepted" else (0, 165, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = (
            f"T{track_id_prefix}{track_id} | {text or '<no text>'} | {category} | "
            f"{confidence:.2f} | {source_name} | {status}"
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
    ):
        if min_candidates < 1:
            raise ValueError("min_candidates must be positive")
        if minimum_exit_candidates < 1:
            raise ValueError("minimum exit candidates must be positive")
        if retry_improvement <= 1.0:
            raise ValueError("retry_improvement must be greater than 1")
        if association_padding < 0:
            raise ValueError("association padding must be non-negative")
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
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plate-recognition")
        self.pending_snapshots: list[set[int]] = []
        self.detection_times_ms: list[float] = []
        self.tracking_times_ms: list[float] = []
        self.crop_scoring_times_ms: list[float] = []
        self.detections_per_frame: list[int] = []
        self.tracked_outputs_per_frame: list[int] = []

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
    ) -> RecognitionAttempt:
        before_ocr = self.reader.calls
        before_agent = self.pipeline.counters.snapshot()["agent_calls"]
        result = self.pipeline.process_crop(
            plate,
            original_image_ref=f"{source_ref}#frame={crop_frame}",
            camera_id=self.camera_id,
            track_id=f"{self.track_id_prefix}{track_id}",
            quality={
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
        after_agent = self.pipeline.counters.snapshot()["agent_calls"]
        return RecognitionAttempt(
            result=result,
            low_level_ocr_calls=self.reader.calls - before_ocr,
            agent_calls=int(after_agent - before_agent),
            crop_score=crop_score,
            crop_area=crop_area,
            crop_sharpness=crop_sharpness,
            crop_frame=crop_frame,
            trigger_reason=trigger_reason,
        )

    def _submit(self, state: TrackState, source_ref: str | Path, trigger_reason: str) -> None:
        if state.pending is not None or state.best_crop is None or len(state.attempts) >= 2:
            return
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
        )

    def _drain(self, source_ref: str | Path, frame_index: int | None) -> None:
        for state in self.tracks.values():
            if state.pending is not None and state.pending.done():
                state.record_attempt(state.pending.result(), self.replacement_confidence_gain)
                state.completed_frames.append(frame_index)
                state.pending = None
            if state.recheck_is_warranted(
                self.retry_improvement, self.recheck_detail_improvement
            ):
                self._submit(state, source_ref, "substantially_better_crop_recheck")

    def finalize_track(self, state: TrackState) -> None:
        """Append which immutable attempt became the final cached track result."""
        if state.final_metadata_written or state.final_result is None:
            return
        self.pipeline.store.mark_track_final(state.final_result.sample_id)
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

    def finish(self, source_ref: str | Path, *, end_trigger: str = "video_end") -> float:
        """Submit eligible end-of-source tracks and drain the recognition worker."""
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
        drain_started = time.perf_counter()
        while any(state.pending is not None for state in self.tracks.values()):
            self._drain(source_ref, None)
            self.pending_snapshots.append({
                track_id for track_id, state in self.tracks.items() if state.pending is not None
            })
            time.sleep(0.01)
        for state in self.tracks.values():
            self.finalize_track(state)
        self.executor.shutdown(wait=True)
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
            raise SystemExit(f"Could not open video: {video}")
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
            for route in ("private_local", "agent", "unknown")
        }
        total_observations = sum(state.candidate_count for state in self.tracks.values())
        pipeline_attempts = len(attempts)
        low_level_ocr_calls = sum(attempt.low_level_ocr_calls for attempt in attempts)
        agent_calls = sum(attempt.agent_calls for attempt in attempts)
        agent_observations = sum(
            state.candidate_count for state in self.tracks.values()
            if state.final_result is not None and state.final_result.route == "agent"
        )
        local_latencies = [attempt.result.timing.local_total_ms for attempt in attempts]
        api_latencies = [attempt.result.timing.api_ms for attempt in attempts if attempt.agent_calls]
        total_latencies = [attempt.result.timing.total_ms for attempt in attempts]
        routing_latencies = [attempt.result.timing.routing_ms for attempt in attempts]
        agent_track_ids = {
            track_id for track_id, state in self.tracks.items()
            if state.final_result is not None and state.final_result.route == "agent"
        }
        agent_pending_depths = [len(snapshot & agent_track_ids) for snapshot in self.pending_snapshots]
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
                "agent_calls": sum(item.agent_calls for item in state.attempts),
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
                        "text": attempt.result.plate_text_normalized
                        or attempt.result.plate_text_raw,
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
                "text": (result.plate_text_normalized or result.plate_text_raw) if result else None,
                "category": result.plate_category if result else "unknown",
                "confidence": round(result.category_confidence, 4) if result else 0.0,
                "route": result.route if result else "unknown",
                "source": result.source if result else "none",
                "status": result.status if result else "unresolved",
                "local_latency_ms": round(result.timing.local_total_ms, 3) if result else 0.0,
                "api_latency_ms": round(result.timing.api_ms, 3) if result else 0.0,
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
            "agent_percent": round(route_counts["agent"] / len(results) * 100, 3) if results else 0.0,
            "recognition_attempts": pipeline_attempts,
            "low_level_ocr_calls": low_level_ocr_calls,
            "agent_calls": agent_calls,
            "local_recognition_latency_ms": _summary(local_latencies),
            "api_latency_ms": _summary(api_latencies),
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
            "agent_calls_avoided_vs_every_plate_observation": max(
                0, total_observations - agent_calls
            ),
            "agent_calls_avoided_vs_every_agent_observation": max(0, agent_observations - agent_calls),
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
            "agent_request_retries": max(
                0, agent_calls - sum(attempt.agent_calls > 0 for attempt in attempts)
            ),
            "agent_failures": sum(
                attempt.agent_calls > 0 and attempt.result.status != "accepted" for attempt in attempts
            ),
            "recognition_worker_concurrency": 1,
            "max_pending_recognition_tasks": max(
                (len(snapshot) for snapshot in self.pending_snapshots), default=0
            ),
            "mean_pending_recognition_tasks": round(
                statistics.mean(len(snapshot) for snapshot in self.pending_snapshots), 3
            ) if self.pending_snapshots else 0.0,
            "max_pending_agent_tasks": max(agent_pending_depths, default=0),
            "mean_pending_agent_tasks": round(statistics.mean(agent_pending_depths), 3)
            if agent_pending_depths else 0.0,
            "agent_tasks_completed_before_video_end": sum(
                state.final_result is not None
                and state.final_result.route == "agent"
                and any(frame is not None for frame in state.completed_frames)
                for state in self.tracks.values()
            ),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metrics-output", type=Path)
    parser.add_argument("--capture-root", type=Path, default=config.CAPTURE_ROOT)
    parser.add_argument("--selected-crops-dir", type=Path)
    parser.add_argument("--track-summary-output", type=Path)
    parser.add_argument("--tracker-config", type=Path, default=DEFAULT_TRACKER)
    parser.add_argument("--agent-enabled", action="store_true")
    parser.add_argument("--conf", type=float, default=None)
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--association-padding", type=float, default=DEFAULT_ASSOCIATION_PADDING)
    parser.add_argument("--strong-quality", type=float, default=12000.0)
    parser.add_argument("--strong-min-candidates", type=int, default=3)
    parser.add_argument("--min-candidates", type=int, default=12)
    parser.add_argument("--stable-observations", type=int, default=5)
    parser.add_argument("--disappearance-frames", type=int, default=5)
    parser.add_argument("--minimum-exit-candidates", type=int, default=8)
    parser.add_argument("--retry-improvement", type=float, default=1.50)
    parser.add_argument("--recheck-detail-improvement", type=float, default=1.25)
    parser.add_argument("--replacement-confidence-gain", type=float, default=0.02)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"Could not open video: {args.video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    capture.release()
    mode = "agent" if args.agent_enabled else "local_only"
    output = args.output or DEFAULT_OUTPUT_ROOT / f"{args.video.stem}_{mode}_annotated.mp4"
    metrics_output = args.metrics_output or output.with_suffix(".metrics.json")

    counting_reader = CountingReader(get_reader())
    pipeline = PrototypePlatePipeline(
        counting_reader,
        TounesMarkerLocalizer(config.WORD_DETECTOR_PATH),
        OpenRouterPlateAgent(enabled=args.agent_enabled),
        ReviewStore(args.capture_root),
    )
    runner = VideoPrototypeRunner(
        pipeline,
        PlateDetector(config.PLATE_DETECTOR_PATH),
        _build_tracker(args.tracker_config, fps),
        counting_reader,
        association_padding=args.association_padding,
        strong_quality=args.strong_quality,
        strong_min_candidates=args.strong_min_candidates,
        min_candidates=args.min_candidates,
        stable_observations=args.stable_observations,
        disappearance_frames=args.disappearance_frames,
        minimum_exit_candidates=args.minimum_exit_candidates,
        retry_improvement=args.retry_improvement,
        recheck_detail_improvement=args.recheck_detail_improvement,
        replacement_confidence_gain=args.replacement_confidence_gain,
    )
    metrics = runner.run(
        args.video, output, conf=args.conf, imgsz=args.imgsz, max_frames=args.max_frames,
        selected_crops_dir=args.selected_crops_dir,
    )
    metrics["agent_enabled"] = args.agent_enabled
    metrics_output.parent.mkdir(parents=True, exist_ok=True)
    metrics_output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    track_summary_output = args.track_summary_output or metrics_output.with_name(
        f"{metrics_output.stem}.tracks.json"
    )
    track_summary_output.parent.mkdir(parents=True, exist_ok=True)
    track_summary_output.write_text(
        json.dumps(metrics["tracks"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
