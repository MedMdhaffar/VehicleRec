"""Continuous structured (JSONL) logging for the live-feed pipeline.

Two rotating JSONL files per camera under live_feed/logs/:
  ch{N}_events.jsonl  — one line per emitted VehicleProfile (event = "active" | "final")
  ch{N}_perf.jsonl    — periodic perf snapshots (fps, per-stage ms, e2e latency, ocr rate)

Per-camera files (one process = one camera → no write contention). Thread-safe
(stdlib RotatingFileHandler locks internally) and cheap (events are per-vehicle,
perf is every ~2 s — never on the per-frame hot path).

Observe live:
    tail -f live_feed/logs/ch4_events.jsonl
    tail -f live_feed/logs/ch4_perf.jsonl
Analyze:
    import pandas as pd; pd.read_json("ch4_events.jsonl", lines=True)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from live_feed.schemas import VehicleProfile

LOG_DIR = Path(__file__).parent / "logs"
_MAX_BYTES = 50_000_000   # 50 MB per file
_BACKUPS = 5              # keep 5 rotated files


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_jsonl_logger(name: str, path: Path) -> logging.Logger:
    lg = logging.getLogger(name)
    lg.setLevel(logging.INFO)
    lg.propagate = False  # don't leak JSON lines into the console root logger
    # avoid duplicate handlers if re-initialised (e.g. /start called twice)
    if not any(isinstance(h, RotatingFileHandler) for h in lg.handlers):
        h = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))  # message is already a JSON line
        lg.addHandler(h)
    return lg


class LiveLogger:
    """Per-camera JSONL logger for vehicle events + perf snapshots."""

    def __init__(self, cam_id: str, log_dir: Path = LOG_DIR) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.cam_id = cam_id
        self.channel = cam_id  # kept for JSON-field backward compat
        self.events_path = log_dir / f"ch{cam_id}_events.jsonl"
        self.perf_path = log_dir / f"ch{cam_id}_perf.jsonl"
        self._events = _make_jsonl_logger(f"live_feed.events.ch{cam_id}", self.events_path)
        self._perf = _make_jsonl_logger(f"live_feed.perf.ch{cam_id}", self.perf_path)
        self._write_perf({"event": "session_start"})

    # ── Vehicle events ─────────────────────────────────────────────────────────

    def log_event(self, p: VehicleProfile, event: str) -> None:
        """One line per emitted profile. `event` = profile.status ("active"|"final")."""
        rec = {
            "ts": _utc_now(),
            "channel": self.channel,
            "event": event,
            "track_id": p.track_id,
            "type": p.type,
            "type_conf": round(p.type_conf, 4),
            "colour": p.colour,
            "colour_conf": round(p.colour_conf, 4),
            "plate": {
                "number": p.plate.number,
                "digits_conf": round(p.plate.digits_conf, 4),
                "type": p.plate.type,
                "type_conf": round(p.plate.type_conf, 4),
                "ocr_attempts": p.plate.ocr_attempts,
                "locked": p.plate.locked,
            },
            "frames_seen": p.frames_seen,
            "duration_s": round(p.last_seen - p.first_seen, 2),
            # accuracy-debug: the raw votes/samples that produced the locked type/colour
            "type_votes": list(p._type_votes),
            "colour_attempts": p._colour_attempts,
            "unknown_colour_samples": p._unknown_colour_samples,
            "colour_decision": p._colour_decision,
            "colour_samples": [
                {
                    "label": sample.label,
                    "confidence": round(sample.confidence, 4),
                    "quality": round(sample.quality, 4),
                    "weight": round(sample.weight, 6),
                }
                for sample in p._colour_samples
            ],
        }
        self._events.info(json.dumps(rec, ensure_ascii=False))

    # ── Perf snapshots ─────────────────────────────────────────────────────────

    def log_perf(self, perf_dict: dict) -> None:
        self._write_perf(perf_dict)

    def _write_perf(self, payload: dict) -> None:
        rec = {"ts": _utc_now(), "channel": self.channel, **payload}
        self._perf.info(json.dumps(rec))
