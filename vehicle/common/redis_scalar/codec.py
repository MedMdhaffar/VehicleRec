"""Closed versioned scalar JSON; no pickle, runtime imports, I/O or connections on import."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import re
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from contracts.db1.reference import canonical_json_bytes, strict_loads, parse_timestamp
from ..db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
from ..frame_receipt import FrameReceipt
from ..vehicle_observation import VehicleLifetime, VehicleClosure
from ..vehicle_frame import VehicleBoxObservation
from ..observation_evidence import VehicleIdentity, RecognitionIdentity
from ..colour_metadata import ColourMetadata, ColourMeasurement
from ..aggregator import ClassSample, validate_class_samples
from ..db1_rows.core import FrozenJSON, Blocked, require, validate_lifetime
from ..db1_rows.natural import NaturalBuilder

MAX_BYTES, MAX_DEPTH, MAX_NODES = 65536, 16, 8192
VERSION = 'vehicle-redis-scalar-v1'


def bounded_json(raw, *, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH, max_nodes=MAX_NODES,
                 max_string=2048):
    require(type(raw) is bytes and len(raw) <= max_bytes, 'WIRE_LIMIT', 'message byte limit')
    # Check depth before JSON parser allocation/recursion, respecting quoted strings.
    depth, quoted, escape = 0, False, False
    for b in raw:
        if quoted:
            if escape:
                escape = False
            elif b == 92:
                escape = True
            elif b == 34:
                quoted = False
        elif b == 34:
            quoted = True
        elif b in (91, 123):
            depth += 1
            require(depth <= max_depth, 'WIRE_LIMIT', 'message nesting limit')
        elif b in (93, 125):
            depth -= 1
    value = strict_loads(raw)
    nodes = 0
    def visit(v):
        nonlocal nodes
        nodes += 1
        require(nodes <= max_nodes, 'WIRE_LIMIT', 'message node limit')
        if isinstance(v, dict):
            for k, child in v.items():
                require(len(k) <= 256, 'WIRE_LIMIT', 'property length limit')
                visit(child)
        elif isinstance(v, list):
            require(len(v) <= 128, 'WIRE_LIMIT', 'array count limit')
            for child in v:
                visit(child)
        elif isinstance(v, str):
            require(len(v) <= max_string, 'WIRE_LIMIT', 'string length limit')
        elif isinstance(v, (int, Decimal)) and not isinstance(v, bool):
            number = Decimal(v)
            require(number.is_finite() and len(number.as_tuple().digits) <= 64
                    and abs(number.adjusted()) <= 300, 'WIRE_LIMIT', 'numeric limit')
    visit(value)
    return FrozenJSON.of(value).value()  # P1 exact numbers; mathematical integers restored.


def pack_receipt(r):
    return dict(frame_index=r.frame_index, ts=r.utc.isoformat(), monotonic=r.monotonic, media_pts=r.media_pts)


def scalar_float(value):
    """Wire scalar/clock profile: reject values that would silently change bytes."""
    result=float(value)
    require(canonical_json_bytes(result)==canonical_json_bytes(value),
            'NUMERIC_PRECISION', 'scalar cannot round-trip through the domain float profile')
    return result


def unpack_receipt(v):
    return FrameReceipt(v['frame_index'], parse_timestamp(v['ts']), scalar_float(v['monotonic']),
                        None if v['media_pts'] is None else scalar_float(v['media_pts']))


def pack_life(life):
    c = life.context
    require(not c.database_keys_resolved and c.environment == 'development',
            'RESOLVED_CONTEXT', 'this transport supports explicit unresolved scalar contexts only')
    return dict(context=dict(org_id=str(c.org_id),camera_id=c.camera.camera_id,
        camera_timezone=c.camera.timezone,valid_from=c.camera.valid_from.isoformat(),
        valid_to=c.camera.valid_to.isoformat() if c.camera.valid_to else None, run_id=c.run_id,
        source_kind=c.source_kind,started_at=c.started_at.isoformat(),service_version=c.service_version,
        detector=dict(model_name=c.resolution.detector.model_name,version=c.resolution.detector.version,task='detect'),
        resolution_provenance=c.resolution.provenance),service_track_id=life.service_track_id,
        tracker_epoch=life.tracker_epoch,raw_track_id=life.raw_track_id,lifetime_sequence=life.lifetime_sequence,
        first=pack_receipt(life.first_receipt),last=pack_receipt(life.last_receipt),
        frames_observed=life.frames_observed,closure=life.closure.value,quarantined=life.quarantined)


def unpack_life(v):
    c = v['context']
    org = UUID(c['org_id'])
    camera = CameraRegistration(org,c['camera_id'],None,c['camera_timezone'],parse_timestamp(c['valid_from']),
                                parse_timestamp(c['valid_to']) if c['valid_to'] else None)
    context = RunContext(org,camera,c['run_id'],c['source_kind'],parse_timestamp(c['started_at']),c['service_version'],
        ResolutionInfo(None,None,'vehicle',ModelRegistration(None,**c['detector']),None,
                       c['resolution_provenance'],False),'development')
    life = VehicleLifetime(context,v['service_track_id'],v['tracker_epoch'],v['raw_track_id'],
        v['lifetime_sequence'],unpack_receipt(v['first']),unpack_receipt(v['last']),v['frames_observed'],
        VehicleClosure(v['closure']),v['quarantined'])
    validate_lifetime(life)
    return life


def unpack_class_samples(values, life):
    identity=VehicleIdentity.from_lifetime(life)
    samples=tuple(ClassSample(identity,unpack_receipt(s['receipt']),s['label'],scalar_float(s['confidence']))
                  for s in values)
    return validate_class_samples(life,samples)


def pack_attempt(m):
    return dict(attempt_id=m.attempt_id,**pack_receipt(m.receipt),label=m.label,
        confidence=m.confidence,quality=m.quality,algorithm=m.algorithm.policy(),quality_algorithm=m.quality_algorithm)


def unpack_attempt(v, life):
    return ColourMeasurement(VehicleIdentity.from_lifetime(life),v['attempt_id'],unpack_receipt(v),v['label'],
        scalar_float(v['confidence']),scalar_float(v['quality']),RecognitionIdentity(**v['algorithm']),v['quality_algorithm'])


def pack_colour(result):
    e = result.evidence
    return dict(claims=dict(state=e.state,label=e.label,confidence=e.confidence,support=pack_receipt(e.support_receipt),
                model=e.executed_model.policy(),samples=len(e.samples)),provenance=result.provenance.value())


def unpack_colour(v, life):
    result = ColourMetadata().finalize(life,tuple(unpack_attempt(a,life) for a in v['provenance']['attempts']))
    require(canonical_json_bytes(pack_colour(result)) == canonical_json_bytes(v),
            'COLOUR_DECISION_MISMATCH', 'terminal claims differ from canonical Colour result')
    return result


def vehicle_key(life):
    i = VehicleIdentity.from_lifetime(life)
    return sha256(canonical_json_bytes([str(i.context.org_id),i.context.run_id,
        i.context.camera.camera_id,i.service_track_id])).hexdigest()


@dataclass(frozen=True)
class Message:
    kind: str
    event_id: str
    body: dict
    canonical: bytes
    life: VehicleLifetime


class Codec:
    def __init__(self, *, schema, trusted_org, identity_policy, validation):
        self.org = UUID(str(trusted_org))
        self.identity_policy, self.validation = identity_policy, validation
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def event_id(self, life, role):
        return str(uuid5(self.identity_policy.namespace, canonical_json_bytes([
            str(self.org),life.context.run_id,life.context.camera.camera_id,life.service_track_id,role]).decode()))

    def encode(self, kind, event_id, body):
        raw = canonical_json_bytes(dict(schema_version=VERSION,event_id=str(event_id),org_id=str(self.org),
            kind=kind,body_sha256=sha256(canonical_json_bytes(body)).hexdigest(),body=body))
        return self.decode(raw).canonical

    def decode(self, raw):
        try:
            v = bounded_json(raw)
            self.validator.validate(v)
            require(UUID(v['org_id']) == self.org, 'ORG_MISMATCH', 'untrusted organization binding')
            body = v['body']
            require(sha256(canonical_json_bytes(body)).hexdigest() == v['body_sha256'],
                    'HASH_MISMATCH', 'body hash mismatch')
            life = unpack_life(body['life'])
            require(life.context.org_id == self.org, 'ORG_MISMATCH', 'lifetime organization differs')
            if v['kind'] == 'colour.batch':
                ids = [a['attempt_id'] for a in body['measurements']]
                require(bool(ids) and len(set(ids)) == len(ids)
                        and len(body['expected_attempt_ids']) == len(ids)
                        and set(body['expected_attempt_ids']) == set(ids),
                        'INCOMPLETE_BATCH', 'batch must explicitly enumerate every original attempt')
                ColourMetadata().finalize(life,tuple(unpack_attempt(a,life) for a in body['measurements']))
            elif v['kind'] == 'vehicle.closed':
                unpack_class_samples(body['classes'],life)
            elif v['kind'] == 'colour.terminal':
                unpack_colour(body['result'],life)
            elif v['kind'] == 'db1.delivery':
                payload = body['bundle']
                require(sha256(canonical_json_bytes(payload)).hexdigest() == body['bundle_sha256'],
                        'HASH_MISMATCH', 'bundle hash mismatch')
                # Delivery payload is an existing, fully closed natural contract.
                require(set(payload)=={'schema_version','org_id','facts','omissions','colour_provenance'}
                        and payload['schema_version']=='natural-db1-bundle-v0'
                        and payload['org_id']==str(self.org), 'INVALID_DELIVERY', 'natural bundle shape mismatch')
                require(len(payload['facts']) in (1,2), 'INVALID_DELIVERY', 'parent/colour facts only')
                for fact in payload['facts']:
                    require(set(fact)=={'row_kind','delivery'} and fact['row_kind'] in {'fact_vehicle','fact_vehicle_colour'},
                            'INVALID_DELIVERY','unsupported natural fact')
                    self.validation.delivery(fact['row_kind'],fact['delivery'])
                    p=fact['delivery']
                    require((p['org_id'],p['run_id'],p['camera_id'],p['track_id']) ==
                        (str(self.org),life.context.run_id,life.context.camera.camera_id,life.service_track_id),
                        'PARENT_MISMATCH','natural fact identity differs')
                require([f['row_kind'] for f in payload['facts']] in
                        [['fact_vehicle'],['fact_vehicle','fact_vehicle_colour']],
                        'INVALID_DELIVERY','ordered unique parent/colour facts required')
                omissions = payload['omissions']
                require(isinstance(omissions,list) and all(isinstance(o,dict)
                        and set(o)=={'domain','reason'} for o in omissions),
                        'INVALID_DELIVERY','closed terminal omission records required')
                parent=payload['facts'][0]['delivery']
                owner=VehicleBoxObservation(life,(),parent['class']['name'],parent['class_conf'])
                colour=None
                if len(payload['facts'])==2:
                    colour=ColourMetadata().finalize(life,tuple(unpack_attempt(a,life)
                        for a in payload['colour_provenance']['attempts']))
                expected=NaturalBuilder(identity_policy=self.identity_policy,validation=self.validation).construct(
                    owner,colour,omissions=tuple((o['domain'],o['reason']) for o in omissions))
                require(expected.payload.canonical==canonical_json_bytes(payload),
                        'INVALID_DELIVERY','natural bundle differs from canonical construction')
            return Message(v['kind'],str(UUID(v['event_id'])),body,canonical_json_bytes(v),life)
        except Blocked:
            raise
        except Exception as error:
            # Never echo hostile values, endpoints, secrets or schema exception text.
            raise Blocked('INVALID_MESSAGE','invalid scalar wire message') from error
