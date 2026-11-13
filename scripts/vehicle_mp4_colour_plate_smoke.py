"""One controlled MP4 decode/model pass, real scalar v1 Redis tee and parity."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
from hashlib import sha256
import importlib.metadata
import json,os,signal,socket,sys,time
from pathlib import Path
from uuid import UUID
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def write_evidence(path,value):
    from contracts.db1.reference import canonical_json_bytes
    from vehicle.common.plate_metadata import _json_scalars
    path.write_bytes(canonical_json_bytes(_json_scalars(value))+b'\n')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('video','vehicle-weights','vehicle-tracker','plate-weights','plate-tracker','ocr-dir','marker','redis-server','output'):
        p.add_argument('--'+name,required=True,type=Path)
    for name in ('camera','timezone','run-id','namespace'):p.add_argument('--'+name,required=True)
    for name in ('org','identity-namespace'):p.add_argument('--'+name,required=True,type=UUID)
    p.add_argument('--device',required=True,choices=['cpu']);p.add_argument('--threads',required=True,type=int,choices=[1])
    p.add_argument('--max-frames',required=True,type=int);p.add_argument('--admission-seconds',required=True,type=float)
    p.add_argument('--delivery-timeout',type=float,default=30)
    p.add_argument('--request-loop-v2',action='store_true',help='Opt in to Plate-owned progressive Redis requests')
    args=p.parse_args(argv);args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=False)
    os.environ.update(OPENROUTER_ENABLED='false',COLOR_AGENT_ENABLED='false',YOLO_AUTOINSTALL='false',YOLO_OFFLINE='true',
        PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',YOLO_CONFIG_DIR=str(args.output/'ultralytics-settings'),
        OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    # No provider/automatic model retrieval; only private Unix Redis sockets.
    connect=socket.socket.connect;connect_ex=socket.socket.connect_ex
    def offline(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('NETWORK_DISABLED_LOCAL_SMOKE')
        return connect(self,address)
    def offline_ex(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('NETWORK_DISABLED_LOCAL_SMOKE')
        return connect_ex(self,address)
    socket.socket.connect=offline;socket.socket.connect_ex=offline_ex
    from contracts.db1.reference import canonical_json_bytes
    from vehicle.common.db1_context import CameraRegistration,ModelRegistration,ResolutionInfo,RunContext
    from vehicle.common.frame_receipt import FrameReceiptClock,ClockPolicy
    from vehicle.common.observation_evidence import RecognitionIdentity
    from vehicle.common.engine_plate import native_access
    from vehicle.common.engine_plate_local import artifact,LocalPlateBinding,NativeCPU
    from vehicle.common.real_video.input import MP4Input
    from vehicle.common.real_video.local import identities,fingerprint,LocalDetector,pixel_operations
    from vehicle.common.real_video.plate_local import NativePlateDetector,detector_artifact
    from vehicle.common.real_video.plate_engine import CombinedEngine,VideoPlateLimits
    from vehicle.common.real_video.plate_host import V1Publisher,drive_video
    from vehicle.common.real_video.runtime import PrivateRuntime
    from vehicle.common.redis_scalar.journal import Journal
    from vehicle.common.redis_scalar.worker import load_plate_codec,load_request_codec
    from vehicle.common.plate_requests.host import V2Publisher
    from vehicle.common.redis_scalar.codec import pack_receipt
    from vehicle.common.db1_rows.core import require
    limits=VideoPlateLimits(max_frames=args.max_frames,max_seconds=args.admission_seconds)
    require(0<args.delivery_timeout<=120,'INVALID_WINDOW','bounded delivery wait required')
    report=dict(status='starting',command=sys.argv,org_id=str(args.org),run_id=args.run_id,camera=args.camera,
        limits=asdict(limits),evidence='real vehicle/class/Colour/Plate detection/association/recognition; no injected components',provider_calls=0,
        reference_model_calls=0,manufacturer='unsupported',frames=[],parity=[])
    runtime=source=engine=vehicle=plate=publisher=None;bindings={};initializations={};stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    def write(name,value):write_evidence(args.output/name,value)
    def observe(packet):report['frames'].append(pack_receipt(packet.receipt))
    try:
        input_id=fingerprint(args.video);vehicle_ids=identities(args.vehicle_weights,args.vehicle_tracker)
        now=datetime.now(timezone.utc)
        context=RunContext(args.org,CameraRegistration(args.org,args.camera,None,args.timezone,now,None),args.run_id,'file',now,
            'bounded-mp4-colour-plate-v1',ResolutionInfo(None,None,'vehicle',ModelRegistration(None,vehicle_ids['detector_name'],1,'detect'),None,
                'local MP4 sha256:'+input_id['sha256'],False),'development')
        clock=FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),monotonic_clock=time.monotonic,policy=ClockPolicy())
        source=MP4Input(args.video,clock);source.__enter__() # one capture; no frame decoded yet
        import cv2
        cv2.setNumThreads(1);fps=source.metadata['fps']
        require(fps>0,'MISSING_FPS','one input capture must report actual FPS')
        plate_id=detector_artifact(args.plate_weights,args.plate_tracker,frame_rate=fps)
        recognizers={kind:artifact(kind,path) for kind,path in [('ocr',args.ocr_dir),('detect',args.marker)]}
        def factory(identity):
            initializations[identity.task]=initializations.get(identity.task,0)+1
            require(initializations[identity.task]<=1,'CAPACITY_EXCEEDED','one model initialization attempt per recognizer in smoke')
            return NativeCPU(identity)
        bindings={kind:LocalPlateBinding(identity,runtime_factory=factory) for kind,identity in recognizers.items()}
        preflight=dict(input=input_id,vehicle=vehicle_ids,plate=dict(model=plate_id.model.policy(),material=plate_id.material(),files=[asdict(f) for f in plate_id.files]),
            recognizers={k:dict(model=v.model.policy(),material=v.material(),files=[asdict(f) for f in v.files]) for k,v in recognizers.items()},
            redis=fingerprint(args.redis_server),source_metadata=source.metadata,limits=asdict(limits),requested_device=args.device,threads=args.threads,
            packages={n:importlib.metadata.version(n) for n in ['torch','ultralytics','paddleocr','paddlepaddle','paddlex','opencv-python','numpy','lap','redis','jsonschema']},
            source=[fingerprint(path) for path in [Path(__file__),ROOT/'vehicle/common/real_video/plate_engine.py',ROOT/'vehicle/common/real_video/plate_host.py',
                ROOT/'vehicle/common/real_video/plate_local.py',ROOT/'vehicle/common/engine_plate.py',ROOT/'vehicle/common/engine_plate_artifacts.py']])
        if args.request_loop_v2:
            preflight['source'].extend(fingerprint(ROOT/path) for path in [
                'vehicle/common/plate_requests/codec.py','vehicle/common/plate_requests/policy.py',
                'vehicle/common/plate_requests/engine.py','vehicle/common/plate_requests/host.py',
                'vehicle/common/plate_requests/worker.py','contracts/vehicle_plate_requests_v2.schema.json'])
        write('preflight.json',preflight) # written before model construction/inference
        with native_access():vehicle=LocalDetector(args.vehicle_weights,args.vehicle_tracker,device='cpu',frame_rate=fps,threads=1)
        plate=NativePlateDetector(plate_id,frame_rate=fps)
        estimate,quality=pixel_operations()
        engine=CombinedEngine(context,clock,vehicle,plate,bindings,algorithm=RecognitionIdentity(vehicle_ids['colour_name'],1,'classify'),
            quality_algorithm=vehicle_ids['quality_name'],plate_quality_algorithm='plate-crop-quality-sha256:'+plate_id.digest,
            estimate=estimate,quality_fn=quality,limits=limits)
        codec=load_plate_codec(args.org,args.identity_namespace)
        journal=Journal(None,namespace=args.namespace,trusted_org=args.org,role='plate_requests' if args.request_loop_v2 else 'plate',consumer='mp4-engine')
        runtime=PrivateRuntime(args.redis_server,args.output,namespace=args.namespace,org=args.org,identity_namespace=args.identity_namespace,delivery_version='v2' if args.request_loop_v2 else 'v1')
        with runtime:
            report['redis_settings']=runtime.settings
            publisher=(V2Publisher(runtime.client,journal,load_request_codec(args.org,args.identity_namespace),engine)
                if args.request_loop_v2 else V1Publisher(runtime.client,journal,codec,engine))
            report['capture']=drive_video(source,engine,publisher,stop_requested=lambda:stopped,on_frame=observe)
            expected=len(publisher.plans);deadline=time.monotonic()+args.delivery_timeout
            while runtime.client.xlen(journal.key('db1.delivery.v1'))<expected:
                if time.monotonic()>=deadline:raise RuntimeError('DELIVERY_TIMEOUT')
                time.sleep(.05)
            rows=runtime.client.xrange(journal.key('db1.delivery.v1'));require(len(rows)==expected,'DELIVERY_CONFLICT','one physical final per closed lifetime required')
            seen=set()
            for entry,fields in rows:
                message=codec.decode(fields[b'json']);sid=message.life.service_track_id;plan=publisher.plans[sid]
                bundle=message.body['bundle'];encoded=canonical_json_bytes(bundle)
                require(encoded==plan.reference.payload.canonical and message.body['bundle_sha256']==plan.reference.digest,
                    'PARITY_CONFLICT','complete canonical bytes/provenance differ')
                require(sid not in seen,'DELIVERY_CONFLICT','duplicate logical final delivery');seen.add(sid)
                plate_result=__import__('vehicle.common.plate_metadata',fromlist=['PlateMetadata']).PlateMetadata().evaluate(message.life,plan.transcript).result
                report['parity'].append(dict(service_track_id=sid,stream_id=entry.decode(),sha256=plan.reference.digest,canonical_bytes_equal=True,
                    fact_ids=[f['delivery']['uid'] for f in bundle['facts']],timestamps=[f['delivery']['ts'] for f in bundle['facts']],
                    omissions=bundle['omissions'],plate_outcome=plate_result.outcome,
                    provenance_sha256=sha256(canonical_json_bytes(bundle['plate_provenance'])).hexdigest(),
                    expected_attempt_ids=list(plan.transcript.expected_attempt_ids),
                    operations=[dict(attempt_id=a.attempt_id,operation_id=o.operation_id,kind=o.kind,outcome=o.outcome,
                        model=o.model.policy() if o.model else None) for a in plan.transcript.attempts for o in a.operations]))
            diagnostics={role:runtime.client.xlen(journal.key('diagnostics.'+role)) for role in ('colour','plate_requests' if args.request_loop_v2 else 'plate','aggregator_plate')}
            require(not any(diagnostics.values()),'METADATA_DIAGNOSTIC','metadata contract diagnostics block smoke acceptance')
            require(expected>0 and len(seen)==expected and not engine.pending,'INCOMPLETE_DELIVERY','all closed lifetimes require complete parity')
            recognition_exercised=bindings['ocr'].calls.get('whole_ocr',0)>0 and bindings['detect'].calls.get('marker',0)>0
            if args.request_loop_v2:
                from vehicle.common.plate_requests.codec import STREAMS
                report['stream_counts']={stream:runtime.client.xlen(journal.key(stream)) for stream in
                    [*STREAMS.values(),'engine.lifecycle.plate','engine.evidence.colour','results.colour','results.plate','db1.delivery.v1']}
                report['pending_counts']={stream:[dict(group=g['name'].decode() if isinstance(g['name'],bytes) else g['name'],pending=g['pending'])
                    for g in runtime.client.xinfo_groups(journal.key(stream))] for stream in report['stream_counts']}
            report.update(status='passed' if recognition_exercised else 'partial_no_recognition',diagnostics=diagnostics,
                delivered_lifetimes=expected,recognition_exercised=recognition_exercised)
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error_code=getattr(error,'code','HOST_FAILURE'))
    finally:
        if source is not None:source.close();report.update(source_opens=source.opens,decoded_frames=source.decoded)
        if engine is not None:
            engine.close()
            report.update(native_api_calls=engine.audit.calls,native_trace=engine.audit.events,max_native_active=engine.audit.max_active,
                native_active=engine.audit.active,engine_cleanup_errors=engine.cleanup_errors,engine_models_closed=engine.closed,
                colour_measurements=engine.colour.hsv_calls,colour_quality_measurements=engine.colour.quality_calls,
                plate_quality_measurements=engine.plate.quality_calls,raw_plate_detections=engine.plate.raw_count,
                confirmed_plate_detections=engine.plate.detections,suppressed_plate_duplicates=engine.plate.suppressed,
                crop_attempts=engine.plate.attempts,crop_replacements=engine.plate.crop_replacements,retained_crop_handles=len(engine.plate.buffers),
                retained_crop_bytes=sum(c.binding.size_bytes for c in engine.plate.buffers),closed_lifetimes=len(engine.colour.closed_ids),
                pending_lifetimes=len(engine.pending),quarantined_lifetimes=sum(v.life.quarantined for v in engine.plate.lives.values()),
                blockers={str(s):v.blocker for s,v in engine.plate.lives.items() if v.blocker},
                retained_closed=[dict(service_track_id=s,closure=e.owner.lifetime.closure.value,publication_confirmed=publisher is not None and s in publisher.confirmed) for s,e in engine.pending.items()])
            associations=[]
            for row in engine.plate.associations:
                row=dict(row)
                if 'receipt' in row:row['receipt']=pack_receipt(row['receipt'])
                associations.append(row)
            write('associations.json',associations)
            report['admission_outcomes']=dict(Counter(row['status'] for row in associations))
            report['crop_release_acknowledgments']=[dict(service_track_id=s,candidate=c.binding.candidate.candidate_id,
                released=c.released,release_calls=c.release_calls) for s,v in engine.plate.lives.items() for _,c in v.best.values()]
            if engine.cleanup_errors and report['status']=='passed':report['status']='failed';report['error_code']='CLEANUP_FAILED'
        elif vehicle is not None:
            with native_access():vehicle.close()
        if publisher is not None:
            report.update(publication_attempts=publisher.attempts,wire_publications=publisher.wire_calls,
                confirmed_lifetimes=sorted(publisher.confirmed),retained_scalar_bytes=publisher.retained_bytes,
                prepared_packet_hashes=[dict(service_track_id=s,closed_sha256=sha256(v.closed).hexdigest(),colour_sha256=sha256(v.colour).hexdigest(),
                    plate_sha256=(sha256(v.session.sealed).hexdigest() if v.session.sealed else None) if args.request_loop_v2 else sha256(v.plate).hexdigest()) for s,v in publisher.plans.items()])
        if args.request_loop_v2 and publisher is not None:
            report['request_loop_version']='vehicle-plate-requests-v2'
            report['requests']=[dict(org_id=str(args.org),service_track_id=s,request_id=r['request_id'],sequence=r['sequence'],
                attempt_id=r['attempt_id'],ordinal=r['ordinal'],candidate_id=r['candidate_id'],operation=r['operation'],
                session=r['session'],dependencies=r['dependencies'],
                accepted=publisher.codec.decode(admission).body['accepted'],reason=publisher.codec.decode(admission).body['reason'])
                for s,plan in publisher.plans.items() for r,admission in plan.session.ledger.values()]
            report['engine_completions']=[dict(service_track_id=s,sealed=plan.session.sealed is not None,
                completion_confirmed=plan.session.completed,admitted_ids=sorted(plan.session.accepted)) for s,plan in publisher.plans.items()]
        report['recognition_invocations']={k:b.calls for k,b in bindings.items()}
        report['recognition_failures']={k:b.failures for k,b in bindings.items()}
        report['recognizer_initializations']=initializations
        report['recognizer_placements']={k:b.runtime.placement if b.runtime else None for k,b in bindings.items()}
        report['recognizer_model_closed']={k:b.closed for k,b in bindings.items()}
        report['recognition_lineage']={k:b.lineage for k,b in bindings.items()}
        if vehicle is not None:report.update(vehicle_predict_calls=vehicle.prediction_calls,vehicle_forward_hooks=vehicle.forward_calls,vehicle_placement=vehicle.placement)
        if plate is not None:report.update(plate_predict_calls=plate.calls,plate_forward_hooks=plate.forwards,plate_placement=plate.placement)
        if runtime is not None:runtime.close();report['cleanup']=runtime.cleanup
        write('report.json',report)
    print(json.dumps(dict(status=report['status'],report=str(args.output/'report.json'))))
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
