"""Local T1 console run ownership. No decoder, inference or credentials in argv.

Status is a deliberately small public projection; private configuration and
canonical deliveries are never served by the API. An Engine process is never
automatically restarted after a web process restart.
"""
from datetime import datetime, timezone
import atexit
import fcntl
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from uuid import UUID, uuid4

TERMINAL = {'completed', 'stopped', 'failed', 'interrupted', 'partial'}
ROOT = Path(__file__).resolve().parents[1]
_START_LOCK = threading.RLock()
_CHILDREN = {}
_LAUNCHING = set()
_SERVERS = {}


class ActiveRunError(RuntimeError):
    pass


class ServerProcessError(RuntimeError):
    pass


def _server_lock(root):
    """One web process per canonical run root, held for its entire lifetime.

    Threads share the descriptor. A fork must acquire its own lock; inherited
    descriptors and registry entries do not confer ownership on the child.
    Workers do not inherit the lock across exec (O_CLOEXEC).
    """
    key = str(root)
    with _START_LOCK:
        previous = _SERVERS.get(key)
        if previous and previous[0] == os.getpid():
            return
        if previous:
            os.close(previous[1])
            del _SERVERS[key]
        fd = os.open(root/'.server.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise ServerProcessError('console_requires_one_server_process') from None
        _SERVERS[key] = (os.getpid(), fd)


@atexit.register
def _close_server_locks():
    for _, fd in _SERVERS.values():
        os.close(fd)
    _SERVERS.clear()


def _process_identity(pid):
    # Linux start ticks protect restart inspection from PID reuse. No signals
    # are ever sent using this record; termination uses our own Popen handle.
    fields = (Path('/proc')/str(pid)/'stat').read_text().rsplit(')', 1)[1].split()
    return fields[19], fields[0]


def atomic_json(path, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def metadata_turn(worker):
    """Do not hold a pure metadata lease while Engine performs native work.

    Each turn reacquires a fence and repairs retained intent before processing.
    These workers never invoke models; recovering a lost lease cannot infer.
    """
    from redis.exceptions import ResponseError
    try:
        return worker.step()
    except ResponseError as error:
        if str(error) != 'STALE_OWNER':raise
        worker.journal.token = None
        return False
    finally:
        if worker.journal.token:
            try:worker.journal.release()
            except ResponseError as error:
                if str(error) != 'STALE_OWNER':raise
                worker.journal.token = None


def review_acknowledgments(client, journal, requests):
    """A quiet pending list alone does not prove an unread event was ACKed.

    An exact request must have its published review result and no longer be in
    the source group's pending list. This is review delivery, never DB1 ACK.
    """
    from .manufacturer.streams import SOURCE, RESULT
    from .redis_scalar.codec import bounded_json
    completed = {bounded_json(fields[b'json'])['request_id']
                 for _, fields in client.xrange(journal.key(RESULT))}
    return {sid:('acknowledged' if request_id in completed and not client.xpending_range(
                journal.key(SOURCE), journal.group, min=entry_id, max=entry_id, count=1)
                else 'pending') for sid,(entry_id,request_id) in requests.items()}


def initial_status(run_id, kind, manufacturer, sample_every=None, max_seconds=None):
    return dict(schema_version='vehicle-console-run-v1', run_id=run_id,
        source_kind=kind, status='starting', phase='Preparing local models',
        started_at=timestamp(), ended_at=None, frames=0,
        sample_every=sample_every, max_seconds=max_seconds, decoded_frames=0,
        analyzed_frames=0, sampling_skips=0, overload_drops=0, end_reason=None, full_video_complete=False,
        preview_revision=0, rows=[], manufacturer_enabled=manufacturer,
        delivery=dict(postgres_commit='pending' if os.environ.get('T1_CONSOLE_DB1')=='fixture' else 'not_configured', redis_ack='not_attempted',
                      review_ack='pending' if manufacturer else 'disabled'),
        error_code=None, stop_requested=False, cleanup=None, counters={})


class RunStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('T1_CONSOLE_RUN_ROOT', ROOT/'media/t1-console'))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()
        os.chmod(self.root, 0o700)
        _server_lock(self.root)

    def directory(self, run_id):
        if str(UUID(str(run_id))) != str(run_id):
            raise ValueError('invalid_run')
        directory = self.root / str(run_id)
        if not directory.is_dir():
            raise FileNotFoundError('missing_run')
        return directory

    def status(self, run_id):
        with _START_LOCK:
            return self._status(run_id)

    def _status(self, run_id):
        directory = self.directory(run_id)
        status = json.loads((directory/'status.json').read_text())
        key = str(directory)
        child = _CHILDREN.get(key)
        dead, active, uncertain = False, False, False
        if key in _LAUNCHING:
            # Reservation precedes Popen. Stop and polling remain available
            # while the launcher is outside the lock, including failed cleanup.
            active = True
        elif child is not None:
            dead = child.poll() is not None
            active = not dead
        else:
            try:
                owner = json.loads((directory/'owner.json').read_text())
                ticks, state = _process_identity(owner['pid'])
                args = (Path('/proc')/str(owner['pid'])/'cmdline').read_bytes().split(b'\0')
                active = (state != 'Z' and os.fsencode(directory) in args
                          and os.fsencode(ROOT.parent/'scripts/vehicle_console_worker.py') in args
                          and owner.get('start_ticks', ticks) == ticks)
                dead = not active
            except FileNotFoundError:
                if (directory/'owner.json').exists():
                    dead = True
                else:
                    try:
                        lifecycle = json.loads((directory/'lifecycle.json').read_text())
                        uncertain = lifecycle['phase'] != 'released'
                    except (OSError, ValueError, KeyError):
                        uncertain = status['status'] not in TERMINAL
                    active = uncertain
            except (OSError, ValueError, KeyError):
                active = uncertain = True
            if (directory/'lifecycle.json').exists():
                lifecycle = json.loads((directory/'lifecycle.json').read_text())
                if lifecycle['phase'] == 'cleanup_failed':
                    active = uncertain = True
                    dead = False
        if dead:
            if status['status'] not in TERMINAL:
                status.update(status='interrupted', phase='Worker exited; inference will not be retried',
                              error_code='worker_interrupted', ended_at=timestamp())
                atomic_json(directory/'status.json', status)
            _CHILDREN.pop(key, None)
        if uncertain:
            if status['status'] not in TERMINAL:
                status['status'] = 'interrupted'
            status.update(phase='Launch ownership needs manual inspection',
                          error_code='ownership_uncertain')
        if (directory/'stop').exists():
            status['stop_requested'] = True
        if status['stop_requested'] and status['status'] not in TERMINAL:
            status.update(status='stopping', stop_requested=True, phase='Stopping input and retiring model work')
        status['active_slot'] = active
        if status['source_kind']=='rtsp':
            live = directory/'live-preview.jpg'
            if live.is_file():status['preview_revision']=live.stat().st_mtime_ns
            if active and (directory/'input-counts.json').exists():
                status.update(json.loads((directory/'input-counts.json').read_text()))
        from .console_crops import enrich_status
        return enrich_status(directory, status)

    def list(self, *, active_only=False):
        rows = []
        for directory in self.root.iterdir():
            if directory.is_dir() and (directory/'status.json').is_file():
                try:
                    status = self.status(directory.name)
                    if not active_only or status['active_slot']:rows.append(status)
                except (ValueError, OSError, json.JSONDecodeError):continue
        ordered = sorted(rows, key=lambda r:r['started_at'], reverse=True)
        return ordered if active_only else ordered[:64]

    def start(self, *, source_kind, upload=None, rtsp_uri=None, rtsp_transport='tcp', manufacturer=False, sample_every=None, max_seconds=None):
        if manufacturer and os.environ.get('WAYCON_MANUFACTURER_AVAILABLE') == '0':
            raise ValueError('Manufacturer runtime is unavailable. Supply the local verified T1_MANUFACTURER_LOCK before enabling it.')
        from .console_sampling import sampling_interval, timeout_seconds
        sample_every = sampling_interval(sample_every, source_kind)
        max_seconds = timeout_seconds(max_seconds)
        if (source_kind not in {'mp4','rtsp'} or rtsp_transport not in {'tcp','udp'} or type(manufacturer) is not bool
                ):
            raise ValueError('invalid_start_options')
        if source_kind == 'mp4' and (upload is None or not upload.name.lower().endswith('.mp4')):
            raise ValueError('mp4_required')
        with _START_LOCK:
            drain = os.environ.get('WAYCON_LAUNCHER_DRAIN')
            if drain and Path(drain).exists():
                raise ActiveRunError('launcher_draining')
            # Inspect every run: the 64-row UI history limit is not admission.
            if any(self._status(p.name)['active_slot'] for p in self.root.iterdir()
                   if p.is_dir() and (p/'status.json').is_file()):
                raise ActiveRunError('run_already_active')
            run_id = str(uuid4())
            directory = self.root/run_id
            directory.mkdir(mode=0o700)
            _LAUNCHING.add(str(directory))
            status = initial_status(run_id, source_kind, manufacturer, sample_every, max_seconds)
            try:
                atomic_json(directory/'lifecycle.json', {'phase':'launching'})
                atomic_json(directory/'status.json', status)
            except BaseException:
                _LAUNCHING.discard(str(directory))
                shutil.rmtree(directory)
                raise
        private = child = None
        gate_read = gate_write = None
        try:
            if source_kind == 'rtsp':
                from .real_video.rtsp import stage_camera
                private = Path(tempfile.mkdtemp(prefix='waycon-camera-'))
                os.chmod(private, 0o700)
                camera = stage_camera(rtsp_uri, private, camera_id='console-'+run_id)
                source = str(camera.path)
            else:
                source = str(directory/'input.mp4')
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                size = 0
                with os.fdopen(fd,'wb') as target:
                    for chunk in upload.chunks():
                        size += len(chunk)
                        if size > 512*1024*1024:raise ValueError('upload_too_large')
                        target.write(chunk)
            config = dict(run_id=run_id, source_kind=source_kind, source=source,
                          rtsp_transport=rtsp_transport, manufacturer=manufacturer, sample_every=sample_every, max_seconds=max_seconds)
            atomic_json(directory/'config.json', config)
            # The worker cannot allocate native/Redis resources until its
            # ownership record is durable. EOF cancels an unpublished launch.
            gate_read, gate_write = os.pipe()
            worker_environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'YOLO_OFFLINE':'true',
                     'YOLO_AUTOINSTALL':'false', 'COLOR_AGENT_ENABLED':'false',
                     'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True', 'OMP_NUM_THREADS':'1',
                     'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1',
                     'T1_CONSOLE_LAUNCH_FD':str(gate_read)}
            # The normal private-file configuration needs no provisioning
            # credential in CUDA's environment. Only the DB1 host reads it.
            if worker_environment.get('WAYCON_DSN_FROM_FILE') == '1':
                worker_environment.pop('DB1_P4_ADMIN_DSN', None)
            child = subprocess.Popen([os.environ.get('T1_CONSOLE_PYTHON',sys.executable), '-B',
                str(ROOT.parent/'scripts/vehicle_console_worker.py'), str(directory)],
                cwd=ROOT.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, pass_fds=(gate_read,),
                env=worker_environment)
            with _START_LOCK:
                _CHILDREN[str(directory)] = child
            atomic_json(directory/'owner.json', {'pid':child.pid,
                        'start_ticks':_process_identity(child.pid)[0]})
            atomic_json(directory/'lifecycle.json', {'phase':'owned'})
            os.write(gate_write, b'1')
            with _START_LOCK:
                _LAUNCHING.discard(str(directory))
        except BaseException:
            # Keep reservation and process handle throughout cleanup. Never
            # delete the run directory while a child can still be using it.
            cleaned = True
            try:
                if child is not None:
                    if child.poll() is None:
                        child.terminate()
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            except Exception:
                cleaned = False
            try:
                if private is not None:shutil.rmtree(private)
            except FileNotFoundError:pass
            except Exception:cleaned = False
            with _START_LOCK:
                status.update(status='failed', phase='Launch failed', ended_at=timestamp(),
                              error_code='launch_failed', cleanup=dict(
                                  decoder_closed=True, models_closed=True, redis_removed=True,
                                  private_camera_removed=private is None or not private.exists(),
                                  native_children_exited=child is None or child.poll() is not None))
                atomic_json(directory/'status.json', status)
                atomic_json(directory/'lifecycle.json', {'phase':'released' if cleaned else 'cleanup_failed'})
                if cleaned:
                    _LAUNCHING.discard(str(directory))
                    _CHILDREN.pop(str(directory), None)
            raise
        finally:
            for fd in (gate_read, gate_write):
                if fd is not None:os.close(fd)
        return self.status(run_id)

    def stop(self, run_id):
        with _START_LOCK:
            directory = self.directory(run_id)
            current = self._status(run_id)
            if current['active_slot']:
                (directory/'stop').touch(mode=0o600, exist_ok=True)
            return self._status(run_id)
