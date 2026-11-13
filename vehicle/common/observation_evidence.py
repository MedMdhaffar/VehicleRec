"""Immutable scalar parent identity and owned image evidence for shared consumers."""
from dataclasses import dataclass

from .db1_context import RunContext
from .frame_receipt import FrameReceipt


@dataclass(frozen=True, slots=True)
class VehicleIdentity:
    context: RunContext
    service_track_id: int
    raw_track_id: int
    tracker_epoch: int
    lifetime_sequence: int

    @classmethod
    def from_lifetime(cls, lifetime):
        return cls(lifetime.context, lifetime.service_track_id, lifetime.raw_track_id,
                   lifetime.tracker_epoch, lifetime.lifetime_sequence)


@dataclass(frozen=True, slots=True)
class OwnedCrop:
    pixels: bytes
    shape: tuple[int, ...]
    dtype: str
    bbox: tuple[int, int, int, int]
    receipt: FrameReceipt

    @classmethod
    def capture(cls, crop, bbox, receipt):
        return cls(crop.tobytes(), tuple(crop.shape), crop.dtype.str, tuple(bbox), receipt)

    def array(self):
        import numpy as np
        return np.frombuffer(self.pixels, dtype=self.dtype).reshape(self.shape).copy()


@dataclass(frozen=True, slots=True)
class RecognitionIdentity:
    """Caller-supplied identity of the executed versioned algorithm/model, no key."""
    model_name: str
    version: int
    task: str

    def __post_init__(self):
        if (not isinstance(self.model_name, str) or not self.model_name.strip()
                or type(self.version) is not int or not 1 <= self.version <= 2147483647
                or self.task not in {'classify', 'vlm', 'ocr'}):
            raise ValueError('explicit recognition name, INT version and task required')

    def policy(self):
        return dict(model_name=self.model_name, version=self.version, task=self.task)
