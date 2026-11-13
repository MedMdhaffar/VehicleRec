"""Opt-in Tunis projection. No inference, numeric catalogue keys or database I/O.

The existing natural fact contract is unchanged. A separate transport preserves
the full v3 proof instead of masquerading as a v2 Engine operation transcript.
"""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from uuid import UUID, uuid5

from contracts.db1.reference import canonical_json_bytes, normalize_plate_text
from .aggregator import Aggregator, Branch
from .db1_rows.core import FrozenJSON, require, receipt_in_lifetime
from .db1_rows.natural_policy import NaturalPolicy
from .observation_evidence import VehicleIdentity, RecognitionIdentity
from .plate_metadata import PlateCandidate, PlateModel, PlateResult, PlateSelection
from .vehicle_frame import VehicleBoxObservation
from .mohamed_lpr_contract import canonical, digest
from .mohamed_lpr_contract import sha
from .mohamed_lpr_engine import identity_key
from .mohamed_lpr_stream_codec import Codec as SourceCodec, Scope, crop, scalar_json, local
from .mohamed_lpr_stream_policy import replay
from .redis_scalar.codec import (Message, bounded_json, pack_life, unpack_life, pack_receipt,
    unpack_receipt, pack_colour, unpack_colour, unpack_class_samples, scalar_float)

VERSION = 'vehicle-mohamed-tunis-delivery-v1'
LIMIT = 4 * 1024 * 1024


class ProjectionGap(ValueError):
    """Review-only result: never synthesize an analytical read."""


@dataclass(frozen=True)
class OCRBinding:
    model_ref: str
    identity: RecognitionIdentity

    def __post_init__(self):
        sha(self.model_ref)
        require(isinstance(self.identity,RecognitionIdentity) and self.identity.task=='ocr',
                'MODEL_PROVENANCE_MISMATCH','explicit OCR binding required')


@dataclass(frozen=True)
class TunisTranscript:
    identity: VehicleIdentity
    capability: str
    candidate: PlateCandidate
    payload: FrozenJSON


@dataclass(frozen=True)
class TunisResult(PlateResult):
    def validate(self, lifetime):
        expected = terminal(self.transcript.payload.value(), lifetime)
        require(self == expected, 'PLATE_DECISION_MISMATCH', 'v3 projection differs from sealed evidence')

    def original_receipts(self):
        return (self.transcript.candidate.receipt,)

    def natural_fields(self, lifetime, identity_policy):
        self.validate(lifetime)
        s = self.selected
        uid = str(identity_policy.attempt_uid(self.transcript.identity, s.segment_id, s.attempt_id))
        return s.receipt, NaturalPolicy.plate(outcome='read', attempts=1, attempt_uid=uid,
            ordinal=1, receipt=s.receipt, route='local_private', model=s.model, complete=True,
            structural=True, text=s.text, category='private', confidence=s.confidence, crop_score=s.crop_score)


def sealed(payload):
    """Recompute the unchanged Plate decision from all exact v3 messages."""
    scope = Scope(**payload['scope'])
    codec = SourceCodec(scope)
    source = codec.read(payload['sealed_json'].encode())
    require(source['kind'] == 'plate_result', 'WRONG_STREAM', 'sealed Plate review result required')
    log = scalar_json(payload['proof_json'].encode(), max_bytes=1024*1024, max_depth=40, max_nodes=500000)
    require(type(log) is list and 0 < len(log) <= 128, 'INVALID_DELIVERY', 'bounded complete proof required')
    for event in log:
        require(codec.read(canonical(event)) == event, 'INVALID_DELIVERY', 'invalid source proof envelope')
    plate, proposal, result, _ = replay(codec, log)
    require(result is not None and proposal is not None and canonical(source['body']) == canonical(result),
            'PLATE_DECISION_MISMATCH', 'review is not the settled proof terminal')
    c = proposal.crop
    decision = proposal.decision
    if decision.status != 'accepted':
        raise ProjectionGap('unresolved_review_only')
    if decision.category != 'tunis':
        raise ProjectionGap('category_has_no_approved_db1_mapping')
    if decision.tunis_mode != 'legacy_whole' or c.generation != 1:
        raise ProjectionGap('mode_or_recheck_requires_delivery_contract')
    if set(dict(decision.observed_text)) != {'registration'}:
        raise ProjectionGap('regional_or_multi_model_read_requires_delivery_contract')
    registration = next((r for r in plate._results.values() if r.request.role == 'registration'), None)
    require(registration is not None and registration.outcome == 'succeeded' and registration.provenance is not None,
            'MISSING_MODEL_EVIDENCE', 'actual successful registration OCR required')
    require(all(event['body']['crop'] == asdict(c) for event in log if event['kind'] == 'offer'),
            'INVALID_DELIVERY', 'multiple crop attempts cannot be collapsed')
    return c, decision, registration


def terminal(payload, life):
    require(set(payload) == {'scope','sealed_json','proof_json','candidate','frame_sha256','frame_receipt_json','model_binding','expected_sessions'},
            'INVALID_DELIVERY', 'closed v3 Plate delivery evidence required')
    c, decision, registration = sealed(payload)
    identity = VehicleIdentity.from_lifetime(life)
    require((c.org_id,c.run_id,c.camera_id,c.vehicle_lifetime_id) ==
            (str(life.context.org_id),life.context.run_id,life.context.camera.camera_id,identity_key(identity)),
            'PARENT_MISMATCH', 'v3 vehicle lifetime binding differs')
    require(payload['expected_sessions'] == [c.session_id], 'INCOMPLETE_PLATE', 'one complete vehicle Plate session required')
    v = payload['candidate']
    require(set(v) == set(PlateCandidate.__dataclass_fields__) - {'identity'}, 'INVALID_DELIVERY', 'closed Engine candidate required')
    candidate = PlateCandidate(identity=identity, **{**v,'receipt':unpack_receipt(v['receipt']),
        'bbox':tuple(v['bbox']),'crop_size':tuple(v['crop_size']),
        'quality':scalar_float(v['quality']) if v['quality'] is not None else None,
        'detector_confidence':scalar_float(v['detector_confidence']) if v['detector_confidence'] is not None else None})
    # crop_accepted is the original v2-adapter flag. The v3 Engine admission is
    # proven by the complete sealed request/result graph above, not that flag.
    # Retain its original value; never invent v2 admission for a v3-only crop.
    require(candidate.association == 'associated' and type(candidate.crop_accepted) is bool
            and candidate.candidate_id == c.candidate_id and candidate.crop_ref == c.crop_id
            and candidate.crop_sha256 == c.crop_sha256 and candidate.crop_size == (c.width,c.height)
            and str(candidate.raw_plate_id) == c.plate_track_id and payload['frame_sha256'] == c.frame_sha256
            and sha256(payload['frame_receipt_json'].encode()).hexdigest() == c.frame_id
            and canonical_json_bytes(v['receipt']) == canonical_json_bytes(scalar_json(payload['frame_receipt_json'].encode())),
            'PARENT_MISMATCH', 'original Engine crop/frame/association differs')
    require(c.session_id == 'mohamed-v3-'+identity_key(identity)[:32]+'-segment-'+str(candidate.segment_id),
            'PARENT_MISMATCH','plate segment differs from source session')
    receipt_in_lifetime(candidate.receipt, life)
    binding = payload['model_binding']
    require(set(binding) == {'model_ref','identity'} and binding['model_ref'] == registration.provenance.model_ref,
            'MODEL_PROVENANCE_MISMATCH', 'selected OCR must match actual execution provenance')
    model = PlateModel(**binding['identity'])
    require(model.task == 'ocr', 'MODEL_PROVENANCE_MISMATCH', 'registration model must be OCR')
    fields = dict(decision.registration_fields)
    require(set(fields) == {'series','registration_number'}, 'PLATE_READ_ADMISSION', 'private fields required')
    text = normalize_plate_text(fields['series'] + ' تونس ' + fields['registration_number'])
    selection = PlateSelection('v3-crop-' + c.crop_id, candidate.segment_id, candidate.receipt,
        candidate.quality, registration.request.operation_id, model, text, registration.confidence, 'private')
    transcript = TunisTranscript(identity, 'enabled', candidate, FrozenJSON.of(payload))
    provenance = FrozenJSON.of(dict(version=VERSION, evidence=payload, selected_operation_id=registration.request.operation_id,
        route='local_private', source_policy=registration.request.policy_version))
    return TunisResult(transcript, 'read', None, selection, provenance)


class TunisDeliveryCodec:
    """Opt-in envelope; uses unchanged Aggregator/fact schemas and DB1 consumer."""
    def __init__(self, *, base, scope, bindings):
        self.org, self.validation, self.identity_policy = base.org, base.validation, base.identity_policy
        self.scope, self.source = scope, SourceCodec(scope)
        self.bindings = tuple(bindings)
        require(str(self.org) == scope.org_id and all(isinstance(b, OCRBinding) for b in self.bindings),
                'ORG_MISMATCH', 'trusted scope and explicit OCR bindings required')
        require(len({b.model_ref for b in self.bindings})==len(self.bindings),
                'MODEL_PROVENANCE_MISMATCH','ambiguous OCR registration binding')

    def plate_reader(self, payload, life):
        require(payload['scope'] == asdict(self.scope), 'PARENT_MISMATCH', 'source Engine scope changed')
        result = terminal(payload, life)
        b = payload['model_binding']
        require(any(x.model_ref == b['model_ref'] and x.identity.policy() == b['identity'] for x in self.bindings),
                'MISSING_MODEL_EVIDENCE', 'actual OCR has no supplied registration binding')
        return result

    def bundle(self, body):
        life = unpack_life(body['life'])
        result = self.plate_reader(body['plate'], life)
        colour = unpack_colour(body['colour']['result'], life)
        return Aggregator(identity_policy=self.identity_policy, validation=self.validation).join_with_plate(
            VehicleBoxObservation(life, (), 'unknown', 0.), unpack_class_samples(body['classes'], life),
            (Branch('colour','enabled',colour), Branch('plate','enabled',result), Branch('manufacturer','unsupported'))).delivery

    def make(self, body):
        body = dict(body)
        bundle = self.bundle(body)
        body.update(bundle=bundle.payload.value(), bundle_sha256=bundle.digest)
        life = unpack_life(body['life'])
        uid = str(uuid5(self.identity_policy.namespace, canonical_json_bytes([
            VERSION,str(self.org),life.context.run_id,life.context.camera.camera_id,life.service_track_id]).decode()))
        raw = canonical_json_bytes(dict(schema_version=VERSION, kind='db1.delivery.v1',
            event_id=uid, body_sha256=sha256(canonical_json_bytes(body)).hexdigest(), body=body))
        return self.decode(raw).canonical

    def decode(self, raw):
        v = bounded_json(raw,max_bytes=LIMIT,max_depth=40,max_nodes=300000,max_string=2*1024*1024)
        require(set(v) == {'schema_version','kind','event_id','body_sha256','body'} and v['schema_version'] == VERSION
                and v['kind'] == 'db1.delivery.v1', 'WRONG_VERSION', 'explicit Tunis delivery envelope required')
        body = v['body']
        require(set(body) == {'life','classes','colour','plate','bundle','bundle_sha256'}, 'INVALID_DELIVERY', 'closed delivery body required')
        expected = self.bundle(body)
        life = unpack_life(body['life'])
        uid = str(uuid5(self.identity_policy.namespace, canonical_json_bytes([
            VERSION,str(self.org),life.context.run_id,life.context.camera.camera_id,life.service_track_id]).decode()))
        require(v['event_id'] == uid and v['body_sha256'] == sha256(canonical_json_bytes(body)).hexdigest()
                and canonical_json_bytes(body['bundle']) == expected.payload.canonical and body['bundle_sha256'] == expected.digest,
                'PAYLOAD_CONFLICT', 'canonical Aggregator delivery differs')
        return Message('db1.delivery.v1', uid, body, canonical_json_bytes(v), life)

    def from_engine(self, *, sealed_raw, proof, bound, closed, expected_sessions):
        source = self.source.read(sealed_raw)
        require(source['kind'] == 'plate_result' and source['body']['crop'] == asdict(bound.scalar),
                'PARENT_MISMATCH', 'review differs from original Engine crop')
        c = bound.scalar
        original = bound.resource.binding.candidate
        identity = VehicleIdentity.from_lifetime(closed.owner.lifetime)
        require(original.identity == identity and original.receipt is bound.receipt,
                'PARENT_MISMATCH', 'Engine receipt/owner differs')
        require(original.bbox == bound.original_bbox, 'PARENT_MISMATCH', 'original frame coordinates differ')
        candidate = replace(original, candidate_id=c.candidate_id, crop_ref=c.crop_id)
        v = asdict(candidate);v.pop('identity');v['receipt'] = pack_receipt(candidate.receipt)
        v = scalar_json(canonical(v))
        payload = dict(scope=asdict(self.scope),sealed_json=sealed_raw.decode(),proof_json=canonical(proof).decode(),
            candidate=v,frame_sha256=c.frame_sha256,frame_receipt_json=canonical(pack_receipt(bound.receipt)).decode(),
            model_binding={},expected_sessions=list(expected_sessions))
        _, _, registration = sealed(payload)
        binding = next((b for b in self.bindings if b.model_ref == registration.provenance.model_ref),None)
        require(binding is not None, 'MISSING_MODEL_EVIDENCE', 'actual OCR registration binding missing')
        payload['model_binding'] = dict(model_ref=binding.model_ref,identity=binding.identity.policy())
        from .colour_metadata import ColourMetadata
        colour = ColourMetadata().finalize(closed.owner.lifetime, closed.measurements)
        body = dict(life=pack_life(closed.owner.lifetime), classes=[dict(receipt=pack_receipt(s.receipt),
            label=s.label,confidence=s.confidence) for s in closed.classes],colour=dict(result=pack_colour(colour)),plate=payload)
        return self.make(body)
