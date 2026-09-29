# Sprint D — Review Agent (VLM post-analysis, maker + plate DB, dataset feedback)

A module **inside `live_feed`** that runs an LLM (Gemini 3 Flash, via OpenRouter +
LangChain structured output) **offline, alongside the live cameras**, to review the
best crop of every finalized vehicle: confirm/correct what our models predicted,
add the **vehicle maker**, note the **real plate nature** for a future DB, and curate
a **retraining dataset**. It never touches the real-time hot loop.

> Read `live_feed/HOW_IT_WORKS.md` first — this sprint extends that pipeline. The
> ROI module (`live_feed/roi.py`) is the precedent for "a module inside live_feed".

---

## 0. Why / intent (one paragraph)

Our live models (RT-DETR vehicle class, HSV colour, PaddleOCR digits, تونس word
detector) are good but imperfect, and they produce **no maker** and **no fine plate
nature**. A cheap, high-end VLM reviewing the *best single crop per track* can (a)
catch confident-but-wrong reads, (b) add the maker, (c) record the real plate
category (diplomatic/military/RS…) as **metadata only** — these stay operationally
`other` because we lack data to train them — and (d) build a **curated dataset** so we
can retrain better models. The agent is a **second opinion + a labeling engine**, not
a runtime dependency.

---

## 1. Hard rules (do not violate)

1. **Offline. Never on the analytics loop.** No LLM call inside `EnrichedTracker.process`.
   The only live-path change is *saving a crop + writing one JSONL line on finalize*.
2. **The agent can never take live_feed down.** Smoke-test fail, API outage, exception
   in the loop → log + stay idle; the camera pipeline keeps running.
3. **VLM is not ground truth.** Use the **agree/disagree** split — agreement → trusted
   dataset; disagreement / low-confidence → human `review_queue.jsonl`.
4. **One review per track.** The unit is the *best crop per track*, not per-frame.
5. **Single-flight.** Never two passes at once. `sleep(IDLE_SECONDS)` is measured
   **after** a pass fully completes. Lock file guards double-launch.
6. **Rotation-safe cursor.** `livelog.py` uses `RotatingFileHandler`; the resume cursor
   must be **content-based** (`record_id`), never byte offset / line number.

---

## 2. Module layout

```
live_feed/review_agent/
  __init__.py
  config.py        # env (OPENROUTER_API_KEY from orchestrator/.env), model id, thresholds, paths, IDLE_SECONDS
  schemas.py       # Pydantic: HealthCheck, VehicleReview(In/Out), PlateReview(In/Out), ReviewCheckpoint
  client.py        # build_llm() → ChatOpenAI(OpenRouter); vehicle_chain / plate_chain (with_structured_output)
  smoke.py         # startup HealthCheck structured-output ping ("status=ok")
  cursor.py        # load/save ReviewCheckpoint atomically (per channel)
  reconcile.py     # agree/disagree + confidence thresholds → routing decision
  dataset.py       # write trusted samples, corrected_events, review_queue, maker/plate DB
  worker.py        # the single-flight loop (collect → review → reconcile → write → advance cursor → sleep)
  run.py           # CLI: --once / --loop (manual + testing; same worker entry)
```

Wiring: `app.py` starts `worker.start_thread(channel)` as a **daemon thread** when the
service comes up (next to the pipeline thread). `run.py` is the same worker callable
standalone for testing.

State / data dirs (create on start):
```
live_feed/crops/ch{N}/                 # best crops, written by pipeline.py on finalize
live_feed/logs/ch{N}_review_input.jsonl# one record_id-stamped line per finalized track
live_feed/review_agent/state/ch{N}_checkpoint.json
live_feed/review_agent/state/ch{N}.lock
live_feed/review_agent/out/ch{N}_corrected_events.jsonl
live_feed/review_agent/out/ch{N}_review_queue.jsonl
live_feed/review_agent/dataset/        # the retraining DB (see §8)
```

---

## 3. Live-path change #1 — best crop per track (`pipeline.py` + `schemas.py`)

**Goal:** keep the single best crop per track (vehicle + plate) in memory, write it to
disk only on **finalize** (once per track) — negligible FPS cost, off the hot path.

### 3.1 `schemas.py` — add runtime-only fields to `VehicleProfile`
```python
# best-crop capture (runtime only, not serialized)
_best_score: float = field(default=-1.0, repr=False)
_best_crop: "np.ndarray | None" = field(default=None, repr=False)
_best_plate_crop: "np.ndarray | None" = field(default=None, repr=False)
# predictions snapshot at the moment the best crop was captured
_best_type: str = field(default="unknown", repr=False)
_best_colour: str = field(default="unknown", repr=False)
```
(Keep `PlateInfo` as the live plate prediction; we snapshot OCR/type at finalize.)

### 3.2 `pipeline.py` — update best crop each frame (cheap score)
Inside the per-box loop, after `crop` is computed and type/colour are known, score the
crop and keep the best. **Default score = bbox area** (cheap, closest vehicle is usually
clearest). Optionally multiply by a sharpness term — keep it behind a config flag so the
hot loop stays cheap by default.

```python
score = (x2 - x1) * (y2 - y1)            # area; cheap, no extra compute
if score > profile._best_score:
    profile._best_score  = score
    profile._best_crop   = crop.copy()    # copy — frame buffer is reused
    profile._best_type   = profile.type
    profile._best_colour = profile.colour
```
When a plate crop is produced in the cascade (the `plate_crop` already cut in step 3),
keep the best plate crop the same way (score by plate width, only when a plate is read):
```python
if plate_crop.size > 0 and (px2 - px1) > getattr(profile, "_best_plate_w", 0):
    profile._best_plate_w = (px2 - px1)
    profile._best_plate_crop = plate_crop.copy()
```
(Add `_best_plate_w` as a runtime field, or reuse `_best_score` logic — keep it simple.)

### 3.3 `pipeline.py` — on finalize, write crop + review-input line
In the finalize block (where `profile.status = "final"`), before/after appending to
`emitted`, hand the profile to a small writer. **Do not** call the LLM here. Use a
module-level `ReviewInputWriter` (in `review_agent/dataset.py` or a tiny `ingest.py`)
that:
1. assigns a monotonic `record_id` (per channel, persisted counter — see §5),
2. saves `ch{N}/{record_id}_veh.jpg` and (if present) `{record_id}_plate.jpg`,
3. appends one JSONL line (the **review input**, schema in §4.1).

Guard everything in `try/except` and log — a crop-write failure must not break finalize.
Only write a review-input line for tracks worth reviewing (e.g. `frames_seen >= K_CLASSIFY`
and a non-empty best crop); skip junk tracks.

---

## 4. Schemas (Pydantic — `review_agent/schemas.py`)

### 4.1 Review input (written by pipeline, read by agent) — JSONL line
```python
class ReviewInput(BaseModel):
    record_id: int
    channel: int
    track_id: int
    finalized_at: float
    vehicle_crop_path: str
    plate_crop_path: str | None = None
    # our model predictions (to confirm/correct)
    vehicle_class_predicted: str
    colour_predicted: str
    ocr_predicted: str | None = None
    plate_type_predicted: str | None = None        # "tounes" | "other"
    # confidences (used to decide what to SEND / how to reconcile)
    type_conf: float = 0.0
    colour_conf: float = 0.0
    digits_conf: float = 0.0
    plate_type_conf: float = 0.0
```

### 4.2 LLM structured outputs (what Gemini must return)
```python
class VehicleReview(BaseModel):
    vehicle_class: str        = Field(description="confirmed or corrected class")
    colour: str               = Field(description="confirmed or corrected colour")
    vehicle_maker: str        = Field(description="brand only e.g. Kia, BMW, Renault; 'unknown' if unsure")
    confidence: float         = Field(ge=0, le=1, description="overall confidence 0..1")

class PlateReview(BaseModel):
    ocr: str                  = Field(description="confirmed or corrected plate digits; '' if unreadable")
    plate_type: str           = Field(description="ONLY 'tounes' or 'other'")
    observed_category: str    = Field(description="real nature for the DB: private/diplomatic/military/RS/taxi/transport/foreign/unknown — metadata only, NOT operational")
    confidence: float         = Field(ge=0, le=1)

class HealthCheck(BaseModel):
    status: str               = Field(description="echo the literal 'ok'")
```

### 4.3 Resume cursor
```python
class ReviewCheckpoint(BaseModel):
    channel: int
    last_record_id: int = 0   # highest record_id fully processed AND written
    updated_at: float = 0.0
```

### 4.4 Final reviewed record (written to corrected_events / dataset mirror)
```python
class ReviewedRecord(BaseModel):
    record_id: int
    channel: int
    track_id: int
    vehicle: VehicleReview
    plate: PlateReview | None = None
    predicted: ReviewInput            # keep the original prediction alongside
    agreement: dict                   # {"class": bool, "colour": bool, "ocr": bool, "plate_type": bool}
    routed_to: str                    # "trusted" | "review_queue"
    reviewed_at: float
```

---

## 5. record_id counter (monotonic, persisted)

A per-channel counter so ids survive restarts and never collide:
`review_agent/state/ch{N}_seq.txt` holding the last id. `next_record_id(channel)` reads,
increments, writes (atomic `os.replace`). Called by the pipeline writer on each finalize.
Restart-safe: ids only ever grow, so the cursor's `> last_record_id` filter stays valid.

---

## 6. LangChain → OpenRouter client (`review_agent/client.py`)

Per the LangChain OpenRouter docs (multimodal + structured output):
```python
import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv("/home/malek/workflow/orchestrator/.env")   # OPENROUTER_API_KEY, SITE vars

def build_llm():
    return ChatOpenAI(
        model="google/gemini-3-flash-preview",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        default_headers={
            "HTTP-Referer": os.environ.get("YOUR_SITE_URL", "http://localhost"),
            "X-Title":      os.environ.get("YOUR_SITE_NAME", "vehicle_services"),
        },
        temperature=0,
        timeout=60,
        max_retries=2,
    )
```

Structured chains — **note the `method=` fallback** (Gemini-over-OpenRouter may need
`json_schema`; if it errors, fall back to `function_calling`):
```python
_llm = build_llm()
vehicle_chain = _llm.with_structured_output(VehicleReview)   # add method="json_schema" if needed
plate_chain   = _llm.with_structured_output(PlateReview)
```

Multimodal message (image as base64 data URL):
```python
import base64, cv2

def _data_url(path: str) -> str:
    img = cv2.imread(path)
    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode()
    return f"data:image/jpeg;base64,{b64}"

def review_vehicle(inp: ReviewInput) -> VehicleReview:
    msg = {"role": "user", "content": [
        {"type": "text", "text": VEHICLE_PROMPT.format(
            cls=inp.vehicle_class_predicted, colour=inp.colour_predicted)},
        {"type": "image_url", "image_url": {"url": _data_url(inp.vehicle_crop_path)}},
    ]}
    return vehicle_chain.invoke([msg])
```
`review_plate` is analogous (plate crop + `ocr_predicted` + `plate_type_predicted`).
**Prompts** must state: confirm if right, correct if wrong; maker = brand only, `unknown`
if unsure; `plate_type` is strictly `tounes`/`other`; `observed_category` is the real
nature for the DB and does **not** change `plate_type`.

Concurrency = async or a small thread pool (e.g. 8 in flight). "Batch" = concurrency,
**one image per call** (packing many images per call wrecks grounding).

---

## 7. Smoke test (`review_agent/smoke.py`) — startup gate

Runs once when the worker thread starts, **before** the loop:
```python
def smoke_test() -> bool:
    try:
        chain = build_llm().with_structured_output(HealthCheck)
        out = chain.invoke([{"role": "user", "content":
            "Reply with status set to the literal string ok."}])
        ok = (out.status.strip().lower() == "ok")
        log.info("✓ review_agent: LLM reachable (structured output OK)" if ok
                 else "review_agent: unexpected smoke reply: %r", out)
        return ok
    except Exception as e:
        log.warning("review_agent DISABLED — smoke test failed: %s", e)
        return False
```
If it returns `False`, the worker logs and **stays idle** (no loop). live_feed unaffected.
This validates key + model id + connection + `with_structured_output` in one call.

---

## 8. Reconcile + outputs (`reconcile.py`, `dataset.py`)

Config thresholds (`review_agent/config.py`, env-overridable):
```
AUTO_ACCEPT_CONF = 0.85    # maker/observed_category accepted into DB above this
SEND_LOWCONF_ONLY = False  # if True, only send tracks where our model was unsure (cost saver)
IDLE_SECONDS = 300
```

Per reviewed track:
- **agreement** = compare each field (`vehicle_class`, `colour`, `ocr`, `plate_type`)
  predicted vs returned.
- **Route:**
  - all compared fields agree → `trusted` → write to `dataset/`.
  - any disagreement, OR any `confidence < AUTO_ACCEPT_CONF` → `review_queue.jsonl`
    (human verifies a correction before it becomes training truth).
- **Always** record `vehicle_maker` and `observed_category` (new fields, no prediction to
  compare) — into the DB if `confidence ≥ AUTO_ACCEPT_CONF`, else into the queue.

Outputs:
- `out/ch{N}_corrected_events.jsonl` — one `ReviewedRecord` per track (downstream truth).
- `out/ch{N}_review_queue.jsonl` — disagreements / low-conf for human glance.
- `dataset/` — the retraining DB:
  ```
  dataset/makers/<maker>/<ch>_<record_id>.jpg          # maker classifier (folder-per-class)
  dataset/plate_type/<tounes|other>/<ch>_<record_id>.jpg
  dataset/plate_observed/<category>/<ch>_<record_id>.jpg  # future fine plate classifier
  dataset/ocr/labels.jsonl   # {crop_path, ocr_corrected}  for OCR/recognition retraining
  dataset/reviews.jsonl      # full ReviewedRecord mirror
  ```
  Idempotent: keyed by `record_id`, re-processing overwrites its own entry (no dupes).

---

## 9. Worker loop (`worker.py`) — single-flight, rotation-safe resume

```python
def run_pass(channel: int) -> int:
    cp = load_checkpoint(channel)                       # ReviewCheckpoint
    rows = [r for r in read_review_input(channel)       # parse JSONL → ReviewInput
            if r.record_id > cp.last_record_id]         # content-based filter (rotation-safe)
    rows.sort(key=lambda r: r.record_id)
    if not rows:
        return 0
    if SEND_LOWCONF_ONLY:
        rows = [r for r in rows if _is_uncertain(r)]    # maker still wanted; see note
    max_done = cp.last_record_id
    for r in rows:                                       # (concurrency in real impl)
        try:
            reviewed = review_and_reconcile(r)           # 2 LLM calls + routing
            write_outputs(reviewed)                      # corrected/queue/dataset
            max_done = max(max_done, r.record_id)        # advance only AFTER write
        except Exception as e:
            log.warning("review failed record_id=%s: %s", r.record_id, e)
            break                                        # stop; retry this tail next pass
    save_checkpoint(channel, max_done)                  # atomic write
    return len(rows)

def loop(channel: int):
    if not smoke_test():
        return                                           # stay idle, live_feed unaffected
    if not acquire_lock(channel):                        # ch{N}.lock with PID
        log.warning("review_agent already running for ch%s — exiting", channel); return
    try:
        while not stop_event.is_set():
            n = run_pass(channel)
            log.info("review_agent ch%s: pass done (%d reviewed)", channel, n)
            stop_event.wait(IDLE_SECONDS)                # 300s AFTER completion
    finally:
        release_lock(channel)
```
`save_checkpoint` writes a temp file then `os.replace` (atomic). The cursor advances only
after each record's outputs are written, so an interrupted pass re-reads its tail (never
skips, never corrupts). `start_thread(channel)` wraps `loop` in a daemon `Thread`.

> Note on `SEND_LOWCONF_ONLY`: when on, we skip LLM review of cleanly-locked
> high-confidence reads to save cost — but maker is new for *all* vehicles, so if you
> want maker everywhere, keep it `False` (default) or add a "maker-only" lightweight call
> for the confident ones. Decide at tuning time.

---

## 10. app.py wiring

In the startup path that launches the pipeline thread, add:
```python
from live_feed.review_agent import worker as review_worker
review_worker.start_thread(LF_CHANNEL)   # daemon; self-disables if smoke fails
```
On shutdown, set the worker `stop_event` so the loop exits cleanly between passes.
Add a `GET /review/health` endpoint returning `{enabled, last_record_id, last_pass_at,
reviewed_total}` for observability (optional but cheap).

---

## 11. Build order

1. **schemas.py** — all Pydantic models (no I/O). Compile-check.
2. **config.py** — paths, thresholds, `IDLE_SECONDS`, load `.env`.
3. **client.py + smoke.py** — `build_llm`, chains, `smoke_test()`. **Run smoke standalone
   first** (`python -m live_feed.review_agent.smoke`) — must print the ✓ line before anything
   else. This is the go/no-go for the whole sprint.
4. **cursor.py** + record_id counter (§5) — atomic load/save, unit-test the `> last_id` filter.
5. **pipeline.py + schemas.py** live-path change (§3) — best-crop capture + finalize writer.
   Verify crops + `ch{N}_review_input.jsonl` appear during a short live run, FPS unchanged.
6. **client review fns + reconcile.py + dataset.py** — review one saved record end-to-end
   (`run.py --once`), inspect `corrected_events` / `dataset` / `review_queue`.
7. **worker.py** loop + lock — `run.py --loop`, confirm single-flight + cursor advance +
   sleep-after-completion across two passes.
8. **app.py** wiring — boots with live_feed, self-disables on smoke fail.

---

## 12. Acceptance criteria

- [ ] `smoke.py` prints `✓ review_agent: LLM reachable (structured output OK)` against
      `google/gemini-3-flash-preview`.
- [ ] During a live run, best crops + `ch{N}_review_input.jsonl` (with `record_id`) are
      written **on finalize only**; measured FPS unchanged vs before.
- [ ] `run.py --once` produces a `ReviewedRecord` with `vehicle_maker` filled and
      `plate.plate_type ∈ {tounes, other}` plus an `observed_category` note.
- [ ] Agreement routing works: a deliberately wrong prediction lands in
      `review_queue.jsonl`; an agreeing one lands in `dataset/`.
- [ ] Cursor: run twice → second pass processes **only new** records; kill mid-pass →
      restart re-reads the unfinished tail, no skips, no dupes (idempotent by `record_id`).
- [ ] Survives file rotation: rotate `ch{N}_review_input.jsonl` mid-run → resume still
      correct (content-based cursor).
- [ ] Single-flight: two `--loop` launches → second exits on the lock; sleep is measured
      after completion (a >5-min pass never overlaps the next).
- [ ] Kill the LLM (bad key) → smoke fails → agent idle, **live_feed cameras unaffected**.
- [ ] `dataset/` accumulates maker / plate_type / observed / ocr labels keyed by
      `record_id`, ready for retraining.

---

## 13. Open tuning knobs (decide while testing, not now)

- `AUTO_ACCEPT_CONF` (0.85 start), `SEND_LOWCONF_ONLY` (maker-everywhere vs cost).
- Best-crop score: area-only (default, cheap) vs area×sharpness (flag).
- Concurrency (8 start) and `IDLE_SECONDS` (300).
- Whether to pass the vehicle class into the plate prompt as a hint (e.g. taxi context) —
  default **no** (kept independent), revisit if it helps `observed_category`.
