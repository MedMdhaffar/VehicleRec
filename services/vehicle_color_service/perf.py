from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

_WINDOW = 30  # rolling frame count (~2 s at 15 FPS)


@dataclass
class _StageStats:
    times: deque = field(default_factory=lambda: deque(maxlen=_WINDOW))

    def record(self, ms: float) -> None:
        self.times.append(ms)

    def avg(self) -> float:
        return sum(self.times) / len(self.times) if self.times else 0.0

    def p95(self) -> float:
        if not self.times:
            return 0.0
        s = sorted(self.times)
        return s[int(0.95 * len(s))]


class PerfMeter:
    def __init__(self) -> None:
        self._stages: dict[str, _StageStats] = {}
        self._e2e: deque[float] = deque(maxlen=_WINDOW)
        self._frame_ts: deque[float] = deque(maxlen=_WINDOW)  # monotonic, for FPS
        self._ocr_calls: int = 0
        self._active_tracks: int = 0
        self._plate_calls: deque[int] = deque(maxlen=_WINDOW)  # plate_detect calls per frame
        self._cur_plate_calls: int = 0
        self._start: float = time.monotonic()
        self._last_report: float = time.monotonic()

    # ── Stage instrumentation ─────────────────────────────────────────────────

    @contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        if name not in self._stages:
            self._stages[name] = _StageStats()
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._stages[name].record((time.perf_counter() - t0) * 1000)

    # ── Per-frame accounting ──────────────────────────────────────────────────

    def tick(self, frame_capture_ts: float) -> None:
        now = time.monotonic()
        self._e2e.append((now - frame_capture_ts) * 1000)
        self._frame_ts.append(now)

    def inc_ocr(self) -> None:
        self._ocr_calls += 1

    def inc_plate_call(self) -> None:
        self._cur_plate_calls += 1

    def frame_done(self) -> None:
        """Call once per processed frame — rolls up the per-frame plate_detect count."""
        self._plate_calls.append(self._cur_plate_calls)
        self._cur_plate_calls = 0

    def _plate_calls_avg(self) -> float:
        return sum(self._plate_calls) / len(self._plate_calls) if self._plate_calls else 0.0

    def set_active_tracks(self, n: int) -> None:
        self._active_tracks = n

    # ── Reporting ─────────────────────────────────────────────────────────────

    def _fps(self) -> float:
        if len(self._frame_ts) < 2:
            return 0.0
        return (len(self._frame_ts) - 1) / (self._frame_ts[-1] - self._frame_ts[0])

    def _e2e_avg(self) -> float:
        return sum(self._e2e) / len(self._e2e) if self._e2e else 0.0

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _ocr_rate(self) -> float:
        elapsed = self._elapsed()
        return self._ocr_calls / elapsed if elapsed > 0 else 0.0

    def report(self) -> str:
        stage_parts = []
        for name, s in self._stages.items():
            if s and s.times:
                stage_parts.append(f"{name}={s.avg():.1f}ms(p95={s.p95():.1f})")
        stages_str = " ".join(stage_parts) if stage_parts else "no data"
        return (
            f"[perf] fps={self._fps():.1f} e2e={self._e2e_avg():.0f}ms "
            f"tracks={self._active_tracks} pdet/frame={self._plate_calls_avg():.1f} "
            f"ocr={self._ocr_rate():.2f}/s | {stages_str}"
        )

    def report_dict(self) -> dict:
        stages: dict = {}
        for name, s in self._stages.items():   # all stages incl. grab/encode/broadcast
            if s and s.times:
                stages[name] = {"avg_ms": round(s.avg(), 2), "p95_ms": round(s.p95(), 2)}
        return {
            "fps": round(self._fps(), 2),
            "e2e_ms": round(self._e2e_avg(), 1),
            "active_tracks": self._active_tracks,
            "plate_calls_per_frame": round(self._plate_calls_avg(), 2),
            "ocr_rate": round(self._ocr_rate(), 3),
            "stages": stages,
        }

    def final_report(self) -> str:
        elapsed = self._elapsed()
        stage_parts = []
        for name in ("track", "colour", "plate_detect", "ocr"):
            s = self._stages.get(name)
            if s and s.times:
                stage_parts.append(f"  {name}: avg={s.avg():.1f}ms p95={s.p95():.1f}ms")
        return (
            f"\n=== LF-1 final perf ({elapsed:.1f}s) ===\n"
            f"  avg fps   : {self._fps():.2f}\n"
            f"  e2e latency: {self._e2e_avg():.0f}ms avg\n"
            f"  ocr total : {self._ocr_calls} ({self._ocr_rate():.3f}/s)\n"
            + "\n".join(stage_parts)
        )
