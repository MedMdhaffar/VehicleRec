"""Fenced, bounded state/output/ACK handoff. No connection is opened here.

Each prepared journal intent retains exact publication bytes and fixed stream IDs.
Every mutation checks the lease, including publication/replay and acknowledgment.
One pending intent per component partition prevents later publication overtaking
an interrupted output. Lua errors are retried from retained intent, never assumed
rolled back. No expiry/trimming of accepted state or unfinished work.
"""
import json
import re
from uuid import uuid4

from ..db1_rows.core import FrozenJSON, require
from contracts.db1.reference import canonical_json_bytes, strict_loads

ACQUIRE = '''
local held=redis.call('GET',KEYS[1])
if held then return false end
local generation=redis.call('INCR',KEYS[2])
local token=ARGV[1]..':'..generation
redis.call('SET',KEYS[1],token,'PX',ARGV[2])
return token
'''
GUARD = '''
if redis.call('GET',KEYS[1]) ~= ARGV[1] then return redis.error_reply('STALE_OWNER') end
'''
RENEW = GUARD+"return redis.call('PEXPIRE',KEYS[1],ARGV[2])"
RELEASE = GUARD+"return redis.call('DEL',KEYS[1])"
PREPARE = GUARD+'''
if redis.call('HEXISTS',KEYS[2],'pending')==1 then return redis.error_reply('REPAIR_REQUIRED') end
local rev=tonumber(redis.call('HGET',KEYS[2],'revision') or '0')
if rev~=tonumber(ARGV[2]) then return redis.error_reply('REVISION_CONFLICT') end
local seen=redis.call('HGET',KEYS[2],ARGV[3])
if seen then
  if seen~=ARGV[4] then return redis.error_reply('PAYLOAD_CONFLICT') end
  return 'duplicate'
end
local events=tonumber(redis.call('HGET',KEYS[2],'events') or '0')
local vehicles=tonumber(redis.call('HGET',KEYS[2],'vehicles') or '0')
local newvehicle=(ARGV[5]~='' and redis.call('HEXISTS',KEYS[2],ARGV[5])==0)
if events>=tonumber(ARGV[8]) or (newvehicle and vehicles>=tonumber(ARGV[9])) then
 return redis.error_reply('CAPACITY_EXCEEDED')
end
if newvehicle then vehicles=vehicles+1 end
-- One HSET command: intent, state, revision and dedupe progress cannot separate.
local values={'pending',ARGV[7],'revision',rev+1,'events',events+1,'vehicles',vehicles,ARGV[3],ARGV[4]}
if ARGV[5]~='' then table.insert(values,ARGV[5]);table.insert(values,ARGV[6]) end
redis.call('HSET',KEYS[2],unpack(values))
return 'prepared'
'''
PUBLISH = GUARD+'''
if redis.call('HGET',KEYS[2],'pending')~=ARGV[2] then return redis.error_reply('INTENT_CHANGED') end
local rows=redis.call('XRANGE',KEYS[3],ARGV[3],ARGV[3],'COUNT',1)
if #rows>0 then
 if #rows[1][2]~=2 or rows[1][2][1]~='json' or rows[1][2][2]~=ARGV[4] then
  return redis.error_reply('PUBLICATION_CONFLICT')
 end
 return 'identical'
end
if redis.call('XLEN',KEYS[3])>=tonumber(ARGV[5]) then return redis.error_reply('CAPACITY_EXCEEDED') end
-- Fixed ID allows ambiguous XADD replies to be reconciled by XRANGE, not re-appended.
redis.call('XADD',KEYS[3],ARGV[3],'json',ARGV[4])
return 'published'
'''
FINISH = GUARD+'''
if redis.call('HGET',KEYS[2],'pending')~=ARGV[2] then return redis.error_reply('INTENT_CHANGED') end
local intent=cjson.decode(ARGV[2])
for i=4,#KEYS do
 local o=intent.outputs[i-3]
 local rows=redis.call('XRANGE',KEYS[i],o.id,o.id,'COUNT',1)
 if #rows~=1 or #rows[1][2]~=2 or rows[1][2][1]~='json' or rows[1][2][2]~=o.data then return redis.error_reply('OUTPUT_MISSING') end
end
redis.call('XACK',KEYS[3],ARGV[3],ARGV[4])
-- If this HDEL fails after XACK, pending survives and repair re-verifies/re-ACKs.
redis.call('HDEL',KEYS[2],'pending')
return 'acknowledged'
'''
ACK_DUPLICATE = GUARD+'''
if redis.call('HEXISTS',KEYS[2],'pending')==1 then return redis.error_reply('REPAIR_REQUIRED') end
if redis.call('HGET',KEYS[2],ARGV[2])~=ARGV[3] then return redis.error_reply('PAYLOAD_CONFLICT') end
return redis.call('XACK',KEYS[3],ARGV[4],ARGV[5])
'''
# Producer deduplication is optional: external test producers may send physical duplicates.
# The producer itself uses caller-stable explicit stream IDs, reconciled on retries.
PRODUCE = '''
local rows=redis.call('XRANGE',KEYS[1],ARGV[1],ARGV[1],'COUNT',1)
if #rows>0 then
 if #rows[1][2]~=2 or rows[1][2][1]~='json' or rows[1][2][2]~=ARGV[2] then return redis.error_reply('PAYLOAD_CONFLICT') end
 return ARGV[1]
end
if redis.call('XLEN',KEYS[1])>=tonumber(ARGV[3]) then return redis.error_reply('CAPACITY_EXCEEDED') end
return redis.call('XADD',KEYS[1],ARGV[1],'json',ARGV[2])
'''


class Journal:
    def __init__(self, client, *, namespace, trusted_org, role, consumer, lease_ms=2000,
                 max_events=256, max_vehicles=128, max_stream=256, hook=None,
                 max_state_bytes=None, max_intent_bytes=None):
        require(re.fullmatch(r'[A-Za-z0-9_-]{1,48}',namespace) is not None,
                'INVALID_NAMESPACE','explicit simple namespace required')
        require(role in {'colour','aggregator','plate','aggregator_plate','plate_requests'} and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',consumer) is not None,
                'INVALID_WORKER','explicit worker identity required')
        require(200 <= lease_ms <= 60000 and 1 <= max_events <= 1024
                and 1 <= max_vehicles <= 128 and 1 <= max_stream <= 1024,
                'INVALID_CAPACITY','bounded lease/capacities required')
        from uuid import UUID
        self.prefix=f'{namespace}:{{vehicle-v1.{UUID(str(trusted_org))}}}:'
        self.client,self.role,self.consumer=client,role,consumer
        self.lease_ms,self.max_events,self.max_vehicles,self.max_stream=lease_ms,max_events,max_vehicles,max_stream
        self.lease=self.prefix+role+':lease'
        self.fence=self.prefix+role+':fence'
        self.state=self.prefix+role+':journal'
        self.group={'colour':'colour-v1','aggregator':'aggregator-v1',
                    'plate':'plate-v1','aggregator_plate':'aggregator-plate-v1','plate_requests':'plate-requests-v2'}[role]
        self.plate_profile=role in {'plate','aggregator_plate','plate_requests'}
        if self.plate_profile:
            from .plate_codec import STATE_LIMIT, INTENT_LIMIT, MAX_WIRE
            state_limit,intent_limit=STATE_LIMIT,INTENT_LIMIT
            self.max_output_bytes=MAX_WIRE
        else:
            state_limit,intent_limit=196608,524288
            self.max_output_bytes=65536
        self.max_state_bytes=state_limit if max_state_bytes is None else max_state_bytes
        self.max_intent_bytes=intent_limit if max_intent_bytes is None else max_intent_bytes
        require(type(self.max_state_bytes) is int and 1 <= self.max_state_bytes <= state_limit
                and type(self.max_intent_bytes) is int and 1 <= self.max_intent_bytes <= intent_limit,
                'INVALID_CAPACITY','byte limits may only lower their versioned profile')
        self.owner=consumer+'-'+uuid4().hex
        self.token=None
        self.hook=hook or (lambda phase:None)

    def require_org(self, trusted_org):
        """Canonical partition check; identity binding, not authorization."""
        from uuid import UUID
        require(self.prefix.endswith(f':{{vehicle-v1.{UUID(str(trusted_org))}}}:'),
                'ORG_MISMATCH','configured journal partition differs from trusted org')

    def key(self,suffix):
        require(suffix in {'engine.evidence.colour','engine.lifecycle','results.colour','db1.delivery',
                          'diagnostics.colour','diagnostics.aggregator','engine.evidence.plate','engine.lifecycle.plate',
                          'results.plate','db1.delivery.v1','diagnostics.plate','diagnostics.aggregator_plate',
                          'engine.crops.v2','plate.requests.v2','engine.admissions.v2','engine.executions.v2',
                          'plate.seals.v2','engine.sealed.v2','diagnostics.plate_requests'},'WRONG_STREAM','unapproved stream')
        return self.prefix+suffix

    def call(self,script,keys,*args):
        return self.client.eval(script,len(keys),*keys,*args)

    def acquire(self):
        token=self.call(ACQUIRE,[self.lease,self.fence],self.owner,self.lease_ms)
        if token:
            self.token=token
        return bool(token)

    def renew(self):
        return self.call(RENEW,[self.lease],self.token,self.lease_ms)

    def release(self):
        if self.token:
            self.call(RELEASE,[self.lease],self.token)
            self.token=None

    def read_state(self,key):
        raw=self.client.hget(self.state,'vehicle:'+key)
        require(raw is None or len(raw)<=self.max_state_bytes,'CAPACITY_EXCEEDED','retained state exceeds profile')
        if raw and self.plate_profile:
            from .codec import bounded_json
            return bounded_json(raw,max_bytes=self.max_state_bytes,max_depth=40,
                                max_nodes=500000,max_string=4096)
        return FrozenJSON.of(strict_loads(raw)).value() if raw else None

    def seen(self,key):
        return self.client.hget(self.state,key)

    def repair(self):
        raw=self.client.hget(self.state,'pending')
        if raw is None:
            return False
        self.renew()
        require(len(raw)<=self.max_intent_bytes,'INVALID_INTENT','journal repair byte limit')
        intent=strict_loads(raw) if self.plate_profile else json.loads(raw)
        if self.plate_profile:
            require(set(intent)=={'profile','source','entry_id','outputs'}
                    and intent['profile']=='plate-journal-v1','STATE_VERSION_CONFLICT','retained intent profile differs')
        require(len(raw)<=self.max_intent_bytes and len(intent['outputs'])<=1,'INVALID_INTENT','journal repair limit')
        targets=[]
        for output in intent['outputs']:
            require(len(output['data'].encode('utf-8'))<=self.max_output_bytes,'CAPACITY_EXCEEDED','output exceeds profile')
            if self.plate_profile:
                require(set(output)=={'stream','id','data'},'INVALID_INTENT','closed publication intent required')
            key=self.key(output['stream'])
            targets.append(key)
            self.call(PUBLISH,[self.lease,self.state,key],self.token,raw,output['id'],output['data'],self.max_stream)
            self.hook('published')
        self.hook('before_ack')
        self.call(FINISH,[self.lease,self.state,self.key(intent['source']),*targets],
                  self.token,raw,self.group,intent['entry_id'])
        self.hook('acknowledged')
        return True

    def handoff(self, *, source, entry_id, marker, digest, vehicle=None, state=None, outputs=()):
        require(len(outputs)<=1,'CAPACITY_EXCEEDED','one bounded output per handoff')
        require(all(type(data) is bytes and len(data)<=self.max_output_bytes for _,data in outputs),
                'CAPACITY_EXCEEDED','bounded journal output bytes required')
        revision=int(self.client.hget(self.state,'revision') or 0)
        intent=dict(source=source,entry_id=entry_id.decode() if isinstance(entry_id,bytes) else entry_id,
                    outputs=[dict(stream=stream,id=f'{revision+1}-0',data=data.decode('utf-8')) for stream,data in outputs])
        if self.plate_profile:intent['profile']='plate-journal-v1'
        raw=json.dumps(intent,separators=(',',':'),ensure_ascii=not self.plate_profile).encode()
        data=canonical_json_bytes(state) if state is not None else b''
        require(len(raw)<=self.max_intent_bytes and len(data)<=self.max_state_bytes,'CAPACITY_EXCEEDED','bounded handoff state bytes')
        # CPU work/serialization may have consumed the lease. Renew only this
        # token under GUARD; never reacquire and commit an old owner's result.
        self.renew()
        self.hook('before_prepare')
        outcome=self.call(PREPARE,[self.lease,self.state],self.token,revision,marker,digest,
            'vehicle:'+vehicle if vehicle else '',data,raw,self.max_events,self.max_vehicles)
        if outcome==b'duplicate':
            self.call(ACK_DUPLICATE,[self.lease,self.state,self.key(source)],self.token,marker,digest,
                      self.group,entry_id)
        else:
            self.hook('prepared')
            self.repair()
