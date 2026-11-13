"""Owned Unix-socket Redis for explicit v3 review/tests; never a shared server."""
import os
from pathlib import Path
import subprocess
import tempfile
import time


def client_preflight():
    """Check transport before model initialization or opening a decoder."""
    try:
        import redis
    except ImportError:
        raise RuntimeError("missing_runtime_package_redis") from None
    required = ("eval", "xautoclaim", "xreadgroup", "xpending", "xgroup_create")
    if not all(callable(getattr(redis.Redis, name, None)) for name in required):
        raise RuntimeError("incompatible_runtime_package_redis")
    return redis


class OwnedRedis:
    def __init__(self, executable):
        self.executable = Path(executable).resolve(strict=True)
        self.directory = None
        self.process = None
        self.client = None
        self.cleaned = False

    def __enter__(self):
        redis = client_preflight()
        self.directory = tempfile.TemporaryDirectory(prefix="lpr-v3-")
        self.path = Path(self.directory.name)
        os.chmod(self.path, 0o700)
        self.socket = self.path / "redis.sock"
        self.process = subprocess.Popen([str(self.executable), "--port", "0", "--unixsocket", str(self.socket),
            "--unixsocketperm", "700", "--protected-mode", "yes", "--appendonly", "yes", "--appendfsync", "always",
            "--save", "", "--dir", str(self.path), "--logfile", str(self.path / "redis.log")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.client = redis.Redis(unix_socket_path=str(self.socket), socket_timeout=2)
        deadline = time.monotonic() + 10
        while True:
            try:
                if self.client.ping():
                    return self
            except redis.RedisError:
                pass
            if self.process.poll() is not None or time.monotonic() >= deadline:
                self.__exit__(None, None, None)
                raise RuntimeError("owned_v3_redis_start_failed")
            time.sleep(.02)

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.client is not None:
            self.client.close()
        if self.directory is not None:
            self.directory.cleanup()
        self.cleaned = (self.process is None or self.process.poll() is not None) and not self.path.exists()
