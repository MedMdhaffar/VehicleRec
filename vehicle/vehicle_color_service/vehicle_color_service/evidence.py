"""Immutable colour evidence, separate from legacy public profiles and SQL."""
from dataclasses import dataclass

try:
    from common.db1_context import RunContext
    from common.frame_receipt import FrameReceipt
    from common.vehicle_frame import VehicleFrame
    from common.vehicle_observation import VehicleClosure
except ModuleNotFoundError as exc:
    if exc.name != 'common':
        raise
    from vehicle.common.db1_context import RunContext
    from vehicle.common.frame_receipt import FrameReceipt
    from vehicle.common.vehicle_frame import VehicleFrame
    from vehicle.common.vehicle_observation import VehicleClosure


try:
    from common.observation_evidence import VehicleIdentity, RecognitionIdentity, OwnedCrop as OwnedColourCrop
except ModuleNotFoundError as exc:
    if exc.name != 'common':
        raise
    from vehicle.common.observation_evidence import VehicleIdentity, RecognitionIdentity, OwnedCrop as OwnedColourCrop


@dataclass(frozen=True, slots=True)
class ColourSampleEvidence:
    receipt: FrameReceipt
    bbox: tuple[int, int, int, int]
    label: str
    confidence: float
    quality: float
    weight: float


@dataclass(frozen=True, slots=True)
class FinalColourEvidence:
    identity: VehicleIdentity
    first_vehicle_receipt: FrameReceipt
    last_vehicle_receipt: FrameReceipt
    closure: VehicleClosure
    quarantined: bool
    state: str
    label: str | None
    confidence: float | None
    source: str | None
    support_receipt: FrameReceipt | None
    attempts: int
    samples: tuple[ColourSampleEvidence, ...]
    local_errors: int
    agent_attempts: int
    agent_error: str | None
    best_crop: OwnedColourCrop | None
    agent_crop: OwnedColourCrop | None
    omission_reason: str | None
    local_candidate: str
    local_winner_share: float
    executed_model: RecognitionIdentity | None = None
    last_attempt_receipt: FrameReceipt | None = None

    @property
    def valid_sample_count(self):
        return len(self.samples)
