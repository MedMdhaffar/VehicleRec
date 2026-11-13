"""One owned console run, using shared Input, CombinedEngine and T1 v2 workers."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(directory):
    from vehicle.common.console_stack import configure, model_paths, console_device
    configure(load_dsn=False)
    from vehicle.common.console_runs import atomic_json, timestamp, metadata_turn, review_acknowledgments
    directory = Path(directory).resolve(strict=True)
    config = json.loads((directory/'config.json').read_text())
    status = json.loads((directory/'status.json').read_text())
    source = None
    stop, watcher_done = threading.Event(), threading.Event()
    if (directory/'stop').exists():stop.set()
    def watch():
        while not watcher_done.wait(.03):
            if (directory/'stop').exists():
                stop.set()
                if source is not None and hasattr(source,'halt'):source.halt.set()
                return
    threading.Thread(target=watch,daemon=True).start()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    def save(**changes):
        status.update(changes)
        atomic_json(directory/'status.json',status)
    source = engine = vehicle = plate_detector = owned = ledger = maker_runtime = publisher = crops = None
    db1_child = None
    workers, bindings, originals, rows = [], {}, {}, {}
    private_camera_dir = Path(config['source']).parent if config['source_kind']=='rtsp' else None
    final_state, error_code, reason = 'completed', None, 'error'
    every = config.get('sample_every', 5 if config['source_kind']=='rtsp' else 1)
    analyzed = skips = 0
    os.environ.update(YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false',COLOR_AGENT_ENABLED='false',
        PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',YOLO_CONFIG_DIR=str(directory/'model-settings'))
    try:
        db1_enabled = os.environ.get('T1_CONSOLE_DB1') == 'fixture'
        if db1_enabled:
            status['delivery'].update(postgres_commit='pending', redis_ack='not_attempted')
        if stop.is_set():
            final_state, reason = 'stopped', 'controlled_stop'
            return 0
        from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
        from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
        from vehicle.common.observation_evidence import RecognitionIdentity
        from vehicle.common.real_video.input import MP4Input
        from vehicle.common.real_video.rtsp import RTSPBounds, private_camera, InputStopped, InputFault
        from vehicle.common.real_video.latest_rtsp import LatestRTSPInput
        from vehicle.common.real_video.local import LocalDetector, pixel_operations, fingerprint
        from vehicle.common.real_video.plate_local import NativePlateDetector, detector_artifact
        from vehicle.common.real_video.plate_engine import CombinedEngine, VideoPlateLimits
        from vehicle.common.engine_plate import native_access
        from vehicle.common.engine_plate_local import LocalPlateBinding, artifact
        from vehicle.common.redis_scalar.worker import load_request_codec, Worker
        from vehicle.common.redis_scalar.journal import Journal
        from vehicle.common.plate_requests.worker import RequestWorker
        from vehicle.common.plate_requests.host import V2Publisher
        from vehicle.common.plate_metadata import PlateMetadata
        from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
        from vehicle.common.manufacturer.engine import ManufacturerLedger
        from vehicle.common.manufacturer.local import LocalRuntime
        from vehicle.common.manufacturer.streams import ReviewCodec, ReviewWorker, publish, SOURCE, RESULT
        from vehicle.common.console_projection import project_track
        from vehicle.common.console_crops import ConsoleCrops
        from vehicle.common.vehicle_observation import RunEnd
        import cv2
        import torch
        torch.set_num_threads(1);cv2.setNumThreads(1)
        if stop.is_set():raise InputStopped('controlled_stop')
        org, namespace_id = UUID('12345678-1234-4234-8234-123456789012'), UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb')
        paths = model_paths()
        now = datetime.now(timezone.utc)
        model = paths['vehicle_model']
        context = RunContext(org,CameraRegistration(org,'console-'+config['run_id'],None,'Africa/Tunis',now,None),
            config['run_id'],'file' if config['source_kind']=='mp4' else 'rtsp',now,'vehicle-console-v1',
            ResolutionInfo(None,None,'vehicle',ModelRegistration(None,'local-yolo26-sha256:'+fingerprint(model)['sha256'],1,'detect'),
                           None,'console backend execution; no production DB1 registration',False),'development')
        clock = FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),monotonic_clock=time.monotonic,policy=ClockPolicy())
        deadline = time.monotonic()+config['max_seconds'] if config.get('max_seconds') else float('inf')
        source = MP4Input(config['source'],clock) if config['source_kind']=='mp4' else LatestRTSPInput(
            private_camera(config['source']),clock,deadline=deadline,stop=stop,
            every=every, directory=directory,
            bounds=RTSPBounds(open_seconds=12, transport=config.get('rtsp_transport','tcp')))
        save(phase='Opening input')
        source.__enter__()
        if stop.is_set():raise InputStopped('controlled_stop')
        maker_runtime = LocalRuntime(os.environ.get('T1_MANUFACTURER_LOCK',ROOT/'vehicle/common/manufacturer/assets.json'),
                                     timeout=30,stop=stop) if config['manufacturer'] else None
        ledger = ManufacturerLedger(enabled=config['manufacturer'],runtime=maker_runtime,
                                    journal=directory/'manufacturer.sqlite',stop=stop)
        crops = ConsoleCrops(directory)
        crops.bind_manufacturer(ledger, maker_runtime)
        fps = source.metadata['fps']
        test_profile = os.environ.get('WAYCON_CONSOLE_TEST_PROFILE') == 'empty'
        if test_profile and db1_enabled:raise RuntimeError('fixture_profile_requires_console_only')
        if test_profile:
            from vehicle.common.tests.console_empty_runtime import EmptyVehicle, EmptyPlate
            vehicle, plate_detector, bindings = EmptyVehicle(tracked=config['source_kind']=='rtsp'), EmptyPlate(), {}
        else:
            with native_access():
                vehicle = LocalDetector(model,paths['vehicle_tracker'],
                                        device=console_device(),frame_rate=fps,threads=1)
            plate_detector = NativePlateDetector(detector_artifact(paths['plate_model'],
                                        paths['plate_tracker'],frame_rate=fps),frame_rate=fps)
            bindings = {kind:LocalPlateBinding(artifact(kind,path)) for kind,path in (
                ('ocr',paths['ocr_dir']), ('detect',paths['marker_model']))}
        estimate, quality = pixel_operations()
        engine = CombinedEngine(context,clock,vehicle,plate_detector,bindings,
            algorithm=RecognitionIdentity('t1-local-hsv-console',1,'classify'),quality_algorithm='t1-colour-quality',
            plate_quality_algorithm='t1-plate-crop-quality',estimate=estimate,quality_fn=quality,manufacturer=ledger,
            limits=VideoPlateLimits(streaming=True,max_frames=None,max_seconds=None,max_api_calls=None,max_raw_total=None))
        crops.bind_plate(engine.plate)
        codec = load_request_codec(org,namespace_id)
        redis_server = os.environ.get('T1_REDIS_SERVER') or shutil.which('redis-server')
        if not redis_server:raise RuntimeError('missing_redis_server')
        owned = OwnedRedis(redis_server).__enter__()
        namespace = 'console-'+config['run_id'].replace('-','')[:24]
        workers = [Worker(owned.client,codec=codec.v1.v0,namespace=namespace,role='colour',consumer='console-colour'),
                   Worker(owned.client,codec=codec.v1,namespace=namespace,role='aggregator_plate',consumer='console-aggregator'),
                   RequestWorker(owned.client,codec=codec,namespace=namespace,consumer='console-plate')]
        review = ReviewWorker(owned.client,codec=ReviewCodec(codec.v1.v0),namespace=namespace,consumer='console-make')
        workers.append(review)
        for worker in workers:worker.setup()
        journal = Journal(owned.client,namespace=namespace,trusted_org=org,role='plate_requests',consumer='console-engine')
        publisher = V2Publisher(owned.client,journal,codec,engine)
        tracker = getattr(getattr(vehicle,'target',None),'tracker',None)
        initial_tracking = dict(frame_id=getattr(tracker,'frame_id',0),
            tracked=len(getattr(tracker,'tracked_stracks',())),lost=len(getattr(tracker,'lost_stracks',())),scope=config['run_id'])
        review_published = {}
        from collections import deque
        runtime_frames = deque(maxlen=128)
        def public_rows():
            return [rows[s] for s in sorted(rows)]
        def update_review_delivery():
            if not config['manufacturer']:return
            states = review_acknowledgments(owned.client,review.journal,
                {sid:(entry,rows[sid]['manufacturer']['request_id']) for sid,entry in review_published.items()})
            for sid,state in states.items():rows[sid]['delivery']['review_ack'] = state
            status['delivery']['review_ack'] = ('acknowledged' if states and
                all(state=='acknowledged' for state in states.values()) else 'pending')
        def preview(packet, vehicles=()):
            pixels = packet.pixels.copy()
            for observation in vehicles:
                x1,y1,x2,y2 = observation.bbox
                cv2.rectangle(pixels,(x1,y1),(x2,y2),(145,230,180),2)
                cv2.putText(pixels,f'#{observation.service_track_id}',(max(0,x1),max(22,y1-8)),
                            cv2.FONT_HERSHEY_SIMPLEX,.8,(145,230,180),2,cv2.LINE_AA)
            ok, image = cv2.imencode('.jpg',pixels,[cv2.IMWRITE_JPEG_QUALITY,80])
            if ok:
                temp = directory/'preview.tmp'
                temp.write_bytes(image.tobytes());os.chmod(temp,0o600);temp.replace(directory/'preview.jpg')
                save(preview_revision=status['preview_revision']+1)
        def capture_closed():
            crops.diagnostics(engine.plate)
            for sid,closed in engine.pending.items():
                originals.setdefault(sid,closed)
                if sid not in rows:
                    rows[sid],_ = project_track(closed,codec.v1.v0,incomplete='stopped' if stop.is_set() else 'pending')
                    if db1_enabled:rows[sid]['delivery']['postgres_commit']='pending'
                if config['manufacturer'] and sid not in review_published:
                    raw = review.codec.encode(closed)
                    entry = f'{len(review_published)+1}-0'
                    publish(owned.client,review.journal,raw,entry)
                    review_published[sid] = entry
            metadata_turn(review)
            update_review_delivery()
            save(rows=public_rows())
        save(status='running',phase='Processing shared vehicle frames')
        def counts():
            value = source.counts() if config['source_kind']=='rtsp' else dict(decoded_frames=source.decoded,sampling_skips=skips,overload_drops=0)
            return dict(value,frames=source.decoded,analyzed_frames=analyzed)
        while True:
            if stop.is_set():reason='controlled_stop';break
            if time.monotonic()>=deadline:reason='time_limit';break
            packet = source.read()
            if packet is None:reason='eof';break
            if stop.is_set():reason='controlled_stop';break
            if config['source_kind']=='mp4' and packet.receipt.frame_index % every:
                skips += 1
                if skips % 100 == 0:save(**counts())
                continue
            # Preview is derived from the same Input; never a browser decoder or
            # another backend video reader. First frame is visible before VLM work.
            preview(packet)
            vehicle.source_index = plate_detector.source_index = packet.receipt.frame_index
            if test_profile:time.sleep(float(os.environ.get('WAYCON_CONSOLE_TEST_DELAY','0')))
            engine.process(packet)
            analyzed += 1
            # Disk evidence records actual successful Engine handoffs. It never
            # reconstructs selected indexes from a total or retains frame pixels.
            with (directory/'analyzed-source.jsonl').open('a') as evidence:
                evidence.write(json.dumps(dict(frame_index=packet.receipt.frame_index,
                    utc=packet.receipt.utc.isoformat(),monotonic=packet.receipt.monotonic,
                    media_pts=packet.receipt.media_pts,connection_id=config['run_id']))+'\n')
            runtime_frames.append(dict(frame=packet.receipt.frame_index,
                vehicle_rows=len(engine.colour.detector.rows),
                observed_vehicles=len(engine.colour.pipeline.observation_frame.vehicles)))
            atomic_json(directory/'native-runtime.json', dict(source_fps=fps, imported_opencv=cv2.__version__,
                vehicle=vehicle.placement, frames=list(runtime_frames), initial_tracking=initial_tracking, evidence_admission=dict(
                    colour_not_admitted=engine.colour.evidence_not_admitted,plate_not_admitted=engine.plate.evidence_not_admitted),
                vehicle_prediction_calls=vehicle.prediction_calls, vehicle_forward_calls=vehicle.forward_calls))
            preview(packet,engine.colour.pipeline.observation_frame.vehicles)
            save(**counts())
            capture_closed()
        source.close()
        save(**counts(),end_reason=reason)
        engine.finish(RunEnd.END_OF_INPUT if reason=='eof' else RunEnd.SERVICE_STOP,trigger=reason)
        save(status='settling',phase='Joining Plate, Colour and manufacturer decisions')
        capture_closed()
        settle_deadline = float('inf')
        while engine.pending and not stop.is_set():
            if time.monotonic()>=settle_deadline:raise RuntimeError('settlement_deadline')
            for worker in workers:metadata_turn(worker)
            update_review_delivery()
            for sid,closed in tuple(engine.pending.items()):
                if publisher.publish(closed):
                    plan = publisher.plans[sid]
                    plate = PlateMetadata().evaluate(closed.owner.lifetime,plan.transcript).result
                    crops.plate(sid, plate)
                    row,bundle = project_track(closed,codec.v1.v0,plate=plate)
                    row['delivery'].update(rows[sid]['delivery'])
                    rows[sid] = row
                    fd = os.open(directory/f'delivery-{sid}.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                    with os.fdopen(fd,'wb') as output:output.write(bundle.payload.canonical)
                    engine.acknowledge(sid)
            save(rows=public_rows())
            time.sleep(.01)
        # Confirm the real canonical Redis delivery, independent of review ACK.
        for _ in range(64):
            progress = [metadata_turn(worker) for worker in workers]
            if not any(progress):break
        update_review_delivery()
        if config['manufacturer'] and not rows:status['delivery']['review_ack']='not_attempted'
        deliveries = owned.client.xlen(journal.key('db1.delivery.v1'))
        status['counters'] = dict(vehicle_detector_calls=engine.audit.calls.get('vehicle',0),
            plate_detector_calls=engine.audit.calls.get('plate',0),manufacturer_calls=ledger.calls,
            plate_model_calls={kind:dict(binding.calls) for kind,binding in bindings.items()},
            tracks=len(rows),canonical_redis_deliveries=deliveries,
            manufacturer_review_results=owned.client.xlen(review.journal.key(RESULT)))
        if stop.is_set():
            final_state='stopped'
            for row in rows.values():
                if row['state']!='settled':row['plate']['status']='stopped';row['state']='incomplete'
        elif len(rows)!=deliveries:raise RuntimeError('canonical_delivery_incomplete')
        if reason=='time_limit':final_state='partial'
        if db1_enabled:
            save(phase='Committing disposable DB1 fixtures', rows=public_rows())
            source_media = None
            if config['source_kind']=='mp4':
                source_media = {k:v for k,v in fingerprint(config['source']).items() if k!='path'}
                source_media.update(width=int(source.metadata['width']), height=int(source.metadata['height']))
            published = set()
            for _,fields in owned.client.xrange(journal.key('db1.delivery.v1')):
                raw = fields[b'json']
                sid = codec.v1.decode(raw).life.service_track_id
                published.add(sid)
                fd = os.open(directory/f'db1-wire-{sid}.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                with os.fdopen(fd,'wb') as retained:retained.write(raw)
            for sid,row in rows.items():
                state = 'pending' if sid in published else 'not_attempted'
                row['delivery'].update(postgres_commit=state,redis_ack=state)
            atomic_json(directory/'db1-control.json', dict(org=str(org), identity_namespace=str(namespace_id),
                redis_socket=str(owned.socket), namespace=namespace, deliveries=deliveries, source_media=source_media))
            db1_child = subprocess.Popen([sys.executable, '-B', str(ROOT/'scripts/vehicle_console_db1.py'), str(directory)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def db1_progress():
                path = directory/'db1-progress.json'
                if not path.exists():return
                progress = json.loads(path.read_text())
                for sid,row in rows.items():
                    row['delivery'].update(progress['tracks'].get(str(sid), {}))
                for key in ('postgres_commit','redis_ack'):
                    values = [row['delivery'][key] for sid,row in rows.items() if sid in published]
                    target = 'committed' if key=='postgres_commit' else 'acknowledged'
                    status['delivery'][key] = target if values and all(v==target for v in values) else ('no_deliveries' if not values else 'pending')
                save(rows=public_rows())
            # Stop never kills a writer mid-transaction or releases its run slot.
            while db1_child.poll() is None:
                db1_progress()
                time.sleep(.03)
            db1_progress()
            if db1_child.returncode:
                error_code='db1_delivery_failed'
                raise RuntimeError('db1_delivery_failed')
            status['counters']['db1_committed_tracks'] = sum(row['delivery']['postgres_commit']=='committed' for row in rows.values())
            status['counters']['db1_acknowledged_tracks'] = sum(row['delivery']['redis_ack']=='acknowledged' for row in rows.values())
        save(rows=public_rows(),counters=status['counters'])
    except Exception as error:
        import traceback
        from vehicle.common.real_video.rtsp import InputFault
        atomic_json(directory/'failure.json',dict(type=type(error).__name__,
            pipeline_code=getattr(error,'code',None),
            input_code=str(error) if isinstance(error, InputFault) else None,
            frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name)
                    for f in traceback.extract_tb(error.__traceback__)]))
        from vehicle.common.real_video.rtsp import InputStopped
        if isinstance(error,InputStopped) or stop.is_set():final_state='stopped'
        else:
            final_state='failed'
            # Never stringify decoder/native exceptions or request configuration.
            error_code=error_code or ('input_unavailable' if source is not None and engine is None else 'backend_execution_failed')
        reason = 'controlled_stop' if stop.is_set() else ('camera_outage' if config['source_kind']=='rtsp' and isinstance(error,InputFault) else 'error')
    finally:
        if db1_child is not None:db1_child.wait()
        cleanup = dict(decoder_closed=True,models_closed=True,redis_removed=True,
                       private_camera_removed=True,native_children_exited=True)
        if source is not None:
            try:source.close()
            except Exception:cleanup['decoder_closed']=False
        if engine is not None:
            for life in engine.plate.lives.values():
                if life.adapter is not None:
                    try:life.adapter.stop();life.adapter.cleanup(discard=True)
                    except Exception:cleanup['models_closed']=False
            if engine.audit.active is None:
                for resource in engine.plate.buffers:resource.release()
            try:engine.close();cleanup['models_closed'] = cleanup['models_closed'] and engine.closed
            except Exception:cleanup['models_closed']=False
        elif vehicle is not None:
            try:vehicle.close()
            except Exception:cleanup['models_closed']=False
        if ledger is not None:ledger.close()
        if maker_runtime is not None:cleanup['native_children_exited']=maker_runtime.cleaned
        for worker in workers:
            if worker.journal.token:
                try:worker.journal.release()
                except Exception:pass
        if owned is not None:
            try:owned.__exit__(None,None,None);cleanup['redis_removed']=owned.cleaned
            except Exception:cleanup['redis_removed']=False
        if private_camera_dir is not None:
            try:shutil.rmtree(private_camera_dir)
            except FileNotFoundError:pass
            except Exception:cleanup['private_camera_removed']=False
        watcher_done.set()
        if crops is not None:
            try:crops.close()
            except Exception:cleanup['models_closed']=False
        if not all(cleanup.values()):final_state,error_code='failed','cleanup_incomplete'
        if source is not None:
            status.update(frames=source.decoded,decoded_frames=source.decoded,analyzed_frames=analyzed,sampling_skips=skips)
            if config['source_kind']=='rtsp' and hasattr(source,'counts'):status.update(source.counts())
        save(status=final_state,phase={'completed':'Full video complete' if config['source_kind']=='mp4' else 'Stream ended',
             'partial':'Partial run: timeout','stopped':'Stopped safely: partial run','failed':'Run failed: partial run'}[final_state],
             end_reason=reason,full_video_complete=(config['source_kind']=='mp4' and reason=='eof' and final_state=='completed'),
             error_code=error_code,ended_at=timestamp(),cleanup=cleanup,stop_requested=stop.is_set())
    return 0 if final_state in {'completed','stopped','partial'} else 1


if __name__=='__main__':
    launch_fd = os.environ.pop('T1_CONSOLE_LAUNCH_FD', None)
    if launch_fd is not None:
        with os.fdopen(int(launch_fd), 'rb') as gate:
            if gate.read(1) != b'1':
                raise SystemExit(1)
    raise SystemExit(run(sys.argv[1]))
