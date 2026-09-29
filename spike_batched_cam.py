"""Batched multi-camera prototype + simple live grid UI (standalone spike).

Proves: one process, N cameras, ALL FOUR models batched on the GPU —
  vehicle detect+track, plate detect, تونس word detect, PaddleOCR — at ≥15 fps.
Touches no production code; imports existing modules read-only.

Run via run_spike.sh (bakes VMS creds), or:
    CHANNELS="4 5" VMS_URL=… VMS_DEVICE_IP=… VMS_PASSWORD=… \
    /home/malek/workflow/.venv_linux/bin/python spike_batched_cam.py [--port 5050] [--no-export]

Open http://localhost:5050 — a tile per camera (annotated MJPEG + fps/tracks/last plate).
Console prints per-stage batched timing every ~2s (the validation numbers).

Staged-batch cascade per tick:
  1) vehicle detect+track (Path A: model.track multi-stream | Path B: batched predict + per-cam BYTETracker)
  2) collect eligible vehicle crops across ALL cameras → batched plate detect
  3) collect plate crops → batched OCR + batched تونس word detect
  4) assign back, annotate, publish per-camera JPEG
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_feed.config as cfg
import plate_recognition.config as pcfg
from common.config import TAXONOMY
from live_feed.vms import DahuaVMSClient

# ── paths / knobs ─────────────────────────────────────────────────────────────
PT_PATH      = Path(cfg.VEHICLE_MODEL_PATH)
ENGINE_PATH  = PT_PATH.with_suffix(".engine")
IMGSZ        = 640
OCR_COOLDOWN = cfg.OCR_COOLDOWN          # frames between plate attempts per unresolved track
MIN_PLATE_W  = cfg.MIN_PLATE_W


def channels() -> list[int]:
    return [int(c) for c in os.environ.get("CHANNELS", str(cfg.LF_CHANNEL)).replace(",", " ").split()]


# live status surfaced in the UI so a blank grid self-explains
STATUS = {"state": "starting", "path": "?", "error": ""}


# ── per-camera state ──────────────────────────────────────────────────────────
@dataclass
class Profile:
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    vtype: str = "unknown"
    plate: str | None = None
    plate_type: str | None = None
    last_frame: int = 0
    last_attempt: int = -10_000


@dataclass
class CamState:
    ch: int
    frame_idx: int = 0
    profiles: dict[int, Profile] = field(default_factory=dict)
    latest_jpeg: bytes | None = None
    _ticks: deque = field(default_factory=lambda: deque(maxlen=30))
    last_plate: str = ""
    last_mean: float = -1.0          # frame brightness (0 ≈ black feed)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def fps(self) -> float:
        if len(self._ticks) < 2:
            return 0.0
        return (len(self._ticks) - 1) / (self._ticks[-1] - self._ticks[0] + 1e-9)


# ── models (loaded once, shared across cameras) ───────────────────────────────
def _trt_available() -> bool:
    import importlib.util
    return any(importlib.util.find_spec(m) for m in ("tensorrt", "tensorrt_cu12"))


class Models:
    def __init__(self, do_export: bool):
        from ultralytics import YOLO
        veh_path = ENGINE_PATH if ENGINE_PATH.exists() else PT_PATH
        if not ENGINE_PATH.exists() and do_export and _trt_available():
            print(f"[models] exporting TensorRT engine from {PT_PATH.name} …")
            try:
                YOLO(str(PT_PATH)).export(format="engine", half=True, dynamic=True,
                                          batch=max(2, len(channels())), imgsz=IMGSZ, device=0)
                veh_path = ENGINE_PATH if ENGINE_PATH.exists() else PT_PATH
            except Exception as e:
                print(f"[models] engine export failed ({e}); using .pt")
        elif not ENGINE_PATH.exists() and do_export:
            print("[models] tensorrt NOT installed → running on .pt (architecture validates the "
                  "same). Add `tensorrt-cu12` to the venv for the TRT speedup, then re-run.")
        print(f"[models] vehicle: {veh_path.name}")
        self.vehicle = YOLO(str(veh_path), task="detect")   # engines lose task metadata → set explicitly
        self.plate   = YOLO(str(pcfg.PLATE_DETECTOR_PATH), task="detect")
        self.tounes  = YOLO(str(pcfg.WORD_DETECTOR_PATH), task="detect")
        self.ocr     = self._load_ocr()

    @staticmethod
    def _load_ocr():
        try:
            from paddleocr import TextRecognition
            eng = TextRecognition(model_name=pcfg.OCR_REC_MODEL, engine="transformers")
            print("[models] OCR: PaddleOCR TextRecognition (transformers)")
            return eng
        except Exception as e:
            print(f"[models] transformers OCR unavailable ({e}); PaddleOCR pipeline fallback")
            from paddleocr import PaddleOCR
            return PaddleOCR(text_recognition_model_name=pcfg.OCR_REC_MODEL,
                             use_doc_orientation_classify=False, use_doc_unwarping=False,
                             use_textline_orientation=False, enable_mkldnn=False)


def _parse_ocr_one(r) -> str:
    d = r if isinstance(r, dict) else (getattr(r, "json", None) or {})
    if isinstance(d, dict) and isinstance(d.get("res"), dict):
        d = d["res"]
    text = ""
    if isinstance(d, dict):
        if d.get("rec_text"):     text = d["rec_text"]
        elif d.get("rec_texts"):  text = " ".join(d["rec_texts"])
        elif d.get("text"):       text = d["text"]
    return re.sub(r"[^0-9A-Za-z]", "", text or "")


def _ocr_batch(models: "Models", crops: list[np.ndarray]) -> list[str]:
    if not crops:
        return []
    try:
        out = list(models.ocr.predict(crops, batch_size=len(crops)))
    except TypeError:
        out = [list(models.ocr.predict(c))[0] for c in crops]   # fallback: per-item
    return [_parse_ocr_one(o) for o in out]


# ── the cascade (operates on one tick's per-camera (frame, boxes)) ────────────
class Cascade:
    def __init__(self, models: "Models", states: dict[int, CamState]):
        self.m = models
        self.states = states
        self.timing = {"vehicle": deque(maxlen=30), "plate": deque(maxlen=30),
                       "ocr": deque(maxlen=30), "tounes": deque(maxlen=30)}

    def run_tick(self, per_cam: list[tuple[int, np.ndarray, object]], veh_ms: float) -> None:
        """per_cam: list of (channel, frame, boxes) for this tick. boxes = ultralytics Boxes."""
        self.timing["vehicle"].append(veh_ms)
        now = time.time()

        # Stage 1 bookkeeping + collect eligible vehicle crops across ALL cameras
        eligible: list[tuple[int, int, np.ndarray]] = []   # (ch, track_id, crop)
        for ch, frame, boxes in per_cam:
            st = self.states[ch]
            st.frame_idx += 1
            st._ticks.append(now)
            st.last_mean = float(frame.mean()) if frame is not None and frame.size else -1.0
            seen = set()
            if boxes is not None and boxes.id is not None:
                ids = boxes.id.int().tolist()
                xyxy = boxes.xyxy.int().tolist()
                clss = boxes.cls.int().tolist()
                for tid, (x1, y1, x2, y2), c in zip(ids, xyxy, clss):
                    seen.add(tid)
                    p = st.profiles.setdefault(tid, Profile())
                    p.bbox = (x1, y1, x2, y2); p.vtype = TAXONOMY.get(c, "unknown"); p.last_frame = st.frame_idx
                    if p.plate is None and (st.frame_idx - p.last_attempt) >= OCR_COOLDOWN:
                        crop = frame[max(0, y1):y2, max(0, x1):x2]
                        if crop.size:
                            eligible.append((ch, tid, crop)); p.last_attempt = st.frame_idx
            # prune long-lost tracks
            for tid in [t for t, p in st.profiles.items() if t not in seen and st.frame_idx - p.last_frame > 30]:
                st.profiles.pop(tid, None)

        # Stage 2 — BATCHED plate detect over all eligible vehicle crops
        plate_items: list[tuple[int, int, np.ndarray]] = []
        if eligible:
            crops = [c for _, _, c in eligible]
            t0 = time.perf_counter()
            results = self.m.plate.predict(crops, conf=pcfg.PLATE_CONF, imgsz=pcfg.DETECT_IMGSZ, verbose=False)
            self.timing["plate"].append((time.perf_counter() - t0) * 1000)
            for (ch, tid, crop), r in zip(eligible, results):
                if r.boxes is not None and len(r.boxes):
                    bi = int(r.boxes.conf.argmax())
                    px1, py1, px2, py2 = (int(v) for v in r.boxes.xyxy[bi].tolist())
                    if (px2 - px1) >= MIN_PLATE_W:
                        pc = crop[max(0, py1):py2, max(0, px1):px2]
                        if pc.size:
                            plate_items.append((ch, tid, pc))

        # Stage 3 — BATCHED OCR + BATCHED تونس over all plate crops
        if plate_items:
            pcs = [pc for _, _, pc in plate_items]
            t0 = time.perf_counter()
            texts = _ocr_batch(self.m, pcs)
            self.timing["ocr"].append((time.perf_counter() - t0) * 1000)

            stretched = [cv2.resize(pc, (pcfg.WORD_IMGSZ, pcfg.WORD_IMGSZ)) for pc in pcs]
            t0 = time.perf_counter()
            tres = self.m.tounes.predict(stretched, conf=pcfg.WORD_CONF, imgsz=pcfg.WORD_IMGSZ, verbose=False)
            self.timing["tounes"].append((time.perf_counter() - t0) * 1000)

            for (ch, tid, _), text, tr in zip(plate_items, texts, tres):
                ptype = "tounes" if (tr.boxes is not None and len(tr.boxes)) else "other"
                st = self.states[ch]; p = st.profiles.get(tid)
                if p and text:
                    p.plate = text; p.plate_type = ptype; st.last_plate = f"{text} ({ptype})"

        # Stage 4 — annotate + publish per camera
        for ch, frame, _ in per_cam:
            self._publish(ch, frame)

    def _publish(self, ch: int, frame: np.ndarray) -> None:
        st = self.states[ch]
        ann = frame.copy()
        for p in st.profiles.values():
            x1, y1, x2, y2 = p.bbox
            cv2.rectangle(ann, (x1, y1), (x2, y2), (0, 200, 255), 2)
            label = p.vtype + (f" | {p.plate} ({p.plate_type})" if p.plate else "")
            cv2.putText(ann, label, (x1 + 2, max(14, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(ann, label, (x1 + 2, max(14, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with st.lock:
                st.latest_jpeg = buf.tobytes()

    def report(self) -> str:
        def avg(k):
            d = self.timing[k]; return sum(d) / len(d) if d else 0.0
        fps = "  ".join(f"ch{ch}={self.states[ch].fps():.1f}fps/b{self.states[ch].last_mean:.0f}"
                        for ch in self.states)
        return (f"[perf] veh={avg('vehicle'):.0f}ms plate={avg('plate'):.0f}ms "
                f"ocr={avg('ocr'):.0f}ms tounes={avg('tounes'):.0f}ms | {fps}")


# ── Main loop — ingest exactly like live_feed (vms.py: one DahuaVMSClient +
#    FrameGrabber per camera) + BATCHED vehicle detect + per-camera BYTETracker
#    + batched plate/OCR/تونس cascade. Only differences vs live_feed: batching +
#    (optional) TensorRT engine. ─────────────────────────────────────────────────
def run_loop(models, states, chans, stop):
    from types import SimpleNamespace
    import yaml
    from ultralytics.trackers.byte_tracker import BYTETracker
    from live_feed.stream import FrameGrabber

    targs = SimpleNamespace(**yaml.safe_load(open(cfg.TRACKER_CONFIG)))
    trackers = {ch: BYTETracker(targs, frame_rate=15) for ch in chans}

    # one VMS client + grabber per camera — same ingestion path as live_feed
    clients, grab = {}, {}
    for ch in chans:
        c = DahuaVMSClient(base_url=cfg.VMS_URL, device_ip=cfg.VMS_DEVICE_IP,
                           device_port=cfg.VMS_DEVICE_PORT, username=cfg.VMS_USERNAME,
                           password=cfg.VMS_PASSWORD)
        c.login()
        g = FrameGrabber(c, ch + cfg.VMS_CHANNEL_OFFSET, cfg.STREAM_TYPE)
        g.start()
        clients[ch], grab[ch] = c, g
    casc = Cascade(models, states)
    print(f"[loop] live_feed-style grabbers + BATCHED predict + per-cam BYTETracker ({len(chans)} cams)")
    try:
        last = time.time()
        while not stop.is_set():
            frames, present = [], []
            for ch in chans:
                f, _ = grab[ch].get_latest()
                if f is not None:
                    frames.append(f); present.append(ch)
            if not frames:
                time.sleep(0.005); continue
            t0 = time.perf_counter()
            dets = models.vehicle.predict(frames, conf=cfg.TRACK_CONF, imgsz=IMGSZ, verbose=False)
            veh_ms = (time.perf_counter() - t0) * 1000
            per_cam = []
            for ch, frame, d in zip(present, frames, dets):
                tracks = trackers[ch].update(d.boxes.cpu().numpy(), frame)
                per_cam.append((ch, frame, _TrackBoxes(tracks)))
            casc.run_tick(per_cam, veh_ms)
            if time.time() - last >= 2:
                print(casc.report()); last = time.time()
    finally:
        for g in grab.values():
            g.stop()
        for c in clients.values():
            c.logout()


class _TrackBoxes:
    """Adapt BYTETracker.update() output [x1,y1,x2,y2,id,score,cls,idx] to the Boxes-like
    interface Cascade expects (.id, .xyxy, .cls as tensors with .int()/.tolist())."""
    def __init__(self, arr: np.ndarray):
        import torch
        self._t = torch.as_tensor(arr.reshape(-1, arr.shape[-1])) if arr.size else torch.zeros((0, 8))
        self.id   = self._t[:, 4] if len(self._t) else None
        self.xyxy = self._t[:, :4]
        self.cls  = self._t[:, 6] if self._t.shape[1] > 6 else torch.zeros(len(self._t))
    def __len__(self):
        return len(self._t)


# ── FastAPI grid UI ───────────────────────────────────────────────────────────
def build_app(states, chans):
    import asyncio
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    app = FastAPI(title="batched-cam spike")

    @app.get("/", response_class=HTMLResponse)
    def index():
        tiles = "".join(
            f'<div class=tile><div class=hdr>ch{ch}</div>'
            f'<img src="/stream/{ch}"><div class=info id="i{ch}">…</div></div>' for ch in chans)
        return f"""<html><head><title>batched cams</title><style>
        body{{background:#111;color:#ddd;font:13px monospace;margin:0;padding:10px}}
        #status{{padding:6px 10px;margin-bottom:10px;background:#1d2b3a;border:1px solid #335}}
        #status.err{{background:#3a1d1d;border-color:#a33}}
        .grid{{display:flex;flex-wrap:wrap;gap:10px}}
        .tile{{background:#000;border:1px solid #333}} .tile img{{display:block;max-width:640px;width:46vw;min-height:120px}}
        .hdr{{padding:4px 8px;background:#222}} .info{{padding:4px 8px;white-space:pre}}
        </style></head><body>
        <div id=status>status: starting…</div>
        <div class=grid>{tiles}</div>
        <script>
        async function tick(){{
          let d=await (await fetch('/info')).json();
          let s=d._status, el=document.getElementById('status');
          el.textContent = `status: ${{s.state}}` + (s.error?`  —  ${{s.error}}`:'');
          el.className = s.state==='ERROR' ? 'err' : '';
          for(const ch in d){{ if(ch[0]==='_') continue;
            let e=document.getElementById('i'+ch);
            if(e)e.textContent=`${{d[ch].fps}} fps  tracks:${{d[ch].tracks}}\\nplate: ${{d[ch].last_plate||'-'}}`;}}}}
        setInterval(tick,1000);tick();
        </script></body></html>"""

    @app.get("/info")
    def info():
        out = {"_status": STATUS}
        for ch in chans:
            out[str(ch)] = {"fps": round(states[ch].fps(), 1),
                            "tracks": len(states[ch].profiles),
                            "last_plate": states[ch].last_plate}
        return JSONResponse(out)

    @app.get("/stream/{ch}")
    def stream(ch: int):
        async def gen():
            while True:
                with states[ch].lock:
                    data = states[ch].latest_jpeg
                if data:
                    yield b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                await asyncio.sleep(1 / 15)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=f")

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    if not cfg.VMS_URL or not cfg.VMS_DEVICE_IP:
        sys.exit("ERROR: set VMS_URL / VMS_DEVICE_IP / VMS_PASSWORD (use run_spike.sh)")
    chans = channels()
    print(f"channels={chans}  port={args.port}")

    states = {ch: CamState(ch=ch) for ch in chans}
    stop = threading.Event()

    # decide Path A vs B: can cv2 open the VMS URLs?
    def cascade_thread():
        import traceback
        try:
            STATUS["state"] = "loading models (first run may download PaddleOCR PP-OCRv6)…"
            print("[status]", STATUS["state"])
            models = Models(do_export=not args.no_export)
            print("[status] models loaded")
            STATUS["state"] = "streaming"
            run_loop(models, states, chans, stop)
        except Exception as e:
            STATUS["state"] = "ERROR"; STATUS["error"] = f"{type(e).__name__}: {e}"
            print("\n[cascade] FATAL — thread died:")
            traceback.print_exc()

    t = threading.Thread(target=cascade_thread, daemon=True, name="cascade")
    t.start()

    import uvicorn
    print(f"→ open http://localhost:{args.port}")
    try:
        uvicorn.run(build_app(states, chans), host="0.0.0.0", port=args.port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
