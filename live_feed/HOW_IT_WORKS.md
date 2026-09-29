# live_feed — How it works (current, detailed)

Authoritative, code-accurate reference for the **live_feed** real-time service, derived
from a function-by-function read of every runtime file. One OS process = one camera.

## Contents
1. What it is
2. Process & thread model (4 threads)
3. End-to-end data flow
4. The models (what loads, when, from where)
5. Per-frame pipeline — `EnrichedTracker.process`, step by step
6. The tracker (ByteTrack config + why)
7. Position-anchored dedup
8. ROI analytics — occupancy + line counting
9. Data schema & emission
10. Serving layer (FastAPI endpoints)
11. Review agent (offline VLM enrichment)
12. Logging & performance metrics
13. Config knobs (current values)
14. Runtime state (files & volumes)
15. External dependencies
16. Known dead code / caveats
17. Local HSV colour extraction

---

## 1. What it is
A per-camera pipeline that ingests a Dahua VMS MJPEG stream and produces an enriched
record per vehicle — **type + colour + plate number + plate category** — plus **ROI traffic
analytics** (parking dwell, restricted-zone violations, overcrowding, line-cross counts).
A second, **offline** review agent (Gemini via OpenRouter) post-corrects records and builds
a retraining dataset (incl. vehicle **maker**). The web frontend is a separate dev/debug
app; the backend is headless (API only).

---

## 2. Process & thread model

One camera = one Python process (`LF_CHANNEL`, port `LF_PORT_BASE + channel` → ch4 = 5034).
Inside it, **four threads**:

| Thread | Code | Job |
|---|---|---|
| **Grabber** | `stream.py FrameGrabber._reader_loop` | Pull VMS MJPEG, decode→BGR, keep **only the latest** frame (single slot; overwrites count as `_frames_dropped`). Reconnect with exponential backoff. |
| **Analytics** | `app.py _pipeline_loop` | grab latest → `tracker.process` → WS broadcast → publish `_latest_frame`. **No JPEG encode.** Source-paced (no FPS cap). |
| **Encoder** | `app.py _encoder_loop` | Only while a stream client is connected: copy `_latest_frame`, `draw_overlay` (under `_profiles_lock`), JPEG-encode → `_latest_jpeg`, at `DISPLAY_FPS`. |
| **Review agent** | `review_agent/worker.loop` | Offline single-flight loop: review finalized vehicles with the VLM every `IDLE_SECONDS`. |

The analytics and encoder threads are **decoupled** (Sprint E): the heavy JPEG encode no
longer sits on the analytics loop, so tracking runs at full source rate. The grabber's
`get_latest()` **consumes** the frame (single-slot), so only the analytics thread pulls
frames; the encoder reads the published `_latest_frame` (read-only after publish).

Shared state (in `app.py`): `_latest_frame` (raw ndarray, `_frame_lock`), `_latest_jpeg`
(annotated bytes, `_jpeg_lock`), `_tracker.active_profiles` + ROI state (`_profiles_lock`),
`_ws_queues`, `_stream_clients` (`_stream_clients_lock`), `_perf` (`_perf_lock`).

---

## 3. End-to-end data flow

```
Dahua VMS (Windows, :5057, MJPEG/HTTP)
   │  vms.iter_frames(): split JPEG (FFD8..FFD9) → cv2.imdecode → BGR
   ▼
FrameGrabber  ── single-slot latest frame ──►  Analytics thread (_pipeline_loop)
                                                 │ EnrichedTracker.process(frame, idx)
                                                 │   → emitted profiles + zone_events
                                                 ├─ broadcast over WS (/ws)
                                                 ├─ livelog.log_event / log_perf (JSONL)
                                                 ├─ review_writer.write(profile) on finalize
                                                 └─ publish _latest_frame
                                Encoder thread ──┘ draw_overlay + encode → _latest_jpeg → /stream.mjpg
Review agent thread (offline)  ── reads ReviewInput + crops ──► Gemini ──► dataset/ + corrected_events
```

---

## 4. The models

Loaded lazily in `EnrichedTracker._ensure_loaded()` on the **first frame** (so `/start`
returns fast):

| Role | What | Loaded from | Notes |
|---|---|---|---|
| Vehicle detect + **track** | `ultralytics.YOLO` + ByteTrack | `common/models/vehicle_yolo26.pt` (`VEHICLE_MODEL_PATH`) | one `.track()` call per frame |
| Type | the detector's class → `common.config.TAXONOMY` | — | no separate model |
| Colour | `common/colour.py estimate_colour` (HSV) | — | **no model**; classical |
| Plate detect | `plate_recognition.PlateDetector` (`YOLO`) | `plate_recognition/models/best_matricule.pt` | lazy-loads on first `predict`, runs on the **vehicle crop** |
| Plate OCR | `plate_recognition.PlateReader` (PaddleOCR `PP-OCRv6_medium_rec`, `engine="transformers"`) | downloaded/cached by PaddleOCR | falls back to Paddle pipeline (oneDNN off) if transformers engine unavailable |
| Plate category (تونس) | `plate_recognition.plate_type.classify_type` (`YOLO`) | `plate_recognition/models/tounes_word_detector.pt` | **stretch crop→512²** then word-spot; stretch is critical (recall 43%→96%) |

**Colour algorithm** (`estimate_colour`): select five bounded, overlapping body-paint
ROIs while avoiding crop borders, roof and the wheel/road strip; retain a center fallback.
Optional illumination normalization precedes HSV (measured default: `none`). ROI HSV
statistics drive calibrated neutral handling, while the original chromatic hue bins are
preserved. ROI evidence is combined into one frame observation; no learned colour model
is loaded.

---

## 5. Per-frame pipeline — `EnrichedTracker.process(frame, frame_idx)`

Returns `(emitted_profiles, zone_events)`. Steps:

**1. Track** (`perf.stage("track")`):
`model.track(frame, persist=True, conf=TRACK_CONF=0.10, tracker=TRACKER_CONFIG, verbose=False)`.
Low feed conf so ByteTrack's 2-stage association sees marginal boxes (§6).

**2. For each tracked box** (skip if `box.id is None`):
- **Profile**: create/update `VehicleProfile` keyed by `track_id`; bump `frames_seen`, refresh `bbox`/`last_seen`/`_last_seen_frame`.
- **Best-crop**: keep the largest-area crop seen (`_best_crop`) — for the review agent. Cheap (area only), off the model path.
- **Dedup anchor refresh** (§7): any written-record anchor this box overlaps (`iou ≥ DEDUP_IOU`) gets its `last_frame` refreshed → survives track-ID churn.
- **Motion**: centroid displacement `< MOVE_THRESH_PX` → increment `_stationary_frames`; else reset, and if it **was parked** (`≥ STATIONARY_N`) and unread → **re-arm** plate attempts (`ocr_attempts = 0`).
- **Type classify-once**: preserve the two-frame `K_CLASSIFY` vote over detector classes, then lock type and refresh only `type_conf`.
- **Temporal colour**: on every non-empty vehicle crop, run HSV until 5–20 valid,
  supported, non-`unknown` observations are collected. Each vote is weighted by
  `HSV confidence × bounded crop quality`; quality covers size, sharpness,
  pathological exposure and image-border clipping. Stable evidence can lock from
  sample 5; sample 20 applies the final threshold and may finalize as `unknown`.
  Lost short tracks use separate conservative finalization rules.
- **Plate cascade (state machine + cadence)** — step 3.

**3. Plate cascade** (per tracked vehicle):
```
parked       = _stationary_frames ≥ STATIONARY_N (10)
eff_max_ocr  = STATIONARY_MAX_OCR (1) if parked else MAX_OCR (4)
resolved     = plate.locked OR ocr_attempts ≥ eff_max_ocr
in_read_zone = no read_zone polygons defined  OR  foot_point(bbox) in any read_zone
ocr_eligible = in_read_zone AND not resolved AND (frame_idx - _last_ocr_frame) ≥ OCR_COOLDOWN (6)

if ocr_eligible:
    _last_ocr_frame = frame_idx                      # cadence gate
    plate_boxes = plate_detector.predict(crop)       # perf.stage("plate_detect")
    if a plate box width ≥ MIN_PLATE_W (60):
        keep best plate crop (widest) → _best_plate_crop
        text,dconf = plate_reader.read(plate_crop)    # perf.stage("ocr")
        ptype,tconf = classify_type(plate_crop)
        ocr_attempts += 1
        if dconf > plate.digits_conf: store number/type/confs  (keep best read)
        if dconf ≥ OCR_LOCK_CONF (0.90): plate.locked = True → emit "active" (once)
    elif parked: spend the attempt anyway            # parked no-plate car resolves & goes quiet
```
Intent: **plate-detect + OCR run only on NEW/MOVING, UNREAD vehicles inside the read zone,
at most every 6th frame; parked cars get one try then go silent** (re-arm if they move).

**4. ROI analytics** — `self._roi.process_frame(active, frame_idx, now)` (§8) → `zone_events`.

**5. Finalize** lost tracks (not seen for `> LOST_FRAMES` = 15 frames):
- mark `status = "final"`;
- **dedup** (§7): if it IoU-matches a live anchor of the **same type** → it's a re-ID of an
  already-written car → **don't write**;
- else `review_writer.write(profile)` → if it produced a `record_id`, create a new anchor;
- append to `emitted` either way (WS still gets the `final` event);
- prune anchors idle `> ANCHOR_EXPIRE_FRAMES` (30).

`perf.set_active_tracks` + `perf.frame_done()` close the frame.

---

## 6. The tracker (`trackers/bytetrack_vehicles.yaml`)

```
tracker_type: bytetrack
track_high_thresh: 0.30   # first-stage association
track_low_thresh:  0.10   # second-stage (BYTE) — recovers weak boxes; must be ≥ feed conf
new_track_thresh:  0.25   # confidence needed to START a track
track_buffer:      75     # keep a lost vehicle ~3.7s @20fps → re-associate, not re-ID
match_thresh:      0.80
fuse_score:        True
```
Paired with `TRACK_CONF = 0.10` in `model.track(conf=…)` so low-confidence detections reach
the 2-stage association. This holds a vehicle's `track_id` stable across the counting line
(fewer re-IDs → less biased counts). Detector is **yolo26m** (RT-DETR-L was evaluated and
rejected — worse recall on the val set).

---

## 7. Position-anchored dedup (`_anchors` in `EnrichedTracker`)

A parked car gets re-IDed by ByteTrack repeatedly → without dedup it would write one review
record per re-ID. Fix: each **written** record drops an anchor `{bbox, type, last_frame}`.
- **Every frame**, any active box overlapping an anchor (`iou ≥ DEDUP_IOU` = 0.6) refreshes
  its `last_frame` → the anchor stays alive as long as a car sits there, **regardless of
  track-ID churn**.
- On finalize, a track that matches a live anchor (same spot, same type, within
  `ANCHOR_EXPIRE_FRAMES` = 30) is treated as a re-ID → **skipped** (first-finalize-wins).
- Anchors expire ~2s after the spot is vacated → a *different* car later gets its own record.
- Anchors are created **only** for tracks that actually wrote a record (so a short, unwritten
  track can't block a later good read of the same car).

---

## 8. ROI analytics (`roi.py RoiAnalytics`) — pure geometry, no models

Config per camera: `rois/ch{N}.json` → `RoiConfig` of `PolygonROI` (functions ⊆
{`read_zone`, `parking`, `restricted`, `occupancy`}) and `LineROI` (counting lines).
Geometry helpers: `foot_point` (bottom-center), `point_in_polygon` (cv2), `iou`, `line_side`
(signed cross product), `segment_crosses` (sign-flip + AABB guard).

**`process_frame`** per frame:
- **Occupancy (parking/restricted)** — `_update_occupancy`: candidates = vehicles whose
  foot-point is inside the polygon; greedy **IoU-match** (`IOU_MATCH` = 0.5) to position-anchored
  `OccupancySlot`s (immune to track-ID churn); confirm a slot after `MIN_OCCUPY_FRAMES` (15);
  drop after absent `FREE_FRAMES` (15). Fires `violation` (restricted, dwell > `RESTRICTED_MIN_SEC`
  = 5s) or `overstay` (parking, dwell > `OVERSTAY_SEC` = 300s), once each. **Vehicles present at
  startup (`frame_idx == 0`) are flagged `present_at_startup` and never fire** (no false alerts
  on boot). Emits `parking_ended` with duration when a confirmed slot frees.
- **Occupancy count** — `_check_occupancy_count`: count foot-points in the polygon; edge-trigger
  `overcrowding` when count crosses `OCCUPANCY_MAX` (10).
- **Line counting** — `_check_lines`: per active track, if `segment_crosses(prev_foot, cur_foot,
  a, b)` and not already counted for this line → increment `counts[direction][vehicle_type]`,
  direction from `line_side` sign, emit `crossing`. Per-track-per-line de-dup via `_crossed`.
  `_prev_foot` updated for all tracks each frame (after the line check). Counts survive ID churn
  only if the track keeps its ID across the line — hence the tracker tuning in §6.

Zone events are returned to the analytics thread → broadcast over WS as `zone_event`.

---

## 9. Data schema & emission (`schemas.py`)

```
VehicleProfile: track_id, bbox, type, type_conf, colour, colour_conf,
                plate{number, digits_conf, type(tounes|other), type_conf, locked, ocr_attempts},
                first_seen, last_seen, frames_seen, status(active|final)
   + runtime-only (repr=False): _type_votes, _type_locked, rich _colour_samples,
     _colour_attempts, _unknown_colour_samples, _colour_finalized and evidence fields,
     _last_ocr_frame, _last_seen_frame, _plate_early_emitted, _last_centroid,
     _stationary_frames, _best_score, _best_crop, _best_plate_crop, _best_plate_w
```
Emitted at most twice over WS: **`active`** the moment the plate locks (early), **`final`**
when the track is lost. `_profile_to_dict` strips the `_`-prefixed fields before sending.

---

## 10. Serving layer (`app.py`, FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /start` | login VMS, build grabber/tracker/livelog, start analytics + encoder + review-agent threads, load ROI |
| `POST /stop` | signal stop, join analytics + encoder threads, logout VMS |
| `GET /health` | running, models_loaded, active_tracks, `frame_ready` (= `_latest_frame` present), ws_clients |
| `GET /metrics` | `perf.report_dict()` |
| `GET /stream.mjpg` | multipart MJPEG of `_latest_jpeg`; increments/decrements `_stream_clients` (so the encoder idles when nobody watches) |
| `WS /ws` | initial profile snapshot, then pushes `profile` / `zone_event` events + `metrics` ~`WS_METRICS_HZ` |
| `GET /snapshot` | raw (un-annotated) `_latest_frame` encoded on demand — calibration canvas |
| `GET/POST /rois`, `POST /reload-rois` | read / validate-save / hot-reload ROI config |
| `GET /review/health`, `/review/records`, `/review/crop/{id}`, `POST /review/verify/{id}` | review-agent dashboard API |

---

## 11. Review agent (offline VLM enrichment) — `review_agent/`

**Ingest (on the live path, finalize only):** `dataset.ReviewInputWriter.write(profile)` — if
`frames_seen ≥ REVIEW_MIN_FRAMES` (5) and a best crop exists: assign a monotonic
`record_id` (`cursor.next_record_id`, per-channel `seq.txt`, atomic), save `crops/ch{N}/{rid}_veh.jpg`
(+ `_plate.jpg` if any), append one `ReviewInput` JSONL line. Returns the `record_id` (used for
the dedup anchor) or `None` if skipped.

**Worker loop (`worker.loop`, its own thread):**
1. `smoke_test()` — one structured-output `HealthCheck` call to Gemini; on failure the agent
   **disables itself and live_feed is unaffected**.
2. PID lock (`state/ch{N}.lock`) — single instance.
3. Repeat until stop: `run_pass(channel)` → `sleep(IDLE_SECONDS = 300)` (single-flight — the
   5-min gap is measured **after** a pass completes, so passes never overlap).

**`run_pass`:** load checkpoint → read `ReviewInput` rows with `record_id > last_record_id`
(rotation-safe, content-based) → (optional `SEND_LOWCONF_ONLY` filter) → for each, in order:
`review_vehicle` + `review_plate` (two Gemini calls) → `reconcile` → `write_outputs` →
advance `max_done`. Cursor saved at the end; a failure **breaks the tail** (retried next pass).
Idempotent by `record_id`.

**LLM calls (`client.py`):** LangChain `ChatOpenAI` → OpenRouter (`OPENROUTER_MODEL` =
`google/gemini-3-flash-preview`), `with_structured_output`, image as base64 data URL.
- `VehicleReview{vehicle_class, colour, vehicle_maker, confidence}` ← vehicle crop + predicted class/colour.
- `PlateReview{ocr, plate_type(tounes|other), observed_category, confidence}` ← plate crop + predicted ocr/type.

**Reconcile (`reconcile.py`):** per-field `agreement` (class/colour/ocr/plate_type, case-insensitive);
`routed_to = "trusted"` iff all compared fields agree **and** confidences ≥ `AUTO_ACCEPT_CONF`
(0.85), else `"review_queue"`.

**Outputs (`dataset.py write_outputs`):** always append `out/ch{N}_corrected_events.jsonl` +
`dataset/reviews.jsonl`. If `review_queue` → `out/ch{N}_review_queue.jsonl` and stop. If `trusted`,
populate the dataset tree: `dataset/makers/<maker>/…` (or `dataset/non_vehicle/<class>/…` for
`person`/`bicycle` — no maker pollution), `dataset/plate_type/<tounes|other>/…`,
`dataset/plate_observed/<category>/…`, and `dataset/ocr/labels.jsonl`. Keyed by `ch{N}_{rid}`
(idempotent overwrite).

**Human verify (`POST /review/verify/{id}` → `write_human_verified`):** appends
`out/ch{N}_human_verified.jsonl` (with original Gemini answer) and copies the crop into
`dataset/human_verified/{makers,plate_type,plate_observed}/…`.

---

## 12. Logging & performance (`livelog.py`, `perf.py`)

- **`ch{N}_events.jsonl`** — one line per emitted profile (incl. raw `_type_votes` /
  `_colour_samples` for accuracy debugging). RotatingFileHandler, 50 MB ×5, `propagate=False`.
- **`ch{N}_perf.jsonl`** — periodic snapshot every `_REPORT_INTERVAL` (= `FPS*2`) frames:
  `fps`, `e2e_ms`, `active_tracks`, `plate_calls_per_frame`, `ocr_rate`, per-stage `avg/p95`
  (`track`/`colour`/`plate_detect`/`ocr`/…), plus **`source_fps`** and **`drop_fps`** computed
  in `_pipeline_loop` from the grabber's `frames_total`/`frames_dropped` deltas.
- `perf.tick(cap_ts)` records `e2e = now - capture_ts` and the frame timestamp (fps = rolling
  30-frame rate). **Note:** `perf._frames_dropped` / `inc_dropped()` is **dead** (never called);
  the real source/drop rates come from the grabber via `source_fps`/`drop_fps`.

---

## 13. Config knobs (`config.py`, current values; all env-overridable)

| Knob | Value | Meaning |
|---|---|---|
| `FPS` | 15 | reference rate (DISPLAY_FPS default, report interval) — **analytics loop is NOT capped** |
| `STREAM_TYPE` | 0 | 0 = main (1080p), 1 = sub |
| `K_CLASSIFY` | 2 | frames to vote vehicle type before locking |
| `MIN_VALID_COLOR_SAMPLES` / `MAX_VALID_COLOR_SAMPLES` | 5 / 20 | valid HSV observations for early/final decisions |
| `COLOR_ROI_MODE` / `COLOR_NEUTRAL_MODE` | multi / adaptive | paint ROIs and calibrated neutral consensus |
| `COLOR_NORMALIZATION` | none | configurable: none, gray_world, gamma |
| `COLOR_QUALITY_MODE` | enhanced | size/sharpness/exposure/clipping temporal weight |
| `COLOR_FEATURE_MODE` | hsv | optional `hsv_gray` or `hsv_lab` auxiliary ROI fusion |
| `EARLY_LOCK_WINNER_SHARE` / `EARLY_LOCK_MARGIN` | 0.68 / 0.20 | stable early-lock thresholds |
| `FINAL_WINNER_SHARE` / `FINAL_WINNER_MARGIN` | 0.55 / 0.10 | sample-budget and lost-track final thresholds |
| `VEHICLE_CONF` | 0.35 | **unused** (tracker now uses `TRACK_CONF`) |
| `TRACK_CONF` | 0.10 | detector feed conf into ByteTrack |
| `TRACKER_CONFIG` | `trackers/bytetrack_vehicles.yaml` | tuned tracker |
| `MIN_PLATE_W` | 60 | below → skip OCR |
| `OCR_COOLDOWN` | 6 | frames between plate looks per track |
| `MAX_OCR` / `STATIONARY_MAX_OCR` | 4 / 1 | plate attempts: moving / parked |
| `STATIONARY_N` / `MOVE_THRESH_PX` | 10 / 8 | parked threshold / per-frame move threshold |
| `OCR_LOCK_CONF` | 0.90 | digit conf to lock & stop reading |
| `WORD_CONF` | 0.50 | تونس word-spot threshold |
| `LOST_FRAMES` | 15 | frames absent → finalize |
| `DEDUP_ENABLED` / `DEDUP_IOU` / `ANCHOR_EXPIRE_FRAMES` | true / 0.6 / 30 | review-record dedup |
| `IOU_MATCH` / `MIN_OCCUPY_FRAMES` / `FREE_FRAMES` | 0.5 / 15 / 15 | occupancy slot matching |
| `OVERSTAY_SEC` / `RESTRICTED_MIN_SEC` / `OCCUPANCY_MAX` | 300 / 5 / 10 | ROI event thresholds |
| `DISPLAY_FPS` / `DISPLAY_MAX_W` / `JPEG_QUALITY` | FPS / 0 / 80 | encoder thread |
| `LF_CHANNEL` / `LF_PORT_BASE` / `WS_METRICS_HZ` | 4 / 5030 / 1.0 | serving |
| review: `OPENROUTER_MODEL`, `AUTO_ACCEPT_CONF`, `SEND_LOWCONF_ONLY`, `IDLE_SECONDS`, `REVIEW_MIN_FRAMES` | gemini-3-flash / 0.85 / off / 300 / 5 | review agent |

---

## 14. Runtime state (must persist → volumes when containerized)
- `rois/ch{N}.json` — ROI calibration (read-write via `/rois`).
- `logs/ch{N}_{events,perf}.jsonl` — observability.
- `crops/ch{N}/{rid}_{veh,plate}.jpg` — review-agent inputs.
- `review_agent/state/` — `ch{N}_seq.txt` (record-id counter), `ch{N}_checkpoint.json` (cursor), `ch{N}.lock`.
- `review_agent/{out,dataset}/` — corrected events, review queue, human-verified, the retraining dataset.

---

## 15. External dependencies
- **Dahua VMS** (`vms.py`): `Device/Login` → `RealPlay/Start` → `realplay/stream/{id}` (MJPEG) →
  `Stop`/`Logout`. `sdk_channel = LF_CHANNEL + VMS_CHANNEL_OFFSET` (offset −1). Env: `VMS_URL`,
  `VMS_DEVICE_IP/PORT`, `VMS_USERNAME/PASSWORD`.
- **OpenRouter** (review agent): `OPENROUTER_API_KEY` (+ `YOUR_SITE_URL/NAME`). ⚠️ `review_agent/config.py`
  currently `load_dotenv(<repo>/orchestrator/.env)` — a host path; in a container inject the key as env.
- **PaddleOCR PP-OCRv6** weights download on first use (cache `~/.paddlex`) — bake at image build.

---

## 16. Known dead code / caveats (see `sprints/SERVICE_ANALYSIS_live_feed.md`)
- `run.py` — **broken** (`process()` now returns a 2-tuple; `run.py:117/120` would `AttributeError`). Use `app.py`.
- `app.py:_FRAME_INTERVAL`, `perf.inc_dropped()` — dead.
- `VEHICLE_CONF` — imported but unused (tracker uses `TRACK_CONF`).
- `plate_recognition/ocr.py:75 _reader` — a **warm OCR singleton** used by the standalone
  `plate_recognition` REST service; importing the module from live_feed eagerly loads a **second**
  OCR engine live_feed never uses (it builds its own in `_ensure_loaded`). Not dead — a memory
  dedup opportunity (have live_feed reuse the singleton), not a deletion.
- `plate_category_cls.pt`, root `yolo26n*.pt` — not referenced at runtime.

---

## 17. Local HSV colour extraction

The live colour path is fully local and follows this architecture:

```text
Vehicle Detection
→ ByteTrack
→ Vehicle Crop
→ 5 Paint ROIs
→ HSV
→ Quality Weighting
→ Temporal Aggregation
→ Final Colour
```

Each tracked vehicle contributes HSV observations from five paint-oriented ROIs.
Crop size, sharpness, exposure, and clipping determine the observation quality
weight. The temporal decision aggregates valid observations until an early lock,
the sample budget, or track finalization, then writes the final label and confidence
directly to `profile.colour` and `profile.colour_conf`.

External LLM/VLM colour verification was removed from the live colour path because
network/API latency is incompatible with the real-time requirement. Colour
extraction starts no external-colour worker or queue, makes no network request, and
emits no late colour-correction WebSocket event. The independent offline
`review_agent` remains available for post-processing finalized records and does not
participate in live colour extraction.

The local MP4 harness exercises the same detector, ByteTrack, ROI, quality, and
temporal HSV code and produces an annotated MP4 plus JSONL diagnostics:

```bash
/home/intern/.venv/bin/python scripts/test_live_pipeline_video.py \\
  --video /home/intern/vehicule/video.mp4 \\
  --output /home/intern/vehicule/hsv_local.mp4 \\
  --device auto
```

The diagnostics file is written beside the video as
`/home/intern/vehicule/hsv_local_tracks.jsonl`. The production default remains
`COLOR_FEATURE_MODE=hsv`; `hsv_gray` and `hsv_lab` are experimental local-only
feature modes.
