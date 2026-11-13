"""Opt-in Plate transport. V0 schema/default bounds and route remain separate."""
from dataclasses import asdict
from hashlib import sha256
from uuid import UUID, uuid5
from jsonschema import Draft202012Validator, FormatChecker
from contracts.db1.reference import canonical_json_bytes
from ..plate_metadata import (PlateMetadata, PlateTranscript, PlateCandidate, PlateCropAttempt,
    PlateOperation, PlateModel, MarkerScalar, LocalPlatePolicy)
from ..observation_evidence import VehicleIdentity
from ..aggregator import Aggregator, Branch
from ..vehicle_frame import VehicleBoxObservation
from ..db1_rows.core import require, Blocked
from .codec import (bounded_json, pack_life, unpack_life, pack_receipt, unpack_receipt,
    scalar_float, Message, unpack_colour, unpack_class_samples)

VERSION='vehicle-redis-plate-v1'
POLICY_VERSION='plate-local-v1'
MiB=1024*1024
WIRE_LIMITS={'plate.batch':6*MiB,'plate.terminal':8*MiB,
             'vehicle.closed.plate':65536,'db1.delivery.v1':12*MiB}
MAX_WIRE=max(WIRE_LIMITS.values())
STATE_LIMIT=20*MiB
# ensure_ascii=False intent quotes already encoded JSON once more: at most twice
# the UTF-8 message bytes, plus bounded stream/ID/source and punctuation overhead.
INTENT_LIMIT=2*MAX_WIRE+4096
STATE_VERSIONS={'plate':'plate-state-v1','aggregator_plate':'natural-v1-join-state-v1'}


def parent(identity):
    return dict(org_id=str(identity.context.org_id),run_id=identity.context.run_id,
        camera_id=identity.context.camera.camera_id,service_track_id=identity.service_track_id,
        raw_track_id=identity.raw_track_id,tracker_epoch=identity.tracker_epoch,
        lifetime_sequence=identity.lifetime_sequence)


def pack_transcript(t):
    def candidate(c):
        v=asdict(c);v.pop('identity');v['parent']=parent(c.identity);v['receipt']=pack_receipt(c.receipt)
        return v
    # P1 accepts JSON arrays, not Python tuples. No alternate canonical serializer.
    from ..plate_metadata import _json_scalars
    return _json_scalars(dict(parent=parent(t.identity),capability=t.capability,
        candidates=[candidate(c) for c in sorted(t.candidates,key=lambda c:c.candidate_id)],expected_attempt_ids=sorted(t.expected_attempt_ids),
        attempts=[dict(asdict(a),expected_operation_ids=sorted(a.expected_operation_ids),
            operations=[dict(asdict(o),dependencies=sorted(o.dependencies)) for o in sorted(a.operations,key=lambda o:o.sequence)])
            for a in sorted(t.attempts,key=lambda a:a.submission_ordinal)],detection_complete=t.detection_complete,
        admission_complete=t.admission_complete,settlement=t.settlement,policy=asdict(t.policy)))


def numeric(v):return None if v is None else scalar_float(v)


def unpack_transcript(v,life):
    identity=VehicleIdentity.from_lifetime(life)
    require(v['parent']==parent(identity),'PARENT_MISMATCH','transcript original parent differs')
    candidates=[]
    for c in v['candidates']:
        require(c['parent']==parent(identity),'PARENT_MISMATCH','candidate original parent differs')
        fields={k:x for k,x in c.items() if k not in {'parent','receipt'}}
        fields.update(bbox=tuple(c['bbox']),crop_size=tuple(c['crop_size']),
            quality=numeric(c['quality']),detector_confidence=numeric(c['detector_confidence']))
        candidates.append(PlateCandidate(identity=identity,receipt=unpack_receipt(c['receipt']),**fields))
    attempts=[]
    for a in v['attempts']:
        operations=[]
        for o in a['operations']:
            marker=o['marker']
            if marker is not None:
                marker=MarkerScalar(marker['detected'],numeric(marker['confidence']),
                    tuple(marker['bbox']) if marker['bbox'] is not None else None,numeric(marker['center_x']))
            operations.append(PlateOperation(**{**o,'dependencies':tuple(o['dependencies']),
                'model':PlateModel(**o['model']) if o['model'] else None,
                'marker':marker,'confidence':numeric(o['confidence'])}))
        attempts.append(PlateCropAttempt(a['attempt_id'],a['submission_ordinal'],a['candidate_id'],
            tuple(a['expected_operation_ids']),tuple(operations)))
    return PlateTranscript(identity,v['capability'],tuple(candidates),tuple(v['expected_attempt_ids']),
        tuple(attempts),v['detection_complete'],v['admission_complete'],v['settlement'],
        LocalPlatePolicy(**{k:numeric(x) for k,x in v['policy'].items()}))


def claims(result):
    selected=None
    if result.selected is not None:
        selected=asdict(result.selected);selected['receipt']=pack_receipt(result.selected.receipt)
    return dict(outcome=result.outcome,omission_reason=result.omission_reason,selected=selected)


def read_plate(body,life):
    t=unpack_transcript(body['transcript'],life)
    evaluation=PlateMetadata().evaluate(life,t)
    require(canonical_json_bytes(pack_transcript(t))==canonical_json_bytes(body['transcript']),
            'NONCANONICAL_TRANSCRIPT','publish canonical transcript ordering')
    if t.capability!='enabled':
        # Aggregator's existing disabled-capability path synthesizes exactly this
        # empty settled terminal; do not silently discard extra disabled evidence.
        empty=PlateTranscript(t.identity,t.capability,(),(),(),True,True,'settled')
        require(t==empty,'CAPABILITY_RESULT_CONFLICT','disabled transport requires canonical empty settlement')
    return evaluation


def terminal_result(body,life):
    evaluation=read_plate(body,life)
    require(evaluation.state=='terminal','INCOMPLETE_PLATE','terminal message contains unfinished work')
    result=evaluation.result
    require(canonical_json_bytes(claims(result))==canonical_json_bytes(body['claims'])
            and result.provenance.digest==body['provenance_sha256'],
            'PLATE_DECISION_MISMATCH','Plate claims differ from original transcript')
    return result


def join_bodies(codec,closed,colour,plate):
    require(canonical_json_bytes(closed['life'])==canonical_json_bytes(colour['life'])
            ==canonical_json_bytes(plate['life']), 'PARENT_MISMATCH','joined lifetime differs')
    require(closed['colour_batch_id']==colour['batch_id'] and closed['plate_batch_id']==plate['batch_id'],
            'PAYLOAD_CONFLICT','original accepted batch identity differs')
    life=unpack_life(closed['life'])
    result=terminal_result(plate,life)
    require(result.transcript.capability==closed['plate'],'CAPABILITY_RESULT_CONFLICT','lifecycle capability differs')
    colour_result=unpack_colour(colour['result'],life)
    joined=Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join_with_plate(
        VehicleBoxObservation(life,(),'unknown',0.),unpack_class_samples(closed['classes'],life),(
        Branch('colour','enabled',colour_result),
        Branch('plate',closed['plate'],result if closed['plate']=='enabled' else None),
        Branch('manufacturer','unsupported')))
    require(joined.delivery is not None,'INCOMPLETE_PLATE','terminal dependencies must construct delivery')
    require(canonical_json_bytes(joined.delivery.payload.value()['plate_provenance'])==result.provenance.canonical,
            'PLATE_DECISION_MISMATCH','disabled settlement provenance differs')
    return joined.delivery


class PlateCodec:
    def __init__(self,*,schema,v0):
        self.v0=v0
        self.org,self.identity_policy,self.validation=v0.org,v0.identity_policy,v0.validation
        Draft202012Validator.check_schema(schema)
        self.validator=Draft202012Validator(schema,format_checker=FormatChecker())

    def event_id(self,life,kind,batch_id=None,revision=None):
        return str(uuid5(self.identity_policy.namespace,canonical_json_bytes([
            str(self.org),life.context.run_id,life.context.camera.camera_id,life.service_track_id,
            VERSION,kind,batch_id,revision]).decode()))

    def make(self,kind,body):
        life=unpack_life(body['life'])
        event_id=self.event_id(life,kind,body.get('batch_id'),body.get('revision'))
        return self.encode(kind,event_id,body)

    def encode(self,kind,event_id,body):
        raw=canonical_json_bytes(dict(schema_version=VERSION,policy_version=POLICY_VERSION,
            org_id=str(self.org),event_id=str(event_id),kind=kind,
            body_sha256=sha256(canonical_json_bytes(body)).hexdigest(),body=body))
        return self.decode(raw).canonical

    def decode(self,raw):
        try:
            v=bounded_json(raw,max_bytes=MAX_WIRE,max_depth=32,max_nodes=200000,max_string=4096)
            if v.get('schema_version')=='vehicle-redis-scalar-v1':
                message=self.v0.decode(raw)
                require(message.kind=='colour.terminal','WRONG_VERSION','only existing Colour terminals bridge into v1')
                return message
            self.validator.validate(v)
            require(len(raw)<=WIRE_LIMITS[v['kind']],'WIRE_LIMIT','per-envelope byte capacity')
            require(UUID(v['org_id'])==self.org,'ORG_MISMATCH','untrusted org partition')
            body=v['body'];life=unpack_life(body['life'])
            require(life.context.org_id==self.org,'ORG_MISMATCH','lifetime tenant differs')
            require(sha256(canonical_json_bytes(body)).hexdigest()==v['body_sha256'],'HASH_MISMATCH','body hash differs')
            require(v['event_id']==self.event_id(life,v['kind'],body.get('batch_id'),body.get('revision')),
                    'EVENT_ID_MISMATCH','deterministic versioned event identity required')
            if v['kind']=='plate.batch':
                read_plate(body,life)
            elif v['kind']=='plate.terminal':
                terminal_result(body,life)
            elif v['kind']=='vehicle.closed.plate':
                unpack_class_samples(body['classes'],life)
            else:
                require(canonical_json_bytes(body['life'])==canonical_json_bytes(body['closed']['life']),
                        'PARENT_MISMATCH','delivery lifetime differs')
                expected=join_bodies(self,body['closed'],body['colour'],body['plate'])
                require(expected.payload.canonical==canonical_json_bytes(body['bundle'])
                        and expected.digest==body['bundle_sha256'],'INVALID_DELIVERY','v1 differs from canonical join')
            return Message(v['kind'],v['event_id'],body,canonical_json_bytes(v),life)
        except Blocked:
            raise
        except Exception as error:
            raise Blocked('INVALID_MESSAGE','invalid Plate wire message') from error
