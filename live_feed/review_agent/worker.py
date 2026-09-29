"""Single-flight review loop.

One pass: read new ReviewInput rows → 2 LLM calls each → reconcile → write outputs →
advance cursor → sleep IDLE_SECONDS. A PID lock prevents two instances running at once.
"""
from __future__ import annotations

import logging
import os
import threading

from live_feed.review_agent import config as cfg
from live_feed.review_agent.client import review_plate, review_vehicle
from live_feed.review_agent.cursor import load_checkpoint, save_checkpoint
from live_feed.review_agent.dataset import read_review_input, write_outputs
from live_feed.review_agent.reconcile import reconcile
from live_feed.review_agent.schemas import ReviewInput
from live_feed.review_agent.smoke import smoke_test

log = logging.getLogger(__name__)

stop_event = threading.Event()


# ── PID lock ──────────────────────────────────────────────────────────────────

def _acquire_lock(cam_id: str) -> bool:
    lock_path = cfg.STATE_DIR / f"ch{cam_id}.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            # Check if that PID is still alive
            os.kill(pid, 0)
            return False   # still alive → someone else owns it
        except (ValueError, ProcessLookupError, PermissionError):
            pass           # stale lock → steal it
    lock_path.write_text(str(os.getpid()))
    return True


def _release_lock(cam_id: str) -> None:
    lock_path = cfg.STATE_DIR / f"ch{cam_id}.lock"
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


# ── Low-confidence filter (cost saver) ───────────────────────────────────────

def _is_uncertain(r: ReviewInput) -> bool:
    return (
        r.type_conf < cfg.AUTO_ACCEPT_CONF
        or r.colour_conf < cfg.AUTO_ACCEPT_CONF
        or (r.digits_conf > 0 and r.digits_conf < cfg.AUTO_ACCEPT_CONF)
        or (r.plate_type_conf > 0 and r.plate_type_conf < cfg.AUTO_ACCEPT_CONF)
    )


# ── Single pass ───────────────────────────────────────────────────────────────

def run_pass(cam_id: str) -> int:
    cp = load_checkpoint(cam_id)
    rows = [r for r in read_review_input(cam_id) if r.record_id > cp.last_record_id]
    rows.sort(key=lambda r: r.record_id)

    if cfg.SEND_LOWCONF_ONLY:
        rows = [r for r in rows if _is_uncertain(r)]

    if not rows:
        return 0

    max_done = cp.last_record_id
    processed = 0
    for r in rows:
        if stop_event.is_set():
            break
        try:
            veh_rev    = review_vehicle(r)
            plate_rev  = review_plate(r)
            rec        = reconcile(r, veh_rev, plate_rev)
            write_outputs(rec, cam_id)
            max_done   = r.record_id
            processed += 1
        except Exception:
            log.warning("review failed record_id=%s — stopping tail", r.record_id, exc_info=True)
            break   # retry the tail next pass

    save_checkpoint(cam_id, max_done)
    return processed


# ── Main loop ─────────────────────────────────────────────────────────────────

def loop(cam_id: str) -> None:
    if not smoke_test():
        log.warning("review_agent DISABLED — smoke test failed; live_feed unaffected")
        return

    if not _acquire_lock(cam_id):
        log.warning("review_agent already running for ch%s — exiting", cam_id)
        return

    log.info("review_agent started for ch%s", cam_id)
    try:
        while not stop_event.is_set():
            n = run_pass(cam_id)
            log.info("review_agent ch%s: pass done (%d reviewed)", cam_id, n)
            stop_event.wait(cfg.IDLE_SECONDS)
    finally:
        _release_lock(cam_id)
        log.info("review_agent ch%s: stopped", cam_id)


def start_thread(cam_id: str) -> threading.Thread:
    t = threading.Thread(
        target=loop,
        args=(cam_id,),
        daemon=True,
        name=f"review-agent-ch{cam_id}",
    )
    t.start()
    return t
