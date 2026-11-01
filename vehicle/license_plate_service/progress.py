"""Request diagnostics while native inference or upload parsing is blocking."""
from contextlib import contextmanager
import logging
import threading
import time


log = logging.getLogger(__name__)


@contextmanager
def video_request_progress(job_id, *, interval=10.0):
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(interval):
            log.info(
                "Video job %s: request still active (%.0fs elapsed); "
                "see last pipeline stage/frame log for progress",
                job_id, time.monotonic() - started,
            )

    log.info("Video job %s: request reached Django; reading upload", job_id)
    worker = threading.Thread(target=heartbeat, daemon=True, name=f"video-progress-{job_id}")
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        log.info("Video job %s: request finished after %.1fs", job_id, time.monotonic() - started)
