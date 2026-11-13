"""Immutable natural DB1 bundle construction, independent of SQL resolution."""
from dataclasses import dataclass
from uuid import UUID

from .core import (FrozenJSON, require, frozen_tree, verify_shared_receipts,
                   integer, validate_lifetime, validate_parent)
from .natural_policy import NaturalPolicy
from ..db1_context import positive_bigint
from ..observation_evidence import VehicleIdentity
from .validation import ContractValidation


@dataclass(frozen=True, slots=True)
class NaturalBundle:
    identity: VehicleIdentity
    payload: FrozenJSON

    @property
    def digest(self):
        return self.payload.digest


class NaturalBuilder:
    """Build supported parent/colour facts only, without surrogate keys or crops."""
    def __init__(self, *, identity_policy, validation):
        require(isinstance(validation, ContractValidation), 'INVALID_VALIDATION', 'loaded contracts required')
        require(callable(getattr(identity_policy, 'row_uid', None)), 'INVALID_IDENTITY_POLICY', 'stable IDs required')
        self.identity_policy, self.validation = identity_policy, validation

    def construct(self, owner, colour, *, omissions, prior=None):
        life = owner.lifetime
        identity = VehicleIdentity.from_lifetime(life)
        frozen_tree((owner, colour, omissions))
        original_receipts = colour.original_receipts() if colour is not None else ()
        verify_shared_receipts((owner, colour, original_receipts), {})
        positive_bigint(identity.service_track_id, 'service_track_id')
        integer(identity.tracker_epoch, 2**63 - 2, 'tracker epoch', 0)
        integer(identity.raw_track_id, 2**63 - 1, 'raw track ID', 0)
        integer(identity.lifetime_sequence, 2**63 - 1, 'lifetime sequence')
        validate_lifetime(life)
        require(not life.quarantined, 'CLOCK_QUARANTINE', 'quarantined lifetime cannot emit analytical facts')
        require(type(omissions) is tuple and len(set(d for d, _ in omissions)) == len(omissions)
                and all(d in {'colour', 'plate', 'manufacturer'}
                        and r in {'disabled_capability', 'unsupported_capability'} for d,r in omissions),
                'INVALID_OMISSION', 'unique explicit terminal capability omissions required')
        omissions = tuple(sorted(omissions))
        omitted = {d for d, _ in omissions}
        require({'plate', 'manufacturer'} <= omitted and (('colour' in omitted) == (colour is None)),
                'BRANCH_SET_MISMATCH', 'every absent branch requires a truthful terminal omission')
        facts = []
        def append(kind, receipt, fields):
            uid = str(UUID(str(self.identity_policy.row_uid(identity, kind))))
            facts.append(dict(row_kind=kind, delivery=NaturalPolicy.delivery(
                kind, identity, receipt, fields, uid, self.validation)))
        append('fact_vehicle', life.first_receipt, NaturalPolicy.vehicle(owner))
        provenance = None
        if colour is not None:
            e = colour.evidence
            validate_parent(e, life, identity)
            colour.validate(life)
            require(e.source == 'local_hsv' and e.agent_attempts == 0 and e.agent_crop is None
                    and e.best_crop is None, 'MODEL_PROVENANCE_MISMATCH', 'metadata-only local colour required')
            receipt, fields, _ = NaturalPolicy.colour(e, life)
            append('fact_vehicle_colour', receipt, fields)
            provenance = colour.provenance.value()
        payload = FrozenJSON.of(dict(schema_version='natural-db1-bundle-v0',
            org_id=str(life.context.org_id), facts=facts, omissions=[dict(domain=d, reason=r) for d,r in omissions],
            colour_provenance=provenance))
        if prior is not None:
            require(isinstance(prior, NaturalBundle) and prior.identity == identity,
                    'PRIOR_IDENTITY_MISMATCH', 'prior must describe the original lifetime')
            require(prior.payload.canonical == payload.canonical, 'PAYLOAD_CONFLICT',
                    'accepted natural payload or original attempt provenance changed')
        return NaturalBundle(identity, payload)


class NaturalPlateBuilder(NaturalBuilder):
    """Explicit v1 Plate-capable bundle; v0 construction/Redis stay unchanged."""
    def construct(self, owner, colour, plate, *, omissions, prior=None):
        from ..plate_metadata import PlateResult
        require(isinstance(plate, PlateResult), 'INVALID_METADATA', 'terminal typed Plate result required')
        life = owner.lifetime
        identity = VehicleIdentity.from_lifetime(life)
        require(plate.transcript.identity == identity, 'PARENT_MISMATCH', 'Plate differs from owner')
        frozen_tree((owner, colour, plate, omissions))
        verify_shared_receipts((owner, colour, plate, colour.original_receipts() if colour else ()), {})
        plate.validate(life)
        require(type(omissions) is tuple and all(d != 'plate' for d,r in omissions),
                'INVALID_OMISSION', 'Plate domain supplies its own authoritative terminal')
        # Reuse the complete v0 parent/Colour validation and serialization path.
        # The internal absent-Plate placeholder is removed before v1 publication.
        base = super().construct(owner, colour, omissions=omissions+(('plate','disabled_capability'),))
        payload = base.payload.value()
        payload['schema_version'] = 'natural-db1-bundle-v1'
        payload['omissions'] = [o for o in payload['omissions'] if o['domain'] != 'plate']
        if plate.outcome == 'omitted':
            from .natural_policy import Omitted
            Omitted(plate.omission_reason, 'domain-supported terminal omission')
            payload['omissions'].append(dict(domain='plate', reason=plate.omission_reason))
        else:
            receipt, fields = plate.natural_fields(life, self.identity_policy)
            uid = str(UUID(str(self.identity_policy.row_uid(identity, 'fact_plate_read'))))
            payload['facts'].append(dict(row_kind='fact_plate_read', delivery=NaturalPolicy.delivery(
                'fact_plate_read', identity, receipt, fields, uid, self.validation)))
        payload['omissions'].sort(key=lambda o:(o['domain'],o['reason']))
        payload['plate_provenance'] = plate.provenance.value()
        payload = FrozenJSON.of(payload)
        if prior is not None:
            require(isinstance(prior, NaturalBundle) and prior.identity == identity,
                    'PRIOR_IDENTITY_MISMATCH', 'prior describes another lifetime')
            require(prior.payload.canonical == payload.canonical, 'PAYLOAD_CONFLICT',
                    'accepted natural row, omission or original provenance changed')
        return NaturalBundle(identity, payload)
