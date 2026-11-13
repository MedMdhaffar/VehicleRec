"""Immutable v1 scalar tee, private publication and bounded video orchestration."""
from dataclasses import dataclass
import time
from ..db1_rows.core import require
from ..aggregator import Aggregator,Branch
from ..colour_metadata import ColourMetadata
from ..plate_metadata import PlateMetadata
from ..vehicle_observation import RunEnd
from ..frame_receipt import ClockEpochInvalid
from ..redis_scalar.codec import pack_life,pack_attempt,pack_receipt
from ..redis_scalar.journal import PRODUCE
from ..redis_scalar.plate_codec import pack_transcript


@dataclass(frozen=True)
class VideoDeliveryPlan:
    sid: int
    stream_id: str
    closed: bytes
    colour: bytes
    plate: bytes
    plate_batch_id: str
    transcript: object
    reference: object
    original: object


def reference(codec,closed,transcript):
    life=closed.owner.lifetime
    require(closed.blocker is None,'BLOCKED_ENGINE_EVIDENCE','failed original Engine evidence cannot finalize')
    plate=PlateMetadata().evaluate(life,transcript)
    require(plate.result is not None,'EXECUTION_WAITING','enabled unfinished Plate has no final bundle')
    return Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join_with_plate(
        closed.owner,closed.classes,(Branch('colour','enabled',ColourMetadata().finalize(life,closed.measurements)),
        Branch('plate','enabled',plate.result),Branch('manufacturer','unsupported'))).delivery


class V1Publisher:
    """One serial host, one fresh private namespace, one in-memory ownership scope.

    Successful preparation admits a FIFO entry and reserves a transport ID.
    Later entries return False until the head confirms all three inputs. IDs
    never reset while this owner exists; this is not a durable Engine outbox or
    coordination with another publisher (including reuse after restart).
    """
    def __init__(self,client,journal,codec,engine):
        self.client,self.journal,self.codec,self.engine=client,journal,codec,engine
        self.plans={};self.confirmed=set();self.attempts=0;self.wire_calls=0;self.reference_model_calls=0
        self.retained_bytes=0;self.next_transport_id=1;self.pending=[];self.progress={}

    def prepare(self,closed):
        sid=closed.owner.service_track_id;life=closed.owner.lifetime
        require(self.codec.org==life.context.org_id and self.journal.prefix.endswith(f':{{vehicle-v1.{self.codec.org}}}:'),
                'PARENT_MISMATCH','lifecycle/Colour/Plate publisher tenant differs')
        if sid in self.plans:
            require(self.plans[sid].original==closed and self.plans[sid].transcript.identity.context==life.context and self.engine.plate.lives[sid].life==life,
                    'PAYLOAD_CONFLICT','prepared closed lifetime changed')
            require(self.engine.plate.lives[sid].adapter.snapshot()==self.plans[sid].transcript,
                    'PAYLOAD_CONFLICT','prepared Plate evidence changed')
            return self.plans[sid]
        require(len(self.plans)<self.engine.limits.max_lifetimes,
                'CAPACITY_EXCEEDED','retained publication lifetime bound; nothing discarded')
        transcript=self.engine.plate.complete(closed)
        if transcript is None:return None
        before=dict(self.engine.audit.calls)
        expected=reference(self.codec,closed,transcript)
        require(before==self.engine.audit.calls,'REFERENCE_INFERENCE','reference must not invoke a model')
        batch_id=self.codec.event_id(life,'plate.batch.identity')
        raw=self.codec.v0.encode('colour.batch',self.codec.v0.event_id(life,'colour.batch'),dict(
            life=pack_life(life),complete=True,expected_attempt_ids=[a.attempt_id for a in closed.measurements],
            measurements=[pack_attempt(a) for a in closed.measurements]))
        closure=self.codec.make('vehicle.closed.plate',dict(life=pack_life(life),delivery_version='natural-db1-bundle-v1',
            colour_batch_id=self.codec.v0.decode(raw).event_id,plate_batch_id=batch_id,
            classes=[dict(receipt=pack_receipt(c.receipt),label=c.label,confidence=c.confidence) for c in closed.classes],
            plate='enabled',manufacturer='unsupported'))
        adapter=self.engine.plate.lives[sid].adapter
        # Preflight before sealing the adapter or consuming a transport ID. A
        # rejected admission must remain retryable after other plans progress.
        plate=self.codec.make('plate.batch',dict(life=pack_life(life),batch_id=batch_id,revision=0,
                                               transcript=pack_transcript(transcript)))
        size=len(raw)+len(closure)+len(plate)+len(expected.payload.canonical)
        require(self.retained_bytes+size<=self.engine.limits.max_scalar_bytes,'CAPACITY_EXCEEDED','unpublished scalar bytes exceeded; no truncation')
        stream_id=f'{self.next_transport_id}-0'
        packet=adapter.prepare_publication(self.codec,batch_id=batch_id,stream_id=stream_id)
        require(packet.payload==plate,'PAYLOAD_CONFLICT','Plate evidence changed during admission')
        plan=VideoDeliveryPlan(sid,stream_id,closure,raw,packet.payload,batch_id,transcript,expected,closed)
        self.plans[sid]=plan;self.retained_bytes+=size
        self.next_transport_id+=1;self.pending.append(sid);self.progress[sid]=set()
        return plan

    def publish(self,closed):
        self.attempts+=1;plan=self.prepare(closed)
        if plan is None:return False
        if plan.sid in self.confirmed:
            return not self.engine.plate.lives[plan.sid].adapter.cleanup()
        if self.pending[0]!=plan.sid:return False # explicit FIFO backpressure; no wire mutation
        for stream,raw in [('engine.lifecycle.plate',plan.closed),('engine.evidence.colour',plan.colour)]:
            if stream in self.progress[plan.sid]:continue
            self.wire_calls+=1
            reply=self.client.eval(PRODUCE,1,self.journal.key(stream),plan.stream_id,raw,self.journal.max_stream)
            require(reply in (plan.stream_id,plan.stream_id.encode()),'PUBLICATION_UNCONFIRMED','original bytes retained until acknowledged')
            self.progress[plan.sid].add(stream)
        adapter=self.engine.plate.lives[plan.sid].adapter
        self.wire_calls+=int(not adapter.confirmed)
        adapter.publish(self.client,self.journal,self.codec,batch_id=plan.plate_batch_id,stream_id=plan.stream_id)
        self.confirmed.add(plan.sid);self.pending.pop(0)
        return not adapter.cleanup()


def drive_video(source,engine,publisher,*,stop_requested=lambda:False,monotonic=time.monotonic,on_frame=lambda p:None):
    limits=engine.limits;start=monotonic();reason='frame_limit'
    def offer():
        if not engine.pending:return
        deadline=monotonic()+limits.max_seconds
        while engine.pending:
            for sid,closed in tuple(engine.pending.items()):
                if publisher.publish(closed):engine.acknowledge(sid)
            if engine.pending:
                # Scan every ready lifetime so a prepared FIFO head can run
                # even when Engine's pending order differs. Bound waiting,
                # including instant False returns; retain evidence on timeout.
                require(monotonic()<deadline,'PUBLICATION_WAITING','bounded handoff wait expired; pending evidence retained')
                time.sleep(.01)
    try:
        with source:
            for _ in range(limits.max_frames):
                if stop_requested():reason='controlled_stop';break
                if monotonic()-start>=limits.max_seconds:reason='time_limit';break
                packet=source.read()
                if packet is None:reason='eof';break
                engine.process(packet);on_frame(packet);del packet
                offer() # source/model work never overlaps pending recognition
        engine.finish(RunEnd.END_OF_INPUT if reason=='eof' else RunEnd.SERVICE_STOP,trigger=reason)
        offer()
        return dict(reason=reason,decoded_frames=source.decoded,elapsed_seconds=monotonic()-start)
    except BaseException as error:
        engine.finish(RunEnd.CLOCK_DISCONTINUITY if isinstance(error,ClockEpochInvalid) else RunEnd.PROCESS_ERROR,trigger='host_failure')
        raise
    finally:
        source.close();engine.close()
