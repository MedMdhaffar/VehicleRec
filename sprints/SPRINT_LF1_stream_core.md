# Sprint LF-1 — Live-feed stream core + perf (headless)

**Phase:** Live Feed (real-time perception + ANPR linking) — precursor to Phase C traffic_analytics.
**Depends on:** vehicle_classification (Phase A ✅), plate_recognition (Sprint 5 ✅).
**Goal:** Stand up the **headless real-time pipeline** on one live VMS camera: connect → grab-latest @15 FPS → detect+track → **classify type+colour ONCE per track (locked)** → per-track **plate cascade (detect → gated OCR → type)** → **enriched VehicleProfile** table → **perf instrumentation**. This is the build whose latency we validate before adding any UI or analytics rules.

> This `pipeline.py` enriched-tracker is **not throwaway** — it becomes the traffic_analytics stream core; Phase C adds geometry/rules on top.

---

## 1. Module layout (new)

```
vehicle_services/live_feed/
  __init__.py
  vms.py        # COPY of DahuaVMSClient (standalone — no live_inference import)
  stream.py     # FrameGrabber: reader thread, keeps only the latest frame, paces to 15 FPS
  pipeline.py   # EnrichedTracker: detect+track → classify-once → plate cascade → profiles
  perf.py       # PerfMeter: per-stage ms, rolling FPS, e2e latency, OCR rate
  config.py     # FPS, thresholds, model paths, VMS env, stream_type
  schemas.py    # VehicleProfile, PlateInfo (pydantic/dataclass)
  run.py        # CLI entry: connect to a channel, run pipeline, log perf (+ optional save frames)
```

---

## 2. Reuse (in-process — Level-1 composition, no HTTP between stages)

| Need | Reuse |
|------|-------|
| Vehicle detector + **tracking** | the vehicle YOLO at `common/models/` (`VEHICLE_MODEL_PATH`) via `model.track(frame, persist=True, tracker="bytetrack.yaml")` |
| class-id → type taxonomy | `common/models/class_map.json` / the mapping used in `common/detector.py` |
| Colour | `common.colour.estimate_colour(crop)` |
| Plate detector | `plate_recognition.plate_detector.PlateDetector` (best_matricule.pt) |
| Plate digits OCR | `plate_recognition.ocr.PlateReader` (PaddleOCR PP-OCRv6, transformers engine) |
| Plate type | `plate_recognition.plate_type.classify_type(crop)` (stretch-512 + word detector) |

All same-process imports — per the Level-1 in-process composition decision (avoids per-frame HTTP).

---

## 3. VMS connection (standalone copy)

- **Copy** `DahuaVMSClient` from `live_inference/vms.py` into `live_feed/vms.py` **verbatim** (no import from live_inference).
- Read the **same env vars** live_inference uses (set them exactly as you already do — don't start live_inference): `VMS_URL`, `VMS_DEVICE_IP`, `VMS_DEVICE_PORT` (37777), `VMS_USERNAME`, `VMS_PASSWORD`, `VMS_CHANNEL_OFFSET` (-1).
- **Main stream:** `start_stream(sdk_ch, stream_type=0)`. `sdk_ch = user_channel + VMS_CHANNEL_OFFSET`.
- Sequence: `login()` → `start_stream(sdk_ch, 0)` → `iter_frames(stream_id)` → frames; **always `stop_stream` + `logout` in a `finally`**.

---

## 4. `stream.py` — FrameGrabber (real-time discipline)

- Reader **thread** consumes `iter_frames()` continuously and stores **only the latest frame** (drop stale frames — never queue a backlog; that's how we measure true latency, not drift).
- `get_latest()` returns the newest frame + its capture timestamp.
- Main loop pulls at **15 FPS** cadence (`FPS=15`); if the camera pushes 25/30, intermediate frames are simply dropped by the grabber.
- Handle drops/reconnect: on `iter_frames` exception, stop+login+restart with backoff.

---

## 5. `pipeline.py` — EnrichedTracker (the core)

**Per frame:**
1. `dets = model.track(frame, persist=True, conf=VEHICLE_CONF, tracker="bytetrack.yaml")` → list of `{track_id, bbox, cls, conf}`.
2. For each tracked vehicle, update its `VehicleProfile` (keyed by `track_id`):

**Classify-once (type + colour):**
- While `frames_seen <= K_CLASSIFY` (=5) **and not locked**: accumulate the per-frame detection **type** (vote) and sample **colour** (`estimate_colour` on the crop).
- At frame `K_CLASSIFY` (or on lock condition): **lock** `type = majority vote`, `colour = dominant sample`. After lock, **do not recompute** — reuse across all later frames (your requirement). Saves the per-frame colour cost and stabilizes the label.

**Plate cascade (gated, the only expensive op):**
- If `not profile.plate.locked`:
  - crop the vehicle box → `PlateDetector.predict(crop)` → plate box (cheap, every frame OK).
  - If a plate box exists **and** `plate_width >= MIN_PLATE_W (60)` **and** `frames_since_last_ocr >= OCR_COOLDOWN (6)` **and** `ocr_attempts < MAX_OCR (4)`:
    - `number, digits_conf = PlateReader.read(plate_crop)`
    - `ptype, type_conf = classify_type(plate_crop)`  *(stretch-512 inside)*
    - update best read; `ocr_attempts += 1`; `last_ocr_frame = now`.
    - **Lock** when `digits_conf >= OCR_LOCK_CONF (0.9)` → stop OCR-ing this track.

**Emit:**
- **early** — when the plate locks (push `profile`).
- **finalize** — when a track hasn't been seen for `LOST_FRAMES` (e.g. 15) → push final `profile`, remove from active table.
- Keep an **in-memory active-profile table** (for LF-2's live view).

---

## 6. `schemas.py`

```python
PlateInfo: number:str|None, digits_conf:float, type:str|None (tounes/other),
           type_conf:float, locked:bool, ocr_attempts:int
VehicleProfile: track_id:int, bbox:[int,int,int,int], type:str, type_conf:float,
                colour:str, colour_conf:float, plate:PlateInfo,
                first_seen:float, last_seen:float, frames_seen:int, status:"active"|"final"
```

---

## 7. `config.py`

```python
FPS            = 15
STREAM_TYPE    = 0      # main stream (1080)
K_CLASSIFY     = 5      # frames to vote type+colour before locking
VEHICLE_CONF   = 0.35
MIN_PLATE_W    = 60     # px — below this OCR fails
OCR_COOLDOWN   = 6      # frames between OCR attempts per track
MAX_OCR        = 4      # hard cap OCR attempts per track
OCR_LOCK_CONF  = 0.90   # stop OCR once digits_conf >= this
WORD_CONF      = 0.50   # plate-type threshold (validated)
LOST_FRAMES    = 15     # frames absent → finalize track
# model paths: VEHICLE_MODEL_PATH (env) | plate_recognition/models/{best_matricule,tounes_word_detector}.pt
# VMS_* env vars (see §3)
```

---

## 8. `perf.py` — instrumentation (the point of LF-1)

Wrap each stage and report (rolling ~2 s window): **per-stage avg + p95 ms** for `track`, `colour`, `plate_detect`, `ocr`; **processed FPS**; **end-to-end latency** (frame-capture ts → profile-update ts); **OCR calls/sec**; **active track count**; **frames dropped** (grabber). Log a one-line summary every ~2 s and a final summary on exit.

---

## 9. `run.py` — CLI entry

```
VMS_URL=… VMS_DEVICE_IP=… VMS_PASSWORD=… \
python -m live_feed.run --channel 4 [--save-frames] [--seconds 120]
```
Connects to the channel, runs the pipeline, prints the perf summary live, optionally saves annotated frames to `live_feed/out/` for a visual gut-check (full UI is LF-3).

---

## 10. NVIDIA MPS / multi-camera

- **One process per camera** (your decision). A single camera = one `run.py` process; no MPS needed.
- **Multiple cameras** = multiple processes sharing the GPU → enable the **CUDA MPS daemon** once: `nvidia-cuda-mps-control -d` (document start/stop). Each camera process then shares the GPU efficiently. Build/validate **single-camera first**; multi is "run N processes."

---

## 11. Acceptance criteria

- `python -m live_feed.run --channel <ch>` connects to the live VMS main stream and runs without backlog (grab-latest working).
- Sustains **~15 FPS** processing; perf log shows per-stage ms, **end-to-end latency**, OCR calls/sec, active tracks.
- Produces **VehicleProfiles** with locked type+colour, and plate `{number, type}` when a readable plate appears (gracefully empty when not).
- **Classify-once** verified: type/colour computed only during the first ~5 frames of a track, then constant.
- OCR rate confirms gating (~≤ a few calls/vehicle, not per-frame).
- **Pass = holds 15 FPS** with latency we judge acceptable → proceed to LF-2.

---

## 12. Out of scope (LF-1)

- HTTP serving / MJPEG / WebSocket (LF-2). Frontend (LF-3). Geometry/rules — counting/parking/restricted (Phase C). Multi-camera orchestration beyond "run N processes."
