"""Opt-in v3 Engine adapter over T1-owned crops. No capture or delivery API."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Event
import time

from .engine_plate import native_access
from .mohamed_lpr_contract import Crop, Result, canonical, digest, require
from .observation_evidence import VehicleIdentity
from .redis_scalar.codec import pack_receipt


class BindingError(RuntimeError):
    """An artifact, placement or identity mismatch must not become OCR failure."""


class RetirementPending(RuntimeError):
    """No scalar result or cleanup is allowed without actual retirement."""


def identity_key(identity):
    return sha256(canonical([str(identity.context.org_id), identity.context.run_id,
        identity.context.camera.camera_id, identity.service_track_id, identity.raw_track_id,
        identity.tracker_epoch, identity.lifetime_sequence])).hexdigest()


@dataclass(frozen=True)
class BoundCrop:
    scalar: Crop
    resource: object
    receipt: object
    original_bbox: tuple


def bind_crop(resource, life, *, plate_epoch, frame_receipt, frame_sha256):
    """Only call for best associated crops retained by T1 PlateLedger.

    The paired receipt is resolved from the sole Input, not recaptured at model
    completion. Plate tracker IDs/epochs and vehicle lifetime IDs remain distinct.
    """
    c = resource.binding.candidate
    owner = VehicleIdentity.from_lifetime(life)
    require(c.identity == owner and c.association == "associated", "unowned_crop")
    require(c.receipt is frame_receipt, "wrong_frame_receipt")
    require(not resource.released, "crop_released")
    w, h = c.crop_size
    require(w >= 4 and h >= 4, "ineligible_crop_size")
    pixels = resource.read()
    require(len(pixels) == w * h * 3 == resource.binding.size_bytes
            and sha256(pixels).hexdigest() == c.crop_sha256, "crop_bytes_mismatch")
    frame_id = sha256(canonical(pack_receipt(frame_receipt))).hexdigest()
    vehicle = identity_key(owner)
    association = sha256(canonical(["t1-same-frame-association-v0", frame_id, vehicle,
                                    plate_epoch, c.raw_plate_id, c.segment_id])).hexdigest()
    scalar = Crop(str(owner.context.org_id), owner.context.run_id, owner.context.camera.camera_id,
                  "mohamed-v3-" + vehicle[:32] + "-segment-" + str(c.segment_id), vehicle,
                  str(c.raw_plate_id), str(plate_epoch), association, c.candidate_id,
                  frame_id, frame_sha256, resource.binding.handle_id, c.crop_sha256, w, h, 1)
    return BoundCrop(scalar, resource, frame_receipt, c.bbox)


class Execution:
    def __init__(self, expected):
        self.expected = expected
        self.actual = None
        self.retirement = Event()
        self.starts = 0

    def started(self, actual):
        self.starts += 1
        if self.starts != 1 or actual != self.expected():
            raise BindingError("actual_execution_identity_mismatch")
        self.actual = actual

    def retired(self):
        self.retirement.set()


class V3EngineAdapter:
    """One owner thread; synchronous invoke with independent retirement ACK.

    Runtime owns preprocessing and models. Only Plate's retained/admitted
    requests execute. An unretired return keeps the T1 global native lane and
    crop lease charged, even on failure. Deadlines are never cancellation proof.
    """
    def __init__(self, runtime, bound, harness):
        require(bound.scalar == harness.engine.crop == harness.plate.crop, "adapter_crop_mismatch")
        self.runtime, self.bound, self.harness = runtime, bound, harness
        self.results = {}
        self.events = []
        self.active = None
        self.blocked = False

    def execute(self, request):
        require(request in self.harness.engine.current_requests, "operation_not_admitted")
        require(request.crop == self.bound.scalar, "adapter_request_binding_mismatch")
        if request.operation_id in self.results:
            return self.results[request.operation_id]  # No model call on retry.
        require(not self.blocked and self.active is None, "adapter_not_available")
        require(not self.bound.resource.released, "crop_released")
        try:
            self.runtime.verify(request.role)  # No call after artifact/index drift.
        except BindingError:
            self.blocked = True
            raise
        data = self.bound.resource.read()
        require(sha256(data).hexdigest() == request.crop.crop_sha256, "crop_bytes_mismatch")
        import numpy as np
        original = np.frombuffer(data, dtype=np.uint8).reshape(request.crop.height, request.crop.width, 3)
        x1, y1, x2, y2 = request.region_xyxy
        submitted = original[y1:y2, x1:x2].copy(order="C")
        lane = native_access()
        lane.__enter__()
        context = Execution(lambda: self.runtime.expected(request.role))
        self.active = dict(request=request, context=context, lane=lane, image=submitted,
                           payload=None, error=None, started=time.perf_counter())
        try:
            self.active["payload"] = self.runtime.invoke(request.role, submitted, context)
        except Exception as error:
            self.active["error"] = error
        if not context.retirement.is_set():
            raise RetirementPending("native_retirement_unconfirmed")
        return self.poll_retirement()

    def poll_retirement(self):
        state = self.active
        if state is None or not state["context"].retirement.is_set():
            return None
        self.active = None
        state["lane"].__exit__(None, None, None)
        r, context, error = state["request"], state["context"], state["error"]
        elapsed = time.perf_counter() - state["started"]
        if isinstance(error, BindingError):
            self.blocked = True
            raise error
        common = dict(request=r, execution_id="native-" + r.operation_id,
                      executor_ref="mohamed-cuda-engine-v3", retired=True, provenance=context.actual)
        if error is None:
            try:
                require(context.starts == 1, "successful_execution_not_started")
                result = Result(**common, outcome="succeeded", **state["payload"])
            except (ValueError, TypeError):
                result = Result(**common, outcome="failed", error_code="output_decode_failed")
        else:
            result = Result(**common, outcome="failed", error_code="execution_failed" if context.actual else "initialization_failed")
        self.events.append(dict(role=r.role, outcome=result.outcome, seconds=elapsed,
                                provenance=digest(context.actual) if context.actual else None))
        self.results[r.operation_id] = result
        return result

    def drive(self, *, deadline, monotonic=time.monotonic):
        while True:
            require(monotonic() < deadline, "recognition_admission_deadline")
            work = self.harness.advance()
            if not work:
                return self.harness.seal()
            for request in work:
                # Work is already admitted. A budget stop retains these crops;
                # it does not fabricate a failed result or a seal.
                require(monotonic() < deadline, "recognition_admission_deadline")
                self.harness.inject(self.execute(request))

    def release(self, *, discard=False):
        require(self.active is None, "crop_execution_not_retired")
        require(discard or self.harness.plate._sealed is not None, "crop_not_sealed")
        self.bound.resource.release()
