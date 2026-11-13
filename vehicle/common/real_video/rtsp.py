"""Opt-in, one-connection RTSP Input. Pixels never leave local shared memory.

A spawned decoder owns the potentially blocking C calls. The host owns receipt
time, deadlines and Stop. No decoder exception or endpoint enters diagnostics.
Reconnection requires a new Input, run context, clock and Engine/tracker.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import multiprocessing as mp
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from .input import DecodedFrame


class InputFault(RuntimeError):
    """Only fixed, non-sensitive codes may cross the decoder boundary."""


class InputStopped(InputFault):
    pass


@dataclass(frozen=True)
class PrivateCamera:
    path: Path
    camera_id: str
    timezone: str
    fingerprint: str = field(repr=False)
    endpoint: str = field(repr=False)


def uri_file(path):
    """Optional automated Start input. The caller owns this file; never delete it."""
    try:
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError()
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        return raw.decode().strip()
    except Exception:
        raise InputFault('private_rtsp_uri_file_unavailable_or_invalid') from None


def stage_camera(uri, directory, *, camera_id=None, timezone='Africa/Tunis'):
    """Common Start boundary for CLI and a future backend; no shared defaults.

    A new Start gets a fresh opaque source identity unless an explicit camera
    identity is supplied. The immutable private snapshot is pinned for reconnects.
    The caller owns the mode-0700 directory and its lifetime.
    """
    try:
        directory = Path(directory)
        info = directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISDIR(info.st_mode):
            raise ValueError()
        path = directory / 'camera.json'
        if not isinstance(uri, str) or len(uri.encode()) > 12000:
            raise ValueError()
        body = dict(endpoint=uri, camera_id=camera_id or 'rtsp-start-'+uuid4().hex, timezone=timezone)
        serialized = json.dumps(body)
        if len(serialized.encode()) > 16384:
            raise ValueError()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as target:
            target.write(serialized)
        return private_camera(path)
    except Exception:
        raise InputFault('invalid_rtsp_start_configuration') from None


def prompted_uri():
    """Fail closed when hidden terminal input is unavailable; never echo."""
    import getpass
    import sys
    import warnings
    if not sys.stdin.isatty():
        raise InputFault('rtsp_uri_prompt_requires_terminal')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass('RTSP URI (hidden): ')
    except (EOFError, getpass.GetPassWarning):
        raise InputFault('rtsp_hidden_prompt_unavailable') from None
    except KeyboardInterrupt:
        raise InputStopped('controlled_stop') from None


def private_camera(path):
    """No endpoint in repr, CLI argv, reports, child errors or shared evidence."""
    try:
        path = Path(path).resolve(strict=True)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError()
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        body = json.loads(raw)
        if set(body) != {'endpoint', 'camera_id', 'timezone'}:
            raise ValueError()
        endpoint = body['endpoint']
        url = urlsplit(endpoint)
        if (url.scheme != 'rtsp' or not url.hostname or not url.port and url.netloc.endswith(':')
                or any(ord(c) < 32 for c in endpoint)):
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', body['camera_id']):
            raise ValueError()
        ZoneInfo(body['timezone'])
        return PrivateCamera(path, body['camera_id'], body['timezone'], sha256(raw).hexdigest(), endpoint)
    except Exception:
        raise InputFault('private_camera_configuration_unavailable_or_invalid') from None


@dataclass(frozen=True)
class RTSPBounds:
    open_seconds: float = 5.0
    read_seconds: float = 3.0
    max_frame_bytes: int = 1920 * 1080 * 3
    transport: str = 'tcp'

    def __post_init__(self):
        if not (0 < self.open_seconds <= 15 and 0 < self.read_seconds <= 15
                and 3 <= self.max_frame_bytes <= 3840 * 2160 * 3 and self.transport in {'tcp', 'udp'}):
            raise ValueError('invalid_rtsp_bounds')


def decoder(camera_path, fingerprint, generation, channel, memory_name, bounds):
    """One fixed frame slot, pull-only decoding, bounded FFmpeg network waits.

    Child stderr is suppressed because FFmpeg can embed credentials in errors.
    The parent can terminate this *owned decoder* without killing a GPU worker.
    """
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    os.close(null)
    memory = capture = None
    try:
        camera = private_camera(camera_path)
        if camera.fingerprint != fingerprint:
            raise InputFault('private_configuration_changed')
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
            'rtsp_transport;'+bounds.transport+'|fflags;nobuffer|max_delay;0|buffer_size;65536'
            '|reorder_queue_size;0|threads;1|probesize;1048576|analyzeduration;1000000')
        import cv2
        import numpy as np
        cv2.setNumThreads(1)
        memory = SharedMemory(name=memory_name)
        capture = cv2.VideoCapture(camera.endpoint, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, max(1, int(bounds.open_seconds * 1000)),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, max(1, int(bounds.read_seconds * 1000))])
        if not capture.isOpened():
            raise InputFault('rtsp_open_failed')
        if capture.getBackendName() != 'FFMPEG':
            raise InputFault('rtsp_decoder_backend_mismatch')
        fps = capture.get(cv2.CAP_PROP_FPS)
        channel.send((generation, 'opened', dict(fps=fps, decoder=dict(opencv_version=cv2.__version__,
            opencv_init_sha256=sha256(Path(cv2.__file__).read_bytes()).hexdigest(),
            opencv_build_sha256=sha256(cv2.getBuildInformation().encode()).hexdigest(),
            backend='FFMPEG',transport=bounds.transport))))
        while True:
            command, sequence = channel.recv()
            if command == 'stop':
                break
            if command != 'read':
                raise InputFault('decoder_protocol_error')
            ok, pixels = capture.read()
            if not ok:
                raise InputFault('rtsp_disconnected')
            if (pixels.dtype != np.uint8 or len(pixels.shape) != 3 or pixels.shape[2] != 3
                    or pixels.nbytes > bounds.max_frame_bytes):
                raise InputFault('decoded_frame_budget')
            np.ndarray(pixels.shape, dtype=np.uint8, buffer=memory.buf)[:] = pixels
            pts = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            channel.send((generation, 'frame', (sequence, pixels.shape, pts if math.isfinite(pts) else None)))
    except BaseException as error:
        code = str(error) if isinstance(error, InputFault) else 'rtsp_decoder_failed'
        try:
            channel.send((generation, 'error', code))
        except (OSError, EOFError):
            pass
    finally:
        if capture is not None:
            capture.release()
        if memory is not None:
            memory.close()
        channel.close()


class RTSPInput:
    """Same DecodedFrame/paired receipt interface as MP4Input; never reconnects.

    There is one decoder and one shared frame slot, no frame queue or prefetch.
    Decoder PTS is informational only; it never supplies receipt UTC/monotonic.
    """
    def __init__(self, camera, clock, *, deadline, bounds=RTSPBounds(), stop=None, decoder_target=decoder):
        if clock.context.source_kind != 'rtsp' or clock.context.camera.camera_id != camera.camera_id:
            raise ValueError('rtsp_context_mismatch')
        self.camera, self.clock = camera, clock
        self.deadline, self.bounds = deadline, bounds
        self.stop_event = stop if stop is not None else threading.Event()
        self.target = decoder_target
        self.generation = uuid4().hex
        self.process = self.channel = self.memory = None
        self.opens = self.decoded = 0
        self.metadata = {}
        self.closed = False
        self._close_lock = threading.RLock()

    def _receive(self, timeout):
        end = min(self.deadline, time.monotonic() + timeout)
        while True:
            if self.stop_event.is_set():
                raise InputStopped('controlled_stop')
            if time.monotonic() >= end:
                raise InputFault('rtsp_total_deadline' if end == self.deadline else 'rtsp_read_or_open_timeout')
            try:
                if self.channel.poll(min(.02, max(0, end - time.monotonic()))):
                    generation, kind, body = self.channel.recv()
                    if generation != self.generation:
                        raise InputFault('stale_decoder_message')
                    if kind == 'error':
                        allowed = {'private_configuration_changed', 'rtsp_open_failed', 'rtsp_disconnected',
                                   'decoder_protocol_error', 'decoded_frame_budget', 'rtsp_decoder_failed',
                                   'rtsp_decoder_backend_mismatch',
                                   'private_camera_configuration_unavailable_or_invalid'}
                        raise InputFault(body if body in allowed else 'rtsp_decoder_failed')
                    return kind, body
                if not self.process.is_alive():
                    raise InputFault('rtsp_decoder_exited')
            except (OSError, EOFError, TypeError):
                raise InputStopped('controlled_stop') if self.stop_event.is_set() else InputFault('rtsp_decoder_exited') from None

    def __enter__(self):
        if self.opens or self.closed:
            raise InputFault('rtsp_connection_cannot_reopen')
        try:
            if self.stop_event.is_set():
                raise InputStopped('controlled_stop')
            ctx = mp.get_context('spawn')
            self.memory = SharedMemory(create=True, size=self.bounds.max_frame_bytes)
            self.channel, child = ctx.Pipe()
            self.process = ctx.Process(target=self.target, args=(str(self.camera.path), self.camera.fingerprint,
                self.generation, child, self.memory.name, self.bounds), daemon=True)
            self.process.start()
            child.close()
            self.opens += 1
            kind, self.metadata = self._receive(self.bounds.open_seconds)
            if kind != 'opened' or not math.isfinite(self.metadata['fps']) or not 0 < self.metadata['fps'] <= 240:
                raise InputFault('rtsp_missing_actual_fps')
            return self
        except BaseException:
            self.close()
            raise

    def read(self):
        try:
            if self.closed or self.channel is None:
                raise InputFault('rtsp_connection_closed')
            self.channel.send(('read', self.decoded))
            kind, body = self._receive(self.bounds.read_seconds)
            if kind != 'frame':
                raise InputFault('decoder_protocol_error')
            sequence, shape, pts = body
            if (sequence != self.decoded or len(shape) != 3 or shape[2] != 3
                    or any(type(v) is not int or v <= 0 for v in shape)
                    or math.prod(shape) > self.bounds.max_frame_bytes):
                raise InputFault('decoder_frame_binding_error')
            import numpy as np
            with self._close_lock:
                if self.stop_event.is_set():
                    raise InputStopped('controlled_stop')
                pixels = np.ndarray(shape, dtype=np.uint8, buffer=self.memory.buf).copy()
                receipt = self.clock.capture(self.decoded, media_pts=pts)
            self.decoded += 1
            return DecodedFrame(receipt, pixels)
        except BaseException:
            self.close()
            raise

    def stop(self):
        self.stop_event.set()
        self.close()

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            if self.process is not None:
                if self.process.is_alive():
                    try:
                        self.channel.send(('stop', 0))
                    except (OSError, EOFError):
                        pass
                    self.process.join(.1)
                if self.process.is_alive():
                    self.process.terminate()
                self.process.join(.5)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(.5)
                if self.process.is_alive():
                    raise InputFault('decoder_retirement_failed')
            if self.channel is not None:
                self.channel.close()
            if self.memory is not None:
                self.memory.close()
                self.memory.unlink()
            self.closed = True

    def __exit__(self, *_):
        self.close()
