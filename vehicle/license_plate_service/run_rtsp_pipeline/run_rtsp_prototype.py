"""Run the validated track-based plate pipeline on one RTSP camera."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import cv2

from run_video_pipeline.classification import create_classifier
from run_video_pipeline.policies import compact_result
import run_rtsp_pipeline.config as config
from run_rtsp_pipeline.data_capture import ReviewStore
from run_rtsp_pipeline.marker_localizer import TounesMarkerLocalizer
from run_rtsp_pipeline.plate_detector import PlateDetector
from run_rtsp_pipeline.prototype_pipeline import PrototypePlatePipeline
from run_rtsp_pipeline.run_video_prototype import (
    DEFAULT_TRACKER,
    CountingReader,
    TrackState,
    VideoPrototypeRunner,
    _build_tracker,
    annotate_frame,
    save_track_crops,
)

CaptureFactory = Callable[[str, int], object]
FrameCallback = Callable[[object], None]
UpdateCallback = Callable[[dict], None]
LIVE_HISTORY_LIMIT = 1000
STALE_TRACK_RETENTION_FRAMES = 300
LIVE_PREVIEW_FPS = 8.0
log = logging.getLogger(__name__)


@dataclass
class LiveTotals:
    tracks_seen: int = 0
    tracks_recognized: int = 0
    local_results: int = 0
    unresolved_track_count: int = 0
    ocr_calls: int = 0
    recognition_attempts: int = 0
    recent_unresolved_tracks: deque[str] = field(
        default_factory=lambda: deque(maxlen=100)
    )

    def add_state(self, state: TrackState, persisted_track_id: str) -> None:
        self.tracks_seen += 1
        result = state.final_result
        if result is not None and result.status == "accepted":
            self.tracks_recognized += 1
        else:
            self.unresolved_track_count += 1
            self.recent_unresolved_tracks.append(persisted_track_id)
        if result is not None:
            self.local_results += result.route == "private_local"
        self.ocr_calls += sum(attempt.low_level_ocr_calls for attempt in state.attempts)
        self.recognition_attempts += len(state.attempts)


def _persisted_track_id(session_id: int, track_id: int) -> str:
    return f"s{session_id}-{track_id}"


def _retire_stale_tracks(
    runner: VideoPrototypeRunner,
    totals: LiveTotals,
    *,
    session_id: int,
    frame_index: int,
    retention_frames: int = STALE_TRACK_RETENTION_FRAMES,
    selected_crops_dir: Path | None = None,
) -> None:
    stale_ids = [
        track_id for track_id, state in runner.tracks.items()
        if state.pending is None and frame_index - state.last_frame >= retention_frames
    ]
    for track_id in stale_ids:
        state = runner.tracks.pop(track_id)
        if state.deferred_reason:
            runner.record_capacity_expiry(state)
        finalize_track = getattr(runner, "finalize_track", None)
        if finalize_track is not None:
            finalize_track(state)
        if selected_crops_dir is not None:
            save_track_crops(
                selected_crops_dir / f"session_{session_id}", {track_id: state}
            )
        totals.add_state(state, _persisted_track_id(session_id, track_id))


def _consume_runner(
    runner: VideoPrototypeRunner, totals: LiveTotals, *, session_id: int
) -> None:
    for track_id, state in runner.tracks.items():
        totals.add_state(state, _persisted_track_id(session_id, track_id))


def _trim_live_history(runner: VideoPrototypeRunner) -> None:
    for name in (
        "pending_snapshots",
        "detection_times_ms",
        "tracking_times_ms",
        "crop_scoring_times_ms",
        "detections_per_frame",
        "tracked_outputs_per_frame",
    ):
        values = getattr(runner, name)
        if len(values) > LIVE_HISTORY_LIMIT:
            del values[:-LIVE_HISTORY_LIMIT]


def _limit_reason(
    frame_index: int,
    elapsed_seconds: float,
    *,
    max_frames: int | None,
    max_seconds: float | None,
) -> str | None:
    if max_frames is not None and frame_index >= max_frames:
        return "maximum frames reached"
    if max_seconds is not None and elapsed_seconds >= max_seconds:
        return "maximum runtime reached"
    return None


def live_track_results(runner: VideoPrototypeRunner) -> list[dict]:
    """Return the bounded-manager payload for current track states."""
    output = []
    for track_id, state in sorted(runner.tracks.items()):
        result = state.final_result
        persisted_id = f"{runner.track_id_prefix}{track_id}"
        if result is None or (state.pending is not None and result.status != "accepted"):
            output.append({
                "track_id": persisted_id,
                "plate_text": "",
                "plate_category": "unknown",
                "confidence": 0.0,
                "source": "none",
                "status": "pending",
            })
            continue
        status = result.status
        output.append({
            "track_id": persisted_id,
            **compact_result(result),
            "identity_conflict": state.identity_conflict,
            "category_conflict": state.category_conflict,
            "plate_category": result.plate_category,
            "confidence": round(result.category_confidence, 4),
            "source": result.source,
            "status": status,
        })
    return output


def _notify(callback: UpdateCallback | FrameCallback | None, payload) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        log.debug("live callback failed", exc_info=True)


def _open_capture(url: str, timeout_ms: int):
    params = [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
        timeout_ms,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC,
        timeout_ms,
    ]
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)


class RTSPFrameReader:
    """Small reconnecting OpenCV reader for one RTSP source."""

    def __init__(
        self,
        url: str,
        *,
        read_timeout_seconds: float = 5.0,
        reconnect_delay_seconds: float = 1.0,
        max_reconnects: int = -1,
        capture_factory: CaptureFactory = _open_capture,
    ):
        if read_timeout_seconds <= 0:
            raise ValueError("read timeout must be positive")
        if reconnect_delay_seconds < 0:
            raise ValueError("reconnect delay must be non-negative")
        if max_reconnects < -1:
            raise ValueError("max reconnects must be -1 (unlimited) or non-negative")
        self.url = url
        self.timeout_ms = max(1, int(read_timeout_seconds * 1000))
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.max_reconnects = max_reconnects
        self.capture_factory = capture_factory
        self.capture = None
        self.connection_attempts = 0
        self.reconnect_count = 0
        self.connection_generation = 0
        self.connection_failures = 0
        self.failed_frame_reads = 0
        self.frames_read = 0
        self.source_fps = 0.0
        self.stop_reason: str | None = None

    def _connect_once(self) -> bool:
        is_reconnect = self.connection_attempts > 0
        if (
            is_reconnect
            and self.max_reconnects >= 0
            and self.reconnect_count >= self.max_reconnects
        ):
            self.stop_reason = "maximum reconnect attempts reached"
            return False
        self.connection_attempts += 1
        if is_reconnect:
            self.reconnect_count += 1
        try:
            capture = self.capture_factory(self.url, self.timeout_ms)
        except (cv2.error, OSError):
            self.connection_failures += 1
            return False
        if not capture.isOpened():
            self.connection_failures += 1
            capture.release()
            return False
        self.capture = capture
        self.connection_generation += 1
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps > 0:
            self.source_fps = fps
        return True

    def ensure_connected(self, stop_event: threading.Event) -> bool:
        while not stop_event.is_set():
            if self.capture is not None and self.capture.isOpened():
                return True
            if self._connect_once():
                return True
            if self.stop_reason:
                return False
            if stop_event.wait(self.reconnect_delay_seconds):
                break
        self.stop_reason = self.stop_reason or "stop requested"
        return False

    def read(self, stop_event: threading.Event):
        while not stop_event.is_set():
            if not self.ensure_connected(stop_event):
                return False, None
            try:
                ok, frame = self.capture.read()
            except (cv2.error, OSError):
                ok, frame = False, None
            if ok and frame is not None and frame.size:
                self.frames_read += 1
                return True, frame
            self.failed_frame_reads += 1
            self._release_capture()
            if stop_event.wait(self.reconnect_delay_seconds):
                break
        self.stop_reason = self.stop_reason or "stop requested"
        return False, None

    def _release_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def close(self) -> None:
        self._release_capture()


def _open_writer(path: Path, frame, fps: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps if fps > 0 else 25.0,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"could not open output video writer: {path}")
    return writer


def _build_live_runner(
    args: SimpleNamespace,
    pipeline: PrototypePlatePipeline,
    detector: PlateDetector,
    counting_reader: CountingReader,
    *,
    source_fps: float,
    session_id: int,
) -> VideoPrototypeRunner:
    return VideoPrototypeRunner(
        pipeline,
        detector,
        _build_tracker(args.tracker_config, source_fps or 25.0),
        counting_reader,
        camera_id=args.camera_id,
        track_id_prefix=f"s{session_id}-",
    )


def _rtsp_summary(
    runner: VideoPrototypeRunner,
    reader: RTSPFrameReader,
    totals: LiveTotals,
    *,
    session_id: int,
    camera_id: str,
    processed_frames: int,
    loop_seconds: float,
    recognition_drain_seconds: float,
    record_output: Path | None,
) -> dict:
    active = LiveTotals()
    _consume_runner(runner, active, session_id=session_id)
    unresolved = list(totals.recent_unresolved_tracks)
    unresolved.extend(active.recent_unresolved_tracks)
    return {
        "camera_id": camera_id,
        "processed_frames": processed_frames,
        "failed_frame_reads": reader.failed_frame_reads,
        "connection_failures": reader.connection_failures,
        "reconnect_count": reader.reconnect_count,
        "source_fps": round(reader.source_fps, 3) if reader.source_fps > 0 else None,
        "processing_fps": round(processed_frames / loop_seconds, 3) if loop_seconds else 0.0,
        "tracks_seen": totals.tracks_seen + active.tracks_seen,
        "tracks_recognized": totals.tracks_recognized + active.tracks_recognized,
        "local_results": totals.local_results + active.local_results,
        "unresolved_track_count": (
            totals.unresolved_track_count + active.unresolved_track_count
        ),
        "unresolved_tracks": unresolved[-100:],
        "ocr_calls": totals.ocr_calls + active.ocr_calls,
        "recognition_attempts": totals.recognition_attempts + active.recognition_attempts,
        "recognition_drain_seconds": round(recognition_drain_seconds, 3),
        "connection_sessions": reader.connection_generation,
        "active_track_states": len(runner.tracks),
        "state_retention_frames": STALE_TRACK_RETENTION_FRAMES,
        "diagnostic_history_frames": LIVE_HISTORY_LIMIT,
        "record_output": str(record_output) if record_output else None,
        "stop_reason": reader.stop_reason or "completed",
        "scheduling": {"capacity": runner.max_pending, "deferred_events": runner.deferred_events},
    }


def run_rtsp(
    args: SimpleNamespace,
    stop_event: threading.Event,
    *,
    on_frame: FrameCallback | None = None,
    on_update: UpdateCallback | None = None,
) -> dict:
    """Run the existing RTSP pipeline with optional serving-layer callbacks."""
    reader = RTSPFrameReader(
        args.url,
        read_timeout_seconds=args.read_timeout,
        reconnect_delay_seconds=args.reconnect_delay,
        max_reconnects=args.max_reconnects,
    )
    writer = None
    runner = None
    frame_index = 0
    recognition_drain_seconds = 0.0
    shutdown_drain_seconds = 0.0
    totals = LiveTotals()
    session_id = 0
    loop_started = time.perf_counter()
    last_preview_at = 0.0
    source_ref = f"rtsp-camera:{args.camera_id}:session=0"
    if not reader.ensure_connected(stop_event):
        reader.close()
        summary = {
            "camera_id": args.camera_id,
            "processed_frames": 0,
            "failed_frame_reads": reader.failed_frame_reads,
            "connection_failures": reader.connection_failures,
            "reconnect_count": reader.reconnect_count,
            "source_fps": None,
            "processing_fps": 0.0,
            "tracks_seen": 0,
            "tracks_recognized": 0,
            "local_results": 0,
            "unresolved_track_count": 0,
            "unresolved_tracks": [],
            "ocr_calls": 0,
            "recognition_attempts": 0,
            "recognition_drain_seconds": 0.0,
            "connection_sessions": reader.connection_generation,
            "active_track_states": 0,
            "state_retention_frames": STALE_TRACK_RETENTION_FRAMES,
            "diagnostic_history_frames": LIVE_HISTORY_LIMIT,
            "record_output": str(args.record_output) if args.record_output else None,
            "stop_reason": reader.stop_reason or "RTSP source unavailable",
        }
        _notify(on_update, {**summary, "running": False, "results": []})
        return summary
    try:
        counting_reader = CountingReader()
        pipeline = PrototypePlatePipeline(
            counting_reader,
            TounesMarkerLocalizer(config.WORD_DETECTOR_PATH),
            ReviewStore(args.capture_root),
            classifier_factory=create_classifier,
        )
        detector = PlateDetector(config.PLATE_DETECTOR_PATH)
        session_id = reader.connection_generation
        source_ref = f"rtsp-camera:{args.camera_id}:session={session_id}"
        runner = _build_live_runner(
            args, pipeline, detector, counting_reader,
            source_fps=reader.source_fps, session_id=session_id,
        )
        while not stop_event.is_set():
            limit_reason = _limit_reason(
                frame_index,
                time.perf_counter() - loop_started,
                max_frames=args.max_frames,
                max_seconds=args.max_seconds,
            )
            if limit_reason:
                reader.stop_reason = limit_reason
                break
            ok, frame = reader.read(stop_event)
            if not ok:
                break
            if reader.connection_generation != session_id:
                recognition_drain_seconds += runner.finish(
                    source_ref, end_trigger="disconnect"
                )
                if args.selected_crops_dir:
                    save_track_crops(
                        args.selected_crops_dir / f"session_{session_id}", runner.tracks
                    )
                _consume_runner(runner, totals, session_id=session_id)
                session_id = reader.connection_generation
                source_ref = f"rtsp-camera:{args.camera_id}:session={session_id}"
                runner = _build_live_runner(
                    args, pipeline, detector, counting_reader,
                    source_fps=reader.source_fps, session_id=session_id,
                )
            annotations = runner.process_frame(
                frame, frame_index, source_ref, conf=args.conf, imgsz=args.imgsz
            )
            now = time.perf_counter()
            emit_preview = (
                on_frame is not None
                and now - last_preview_at >= 1.0 / LIVE_PREVIEW_FPS
            )
            if args.record_output or emit_preview:
                annotated = frame.copy()
                annotate_frame(
                    annotated, annotations, runner.tracks,
                    track_id_prefix=runner.track_id_prefix,
                )
                if writer is None:
                    if args.record_output:
                        writer = _open_writer(
                            args.record_output, annotated, reader.source_fps
                        )
                if writer is not None:
                    writer.write(annotated)
                if emit_preview:
                    last_preview_at = now
                    _notify(on_frame, annotated)
            _retire_stale_tracks(
                runner,
                totals,
                session_id=session_id,
                frame_index=frame_index,
                selected_crops_dir=args.selected_crops_dir,
            )
            _trim_live_history(runner)
            frame_index += 1
            current_results = live_track_results(runner)
            _notify(on_update, {
                "running": True,
                "camera_id": args.camera_id,
                "processed_frames": frame_index,
                "processing_fps": round(
                    frame_index / max(time.perf_counter() - loop_started, 1e-9), 3
                ),
                "reconnect_count": reader.reconnect_count,
                "active_tracks": len(runner.tracks),
                "recognized_tracks": totals.tracks_recognized + sum(
                    item["status"] == "accepted" for item in current_results
                ),
                "results": current_results,
                "scheduling": {"pending": sum(t.pending is not None for t in runner.tracks.values()),
                               "deferred": sum(t.deferred_reason is not None for t in runner.tracks.values()),
                               "capacity": runner.max_pending, "deferred_events": runner.deferred_events},
            })
    finally:
        if stop_event.is_set() and reader.stop_reason is None:
            reader.stop_reason = "stop requested"
        reader.close()
        try:
            if runner is not None:
                shutdown_drain_seconds = runner.finish(
                    source_ref, end_trigger="stream_end"
                )
                recognition_drain_seconds += shutdown_drain_seconds
        finally:
            if writer is not None:
                writer.release()

    loop_seconds = time.perf_counter() - loop_started - shutdown_drain_seconds
    if args.selected_crops_dir:
        save_track_crops(
            args.selected_crops_dir / f"session_{session_id}", runner.tracks
        )
    summary = _rtsp_summary(
        runner,
        reader,
        totals,
        session_id=session_id,
        camera_id=args.camera_id,
        processed_frames=frame_index,
        loop_seconds=max(0.0, loop_seconds),
        recognition_drain_seconds=recognition_drain_seconds,
        record_output=args.record_output,
    )
    summary["detector"] = detector.metadata
    _notify(on_update, {
        **summary,
        "running": False,
        "active_tracks": len(runner.tracks),
        "results": live_track_results(runner),
    })
    return summary
