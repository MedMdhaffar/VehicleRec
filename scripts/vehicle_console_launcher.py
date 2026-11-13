"""Foreground owner for the loopback WAYCON demo. Never opens an input."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
URL = 'http://127.0.0.1:5173/'
API = 'http://127.0.0.1:8002/'


def tell(message):
    print(message, file=sys.stderr, flush=True)


def get(url, *, method='GET'):
    with urlopen(Request(url, method=method), timeout=7) as response:
        return json.load(response)


def listening(port):
    with socket.socket() as probe:
        return probe.connect_ex(('127.0.0.1', port)) == 0


def database_matches(state, *, port=None):
    if state.get('mode')=='console-only':return True
    database=state.get('database',{})
    owned=os.environ.get('WAYCON_POSTGRES_MODE','owned')=='owned'
    return (bool(database.get('launcher_owned',False))==owned and database.get('host')=='127.0.0.1'
            and (port is None or database.get('port')==port))


def require_loopback_listener(port):
    for name in ('tcp', 'tcp6'):
        for row in Path('/proc/net/'+name).read_text().splitlines()[1:]:
            fields = row.split()
            address, value = fields[1].split(':')
            if fields[3]=='0A' and int(value,16)==port and address not in (
                    '0100007F', '00000000000000000000000001000000'):
                raise RuntimeError('An existing web listener is not loopback-only; it was left untouched.')


def wait_ready(check, children):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError('An owned service exited; inspect the private launcher logs.')
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(.15)
    raise RuntimeError('Service readiness timed out; inspect the private launcher logs.')


def preflight(directory, mode):
    """Synthetic native inference, owned Redis and disposable DB1 proof; no input."""
    from vehicle.common.console_runs import atomic_json
    from vehicle.common.console_stack import database_probe, model_paths, console_device
    from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
    from vehicle.common.real_video.local import fingerprint, LocalDetector
    from vehicle.common.engine_plate_local import artifact
    from vehicle.common.real_video.plate_local import detector_artifact
    from vehicle.common.manufacturer.local import LocalRuntime
    report = dict(status='checking', mode=mode, failures=[])
    def check(name, operation):
        try:
            report[name]=operation()
        except Exception as error:
            from vehicle.common.console_stack import StackUnavailable
            message = str(error) if isinstance(error,StackUnavailable) else (
                name+' check failed ('+type(error).__name__+'). Check its configured files/runtime; see prerequisites.json.')
            report['failures'].append(message)
            tell(message)
    tell('Checking the existing CUDA device and local model artifacts…')
    paths=model_paths()
    def cuda():
        import torch
        import numpy as np
        from vehicle.common.console_stack import StackUnavailable
        from vehicle.common.engine_plate import native_access
        device=console_device()
        if not torch.cuda.is_available():raise StackUnavailable('CUDA unavailable: install a compatible CUDA PyTorch runtime; CPU fallback is disabled.')
        selected=torch.device(device)
        if selected.index>=torch.cuda.device_count():raise StackUnavailable('Configured CUDA device is unavailable: '+device)
        name=torch.cuda.get_device_name(selected)
        expected=os.environ.get('WAYCON_EXPECTED_GPU')
        if expected and expected.lower() not in name.lower():raise StackUnavailable('Configured expected GPU does not match the selected local device: '+name)
        torch.set_num_threads(1)
        detector=None
        try:
            with native_access():
                detector=LocalDetector(paths['vehicle_model'],paths['vehicle_tracker'],device=device,frame_rate=25,threads=1)
                detector.process(np.zeros((640,640,3),dtype=np.uint8))
                torch.cuda.synchronize(selected)
                if detector.placement['parameter_devices']!=[device] or not detector.forward_calls:
                    raise StackUnavailable('Vehicle inference did not execute on the configured CUDA device.')
                return dict(device=device,name=name,version=torch.version.cuda,torch=torch.__version__,
                    capability=list(torch.cuda.get_device_capability(selected)),
                    parameter_devices=detector.placement['parameter_devices'],native_forward_calls=detector.forward_calls)
        except StackUnavailable:raise
        except Exception:
            raise StackUnavailable('CUDA model inference failed on '+name+'. Check the CUDA PyTorch/torchvision build for this GPU architecture; no CPU fallback was used.') from None
        finally:
            if detector is not None:detector.close()
    check('cuda',cuda)
    check('vehicle',lambda:fingerprint(paths['vehicle_model']))
    check('vehicle_tracker',lambda:fingerprint(paths['vehicle_tracker']))
    check('plate',lambda:detector_artifact(paths['plate_model'],paths['plate_tracker'],frame_rate=25).material())
    check('ocr',lambda:artifact('ocr',paths['ocr_dir']).material())
    check('marker',lambda:artifact('detect',paths['marker_model']).material())
    from vehicle.common.console_prerequisites import inventory
    report['manufacturer']=inventory(mode)['manufacturer']
    if report['manufacturer']['available']:
        try:
            maker=LocalRuntime(os.environ.get('T1_MANUFACTURER_LOCK',ROOT/'vehicle/common/manufacturer/assets.json'))
            report['manufacturer'].update(artifacts_verified=True,provenance=maker.provenance)
        except Exception:
            report['manufacturer'].update(available=False,error='Manufacturer artifact verification failed; enable only with a verified local lock.')
    tell('Checking the run-owned Redis lifecycle (private Unix socket, no TCP broker)…')
    def redis_check():
        with OwnedRedis(os.environ['T1_REDIS_SERVER']) as redis:
            assert redis.client.ping()
            assert redis.client.config_get('port')['port'] == '0'
            result=dict(version=redis.client.info('server')['redis_version'],run_owned=True)
        assert redis.cleaned
        return dict(result,startup_probe_removed=True)
    check('redis',redis_check)
    if mode == 'fixture':
        tell('Checking PostgreSQL and the existing disposable DB1 fixture registrations…')
        check('database',database_probe)
        from vehicle.common.redis_scalar.worker import load_request_codec
        from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
        from vehicle.common.tests.test_db1_consumer import delivery_fixture
        from vehicle.common.mohamed_lpr_test_database import OwnedDeliveryDatabase
        codec = load_request_codec(ORG, IDENTITY_NAMESPACE)
        raw = delivery_fixture(codec)
        def fixture_check():
            # A rejected target must never reach the provisioning fixture.
            database_probe()
            with OwnedDeliveryDatabase(codec.v1, raw) as db:
                consumer=db.consumer();entry=consumer.prepare(raw)
                with db.fixture.connect() as connection:
                    consumer.adapter.deliver(connection,entry,tenant=db.fixture.tenant,resolutions=db.fixture.res)
                snapshot=db.inspect(entry)
                assert snapshot['acceptances']==1 and snapshot['row_receipts']==3
                result=dict(settings=db.settings,separate_connection_verified=True,rows=snapshot['rows'],row_receipts=snapshot['row_receipts'])
            return dict(result,cleanup=db.cleanup)
        if 'database' in report:check('fixture',fixture_check)
    else:
        tell('CONSOLE-ONLY MODE: the full stack is not ready; DB1 delivery is disabled explicitly.')
    report['status'] = 'failed' if report['failures'] else 'ready'
    atomic_json(directory/'preflight.json', report)
    return 1 if report['failures'] else 0


def main(argv=None):
    global URL, API
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console-only', action='store_true', help='Explicitly run without PostgreSQL delivery')
    parser.add_argument('--stop', action='store_true', help='Ask the existing launcher to drain and stop only its owned services')
    parser.add_argument('--check', action='store_true', help='Check prerequisites and disposable delivery, then retire probes without starting web services')
    args = parser.parse_args(argv)
    from vehicle.common.console_stack import configure, identity
    from vehicle.common.console_runs import atomic_json
    configure(load_dsn=False)
    selected=os.environ.get('T1_CONSOLE_PYTHON')
    if selected:
        selected=shutil.which(selected) or str(Path(selected).expanduser())
        if os.path.abspath(selected)!=os.path.abspath(sys.executable):
            if not os.access(selected,os.X_OK):raise RuntimeError('Configured T1_CONSOLE_PYTHON is not executable: '+selected)
            os.execve(selected,[selected,'-B',__file__,*(sys.argv[1:] if argv is None else argv)],os.environ)
    api_port=int(os.environ.get('WAYCON_API_PORT','8002'))
    web_port=int(os.environ.get('WAYCON_WEB_PORT','5173'))
    if not (1<=api_port<=65535 and 1<=web_port<=65535) or api_port==web_port:
        raise RuntimeError('WAYCON_API_PORT and WAYCON_WEB_PORT must be different valid ports.')
    URL=f'http://127.0.0.1:{web_port}/';API=f'http://127.0.0.1:{api_port}/'
    os.environ['WAYCON_API_PROXY']=API.rstrip('/')
    os.environ.update(T1_CONSOLE_DB1='console-only' if args.console_only else 'fixture',
                      PYTHONDONTWRITEBYTECODE='1',
                      YOLO_OFFLINE='true', YOLO_AUTOINSTALL='false', COLOR_AGENT_ENABLED='false',
                      PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', OMP_NUM_THREADS='1',
                      OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    postgres_mode=os.environ.get('WAYCON_POSTGRES_MODE','owned')
    if postgres_mode=='existing':
        os.environ['DB1_P4_EXISTING_SERVER']='1'
        os.environ.pop('DB1_P4_ISOLATED',None)
    elif postgres_mode=='owned':
        # An inherited DSN/file must never route a fresh local launch to Analytic.
        os.environ.pop('DB1_P4_ADMIN_DSN',None)
        os.environ.pop('DB1_P4_EXISTING_SERVER',None)
        os.environ.pop('WAYCON_DSN_FROM_FILE',None)
    else:raise RuntimeError('WAYCON_POSTGRES_MODE must be owned or existing.')
    directory = Path(os.environ.get('WAYCON_LAUNCHER_ROOT', ROOT/'vehicle/media/waycon-launcher')).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    lock = (directory/'launcher.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if args.stop:
            (directory/'stop-request').touch(mode=0o600)
            tell('Requested Stop. Waiting for the launcher to drain active work…')
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    tell('Owned shutdown complete. Shared PostgreSQL and system Redis were left running.')
                    return 0
                except BlockingIOError:
                    pass
                time.sleep(.2)
            raise RuntimeError('Run slot did not release; services were left running. Inspect the run status.')
        state = get(API+'api/t1/stack/')
        if args.check:raise RuntimeError('A launcher already uses this state directory; use a separate WAYCON_LAUNCHER_ROOT for independent checks.')
        require_loopback_listener(api_port)
        require_loopback_listener(web_port)
        if state['stack_id'] != identity() or state['mode'] != os.environ['T1_CONSOLE_DB1']:
            raise RuntimeError('Existing launcher uses a different console root or mode.')
        if state['mode'] == 'fixture' and not state['full_stack_ready']:
            raise RuntimeError('The running full stack is not ready: DB1 access is unavailable.')
        expected_port=int(os.environ.get('WAYCON_PG_PORT','5432')) if postgres_mode=='existing' else None
        if not database_matches(state,port=expected_port):
            raise RuntimeError('The running launcher uses a different PostgreSQL ownership mode; services were left untouched.')
        if get(URL+'api/t1/stack/')['stack_id'] != identity():
            raise RuntimeError('The existing browser listener does not proxy this console.')
        tell('Reusing the running launcher; no services or video runs were started.')
        print(URL, flush=True)
        return 0
    if args.stop:
        tell('No launcher owns these services; no processes were stopped.')
        return 0
    (directory/'stop-request').unlink(missing_ok=True)
    def startup_stop(*_):
        (directory/'stop-request').touch(mode=0o600)
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,startup_stop)
    from vehicle.common.console_prerequisites import inventory
    prerequisites=inventory(os.environ['T1_CONSOLE_DB1'])
    atomic_json(directory/'prerequisites.json',prerequisites)
    for warning in prerequisites['warnings']:tell(warning)
    if prerequisites['missing']:
        for missing in prerequisites['missing']:tell(missing)
        raise RuntimeError('Project prerequisites are missing. Nothing was installed and no services were started; see '+str(directory/'prerequisites.json'))
    from contextlib import ExitStack
    from vehicle.common.console_postgres import OwnedPostgres
    with ExitStack() as resources:
        postgres=None
        if not args.console_only and postgres_mode=='owned':
            tell('Starting a launcher-owned local PostgreSQL 16 test instance…')
            postgres=resources.enter_context(OwnedPostgres(directory))
        return start_services(directory,args,postgres)


def start_services(directory,args,postgres=None):
    from vehicle.common.console_runs import atomic_json
    from vehicle.common.console_stack import identity
    drain = directory/'draining'
    drain.unlink(missing_ok=True)
    os.environ['WAYCON_LAUNCHER_DRAIN'] = str(drain)
    os.environ['WAYCON_PREFLIGHT'] = str(directory/'preflight.json')
    # Run preflight in a short-lived process: the supervisor must not retain CUDA.
    tell('Checking CUDA, local artifacts, run-owned Redis and the requested DB1 mode…')
    with (directory/'preflight.log').open('wb') as log:
        result = subprocess.run([sys.executable, '-B', __file__, '--preflight', str(directory), os.environ['T1_CONSOLE_DB1']],
                                stdout=log, stderr=log)
    if result.returncode:
        report_path=directory/'preflight.json'
        if report_path.exists():
            for failure in json.loads(report_path.read_text()).get('failures',[]):tell(failure)
        raise RuntimeError('Full stack is not ready. See '+str(directory/'preflight.log')+'; no web services were started.')
    report=json.loads((directory/'preflight.json').read_text())
    os.environ['WAYCON_MANUFACTURER_AVAILABLE']='1' if report['manufacturer']['available'] else '0'
    if args.check:
        tell('Prerequisite and disposable-delivery checks passed. Owned probes will stop; no video or camera was opened.')
        return 0
    if (directory/'stop-request').exists():
        tell('Stop requested during startup. Owned probes will retire; no web services were started.')
        return 0
    tell('CUDA, artifacts, run-owned Redis and requested DB1 mode verified.')
    if args.console_only:
        tell('CONSOLE-ONLY MODE: full stack is not ready; PostgreSQL delivery is disabled explicitly.')
    node = os.environ.get('WAYCON_NODE') or shutil.which('node')
    children, logs, owned = [], [], []
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    def matches(url):
        state = get(url+'api/t1/stack/')
        port=int(os.environ.get('WAYCON_PG_PORT','5432')) if os.environ['T1_CONSOLE_DB1']=='fixture' else None
        return (state['stack_id']==identity() and state['mode']==os.environ['T1_CONSOLE_DB1']
                and database_matches(state,port=port))
    def spawn(command, name, cwd):
        log = (directory/(name+'.log')).open('ab')
        logs.append(log)
        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, start_new_session=True)
        children.append(child)
        owned.append(name)
    try:
        api_port=int(API.split(':')[-1].rstrip('/'))
        web_port=int(URL.split(':')[-1].rstrip('/'))
        if listening(api_port):
            require_loopback_listener(api_port)
            if not matches(API):
                raise RuntimeError('Configured API port belongs to another backend. It was left untouched.')
        else:
            spawn([sys.executable, '-B', str(ROOT/'vehicle/manage.py'), 'runserver', f'127.0.0.1:{api_port}', '--noreload'], 'django', ROOT)
        wait_ready(lambda: matches(API) and (os.environ['T1_CONSOLE_DB1']=='console-only' or get(API+'api/t1/stack/')['full_stack_ready'])
                   and isinstance(get(API+'api/t1/runs/')['runs'], list), children)
        if listening(web_port):
            require_loopback_listener(web_port)
            if not matches(URL):
                raise RuntimeError('Configured browser port belongs to another frontend. It was left untouched.')
        else:
            spawn([node, str(ROOT/'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(web_port), '--strictPort'], 'vite', ROOT/'frontend')
        wait_ready(lambda: matches(URL), children)
        atomic_json(directory/'owner.json', dict(pid=os.getpid(), owned_services=owned+(['postgres'] if postgres else []), stack_id=identity()))
        tell('WAYCON ready. No video or camera has been started. Ctrl-C requests an owned shutdown.')
        print(URL, flush=True)
        while True:
            if (directory/'stop-request').exists():
                stop.set()
            if any(child.poll() is not None for child in children) or (postgres and postgres.process.poll() is not None):
                stop.set()
                tell('An owned service exited; draining active work before shutdown.')
            if stop.is_set():
                # No Stop is sent to a backend the launcher did not start.
                if 'django' in owned:
                    drain.touch(mode=0o600)
                    try:
                        active = get(API+'api/t1/runs/?active=1')['runs']
                        for run in active:
                            get(API+f"api/t1/runs/{run['run_id']}/stop/", method='POST')
                        deadline = time.monotonic()+330
                        while active and time.monotonic() < deadline:
                            time.sleep(.2)
                            active = get(API+'api/t1/runs/?active=1')['runs']
                        if active:
                            raise RuntimeError('The active run has not released its slot.')
                    except Exception:
                        tell('Cannot confirm the run slot is released. Services remain running; inspect run status and retry --stop.')
                        stop.clear()
                        (directory/'stop-request').unlink(missing_ok=True)
                        time.sleep(1)
                        continue
                else:
                    try:
                        if get(API+'api/t1/runs/?active=1')['runs']:
                            raise RuntimeError('Unowned backend has an active run.')
                    except Exception:
                        tell('The backend is not launcher-owned and may have active work. Services remain running; retry --stop after the run releases its slot.')
                        stop.clear()
                        (directory/'stop-request').unlink(missing_ok=True)
                        time.sleep(1)
                        continue
                break
            time.sleep(.2)
    except Exception:
        # Startup errors must also avoid killing a backend with active work.
        if 'django' in owned:
            drain.touch(mode=0o600)
            try:
                if get(API+'api/t1/runs/?active=1')['runs']:
                    tell('Startup failed with active work; owned services were left running.')
                    if postgres:postgres.retain_for_inspection()
                    children.clear()
            except Exception:
                if any(child.poll() is None for child in children):
                    tell('Could not confirm backend admission; services were left running.')
                    if postgres:postgres.retain_for_inspection()
                    children.clear()
        raise
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for log in logs:
            log.close()
    tell('Owned services stopped. Shared PostgreSQL and system Redis remain running.')
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--preflight':
        try:
            raise SystemExit(preflight(Path(sys.argv[2]), sys.argv[3]))
        except Exception as error:
            import traceback
            from vehicle.common.console_runs import atomic_json
            atomic_json(Path(sys.argv[2])/'preflight-failure.json', dict(type=type(error).__name__,
                frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                        for frame in traceback.extract_tb(error.__traceback__)]))
            # Never stringify driver/native errors with possible secrets.
            from vehicle.common.console_stack import StackUnavailable
            if isinstance(error, StackUnavailable):
                tell(str(error))
            else:
                tell('Preflight failed: '+type(error).__name__+'. Verify private DB1 configuration, CUDA and local model paths.')
            raise SystemExit(1)
    else:
        try:
            raise SystemExit(main())
        except Exception as error:
            from vehicle.common.console_stack import StackUnavailable
            tell(str(error) if isinstance(error, (StackUnavailable, RuntimeError)) else 'Launcher failed; inspect private configuration and launcher logs.')
            raise SystemExit(1)
