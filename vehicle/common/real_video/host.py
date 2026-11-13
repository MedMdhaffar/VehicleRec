"""Single-pass orchestration and confirmed publication; no model or decoder here."""
import time
from ..aggregator import Aggregator,Branch
from ..colour_metadata import ColourMetadata
from ..frame_receipt import ClockEpochInvalid
from ..vehicle_observation import RunEnd
from ..db1_rows.core import require
from ..redis_scalar.codec import pack_life,pack_receipt,pack_attempt
from ..redis_scalar.journal import PRODUCE


def scalar_body(evidence):
    return dict(blocker=evidence.blocker,life=pack_life(evidence.owner.lifetime),
        classes=[dict(receipt=pack_receipt(s.receipt),label=s.label,confidence=s.confidence) for s in evidence.classes],
        measurements=[pack_attempt(m) for m in evidence.measurements])


def reference_bundle(codec,evidence):
    require(evidence.blocker is None,'BLOCKED_ENGINE_EVIDENCE','blocked Engine evidence cannot produce a final bundle')
    colour=ColourMetadata().finalize(evidence.owner.lifetime,evidence.measurements)
    return Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join(
        evidence.owner,evidence.classes,(Branch('colour','enabled',colour),Branch('plate','disabled'),
                                        Branch('manufacturer','unsupported'))).delivery


class Publisher:
    def __init__(self,client,journal,codec):
        self.client,self.journal,self.codec=client,journal,codec
        self.plans={}
        self.sequence=0

    def prepare(self,evidence):
        require(evidence.blocker is None,'BLOCKED_ENGINE_EVIDENCE','blocked Engine evidence cannot be published')
        life=evidence.owner.lifetime
        key=self.codec.event_id(life,'vehicle.closed')
        body=scalar_body(evidence)
        batch_id=self.codec.event_id(life,'colour.batch')
        batch=self.codec.encode('colour.batch',batch_id,dict(life=body['life'],complete=True,
            expected_attempt_ids=[a['attempt_id'] for a in body['measurements']],measurements=body['measurements']))
        closed=self.codec.encode('vehicle.closed',key,dict(life=body['life'],colour_batch_id=batch_id,
            classes=body['classes'],plate='disabled',manufacturer='unsupported'))
        if key in self.plans:
            old=self.plans[key]
            require(old['closed']==closed and old['batch']==batch,'PAYLOAD_CONFLICT','retained closed evidence changed')
            return old
        self.sequence+=1
        plan=dict(id=f'{self.sequence}-0',closed=closed,batch=batch)
        self.plans[key]=plan
        return plan

    def publish(self,evidence):
        plan=self.prepare(evidence)
        for stream,name in [('engine.lifecycle','closed'),('engine.evidence.colour','batch')]:
            self.client.eval(PRODUCE,1,self.journal.key(stream),plan['id'],plan[name],self.journal.max_stream)
        return plan


def run_capture(source,engine,sink,*,max_frames,max_seconds,stop_requested=lambda:False,
                monotonic=time.monotonic,on_frame=lambda packet:None):
    require(type(max_frames) is int and 1<=max_frames<=20 and 0<max_seconds<=300,
            'INVALID_WINDOW','explicit smoke window: 1..20 frames and <=300 seconds')
    start=monotonic()
    reason='frame_limit'
    def offer():
        for sid,evidence in tuple(engine.pending.items()):
            sink(evidence)  # Must return only after publication is confirmed.
            engine.acknowledge(sid)
    try:
        with source:
            for _ in range(max_frames):
                if stop_requested():reason='controlled_stop';break
                if monotonic()-start>=max_seconds:reason='time_limit';break
                packet=source.read()
                if packet is None:reason='eof';break
                engine.process(packet)
                on_frame(packet)
                del packet
                offer()
        engine.finish(RunEnd.END_OF_INPUT if reason=='eof' else RunEnd.SERVICE_STOP,trigger=reason)
        offer()
        return dict(reason=reason,decoded_frames=source.decoded,elapsed_seconds=monotonic()-start)
    except BaseException as error:
        # Never drain/ACK closed evidence on sink failure. Preserve all unsent
        # metadata, including active lifetimes closed on host failure.
        engine.finish(RunEnd.CLOCK_DISCONTINUITY if isinstance(error,ClockEpochInvalid) else RunEnd.PROCESS_ERROR,
                      trigger='host_failure')
        raise
    finally:
        source.close()
        engine.close()
