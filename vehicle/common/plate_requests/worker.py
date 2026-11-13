"""Fenced v2 metadata service. State/output/waits precede input XACK."""
from hashlib import sha256
from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import Blocked, require
from ..redis_scalar.worker import Worker
from ..redis_scalar.journal import Journal
from ..redis_scalar.codec import vehicle_key
from .codec import STREAMS
from .policy import reduce_event


class RequestWorker(Worker):
    def __init__(self,client,*,codec,namespace,role='plate_requests',consumer,regional_order=('left_ocr','right_ocr'),**options):
        require(role=='plate_requests','WRONG_VERSION','explicit progressive worker role required')
        options.setdefault('lease_ms',60000)
        self.codec,self.client,self.role=codec,client,role
        self.current_kind=None
        self.regional_order=regional_order
        self.journal=Journal(client,namespace=namespace,trusted_org=codec.org,role=role,consumer=consumer,**options)
        self.sources=[STREAMS[k] for k in ('crop','admission','evidence','sealed')]
        self.cursor={s:'0-0' for s in self.sources};self.next_source=0
        self.prefer_pending={s:True for s in self.sources}

    def process(self,source,entry_id,fields):
        j=self.journal;raw=fields.get(b'json',b'')
        m=None
        try:
            require(set(fields)=={b'json'},'INVALID_MESSAGE','one bounded JSON field required')
            m=self.codec.decode(raw)
            self.current_kind=m.kind
            require(STREAMS[m.kind]==source and source in self.sources,'WRONG_STREAM','v2 event on incorrect stream')
            marker='seen:'+m.event_id;digest=sha256(m.canonical).hexdigest();old=j.seen(marker)
            require(old is None or old.decode()==digest,'PAYLOAD_CONFLICT','changed immutable event')
            if old is not None:
                j.handoff(source=source,entry_id=entry_id,marker=marker,digest=digest);return
            key=vehicle_key(m.life)
            state,outputs=reduce_event(self.codec,m,j.read_state(key),regional_order=self.regional_order)
            j.handoff(source=source,entry_id=entry_id,marker=marker,digest=digest,vehicle=key,state=state,outputs=outputs)
        except Blocked as error:
            if error.code=='CAPACITY_EXCEEDED':raise
            diagnostic=canonical_json_bytes(dict(schema_version='vehicle-redis-diagnostic-v1',org_id=str(self.codec.org),
                source=source,code=error.code,input_sha256=sha256(raw).hexdigest()))
            key=vehicle_key(m.life) if m is not None else None
            state=j.read_state(key) if key is not None else None
            if state is not None and error.code not in {'WRONG_STREAM','INVALID_MESSAGE','STATE_VERSION_CONFLICT'}:
                # Retain the original terminal unchanged, or block an unfinished
                # lifetime. Diagnostics alone must not discard conflicting input
                # and allow a later apparently clean terminal to escape.
                if state.get('status')!='terminal':state.update(status='blocked',reason='conflicting_evidence')
                state['conflict_sha256']=sha256(raw).hexdigest()
            else:key=state=None
            j.handoff(source=source,entry_id=entry_id,marker='rejected:'+source+':'+entry_id.decode(),
                digest=sha256(raw).hexdigest(),vehicle=key,state=state,outputs=[('diagnostics.plate_requests',diagnostic)])
