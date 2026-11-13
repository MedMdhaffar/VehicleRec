"""Opt-in crop smoke: real Engine Plate calls, injected parent/Colour, private Redis."""
from dataclasses import asdict
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from .db1_rows.core import require
from .db1_context import CameraRegistration,ModelRegistration,ResolutionInfo,RunContext
from .frame_receipt import FrameReceipt
from .vehicle_observation import VehicleLifetime,VehicleClosure
from .vehicle_frame import VehicleBoxObservation
from .observation_evidence import VehicleIdentity,RecognitionIdentity
from .plate_metadata import PlateCandidate,PlateMetadata
from .colour_metadata import ColourMetadata,ColourMeasurement
from .aggregator import Aggregator,Branch,ClassSample
from .engine_plate import CropBinding,CropPlan,OperationPlan,PlateExecutionAdapter
from .engine_plate_local import artifact,LocalPlateBinding
from .redis_scalar.codec import pack_life,pack_receipt,pack_attempt
from .redis_scalar.journal import PRODUCE,Journal
from contracts.db1.reference import canonical_json_bytes


class EngineCrop:
    def __init__(self,binding,pixels):
        self._binding=binding;self._pixels=bytes(pixels);self.released=False;self.release_calls=0
    @property
    def binding(self):return self._binding
    def read(self):
        require(not self.released,'CROP_RELEASED','crop allocation was released')
        return self._pixels
    def release(self):
        if not self.released:self._pixels=None;self.released=True;self.release_calls+=1


def evidence(codec,context,path,index):
    import cv2
    import numpy as np
    path=Path(path).resolve(strict=True);encoded=path.read_bytes()
    require(len(encoded)<=2*1024*1024,'CAPACITY_EXCEEDED','bounded encoded local crop')
    image=cv2.imdecode(np.frombuffer(encoded,dtype=np.uint8),cv2.IMREAD_COLOR)
    # One paired host receipt after successful crop decode; no video PTS exists.
    utc=datetime.now(timezone.utc);monotonic=time.monotonic()
    require(image is not None and image.ndim==3 and image.shape[2]==3,'INVALID_CROP','BGR8 crop required')
    require(image.nbytes<=8*1024*1024,'CAPACITY_EXCEEDED','bounded admitted BGR crop')
    h,w=image.shape[:2];pixels=image.tobytes();receipt=FrameReceipt(index,utc,monotonic,None)
    life=VehicleLifetime(context,index,0,index,1,receipt,receipt,1,VehicleClosure.END_OF_INPUT)
    identity=VehicleIdentity.from_lifetime(life)
    candidate=PlateCandidate(identity,'local-crop-'+str(index),1,index,receipt,'associated',True,
        'local-crop-sha256:'+sha256(encoded).hexdigest(),sha256(pixels).hexdigest(),(0,0,w,h),(w,h),None,None,
        'unmeasured-quality-crop-smoke-v1')
    crop=EngineCrop(CropBinding(candidate,'private-crop-'+str(index),len(pixels)),pixels)
    measurements=(ColourMeasurement(identity,'injected-colour-'+str(index),receipt,'red',.8,.9,
        RecognitionIdentity('injected-colour-crop-smoke-v1',1,'classify'),'injected-colour-quality-smoke-v1'),)
    classes=(ClassSample(identity,receipt,'car',.8),)
    return life,crop,measurements,classes,dict(path=str(path),file_sha256=sha256(encoded).hexdigest(),
        file_bytes=len(encoded),pixels_sha256=sha256(pixels).hexdigest(),shape=(h,w,3),receipt=pack_receipt(receipt))


def reference(codec,life,measurements,classes,transcript):
    plate=PlateMetadata().evaluate(life,transcript)
    require(plate.result is not None,'EXECUTION_WAITING','unfinished scalar evidence has no reference delivery')
    return Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join_with_plate(
        VehicleBoxObservation(life,(),'unknown',0.),classes,(
            Branch('colour','enabled',ColourMetadata().finalize(life,measurements)),
            Branch('plate','enabled',plate.result),Branch('manufacturer','unsupported'))).delivery


def require_real_calls(summary):
    count=len(summary['receipts'])
    require(count>0 and summary['invocations']['ocr'].get('whole_ocr',0)==count
        and summary['invocations']['detect'].get('marker',0)==count,
        'NATIVE_RUNTIME_BLOCKED','initialization/no-start evidence is not a real recognition smoke')


def run(args):
    # Existing isolated harness owns/reaps only its native child and workers.
    from vehicle.common.tests.test_engine_plate_redis_integration import EnginePlateRedisIntegrationTests as Harness
    from vehicle.common.tests.test_redis_scalar_integration import eventually
    from vehicle.common.redis_scalar.worker import load_plate_codec
    require(1<=len(args.crops)<=2 and 0<args.admission_seconds<=300,'INVALID_WINDOW','one/two crops and bounded admission window required')
    from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
    require(args.org==ORG and args.identity_namespace==IDENTITY_NAMESPACE,'INVALID_SMOKE_ORG','existing private harness uses its declared fixture org')
    codec=load_plate_codec(args.org,args.identity_namespace)
    now=datetime.now(timezone.utc)
    context=RunContext(args.org,CameraRegistration(args.org,'injected-crop-parent',None,'Africa/Tunis',now,None),
        args.run_id,'file',now,'local-plate-crop-smoke-v1',
        ResolutionInfo(None,None,'vehicle',ModelRegistration(None,'injected-parent-context',1,'detect'),None,
            'INJECTED parent/class/association/Colour for real Plate crop smoke',True),'development')
    identities={kind:artifact(kind,path) for kind,path in [('ocr',args.ocr_dir),('detect',args.marker)]}
    bindings={kind:LocalPlateBinding(identity) for kind,identity in identities.items()}
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    manifest={kind:dict(material=identity.material(),model=identity.model.policy(),files=[asdict(f) for f in identity.files]) for kind,identity in identities.items()}
    (output/'artifact-bindings.json').write_text(json.dumps(manifest,indent=2)+'\n')
    harness=None;adapters=[];receipts=[];start=time.monotonic();attempts=0;reason='crop_limit';failure=None
    server_started=False
    try:
        Harness.setUpClass();server_started=True
        harness=Harness('runTest');harness.setUp();harness.ns=args.namespace
        harness.codec=codec.v0;harness.pc=codec
        for role in ('colour','plate','aggregator_plate'):harness.start(role)
        journal=Journal(None,namespace=args.namespace,trusted_org=args.org,role='plate',consumer='crop-engine')
        for index,path in enumerate(args.crops,1):
            if time.monotonic()-start>=args.admission_seconds:reason='admission_time_limit';break
            life,crop,measurements,classes,input_receipt=evidence(codec,context,path,index)
            first,last=('left','right') if index%2 else ('right','left')
            operations=(OperationPlan('whole',1,(),'whole_ocr','whole'),OperationPlan('marker',2,(),'marker','marker'),
                OperationPlan(first,3,('whole','marker'),first+'_ocr',first),OperationPlan(last,4,('whole','marker'),last+'_ocr',last))
            plan=CropPlan('crop-attempt-'+str(index),index,crop.binding.candidate.candidate_id,operations,'fixed-local-smoke-plan-v1')
            executors=tuple(bindings['detect' if o.kind=='marker' else 'ocr'].executor(o.executor_id,o.kind) for o in operations)
            adapter=PlateExecutionAdapter(life,executors,expected_attempt_ids=(plan.attempt_id,));adapters.append((adapter,crop))
            adapter.admit(crop.binding,crop,plan);adapter.mark_detection_complete();adapter.seal_admission()
            # Admission is elapsed-bounded; native call duration is not cancellation.
            while not adapter.drain(5):
                if adapter.blockers:raise RuntimeError('ENGINE_EVIDENCE_CONFLICT')
            transcript=adapter.snapshot();expected=reference(codec,life,measurements,classes,transcript)
            plate_batch_id=codec.event_id(life,'plate.batch.identity');sid=f'{index}-0'
            colour=codec.v0.encode('colour.batch',codec.v0.event_id(life,'colour.batch'),dict(life=pack_life(life),complete=True,
                expected_attempt_ids=[m.attempt_id for m in measurements],measurements=[pack_attempt(m) for m in measurements]))
            colour_id=codec.v0.decode(colour).event_id
            closed=codec.make('vehicle.closed.plate',dict(life=pack_life(life),delivery_version='natural-db1-bundle-v1',
                colour_batch_id=colour_id,plate_batch_id=plate_batch_id,
                classes=[dict(receipt=pack_receipt(c.receipt),label=c.label,confidence=c.confidence) for c in classes],
                plate='enabled',manufacturer='unsupported'))
            for stream,raw in [('engine.lifecycle.plate',closed),('engine.evidence.colour',colour)]:
                harness.client.eval(PRODUCE,1,journal.key(stream),sid,raw,journal.max_stream)
            attempts+=1;publication=adapter.publish(harness.client,journal,codec,batch_id=plate_batch_id,stream_id=sid)
            # Same exact prepared bytes and ID; no model call or second stream entry.
            attempts+=1;adapter.publish(harness.client,journal,codec,batch_id=plate_batch_id,stream_id=sid)
            adapter.cleanup()
            eventually(lambda:harness.count('db1.delivery.v1')==index)
            raw=harness.client.xrange(journal.key('db1.delivery.v1'))[-1][1][b'json'];message=codec.decode(raw)
            require(canonical_json_bytes(message.body['bundle'])==expected.payload.canonical
                and message.body['bundle_sha256']==expected.digest,'PARITY_CONFLICT','direct/Redis natural bytes differ')
            result=PlateMetadata().evaluate(life,transcript).result
            row=dict(input=input_receipt,lifetime=index,bundle_sha256=expected.digest,canonical_bytes_equal=True,
                fact_ids=[f['delivery']['uid'] for f in message.body['bundle']['facts']],
                fact_timestamps=[f['delivery']['ts'] for f in message.body['bundle']['facts']],
                omissions=message.body['bundle']['omissions'],plate_outcome=result.outcome,
                operations=[dict(operation_id=r.operation_id,outcome=r.outcome,model=r.model.policy() if r.model else None,error=r.error) for r in adapter.records()],
                batch_event_id=publication.event_id,stream_id=sid,publication_confirmed=adapter.confirmed,release_calls=crop.release_calls)
            receipts.append(row)
        require(bool(receipts),'NO_CROPS_ADMITTED','no faithful crop evidence delivered')
    except Exception as error:
        failure=dict(type=type(error).__name__,code=getattr(error,'code','SMOKE_FAILED'));raise
    finally:
        retained=[];cleanup_errors=[]
        for adapter,crop in adapters:
            try:
                adapter.stop(drain_timeout=5,finish_admitted=True)
                if adapter.confirmed:adapter.cleanup()
            except Exception as error:cleanup_errors.append(dict(component='crop',code=type(error).__name__))
            if not crop.released:retained.append(dict(candidate=crop.binding.candidate.candidate_id,confirmed=adapter.confirmed,
                active=adapter._active is not None))
        for binding in bindings.values():
            if not binding.active:
                try:binding.close()
                except Exception as error:cleanup_errors.append(dict(component='model',code=type(error).__name__))
        processes=[]
        if harness is not None:
            harness.tearDown()
            processes=[dict(pid=p.pid,returncode=p.poll()) for p in harness.children]
        if server_started:
            server_pid=Harness.server.pid;Harness.tearDownClass()
            processes.append(dict(pid=server_pid,returncode=Harness.server.returncode))
        summary=dict(status='failed' if failure or cleanup_errors or retained else 'passed',failure=failure,cleanup_errors=cleanup_errors,reason=reason,
            elapsed_seconds=time.monotonic()-start,bounds=dict(crops=2,attempts=2,planned_operations=8,admission_seconds=args.admission_seconds),
            parent_class_association_colour='injected',plate='real-local',provider_fallback=False,device='cpu',
            publication_attempts=attempts,retained_unconfirmed=retained,receipts=receipts,
            invocations={kind:binding.calls for kind,binding in bindings.items()},
            placements={kind:binding.runtime.placement if binding.runtime else None for kind,binding in bindings.items()},
            lineage={kind:binding.lineage for kind,binding in bindings.items()},
            failures={kind:binding.failures for kind,binding in bindings.items()},
            model_released={kind:binding.closed for kind,binding in bindings.items()},owned_processes=processes)
        if summary['status']=='passed':
            try:require_real_calls(summary)
            except Exception as error:
                summary['status']='failed';summary['failure']=dict(type=type(error).__name__,code=getattr(error,'code','NATIVE_RUNTIME_BLOCKED'))
        (output/'execution-receipts.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
