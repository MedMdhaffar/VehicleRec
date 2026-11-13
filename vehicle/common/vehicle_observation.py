"""Run-local vehicle lifetimes, independent of detectors and row construction.

Single-owner state machine. Call lose when a tracker declares a lifetime lost;
absence on one frame alone is not loss. Raw ID reuse without such a boundary
cannot be inferred. Closed snapshots remain immutable and in memory for P2a.
"""
from dataclasses import dataclass, replace
from enum import Enum

from .db1_context import BIGINT_MAX, RunContext, positive_bigint
from .frame_receipt import FrameReceipt, FrameReceiptClock


class VehicleClosure(str, Enum):
    TRACK_LOSS = "track_loss"
    TRACKER_RESET = "tracker_reset"
    CAMERA_OUTAGE = "camera_outage"
    END_OF_INPUT = "end_of_input"
    SERVICE_STOP = "service_stop"
    CLOCK_DISCONTINUITY = "clock_discontinuity"
    PROCESS_ERROR = "process_error"


class RunEnd(str, Enum):
    END_OF_INPUT = "end_of_input"
    SERVICE_STOP = "service_stop"
    CAMERA_OUTAGE = "camera_outage"
    CLOCK_DISCONTINUITY = "clock_discontinuity"
    PROCESS_ERROR = "process_error"


class TrackIdExhausted(OverflowError):
    pass


class TrackIdAllocator:
    def __init__(self, *, start: int = 1):
        positive_bigint(start, "allocator start")
        self._start = start
        self._next = start

    def was_allocated(self, service_id: int) -> bool:
        positive_bigint(service_id, 'service_track_id')
        return self._start <= service_id < self._next

    def allocate(self) -> int:
        if self._next > BIGINT_MAX:
            raise TrackIdExhausted("run track allocator exhausted positive BIGINT")
        result = self._next
        self._next += 1
        return result


@dataclass(frozen=True, slots=True)
class VehicleLifetime:
    context: RunContext
    service_track_id: int
    tracker_epoch: int
    raw_track_id: int
    lifetime_sequence: int
    first_receipt: FrameReceipt
    last_receipt: FrameReceipt
    frames_observed: int
    closure: VehicleClosure | None = None
    quarantined: bool = False

    @property
    def raw_identity(self) -> tuple[int, int, int]:
        return self.tracker_epoch, self.raw_track_id, self.lifetime_sequence


class VehicleLifecycle:
    def __init__(self, context: RunContext, clock: FrameReceiptClock, *, allocator_start: int = 1):
        if clock.context is not context:
            raise ValueError("lifecycle and clock must share the same run context")
        self._context = context
        self._clock = clock
        self._allocator = TrackIdAllocator(start=allocator_start)
        self._epoch = 0
        self._sequences: dict[tuple[int, int], int] = {}
        self._active: dict[int, int] = {}
        self._lifetimes: dict[int, VehicleLifetime] = {}
        self._end_reason: RunEnd | None = None

    @property
    def context(self):
        return self._context

    @property
    def tracker_epoch(self):
        return self._epoch

    @property
    def end_reason(self):
        return self._end_reason

    @property
    def lifetimes(self) -> tuple[VehicleLifetime, ...]:
        return tuple(self._lifetimes.values())

    def is_released(self, service_id: int) -> bool:
        """Spent contiguous allocator range avoids per-closure tombstones."""
        return self._allocator.was_allocated(service_id) and service_id not in self._lifetimes

    def release_closed(self, service_ids: tuple[int, ...]) -> None:
        """Release acknowledged snapshots atomically; allocated IDs remain spent.

        Re-closing a released ID raises KeyError rather than finalizing it again.
        Live IDs cannot be released. Raw lifetime sequence counters are retained
        for same-epoch reuse; they contain integers, never frame observations.
        """
        for service_id in service_ids:
            if self._lifetimes[service_id].closure is None:
                raise ValueError("cannot release an active lifetime")
        for service_id in set(service_ids):
            del self._lifetimes[service_id]

    def _require_open(self):
        if self.end_reason is not None:
            raise RuntimeError("run is ended; construct a new run explicitly")
        if self._clock.invalid_reason is not None:
            self.clock_fault()
        self._clock.require_valid()

    def observe(self, raw_track_id: int, receipt: FrameReceipt) -> VehicleLifetime:
        self._require_open()
        if type(raw_track_id) is not int or raw_track_id < 0:
            raise ValueError("raw tracker ID must be a nonnegative integer")
        if receipt is not self._clock.last_valid_receipt:
            raise ValueError("observations require the current shared receipt from this run")
        service_id = self._active.get(raw_track_id)
        if service_id is None:
            identity = self.tracker_epoch, raw_track_id
            sequence = self._sequences.get(identity, 0) + 1
            service_id = self._allocator.allocate()
            lifetime = VehicleLifetime(self.context, service_id, self.tracker_epoch,
                                       raw_track_id, sequence, receipt, receipt, 1)
            self._sequences[identity] = sequence
            self._active[raw_track_id] = service_id
        else:
            previous = self._lifetimes[service_id]
            # Multiple sightings in one frame count once and share the same record.
            lifetime = replace(previous, last_receipt=receipt,
                               frames_observed=previous.frames_observed +
                               (receipt.frame_index != previous.last_receipt.frame_index))
        self._lifetimes[service_id] = lifetime
        return lifetime

    def close(self, service_id: int, cause: VehicleClosure) -> VehicleLifetime:
        if not isinstance(cause, VehicleClosure):
            raise ValueError("closure requires a vehicle closure cause")
        previous = self._lifetimes[service_id]
        if previous.closure is not None:
            return previous
        lifetime = replace(previous, closure=cause,
                           quarantined=cause == VehicleClosure.CLOCK_DISCONTINUITY)
        self._lifetimes[service_id] = lifetime
        self._active.pop(previous.raw_track_id, None)
        return lifetime

    def lose(self, raw_track_id: int) -> VehicleLifetime | None:
        service_id = self._active.get(raw_track_id)
        return None if service_id is None else self.close(service_id, VehicleClosure.TRACK_LOSS)

    def _close_all(self, cause: VehicleClosure):
        return tuple(self.close(service_id, cause) for service_id in tuple(self._active.values()))

    def reset(self):
        self._require_open()
        closed = self._close_all(VehicleClosure.TRACKER_RESET)
        self._epoch += 1
        self._sequences.clear()  # Previous-epoch raw identities cannot be reused.
        return closed

    def reconnect(self):
        self._require_open()
        closed = self._close_all(VehicleClosure.CAMERA_OUTAGE)
        self._epoch += 1
        self._sequences.clear()  # Previous-epoch raw identities cannot be reused.
        return closed

    def end(self, cause: RunEnd):
        if not isinstance(cause, RunEnd):
            raise ValueError("run end requires a run-ending cause")
        if self.end_reason is not None:
            return ()
        if self._clock.invalid_reason is not None:
            cause = RunEnd.CLOCK_DISCONTINUITY
        closed = self._close_all(VehicleClosure(cause.value))
        self._end_reason = cause
        return closed

    def clock_fault(self):
        if self._clock.invalid_reason is None:
            raise ValueError("clock epoch must be invalid before clock-fault closure")
        return self.end(RunEnd.CLOCK_DISCONTINUITY)
