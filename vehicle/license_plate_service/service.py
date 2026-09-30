"""Serving-layer adapters for the standalone license-plate pipelines."""
from __future__ import annotations

import threading
import uuid
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from django.conf import settings


def _ensure_pipeline_import_path() -> None:
    """Expose the sibling pipeline packages using the imports used by their CLIs."""
    package_root = str(Path(__file__).resolve().parent)
    if package_root not in sys.path:
        sys.path.insert(0, package_root)


def _jobs_root() -> Path:
    root = Path(settings.MEDIA_ROOT) / "license_plate"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_video_analysis(video: Path, *, job_id: str, max_frames: int | None = None,
                       agent_enabled: bool = False) -> dict:
    """Run the packaged video pipeline and return its JSON-safe metrics."""
    import cv2
    _ensure_pipeline_import_path()
    from run_video_pipeline import config
    from run_video_pipeline.agent import OpenRouterPlateAgent
    from run_video_pipeline.data_capture import ReviewStore
    from run_video_pipeline.marker_localizer import TounesMarkerLocalizer
    from run_video_pipeline.ocr import get_reader
    from run_video_pipeline.plate_detector import PlateDetector
    from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
    from run_video_pipeline.run_video_prototype import (
        DEFAULT_ASSOCIATION_PADDING, DEFAULT_TRACKER, CountingReader,
        VideoPrototypeRunner, _build_tracker,
    )

    job_dir = _jobs_root() / job_id
    output = job_dir / "annotated.mp4"
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError("The uploaded file is not a readable video.")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    capture.release()
    counting_reader = CountingReader(get_reader())
    pipeline = PrototypePlatePipeline(
        counting_reader, TounesMarkerLocalizer(config.WORD_DETECTOR_PATH),
        OpenRouterPlateAgent(enabled=agent_enabled), ReviewStore(job_dir / "captures"),
    )
    runner = VideoPrototypeRunner(
        pipeline, PlateDetector(config.PLATE_DETECTOR_PATH),
        _build_tracker(DEFAULT_TRACKER, fps), counting_reader,
        association_padding=DEFAULT_ASSOCIATION_PADDING,
    )
    metrics = runner.run(video, output, max_frames=max_frames,
                         selected_crops_dir=job_dir / "selected_crops")
    metrics.update({
        "agent_enabled": agent_enabled, "job_id": job_id,
        "output_video_url": f"/api/license-plate/jobs/{job_id}/annotated.mp4/",
    })
    return metrics


class RTSPManager:
    """Own at most one in-process camera worker and its latest UI snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._state: dict = {"running": False, "status": "idle", "results": []}
        self._preview: bytes | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def preview(self) -> bytes | None:
        with self._lock:
            return self._preview

    def start(self, payload: dict) -> dict:
        url = str(payload.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ValueError("Enter a valid RTSP or RTSPS camera URL.")
        _ensure_pipeline_import_path()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A camera session is already running.")
            session_id = uuid.uuid4().hex
            session_dir = _jobs_root() / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            stop_event = threading.Event()
            from run_rtsp_pipeline.run_rtsp_prototype import DEFAULT_TRACKER
            args = SimpleNamespace(
                url=url, camera_id=str(payload.get("camera_id") or "cam-1")[:80],
                agent_enabled=bool(payload.get("agent_enabled", False)),
                max_frames=_optional_positive_int(payload.get("max_frames")),
                max_seconds=_optional_positive_float(payload.get("max_seconds")),
                read_timeout=5.0, reconnect_delay=1.0, max_reconnects=3,
                record_output=(session_dir / "recording.mp4") if payload.get("record") else None,
                capture_root=session_dir / "captures",
                selected_crops_dir=session_dir / "selected_crops",
                tracker_config=DEFAULT_TRACKER, conf=None, imgsz=None,
            )
            self._state = {
                "running": True, "status": "connecting", "session_id": session_id,
                "camera_id": args.camera_id, "processed_frames": 0, "results": [],
            }
            self._preview = None
            self._stop_event = stop_event
            self._thread = threading.Thread(
                target=self._run, args=(args, stop_event, session_id), daemon=True,
                name="license-plate-rtsp",
            )
            self._thread.start()
            return dict(self._state)

    def _run(self, args, stop_event: threading.Event, session_id: str) -> None:
        from run_rtsp_pipeline.run_rtsp_prototype import run_rtsp

        def on_update(update: dict) -> None:
            with self._lock:
                phase = "running" if update.get("running") else "complete"
                self._state = {**self._state, **update, "status": phase}

        def on_frame(frame) -> None:
            import cv2
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if ok:
                with self._lock:
                    self._preview = encoded.tobytes()
        try:
            summary = run_rtsp(args, stop_event, on_frame=on_frame, on_update=on_update)
            if args.record_output and args.record_output.exists():
                summary["recording_url"] = f"/api/license-plate/jobs/{session_id}/recording.mp4/"
            with self._lock:
                self._state = {**self._state, **summary, "running": False, "status": "complete"}
        except Exception as exc:
            with self._lock:
                self._state = {**self._state, "running": False, "status": "error", "error": str(exc)}

    def stop(self) -> dict:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            if self._state.get("running"):
                self._state = {**self._state, "status": "stopping"}
            return dict(self._state)


def _optional_positive_int(value) -> int | None:
    if value in (None, ""):
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError("max_frames must be positive.")
    return parsed


def _optional_positive_float(value) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("max_seconds must be positive.")
    return parsed


rtsp_manager = RTSPManager()
