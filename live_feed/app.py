"""FastAPI serving layer for the live-feed pipeline (F3 — batched multi-camera).

One process runs all cameras off a shared TensorRT engine.
Cameras come from CHANNELS env var (space- or comma-separated channel numbers).
Port = LF_PORT_BASE + first_channel  (default ch4 → 5034).

Run:
    CHANNELS="4 1" VMS_URL=… VMS_DEVICE_IP=… VMS_PASSWORD=… \\
    python -m uvicorn live_feed.app:app --host 0.0.0.0 --port 5034

Per-camera endpoints take ?cam=<id>; default is the first camera in CHANNELS.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

import cv2
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

# ── sys.path bootstrap ────────────────────────────────────────────────────────
_VS_DIR = Path(__file__).parent.parent
if str(_VS_DIR) not in sys.path:
    sys.path.insert(0, str(_VS_DIR))

import live_feed.config as cfg
from common.annotate import draw_overlay
from live_feed.multistream import Models, MultiStreamDriver
from live_feed.roi import RoiConfig
from live_feed.schemas import VehicleProfile

log = logging.getLogger(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────

_driver: MultiStreamDriver | None = None
_driver_thread: threading.Thread | None = None
_encoder_threads: dict[str, threading.Thread] = {}
_review_threads:  dict[str, threading.Thread] = {}

_latest_jpeg:         dict[str, bytes | None]       = {}
_jpeg_locks:          dict[str, threading.Lock]     = {}
_stream_clients:      dict[str, int]                = {}
_stream_clients_locks: dict[str, threading.Lock]    = {}
_ws_queues:           dict[str, set[asyncio.Queue]] = {}

_event_loop: asyncio.AbstractEventLoop | None = None
_stop_evt  = threading.Event()
_started   = False
_started_at: float = 0.0
_active_cameras: list[dict] = []   # cameras actually started; set by /start


class StartBody(BaseModel):
    cameras: list[str] | None = None   # None = start all cfg.CAMERAS


# Pre-populate per-camera dicts at import time so endpoints work before /start
for _cam in cfg.CAMERAS:
    _cam_id = _cam["id"]
    _latest_jpeg[_cam_id]           = None
    _jpeg_locks[_cam_id]            = threading.Lock()
    _stream_clients[_cam_id]        = 0
    _stream_clients_locks[_cam_id]  = threading.Lock()
    _ws_queues[_cam_id]             = set()
del _cam, _cam_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_cam(cam: str | None) -> str:
    """Return cam_id, defaulting to the first camera in the registry."""
    if cam is None:
        return cfg.CAMERAS[0]["id"]
    return cam


def _profile_to_dict(p: VehicleProfile) -> dict[str, Any]:
    raw = dataclasses.asdict(p)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _ws_put(q: asyncio.Queue, payload: str) -> None:
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass  # slow client — drop


def _broadcast_profile(cam_id: str, p: VehicleProfile) -> None:
    if _event_loop is None:
        return
    qs = _ws_queues.get(cam_id)
    if not qs:
        return
    payload = json.dumps({
        "type": "profile",
        "event": p.status,
        "cam": cam_id,
        "profile": _profile_to_dict(p),
    })
    for q in list(qs):
        _event_loop.call_soon_threadsafe(_ws_put, q, payload)


def _broadcast_zone_event(cam_id: str, evt: dict) -> None:
    if _event_loop is None:
        return
    qs = _ws_queues.get(cam_id)
    if not qs:
        return
    payload = json.dumps({"type": "zone_event", "cam": cam_id, **evt})
    for q in list(qs):
        _event_loop.call_soon_threadsafe(_ws_put, q, payload)


def _handle_emit(cam_id: str, emitted: list, zone_events: list) -> None:
    """on_emit callback called by the driver after each camera's apply_plate_results."""
    for p in emitted:
        _broadcast_profile(cam_id, p)
    for evt in zone_events:
        _broadcast_zone_event(cam_id, evt)


# ── Encoder loop (one thread per camera) ─────────────────────────────────────

def _encoder_loop(cam_id: str, stop_evt: threading.Event) -> None:
    interval  = 1.0 / max(1, cfg.DISPLAY_FPS)
    next_tick = time.monotonic()
    while not stop_evt.is_set():
        with _stream_clients_locks[cam_id]:
            have_clients = _stream_clients[cam_id] > 0
        if have_clients and _driver is not None:
            frame = _driver.latest_frame(cam_id)
            if frame is not None:
                ann      = frame.copy()
                profiles = _driver.active_profiles(cam_id)
                roi_analytics = _driver.get_roi_analytics(cam_id)
                roi_config    = _driver.get_roi_config(cam_id)
                draw_overlay(ann, profiles.values(),
                             roi_config=roi_config, roi_analytics=roi_analytics)
                if cfg.DISPLAY_MAX_W and ann.shape[1] > cfg.DISPLAY_MAX_W:
                    h = int(ann.shape[0] * cfg.DISPLAY_MAX_W / ann.shape[1])
                    ann = cv2.resize(ann, (cfg.DISPLAY_MAX_W, h))
                ok, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
                if ok:
                    with _jpeg_locks[cam_id]:
                        _latest_jpeg[cam_id] = buf.tobytes()
        next_tick += interval
        sleep_dur = next_tick - time.monotonic()
        if sleep_dur > 0:
            time.sleep(sleep_dur)
        else:
            next_tick = time.monotonic()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    if cfg.AUTO_START:
        asyncio.get_running_loop().create_task(start())
    try:
        yield
    finally:
        if _started:
            _stop_evt.set()
            if _driver:
                _driver.stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="live_feed", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/start")
async def start(body: StartBody | None = None):
    global _driver, _driver_thread, _encoder_threads, _review_threads
    global _latest_jpeg, _jpeg_locks, _stream_clients, _stream_clients_locks, _ws_queues
    global _started, _started_at, _active_cameras

    if _started:
        return {"status": "already_running", "cameras": [c["id"] for c in _active_cameras], "port": cfg.LF_PORT}

    # Accept new VMS_HOST/DAHUA_NVR_IP env names as well as legacy VMS_URL/VMS_DEVICE_IP
    _has_vms = bool(cfg.VMS_URL or os.environ.get("VMS_HOST"))
    _has_nvr = bool(cfg.VMS_DEVICE_IP or os.environ.get("DAHUA_NVR_IP"))
    if not _has_vms or not _has_nvr:
        raise HTTPException(
            status_code=500,
            detail="VMS not configured: set VMS_HOST + DAHUA_NVR_IP (or legacy VMS_URL + VMS_DEVICE_IP)",
        )

    # Filter to requested cameras (or all if not specified)
    cam_filter = set(body.cameras) if body and body.cameras else None
    active_cameras = [c for c in cfg.CAMERAS if cam_filter is None or c["id"] in cam_filter]
    if not active_cameras:
        raise HTTPException(status_code=400, detail="No matching cameras in registry")

    # Reset per-camera JPEG buffers for a clean start
    for cam in active_cameras:
        _latest_jpeg[cam["id"]] = None

    models  = Models()
    _driver = MultiStreamDriver(active_cameras, models, on_emit=_handle_emit)
    _driver.start()

    _stop_evt.clear()
    _driver_thread = threading.Thread(
        target=_driver.run,
        args=(_stop_evt,),
        daemon=False,
        name="driver",
    )
    _driver_thread.start()

    # Per-camera encoder threads
    _encoder_threads = {}
    for cam in active_cameras:
        cam_id = cam["id"]
        t = threading.Thread(
            target=_encoder_loop,
            args=(cam_id, _stop_evt),
            daemon=True,
            name=f"encoder-ch{cam_id}",
        )
        t.start()
        _encoder_threads[cam_id] = t

    # Per-camera review-agent worker threads
    from live_feed.review_agent import worker as _review_worker
    _review_worker.stop_event.clear()
    _review_threads = {}
    for cam in active_cameras:
        cam_id = cam["id"]
        _review_threads[cam_id] = _review_worker.start_thread(cam_id)

    _active_cameras = active_cameras
    _started    = True
    _started_at = time.time()
    log.info("Pipeline started: cameras=%s", [c["id"] for c in active_cameras])
    return {
        "status": "started",
        "cameras": [c["id"] for c in active_cameras],
        "port": cfg.LF_PORT,
    }


@app.post("/stop")
async def stop():
    global _started, _driver, _driver_thread, _encoder_threads, _review_threads

    if not _started:
        raise HTTPException(status_code=400, detail="Pipeline not running")

    _stop_evt.set()

    from live_feed.review_agent import worker as _review_worker
    _review_worker.stop_event.set()

    loop = asyncio.get_event_loop()
    if _driver_thread and _driver_thread.is_alive():
        await loop.run_in_executor(None, _driver_thread.join, 5)
    for t in list(_encoder_threads.values()):
        if t.is_alive():
            await loop.run_in_executor(None, t.join, 3)

    if _driver:
        _driver.stop()
        _driver = None

    _started = False
    log.info("Pipeline stopped")
    return {"status": "stopped", "cameras": [c["id"] for c in cfg.CAMERAS]}


@app.get("/health")
async def health():
    cameras = []
    report_cams = _active_cameras if _active_cameras else cfg.CAMERAS
    for cam in report_cams:
        cam_id = cam["id"]
        active_tracks = 0
        fps = 0.0
        if _driver is not None:
            active_tracks = len(_driver.active_profiles(cam_id))
            fps = round(_driver.perf(cam_id).report_dict().get("fps", 0.0), 1)
        cameras.append({"cam": cam_id, "active_tracks": active_tracks, "fps": fps})

    return {
        "status": "ok",
        "running": _started,
        "started_at": _started_at if _started else None,
        "cameras": cameras,
        "ws_clients": sum(len(qs) for qs in _ws_queues.values()),
    }


@app.get("/cameras")
async def list_cameras():
    """Return available cameras: Hik from cfg.CAMERAS (env) + Dahua discovered from VMS.

    If CHANNELS env var is set, Dahua channels come from cfg.CAMERAS (fast, no VMS call).
    If CHANNELS is empty, the endpoint logs in to the Dahua NVR to fetch active channels.
    """
    from live_feed.live_sources import build_vms_config, discover_dahua_channels

    hik_cams = [
        {"id": c["id"], "type": "hik", "label": f"HIK-{c['id']}", "device_ip": c["device_ip"]}
        for c in cfg.CAMERAS if c["type"] == "hik"
    ]

    dahua_cfg = [c for c in cfg.CAMERAS if c["type"] == "dahua"]
    if dahua_cfg:
        dahua_cams = [
            {"id": c["id"], "type": "dahua", "label": f"CH{c['id']}", "channel": c["channel"]}
            for c in dahua_cfg
        ]
    else:
        try:
            vms_cfg = build_vms_config()
            channels = await asyncio.get_event_loop().run_in_executor(
                None, discover_dahua_channels, vms_cfg
            )
            dahua_cams = [
                {"id": str(ch), "type": "dahua", "label": f"CH{ch}", "channel": ch}
                for ch in channels
            ]
        except Exception as exc:
            log.warning("/cameras: Dahua discovery failed: %s", exc)
            dahua_cams = []

    return {"cameras": dahua_cams + hik_cams}


@app.get("/review/health")
async def review_health(cam: str | None = None):
    cam_id = _resolve_cam(cam)
    from live_feed.review_agent.cursor import load_checkpoint
    cp = load_checkpoint(cam_id)
    return {
        "enabled": True,
        "cam": cam_id,
        "last_record_id": cp.last_record_id,
        "last_pass_at": cp.updated_at,
    }


@app.get("/review/records")
async def review_records(
    cam: str | None = None,
    filter: str = "all",
    limit: int = 50,
    offset: int = 0,
):
    cam_id = _resolve_cam(cam)
    from live_feed.review_agent.dataset import read_corrected_events
    records = read_corrected_events(cam_id)
    if filter in ("trusted", "review_queue"):
        records = [r for r in records if r.routed_to == filter]
    records = list(reversed(records))
    total = len(records)
    page  = records[offset: offset + limit]
    return {"total": total, "cam": cam_id, "records": [r.model_dump() for r in page]}


@app.get("/review/crop/{record_id}")
async def review_crop(record_id: int, type: str = "veh", cam: str | None = None):
    cam_id = _resolve_cam(cam)
    from live_feed.review_agent.dataset import read_corrected_events
    records = read_corrected_events(cam_id)
    rec = next((r for r in records if r.record_id == record_id), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found")
    path = rec.predicted.vehicle_crop_path if type == "veh" else rec.predicted.plate_crop_path
    if not path:
        raise HTTPException(status_code=404, detail="No crop for this type")
    import os
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Crop file missing on disk")
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")


@app.post("/review/verify/{record_id}")
async def review_verify(record_id: int, request: Request, cam: str | None = None):
    cam_id = _resolve_cam(cam)
    corrections = await request.json()
    from live_feed.review_agent.dataset import write_human_verified
    try:
        write_human_verified(record_id, corrections, cam_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "saved", "record_id": record_id, "cam": cam_id}


@app.get("/metrics")
async def metrics(cam: str | None = None):
    cam_id = _resolve_cam(cam)
    if _driver is None:
        raise HTTPException(status_code=400, detail="Pipeline not running")
    return _driver.perf(cam_id).report_dict()


@app.get("/stream.mjpg")
async def stream_mjpg(cam: str | None = None):
    cam_id = _resolve_cam(cam)
    with _stream_clients_locks[cam_id]:
        _stream_clients[cam_id] += 1

    async def _generator() -> AsyncGenerator[bytes, None]:
        try:
            interval = 1.0 / max(1, cfg.DISPLAY_FPS)
            while _started:
                with _jpeg_locks[cam_id]:
                    data = _latest_jpeg[cam_id]
                if data:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + data
                        + b"\r\n"
                    )
                await asyncio.sleep(interval)
        finally:
            with _stream_clients_locks[cam_id]:
                _stream_clients[cam_id] = max(0, _stream_clients[cam_id] - 1)

    return StreamingResponse(
        _generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, cam: str | None = None):
    cam_id = _resolve_cam(cam)
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    _ws_queues.setdefault(cam_id, set()).add(q)
    log.debug("WS client connected cam=%s (total=%d)", cam_id, len(_ws_queues[cam_id]))

    metrics_interval = 1.0 / cfg.WS_METRICS_HZ
    last_metrics     = time.monotonic()

    try:
        # Initial snapshot
        if _driver is not None:
            snapshot = [_profile_to_dict(p) for p in _driver.active_profiles(cam_id).values()]
            await websocket.send_text(
                json.dumps({"type": "snapshot", "cam": cam_id, "profiles": snapshot})
            )

        while True:
            while not q.empty():
                msg = q.get_nowait()
                await websocket.send_text(msg)

            now = time.monotonic()
            if _driver is not None and (now - last_metrics) >= metrics_interval:
                data = _driver.perf(cam_id).report_dict()
                await websocket.send_text(
                    json.dumps({"type": "metrics", "cam": cam_id, "data": data})
                )
                last_metrics = now

            await asyncio.sleep(0.05)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS error cam=%s", cam_id)
    finally:
        _ws_queues.get(cam_id, set()).discard(q)
        log.debug("WS client disconnected cam=%s", cam_id)


@app.get("/snapshot")
async def snapshot(cam: str | None = None):
    """Latest raw (unannotated) frame as JPEG — for the calibration canvas."""
    cam_id = _resolve_cam(cam)
    if _driver is None:
        raise HTTPException(status_code=503, detail="Pipeline not running")
    frame = _driver.latest_frame(cam_id)
    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available yet")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
    if not ok:
        raise HTTPException(status_code=500, detail="encode failed")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/rois")
async def get_rois(cam: str | None = None):
    cam_id = _resolve_cam(cam)
    config = RoiConfig.load(cam_id)
    return {
        "cam": cam_id,
        "camera": config.camera,
        "frame_size": config.frame_size,
        "polygons": [
            {"id": p.id, "name": p.name, "points": p.points, "functions": p.functions}
            for p in config.polygons
        ],
        "lines": [
            {"id": l.id, "name": l.name, "a": l.a, "b": l.b, "count": l.count}
            for l in config.lines
        ],
    }


@app.post("/rois")
async def post_rois(request: Request, cam: str | None = None):
    cam_id = _resolve_cam(cam)
    body   = await request.json()
    if "polygons" not in body or "lines" not in body:
        raise HTTPException(status_code=422, detail="Body must contain 'polygons' and 'lines'")
    for p in body.get("polygons", []):
        if len(p.get("points", [])) < 3:
            raise HTTPException(status_code=422, detail=f"Polygon '{p.get('id')}' needs ≥3 points")
    for l in body.get("lines", []):
        if "a" not in l or "b" not in l:
            raise HTTPException(status_code=422, detail=f"Line '{l.get('id')}' missing 'a' or 'b'")
    RoiConfig.save(cam_id, body)
    if _driver is not None and _started:
        roi_cfg = RoiConfig.load(cam_id)
        _driver.set_roi(cam_id, roi_cfg, time.time())
    return {"status": "saved", "cam": cam_id}


@app.post("/reload-rois")
async def reload_rois(cam: str | None = None):
    cam_id = _resolve_cam(cam)
    if not _started or _driver is None:
        raise HTTPException(status_code=400, detail="Pipeline not running")
    _driver.reload_roi(cam_id, time.time())
    return {"status": "reloaded", "cam": cam_id}


# ── Dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = cfg.LF_PORT
    uvicorn.run("live_feed.app:app", host="0.0.0.0", port=port, log_level="info")
