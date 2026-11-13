"""Console RTSP decoder: one overwrite slot, sampled before inference.

The decoder continuously drains the transport. Only an owned shared-memory
frame and a host copy exist; no growing packet or pixel queue is introduced.
The original pull-only RTSPInput remains for the bounded standalone runners.
"""
from datetime import datetime, timezone
import math
import multiprocessing as mp
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import time

from .rtsp import RTSPInput, InputFault, private_camera
from .input import DecodedFrame


def decode_latest(camera_path, fingerprint, generation, channel, memory_name, bounds,
                  stats, lock, halt, every, directory):
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2);os.close(null)
    memory = capture = None
    try:
        camera = private_camera(camera_path)
        if camera.fingerprint != fingerprint:raise InputFault('private_configuration_changed')
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
            'rtsp_transport;'+bounds.transport+'|fflags;nobuffer|max_delay;0|buffer_size;65536'
            '|reorder_queue_size;0|threads;1|probesize;1048576|analyzeduration;1000000')
        import cv2
        import numpy as np
        cv2.setNumThreads(1)
        memory = SharedMemory(name=memory_name)
        capture = cv2.VideoCapture(camera.endpoint, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(bounds.open_seconds*1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(bounds.read_seconds*1000)])
        if not capture.isOpened():raise InputFault('rtsp_open_failed')
        fps = capture.get(cv2.CAP_PROP_FPS)
        channel.send((generation,'opened',dict(fps=fps,decoder=dict(backend='FFMPEG',transport=bounds.transport))))
        last_preview = 0
        while not halt.is_set():
            ok, pixels = capture.read()
            if not ok:raise InputFault('rtsp_disconnected')
            utc, mono = time.time_ns(), time.monotonic_ns()
            if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2]!=3 or pixels.nbytes>bounds.max_frame_bytes:
                raise InputFault('decoded_frame_budget')
            pts = capture.get(cv2.CAP_PROP_POS_MSEC)/1000
            with lock:
                index = stats[0];stats[0] += 1
                if index % every:
                    stats[1] += 1
                elif stats[3] and not stats[10]:
                    # Preserve frame zero until its first handoff. Later slots
                    # always prefer the newest selected frame.
                    stats[2] += 1
                else:
                    if stats[3]:stats[2] += 1
                    np.ndarray(pixels.shape,dtype=np.uint8,buffer=memory.buf)[:] = pixels
                    stats[3:10] = [1,index,pixels.shape[0],pixels.shape[1],utc,mono,
                                   int(pts*1000000) if math.isfinite(pts) else -1]
            if mono-last_preview >= 250000000:
                from ..console_runs import atomic_json
                atomic_json(Path(directory)/'input-counts.json',dict(decoded_frames=stats[0],sampling_skips=stats[1],overload_drops=stats[2]))
                small = cv2.resize(pixels,(min(960,pixels.shape[1]),max(1,int(pixels.shape[0]*min(960,pixels.shape[1])/pixels.shape[1]))))
                ok, encoded = cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,75])
                if ok:
                    path = Path(directory)/'live-preview.tmp'
                    path.write_bytes(encoded.tobytes());path.chmod(0o600)
                    path.replace(Path(directory)/'live-preview.jpg')
                last_preview = mono
    except BaseException as error:
        code = str(error) if isinstance(error, InputFault) else 'rtsp_decoder_failed'
        try:channel.send((generation,'error',code))
        except (OSError,EOFError):pass
    finally:
        if capture is not None:capture.release()
        if memory is not None:memory.close()
        channel.close()


class LatestRTSPInput(RTSPInput):
    def __init__(self, camera, clock, *, every, directory, **kwargs):
        super().__init__(camera,clock,**kwargs)
        ctx = mp.get_context('spawn')
        self.stats = ctx.Array('q',11,lock=False)
        self.slot_lock = ctx.RLock()
        self.halt = ctx.Event()
        self.every, self.directory = every, str(directory)

    @property
    def decoded(self):return self.stats[0] if hasattr(self,'stats') else 0
    @decoded.setter
    def decoded(self,value):pass  # Base initializer's counter is replaced by shared statistics.

    def counts(self):
        acquired = self.slot_lock.acquire(timeout=.05)
        try:
            return dict(decoded_frames=self.stats[0],sampling_skips=self.stats[1],overload_drops=self.stats[2],
                        selected_pending=self.stats[3])
        finally:
            if acquired:self.slot_lock.release()

    def __enter__(self):
        ctx = mp.get_context('spawn')
        self.memory = SharedMemory(create=True,size=self.bounds.max_frame_bytes)
        self.channel, child = ctx.Pipe()
        self.process = ctx.Process(target=decode_latest,args=(str(self.camera.path),self.camera.fingerprint,
            self.generation,child,self.memory.name,self.bounds,self.stats,self.slot_lock,self.halt,self.every,self.directory),daemon=True)
        try:
            self.process.start();child.close();self.opens += 1
            kind, self.metadata = self._receive(self.bounds.open_seconds)
            if kind!='opened' or not math.isfinite(self.metadata['fps']) or not 0<self.metadata['fps']<=240:
                raise InputFault('rtsp_missing_actual_fps')
            return self
        except BaseException:
            self.close();raise

    def read(self):
        import numpy as np
        until = min(self.deadline,time.monotonic()+self.bounds.read_seconds)
        while True:
            if self.stop_event.is_set():
                from .rtsp import InputStopped
                raise InputStopped('controlled_stop')
            acquired = self.slot_lock.acquire(timeout=.02)
            try:
                if acquired and self.stats[3]:
                    _,index,h,w,utc,mono,pts = self.stats[3:10]
                    pixels = np.ndarray((h,w,3),dtype=np.uint8,buffer=self.memory.buf).copy()
                    self.stats[3]=0;self.stats[10]=1
                    receipt = self.clock.capture(index,media_pts=pts/1000000 if pts>=0 else None,
                        sampled_at=(datetime.fromtimestamp(utc/1e9,timezone.utc),mono/1e9))
                    return DecodedFrame(receipt,pixels)
            finally:
                if acquired:self.slot_lock.release()
            if self.channel.poll():self._receive(.1)  # Only opened/error control messages; no frame queue.
            if not self.process.is_alive():raise InputFault('rtsp_decoder_exited')
            if time.monotonic()>=until:raise InputFault('rtsp_read_or_open_timeout')
            time.sleep(.005)

    def close(self):
        if hasattr(self,'halt'):self.halt.set()
        super().close()
