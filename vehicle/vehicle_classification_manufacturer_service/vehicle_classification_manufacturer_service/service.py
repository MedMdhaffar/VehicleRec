"""Public Python service for standalone video and frame processing."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


class VehicleAnalysisService:
    def __init__(
        self,
        device: str = "auto",
        enable_roi: bool = True,
        enable_manufacturer: bool = False,
        roi_path: str | Path | None = None,
        model_path: str | Path | None = None,
        tracker_path: str | Path | None = None,
        frame_rate: float = 15.0,
        _detector_tracker=None,
    ) -> None:
        if enable_manufacturer:
            raise ValueError("manufacturer analysis is unavailable because its current crop scorer violates this package's image-processing isolation")
        from .detector import VehicleDetectorTracker
        from .pipeline import VehiclePipeline
        from .roi import RoiConfig
        roi_config = RoiConfig.load(roi_path) if enable_roi and roi_path else (RoiConfig() if enable_roi else None)
        detector_tracker = _detector_tracker or VehicleDetectorTracker(device, model_path, tracker_path, frame_rate)
        self.pipeline = VehiclePipeline(detector_tracker, roi_config)

    def process_frame(self, frame, frame_index: int):
        return self.pipeline.process_frame(frame, frame_index)

    def finish(self) -> list[dict[str, Any]]:
        return [profile.to_dict() for profile in self.pipeline.finish()]

    def process_video(
        self,
        video: str | Path,
        output_path: str | Path | None = None,
        json_output: str | Path | None = None,
        events_output: str | Path | None = None,
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

        writer = None
        frames_processed = 0
        printed_final: set[int] = set()
        started = time.monotonic()
        events_stream = None
        try:
            if events_output:
                event_path = Path(events_output).expanduser().resolve()
                event_path.parent.mkdir(parents=True, exist_ok=True)
                events_stream = event_path.open("w", encoding="utf-8")
            while max_frames is None or frames_processed < max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                update = self.process_frame(frame, frames_processed)
                for profile in update.finalized:
                    printed_final.add(profile["track_id"])
                    print(f"[FINAL] id={profile['track_id']} type={profile['vehicle_type']} confidence={profile['type_confidence']:.2f} first_frame={profile['first_frame']} last_frame={profile['last_frame']}")
                for event in update.events:
                    print(f"[ROI] track={event.get('track_id', '-')} event={event['event']} roi={event.get('zone', '-')}")
                    if events_stream:
                        events_stream.write(json.dumps(event) + "\n")
                        events_stream.flush()
                if output_path:
                    if writer is None:
                        destination = Path(output_path).expanduser().resolve()
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        fps = capture.get(cv2.CAP_PROP_FPS) or 15.0
                        height, width = frame.shape[:2]
                        writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
                        if not writer.isOpened():
                            raise RuntimeError(f"Could not create output video: {destination}")
                    annotated = draw_overlay(frame, self.pipeline.active_profiles.values(), self.pipeline.roi.config if self.pipeline.roi else None, self.pipeline.roi)
                    writer.write(annotated)
                frames_processed += 1
                if frames_processed % 100 == 0:
                    print(f"Processing frame {frames_processed} ({self.pipeline.perf.report_dict()['fps']:.1f} FPS)")
        finally:
            capture.release()
            if writer is not None:
                writer.release()
            if events_stream is not None:
                events_stream.close()

        vehicles = self.finish()
        for profile in vehicles:
            if profile["track_id"] not in printed_final:
                print(f"[FINAL] id={profile['track_id']} type={profile['vehicle_type']} confidence={profile['type_confidence']:.2f} first_frame={profile['first_frame']} last_frame={profile['last_frame']}")
        elapsed = time.monotonic() - started
        result = {
            "video": str(source),
            "frames_processed": frames_processed,
            "tracks_detected": len(vehicles),
            "processing_seconds": round(elapsed, 3),
            "vehicles": vehicles,
            "events": list(self.pipeline.events),
            "performance": self.pipeline.perf.report_dict(),
        }
        if json_output:
            destination = Path(json_output).expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
