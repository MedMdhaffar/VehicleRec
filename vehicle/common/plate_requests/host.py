"""Bounded FIFO publication and fair live Engine request pumping for MP4 v2."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4
from ..db1_rows.core import require, Blocked
from ..redis_scalar.codec import pack_life, pack_attempt, pack_receipt, vehicle_key
from ..redis_scalar.journal import PRODUCE
from ..redis_scalar.plate_codec import unpack_transcript
from ..real_video.plate_host import reference
from .codec import STREAMS
from .engine import EngineSession


class OrderedPublisher:
    """FIFO byte helper; caller must validate tenant binding before construction.

    V2Publisher performs that check before any Redis access or queue retention.
    """
    def __init__(self,client,journal,*,max_events=256,max_bytes=8*1024*1024):
        self.client,self.journal=client,journal
        self.max_events,self.max_bytes=max_events,max_bytes
        self.entries={};self.pending=[];self.confirmed=set();self.bytes=0;self.calls=0

    def add(self,identity,stream,raw):
        old=self.entries.get(identity)
        if old is not None:
            require(old[1:]==(stream,raw),'PAYLOAD_CONFLICT','publication identity changed');return
        require(len(self.entries)<self.max_events and self.bytes+len(raw)<=self.max_bytes,
                'CAPACITY_EXCEEDED','retained Engine publication budget full; no eviction')
        self.entries[identity]=(f'{len(self.entries)+1}-0',stream,raw)
        self.pending.append(identity);self.bytes+=len(raw)

    def flush(self):
        # One bounded append per turn; an ambiguous reply retains the FIFO head.
        if not self.pending:return False
        identity=self.pending[0];sid,stream,raw=self.entries[identity];self.calls+=1
        reply=self.client.eval(PRODUCE,1,self.journal.key(stream),sid,raw,self.journal.max_stream)
        require(reply in (sid,sid.encode()),'PUBLICATION_UNCONFIRMED','retain original bytes until confirmed')
        self.confirmed.add(identity);self.pending.pop(0);return True


@dataclass
class DeliveryPlan:
    original: object
    session: EngineSession
    closed: bytes
    colour: bytes
    transcript: object=None
    reference: object=None


class V2Publisher:
    """Engine owner thread. Metadata workers remain independently restartable.

    Request XACK follows confirmed admission publication; in-process ledger and
    original resources remain until the completion handshake. This is explicitly
    not durable inference recovery after Engine death.
    """
    def __init__(self,client,journal,codec,engine):
        journal.require_org(codec.org)  # before XGROUP, Engine access or queue allocation
        self.client,self.journal,self.codec,self.engine=client,journal,codec,engine
        self.trusted_context=engine.colour.pipeline.observation_clock.context
        require(self.trusted_context.org_id==codec.org,'ORG_MISMATCH','configured Engine org differs')
        self.session=str(uuid4())
        from .ownership import claim_engine
        self.owner_key,self.owner_bytes=claim_engine(client,journal,codec,self.trusted_context,self.session)
        self.plans={};self.confirmed=set();self.attempts=0
        self.writer=OrderedPublisher(client,journal,max_bytes=engine.limits.max_scalar_bytes)
        self.retained_bytes=0;self.reference_model_calls=0;self.input_progress={};self.next_source=0;self.next_vehicle=0
        self.sources=[STREAMS['request'],STREAMS['seal']];self.cursors={s:'0-0' for s in self.sources}
        self.prefer_pending={s:True for s in self.sources};self.group='engine-requests-v2';self.consumer='engine-'+uuid4().hex
        from redis.exceptions import ResponseError
        for stream in self.sources:
            try:client.xgroup_create(journal.key(stream),self.group,id='0-0',mkstream=True)
            except ResponseError as error:
                if not str(error).startswith('BUSYGROUP'):raise

    @property
    def wire_calls(self):return self.writer.calls

    def prepare(self,closed):
        require(closed.owner.lifetime.context==self.trusted_context,'PARENT_MISMATCH','Engine lifetime differs from configured run/camera')
        self.journal.require_org(self.codec.org)
        sid=closed.owner.service_track_id
        if sid in self.plans:
            require(self.plans[sid].original==closed,'PAYLOAD_CONFLICT','original closed lifetime changed')
            return self.plans[sid]
        require(len(self.plans)<self.engine.limits.max_lifetimes,'CAPACITY_EXCEEDED','bounded retained lifetimes')
        life=closed.owner.lifetime;adapter=self.engine.plate.prepare(closed,progressive=True)
        v=self.engine.plate.lives[sid]
        selected=sorted(v.best.values(),key=lambda value:(v.candidates[value[0]].receipt.frame_index,v.candidates[value[0]].segment_id))
        resources={c.binding.candidate.candidate_id:c for _,c in selected}
        offers=[dict(attempt_id=aid,ordinal=i,candidate_id=c.binding.candidate.candidate_id)
            for i,(aid,(_,c)) in enumerate(zip(adapter._expected,selected),1)]
        batch_id=self.codec.v1.event_id(life,'plate.batch.identity')
        session=EngineSession(self.codec,adapter,resources,offers,session=self.session,batch_id=batch_id,
            bindings=dict(whole_ocr='whole',marker='marker',left_ocr='left',right_ocr='right'))
        v0=self.codec.v1.v0
        colour=v0.encode('colour.batch',v0.event_id(life,'colour.batch'),dict(life=pack_life(life),complete=True,
            expected_attempt_ids=[a.attempt_id for a in closed.measurements],measurements=[pack_attempt(a) for a in closed.measurements]))
        closure=self.codec.v1.make('vehicle.closed.plate',dict(life=pack_life(life),delivery_version='natural-db1-bundle-v1',
            colour_batch_id=v0.decode(colour).event_id,plate_batch_id=batch_id,
            classes=[dict(receipt=pack_receipt(c.receipt),label=c.label,confidence=c.confidence) for c in closed.classes],
            plate='enabled',manufacturer='unsupported'))
        # Reserve an entire initial publication batch before mutating the FIFO.
        needed=len(closure)+len(colour)+len(session.raw)
        require(self.writer.bytes+needed<=self.writer.max_bytes and len(self.writer.entries)+3<=self.writer.max_events,
                'CAPACITY_EXCEEDED','initial scalar batch cannot fit; no partial publication')
        plan=DeliveryPlan(closed,session,closure,colour)
        for key,stream,raw in [(f'closed-{sid}','engine.lifecycle.plate',closure),
                (f'colour-{sid}','engine.evidence.colour',colour),('crop-'+str(sid),STREAMS['crop'],session.raw)]:self.writer.add(key,stream,raw)
        self.plans[sid]=plan
        return plan

    def _message(self,stream,entry,fields):
        require(set(fields)=={b'json'},'INVALID_MESSAGE','closed request fields required')
        input_key=(stream,entry);input_digest=sha256(fields[b'json']).hexdigest()
        require(input_key not in self.input_progress or self.input_progress[input_key]==input_digest,
                'PAYLOAD_CONFLICT','physical input changed after acceptance')
        require(input_key in self.input_progress or len(self.input_progress)<128,
                'CAPACITY_EXCEEDED','bounded live input ledger full; do not ACK')
        m=self.codec.decode(fields[b'json'])
        require(STREAMS[m.kind]==stream and stream in self.sources,'WRONG_STREAM','request/seal on wrong stream')
        sid=m.life.service_track_id
        require(sid in self.plans,'WAITING_DEPENDENCY','request awaits retained Engine lifetime')
        session=self.plans[sid].session
        if m.kind=='request':
            raw=session.admission(m);rid=m.body['request_id'];key='admission-'+rid
            self.writer.add(key,STREAMS['admission'],raw)
            if key not in self.writer.confirmed:return
            if rid in session.accepted:session.allow_execution(rid)
        else:
            raw=session.finish_generation(m)
            if raw is None:return
            key='sealed-'+str(sid);self.writer.add(key,STREAMS['sealed'],raw)
            if key not in self.writer.confirmed:return
        self.client.xack(self.journal.key(stream),self.group,entry)
        self.input_progress[input_key]=input_digest

    def reconnect(self):
        """Only this still-live owner may resume; no namespace/group reset."""
        self.client.connection_pool.disconnect()
        self.client.ping()
        self.verify_owner()
        return self.session

    def verify_owner(self):
        require(self.client.get(self.owner_key)==self.owner_bytes,'ENGINE_SESSION_LOST',
                'original Redis Engine ownership missing/changed; stop without replaying native work')

    def pump(self):
        self.verify_owner()
        progress=self.writer.flush()
        # Fair source and PEL/new alternation; no blocking read or nested drain.
        for offset in range(len(self.sources)):
            stream=self.sources[(self.next_source+offset)%len(self.sources)]
            rows=[]
            for pending in (self.prefer_pending[stream],not self.prefer_pending[stream]):
                if pending:
                    claimed=self.client.xautoclaim(self.journal.key(stream),self.group,self.consumer,0,
                        start_id=self.cursors[stream],count=1)
                    self.cursors[stream]=claimed[0];rows=claimed[1]
                else:
                    reply=self.client.xreadgroup(self.group,self.consumer,{self.journal.key(stream):'>'},count=1)
                    rows=reply[0][1] if reply else []
                if rows:
                    self.prefer_pending[stream]=not pending
                    self.next_source=(self.sources.index(stream)+1)%len(self.sources)
                    try:self._message(stream,*rows[0])
                    except Blocked as error:
                        if error.code!='WAITING_DEPENDENCY':raise
                    progress=True;break
            if rows:break
        plans=list(self.plans.values())
        if plans:
            rotated=plans[self.next_vehicle:]+plans[:self.next_vehicle]
            self.next_vehicle=(self.next_vehicle+1)%len(plans)
            for plan in rotated:
                progress=plan.session.step() or progress
                plan.session.collect()
                # Collection and FIFO reservation are separate failure points.
                # Replay every retained scalar intent so capacity/serialization
                # failures cannot strand evidence already collected from native
                # execution. add() reconciles exact bytes without re-appending.
                for body in plan.session.evidence.values():
                    self.writer.add('evidence-'+body['request']['request_id'],STREAMS['evidence'],self.codec.make('evidence',body))
        self.retained_bytes=self.writer.bytes
        return progress

    def publish(self,closed):
        self.attempts+=1;plan=self.prepare(closed);self.pump()
        sid=closed.owner.service_track_id
        if sid in self.confirmed:return not plan.session.adapter.cleanup()
        if plan.session.sealed is None or 'sealed-'+str(sid) not in self.writer.confirmed:return False
        from .completion import observe_completion
        terminal=observe_completion(self.client,self.journal,self.codec,plan.session,
            entry_id=self.writer.entries['sealed-'+str(sid)][0])
        if terminal is None:return False
        plan.session.complete(terminal)
        plan.transcript=unpack_transcript(self.codec.v1.decode(terminal).body['transcript'],closed.owner.lifetime)
        before=dict(self.engine.audit.calls)
        plan.reference=reference(self.codec.v1,closed,plan.transcript)
        require(before==self.engine.audit.calls,'REFERENCE_INFERENCE','reference cannot run models')
        self.confirmed.add(sid)
        return not plan.session.adapter.cleanup()
