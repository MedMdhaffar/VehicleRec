"""Explicit bounded MP4 -> target Engine -> local CUDA VLM -> Redis review.

Manufacturer DB1 delivery remains blocked by approved registration/mapping gaps.
No camera, source runner, shared model server or production database is used.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-manufacturer', action='store_true', required=True)
    for name in ('assets','video','detector','tracker','redis-server','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--max-frames',type=int,required=True,choices=range(1,9))
    parser.add_argument('--max-seconds',type=float,required=True)
    parser.add_argument('--inference-timeout',type=float,default=30)
    args = parser.parse_args()
    if not 0 < args.max_seconds <= 300:
        parser.error('max-seconds must be positive and <=300')
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ.update(YOLO_OFFLINE='true', YOLO_AUTOINSTALL='false', COLOR_AGENT_ENABLED='false',
                      YOLO_CONFIG_DIR=str(args.output/'runtime-settings'), PYTHONDONTWRITEBYTECODE='1')
    import socket
    original_connect = socket.socket.connect
    def local_only(self,address):
        if self.family != socket.AF_UNIX:
            raise RuntimeError('network disabled during manufacturer review')
        return original_connect(self,address)
    socket.socket.connect = socket.socket.connect_ex = local_only
    from vehicle.common.manufacturer.engine import ManufacturerLedger
    from vehicle.common.manufacturer.local import LocalRuntime
    from vehicle.common.manufacturer.streams import ReviewCodec, ReviewWorker, SOURCE, RESULT, DB1_GAP, publish
    from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
    from vehicle.common.real_video.engine import Engine
    from vehicle.common.real_video.host import run_capture, reference_bundle
    from vehicle.common.real_video.input import MP4Input
    from vehicle.common.real_video.local import LocalDetector, pixel_operations, fingerprint
    from vehicle.common.redis_scalar.worker import load_codec
    from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
    from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
    from vehicle.common.observation_evidence import RecognitionIdentity
    report = dict(status='starting', command=sys.argv, model_calls=0, db1_status='review_only',db1_gap=DB1_GAP,
                  db1_rows=0, provider_calls=0, bindings=[], cleanup_errors=[])
    source = engine = detector = runtime = ledger = worker = None
    started = time.monotonic()
    owned = OwnedRedis(args.redis_server)
    exit_code = 1
    try:
        runtime = LocalRuntime(args.assets, timeout=args.inference_timeout)
        ledger = ManufacturerLedger(enabled=True,runtime=runtime,journal=args.output/'attempts.sqlite')
        report['artifacts'] = {name:fingerprint(path) for name,path in
                               [('video',args.video),('detector',args.detector),('tracker',args.tracker),('lock',args.assets)]}
        report['manufacturer_provenance'] = runtime.provenance
        now = datetime.now(timezone.utc)
        org = UUID('12345678-1234-4234-8234-123456789012')
        context = RunContext(org,CameraRegistration(org,'local-manufacturer-mp4',None,'Africa/Tunis',now,None),
            'manufacturer-'+uuid4().hex,'file',now,'manufacturer-review-v1',
            ResolutionInfo(None,None,'vehicle',ModelRegistration(None,'local-vehicle-sha256:'+report['artifacts']['detector']['sha256'],1,'detect'),
                           None,'local bounded MP4 manufacturer review only',False),'development')
        clock = FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),
                                  monotonic_clock=time.monotonic,policy=ClockPolicy())
        source = MP4Input(args.video,clock)
        # Opening here learns the real FPS; run_capture reuses this same Input.
        source.__enter__()
        report['source_metadata'] = source.metadata
        detector = LocalDetector(args.detector,args.tracker,device='cuda:0',frame_rate=source.metadata['fps'],threads=1)
        estimate,quality = pixel_operations()
        engine = Engine(context,clock,detector,algorithm=RecognitionIdentity('t1-local-hsv-review',1,'classify'),
                        quality_algorithm='t1-local-colour-quality',estimate=estimate,quality=quality,
                        max_vehicles=16,manufacturer=ledger)
        codec = ReviewCodec(load_codec(org,UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb')))
        owned.__enter__()
        worker = ReviewWorker(owned.client,codec=codec,namespace='manufacturer-'+uuid4().hex[:16])
        worker.setup()
        before = {}
        def sink(closed):
            raw = codec.encode(closed)
            joined = codec.join(raw)
            if joined.delivery.digest != reference_bundle(codec.base,closed).digest:
                raise RuntimeError('manufacturer changed existing parent/colour facts')
            evidence = json.loads(closed.manufacturer_json)
            binding = evidence['binding']
            report['bindings'].append(dict(track_id=closed.owner.service_track_id, raw_track_id=closed.owner.raw_track_id,
                run_id=context.run_id, frame=binding['receipt']['frame_index'] if binding['receipt'] else None,
                request_id=evidence['request_id'], crop_sha256=binding['crop_sha256'], frame_sha256=binding['frame_sha256'],
                status=joined.manufacturer_review.status,make=joined.manufacturer_review.make,
                seconds=evidence['seconds'], bbox=binding['bbox']))
            sid = f'{len(report["bindings"])}-0'
            publish(owned.client,worker.journal,raw,sid)
            worker.step()
            # Identical delivery and a second transport entry must not infer or emit again.
            calls = ledger.calls
            publish(owned.client,worker.journal,raw,sid)
            before[closed.owner.service_track_id] = (raw,calls)
        report['capture'] = run_capture(source,engine,sink,max_frames=args.max_frames,max_seconds=args.max_seconds)
        # Redis transport IDs must increase globally, including duplicate tests.
        for index,(raw,calls) in enumerate(before.values(),101):
            publish(owned.client,worker.journal,raw,f'{index}-0')
            worker.step()
        report['model_calls'] = ledger.calls
        report['outcomes'] = dict(Counter(b['status'] for b in report['bindings']))
        report['detector'] = detector.placement
        report['detector_calls'] = detector.prediction_calls
        report['gpu_memory'] = runtime.metrics
        report['decoder_opens'] = source.opens
        report['redis'] = dict(results=owned.client.xlen(worker.journal.key(RESULT)),
                               pending=owned.client.xpending(worker.journal.key(SOURCE),worker.journal.group)['pending'])
        if not ledger.calls or report['outcomes'].get('failed',0) or report['outcomes'].get('timed_out',0):
            raise RuntimeError('manufacturer real model did not complete')
        if report['redis'] != dict(results=len(before),pending=0) or source.opens != 1:
            raise RuntimeError('manufacturer handoff or decoder count mismatch')
        report['status'],exit_code = 'passed',0
    except Exception as error:
        report.update(status='blocked' if engine is None else 'failed',failure_type=type(error).__name__,
                      failure_code=getattr(error,'code','MANUFACTURER_SMOKE_FAILED'))
    finally:
        for obj in (source, engine if engine is not None else detector, ledger):
            if obj is None:continue
            try:obj.close()
            except Exception as error:report['cleanup_errors'].append(type(error).__name__)
        if runtime is not None:
            report['owned_native_cleaned'] = runtime.cleaned
            report['gpu_memory'] = runtime.metrics
            report['model_calls'] = ledger.calls if ledger else 0
        if worker is not None and worker.journal.token:
            try:worker.journal.release()
            except Exception as error:report['cleanup_errors'].append(type(error).__name__)
        if owned.process is not None:
            try:owned.__exit__(None,None,None)
            except Exception as error:report['cleanup_errors'].append(type(error).__name__)
            report['owned_redis_cleaned'] = owned.cleaned
        if report['cleanup_errors']:
            report['status'],exit_code = 'failed',1
        report['elapsed_seconds'] = time.monotonic()-started
        (args.output/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
        print(json.dumps({k:report[k] for k in ('status','model_calls','db1_status')}))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
