"""One MP4 decode/inference pass, immutable scalar tee and exact native-Redis parity."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',required=True,type=Path)
    parser.add_argument('--weights',required=True,type=Path)
    parser.add_argument('--tracker',required=True,type=Path)
    parser.add_argument('--redis-server',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--org',required=True,type=UUID)
    parser.add_argument('--camera',required=True)
    parser.add_argument('--timezone',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--namespace',required=True)
    parser.add_argument('--identity-namespace',required=True,type=UUID)
    parser.add_argument('--device',required=True,choices=['cpu','cuda:0'])
    parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--max-frames',required=True,type=int)
    parser.add_argument('--max-seconds',required=True,type=float)
    parser.add_argument('--delivery-timeout',type=float,default=30)
    args=parser.parse_args(argv)
    if not 1<=args.max_frames<=20 or not 0<args.max_seconds<=300 or not 1<=args.threads<=8 or not 0<args.delivery_timeout<=120:
        parser.error('bounded frames/time/threads required')
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ.update(YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false',COLOR_AGENT_ENABLED='false',
        YOLO_CONFIG_DIR=str(args.output/'ultralytics-settings'),OMP_NUM_THREADS=str(args.threads),
        MKL_NUM_THREADS=str(args.threads),OPENBLAS_NUM_THREADS=str(args.threads))
    from contracts.db1.reference import canonical_json_bytes
    from vehicle.common.db1_context import CameraRegistration,ModelRegistration,ResolutionInfo,RunContext
    from vehicle.common.frame_receipt import FrameReceiptClock,ClockPolicy
    from vehicle.common.observation_evidence import RecognitionIdentity
    from vehicle.common.real_video.local import identities,fingerprint,LocalDetector,pixel_operations
    from vehicle.common.real_video.input import MP4Input
    from vehicle.common.real_video.engine import Engine
    from vehicle.common.real_video.host import Publisher,reference_bundle,run_capture,scalar_body
    from vehicle.common.real_video.runtime import PrivateRuntime
    from vehicle.common.redis_scalar.journal import Journal
    from vehicle.common.redis_scalar.codec import pack_receipt
    from vehicle.common.redis_scalar.worker import load_codec
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    report=dict(status='starting',command=sys.argv,limits=dict(frames=args.max_frames,seconds=args.max_seconds),
                org_id=str(args.org),run_id=args.run_id,camera_id=args.camera)
    engine=source=detector=runtime=None
    references={}
    captured=[]
    frames=[]
    def write(name,value):
        (args.output/name).write_bytes(canonical_json_bytes(value)+b'\n')
    try:
        artifacts=identities(args.weights,args.tracker)
        video=fingerprint(args.video)
        report.update(input=video,artifacts=artifacts,redis_binary=fingerprint(args.redis_server))
        started=datetime.now(timezone.utc)
        context=RunContext(args.org,CameraRegistration(args.org,args.camera,None,args.timezone,started,None),
            args.run_id,'file',started,'real-video-slice-v1',
            ResolutionInfo(None,None,'vehicle',ModelRegistration(None,artifacts['detector_name'],1,'detect'),None,
                'unregistered local MP4 sha256:'+video['sha256'],False),'development')
        clock=FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),monotonic_clock=time.monotonic,policy=ClockPolicy())
        codec=load_codec(args.org,args.identity_namespace)
        journal=Journal(None,namespace=args.namespace,trusted_org=args.org,role='colour',consumer='video-producer')
        runtime=PrivateRuntime(args.redis_server,args.output,namespace=args.namespace,org=args.org,identity_namespace=args.identity_namespace)
        with runtime:
            # Input is the only object permitted to open the video.
            source=MP4Input(args.video,clock)
            source.__enter__()  # One open; metadata comes from this same capture.
            import math
            import cv2
            cv2.setNumThreads(args.threads)
            fps=source.metadata['fps']
            if not math.isfinite(fps) or fps<=0:raise RuntimeError('source FPS is unavailable')
            detector=LocalDetector(args.weights,args.tracker,device=args.device,frame_rate=fps,threads=args.threads)
            detector.placement.update(opencv_threads=cv2.getNumThreads(),tracker_frame_rate=fps)
            estimate,quality=pixel_operations()
            engine=Engine(context,clock,detector,algorithm=RecognitionIdentity(artifacts['colour_name'],1,'classify'),
                quality_algorithm=artifacts['quality_name'],estimate=estimate,quality=quality)
            publisher=Publisher(runtime.client,journal,codec)
            def sink(evidence):
                key=codec.event_id(evidence.owner.lifetime,'vehicle.closed')
                if key not in references:
                    references[key]=reference_bundle(codec,evidence)
                    captured.append(evidence)
                publisher.publish(evidence)
            capture=run_capture(source,engine,sink,max_frames=args.max_frames,max_seconds=args.max_seconds,
                stop_requested=lambda:stopped,on_frame=lambda packet:frames.append(pack_receipt(packet.receipt)))
            report.update(capture=capture,input_metadata=source.metadata,redis=runtime.settings)
            if not captured:raise RuntimeError('no eligible real closed lifetimes; smoke does not establish vehicle-path parity')
            deadline=time.monotonic()+args.delivery_timeout
            rows=[]
            while time.monotonic()<deadline:
                rows=runtime.client.xrange(journal.key('db1.delivery'))
                if len(rows)>=len(references):break
                if any(child.poll() is not None for name,child in runtime.children):raise RuntimeError('owned Redis worker exited')
                time.sleep(.05)
            if len(rows)!=len(references):raise RuntimeError('final delivery count differs from closed eligible lifetimes')
            deliveries={}
            for entry,fields in rows:
                msg=codec.decode(fields[b'json'])
                key=codec.event_id(msg.life,'vehicle.closed')
                if key in deliveries:raise RuntimeError('duplicate logical final delivery')
                deliveries[key]=(msg,entry.decode())
                (args.output/('delivery-'+str(msg.life.service_track_id)+'.json')).write_bytes(fields[b'json']+b'\n')
            parity=[]
            for evidence in captured:
                life=evidence.owner.lifetime
                key=codec.event_id(life,'vehicle.closed')
                expected=references[key]
                msg,entry=deliveries[key]
                actual=canonical_json_bytes(msg.body['bundle'])
                if actual!=expected.payload.canonical or msg.body['bundle_sha256']!=expected.digest:
                    raise RuntimeError('canonical natural bundle parity failure')
                (args.output/('reference-'+str(life.service_track_id)+'.json')).write_bytes(expected.payload.canonical+b'\n')
                plan=publisher.prepare(evidence)
                (args.output/('closed-'+str(life.service_track_id)+'.json')).write_bytes(plan['closed']+b'\n')
                (args.output/('batch-'+str(life.service_track_id)+'.json')).write_bytes(plan['batch']+b'\n')
                payload=expected.payload.value()
                parity.append(dict(service_track_id=life.service_track_id,raw_track_id=life.raw_track_id,
                    frames_observed=life.frames_observed,attempts=len(evidence.measurements),class_samples=len(evidence.classes),
                    first=pack_receipt(life.first_receipt),last=pack_receipt(life.last_receipt),closure=life.closure.value,
                    expected_sha256=expected.digest,actual_sha256=hashlib.sha256(actual).hexdigest(),bytes_equal=True,
                    fact_ids=[f['delivery']['uid'] for f in payload['facts']],
                    fact_timestamps=[f['delivery']['ts'] for f in payload['facts']],omissions=payload['omissions'],
                    provenance_sha256=hashlib.sha256(canonical_json_bytes(payload['colour_provenance'])).hexdigest(),
                    stream_entry=entry))
            diagnostics={name:runtime.client.xlen(journal.key('diagnostics.'+name)) for name in ('colour','aggregator')}
            if any(diagnostics.values()):raise RuntimeError('metadata diagnostic blocks smoke acceptance')
            report.update(status='passed',parity=parity,diagnostics=diagnostics,closed_lifetimes=len(captured))
        return_code=0
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error_code=getattr(error,'code','HOST_FAILURE'))
        if engine is not None:
            write('unsent-closed-evidence.json',[scalar_body(e) for e in engine.pending.values()])
        return_code=2
    finally:
        if source is not None:source.close()
        if engine is not None:engine.close()
        elif detector is not None:detector.close()
        if runtime is not None:runtime.close();report['cleanup']=runtime.cleanup
        all_evidence={e.owner.service_track_id:e for e in captured}
        if engine is not None:all_evidence.update(engine.pending)
        write('captured-closed-evidence.json',[scalar_body(e) for e in all_evidence.values()])
        report['frame_receipts']=frames
        if source is not None:report['source_opens']=source.opens;report['decoded_frames']=source.decoded
        if detector is not None:report.update(placement=detector.placement,prediction_calls=detector.prediction_calls,
            forward_calls_after_load=detector.forward_calls,model_released=detector.closed)
        if engine is not None:report.update(hsv_calls=engine.hsv_calls,quality_calls=engine.quality_calls,
            unsent_lifetimes=len(engine.pending),provider_calls=0,plate_calls=0,manufacturer_calls=0)
        write('report.json',report)
    print(json.dumps(dict(status=report['status'],report=str(args.output/'report.json'))))
    return return_code


if __name__=='__main__':raise SystemExit(main())
