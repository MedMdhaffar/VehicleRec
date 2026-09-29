"""Standalone spike — validate TensorRT + Ultralytics multi-stream batched tracking on the
live Dahua VMS, BEFORE touching live_feed. Touches no production code.

Run from vehicle_services/ with the same VMS env the service uses:

    CHANNELS="4 5" \
    VMS_URL=http://<host>:5057 VMS_DEVICE_IP=192.168.40.200 VMS_PASSWORD='...' \
    /home/malek/workflow/.venv_linux/bin/python spike_multistream_trt.py [--seconds 20] [--no-export]

What it checks, in order:
  1. tensorrt importable (Ultralytics needs it to load .engine).
  2. Export vehicle_yolo26.pt -> vehicle_yolo26.engine (FP16, dynamic batch) if missing.
  3. Sanity: engine vs .pt detection counts on one real VMS frame.
  4. Path A: can cv2 open the VMS MJPEG URLs? If yes -> model.track(urls, stream=True) and
     measure per-stream FPS + track-id behavior (the official multi-stream pattern).
  5. Path B (fallback if A fails): our FrameGrabber per camera + batched model.predict, FPS.

Prints a clear VERDICT (which path works, per-stream fps, engine ok) so we know how to integrate.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# ── make the package importable when run from vehicle_services/ ──
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402

import live_feed.config as cfg  # noqa: E402
from live_feed.vms import DahuaVMSClient  # noqa: E402

PT_PATH = Path(cfg.VEHICLE_MODEL_PATH)
ENGINE_PATH = PT_PATH.with_suffix(".engine")


def banner(msg: str) -> None:
    print(f"\n{'='*70}\n{msg}\n{'='*70}")


def channels() -> list[int]:
    raw = os.environ.get("CHANNELS", str(cfg.LF_CHANNEL))
    return [int(c) for c in raw.replace(",", " ").split()]


# ── 1 & 2: tensorrt + engine export ──────────────────────────────────────────

def ensure_engine(chans: list[int], do_export: bool) -> bool:
    banner("1) tensorrt import + 2) engine export")
    try:
        import tensorrt  # noqa: F401
        print(f"  ✓ tensorrt {tensorrt.__version__} importable")
    except Exception as e:
        print(f"  ✗ tensorrt NOT importable: {e}\n    → add `tensorrt` to requirements/image before integrating")
        # not fatal for Path-A/B detection sanity if engine already exists
    if ENGINE_PATH.exists():
        print(f"  ✓ engine already present: {ENGINE_PATH}")
        return True
    if not do_export:
        print(f"  — engine missing and --no-export set; will run on .pt only")
        return False
    if not PT_PATH.exists():
        print(f"  ✗ source weights missing: {PT_PATH}")
        return False
    from ultralytics import YOLO
    print(f"  exporting {PT_PATH.name} → engine (FP16, dynamic, batch={max(2, len(chans))}) …")
    try:
        YOLO(str(PT_PATH)).export(
            format="engine", half=True, dynamic=True,
            batch=max(2, len(chans)), imgsz=640, device=0,
        )
        ok = ENGINE_PATH.exists()
        print("  ✓ engine exported" if ok else "  ✗ export ran but .engine not found")
        return ok
    except Exception as e:
        print(f"  ✗ export failed: {e}")
        return False


# ── VMS helpers ───────────────────────────────────────────────────────────────

def open_vms_sources(chans: list[int]):
    """Return (client, [(channel, sdk_ch, stream_id, url)]) — sources started on the VMS."""
    client = DahuaVMSClient(
        base_url=cfg.VMS_URL, device_ip=cfg.VMS_DEVICE_IP, device_port=cfg.VMS_DEVICE_PORT,
        username=cfg.VMS_USERNAME, password=cfg.VMS_PASSWORD,
    )
    client.login()
    base = cfg.VMS_URL.rstrip("/")
    sources = []
    for ch in chans:
        sdk = ch + cfg.VMS_CHANNEL_OFFSET
        sid = client.start_stream(sdk, cfg.STREAM_TYPE)
        sources.append((ch, sdk, sid, f"{base}/realplay/stream/{sid}"))
    return client, sources


# ── 3: detection sanity (engine vs pt) ────────────────────────────────────────

def sanity_engine_vs_pt(sources, have_engine: bool) -> None:
    banner("3) sanity: engine vs .pt on one real VMS frame")
    from ultralytics import YOLO
    # grab one frame from the first source via our client iterator
    client2, srcs = open_vms_sources([sources[0][0]])
    frame = None
    try:
        for f in client2.iter_frames(srcs[0][2]):
            frame = f; break
    finally:
        client2.stop_stream(srcs[0][2]); client2.logout()
    if frame is None:
        print("  ✗ could not grab a frame for sanity"); return
    pt = YOLO(str(PT_PATH))
    n_pt = len(pt.predict(frame, conf=cfg.TRACK_CONF, imgsz=640, verbose=False)[0].boxes)
    print(f"  .pt detections: {n_pt}")
    if have_engine:
        eng = YOLO(str(ENGINE_PATH))
        n_e = len(eng.predict(frame, conf=cfg.TRACK_CONF, imgsz=640, verbose=False)[0].boxes)
        print(f"  .engine detections: {n_e}  → {'≈ match' if abs(n_e-n_pt)<=2 else 'DIVERGENT (investigate)'}")


# ── 4: Path A — Ultralytics opens the VMS MJPEG URLs ──────────────────────────

def path_a(chans: list[int], seconds: int, have_engine: bool) -> bool:
    banner("4) Path A — can Ultralytics batch-track the VMS MJPEG URLs?")
    client, sources = open_vms_sources(chans)
    try:
        # 4a. can cv2 even open the URLs?
        openable = True
        for ch, sdk, sid, url in sources:
            cap = cv2.VideoCapture(url)
            ok = cap.isOpened() and cap.read()[0]
            cap.release()
            print(f"  ch{ch}: cv2 open {'✓' if ok else '✗'}  ({url})")
            openable = openable and ok
        if not openable:
            print("  → Path A NOT viable (cv2 cannot open the VMS URL). Use Path B.")
            return False

        # 4b. multi-stream batched track
        from ultralytics import YOLO
        model = YOLO(str(ENGINE_PATH if have_engine else PT_PATH))
        urls = [s[3] for s in sources]
        print(f"  running model.track on {len(urls)} streams for ~{seconds}s …")
        counts = defaultdict(int); ids = defaultdict(set)
        t0 = time.time()
        for results in model.track(urls, stream=True, persist=True,
                                   conf=cfg.TRACK_CONF, tracker=cfg.TRACKER_CONFIG, verbose=False):
            for idx, r in enumerate(results):
                ch = chans[idx]; counts[ch] += 1
                if r.boxes is not None and r.boxes.id is not None:
                    ids[ch].update(int(i) for i in r.boxes.id.tolist())
            if time.time() - t0 >= seconds:
                break
        dt = time.time() - t0
        print("  per-stream results:")
        for ch in chans:
            print(f"    ch{ch}: {counts[ch]/dt:5.1f} fps   unique track_ids={len(ids[ch])}")
        print("  → Path A WORKS ✓ (official multi-stream pattern is viable)")
        return True
    finally:
        for ch, sdk, sid, url in sources:
            client.stop_stream(sid)
        client.logout()


# ── 5: Path B — our FrameGrabber + batched predict ────────────────────────────

def path_b(chans: list[int], seconds: int, have_engine: bool) -> None:
    banner("5) Path B — our FrameGrabber per camera + batched model.predict")
    from ultralytics import YOLO
    from live_feed.stream import FrameGrabber
    client = DahuaVMSClient(
        base_url=cfg.VMS_URL, device_ip=cfg.VMS_DEVICE_IP, device_port=cfg.VMS_DEVICE_PORT,
        username=cfg.VMS_USERNAME, password=cfg.VMS_PASSWORD,
    )
    client.login()
    grabbers = {}
    for ch in chans:
        g = FrameGrabber(client, ch + cfg.VMS_CHANNEL_OFFSET, cfg.STREAM_TYPE)
        g.start(); grabbers[ch] = g
    model = YOLO(str(ENGINE_PATH if have_engine else PT_PATH))
    counts = defaultdict(int); t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            batch, present = [], []
            for ch in chans:
                f, _ = grabbers[ch].get_latest()
                if f is not None:
                    batch.append(f); present.append(ch)
            if not batch:
                time.sleep(0.01); continue
            model.predict(batch, conf=cfg.TRACK_CONF, imgsz=640, verbose=False)  # batched detection
            for ch in present:
                counts[ch] += 1
        dt = time.time() - t0
        print("  per-stream batched-predict fps:")
        for ch in chans:
            print(f"    ch{ch}: {counts[ch]/dt:5.1f} fps")
        print("  → Path B WORKS ✓ (full control of the VMS lifecycle; manual per-cam tracker needed for integration)")
    finally:
        for g in grabbers.values():
            g.stop()
        client.logout()


def main() -> None:
    ap = argparse.ArgumentParser(description="Spike: TensorRT + multi-stream on the VMS")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--no-export", action="store_true", help="don't export the engine if missing")
    args = ap.parse_args()

    if not cfg.VMS_URL or not cfg.VMS_DEVICE_IP:
        sys.exit("ERROR: set VMS_URL and VMS_DEVICE_IP (and VMS_PASSWORD) in the environment")
    chans = channels()
    print(f"channels: {chans}  |  vehicle weights: {PT_PATH}")

    have_engine = ensure_engine(chans, do_export=not args.no_export)
    try:
        client, sources = open_vms_sources(chans)
        for ch, sdk, sid, url in sources:  # close them; the path fns re-open
            client.stop_stream(sid)
        client.logout()
    except Exception as e:
        sys.exit(f"ERROR: VMS login/start failed: {e}")

    sanity_engine_vs_pt(sources, have_engine)
    a_ok = path_a(chans, args.seconds, have_engine)
    if not a_ok:
        path_b(chans, args.seconds, have_engine)

    banner("VERDICT")
    print(f"  engine present : {have_engine}")
    print(f"  integration path: {'A (model.track on VMS URLs)' if a_ok else 'B (grabbers + batched predict + manual per-cam tracker)'}")
    print("  → use this to finalize Task 1 of SPRINT_F2.")


if __name__ == "__main__":
    main()
