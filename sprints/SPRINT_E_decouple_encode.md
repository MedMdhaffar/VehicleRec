# Sprint E — Decouple display encoding from the analytics loop (fix FPS / line-count skipping)

Move all JPEG encoding **off** the per-frame analytics thread so tracking runs at a
true 15 FPS. This fixes the real, observed bug: **line-cross counting skips vehicles**
because the bbox "jumps" between processed frames. The jump is a *symptom* — the
analytics loop is throttled to ~9 FPS by JPEG encoding, so vehicles move too far
between processed frames, ByteTrack loses the track and re-IDs it (the "jump"), and the
crossing is never seen by a continuous track. The GPU is idle because the bottleneck is
**CPU JPEG encoding**, not the model.

> Read `live_feed/HOW_IT_WORKS.md` §8 (known bottleneck) first. This sprint implements
> the fix flagged there.

---

## 0. Root cause (measured / confirmed in code)

The analytics thread (`app.py:_pipeline_loop`) does **two full-frame 1080p JPEG encodes
every iteration**, both on the hot loop, **even when nobody is watching**:

1. `app.py:129` — `cv2.imencode(".jpg", frame, …)` → raw snapshot for `/snapshot`.
2. `app.py:139-146` — `draw_live_frame(...)` → annotates **and** encodes for `/stream.mjpg`.

1080p CPU encode ≈ 40-80 ms each → ~110 ms+/iteration → loop capped at ~9 FPS. Model
inference is ~25 ms (≈40 FPS capable). So the model/GPU is not the limit — the encode is.

Effect on counting: at 9 FPS a vehicle's foot-point moves a large distance between
processed frames → ByteTrack's motion/IoU association fails → **track-ID churn** → the
(already correct) `roi.segment_crosses` logic never gets a continuous `prev→cur`
foot-point pair spanning the counting line for one stable ID → **missed counts**.

---

## 1. The fix in one line

**The analytics thread must never encode (or draw) a JPEG.** It only: grab → `process`
→ broadcast → publish the latest raw frame. A **separate encoder thread** draws + encodes
the display stream on its own cadence, and only while a stream client is connected.
`/snapshot` encodes on demand.

The frontend already gets live analytics (counts/profiles/metrics) over the **WebSocket**,
not the video — so this is **backend-only**. The video stays real-time because the encoder
thread reads the same fresh, fast-updated `active_profiles` + ROI state.

---

## 2. Architecture: before → after

```
BEFORE (coupled, ~9 FPS — both analysis and video stuck behind encode)
  [ pipeline thread ]
    grab → imencode(raw)        ← encode #1 (every frame, even if unused)
         → tracker.process()    ← tracking + line counting
         → draw_live_frame()    ← encode #2 (draw + encode, every frame)
         → broadcast(WS)

AFTER (decoupled)
  [ analytics thread @ 15 FPS ]              [ encoder thread @ DISPLAY_FPS ]
    grab → tracker.process()                   if stream clients > 0:
         → broadcast(WS profiles/zones)          frame   = _latest_frame   (raw, shared)
         → publish _latest_frame (raw ndarray)   ann     = frame.copy()
    (NO encode, NO draw)                          with _profiles_lock:
                                                     draw_overlay(ann, profiles, roi)  ← fast draws only
                                                  _latest_jpeg = imencode(ann)         ← slow, OUTSIDE lock
  /stream.mjpg  → serves _latest_jpeg (unchanged)
  /snapshot     → imencode(_latest_frame) on demand (raw)
```

Key: the encoder holds `_profiles_lock` **only for the in-place draws** (a few ms — no
frame copy, no encode inside the lock), so it never re-couples to the analytics thread.

---

## 3. Files to change (backend only)

### 3.1 `common/annotate.py` — split draw from encode
Currently `draw_live_frame(frame, profiles, jpeg_quality, roi_config, roi_analytics)`
does `ann = frame.copy()`, draws everything, then `cv2.imencode` and returns **bytes**.

**Refactor into two functions:**
- `draw_overlay(ann, profiles, roi_config=None, roi_analytics=None) -> None`
  — draws **in place** onto a caller-provided `ann` ndarray. This is the entire current
  body **minus** the leading `ann = frame.copy()` and **minus** the trailing
  `cv2.imencode(...) / return buf.tobytes()` (both `return` sites — the early
  `roi_config is None` one and the final one).
- Keep `draw_live_frame(frame, profiles, jpeg_quality=80, roi_config=None,
  roi_analytics=None) -> bytes` as a **thin wrapper** for backward compatibility:
  ```python
  def draw_live_frame(frame, profiles, jpeg_quality=80, roi_config=None, roi_analytics=None) -> bytes:
      ann = frame.copy()
      draw_overlay(ann, profiles, roi_config=roi_config, roi_analytics=roi_analytics)
      ok, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
      return buf.tobytes() if ok else b""
  ```
  (No other caller of `draw_live_frame` should break — it's only used in `app.py`.)

### 3.2 `live_feed/config.py` — display knobs
Add (env-overridable), near the serving section:
```python
DISPLAY_FPS   = int(os.environ.get("DISPLAY_FPS",   str(FPS)))  # encoder-thread cadence; ≤ FPS
DISPLAY_MAX_W = int(os.environ.get("DISPLAY_MAX_W", "0"))       # 0 = full res; else downscale display only
```
`DISPLAY_MAX_W` is optional: if > 0, the encoder resizes `ann` to that width before
encoding (analytics still runs on the full frame — detection/tracking unaffected). Leave
0 by default; it's a cheap lever if the encoder thread itself ever gets hot.

### 3.3 `live_feed/app.py` — the main change
**Shared state (replace the current `_latest_jpeg` / `_latest_raw_jpeg` block):**
```python
_latest_frame: "np.ndarray | None" = None   # raw frame published by analytics thread
_latest_jpeg:  bytes | None = None           # annotated display JPEG produced by encoder thread
_frame_lock = threading.Lock()               # guards _latest_frame
_jpeg_lock  = threading.Lock()               # guards _latest_jpeg
_perf_lock = threading.Lock()
_profiles_lock = threading.Lock()
_stream_clients = 0                          # MJPEG viewers connected
_stream_clients_lock = threading.Lock()
_encoder_thread: threading.Thread | None = None
```
(Remove `_latest_raw_jpeg` and `_raw_lock`. Add `import numpy as np` if not present.)

**`_pipeline_loop` — strip BOTH encodes, publish raw frame:**
- DELETE the raw-encode block (`app.py:128-132`).
- DELETE the annotate+encode block (`app.py:138-148`) including the `perf.stage("encode")`.
- After `tracker.process(...)` + `perf.tick(...)`, publish the raw frame:
  ```python
  with _frame_lock:
      _latest_frame = frame
  ```
- Keep `grab`, `process`, `broadcast`, perf reporting, and the `next_tick` pacing as-is.
- The loop's per-iteration time now reflects true processing (~25 ms) → it actually hits
  `FPS=15`.

**New encoder thread:**
```python
def _encoder_loop(stop_evt: threading.Event) -> None:
    global _latest_jpeg
    interval = 1.0 / max(1, cfg.DISPLAY_FPS)
    next_tick = time.monotonic()
    while not stop_evt.is_set():
        with _stream_clients_lock:
            have_clients = _stream_clients > 0
        if have_clients and _tracker is not None:
            with _frame_lock:
                frame = _latest_frame
            if frame is not None:
                ann = frame.copy()                       # outside profiles_lock
                with _profiles_lock:                      # held ONLY for fast in-place draws
                    draw_overlay(ann, _tracker.active_profiles.values(),
                                 roi_config=_roi_config, roi_analytics=_tracker._roi)
                if cfg.DISPLAY_MAX_W and ann.shape[1] > cfg.DISPLAY_MAX_W:
                    h = int(ann.shape[0] * cfg.DISPLAY_MAX_W / ann.shape[1])
                    ann = cv2.resize(ann, (cfg.DISPLAY_MAX_W, h))
                ok, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
                if ok:
                    with _jpeg_lock:
                        _latest_jpeg = buf.tobytes()
        next_tick += interval
        sleep_dur = next_tick - time.monotonic()
        if sleep_dur > 0:
            time.sleep(sleep_dur)
        else:
            next_tick = time.monotonic()                  # fell behind; resync
```
Import `draw_overlay` from `common.annotate` (alongside / instead of `draw_live_frame`).

**`/start`** — launch the encoder thread next to the pipeline thread (after `_thread.start()`):
```python
global _encoder_thread
_encoder_thread = threading.Thread(target=_encoder_loop, args=(_stop_evt,),
                                   daemon=True, name=f"encoder-ch{cfg.LF_CHANNEL}")
_encoder_thread.start()
```
(`_stop_evt` already gates both; both are cleared in `/start` and set in `/stop`.)

**`/stop`** — join the encoder thread too (after the pipeline join):
```python
if _encoder_thread and _encoder_thread.is_alive():
    await loop.run_in_executor(None, _encoder_thread.join, 5)
```

**`/stream.mjpg`** — count clients so the encoder idles when nobody watches:
```python
@app.get("/stream.mjpg")
async def stream_mjpg():
    global _stream_clients
    with _stream_clients_lock:
        _stream_clients += 1
    async def _generator():
        try:
            interval = 1.0 / max(1, cfg.DISPLAY_FPS)
            while _started:
                with _jpeg_lock:
                    data = _latest_jpeg
                if data:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                await asyncio.sleep(interval)
        finally:
            global _stream_clients
            with _stream_clients_lock:
                _stream_clients = max(0, _stream_clients - 1)
    return StreamingResponse(_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
```
(The `finally` ensures disconnects decrement the counter.)

**`/snapshot`** — encode the raw frame on demand (replaces serving `_latest_raw_jpeg`):
```python
@app.get("/snapshot")
async def snapshot():
    with _frame_lock:
        frame = _latest_frame
    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available yet")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
    if not ok:
        raise HTTPException(status_code=500, detail="encode failed")
    return Response(content=buf.tobytes(), media_type="image/jpeg")
```

**`/health`** — `frame_ready` now checks `_latest_frame` instead of `_latest_jpeg`
(so it's true as soon as the pipeline produces a frame, regardless of viewers).

### 3.4 `live_feed/perf.py` — minor
The `perf.stage("encode")` block is removed from the analytics loop, so the "encode"
timer disappears from the analytics perf report (correct — it's no longer on that thread).
Optional: add a separate counter for encoder-thread FPS if you want it observable; not
required. The existing `grab` / `track` / `ocr` / `plate_detect` stages stay.

### 3.5 `live_feed/stream.py` — NO change
`FrameGrabber` already keeps only the latest frame (grab-latest, no backlog) — this is
correct and is what we want. **Do not add a buffer.** Note: `get_latest()` *consumes*
(`self._latest = None`), so only the analytics thread pulls frames; the encoder reads the
published `_latest_frame`, never the grabber. The raw frame is read-only after publish
(`process` only crops views, never mutates), so sharing the ndarray reference is safe.

---

## 4. Frontend — analysis result: NO changes needed

Confirmed by reading the components. The split already exists client-side:

| File | Role | Source | Change |
|------|------|--------|--------|
| `CameraView.jsx` | orchestrates the view | `/stream.mjpg` (`<img>`) **+** `/ws` | none |
| `TrafficPanel.jsx` | line counts + zone events | WS `zone_event` (`crossing`) → `lineCounters` | none |
| `MetricsStrip.jsx` | fps / latency / stage timers | WS `metrics` | none |
| `ProfilesTable.jsx` | per-vehicle records | WS `profile` / `snapshot` | none |
| `App.jsx`, `config.js` | tabs, URLs | — | none |

The line counts the user sees in **TrafficPanel** come from WS `crossing` events emitted
by the analytics thread (`app.py:_broadcast_zone_event` ← `roi.process_frame`), **not**
from the video. Speeding up the analytics thread makes those counts *more* accurate (no
ID churn) with zero frontend work. The counts drawn *into the video* (by `draw_overlay`
reading `roi_analytics._line_counts`) stay consistent with TrafficPanel because both read
the same ROI state.

→ After backend changes, run `npm run build` in `live_feed/frontend` only as a sanity
check; do **not** start the dev server.

---

## 5. Build order

1. `common/annotate.py` — split into `draw_overlay` (in-place) + `draw_live_frame` wrapper.
   Compile-check.
2. `live_feed/config.py` — add `DISPLAY_FPS`, `DISPLAY_MAX_W`.
3. `live_feed/app.py` — shared-state rework, strip both in-loop encodes, publish
   `_latest_frame`, add `_encoder_loop` + thread wiring in `/start` & `/stop`, client
   counter on `/stream.mjpg`, on-demand `/snapshot`, `/health` tweak. Compile-check.
4. Sanity: `npm run build` in the frontend (no code change expected).

---

## 6. Acceptance criteria

- [ ] With the dashboard open on ch4, `/metrics` (or the MetricsStrip) shows the
      analytics loop at **~15 FPS** (was ~9), and the analytics per-frame time ≈ the
      `track` stage (~25 ms) with **no** `encode` stage on that thread.
- [ ] GPU still mostly idle (this was never GPU-bound); CPU no longer pinned by a
      double 1080p encode on the analytics thread.
- [ ] **Line counting:** vehicles crossing the line are counted reliably — the
      `prev→cur` foot-point steps are small/continuous, ByteTrack holds the track_id
      across the line, `segment_crosses` fires once per vehicle. Manually drive a clip /
      watch live: counts match observed vehicles (no skips).
- [ ] Video stream still shows live boxes + ROI overlays + line counts, visually fresh.
- [ ] Closing all stream viewers → encoder thread idles (no JPEG produced); `/ws`
      analytics keep flowing; reopening a viewer resumes the video.
- [ ] `/snapshot` still returns a clean raw (un-annotated) frame for the calibration
      canvas.
- [ ] `/start` then `/stop` cleanly starts/joins both the pipeline and encoder threads;
      no thread leak on restart.

---

## 7. Notes / non-goals

- **Not a buffering change.** The grabber stays grab-latest. Adding a frame buffer would
  add latency and process stale frames — the opposite of the goal.
- **Model choice (separate decision):** for the *live tracker*, FPS/coherence matters more
  than per-frame class accuracy, so keep a fast detector (yolo26n/m) live. RT-DETR-L is
  heavier → lower live FPS → worse tracking/counting; it belongs in the **offline review
  agent**, not live_feed.
- If line counting still occasionally skips after the FPS fix (fast vehicles, brief
  occlusion at the line), the next lever is short track-ID bridging or ByteTrack tuning
  (`track_buffer`, `match_thresh`) — out of scope here; revisit only if needed.
- `DISPLAY_FPS` < `FPS` (e.g. 10) is a valid way to spend even less CPU on display while
  keeping analytics at 15.
