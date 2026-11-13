"""Frozen analytical plate evidence; separate from standalone review results."""
from dataclasses import dataclass

try:
    from common.db1_context import RunContext
    from common.vehicle_frame import VehicleFrame
    from common.frame_receipt import FrameReceipt
    from common.vehicle_observation import VehicleClosure
    from common.observation_evidence import VehicleIdentity, OwnedCrop
except ModuleNotFoundError as exc:
    if exc.name != 'common':
        raise
    from vehicle.common.db1_context import RunContext
    from vehicle.common.vehicle_frame import VehicleFrame
    from vehicle.common.frame_receipt import FrameReceipt
    from vehicle.common.vehicle_observation import VehicleClosure
    from vehicle.common.observation_evidence import VehicleIdentity, OwnedCrop


@dataclass(frozen=True, slots=True)
class PlateAssociationEvidence:
    receipt: FrameReceipt
    raw_plate_id: int
    status: str
    selected_service_track_id: int | None
    candidate_scores: tuple[tuple[int, float], ...]


@dataclass(frozen=True, slots=True)
class RecognizerModel:
    """Explicit caller-supplied artifact/prompt registration, not catalogue defaults."""
    model_name: str
    version: int
    task: str = 'ocr'

    def __post_init__(self):
        if not isinstance(self.model_name, str) or not self.model_name or type(self.version) is not int or self.version < 1 or self.task not in {'ocr', 'vlm'}:
            raise ValueError('explicit OCR/VLM model name and positive version required')

    def policy(self):
        return dict(model_name=self.model_name, version=self.version, task=self.task)


@dataclass(frozen=True, slots=True)
class Execution:
    route: str
    model: RecognizerModel

    def __post_init__(self):
        if self.route not in {'private_local', 'agent'} or not isinstance(self.model, RecognizerModel):
            raise ValueError('registered execution route/model required')


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    result_json: bytes | None
    executions: tuple[Execution, ...]
    error: str | None
    completion_monotonic: float


@dataclass(frozen=True, slots=True)
class PlateAttempt:
    attempt_id: str
    submission_ordinal: int
    parent: VehicleIdentity
    segment_id: int
    raw_plate_id: int
    crop: OwnedCrop
    crop_score: float | None
    trigger: str
    status: str
    executions: tuple[Execution, ...] = ()
    result_json: bytes | None = None
    error: str | None = None
    completion_monotonic: float | None = None
    execution_unknown: bool = False

    @property
    def recognizer_executed(self):
        return True if self.executions else None if self.execution_unknown else False


@dataclass(frozen=True, slots=True)
class SelectedPlate:
    attempt_id: str
    segment_id: int
    crop: OwnedCrop
    crop_score: float | None
    text: str | None
    route: str
    model: RecognizerModel

    @property
    def receipt(self):
        return self.crop.receipt


@dataclass(frozen=True, slots=True)
class FinalPlateEvidence:
    identity: VehicleIdentity
    first_vehicle_receipt: FrameReceipt
    last_vehicle_receipt: FrameReceipt
    closure: VehicleClosure
    quarantined: bool
    outcome: str
    omission_reason: str | None
    ledger: tuple[PlateAttempt, ...]
    selected: SelectedPlate | None
    detections: int
    admitted_crops: int
    ambiguous_crops: int
    unassociated_crops: int
    settlement_incomplete: bool

    @property
    def attempts(self):
        return len(self.ledger)
