"""Public Python service for standalone video and frame processing."""
from __future__ import annotations

import json
import logging
import math
from contextlib import ExitStack
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
        *,
        observation_clock=None,
    ) -> None:
        if enable_manufacturer:
            raise ValueError("manufacturer analysis is unavailable because its current crop scorer violates this package's image-processing isolation")
        from .pipeline import VehiclePipeline
        from .roi import RoiConfig
        roi_config = RoiConfig.load(roi_path) if enable_roi and roi_path else (RoiConfig() if enable_roi else None)
        if _detector_tracker is None:
            from .detector import VehicleDetectorTracker
            _detector_tracker = VehicleDetectorTracker(device, model_path, tracker_path, frame_rate)
        self.pipeline = VehiclePipeline(_detector_tracker, roi_config, observation_clock=observation_clock)

    def capture_receipt(self, frame_index: int, *, media_pts_reader=None):
        """External decoder boundary: call immediately after successful read."""
        return self.pipeline.capture_receipt(frame_index, media_pts_reader=media_pts_reader)

    def process_frame(self, frame, frame_index: int, *, receipt=None):
        return self.pipeline.process_frame(frame, frame_index, receipt=receipt)

    def finish(self, cause=None, *, trigger=None) -> list[dict[str, Any]]:
        return [profile.to_dict() for profile in self.pipeline.finish(cause, trigger=trigger)]

    def _finish_for(self, cause: str, *, trigger=None):
        if self.pipeline.lifecycle is None:
            return self.finish(trigger=trigger)
        from .observations import RunEnd
        return self.finish(RunEnd(cause), trigger=trigger)

    def stop(self):
        return self._finish_for("service_stop")

    def clock_fault(self, reason: str):
        self.pipeline.clock_fault(reason)

    def reset_tracking(self):
        return self.pipeline.reset_tracking()

    def claim_closure_delivery(self, owner):
        self.pipeline.claim_closure_delivery(owner)

    def pending_finalized_observations(self, *, owner=None):
        return self.pipeline.pending_finalized_observations(owner=owner)

    def acknowledge_finalized_observations(self, service_ids, *, owner=None):
        self.pipeline.acknowledge_finalized_observations(service_ids, owner=owner)

    def drain_finalized_observations(self, *, owner=None):
        return self.pipeline.drain_finalized_observations(owner=owner)

    def drain_finalized_profiles(self):
        return [profile.to_dict() for profile in self.pipeline.drain_finalized_profiles()]

    def process_video(
        self,
        video: str | Path,
        output_path: str | Path | None = None,
        json_output: str | Path | None = None,
        events_output: str | Path | None = None,
        max_frames: int | None = None,
        *,
        stop_requested=None,
        on_observation=None,
        _capture_factory=None,
        _media_pts_reader=None,
    ) -> dict[str, Any]:
        if on_observation is not None and self.pipeline.lifecycle is None:
            raise ValueError("observation consumer requires explicit run context")
        if self.pipeline._finished:
            raise RuntimeError("pipeline is already finished")
        source = Path(video).expanduser().resolve()
        writer = None
        frames_processed = 0
        printed_final: set[int] = set()
        started = time.monotonic()
        events_stream = None
        end_cause = "end_of_input"
        end_trigger = "end_of_input"
        try:
            if max_frames is not None and (type(max_frames) is not int or max_frames < 0):
                raise ValueError("max_frames must be a nonnegative integer")
            if not source.is_file():
                raise FileNotFoundError(f"Input video not found: {source}")
            if (self.pipeline.lifecycle is not None and
                    self.pipeline.lifecycle.context.source_kind != "file"):
                raise ValueError("process_video requires an explicit file run context")
            if _capture_factory is None or output_path or _media_pts_reader is None:
                import cv2
            factory = _capture_factory if _capture_factory is not None else cv2.VideoCapture
            with ExitStack() as cleanup:
                capture = factory(str(source))
                cleanup.callback(capture.release)
                if not capture.isOpened():
                    raise RuntimeError(f"Could not open input video: {source}")

                def read_pts():
                    # Best-effort decoder position; never converted to absolute UTC.
                    if _media_pts_reader is not None:
                        return _media_pts_reader(capture)
                    value = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
                    return value if math.isfinite(value) and value >= 0 else None

                if events_output:
                    event_path = Path(events_output).expanduser().resolve()
                    event_path.parent.mkdir(parents=True, exist_ok=True)
                    events_stream = cleanup.enter_context(event_path.open("w", encoding="utf-8"))
                while True:
                    if self.pipeline._finished or (stop_requested is not None and stop_requested()):
                        end_cause = "service_stop"
                        end_trigger = "explicit_stop"
                        break
                    if max_frames is not None and frames_processed >= max_frames:
                        end_cause = "service_stop"
                        end_trigger = "frame_limit"
                        break
                    ok, frame = capture.read()
                    if not ok:
                        break
                    receipt = (self.capture_receipt(frames_processed, media_pts_reader=read_pts)
                               if self.pipeline.lifecycle is not None else None)
                    update = self.process_frame(frame, frames_processed, receipt=receipt)
                    if on_observation is not None:
                        on_observation(frame, update.observation)
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
                            cleanup.callback(writer.release)
                            if not writer.isOpened():
                                raise RuntimeError(f"Could not create output video: {destination}")
                        from .annotation import draw_overlay
                        annotated = draw_overlay(frame, self.pipeline.active_profiles.values(), self.pipeline.roi.config if self.pipeline.roi else None, self.pipeline.roi)
                        writer.write(annotated)
                    frames_processed += 1
                    if frames_processed % 100 == 0:
                        print(f"Processing frame {frames_processed} ({self.pipeline.perf.report_dict()['fps']:.1f} FPS)")
        except KeyboardInterrupt:
            self._finish_for("service_stop")
            raise
        except BaseException:
            self._finish_for("process_error")
            raise

        vehicles = self._finish_for(end_cause, trigger=end_trigger)
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
