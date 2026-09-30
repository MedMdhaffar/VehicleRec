"""Per-frame profile lifecycle extracted from live_feed.pipeline."""
from __future__ import annotations

import time

from .classification import PUBLIC_VEHICLE_TYPES, normalize_streetdet_class_id
from .config import K_CLASSIFY, LOST_FRAMES
from .perf import PerfMeter
from .roi import RoiAnalytics, RoiConfig
from .schemas import FrameUpdate, VehicleProfile


def aggregate_vehicle_type(confidence_sums: dict[str, float], observation_counts: dict[str, int], total_observations: int) -> tuple[str, float]:
    """Production consensus-aware, deterministic vehicle-type aggregation."""
    if total_observations <= 0:
        return "unknown", 0.0
    known = [label for label in PUBLIC_VEHICLE_TYPES if label != "unknown" and observation_counts.get(label, 0) > 0]
    if not known:
        count = observation_counts.get("unknown", 0)
        return "unknown", round(confidence_sums.get("unknown", 0.0) / count if count else 0.0, 4)
    winner = min(known, key=lambda label: (-confidence_sums.get(label, 0.0), -observation_counts.get(label, 0), label))
    winner_sum, winner_count = confidence_sums[winner], observation_counts[winner]
    total_sum = sum(confidence_sums.get(label, 0.0) for label in PUBLIC_VEHICLE_TYPES)
    second_sum = max((confidence_sums.get(label, 0.0) for label in PUBLIC_VEHICLE_TYPES if label != winner), default=0.0)
    mean = winner_sum / winner_count
    confidence_share = winner_sum / total_sum if total_sum else 0.0
    observation_share = winner_count / total_observations
    margin = max(0.0, winner_sum - second_sum) / total_sum if total_sum else 0.0
    return winner, round(mean * (confidence_share + observation_share + margin) / 3.0, 4)


def update_track_vehicle_type(profile: VehicleProfile, class_id: int, confidence: float, min_observations: int = K_CLASSIFY) -> None:
    label = normalize_streetdet_class_id(class_id, confidence)
    if label is None:
        return
    profile._type_confidence_sums[label] = profile._type_confidence_sums.get(label, 0.0) + confidence
    profile._type_observation_counts[label] = profile._type_observation_counts.get(label, 0) + 1
    profile._type_total_observations += 1
    if profile._type_total_observations >= min_observations:
        profile.type, profile.type_conf = aggregate_vehicle_type(profile._type_confidence_sums, profile._type_observation_counts, profile._type_total_observations)


class VehiclePipeline:
    def __init__(self, detector_tracker, roi_config: RoiConfig | None = None) -> None:
        self.detector_tracker = detector_tracker
        self.active_profiles: dict[int, VehicleProfile] = {}
        self.finalized_profiles: list[VehicleProfile] = []
        self.events: list[dict] = []
        self.perf = PerfMeter()
        self.roi = RoiAnalytics(roi_config, time.monotonic()) if roi_config is not None else None
        self._finished = False

    def process_frame(self, frame, frame_index: int) -> FrameUpdate:
        if self._finished:
            raise RuntimeError("pipeline is already finished")
        with self.perf.stage("track"):
            rows = self.detector_tracker.process(frame)
        now = time.monotonic()
        seen: set[int] = set()
        for row in rows:
            x1, y1, x2, y2, raw_id, confidence, raw_class = row[:7]
            track_id, class_id = int(raw_id), int(raw_class)
            label = normalize_streetdet_class_id(class_id, float(confidence))
            if label is None:
                continue
            seen.add(track_id)
            bbox = [int(x1), int(y1), int(x2), int(y2)]
            profile = self.active_profiles.get(track_id)
            if profile is None:
                profile = VehicleProfile(track_id=track_id, bbox=bbox, first_seen=now, last_seen=now, first_frame=frame_index, last_frame=frame_index)
                self.active_profiles[track_id] = profile
            profile.bbox = bbox
            profile.last_seen = now
            profile.last_frame = frame_index
            profile.frames_seen += 1
            update_track_vehicle_type(profile, class_id, float(confidence))

        events = self.roi.process_frame(self.active_profiles, frame_index, now) if self.roi else []
        self.events.extend(events)
        lost = [track_id for track_id, profile in self.active_profiles.items() if track_id not in seen and frame_index - profile.last_frame > LOST_FRAMES]
        finalized = self._finalize(lost)
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
            profile.status = "final"
            self.finalized_profiles.append(profile)
            finalized.append(profile)
        return finalized
