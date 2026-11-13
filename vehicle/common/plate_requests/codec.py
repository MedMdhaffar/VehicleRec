"""Closed scalar-only v2 envelopes. No executor/model selectors in requests."""
from hashlib import sha256
from uuid import UUID, uuid5
from jsonschema import Draft202012Validator, FormatChecker
from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require, Blocked
from ..redis_scalar.codec import bounded_json, unpack_life, Message
from ..redis_scalar.plate_codec import unpack_transcript, parent
from ..observation_evidence import VehicleIdentity
from ..plate_metadata import PlateMetadata

VERSION='vehicle-plate-requests-v2'
STREAMS={'crop':'engine.crops.v2','request':'plate.requests.v2','admission':'engine.admissions.v2',
         'evidence':'engine.executions.v2','seal':'plate.seals.v2','sealed':'engine.sealed.v2'}
MAX_BYTES=6*1024*1024


def digest(value):return sha256(canonical_json_bytes(value)).hexdigest()


class RequestCodec:
    def __init__(self,v1,schema):
        self.v1=v1
        self.org,self.identity_policy,self.validation=v1.org,v1.identity_policy,v1.validation
        Draft202012Validator.check_schema(schema)
        self.validator=Draft202012Validator(schema,format_checker=FormatChecker())

    def request_id(self,body):
        life=unpack_life(body['life'])
        return str(uuid5(self.identity_policy.namespace,canonical_json_bytes([
            VERSION,parent(VehicleIdentity.from_lifetime(life)),body['session'],
            body['attempt_id'],body['ordinal'],body['candidate_id'],body['sequence']]).decode()))

    def event_id(self,kind,body):
        life=unpack_life(body['life'])
        request=body if kind=='request' else body.get('request')
        return str(uuid5(self.identity_policy.namespace,canonical_json_bytes([
            VERSION,parent(VehicleIdentity.from_lifetime(life)),body['session'],kind,
            request['request_id'] if request else None]).decode()))

    def make(self,kind,body):
        return self.decode(canonical_json_bytes(dict(schema_version=VERSION,policy_version='plate-local-v1',
            org_id=str(self.org),event_id=self.event_id(kind,body),kind=kind,body_sha256=digest(body),body=body))).canonical

    def decode(self,raw):
        try:
            v=bounded_json(raw,max_bytes=MAX_BYTES,max_depth=32,max_nodes=200000,max_string=4096)
            self.validator.validate(v)
            body=v['body'];life=unpack_life(body['life'])
            require(UUID(v['org_id'])==self.org==life.context.org_id,'ORG_MISMATCH','trusted org partition differs')
            require(v['body_sha256']==digest(body),'HASH_MISMATCH','body hash changed')
            require(v['event_id']==self.event_id(v['kind'],body),'EVENT_ID_MISMATCH','stable event identity required')
            request=body if v['kind']=='request' else body.get('request')
            if request:
                require(request['request_id']==self.request_id(request),'REQUEST_ID_MISMATCH','stable full-binding request identity required')
                for k in ('life','session','batch_id'):
                    require(digest(request[k])==digest(body[k]),'PARENT_MISMATCH','response request binding differs')
                require(len({d['operation_id'] for d in request['dependencies']})==len(request['dependencies']),
                        'INVALID_DEPENDENCY','dependency identity repeated')
            if v['kind'] in {'crop','sealed'}:
                t=unpack_transcript(body['transcript'],life)
                evaluation=PlateMetadata().evaluate(life,t)
                if v['kind']=='crop':
                    require(not t.attempts and not t.expected_attempt_ids and t.detection_complete
                            and not t.admission_complete and t.settlement=='open',
                            'INVALID_OFFER','crop metadata cannot claim future admission/execution')
                    offers=body['offers'];candidates={c.candidate_id:c for c in t.candidates}
                    require(len({o['attempt_id'] for o in offers})==len({o['candidate_id'] for o in offers})==len({o['ordinal'] for o in offers})==len(offers),
                            'INVALID_OFFER','unique attempt/candidate/ordinal required')
                    require(all(o['candidate_id'] in candidates and candidates[o['candidate_id']].crop_accepted for o in offers),
                            'INVALID_OFFER','offers require accepted original crops')
                    require({o['candidate_id'] for o in offers}=={c.candidate_id for c in t.candidates if c.crop_accepted},
                            'INVALID_OFFER','exact eligible crop offer set required')
                    require(t.capability=='enabled' or not offers,'CAPABILITY_RESULT_CONFLICT','disabled branch cannot offer crops')
                else:
                    require(evaluation.state=='terminal' and t.admission_complete and t.settlement=='settled',
                            'EXECUTION_WAITING','sealed response requires complete known retired execution')
            if v['kind']=='evidence':
                op=body['operation'];r=body['request']
                require(op['operation_id']==r['request_id'] and op['sequence']==r['sequence']
                        and op['kind']==r['operation'] and op['dependencies']==[d['operation_id'] for d in r['dependencies']]
                        and op['outcome'] in {'succeeded','failed','not_started','unknown'},
                        'INVALID_EXECUTION','original request/evidence correlation required')
                require(body['generation']==digest(op),'HASH_MISMATCH','exact immutable evidence generation required')
            if v['kind']=='admission':
                require(body['accepted']==(body['reason']=='accepted'),'INVALID_ADMISSION','acceptance differs from reason')
            if v['kind'] in {'seal','sealed'}:
                seal=body if v['kind']=='seal' else body['seal']
                require(seal['request_ids']==sorted(seal['request_ids']) and seal['revision']==len(seal['request_ids']),
                        'SEAL_MISMATCH','exact sorted request set and revision required')
                require(all(digest(seal[k])==digest(body[k]) for k in ('life','session','batch_id')),
                        'PARENT_MISMATCH','seal binding changed')
            return Message(v['kind'],v['event_id'],body,canonical_json_bytes(v),life)
        except Blocked:raise
        except Exception as error:raise Blocked('INVALID_MESSAGE','invalid bounded request envelope') from error
