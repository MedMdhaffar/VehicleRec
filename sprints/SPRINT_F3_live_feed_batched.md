# Sprint F3 — Rework live_feed to the batched multi-camera architecture

Replace `live_feed`'s **one-process-per-camera** design with **one process running all cameras
off a single shared TensorRT engine**, batched. This promotes the **validated prototype**
(`spike_batched_cam.py` — proven: TensorRT engine, 17 fps+ on 2 cams, VRAM flat via model
sharing, batched cascade) into production. Supersedes the per-camera compose in Sprint G.

> Read `HOW_IT_WORKS.md` (current single-cam design) and `spike_batched_cam.py` (the working
> blueprint) first. The spike's `Models` / `run_loop` / `Cascade` are the reference implementation
> — this sprint hardens them into `live_feed` with the full per-camera state + serving.

---

## 0. Architecture: before → after

```
BEFORE (N processes, one per camera):
  each process: FrameGrabber → EnrichedTracker.process → model.track(frame)   ← N× model in VRAM

AFTER (ONE process, all cameras):
  MultiStreamDriver (live_feed/multistream.py) — owns ONE shared engine + shared plate/OCR/تونس
    every tick:
      1. grab latest frame per camera   (FrameSource per cam — Dahua FrameGrabber today)
      2. BATCHED vehicle detect          vehicle.predict([frames])              (1 GPU call)
      3. per-camera BYTETracker.update   → tracked boxes per camera
      4. per-camera CameraPipeline.ingest(frame, tracks) → update profiles, collect eligible crops
      5. BATCHED plate detect            plate.predict([crops across all cams])
      6. BATCHED OCR + BATCHED تونس       ocr.predict([plate crops]) / tounes.predict([stretched])
      7. per-camera CameraPipeline.apply(results) → finalize/emit/dedup/ROI
      8. publish per-cam raw frame + profiles  → per-cam encoder threads draw+encode on demand
  Shared: vehicle engine, plate YOLO, تونس YOLO, PaddleOCR (loaded ONCE).
  Per camera: BYTETracker, CameraPipeline (profiles/ROI/anchors/review), PerfMeter, LiveLogger,
              encoder thread, review-agent worker.
```

Key wins (validated in the spike): models load once (VRAM flat across cameras), one batched GPU
call per stage, TensorRT FP16. Per-camera tracking/ROI/plate-state logic is **unchanged** — it
already operates on boxes.

---

## 1. Functions / state to KILL

| File:loc | Kill | Why |
|---|---|---|
| `pipeline.py:104` `self._model.track(...)` (inside `EnrichedTracker.process`) | the coupled per-camera detect+track | replaced by shared **batched `predict`** + per-camera `BYTETracker` in the driver |
| `pipeline.py:69` `_ensure_loaded` (the **vehicle** `YOLO(VEHICLE_MODEL_PATH)` load) | per-tracker vehicle-model load | vehicle model now loaded **once** in the driver (as a `.engine`) |
| `app.py:111` `_pipeline_loop(...)` | the single-camera analytics loop | replaced by the driver's one multi-stream loop for all cameras |
| `app.py:50-72` single-camera globals: `_latest_frame, _latest_jpeg, _client, _grabber, _tracker, _perf, _livelog, _roi_config, _stream_clients, _thread, _encoder_thread` | scalar state | becomes **per-camera dicts** keyed by `cam_id` (held by the driver) |
| `app.py` `start(stream_type)` single-channel logic (`LF_CHANNEL`) | one-camera startup | `/start` now spins the driver for **all** cameras in the registry |

Do NOT kill: the per-camera *logic* (classify-once, motion/stationary, plate state machine,
dedup anchors, ROI analytics, finalize/emit, best-crop, review_writer) — it moves into
`CameraPipeline` intact.

---

## 2. Functions to UPDATE / CREATE

### 2a. NEW `live_feed/multistream.py` — the driver (promote the spike)
Promote `spike_batched_cam.py`'s `Models`, `run_loop`, `Cascade` into production:
- `Models`: load vehicle `.engine` (`YOLO(cfg.VEHICLE_ENGINE_PATH, task="detect")`; build from `.pt`
  at startup if missing — see §5), plate/تونس YOLO (`task="detect"`), PaddleOCR (transformers→pipeline
  fallback, as in `plate_recognition/ocr.py`). **Shared singletons.**
- `MultiStreamDriver(cameras)`: builds a `FrameSource` + `BYTETracker` (`SimpleNamespace(**yaml)` from
  `cfg.TRACKER_CONFIG`) + `CameraPipeline` + `PerfMeter` + `LiveLogger` per camera; runs the tick loop
  (steps 1–8 above). Publishes per-cam `latest_frame` + `profiles` snapshot to shared per-cam state.
- The **batched cascade** (steps 5–6) = the spike's `Cascade`, but gated by `CameraPipeline` state
  (resolved / OCR cooldown / read_zone) instead of the spike's simplified gating.

### 2b. UPDATE `pipeline.py` — `EnrichedTracker` → `CameraPipeline`
- Rename `EnrichedTracker` → `CameraPipeline`; constructor takes `cam_id`, `perf`, shared models refs
  (plate/ocr/تونس) — NOT the vehicle model.
- Split `process(frame, frame_idx)` (95) into:
  - `ingest(frame, tracked_boxes, frame_idx)` → everything from the per-box loop **up to** the plate
    cascade: profile create/update, best-crop, dedup-anchor refresh, motion, classify-once; **return
    the list of eligible `(track_id, crop)`** for batched plate detect (the driver collects these
    across cameras).
  - `apply_plate_results(results_for_my_crops, frame_idx)` → the OCR-lock / keep-best / early-emit
    logic (today's 235-252), then ROI `process_frame`, finalize+dedup (259-300), `frame_done`.
- Keep `set_roi`, `active_profiles`, `_anchors`, `_review_writer`, the whole plate **state machine**
  (parked / eff_max_ocr / resolved / read_zone gate / cooldown) — it just decides eligibility in
  `ingest` now instead of calling the models itself.

### 2c. UPDATE `app.py` — multi-camera serving
- Replace the single-cam globals with the driver + per-cam state. `/start` (239) builds
  `MultiStreamDriver(cfg.CAMERAS)` and starts: the driver thread + one **encoder thread per camera**
  + one **review-agent worker per camera**. `/stop` (293) stops all.
- **Channel-parameterize** every per-camera endpoint with `?cam=<id>` (default = first camera):
  `/stream.mjpg`, `/ws`, `/metrics`, `/snapshot`, `/rois` (GET/POST), `/reload-rois`, all `/review/*`.
  Read from the per-cam dicts. `/health` returns an aggregate over all cameras.
- `_encoder_loop` (183): keep the **Sprint E** pattern but **per camera** — one encoder thread per
  `cam_id`, reading that camera's `latest_frame` + `active_profiles`, encoding only while that
  camera's `/stream.mjpg` has a client. (Encoding stays OFF the shared analytics loop.)
- `_broadcast_profile` / `_broadcast_zone_event` (89/101): tag payloads with `cam` and fan out to that
  camera's WS queue set (per-cam `_ws_queues[cam]`).

### 2d. UPDATE `config.py` — camera registry + engine
```python
# camera registry — F3 ships Dahua channels; F4 adds an rtsp entry (same shape)
CAMERAS = [{"id": str(c), "type": "dahua", "channel": int(c)}
           for c in os.environ.get("CHANNELS", str(LF_CHANNEL)).replace(",", " ").split()]
VEHICLE_ENGINE_PATH = os.environ.get("VEHICLE_ENGINE_PATH", str(MODELS_DIR / "vehicle_yolo26.engine"))
BATCH_MAX = int(os.environ.get("BATCH_MAX", "8"))   # engine dynamic-batch upper bound
```
`cam_id` is a **string** (so F4's RTSP cam fits). All per-cam files key by it: `rois/ch{cam_id}.json`,
`logs/ch{cam_id}_*.jsonl`, `review_agent/{state,crops,…}/…ch{cam_id}…`. Generalize the `int channel`
in `livelog.py`, `roi.RoiConfig.load/save`, `review_agent/{dataset,cursor,worker}` to accept the
string `cam_id` (it's only used in filenames + log tags — drop-in).

### 2e. UPDATE `review_agent` + `livelog` + `perf` + `roi`
- One instance **per camera**, keyed by `cam_id` (the driver owns the dicts). `worker.start_thread`,
  `ReviewInputWriter`, `cursor.*`, `LiveLogger`, `PerfMeter`, `RoiConfig`/`RoiAnalytics` → take `cam_id`.
- The **FrameSource abstraction** is the seam F4 plugs into: `live_feed/stream.py FrameGrabber`
  (Dahua) already exposes `start()/get_latest()/stop()/frames_total/frames_dropped`. Treat that as the
  `FrameSource` interface; the driver builds sources via a small factory keyed on `camera["type"]`
  (`"dahua"` → `FrameGrabber(DahuaVMSClient(...), channel)`). **F4 adds `"rtsp"`.**

---

## 3. Build order
1. `multistream.py` — port `Models` (engine + task="detect"), the source factory (dahua only),
   per-cam `BYTETracker`, the tick loop + batched cascade. Reuse spike code.
2. `pipeline.py` — split `EnrichedTracker` → `CameraPipeline.ingest` / `apply_plate_results`.
   Compile-check; unit-sanity one camera's profiles vs the spike.
3. `config.py` — `CAMERAS`, `VEHICLE_ENGINE_PATH`, `BATCH_MAX`; generalize `cam_id` (string) through
   `livelog`/`roi`/`review_agent`.
4. `app.py` — driver wiring in `/start`/`/stop`, per-cam encoder + review threads, `?cam=` on all
   per-camera endpoints, aggregate `/health`.
5. Engine-at-startup (build `.engine` from `.pt` if missing).
6. Frontend: dev UI passes `?cam=` (the team's real UI does its own thing) — minimal change.

---

## 4. Verification / acceptance
- [ ] One process, `CHANNELS="4 1"`: `/metrics?cam=4` and `?cam=1` both **≥15 fps**; `nvidia-smi`
      shows **one** model set (~2.4 GB), not N×.
- [ ] **Parity with single-cam baseline**: for one camera, profiles/plates/line-counts/ROI events
      match the old `live_feed` behavior (run the old and new on the same camera, compare events).
- [ ] `/stream.mjpg?cam=4` and `?cam=1` show correct annotated feeds; closing a viewer idles only
      that camera's encoder.
- [ ] Review agent runs per camera (per-`cam_id` state/cursor/dataset); plates still reviewed.
- [ ] Kill one camera's source (unplug/stop) → others keep running (driver tolerates a dead source).
- [ ] TensorRT engine in use (`veh` ms low); detections ≈ `.pt` on a sample.

## 5. Risks / notes
- **Engine not portable** — build `vehicle_yolo26.engine` from `.pt` **at startup** (entrypoint), not
  baked (GPU/TRT-specific). Keep `.pt` as source of truth. (TensorRT must be **10.x** — TRT 11 breaks
  ultralytics 8.4.46's exporter; see `requirements.txt`.)
- **One process = one crash domain** for all cameras (accepted for the camera counts here). Wrap the
  driver tick in try/except per camera so one bad frame/source doesn't kill the loop.
- **A dead/slow source** must not stall the batch — skip cameras with no fresh frame this tick
  (the spike's `present` pattern), batch only the ready ones.
- **Encoding stays off the analytics loop** (Sprint E) — per-cam encoder threads, on-demand.
- Beyond the GPU's stream capacity → Triton/DeepStream (per Ultralytics guidance), not more processes.
