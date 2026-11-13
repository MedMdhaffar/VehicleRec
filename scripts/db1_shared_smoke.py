#!/usr/bin/env python3
"""Real local shared-path smoke, always supervised in a bounded subprocess.

No model downloads, agents, database keys, SQL or raw plate text in summaries.
Run with the VM interpreter. Logs/metadata stay in the explicitly scoped output.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
VEHICLE = ROOT/'vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service'
PLATE = ROOT/'vehicle/license_plate_service/run_video_pipeline'
OUTPUT_ROOT = ROOT/'local_output/db1_shared_smoke'


def fingerprint(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            digest.update(chunk)
    return {'path':str(path), 'bytes':path.stat().st_size, 'sha256':digest.hexdigest()}


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temp.replace(path)


def receipt_meta(receipt):
    return None if receipt is None else dict(frame_index=receipt.frame_index,
        utc=receipt.utc.isoformat(), monotonic=receipt.monotonic, media_pts=receipt.media_pts)


def validate_selected(evidence):
    selected = evidence.selected
    if selected is None:
        return
    matches = [a for a in evidence.ledger if a.attempt_id == selected.attempt_id]
    assert len(matches) == 1, 'selected attempt must be unique'
    attempt = matches[0]
    assert attempt.parent == evidence.identity and attempt.segment_id == selected.segment_id
    assert selected.crop is attempt.crop and selected.receipt is attempt.crop.receipt
    assert selected.crop_score == attempt.crop_score
    assert any(e.route == selected.route and e.model == selected.model for e in attempt.executions)
    assert not evidence.settlement_incomplete


def completed_meta(completed):
    from vehicle.common.observation_evidence import VehicleIdentity
    vehicle = completed.vehicle
    expected = VehicleIdentity.from_lifetime(vehicle.lifetime)
    consumers = dict(completed.consumer_evidence)
    colour, plate = consumers['colour'], consumers['plate']
    assert colour.identity == plate.identity == expected, 'consumer/owner identity mismatch'
    assert colour.closure == plate.closure == vehicle.lifetime.closure
    assert colour.quarantined == plate.quarantined == vehicle.lifetime.quarantined
    validate_selected(plate)
    assert not colour.agent_attempts and colour.source != 'agent_override'
    if colour.state == 'decided':
        assert colour.samples and colour.support_receipt is colour.samples[-1].receipt
    if plate.attempts == 0 and plate.admitted_crops > 0:
        assert plate.omission_reason == 'failure_before_model', 'accepted but unscheduled crop is not association rejection'
    if plate.settlement_incomplete:
        assert plate.outcome == 'omitted' and plate.omission_reason == 'unsettled_operation' and plate.selected is None
    for a in plate.ledger:
        assert a.parent == expected
        assert a.crop.receipt.frame_index >= vehicle.lifetime.first_receipt.frame_index
        assert a.crop.receipt.frame_index <= vehicle.lifetime.last_receipt.frame_index
        assert all(e.route != 'agent' for e in a.executions)
    return dict(service_track_id=expected.service_track_id, raw_vehicle_id=expected.raw_track_id,
        tracker_epoch=expected.tracker_epoch, lifetime_sequence=expected.lifetime_sequence,
        run_id=expected.context.run_id, closure=plate.closure.value, quarantined=plate.quarantined,
        first_vehicle_receipt=receipt_meta(plate.first_vehicle_receipt),
        last_vehicle_receipt=receipt_meta(plate.last_vehicle_receipt),
        colour=dict(state=colour.state, source=colour.source, label=colour.label,
            attempts=colour.attempts, valid_samples=colour.valid_sample_count,
            local_errors=colour.local_errors, support_receipt=receipt_meta(colour.support_receipt),
            omission_reason=colour.omission_reason, agent_attempts=colour.agent_attempts),
        plate=dict(outcome=plate.outcome, omission_reason=plate.omission_reason,
            attempts=plate.attempts, detections=plate.detections, admitted_crops=plate.admitted_crops,
            ambiguous_crops=plate.ambiguous_crops, unassociated_crops=plate.unassociated_crops,
            settlement_incomplete=plate.settlement_incomplete,
            selected=None if plate.selected is None else dict(attempt_id=plate.selected.attempt_id,
                segment_id=plate.selected.segment_id, route=plate.selected.route,
                model_name=plate.selected.model.model_name, version=plate.selected.model.version,
                receipt=receipt_meta(plate.selected.receipt), bbox=plate.selected.crop.bbox,
                crop_sha256=hashlib.sha256(plate.selected.crop.pixels).hexdigest(), crop_score=plate.selected.crop_score),
            ledger=[dict(attempt_id=a.attempt_id, segment_id=a.segment_id, raw_plate_id=a.raw_plate_id,
                parent_service_track_id=a.parent.service_track_id, status=a.status, error=a.error,
                recognizer_executed=a.recognizer_executed, receipt=receipt_meta(a.crop.receipt),
                crop_score=a.crop_score, completion_monotonic=a.completion_monotonic,
                executions=[dict(route=e.route,model_name=e.model.model_name,version=e.model.version) for e in a.executions])
                for a in plate.ledger]))


def install_network_guard(counters):
    import socket
    # Record library/function origins only; never endpoint addresses or credentials.
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            counters['blocked_network_calls'] += 1
            raise RuntimeError('network disabled in controlled smoke')
        return original(sock, address)
    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            counters['blocked_network_calls'] += 1
            raise RuntimeError('network disabled in controlled smoke')
        return original_ex(sock, address)
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex


def child(args):
    # One consistent import root, plus the existing plate package's CLI import root.
    sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(PLATE.parent))
    out = Path(args.output).resolve()
    started = time.monotonic()
    counts = Counter()
    install_network_guard(counts)
    summary = dict(status='initializing', development_unprovisioned=True,
        database_keys_resolved=False, providers_enabled=False, max_frames=args.max_frames,
        outer_timeout_seconds=args.timeout, closure_drain_seconds=args.drain_timeout,
        clock_tolerance_seconds=5, requested_device=args.device, tracker_frame_rate=args.frame_rate,
        counts=counts, phase='preflight')
    coordinator = plate = colour = owner = reader = None
    completed_ids, associations = set(), Counter()
    outputs, closures, omissions, routes, operation_statuses = Counter(), Counter(), Counter(), Counter(), Counter()
    audit = (out/'completed.jsonl').open('w')
    association_log = (out/'associations.jsonl').open('w')
    try:
        artifacts = {'video':Path(args.video), 'vehicle_model':Path(args.vehicle_model),
            'plate_model':Path(args.plate_model),'marker_model':Path(args.marker_model),
            'vehicle_tracker':VEHICLE/'trackers/bytetrack_vehicles.yaml',
            'plate_tracker':PLATE/'trackers/bytetrack_plates.yaml'}
        for name in ('inference.pdiparams','inference.json','inference.yml'):
            artifacts['ocr/'+name] = Path(args.ocr_dir)/name
        for name in ('ocr.py','pipeline.py','prototype_pipeline.py','private_plate_parser.py','routing.py','config.py'):
            artifacts['recognition_code/'+name] = PLATE/name
        summary['artifacts'] = {}
        for name,path in artifacts.items():
            summary['current_artifact'] = dict(name=name,path=str(path.resolve()))
            summary['artifacts'][name] = fingerprint(path)
        summary.pop('current_artifact')
        write_json(out/'preflight.json',summary)
        import importlib.metadata as md
        import cv2
        import torch
        summary['environment'] = {name:md.version(name) for name in ('torch','ultralytics','paddleocr','paddlex','numpy')}
        summary['environment'].update(python=sys.version.split()[0],opencv=cv2.__version__,
            cuda_available=torch.cuda.is_available(),cuda_devices=torch.cuda.device_count())
        from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
        from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
        from vehicle.common.observation_coordinator import ObservationCoordinator
        from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.service import VehicleAnalysisService
        from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.detector import resolve_device
        from vehicle.vehicle_color_service.vehicle_color_service.shared import SharedColourPipeline
        from run_video_pipeline.shared import SharedPlatePipeline, PrototypeRecognizer
        from run_video_pipeline.shared_evidence import RecognizerModel
        from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
        from run_video_pipeline.plate_detector import PlateDetector
        from run_video_pipeline.marker_localizer import TounesMarkerLocalizer
        from run_video_pipeline.agent import OpenRouterPlateAgent
        from run_video_pipeline.ocr import PlateReader
        from run_video_pipeline.run_video_prototype import _build_tracker
        device = resolve_device(args.device)
        from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service import config as vehicle_config
        from vehicle.vehicle_color_service.vehicle_color_service import config as colour_config
        from run_video_pipeline import config as plate_config
        summary['effective_settings'] = dict(vehicle_confidence=vehicle_config.TRACK_CONF,vehicle_imgsz=640,
            vehicle_lost_frames=vehicle_config.LOST_FRAMES,plate_confidence=plate_config.PLATE_CONF,
            plate_imgsz=plate_config.DETECT_IMGSZ,marker_imgsz=plate_config.WORD_IMGSZ,
            marker_inference_confidence=plate_config.MARKER_INFERENCE_CONF,
            colour={name:getattr(colour_config,name) for name in ('COLOR_ROI_MODE','COLOR_NORMALIZATION',
                'COLOR_NEUTRAL_MODE','COLOR_FEATURE_MODE','MAX_VALID_COLOR_SAMPLES')})
        summary['resolved_device'] = device
        now,org = datetime.now(timezone.utc),uuid4()
        camera = CameraRegistration(org,'development-local-file:'+summary['artifacts']['video']['sha256'],None,'Africa/Tunis',now,None)
        detector_reg = ModelRegistration(None,'development/vehicle/'+summary['artifacts']['vehicle_model']['sha256'],1,'detect')
        ctx = RunContext(org,camera,'development-smoke-'+uuid4().hex,'file',now,'development-shared-smoke-v1',
            ResolutionInfo(None,None,'vehicle',detector_reg,None,'unprovisioned development; actual local artifact SHA-256 identities',False),'development')
        summary['run_id'] = ctx.run_id
        def utc_clock():
            counts['receipts'] += 1
            return datetime.now(timezone.utc)
        clock = FrameReceiptClock(ctx,utc_clock=utc_clock,monotonic_clock=time.monotonic,policy=ClockPolicy(5))
        summary['phase'] = 'vehicle_model_load'
        write_json(out/'progress.json',summary)
        owner = VehicleAnalysisService(device=device,model_path=artifacts['vehicle_model'],
            tracker_path=artifacts['vehicle_tracker'],frame_rate=args.frame_rate,enable_roi=False,
            observation_clock=clock)
        summary['phase'] = 'plate_marker_load'
        plate_detector = PlateDetector(str(artifacts['plate_model']),device=device)
        marker = TounesMarkerLocalizer(str(artifacts['marker_model']),device=device)
        plate_detector.load();marker.load()
        summary['phase'] = 'ocr_model_load'
        write_json(out/'progress.json',summary)
        # Paddle uses cpu/gpu:0 spelling; keep all models on the explicitly chosen device.
        paddle_device = 'cpu' if device == 'cpu' else 'gpu:0'
        reader = PlateReader(model_dir=args.ocr_dir,device=paddle_device)
        summary['ocr_device'] = paddle_device
        import logging
        class ErrorCounter(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    counts['local_recognition_warnings'] += 1
                    if record.getMessage().startswith(('OCR error:', 'OCR failed')):
                        counts['ocr_errors'] += 1
        for name in ('run_video_pipeline.ocr','run_video_pipeline.pipeline'):
            log=logging.getLogger(name);log.addHandler(ErrorCounter());log.propagate=False
        def instrument(obj, method, counter):
            original=getattr(obj,method)
            def call(*a,**kw):
                counts[counter]+=1
                return original(*a,**kw)
            setattr(obj,method,call)
        # Delegating counters use actual inference, never substituted results.
        instrument(owner.pipeline.detector_tracker.model,'predict','vehicle_model_predict_calls')
        original_process=owner.pipeline.detector_tracker.process
        def vehicle_process(image):
            index=clock.last_valid_receipt.frame_index
            assert counts['vehicle_inference_calls'] == index, 'duplicate/out-of-order vehicle inference'
            counts['vehicle_inference_calls'] += 1
            return original_process(image)
        owner.pipeline.detector_tracker.process=vehicle_process
        instrument(plate_detector,'predict','plate_detector_calls')
        instrument(marker._model,'predict','marker_predict_calls')
        instrument(reader,'read_result','low_level_ocr_calls')
        # Verify association IDs against the actual same-frame owner tuple, without
        # another detector or another geometry calculation.
        current_observation = None
        def association(e):
            assert current_observation is not None and e.receipt is current_observation.receipt
            same_frame_ids = {v.service_track_id for v in current_observation.vehicles}
            assert all(sid in same_frame_ids for sid,_ in e.candidate_scores)
            assert e.selected_service_track_id is None or e.selected_service_track_id in same_frame_ids
            associations[e.status]+=1
            association_log.write(json.dumps(dict(receipt=receipt_meta(e.receipt),raw_plate_id=e.raw_plate_id,
                status=e.status,selected_service_track_id=e.selected_service_track_id,candidate_scores=e.candidate_scores))+'\n')
            association_log.flush()
        bundle = hashlib.sha256(json.dumps({k:v['sha256'] for k,v in summary['artifacts'].items()
            if k.startswith(('ocr/','recognition_code/')) or k=='marker_model'},sort_keys=True).encode()).hexdigest()
        pipeline=PrototypePlatePipeline(reader,marker,OpenRouterPlateAgent(enabled=False),store=None)
        recognizer=PrototypeRecognizer(pipeline,local_model=RecognizerModel('development/local-ocr-bundle/'+bundle,1))
        summary['local_recognizer_bundle_sha256']=bundle
        colour=SharedColourPipeline(ctx,agent_enabled=False)
        plate=SharedPlatePipeline(ctx,detector=plate_detector,tracker=_build_tracker(artifacts['plate_tracker'],args.frame_rate),
            recognizer=recognizer,drain_timeout=args.drain_timeout,on_association=association,
            max_pending=8,max_segments=128,max_finalized=128)
        process_plate_frame = plate.process_vehicle_frame
        def plate_frame(image, observation):
            nonlocal current_observation
            current_observation = observation
            try:
                return process_plate_frame(image, observation)
            finally:
                current_observation = None
        plate.process_vehicle_frame = plate_frame
        summary['plate_policy'] = {name:getattr(plate,name) for name in ('strong_quality','strong_min_candidates',
            'min_candidates','stable_observations','disappearance_frames','minimum_exit_candidates',
            'retry_improvement','recheck_detail_improvement','replacement_confidence_gain','max_pending')}
        def completed(result):
            metadata=completed_meta(result)
            sid=metadata['service_track_id']
            assert sid not in completed_ids, 'duplicate completed lifetime'
            audit.write(json.dumps(metadata,sort_keys=True)+'\n');audit.flush()
            completed_ids.add(sid)
            closures[metadata['closure']]+=1
            if metadata['quarantined']: counts['quarantined_lifetimes']+=1
            p=metadata['plate'];outputs[p['outcome']]+=1
            if p['omission_reason']: omissions[p['omission_reason']]+=1
            counts['plate_submissions']+=p['attempts']
            for a in p['ledger']:
                operation_statuses[a['status']]+=1
                for e in a['executions']: routes[e['route']]+=1
            # Release legacy completed JSON history once the combined result is accepted.
            owner.drain_finalized_profiles()
            summary['completed_vehicle_lifetimes']=len(completed_ids)
            write_json(out/'progress.json',summary)
        coordinator=ObservationCoordinator(owner,{'colour':colour,'plate':plate},on_completed=completed)
        original_capture=cv2.VideoCapture
        class Capture:
            def __init__(self,source):
                counts['decoder_open_calls']+=1
                if counts['decoder_open_calls']!=1:
                    raise RuntimeError('second decoder attempted')
                self.capture=original_capture(source)
                summary['video_metadata']=dict(fps=self.capture.get(cv2.CAP_PROP_FPS),
                    total_frames=int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT)),
                    width=int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)),height=int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            def read(self):
                counts['decode_reads']+=1
                ok,image=self.capture.read()
                if ok: counts['successful_decodes']+=1
                return ok,image
            def get(self,*a): return self.capture.get(*a)
            def isOpened(self): return self.capture.isOpened()
            def release(self):
                counts['decoder_releases']+=1
                self.capture.release()
        summary['phase']='processing'
        write_json(out/'progress.json',summary)
        cv2.VideoCapture=Capture
        try:
            result=coordinator.process_video(args.video,max_frames=args.max_frames)
        finally:
            cv2.VideoCapture=original_capture
        summary['processed_frames']=result['frames_processed']
        assert counts['decoder_open_calls']==counts['decoder_releases']==1
        assert counts['successful_decodes']==counts['receipts']==counts['vehicle_inference_calls']==counts['vehicle_model_predict_calls']==result['frames_processed']
        assert counts['plate_detector_calls']==result['frames_processed']
        summary['status']='passed'
    except BaseException as exc:
        summary['status']='failed'
        summary['failure_type']=type(exc).__name__
        # Scoped failure trace has code locations only; no exception/recognition values.
        import traceback
        summary['failure_locations']=[dict(file=str(Path(f.filename).resolve()),line=f.lineno,function=f.name)
            for f in traceback.extract_tb(exc.__traceback__)]
    finally:
        summary['phase_at_exit']=summary['phase']
        cleanup_errors=[]
        if coordinator is not None:
            for name,action in [('finish',coordinator.finish),('flush',coordinator.flush_closures),('plate_shutdown',plate.shutdown)]:
                try: action()
                except BaseException as exc: cleanup_errors.append(dict(step=name,type=type(exc).__name__))
            plate.poll()
            summary['pending_after_cleanup']=dict(owner_closures=len(owner.pending_finalized_observations(owner=coordinator)),
                owner_active_lifetimes=len(owner.pipeline.active_profiles),colour_active=len(colour._tracks),
                colour_finalized=len(colour._pending),plate_active=len(plate._vehicles),plate_finalized=len(plate._pending),
                plate_work=len(plate._inflight),coordinator_completed=len(coordinator._completed))
            summary['run_end']=dict(cause=owner.pipeline.lifecycle.end_reason.value if owner.pipeline.lifecycle.end_reason else None,
                trigger=owner.pipeline.end_trigger)
        if reader is not None and (plate is None or not plate._inflight):
            try: reader.engine.close()
            except BaseException as exc: cleanup_errors.append(dict(step='ocr_close',type=type(exc).__name__))
        audit.close();association_log.close()
        summary.update(elapsed_seconds=round(time.monotonic()-started,3),completed_vehicle_lifetimes=len(completed_ids),
            associations=dict(associations),plate_outcomes=dict(outputs),plate_omissions=dict(omissions),
            operation_statuses=dict(operation_statuses),executed_routes=dict(routes),counts=dict(counts),cleanup_errors=cleanup_errors)
        if cleanup_errors: summary['status']='failed'
        write_json(out/'summary.json',summary)
    return 0 if summary['status']=='passed' else 1


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video',default=str(PLATE/'test_video.mp4'))
    p.add_argument('--vehicle-model',default=str(VEHICLE/'models/vehicle_yolo26.pt'))
    p.add_argument('--plate-model',default=str(PLATE/'models/best.pt'))
    p.add_argument('--marker-model',default=str(PLATE/'models/tounes_word_detector.pt'))
    p.add_argument('--ocr-dir',default='/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec')
    p.add_argument('--device',choices=('cpu','auto','cuda'),default='cpu')
    p.add_argument('--max-frames',type=int,default=150)
    p.add_argument('--timeout',type=float,default=300)
    p.add_argument('--drain-timeout',type=float,default=20)
    p.add_argument('--frame-rate',type=float,default=25)
    p.add_argument('--output')
    p.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    return p


def main():
    args=parser().parse_args()
    if not 1<=args.max_frames<=150 or not 0<args.timeout<=300 or not 0<=args.drain_timeout<args.timeout:
        raise SystemExit('require 1..150 frames, outer deadline <=300s and bounded drain < outer deadline')
    if not math.isfinite(args.frame_rate) or args.frame_rate<=0:
        raise SystemExit('positive tracker frame rate required')
    if args.child:
        return child(args)
    output=(Path(args.output) if args.output else OUTPUT_ROOT/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if OUTPUT_ROOT.resolve() not in output.parents:
        raise SystemExit('output must be a new child of local_output/db1_shared_smoke')
    output.mkdir(parents=True,exist_ok=False)
    cmd=[sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--output',str(output),'--child']
    env=dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',YOLO_AUTOINSTALL='false',
        YOLO_CONFIG_DIR=str(output/'ultralytics'),OPENROUTER_ENABLED='false',COLOR_AGENT_ENABLED='false')
    # Do not pass inherited provider credentials into the smoke child.
    for name in tuple(env):
        if any(s in name.upper() for s in ('API_KEY','ACCESS_TOKEN','SECRET','PASSWORD')):
            del env[name]
    snapshot=Path(__file__).read_bytes()
    (output/'host_snapshot.py').write_bytes(snapshot)
    start=time.monotonic()
    with (output/'worker.log').open('wb') as log:
        process=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        timed_out=False
        try:
            returncode=process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            os.killpg(process.pid,signal.SIGKILL)
            returncode=process.wait(timeout=5)
        finally:
            # Only this child's process group; no broad pkill or unrelated cleanup.
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    supervisor=dict(invocation=[sys.executable,str(Path(__file__).resolve()),*sys.argv[1:]],
        host_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        child_returncode=returncode,outer_timeout=timed_out,wall_seconds=round(time.monotonic()-start,3),output=str(output))
    write_json(output/'supervisor.json',supervisor)
    print(json.dumps(supervisor,sort_keys=True))
    return 124 if timed_out else 0 if returncode==0 else 1


if __name__=='__main__':
    raise SystemExit(main())
