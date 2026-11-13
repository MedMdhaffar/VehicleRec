"""One paired receipt per successful decode, captured before inference.

The provisional divergence tolerance is fixed at construction. Faults latch;
recovery requires an explicitly constructed new run/context and clock epoch.
No timestamp clamping, source PTS conversion, or stability inference occurs.
"""
import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from .db1_context import RunContext, aware_utc


class ClockEpochInvalid(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrameReceipt:
    frame_index: int
    utc: datetime
    monotonic: float
    media_pts: float | None = None

    def __post_init__(self):
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame index must be a nonnegative integer")
        object.__setattr__(self, "utc", aware_utc(self.utc))
        if not math.isfinite(self.monotonic) or self.monotonic < 0:
            raise ValueError("monotonic reading must be finite and nonnegative")
        if self.media_pts is not None and not math.isfinite(self.media_pts):
            raise ValueError("media PTS must be finite when supplied")


@dataclass(frozen=True, slots=True)
class ClockPolicy:
    tolerance_seconds: float = 5.0

    def __post_init__(self):
        if not math.isfinite(self.tolerance_seconds) or self.tolerance_seconds < 0:
            raise ValueError("clock tolerance must be finite and nonnegative")


class FrameReceiptClock:
    def __init__(self, context: RunContext, *, utc_clock: Callable[[], datetime],
                 monotonic_clock: Callable[[], float], policy: ClockPolicy):
        if not isinstance(context, RunContext) or not isinstance(policy, ClockPolicy):
            raise ValueError("explicit immutable run context and clock policy are required")
        self._context = context
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._policy = policy
        self._last: FrameReceipt | None = None
        self._invalid_reason: str | None = None

    @property
    def context(self):
        return self._context

    @property
    def policy(self):
        return self._policy

    @property
    def last_valid_receipt(self):
        return self._last

    @property
    def invalid_reason(self):
        return self._invalid_reason

    def require_valid(self):
        if self._invalid_reason is not None:
            raise ClockEpochInvalid(self._invalid_reason)

    def invalidate(self, reason: str = "explicit clock step"):
        if not reason:
            raise ValueError("clock fault requires a reason")
        if self._invalid_reason is None:
            self._invalid_reason = reason

    def capture(self, frame_index: int, media_pts: float | None = None, *,
                media_pts_reader: Callable[[], float | None] | None = None, sampled_at=None) -> FrameReceipt:
        self.require_valid()
        if type(frame_index) is not int or frame_index < 0:
            raise ValueError("frame index must be a nonnegative integer")
        if self._last is not None and frame_index <= self._last.frame_index:
            raise ValueError("capture once per frame with increasing indices; share the returned record")
        if media_pts is not None and not math.isfinite(media_pts):
            raise ValueError("media PTS must be finite")
        try:
            utc, mono = sampled_at if sampled_at is not None else (self._utc_clock(), self._monotonic_clock())
            receipt = FrameReceipt(frame_index, utc, mono, media_pts)
            if self._last is not None:
                wall_delta = (receipt.utc - self._last.utc).total_seconds()
                mono_delta = receipt.monotonic - self._last.monotonic
                if wall_delta < 0 or mono_delta < 0 or abs(wall_delta - mono_delta) > self.policy.tolerance_seconds:
                    raise ValueError("paired clock discontinuity")
        except Exception as exc:
            self.invalidate(f"clock sample failed: {exc}")
            raise ClockEpochInvalid(self._invalid_reason) from exc
        # Read optional decoder PTS after sampling clocks, before publishing.
        # A PTS error is an input error; it does not invalidate the clock epoch.
        if media_pts_reader is not None:
            receipt = replace(receipt, media_pts=media_pts_reader())
        self._last = receipt
        return receipt
