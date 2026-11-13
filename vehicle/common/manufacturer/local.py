"""Owned, deadline-limited CUDA subprocess using explicit verified artifacts."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from ..db1_rows.core import require
from .engine import hashed


class LocalRuntime:
    def __init__(self, lock_path, *, timeout=30, stop=None):
        require(type(timeout) in (float, int) and 0 < timeout <= 60,
                'INVALID_WINDOW', 'manufacturer timeout must be <=60 seconds')
        self.lock_path = Path(lock_path).resolve(strict=True)
        self.lock = json.loads(self.lock_path.read_text())
        # Relative paths are local to a relocated verified manifest.
        for item in self.lock['artifacts'].values():
            path=Path(item['path']).expanduser()
            item['path']=str(path if path.is_absolute() else self.lock_path.parent/path)
        runtime=Path(self.lock['runtime']).expanduser()
        self.lock['runtime']=str(runtime if runtime.is_absolute() else self.lock_path.parent/runtime)
        self.timeout = timeout
        self.stop = stop
        for item in self.lock['artifacts'].values():
            path = Path(item['path'])
            with path.open('rb') as stream:
                digest = __import__('hashlib').file_digest(stream, 'sha256').hexdigest()
            require(path.stat().st_size == item['bytes'] and digest == item['sha256'],
                    'ARTIFACT_MISMATCH', 'manufacturer artifact changed')
        self.code = {p.name: sha256(p.read_bytes()).hexdigest()
                     for p in Path(__file__).parent.glob('*.py')}
        self.provenance = hashed(dict(lock=self.lock, code=self.code,
                                      timeout=timeout, preprocessing='original-bgr-to-rgb-png-v1'))
        self.children = []
        self.metrics = []

    def infer(self, crop):
        from PIL import Image
        require(crop.dtype.name == 'uint8' and crop.ndim == 3 and crop.shape[2] == 3
                and crop.nbytes <= 8*1024*1024, 'REJECTED_CROP', 'bounded BGR crop required')
        for name, digest in self.code.items():
            require(sha256((Path(__file__).parent/name).read_bytes()).hexdigest() == digest,
                    'ARTIFACT_MISMATCH', 'manufacturer code changed after preflight')
        with Image.fromarray(crop[:, :, ::-1]) as image, io.BytesIO() as buffer:
            image.save(buffer, format='PNG')
            pixels = buffer.getvalue()
        env = dict(PATH=self.lock['runtime']+'/bin:/usr/bin:/bin',
                   LD_LIBRARY_PATH=self.lock['runtime']+'/lib',
                   CUDA_VISIBLE_DEVICES=self.visible_device(),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   PYTHONDONTWRITEBYTECODE='1')
        # No server, shared environment installation, provider, or CPU route.
        command = [self.lock['artifacts']['python']['path'], '-B', '-I',
                   str(Path(__file__).with_name('native_worker.py')), str(self.lock_path)]
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                     stderr=subprocess.DEVNULL, env=env, start_new_session=True)
            self.children.append(child)
            try:
                import time
                deadline = time.monotonic()+self.timeout
                while True:
                    stop = getattr(self,'stop',None)
                    if stop is not None and stop.is_set():
                        raise RuntimeError('manufacturer controlled stop')
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:raise subprocess.TimeoutExpired(command,self.timeout)
                    try:
                        child.communicate(pixels, timeout=min(.1,remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pixels = None  # communicate retains the partially written input
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=5)
                raise TimeoutError('manufacturer owned process deadline') from None
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            output.seek(0)
            raw = output.read(4097)
        require(child.returncode == 0 and len(raw) <= 4096,
                'MANUFACTURER_EXECUTION_FAILED', 'owned CUDA worker did not complete')
        result = json.loads(raw)
        require(set(result) == {'outcome','text','gpu_memory'} and result['outcome'] in {'completed','malformed'},
                'INVALID_MANUFACTURER', 'invalid native result')
        self.metrics.append(result['gpu_memory'])
        # A token-limit result remains a completed native call with malformed
        # bounded text. Policy will reject the deliberately empty response.
        return 'completed', result['text']

    @property
    def cleaned(self):
        return all(child.poll() is not None for child in self.children)

    @staticmethod
    def visible_device():
        index=int(os.environ.get('T1_CONSOLE_DEVICE','cuda:0').split(':')[-1])
        visible=os.environ.get('CUDA_VISIBLE_DEVICES')
        return visible.split(',')[index].strip() if visible else str(index)
