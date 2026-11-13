"""Shared natural identity, canonical serialization and evidence validation."""
from dataclasses import dataclass, is_dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math
from uuid import UUID, uuid5
from contracts.db1.reference import canonical_json_bytes
from ..frame_receipt import FrameReceipt

def integer(value, maximum, name, minimum=1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} outside SQL integer range')


@dataclass(frozen=True, slots=True)
class FrozenJSON:
    """Retain canonical bytes; value() returns a disposable independent copy."""
    canonical: bytes

    @classmethod
    def of(cls, value):
        return cls(canonical_json_bytes(value))

    def value(self):
        def exact_number(text):
            value = Decimal(text)
            # Canonical P1 emits scientific notation even for integer keys.
            # Restore mathematical integers for jsonschema's Python type checker.
            return int(value) if value == int(value) else value
        return json.loads(self.canonical, parse_float=exact_number)

    @property
    def digest(self):
        return sha256(self.canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class UUID5Policy:
    namespace: UUID

    def __post_init__(self):
        if not isinstance(self.namespace, UUID):
            raise ValueError('explicit stable UUID namespace required')

    def row_uid(self, identity, kind):
        return self._id(identity, 'row', kind)

    def attempt_uid(self, identity, segment_id, attempt_id):
        return self._id(identity, 'attempt', segment_id, attempt_id)

    def _id(self, identity, *parts):
        # Canonical array encoding avoids separator/collision ambiguity.
        return str(uuid5(self.namespace, canonical_json_bytes([
            str(identity.context.org_id), identity.context.camera.camera_id,
            identity.context.run_id, identity.service_track_id, *parts]).decode()))


class Blocked(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise Blocked(code, message)


def score(value, *, quality=False, nullable=True):
    if value is None and nullable:
        return value
    require(not isinstance(value, bool) and isinstance(value, (int, float, Decimal)),
            'INVALID_SCORE', 'score must be an int, float or Decimal')
    # Never send arbitrary integers or Decimals through a lossy float conversion.
    finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value) if isinstance(value, float) else True
    require(finite, 'INVALID_SCORE', 'score must be finite')
    maximum = 2**128 - 2**104 if quality else 1  # exact largest finite binary32
    require(0 <= value <= maximum, 'INVALID_SCORE', 'score outside nonnegative REAL service range')
    number = Decimal.from_float(value) if isinstance(value, float) else Decimal(value)
    require(number == 0 or number >= Decimal('1.401298464324817e-45'),
            'INVALID_SCORE', 'nonzero score is below supported REAL subnormal range')
    return value


def frozen_tree(value):
    if is_dataclass(value):
        require(value.__dataclass_params__.frozen, 'MUTABLE_EVIDENCE', 'completed evidence must be frozen')
        for field in value.__dataclass_fields__:
            frozen_tree(getattr(value, field))
    elif isinstance(value, tuple):
        for child in value:
            frozen_tree(child)
    else:
        require(value is None or isinstance(value, (str, bytes, int, float, Decimal, UUID))
                or type(value).__module__ in {'datetime', 'enum'},
                'MUTABLE_EVIDENCE', 'unsupported mutable completed evidence member')


def verify_shared_receipts(value, known):
    if isinstance(value, FrameReceipt):
        previous = known.setdefault(value.frame_index, value)
        require(previous == value, 'FRAME_RECEIPT_MISMATCH', 'same frame has inconsistent immutable receipts')
    elif is_dataclass(value):
        for field in value.__dataclass_fields__:
            verify_shared_receipts(getattr(value, field), known)
    elif isinstance(value, tuple):
        for child in value:
            verify_shared_receipts(child, known)


def receipt_in_lifetime(receipt, life):
    require(isinstance(receipt, FrameReceipt), 'MISSING_RECEIPT', 'supporting frame receipt required')
    first, last = life.first_receipt, life.last_receipt
    require((receipt.frame_index != first.frame_index or receipt == first)
            and (receipt.frame_index != last.frame_index or receipt == last),
            'FRAME_RECEIPT_MISMATCH', 'endpoint crop/sample receipt differs from owner frame')
    require(first.frame_index <= receipt.frame_index <= last.frame_index
            and first.utc <= receipt.utc <= last.utc
            and first.monotonic <= receipt.monotonic <= last.monotonic,
            'RECEIPT_MISMATCH', 'supporting receipt lies outside the original vehicle lifetime')




def validate_lifetime(life):
    from ..vehicle_observation import VehicleClosure
    require(life.closure is not None and isinstance(life.closure, VehicleClosure),
            'INCOMPLETE_LIFETIME', 'owner lifetime is not finalized')
    require(type(life.quarantined) is bool and life.quarantined == (life.closure == VehicleClosure.CLOCK_DISCONTINUITY),
            'QUARANTINE_MISMATCH', 'clock closure/quarantine disagree')
    require(isinstance(life.first_receipt, FrameReceipt) and isinstance(life.last_receipt, FrameReceipt),
            'MISSING_RECEIPT', 'completed owner must retain first and last frame receipts')
    receipt_in_lifetime(life.last_receipt, life)
    require(life.first_receipt.utc >= life.context.started_at, 'RECEIPT_MISMATCH', 'receipt precedes run start')
    integer(life.frames_observed, 2147483647, 'frames_observed')
    require(life.frames_observed <= life.last_receipt.frame_index - life.first_receipt.frame_index + 1,
            'FRAME_COUNT_MISMATCH', 'vehicle frame count exceeds lifetime span')


def validate_parent(evidence, life, identity):
    require(evidence.identity == identity and evidence.first_vehicle_receipt == life.first_receipt
            and evidence.last_vehicle_receipt == life.last_receipt and evidence.closure == life.closure
            and type(evidence.quarantined) is bool and evidence.quarantined == life.quarantined,
            'PARENT_MISMATCH', 'consumer parent/run/camera/closure differs from owner')
