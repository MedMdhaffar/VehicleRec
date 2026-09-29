# Sprint F2 — TensorRT + single-process multi-stream (shared model, batched)

Replace the **one-process-per-camera** design with **one process running N cameras off a single
shared TensorRT model**, using Ultralytics' native multi-stream tracking. Kills the N× model
duplication ("model loading per camera is a waste"), batches detection on the GPU, and targets
**2–4 cameras at ≥15 fps**. This **supersedes Sprint G's per-camera compose** → one container,
`CHANNELS="4 5"`.

> Basis: Ultralytics "Multi-Stream Inference" (Academy). Recommended pattern: **producers
> (decoders) + a single batched GPU consumer**, via `model.track(sources, stream=True,
> persist=True)` — Ultralytics opens a thread per source, **batches frames into one model call**,
> and keeps **per-stream tracking state** (`box.id` unique per stream). Capacity ref: yolo26m
> ≈ 12–20 streams on one A10/T4 FP16 → 2–4 cams is trivial headroom.

Read `HOW_IT_WORKS.md` (current single-camera pipeline) first. Do Sprint F (cleanup) first.

---

## 0. Architecture: before → after

```
BEFORE (per process):  FrameGrabber → EnrichedTracker.process → model.track(frame)   ×N processes
                       (N× model in VRAM, N× load)

AFTER (one process, N cameras):
   Ultralytics multi-stream driver (ONE shared yolo26 .engine)
     model.track([src_ch4, src_ch5, ...], stream=True, persist=True)
        │  (Ultralytics: thread-per-source decode + BATCHED inference + per-stream ByteTrack)
        ▼  yields per tick: [Results_ch4, Results_ch5, ...]
   for each stream r:  CameraPipeline[ch].process_detections(r, frame_idx[ch])
        ├─ classify-once, plate cascade, ROI, dedup, finalize   (per-camera state, unchanged logic)
        ├─ publish _latest_frame[ch]  (from r.orig_img)
        └─ broadcast WS[ch] + review_writer[ch]
   Shared singletons: plate detector, OCR, تونس word detector  (loaded once, called per-crop)
   Per camera: encoder thread, review-agent worker, livelog, perf, ROI, anchors
```

The vehicle detector + tracking become **shared + batched**; everything downstream stays
**per-camera** (it already operates on boxes). Plate/OCR/word models are shared singletons
(rare, gated calls) — pairs with Sprint F's OCR-singleton reuse.

---

## Task 0 — De-risk + TensorRT (do first, isolated)

**0a. TensorRT FP16 engine** (`training/export_trt.py` or a one-off):
```python
from ultralytics import YOLO
YOLO("common/models/vehicle_yolo26.pt").export(
    format="engine", half=True, dynamic=True, batch=4, imgsz=640, device=0)
# → common/models/vehicle_yolo26.engine
```
- **Engine is GPU/driver/TRT-version specific — NOT portable.** Do **not** bake the `.engine` in
  the image; **build it at container startup** (entrypoint: export if `.engine` missing) into a
  volume, or build per host. Keep `.pt` as the source of truth.
- `dynamic=True, batch=4` so one engine batches up to 4 streams (raise for more cameras).

**0b. VMS-source spike (the one real risk):** can Ultralytics/cv2 open the VMS MJPEG stream URL?
The current `DahuaVMSClient` does `login → start_stream(sdk_ch) → realplay/stream/{stream_id}`
(MJPEG/HTTP). Test:
```python
import cv2
# after client.login() + sid = client.start_stream(sdk_ch, stream_type):
url = f"{base_url}/realplay/stream/{sid}"
cap = cv2.VideoCapture(url)         # can cv2 open it?
print(cap.isOpened(), cap.read()[0])
```
- **If YES** → Path A (below): hand the N URLs to `model.track(urls, stream=True)`. Clean, official.
- **If NO** (session/headers/codec issues) → Path B: keep our `FrameGrabber`s, collect the latest
  frame per camera each tick, and call `model.predict(list_of_frames)` (batched detection) + feed
  each camera's boxes to a per-camera `ultralytics.trackers.byte_tracker.BYTETracker` instance.
  More code, full control, no dependency on cv2 opening the VMS URL.

**Decide A vs B from the spike before building Task 1.**

---

## Task 1 — Shared-model multi-stream driver (`live_feed/multistream.py`)

A single driver owning the shared model + the multi-stream loop.

**Path A (preferred):**
```python
class MultiStreamDriver:
    def __init__(self, channels: list[int]):
        self.channels = channels
        self.model = YOLO(cfg.VEHICLE_ENGINE_PATH)        # ONE shared engine
        self.cams = {ch: CameraPipeline(ch) for ch in channels}   # per-camera state (Task 2)
        self.sources = []   # built from VMS: login + start_stream per ch → MJPEG URL (ordered == channels)

    def run(self, stop_evt):
        for results in self.model.track(self.sources, stream=True, persist=True,
                                        conf=cfg.TRACK_CONF, tracker=cfg.TRACKER_CONFIG,
                                        verbose=False):
            if stop_evt.is_set(): break
            for idx, r in enumerate(results):
                ch = self.channels[idx]
                self.cams[ch].process_detections(r)     # frame = r.orig_img, boxes = r.boxes
```
- `sources` order must map 1:1 to `self.channels` (stream_idx → channel).
- VMS session lifecycle (login/keepalive/logout) for all channels handled here; on stream drop,
  Ultralytics reconnects, but VMS session expiry needs a watchdog (re-login + rebuild sources).

**Path B (fallback):** per-tick `frames = [grab[ch].get_latest() for ch in channels]`;
`results = self.model.predict(frames, conf=..., imgsz=640)`; per camera feed `r.boxes` to its
`BYTETracker.update(...)`; then `cams[ch].process_detections(tracked)`. (Verify the 8.4.46
`BYTETracker.update` signature in the spike.)

---

## Task 2 — Refactor `EnrichedTracker` → per-camera `CameraPipeline`

Split detection (now external/shared) from the per-camera pipeline. The existing
`EnrichedTracker.process` body **after** the `model.track` call moves nearly verbatim into
`CameraPipeline.process_detections(results)`:
- Keep per-camera: `_active` profiles, `_anchors` (dedup), `_roi`, `_review_writer`, `_perf`,
  `_last_*` frame counters, best-crop, classify-once, plate cascade, ROI `process_frame`,
  finalize. **Unchanged logic** — it already reads `results.boxes`.
- **Remove** from it: model ownership + the `model.track(...)` call (now in the driver) and
  `_ensure_loaded` of the vehicle model.
- **Shared singletons** injected once (not per camera): `PlateDetector`, `PlateReader`
  (`ocr.get_reader()` from Sprint F), `classify_type`'s word model. Pass them in or import the
  module-level singletons.
- Per-camera `frame_idx` counter; `r.orig_img` is the frame for crops + `_latest_frame[ch]`.

`box.id` is per-stream-unique (Ultralytics persist), so `track_id` semantics hold per camera.

---

## Task 3 — Multi-camera serving (`app.py`)

One FastAPI app for all cameras. Channel-parameterize the per-camera endpoints:
- `GET /stream.mjpg?channel=4`, `WS /ws?channel=4`, `GET /metrics?channel=4`,
  `GET /snapshot?channel=4`, `GET/POST /rois?channel=4`, all `/review/*?channel=4`,
  `GET /health` (aggregate: all channels).
- Shared state becomes **per-channel dicts**: `_latest_frame[ch]`, `_latest_jpeg[ch]`,
  `_stream_clients[ch]`, `_ws_queues[ch]`, `_perf[ch]`, `_livelog[ch]`, `_roi_config[ch]`.
- **Threads:** one `MultiStreamDriver` thread (replaces the N analytics loops); **one encoder
  thread per camera** (unchanged, keyed by channel, idles when that channel has no viewer);
  **one review-agent worker per camera** (unchanged — per-channel cursor/state/lock).
- `POST /start` builds the driver for `cfg.CHANNELS` and starts everything; `POST /stop` joins all.
- `AUTO_START=1` (from Sprint G) starts on boot.

---

## Task 4 — Config (`config.py`)

```python
CHANNELS = [int(c) for c in os.environ.get("CHANNELS", str(LF_CHANNEL)).split()]  # e.g. "4 5"
VEHICLE_ENGINE_PATH = os.environ.get("VEHICLE_ENGINE_PATH",
                                     str(MODELS_DIR / "vehicle_yolo26.engine"))
BATCH_MAX = int(os.environ.get("BATCH_MAX", "4"))   # engine dynamic batch upper bound
```
Keep `TRACK_CONF`, `TRACKER_CONFIG` (per-stream ByteTrack uses the same yaml). `LF_CHANNEL` stays
as the single-camera fallback.

---

## Task 5 — Dockerization impact (supersedes Sprint G compose)
- **One container**, not per-camera. `environment: CHANNELS=4 5`, one port (serves all channels;
  channel is a query param). Volumes become per-channel **subdirs** under the same mounts
  (`rois/`, `logs/`, `crops/`, `review_agent/{state,out,dataset}/` already key files by `ch{N}`).
- **Entrypoint builds the `.engine`** from `vehicle_yolo26.pt` if missing (GPU-specific), into a
  models volume — do NOT bake the engine. The `.pt` is baked (Sprint G).
- One GPU, shared by the single process (no MPS needed — it's one process now).
- Update `requirements.txt`? No new deps (TensorRT comes via the `tensorrt` extra of ultralytics
  /the CUDA image's TRT; **verify `tensorrt` is importable** in the venv — if not, add it to
  requirements and the image).

---

## Verification / acceptance
- [ ] **0a:** `.engine` exports; `YOLO(engine).predict(frame)` matches `.pt` detections (sanity).
- [ ] **0b:** spike decides Path A or B (documented).
- [ ] One process runs **2 then 4 cameras**; `/metrics?channel=N` shows each at **≥15 fps**;
      `nvidia-smi` shows **one** model resident (not N×) and healthy GPU util.
- [ ] Per-camera correctness unchanged vs single-camera baseline: tracking, **line counting**,
      plate cascade, ROI events, review agent — all per channel.
- [ ] `/stream.mjpg?channel=4` and `?channel=5` show the right annotated feeds; killing viewers
      idles that channel's encoder only.
- [ ] TensorRT FP16 detections ≈ `.pt` (no accuracy regression on a sample clip).
- [ ] Docker: single container, `CHANNELS=4 5`, builds engine at startup, both cameras live.

## Risks / notes
- **VMS session vs Ultralytics-owned decode (Path A):** Ultralytics reconnects dropped streams,
  but the Dahua **session** can expire → add a watchdog that re-logs-in and rebuilds `sources`.
  If this proves fragile, Path B (our grabbers + batched `predict` + manual per-cam tracker)
  keeps full control of the VMS lifecycle.
- **TensorRT engine portability:** never bake/ship the `.engine`; build on the target GPU.
- **Isolation trade-off (accepted):** one process = one crash domain for all cameras. For 2–4
  cams that's fine; beyond the GPU's stream capacity, the scale home is **Triton/DeepStream**
  (per the Ultralytics guidance), not more processes.
- **Plate/OCR/تونس batching: IN SCOPE** (decision updated). Not just shared singletons — the full
  cascade is **batched per tick** across cameras: collect eligible vehicle crops from all cameras →
  one `plate_model.predict(crops)`; collect plate crops → one `ocr.predict(pcs, batch_size=K)` +
  one `tounes_model.predict([stretch512(pc)])`. Prototyped and validated standalone in
  **`spike_batched_cam.py`** (run via `run_spike.sh`); F2 integration hardens that staged-batch
  cascade into `CameraPipeline` (Task 2). The earlier "plate/OCR out of scope" note is superseded.
