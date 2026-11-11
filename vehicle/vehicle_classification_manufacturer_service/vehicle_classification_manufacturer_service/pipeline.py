"""Per-frame profile lifecycle extracted from live_feed.pipeline."""
from __future__ import annotations

import time
import logging

from .config import LOST_FRAMES, SCENE_CUT_THRESHOLD
from .scene import SceneCutDetector
from .perf import PerfMeter
from .roi import RoiAnalytics, RoiConfig
from .schemas import FrameUpdate, VehicleProfile


log = logging.getLogger(__name__)

class VehiclePipeline:
    def __init__(self, detector_tracker, roi_config: RoiConfig | None = None, recognizer=None) -> None:
        self.recognizer = recognizer
        self.detector_tracker = detector_tracker
        self.active_profiles: dict[int, VehicleProfile] = {}
        self.finalized_profiles: list[VehicleProfile] = []
        self.events: list[dict] = []
        self.perf = PerfMeter()
        self.roi = RoiAnalytics(roi_config, time.monotonic()) if roi_config is not None else None
        self.scene_cuts = []
        self.scene_detector = SceneCutDetector(SCENE_CUT_THRESHOLD)
        self._finished = False

    def process_frame(self, frame, frame_index: int) -> FrameUpdate:
        if self._finished:
            raise RuntimeError("pipeline is already finished")
        cut_finalized = []
        if self.scene_detector.update(frame):
            log.info("Scene cut at frame %d: finalizing %d tracks and resetting ByteTrack", frame_index, len(self.active_profiles))
            cut_finalized = self._finalize(list(self.active_profiles))
            self.detector_tracker.reset()
            self.scene_cuts.append(frame_index)
            if self.roi is not None:
                self.roi = RoiAnalytics(self.roi.config, time.monotonic())
        with self.perf.stage("track"):
            rows = self.detector_tracker.process(frame)
        now = time.monotonic()
        seen: set[int] = set()
        for row in rows:
            x1, y1, x2, y2, raw_id = row[:5]
            track_id = int(raw_id)
            seen.add(track_id)
            bbox = [int(x1), int(y1), int(x2), int(y2)]
            profile = self.active_profiles.get(track_id)
            if profile is None:
                profile = VehicleProfile(track_id=track_id, bbox=bbox, first_seen=now, last_seen=now, first_frame=frame_index, last_frame=frame_index)
                if self.recognizer:
                    profile.recognition.status = "pending"
                    profile.recognition.key = f"track_{track_id}_{frame_index}"
                self.active_profiles[track_id] = profile
            profile.bbox = bbox
            profile.last_seen = now
            profile.last_frame = frame_index
            profile.frames_seen += 1
            if self.recognizer:
                pending = [p.recognition.candidate for p in self.active_profiles.values() if p.recognition.candidate is not None]
                used_bytes = sum(candidate.image.nbytes for candidate in pending)
                x1, y1, x2, y2 = bbox
                height, width = frame.shape[:2]
                crop_bytes = max(0, min(width, x2) - max(0, x1)) * max(0, min(height, y2) - max(0, y1)) * 3
                previous_bytes = profile.recognition.candidate.image.nbytes if profile.recognition.candidate is not None else 0
                fits_count = profile.recognition.candidate is not None or len(pending) < 64
                fits_memory = used_bytes - previous_bytes + crop_bytes <= self.recognizer.config.max_pending_crop_mb * 1024 * 1024
                if fits_count and fits_memory:
                    self.recognizer.observe(profile.recognition, frame, bbox, frame_index, profile.frames_seen)
                else:
                    profile.recognition.failure_reason = "crop_memory_budget_exceeded"

        events = self.roi.process_frame(self.active_profiles, frame_index, now) if self.roi else []
        self.events.extend(events)
        lost = [track_id for track_id, profile in self.active_profiles.items() if track_id not in seen and frame_index - profile.last_frame > LOST_FRAMES]
        finalized = cut_finalized + self._finalize(lost)
        self.perf.frame_done(len(self.active_profiles))
        return FrameUpdate(frame_index, [p.to_dict() for p in self.active_profiles.values()], [p.to_dict() for p in finalized], events)

    def finish(self) -> list[VehicleProfile]:
        if not self._finished:
            self._finalize(list(self.active_profiles))
            self.perf.frame_done(0)
            self._finished = True
        return list(self.finalized_profiles)

    def _finalize(self, track_ids: list[int]) -> list[VehicleProfile]:
        finalized: list[VehicleProfile] = []
        for track_id in track_ids:
            profile = self.active_profiles.pop(track_id, None)
            if profile is None:
                continue
            if self.recognizer:
                self.recognizer.finish(profile.recognition)
            log.info("Track %s finalized: brand=%s body=%s color=%s status=%s attempts=%d reason=%s", profile.track_id, profile.recognition.manufacturer, profile.recognition.body_type, profile.recognition.color, profile.recognition.status, len(profile.recognition.attempts), profile.recognition_reason() or profile.recognition.failure_reason or "none")
            profile.status = "final"
            self.finalized_profiles.append(profile)
            finalized.append(profile)
        return finalized
