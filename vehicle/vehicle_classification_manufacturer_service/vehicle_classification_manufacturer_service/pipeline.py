"""Per-frame profile lifecycle extracted from live_feed.pipeline."""
from __future__ import annotations

import time

from .classification import PUBLIC_VEHICLE_TYPES, normalize_streetdet_class_id
from .config import K_CLASSIFY, LOST_FRAMES
from .perf import PerfMeter
from .roi import RoiAnalytics, RoiConfig
from .schemas import FrameUpdate, VehicleProfile


from .consensus import aggregate_vehicle_type


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
    def __init__(self, detector_tracker, roi_config: RoiConfig | None = None, *, observation_clock=None, class_consensus_enabled: bool = True) -> None:
        if type(class_consensus_enabled) is not bool:
            raise ValueError('class consensus capability must be an explicit bool')
        self.class_consensus_enabled = class_consensus_enabled
        self.detector_tracker = detector_tracker
        self.active_profiles: dict[int, VehicleProfile] = {}
        self.finalized_profiles: list[VehicleProfile] = []
        self.events: list[dict] = []
        self.perf = PerfMeter()
        self.roi = RoiAnalytics(roi_config, time.monotonic()) if roi_config is not None else None
        self._finished = False
        self.end_trigger = None
        self.observation_clock = observation_clock
        self.lifecycle = None
        self.observation_frame = None
        self._finalized_observations = []
        self._closure_drain_owner = None
        self._last_processed_frame = -1
        if observation_clock is not None:
            from .observations import FrameReceiptClock, VehicleLifecycle
            if not isinstance(observation_clock, FrameReceiptClock):
                raise ValueError("observation_clock must carry an explicit supplied RunContext")
            self.lifecycle = VehicleLifecycle(observation_clock.context, observation_clock)

    def capture_receipt(self, frame_index: int, *, media_pts_reader=None):
        if self.observation_clock is None:
            raise ValueError("receipt capture requires explicit observation context")
        if self._finished:
            raise RuntimeError("pipeline is already finished")
        from .observations import ClockEpochInvalid, RunEnd
        try:
            return self.observation_clock.capture(frame_index, media_pts_reader=media_pts_reader)
        except ClockEpochInvalid:
            self.finish(RunEnd.CLOCK_DISCONTINUITY)
            raise
        except KeyboardInterrupt:
            self.finish(RunEnd.SERVICE_STOP)
            raise
        except BaseException:
            self.finish(RunEnd.PROCESS_ERROR)
            raise

    def clock_fault(self, reason: str):
        if self.observation_clock is None:
            raise ValueError("clock fault requires observation context")
        from .observations import RunEnd
        self.observation_clock.invalidate(reason)
        self.finish(RunEnd.CLOCK_DISCONTINUITY)

    def process_frame(self, frame, frame_index: int, *, receipt=None) -> FrameUpdate:
        try:
            return self._process_frame(frame, frame_index, receipt=receipt)
        except KeyboardInterrupt:
            if self.lifecycle is not None:
                from .observations import RunEnd
                self.finish(RunEnd.SERVICE_STOP)
            raise
        except BaseException:
            if self.lifecycle is not None:
                from .observations import RunEnd
                self.finish(RunEnd.PROCESS_ERROR)
            raise

    def _process_frame(self, frame, frame_index: int, *, receipt=None) -> FrameUpdate:
        if self._finished:
            raise RuntimeError("pipeline is already finished")
        if self.lifecycle is not None:
            self.observation_clock.require_valid()
            if (receipt is None or receipt is not self.observation_clock.last_valid_receipt
                    or receipt.frame_index != frame_index or frame_index <= self._last_processed_frame):
                raise ValueError("integrated processing requires the current decoded-frame receipt")
        elif receipt is not None:
            raise ValueError("receipt supplied without explicit observation context")
        self.observation_frame = None
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
            if self.class_consensus_enabled:
                update_track_vehicle_type(profile, class_id, float(confidence))
            if self.lifecycle is not None:
                profile._lifetime = self.lifecycle.observe(track_id, receipt)

        events = self.roi.process_frame(self.active_profiles, frame_index, now) if self.roi else []
        self.events.extend(events)
        lost = [track_id for track_id, profile in self.active_profiles.items() if track_id not in seen and frame_index - profile.last_frame > LOST_FRAMES]
        finalized = self._finalize(lost)
        self.perf.frame_done(len(self.active_profiles))
        if self.lifecycle is not None:
            from .observations import VehicleBoxObservation, VehicleFrame
            self.observation_frame = VehicleFrame(receipt, tuple(
                VehicleBoxObservation(p._lifetime, tuple(p.bbox), p.type, p.type_conf)
                for p in self.active_profiles.values() if p.last_frame == frame_index))
        self._last_processed_frame = frame_index
        return FrameUpdate(frame_index, [p.to_dict() for p in self.active_profiles.values()],
                           [p.to_dict() for p in finalized], events, self.observation_frame)

    def finish(self, cause=None, *, trigger=None) -> list[VehicleProfile]:
        if not self._finished:
            if self.lifecycle is not None:
                from .observations import RunEnd
                self.lifecycle.end(RunEnd.SERVICE_STOP if cause is None else cause)
                effective_cause = self.lifecycle.end_reason.value
            else:
                effective_cause = "service_stop" if cause is None else cause.value
            self.end_trigger = ("clock_discontinuity" if effective_cause == "clock_discontinuity"
                                else trigger or ("explicit_stop" if effective_cause == "service_stop" else effective_cause))
            self.observation_frame = None
            self._finalize(list(self.active_profiles))
            self.perf.frame_done(0)
            self._finished = True
        return list(self.finalized_profiles)

    def reset_tracking(self) -> list[VehicleProfile]:
        """Notify AFTER the caller explicitly resets/replaces its tracker.

        Does not alter the detector or invoke an undocumented tracker reset API.
        """
        if self._finished:
            raise RuntimeError("pipeline is already finished")
        if self.lifecycle is not None:
            from .observations import RunEnd
            try:
                self.lifecycle.reset()
            except BaseException:
                self.finish(RunEnd.PROCESS_ERROR)
                raise
        self.observation_frame = None
        return self._finalize(list(self.active_profiles))

    def drain_finalized_profiles(self) -> list[VehicleProfile]:
        result, self.finalized_profiles = self.finalized_profiles, []
        return result

    def claim_closure_delivery(self, owner):
        if owner is None or self.lifecycle is None:
            raise ValueError("closure delivery requires an integrated owner")
        if self._closure_drain_owner is not None and self._closure_drain_owner is not owner:
            raise RuntimeError("closure delivery already belongs to another coordinator")
        self._closure_drain_owner = owner

    def _check_closure_owner(self, owner):
        if self._closure_drain_owner is not None and self._closure_drain_owner is not owner:
            raise RuntimeError("only the registered coordinator may deliver closures")

    def pending_finalized_observations(self, *, owner=None):
        self._check_closure_owner(owner)
        return tuple(self._finalized_observations)

    def acknowledge_finalized_observations(self, service_ids, *, owner=None):
        """Idempotent acknowledgment; resume a partially released owner closure."""
        self._check_closure_owner(owner)
        ids = set(service_ids)
        pending_ids = {item.service_track_id for item in self._finalized_observations}
        already_released = {sid for sid in ids if self.lifecycle is not None and self.lifecycle.is_released(sid)}
        if not ids <= pending_ids | already_released:
            raise ValueError("acknowledgement includes an unknown closure")
        if self.lifecycle is not None:
            self.lifecycle.release_closed(tuple(ids - already_released))
        for profile in self.finalized_profiles:
            if profile._lifetime is not None and profile._lifetime.service_track_id in ids:
                profile._lifetime = None
        self._finalized_observations[:] = [item for item in self._finalized_observations
                                          if item.service_track_id not in ids]

    def drain_finalized_observations(self, *, owner=None):
        result = self.pending_finalized_observations(owner=owner)
        self.acknowledge_finalized_observations(
            tuple(item.service_track_id for item in result), owner=owner)
        return result

    def _finalize(self, track_ids: list[int]) -> list[VehicleProfile]:
        finalized: list[VehicleProfile] = []
        for track_id in track_ids:
            profile = self.active_profiles.pop(track_id, None)
            if profile is None:
                continue
            if self.lifecycle is not None and profile._lifetime is not None:
                from .observations import VehicleBoxObservation, VehicleClosure
                # End/reset may already have closed it; close is idempotent.
                profile._lifetime = self.lifecycle.close(
                    profile._lifetime.service_track_id, VehicleClosure.TRACK_LOSS)
                self._finalized_observations.append(VehicleBoxObservation(
                    profile._lifetime, tuple(profile.bbox), profile.type, profile.type_conf))
            profile.status = "final"
            self.finalized_profiles.append(profile)
            finalized.append(profile)
        return finalized
