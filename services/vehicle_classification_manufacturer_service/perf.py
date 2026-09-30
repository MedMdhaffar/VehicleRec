"""Rolling performance meter adapted from live_feed.perf."""
from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager


class PerfMeter:
    def __init__(self) -> None:
        self._stages: dict[str, deque[float]] = {}
        self._frame_ts: deque[float] = deque(maxlen=30)
        self._active_tracks = 0
        self._start = time.monotonic()

    @contextmanager
    def stage(self, name: str):
        values = self._stages.setdefault(name, deque(maxlen=30))
        started = time.perf_counter()
        try:
            yield
        finally:
            values.append((time.perf_counter() - started) * 1000)

    def frame_done(self, active_tracks: int) -> None:
        self._active_tracks = active_tracks
        self._frame_ts.append(time.monotonic())

    def report_dict(self) -> dict:
        fps = 0.0
        if len(self._frame_ts) > 1:
            fps = (len(self._frame_ts) - 1) / (self._frame_ts[-1] - self._frame_ts[0])
        return {
            "fps": round(fps, 2),
            "elapsed_seconds": round(time.monotonic() - self._start, 3),
            "active_tracks": self._active_tracks,
            "stages": {
                name: {"avg_ms": round(sum(v) / len(v), 2), "p95_ms": round(sorted(v)[int(.95 * len(v))], 2)}
                for name, v in self._stages.items() if v
            },
        }
