"""Internal CUDA worker for the explicit owned-resource RTSP smoke host."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('camera-config', 'source-root', 'ocr-cache', 'redis-server', 'output', 'control-dir'):
        p.add_argument('--' + key, required=True, type=Path)
    p.add_argument('--tunis-mode', required=True, choices=('legacy_whole', 'marker_first'))
    p.add_argument('--rtsp-transport',required=True,choices=('tcp','udp'))
    for key in ('max-frames', 'max-crops', 'reconnects'):
        p.add_argument('--' + key, required=True, type=int)
    for key in ('max-seconds', 'open-seconds', 'read-seconds'):
        p.add_argument('--' + key, required=True, type=float)
    a = p.parse_args(argv)
    a.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', YOLO_AUTOINSTALL='false', YOLO_OFFLINE='true',
        OPENROUTER_ENABLED='false', COLOR_AGENT_ENABLED='false', FLAGS_allocator_strategy='auto_growth',
        YOLO_CONFIG_DIR=str(a.output/'runtime-settings'), OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    # Only the spawned Input decoder receives network permission. Native GPU
    # libraries and Redis IPC in this interpreter have no external socket route.
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def unix_connect(self, address):
        if self.family != socket.AF_UNIX:
            raise RuntimeError('network_disabled_in_rtsp_engine')
        return connect(self, address)
    def unix_connect_ex(self, address):
        if self.family != socket.AF_UNIX:
            raise RuntimeError('network_disabled_in_rtsp_engine')
        return connect_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = unix_connect, unix_connect_ex
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    report = dict(status='BLOCKED', frames=0, db1_records=0, decoder_opens=0)
    models = owned = detector = None
    started = time.monotonic()
    try:
        from vehicle.common.real_video.rtsp import private_camera, RTSPBounds
        from vehicle.common.real_video.rtsp_host import run_connections
        from vehicle.common.real_video.rtsp_v3 import V3RTSPSession
        from vehicle.common.real_video.rtsp_delivery import projection_hook
        from vehicle.common.mohamed_lpr_native import Assets, CudaModels, CudaPlateDetector, cuda_preflight, file_hash
        from vehicle.common.mohamed_lpr_engine import BindingError
        from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis, client_preflight
        from vehicle.common.engine_plate import native_access
        camera = private_camera(a.camera_config)
        client = client_preflight()
        owned = OwnedRedis(a.redis_server).__enter__()
        if not owned.client.ping():
            raise RuntimeError('owned_redis_unreachable')
        if not a.source_root.is_dir():
            raise BindingError('approved_source_checkout_unavailable')
        assets = Assets(a.source_root, a.ocr_cache)
        assets.runtime = cuda_preflight()
        import torch
        import cv2
        # Distribution metadata and imported code can differ on a shared host.
        # Preserve both in this route's actual execution provenance.
        assets.runtime['loaded_opencv']=dict(version=cv2.__version__,
            init_sha256=file_hash(Path(cv2.__file__)),
            build_sha256=__import__('hashlib').sha256(cv2.getBuildInformation().encode()).hexdigest())
        torch.set_num_threads(1); cv2.setNumThreads(1)
        models = CudaModels(assets)
        detector = CudaPlateDetector(assets, frame_rate=None)
        with native_access():
            for role in ('category_ocr', 'visual', 'registration', 'marker'):
                models.initialize(role)
            detector.load_backend()
        report['preopen_checks'] = dict(redis_import=True, redis_version=client.__version__, redis_reachable=True,
            cuda=True, artifacts=True, source_head=assets.lock['source_head'], decoder_opens=0,
            artifact_lock_sha256=file_hash(ROOT/'vehicle/common/mohamed_lpr_assets.json'),
            reference_index=asdict(assets.index_identity), runtime=assets.runtime)
        first = [detector]
        sessions = []
        def factory(context, clock, fps, deadline, stop):
            # First verified backend is reused; every subsequent connection gets
            # new plate and vehicle trackers. No reset-by-assumption API.
            target = first.pop() if first else None
            session = V3RTSPSession(context, clock, fps, deadline, stop, assets=assets, models=models, owned=owned,
                tunis_mode=a.tunis_mode, max_frames=a.max_frames, max_seconds=a.max_seconds, max_crops=a.max_crops,
                detector=target, delivery_hook=lambda **kw: projection_hook(a.control_dir, stop, deadline, **kw))
            sessions.append(session)
            return session
        result = run_connections(camera, factory, max_frames=a.max_frames, max_seconds=a.max_seconds,
            reconnects=a.reconnects, bounds=RTSPBounds(a.open_seconds, a.read_seconds,transport=a.rtsp_transport), stop=stop)
        report.update(result, tunis_mode=a.tunis_mode,
                      limits=dict(frames=a.max_frames, seconds=a.max_seconds, crops_per_connection=a.max_crops,
                                  reconnects=a.reconnects, open_seconds=a.open_seconds, read_seconds=a.read_seconds))
        report['limits']['transport']=a.rtsp_transport
        report['limits'].update(frame_slots=1,frame_bytes=1920*1080*3,
            socket_buffer_bytes=65536,probe_bytes=1048576,analysis_microseconds=1000000,
            crop_bytes=8*1024*1024,retained_crop_bytes=32*1024*1024)
        report['db1_records'] = sum(c.get('db1_records', 0) for c in result['connections'])
        report['model_calls'] = dict(models.calls)
        report['actual_provenance'] = {r: asdict(models.expected(r)) for r in models.calls}
        report['model_placements'] = models.placements
        report['native_active'] = sum(s.live.pending_native for s in sessions if s.live is not None)
    except BaseException as error:
        report.update(status='FAIL' if report.get('preopen_checks') else 'BLOCKED',
                      failure_type=type(error).__name__, reason='rtsp_worker_failure_details_suppressed')
        # Errors may carry camera URLs; only known bounded symbolic codes leave.
        from vehicle.common.real_video.rtsp import InputFault
        from vehicle.common.mohamed_lpr_engine import BindingError
        if isinstance(error, (InputFault, BindingError)) and __import__('re').fullmatch('[a-z0-9_]{1,96}', str(error)):
            report['reason'] = str(error)
    finally:
        active = report.get('native_active', 0)
        if 'sessions' in locals():
            active = sum(s.live.pending_native for s in sessions if s.live is not None)
        report['native_active'] = active
        if not active:
            from vehicle.common.engine_plate import native_access
            with native_access():
                if models is not None: models.close()
                if detector is not None: detector.close()
        if owned is not None and not active:
            owned.__exit__(None, None, None)
            report['owned_redis_cleaned'] = owned.cleaned
        elif active:
            # Retain the Engine process/crops and owned Redis. The host will
            # report retained resources rather than pretending recovery.
            report['resources_retained'] = True
        report['elapsed_seconds'] = time.monotonic()-started
        (a.output/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        print(json.dumps({k:report[k] for k in ('status','frames','reconnects','db1_records','native_active') if k in report}))
        if active:
            while True: time.sleep(1)
    return 0 if report['status']=='PASS' else 2 if report['status']=='BLOCKED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
