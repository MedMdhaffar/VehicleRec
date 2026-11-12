"""Vehicle detection, tracking and VLM recognition for video and frame processing."""
from __future__ import annotations

import gc
import json
import logging
import time
import tempfile
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


class VehicleAnalysisService:
    def __init__(
        self,
        device: str = "auto",
        enable_manufacturer: bool = False,
        model_path: str | Path | None = None,
        tracker_path: str | Path | None = None,
        frame_rate: float = 15.0,
        model_settings: dict | None = None,
        _detector_tracker=None,
    ) -> None:
        self.pipeline = None
        self._manufacturer = None
        from .manufacturer.config import RecognitionConfig, parse_model_settings
        from .manufacturer.service import ManufacturerService
        from .manufacturer.tracking import TrackRecognizer
        recognizer = None
        config = replace(RecognitionConfig.from_env(), **parse_model_settings(model_settings))
        self.model_settings = config.model_settings()
        try:
            if enable_manufacturer:
                self._manufacturer = ManufacturerService(config)
                recognizer = TrackRecognizer(self._manufacturer, config)
            from .detector import VehicleDetectorTracker
            from .pipeline import VehiclePipeline
            detector_tracker = _detector_tracker or VehicleDetectorTracker(device, model_path, tracker_path, frame_rate)
            self.pipeline = VehiclePipeline(detector_tracker, recognizer=recognizer)
        except BaseException:
            self.close()
            raise

    def close(self):
        """Release job-owned models when Django completes or fails a request."""
        manufacturer = getattr(self, "_manufacturer", None)
        self._manufacturer = None
        try:
            if manufacturer is not None:
                manufacturer.close()
        finally:
            self.pipeline = None
            gc.collect()
            import torch
            if torch.cuda.is_initialized():
                torch.cuda.empty_cache()

    def process_frame(self, frame, frame_index: int):
        return self.pipeline.process_frame(frame, frame_index)

    def finish(self) -> list[dict[str, Any]]:
        return [profile.to_dict() for profile in self.pipeline.finish()]

    def process_video(
        self,
        video: str | Path,
        output_path: str | Path | None = None,
        json_output: str | Path | None = None,
        max_frames: int | None = None,
    ) -> dict[str, Any]:
        source = Path(video).expanduser().resolve()
        import cv2
        from .annotation import draw_overlay
        if not source.is_file():
            raise FileNotFoundError(f"Input video not found: {source}")
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open input video: {source}")

        if self.pipeline.recognizer and (output_path or json_output):
            self.pipeline.recognizer.output_dir = Path(output_path or json_output).resolve().parent / "crops"
        render_records = tempfile.TemporaryFile(mode="w+t") if output_path else None
        writer = None
        frames_processed = 0
        started = time.monotonic()
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        total_frames = min(total_frames, max_frames) if max_frames and total_frames > 0 else total_frames
        log.info("Video opened: frames=%s fps=%.2f; starting detection/tracking", total_frames or "unknown", capture.get(cv2.CAP_PROP_FPS))
        last_progress = started
        try:
            while max_frames is None or frames_processed < max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                self.process_frame(frame, frames_processed)
                if render_records is not None:
                    render_records.write(json.dumps([
                        {"track_id": p.track_id, "first_frame": p.first_frame, "bbox": p.bbox}
                        for p in self.pipeline.active_profiles.values()]) + "\n")
                frames_processed += 1
                if time.monotonic() - last_progress >= 5:
                    log.info("Video progress: frame=%d/%s active=%d finalized=%d elapsed=%.1fs", frames_processed,
                             total_frames or "unknown", len(self.pipeline.active_profiles),
                             len(self.pipeline.finalized_profiles), time.monotonic() - started)
                    last_progress = time.monotonic()
        except BaseException:
            if render_records is not None:
                render_records.close()
            raise
        finally:
            capture.release()
            if writer is not None:
                writer.release()

        log.info("Detection finished: %d frames; finalizing remaining tracks", frames_processed)
        vehicles = self.finish()
        if render_records is not None:
            log.info("Rendering final recognition overlays: %d frames", frames_processed)
            render_started = time.monotonic()
            last_render_progress = render_started
            try:
                final = {(p.track_id, p.first_frame): p.recognition for p in self.pipeline.finalized_profiles}
                render_records.seek(0)
                capture = cv2.VideoCapture(str(source))
                destination = Path(output_path).expanduser().resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"),
                    capture.get(cv2.CAP_PROP_FPS) or 15.0,
                    (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))))
                if not capture.isOpened() or not writer.isOpened():
                    raise RuntimeError("Could not open final annotation pass")
                for rendered, line in enumerate(render_records, 1):
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError("Video ended during final annotation pass")
                    profiles = [SimpleNamespace(**record, recognition=final[(record["track_id"], record["first_frame"])])
                                for record in json.loads(line)]
                    writer.write(draw_overlay(frame, profiles))
                    if time.monotonic() - last_render_progress >= 5:
                        log.info("Rendering progress: %d/%d frames", rendered, frames_processed)
                        last_render_progress = time.monotonic()
            finally:
                capture.release()
                if writer is not None:
                    writer.release()
                render_records.close()
            log.info("Final rendering complete in %.1fs", time.monotonic() - render_started)
        elapsed = time.monotonic() - started
        result = {
            "video": str(source),
            "model_settings": self.model_settings,
            "frames_processed": frames_processed,
            "tracks_detected": len(vehicles),
            "scene_cuts": list(self.pipeline.scene_cuts),
            "processing_seconds": round(elapsed, 3),
            "vehicles": vehicles,
            "events": [],
            "performance": self.pipeline.perf.report_dict(),
        }
        if json_output:
            destination = Path(json_output).expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
