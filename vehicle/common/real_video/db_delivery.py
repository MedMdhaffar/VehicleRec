"""Bounded physical-delivery completion after correlated DB commit and Redis ACK.

This ledger belongs to the live runner. Database history remains the authority
for transaction retry; it is never replaced by these in-memory observations.
"""
from collections import Counter
from hashlib import sha256
import re,time
from ..db1_adapter import DeliveryResult,DeliveryConflict,DatabaseDeliveryError
from ..db1_rows.core import require

# One read-only atomic observation: original physical bytes, group cursor, PEL.
# A group's empty PEL is insufficient: the target might not have been read yet.
OBSERVE_ACK = '''
local rows=redis.call('XRANGE',KEYS[1],ARGV[2],ARGV[2],'COUNT',1)
if #rows~=1 or #rows[1][2]~=2 or rows[1][2][1]~='json' or rows[1][2][2]~=ARGV[3] then
 return {-1}
end
local cursor=false
for _,g in ipairs(redis.call('XINFO','GROUPS',KEYS[1])) do
 local name=false;local position=false
 for i=1,#g,2 do
  if g[i]=='name' then name=g[i+1] end
  if g[i]=='last-delivered-id' then position=g[i+1] end
 end
 if name==ARGV[1] then cursor=position end
end
if not cursor then return {0} end
local pending=redis.call('XPENDING',KEYS[1],ARGV[1],ARGV[2],ARGV[2],1)
return {1,cursor,#pending}
'''


def stream_id(value):
    raw=value.encode() if isinstance(value,str) else value
    require(isinstance(raw,bytes) and re.fullmatch(rb'(0|[1-9][0-9]*)-(0|[1-9][0-9]*)',raw) is not None,
            'INVALID_DELIVERY_ID','canonical physical stream identity required')
    pair=tuple(int(v) for v in raw.split(b'-'))
    require(all(v<=2**64-1 for v in pair),'INVALID_DELIVERY_ID','bounded Redis stream identity required')
    return raw,pair


class TargetDeliveries:
    def __init__(self,db,rows):
        self.db=db;self.targets={};self.commits={};self.completed=set();self.attempts=Counter();self.counts=Counter()
        self.observed={};self.ack_replied=set()
        require(len(rows)<=256,'CAPACITY_EXCEEDED','bounded target set required')
        size=0
        for entry,fields in rows:
            identity,_=stream_id(entry)
            require(set(fields)=={b'json'} and isinstance(fields[b'json'],bytes),'INVALID_MESSAGE','exact scalar target bytes required')
            raw=fields[b'json']
            require(identity not in self.targets or self.targets[identity]==raw,'PAYLOAD_CONFLICT','physical target payload changed')
            if identity not in self.targets:size+=len(raw)
            require(size<=8*1024*1024,'CAPACITY_EXCEEDED','bounded retained delivery bytes required')
            self.targets[identity]=raw

    @property
    def done(self):return len(self.completed)==len(self.targets)

    def before_process(self,entry,raw):
        entry,_=stream_id(entry)
        require(entry in self.targets and self.targets[entry]==raw,'DELIVERY_BINDING','input differs from exact target entry/payload')
        self.counts['delivery_attempts']+=1
        self.counts['retry_attempts']+=self.attempts[entry]>0
        self.attempts[entry]+=1

    def capture(self):
        progress=self.db.last_delivery
        if progress is None:return
        entry,_=stream_id(progress.entry_id)
        require(entry in self.targets and self.targets[entry]==progress.raw,'DELIVERY_BINDING','commit progress differs from exact target input')
        if progress.commit is None:return
        result=progress.commit
        require(isinstance(result,DeliveryResult) and result.disposition in {'new','identical'},'COMMIT_UNCONFIRMED','confirmed database result required')
        old=self.commits.get(entry)
        require(old is None or (old.acceptance_sha256,old.fact_count,old.omission_count)==
                (result.acceptance_sha256,result.fact_count,result.omission_count),
                'PAYLOAD_CONFLICT','confirmed receipt changed for retained target')
        self.commits[entry]=result
        self.counts[result.disposition]+=1;self.counts['transactions_confirmed']+=1
        if progress.ack_reply is not None:
            self.counts['ack_callbacks_completed']+=1;self.ack_replied.add(entry)

    def reconcile(self):
        key=self.db.journal.key('db1.delivery.v1')
        for entry,raw in self.targets.items():
            if entry in self.completed:continue
            # No database proof for this exact physical input: do not infer it
            # from another input's last_commit or from Redis state alone.
            if entry not in self.commits:continue
            answer=self.db.client.eval(OBSERVE_ACK,1,key,self.db.group,entry,raw)
            require(answer and answer[0] in {-1,0,1},'ACK_UNCONFIRMED','invalid ACK observation')
            require(answer[0]!=-1,'PAYLOAD_CONFLICT','target stream entry missing or changed')
            if answer[0]==0:continue
            _,cursor=stream_id(answer[1]);_,target=stream_id(entry)
            require(answer[2] in (0,1),'ACK_UNCONFIRMED','invalid exact PEL observation')
            state='unread' if cursor<target else 'pending' if answer[2] else 'acknowledged'
            self.observed[entry]=state
            if state=='acknowledged':
                self.completed.add(entry)
                self.counts['acknowledgements_reconciled']+=entry not in self.ack_replied

    def turn(self):
        from redis.exceptions import ConnectionError,TimeoutError
        try:self.reconcile()
        except (ConnectionError,TimeoutError):self.counts['redis_errors']+=1;return
        if self.done:return
        self.counts['step_calls']+=1
        try:self.db.step(before_process=self.before_process)
        except DeliveryConflict:self.counts['conflicts']+=1;raise
        except DatabaseDeliveryError as error:
            self.counts['unknown_commits']+=error.commit_outcome=='unknown'
            self.counts['not_committed']+=error.commit_outcome=='not_committed'
        except (ConnectionError,TimeoutError):self.counts['redis_errors']+=1
        finally:self.capture()
        try:self.reconcile()
        except (ConnectionError,TimeoutError):self.counts['redis_errors']+=1

    def status(self):
        c=self.db.consumer
        return dict(org_id=str(c.tenant.org_id),run_id=c.run,camera_id=c.camera,
            **{key:self.counts[key] for key in ['new','identical','transactions_confirmed','unknown_commits','not_committed',
                'conflicts','step_calls','delivery_attempts','retry_attempts','redis_errors','ack_callbacks_completed','acknowledgements_reconciled']},
            targets=len(self.targets),completed=len(self.completed),waiting=len(self.targets)-len(self.completed),
            deliveries=[dict(entry_id=entry.decode(),payload_sha256=sha256(raw).hexdigest(),
                acceptance_sha256=self.commits[entry].acceptance_sha256 if entry in self.commits else None,
                committed=entry in self.commits,acknowledgement='confirmed' if entry in self.completed else self.observed.get(entry,'unconfirmed'))
                for entry,raw in self.targets.items()])


def deliver_targets(db,rows,*,deadline,on_status=lambda s:None,runtime_check=lambda:None):
    ledger=TargetDeliveries(db,rows)
    try:
        while not ledger.done:
            require(time.monotonic()<deadline,'DB_WAITING','bounded DB delivery wait; exact targets remain unresolved')
            runtime_check();ledger.turn();on_status(ledger.status())
            if not ledger.done:time.sleep(.025)
        return ledger.status()
    finally:on_status(ledger.status())
