"""Colour consumption of the classification owner; no capture or vehicle model."""
from dataclasses import dataclass, field

import numpy as np

from . import config
from .detector import ColourPipeline
from .schemas import VehicleProfile
from .temporal import decide_colour, SUPPORTED_COLOURS
from .evidence import (RunContext, VehicleFrame, VehicleIdentity, OwnedColourCrop,
                       ColourSampleEvidence, FinalColourEvidence)


@dataclass
class _Track:
    identity: VehicleIdentity
    profile: VehicleProfile
    samples: list[ColourSampleEvidence] = field(default_factory=list)
    last_attempt_receipt: object = None
    last_valid_receipt: object = None
    best_crop: OwnedColourCrop | None = None
    agent_crop: OwnedColourCrop | None = None
    local_errors: int = 0
    agent_attempts: int = 0
    agent_error: str | None = None


class SharedColourPipeline(ColourPipeline):
    """One explicit run. Reuses recognition/temporal rules, never calls track()."""

    def __init__(self, context: RunContext, *, capability='enabled', agent_enabled=None,
                 estimate_colour=None, crop_quality=None, agent_colour=None,
                 local_model=None, agent_model=None):
        # Deliberately do not invoke the standalone detector constructor.
        if not isinstance(context, RunContext):
            raise ValueError('shared colour requires explicit RunContext')
        if capability not in {'enabled', 'disabled', 'unavailable'}:
            raise ValueError('invalid colour capability')
        if agent_enabled is not None and type(agent_enabled) is not bool:
            raise ValueError("agent_enabled must be an explicit bool or None")
        try:
            from common.observation_evidence import RecognitionIdentity
        except ModuleNotFoundError:
            from vehicle.common.observation_evidence import RecognitionIdentity
        for model, task in ((local_model, 'classify'), (agent_model, 'vlm')):
            if model is not None and (not isinstance(model, RecognitionIdentity) or model.task != task):
                raise ValueError('colour model identity must match its execution route')
        self._local_model, self._agent_model = local_model, agent_model
        self.agent_enabled = agent_enabled
        self.context = context
        self.capability = capability
        self._tracks: dict[int, _Track] = {}
        self._pending: dict[int, FinalColourEvidence] = {}
        self._closed_through = 0
        self._closed_ranges = []
        self._result_owner = None
        self._last_frame = -1
        self._agent = None
        self._agent_operation = agent_colour
        self._agent_track = None
        if estimate_colour is not None:
            self._estimate_colour = estimate_colour
        if crop_quality is not None:
            self._crop_quality = crop_quality

    @property
    def local_model(self):
        return self._local_model

    @property
    def agent_model(self):
        return self._agent_model

    def process_frame(self, frame):
        raise RuntimeError('shared colour requires process_vehicle_frame(frame, VehicleFrame)')

    def _closed(self, service_id):
        return service_id <= self._closed_through or any(
            start <= service_id <= end for start, end in self._closed_ranges)

    def claim_result_delivery(self, owner):
        if owner is None or (self._result_owner is not None and self._result_owner is not owner):
            raise RuntimeError('colour results already belong to another delivery owner')
        self._result_owner = owner

    def _check_result_owner(self, owner):
        if self._result_owner is not None and self._result_owner is not owner:
            raise RuntimeError('only the coordinator may transfer colour results')

    def _remember_closed(self, service_id):
        # Compress acknowledged IDs into intervals, including out-of-order closure.
        # A long-lived low ID must not retain one tombstone per newer vehicle.
        merged = []
        for start, end in sorted(self._closed_ranges + [(service_id, service_id)]):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        if merged and merged[0][0] <= self._closed_through + 1:
            self._closed_through = merged.pop(0)[1]
        self._closed_ranges = merged

    def process_vehicle_frame(self, frame, observation: VehicleFrame):
        if not isinstance(observation, VehicleFrame) or observation.receipt.frame_index <= self._last_frame:
            raise ValueError('shared colour requires an increasing VehicleFrame')
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError('shared colour requires a BGR uint8 image')
        ids = set()
        for vehicle in observation.vehicles:
            lifetime = vehicle.lifetime
            if (lifetime.context != self.context or lifetime.last_receipt is not observation.receipt
                    or lifetime.closure is not None or lifetime.quarantined):
                raise ValueError('vehicle does not belong to this active run/frame')
            if vehicle.service_track_id in ids or self._closed(vehicle.service_track_id):
                raise ValueError('duplicate or already closed service identity')
            ids.add(vehicle.service_track_id)
        self._last_frame = observation.receipt.frame_index
        for vehicle in observation.vehicles:
            identity = VehicleIdentity.from_lifetime(vehicle.lifetime)
            track = self._tracks.get(identity.service_track_id)
            if track is None:
                track = _Track(identity, VehicleProfile(identity.service_track_id, list(vehicle.bbox)))
                self._tracks[identity.service_track_id] = track
            elif track.identity != identity:
                raise ValueError('service identity changed within a lifetime')
            profile = track.profile
            profile.type, profile.type_conf = vehicle.vehicle_type, vehicle.type_confidence
            profile.frames_seen += 1
            profile._last_seen_frame = observation.receipt.frame_index
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = vehicle.bbox
            x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
            y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))
            profile.bbox = [x1, y1, x2, y2]
            if self.capability != 'enabled' or x2 <= x1 or y2 <= y1:
                continue
            # Every downstream operation receives owned crop bytes, never an image view.
            crop = frame[y1:y2, x1:x2].copy()
            score = crop.shape[0] * crop.shape[1]
            if score > profile._best_score:
                profile._best_score = score
                track.best_crop = OwnedColourCrop.capture(crop, profile.bbox, observation.receipt)
                profile._best_crop = crop.copy()
            if not profile._colour_finalized:
                track.last_attempt_receipt = observation.receipt
                try:
                    self._observe(profile, crop, frame.shape)
                except Exception:
                    track.local_errors += 1
                    raise
                sample = profile._last_colour_attempt
                if sample.valid:
                    track.last_valid_receipt = observation.receipt
                    track.samples.append(ColourSampleEvidence(observation.receipt, tuple(profile.bbox),
                                        sample.label, sample.confidence, sample.quality, sample.weight))
                if profile._colour_finalized:
                    self._fallback_track(track)
        return tuple(self._tracks[vehicle.service_track_id].identity for vehicle in observation.vehicles)

    def _fallback_track(self, track):
        if self.agent_enabled is False:
            return
        self._agent_track = track
        try:
            self._fallback_if_needed(track.profile)
        finally:
            self._agent_track = None

    def _agent_colour(self, crop):
        if self.agent_enabled is False:
            return None
        track = self._agent_track
        if self._agent_operation is None and (not config.COLOR_AGENT_ENABLED or not config.COLOR_AGENT_API_KEY):
            return None
        # Capture the exact submitted supporting crop BEFORE agent invocation.
        owned = crop.copy()
        track.agent_crop = OwnedColourCrop.capture(owned, track.best_crop.bbox, track.best_crop.receipt)
        track.agent_attempts += 1
        try:
            answer = (self._agent_operation(owned) if self._agent_operation is not None
                      else super()._agent_colour(owned))
        except Exception:
            track.agent_error = 'agent_failure'
            return None
        try:
            if answer is not None:
                label, confidence = answer
                if not isinstance(label, str) or not isinstance(confidence, (int, float)):
                    raise ValueError('invalid agent answer')
        except (TypeError, ValueError):
            track.agent_error = 'agent_rejected_result'
            return None
        if answer is None:
            track.agent_error = 'agent_no_result'
        elif (answer[0] not in SUPPORTED_COLOURS or not 0 <= answer[1] <= 1):
            track.agent_error = 'agent_rejected_result'
        return answer

    def finalize_vehicle(self, vehicle, *, owner=None):
        self._check_result_owner(owner)
        lifetime = vehicle.lifetime
        identity = VehicleIdentity.from_lifetime(lifetime)
        if identity.context != self.context or lifetime.closure is None:
            raise ValueError('colour finalization requires a closed lifetime in this run')
        service_id = identity.service_track_id
        if service_id in self._pending:
            return self._pending[service_id]
        if self._closed(service_id):
            return None  # Evidence was already transferred, never recognize again.
        track = self._tracks.get(service_id) or _Track(identity, VehicleProfile(service_id, list(vehicle.bbox)))
        if track.identity != identity:
            raise ValueError('closure identity differs from colour lifetime')
        profile = track.profile
        if self.capability == 'enabled' and profile._colour_attempts and not profile._colour_finalized:
            self._apply(profile, decide_colour(profile._colour_samples, finalizing=True))
            self._fallback_track(track)
        attempts = profile._colour_attempts
        omission = None
        if self.capability != 'enabled':
            state, omission = 'omitted', ('disabled_capability' if self.capability == 'disabled' else 'unsupported_capability')
        elif not attempts:
            state, omission = 'omitted', 'rejected_crop'
        elif profile.colour != 'unknown':
            state = 'decided'
        else:
            state = 'error' if track.local_errors and not track.samples else 'undecided'
        source = None if state == 'omitted' else ('agent_override' if profile._colour_source == 'agent_fallback' else 'local_hsv')
        support = (None if state == 'omitted' else
                   track.agent_crop.receipt if source == 'agent_override' else
                   track.last_valid_receipt if state == 'decided' else track.last_attempt_receipt)
        evidence = FinalColourEvidence(identity, lifetime.first_receipt, lifetime.last_receipt,
                        lifetime.closure, lifetime.quarantined, state,
                        profile.colour if state == 'decided' else None,
                        profile.colour_conf if state == 'decided' else None, source, support,
                        attempts, tuple(track.samples), track.local_errors, track.agent_attempts,
                        track.agent_error, track.best_crop, track.agent_crop, omission,
                        profile._colour_candidate, profile._colour_winner_share,
                        None if state == 'omitted' else self.agent_model if source == 'agent_override' else self.local_model,
                        track.last_attempt_receipt)
        # Transfer before deleting working state. Pending evidence is never evicted.
        self._pending[service_id] = evidence
        self._tracks.pop(service_id, None)
        self._remember_closed(service_id)
        return evidence

    def release_finalized(self, service_id, *, owner=None):
        """Idempotent after finalization, including retries after a side effect."""
        self._check_result_owner(owner)
        if type(service_id) is not int or service_id < 1 or not self._closed(service_id):
            raise ValueError('release requires a known finalized service identity')
        self._pending.pop(service_id, None)

    def drain_finalized(self, *, owner=None):
        self._check_result_owner(owner)
        result = tuple(self._pending.values())
        self._pending.clear()
        return result

    def finalize_all(self):
        raise RuntimeError('shared colour needs owner closure causes; use finalize_vehicle')
