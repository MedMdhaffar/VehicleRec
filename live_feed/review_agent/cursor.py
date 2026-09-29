"""Atomic checkpoint (resume cursor) and per-channel monotonic record_id counter.

Checkpoint: live_feed/review_agent/state/ch{N}_checkpoint.json
Sequence:   live_feed/review_agent/state/ch{N}_seq.txt

Both writes go through os.replace so they are crash-safe.
The record_id counter uses a per-channel threading.Lock — the pipeline
thread is the only writer, so contention is essentially zero.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from live_feed.review_agent import config as cfg
from live_feed.review_agent.schemas import ReviewCheckpoint

# ── Per-camera locks for the sequence counter ─────────────────────────────────
_seq_locks: dict[str, threading.Lock] = {}
_seq_locks_mu = threading.Lock()


def _seq_lock(cam_id: str) -> threading.Lock:
    with _seq_locks_mu:
        if cam_id not in _seq_locks:
            _seq_locks[cam_id] = threading.Lock()
        return _seq_locks[cam_id]


def _seq_path(cam_id: str) -> Path:
    return cfg.STATE_DIR / f"ch{cam_id}_seq.txt"


def _checkpoint_path(cam_id: str) -> Path:
    return cfg.STATE_DIR / f"ch{cam_id}_checkpoint.json"


# ── Record-id sequence ────────────────────────────────────────────────────────

def next_record_id(cam_id: str) -> int:
    """Return the next monotonic record_id for this camera; persists across restarts."""
    path = _seq_path(cam_id)
    with _seq_lock(cam_id):
        try:
            current = int(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            current = 0
        new_id = current + 1
        tmp = path.with_suffix(".tmp")
        tmp.write_text(str(new_id))
        os.replace(tmp, path)
        return new_id


# ── Review checkpoint ─────────────────────────────────────────────────────────

def load_checkpoint(cam_id: str) -> ReviewCheckpoint:
    path = _checkpoint_path(cam_id)
    try:
        data = json.loads(path.read_text())
        return ReviewCheckpoint(**data)
    except (FileNotFoundError, ValueError, KeyError):
        return ReviewCheckpoint(channel=cam_id)


def save_checkpoint(cam_id: str, last_record_id: int) -> None:
    cp = ReviewCheckpoint(
        channel=cam_id,
        last_record_id=last_record_id,
        updated_at=time.time(),
    )
    path = _checkpoint_path(cam_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(cp.model_dump_json())
    os.replace(tmp, path)
