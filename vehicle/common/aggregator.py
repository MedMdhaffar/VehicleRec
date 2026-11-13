"""First in-memory Aggregator boundary; no broker, database or inference."""
from dataclasses import dataclass, replace
from .db1_rows.core import require, score, receipt_in_lifetime, frozen_tree, verify_shared_receipts, validate_lifetime
from .db1_rows.natural import NaturalBuilder, NaturalBundle, NaturalPlateBuilder
from .observation_evidence import VehicleIdentity
from .frame_receipt import FrameReceipt
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.consensus import aggregate_vehicle_type
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.classification import PUBLIC_VEHICLE_TYPES


@dataclass(frozen=True, slots=True)
class ClassSample:
    identity: VehicleIdentity
    receipt: FrameReceipt
    label: str
    confidence: float


def validate_class_samples(lifetime, class_samples):
    """Canonical class-evidence admission, independent of terminal joining.

    Validate attribution and receipts without selecting a vehicle class. Both
    wire closure admission and final Aggregator consensus use these checks.
    """
    require(type(class_samples) is tuple and all(isinstance(s, ClassSample) for s in class_samples),
            'INVALID_METADATA', 'immutable typed class samples required')
    validate_lifetime(lifetime)
    frozen_tree(class_samples)
    require(all(isinstance(s.receipt, FrameReceipt) for s in class_samples),
            'MISSING_RECEIPT', 'class samples require paired frame receipts')
    verify_shared_receipts((lifetime, class_samples), {})
    identity = VehicleIdentity.from_lifetime(lifetime)
    ordered = tuple(sorted(class_samples, key=lambda s: s.receipt.frame_index))
    frames = set()
    for s in ordered:
        require(s.identity == identity, 'PARENT_MISMATCH', 'class sample identity differs')
        require(s.receipt.frame_index not in frames and s.label in PUBLIC_VEHICLE_TYPES,
                'CLASS_SAMPLE_MISMATCH', 'unique normalized class samples required')
        receipt_in_lifetime(s.receipt, lifetime)
        score(s.confidence, nullable=False)
        frames.add(s.receipt.frame_index)
    require(bool(frames), 'MISSING_CLASS_SAMPLES', 'class consensus needs detector samples')
    return ordered


@dataclass(frozen=True, slots=True)
class Branch:
    domain: str
    capability: str  # enabled | disabled | unsupported
    result: object = None  # None on enabled means unfinished, never omission


@dataclass(frozen=True, slots=True)
class JoinResult:
    state: str
    waiting: tuple[str, ...]
    delivery: NaturalBundle | None
    manufacturer_review: object = None


class Aggregator:
    def __init__(self, *, identity_policy, validation):
        self.builder = NaturalBuilder(identity_policy=identity_policy, validation=validation)

    def join(self, owner, class_samples, branches, *, prior=None):
        return self._join(owner, class_samples, branches, prior=prior, plate_format=False)

    def join_with_plate(self, owner, class_samples, branches, *, prior=None):
        """Opt-in natural-db1-bundle-v1; no v1 Redis support in this slice."""
        return self._join(owner, class_samples, branches, prior=prior, plate_format=True)

    def _join(self, owner, class_samples, branches, *, prior, plate_format):
        require(type(branches) is tuple and type(class_samples) is tuple,
                'MUTABLE_EVIDENCE', 'immutable branch and sample sets required')
        require(all(isinstance(b, Branch) for b in branches)
                and all(isinstance(s, ClassSample) for s in class_samples),
                'INVALID_METADATA', 'typed branches and class samples required')
        require(len(branches) == 3 and {b.domain for b in branches} == {'colour','plate','manufacturer'},
                'BRANCH_SET_MISMATCH', 'explicit complete branch set required')
        for b in branches:
            require(b.capability in {'enabled','disabled','unsupported'}, 'INVALID_CAPABILITY', 'explicit capability required')
            require(b.capability == 'enabled' or b.result is None,
                    'CAPABILITY_RESULT_CONFLICT', 'disabled/unsupported branch claims output')
        waiting = tuple(sorted(b.domain for b in branches if b.capability == 'enabled' and b.result is None))
        if prior is not None:
            require(isinstance(prior, NaturalBundle)
                    and prior.identity == VehicleIdentity.from_lifetime(owner.lifetime),
                    'PRIOR_IDENTITY_MISMATCH', 'prior must describe the original lifetime')
            require(not waiting, 'PAYLOAD_CONFLICT',
                    'accepted terminal natural output cannot disappear or regress to waiting')
        if waiting:
            return JoinResult('waiting', waiting, None)
        require(all(b.result is None or b.domain in {'colour','manufacturer'} or (plate_format and b.domain == 'plate') for b in branches),
                'UNSUPPORTED_TERMINAL', 'this slice only supports colour terminal results')
        frozen_tree((owner, class_samples, branches))
        plate = next(b.result for b in branches if b.domain == 'plate')
        if plate_format and plate is not None:
            from .plate_metadata import PlateResult
            require(isinstance(plate, PlateResult) and plate.transcript.capability == 'enabled',
                    'CAPABILITY_RESULT_CONFLICT', 'enabled Plate branch requires enabled terminal evidence')
        colour = next(b.result for b in branches if b.domain == 'colour')
        original_receipts = colour.original_receipts() if colour is not None else ()
        verify_shared_receipts((owner, class_samples, branches, original_receipts, plate.original_receipts() if plate_format and plate else ()), {})
        samples = validate_class_samples(owner.lifetime, class_samples)
        manufacturer = next(b.result for b in branches if b.domain == 'manufacturer')
        if manufacturer is not None:
            from .manufacturer.policy import ManufacturerDecision
            import json
            from contracts.db1.reference import canonical_json_bytes
            from .redis_scalar.codec import pack_receipt
            require(isinstance(manufacturer, ManufacturerDecision), 'INVALID_MANUFACTURER',
                    'typed manufacturer policy decision required')
            manufacturer.validate(owner.lifetime)
            receipt = json.loads(manufacturer.evidence_json)['binding']['receipt']
            require(receipt is None or any(canonical_json_bytes(pack_receipt(s.receipt)) == canonical_json_bytes(receipt) for s in samples),
                    'RECEIPT_MISMATCH', 'manufacturer crop requires an original same-track class receipt')
        sums, counts = {}, {}
        for s in samples:
            sums[s.label] = sums.get(s.label, 0.0) + s.confidence
            counts[s.label] = counts.get(s.label, 0) + 1
        label, confidence = aggregate_vehicle_type(sums, counts, len(samples))
        owner = replace(owner, vehicle_type=label, type_confidence=confidence)
        omissions = tuple(sorted((b.domain, b.capability+'_capability') for b in branches if b.capability != 'enabled'))
        # Recognition is joined, but the canonical DB1 path remains review-only
        # until this VLM and its brand mapping have approved registrations.
        if manufacturer is not None:
            omissions += (('manufacturer','unsupported_capability'),)
        if plate_format:
            if plate is None:
                from .plate_metadata import PlateMetadata, PlateTranscript
                capability = next(b.capability for b in branches if b.domain == 'plate')
                plate = PlateMetadata().evaluate(owner.lifetime, PlateTranscript(
                    VehicleIdentity.from_lifetime(owner.lifetime), capability, (), (), (), True, True, 'settled')).result
            builder = NaturalPlateBuilder(identity_policy=self.builder.identity_policy,
                                           validation=self.builder.validation)
            bundle = builder.construct(owner, colour, plate,
                omissions=tuple((d,r) for d,r in omissions if d != 'plate'), prior=prior)
        else:
            bundle = self.builder.construct(owner, colour, omissions=omissions, prior=prior)
        return JoinResult('ready', (), bundle, manufacturer)
