# live_feed — Service Analysis (pre-cleanup / pre-dockerization)

Evidence-based audit of the **live_feed** real-time service: what it actually needs at
runtime, what is dead, what must persist, and the dockerization-critical gaps. Nothing
should be deleted that isn't on the DEAD list (with its evidence) — everything else is
KEEP or VERIFY-FIRST. Method: traced every import (incl. lazy/indented), every config
knob's usage, every model path, and the external-service surface.

---

## 1. Runtime dependency closure (the KEEP set)

The service is **3 Python packages**, nothing more:

```
live_feed/                      ← the service (FastAPI app + pipeline + review_agent)
  └─ common/                    ← annotate, colour, config, schemas   (leaf: no sibling imports)
  └─ plate_recognition/         ← ocr, plate_detector, plate_type, config, schemas  (imports common)
```

**Verified** (`grep` of all imports, including the lazy ones inside `EnrichedTracker._ensure_loaded`):
- `live_feed` imports only `common.{annotate,colour,config}` + `plate_recognition.{ocr,plate_detector,plate_type}`.
- `common` imports **no** sibling package (leaf). `common.annotate` uses `common.schemas`.
- `plate_recognition` runtime modules import only `common` + third-party.
- **NOT** imported anywhere by the service: `vehicle_classification/`, `live_inference/`, `training/`, `vehicle_class1/` (dataset).

### Exact files needed from each package
| Package | KEEP (runtime) | NOT needed at runtime |
|---|---|---|
| `live_feed` | `__init__, app, config, pipeline, perf, livelog, roi, schemas, stream, vms` + `review_agent/*` + `trackers/bytetrack_vehicles.yaml` + `rois/*.json` | `frontend/` (separate, dev-only), `run.py`*, `launch.sh`, `start_live_feed.sh` (host launchers) |
| `common` | `__init__, config, colour, annotate, schemas`, `models/vehicle_yolo26.pt` | `results/` (runtime output) |
| `plate_recognition` | `__init__, config, ocr, plate_detector, plate_type, schemas`, `models/best_matricule.pt`, `models/tounes_word_detector.pt` | everything else (see EXCLUDE §9) |

\* `run.py` — see Dead/Redundant (§8).

---

## 2. Python third-party dependencies (full runtime pip set)

Imported by the runtime closure (evidence: grep across the 3 packages):

| Package | Used by | In `requirements.txt`? |
|---|---|---|
| fastapi, uvicorn[standard], python-multipart | app serving | ✅ |
| pydantic>=2 | schemas (live_feed + review_agent + plate) | ✅ |
| opencv-python (`cv2`) | frames, crops, annotate, OCR pre | ✅ |
| numpy | everywhere | ✅ |
| ultralytics | vehicle detect+track, plate detect, word detect | ✅ (→ pulls **torch/torchvision**) |
| requests | `vms.py` (Dahua HTTP client) | ✅ |
| langchain-openai, langchain, python-dotenv | review_agent (Gemini via OpenRouter) | ✅ |
| **paddleocr** | `plate_recognition/ocr.py` (PP-OCRv6 `TextRecognition`) | **❌ MISSING** |
| **transformers** | OCR `engine="transformers"` path (primary) | **❌ MISSING** |
| **paddlepaddle** | OCR fallback path (`PaddleOCR(...)`) + paddleocr import dep | **❌ MISSING (verify)** |

> ⚠️ **CRITICAL GAP:** `requirements.txt` is missing the entire **OCR stack** (`paddleocr`,
> `transformers`, and likely `paddlepaddle`). `ocr.py` tries the transformers engine first
> (needs `transformers` + `torch`) and falls back to the Paddle pipeline (needs
> `paddlepaddle`). The Docker build **must** pin these or OCR fails at runtime. Exact pins
> to be resolved during the docker sprint by matching the working dev venv (`.venv_linux`).

**NOT a dependency:** `pandas` — appears only in a **docstring example** in `livelog.py:15`
(`import pandas as pd; pd.read_json(...)`). Do **not** add it.

---

## 3. Model / asset inventory

### KEEP (runtime weights — bake into image or mount)
| File | Size | Loaded by | Source of path |
|---|---|---|---|
| `common/models/vehicle_yolo26.pt` | 43M | vehicle detect+track | `common/config.py:11` (`VEHICLE_MODEL_PATH`) |
| `plate_recognition/models/best_matricule.pt` | 5.3M | plate detector | `plate_recognition/config.py:9` (`PLATE_DETECTOR_PATH`) |
| `plate_recognition/models/tounes_word_detector.pt` | 5.2M | تونس word-spot (`classify_type`) | `plate_recognition/config.py:12` (`WORD_DETECTOR_PATH`); confirmed in `plate_type.py:20` |
| `live_feed/trackers/bytetrack_vehicles.yaml` | — | tracker config | `config.py` (`TRACKER_CONFIG`) |

### PaddleOCR PP-OCRv6 — **must be baked at build**
- Model name: `OCR_REC_MODEL = "PP-OCRv6_medium_rec"` (`plate_recognition/config.py:16`).
- PaddleOCR **downloads** this on first use (cache `~/.paddlex`). For cloud/offline/reproducibility,
  **pre-download it during `docker build`** so the container never fetches at runtime.

### NOT runtime — exclude (DEAD weights, evidence below)
| File | Why not runtime |
|---|---|
| `plate_recognition/models/plate_category_cls.pt` (3.1M) | **Confirmed training-only** (repo-wide grep): referenced **only** by `train_classifier.py`. Never loaded at runtime. |
| `plate_recognition/yolo26n.pt`, `yolo26n-cls.pt` (root) | **Confirmed training-only**: referenced **only** as base-weight defaults in `train_classifier.py` / `train_word_detector.py`. Never loaded at runtime. |

---

## 4. External services & configuration

| Service | How reached | Config |
|---|---|---|
| **Dahua VMS** (frames) | `vms.py` HTTP → MJPEG | `VMS_URL`, `VMS_DEVICE_IP/PORT`, `VMS_USERNAME/PASSWORD`, `VMS_CHANNEL_OFFSET` (env) |
| **OpenRouter** (review agent) | `langchain-openai` → `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY`, `YOUR_SITE_URL`, `YOUR_SITE_NAME` (env) |

> ⚠️ **CRITICAL for Docker:** `live_feed/review_agent/config.py:8` does
> `load_dotenv(<...>/orchestrator/.env)` — a **hardcoded host path** 4 levels up. That path
> won't exist in the container. It won't *crash* (missing-file `load_dotenv` is a no-op, and
> `client.py` reads `os.environ`), but the key **must be injected as a real env var**. Cleanup:
> make the dotenv path optional/overridable (e.g. `REVIEW_ENV_FILE`), rely on injected env in Docker.

All other config (`live_feed/config.py`, `review_agent/config.py`, `plate_recognition/config.py`,
`common/config.py`) is already **env-overridable** — good for containerization.

---

## 5. Runtime state → must be VOLUMES (never baked, must persist)

| Path | Written by | Why volume |
|---|---|---|
| `live_feed/rois/*.json` | `POST /rois` (calibration) | **Calibration must survive restart/redeploy** |
| `live_feed/logs/ch*_{events,perf}.jsonl` | `livelog.py` | observability history |
| `live_feed/crops/ch*/` | pipeline finalize | review-agent input images |
| `live_feed/review_agent/{dataset,state,out}/` | review worker | dataset, resume cursor, corrected events |

Seed `rois/ch1.json`, `rois/ch4.json` exist in-repo — can be baked as defaults, but the dir
must be a writable volume at runtime.

---

## 6. Entry points

| Entry | Role | Docker disposition |
|---|---|---|
| `live_feed/app.py` (uvicorn) | **The service** — FastAPI, one process per camera (`LF_CHANNEL`) | **Container CMD** |
| `live_feed/launch.sh` | host: launches N uvicorn procs (one/channel) + MPS note | replaced by `docker-compose` (one service/channel) |
| `start_live_feed.sh` (top-level) | older host launcher (VMS+MPS env) | host-dev only; not in image |
| `live_feed/run.py` | standalone CLI pipeline (no web server) | **redundant — see §8** |

---

## 7. Confirmed live behavior (context, do-not-regress)
- Analytics loop is **encode-free** (Sprint E): publishes `_latest_frame`; a separate encoder
  thread + `/stream.mjpg` do display JPEG only when a client is connected.
- FPS cap removed → source-paced. Source-rate + drop-rate instrumented (`source_fps`/`drop_fps`).
- Tracking tuned via `bytetrack_vehicles.yaml` (low feed `TRACK_CONF=0.10`, `track_buffer=75`).
- Detector = **yolo26m** (`vehicle_yolo26.pt`). RT-DETR rejected (see `project_rtdetr_vs_yolo26m`).
These must remain intact through cleanup.

---

## 8. DEAD / REDUNDANT code (with evidence)

| Item | Evidence | Action |
|---|---|---|
| `live_feed/app.py:46 _FRAME_INTERVAL` | defined, **no usage** in app.py (FPS pacing removed) | remove (safe) |
| `live_feed/perf.py:61 inc_dropped()` | **never called** (drop-rate now read from grabber directly) | remove (safe) |
| `VEHICLE_CONF` (`config.py:10`, imported `pipeline.py:15`) | **imported but unused** in pipeline body (track() uses `TRACK_CONF`) | drop import + the unused knob (or keep knob as documented default — decide) |
| `plate_recognition/models/plate_category_cls.pt` | not referenced by any runtime path | exclude from image; remove if truly unused (VERIFY no other service uses it) |
| `plate_recognition/yolo26n*.pt` (root) | not referenced by runtime config | exclude from image |
| `live_feed/run.py` | **BROKEN, not just stale.** `run.py:117` `emitted = tracker.process(...)` captures the 2-tuple `(emitted, zone_events)` (`pipeline.py:95,304`), then `run.py:120` `for p in emitted` iterates the tuple → `p` = the list → `p.status` → `AttributeError` on the **first processed frame**. Also still has the pre-Sprint-E encode-in-loop + FPS-cap logic. | **remove** (it cannot run; keeping it invites confusion). If a headless CLI is wanted, write a thin one that calls the real `app` loop. |

### Wasteful import side-effect (NOT dead — needs a lazy-init refactor)
| Item | Evidence | Action |
|---|---|---|
| `plate_recognition/ocr.py:75` module-level `_reader = PlateReader(config.OCR_REC_MODEL)` | Importing `plate_recognition.ocr` **eagerly loads an OCR engine at import time**. live_feed imports the *class* (`pipeline.py:73`) and builds its **own** reader in `_ensure_loaded`, so live_feed loads **two** OCR engines and uses one — wasted memory + startup time, ×N per camera container. **NOT dead:** `plate_recognition/app.py:46,141` uses this global as the REST service's singleton. | **Make it lazy** (`get_reader()` instantiating on first call; point `app.py` at it). Do **NOT** delete — that breaks the plate_recognition REST service. |

---

## 9. EXCLUDE from the image (not runtime — `.dockerignore`)

- `training/` (473M), `vehicle_class1/` (dataset), `vehicle_classification/`
- `plate_recognition/`: `cls_dataset/`, `synthetic_data/`, `synthetic_generator.py`,
  `tounes_word_dataset/`, `runs/`, `runs_cls/`, `runs_word/`, `civilian_crops/`,
  `civilian_oneclass.py`, `cluster_classify.py`, `word_classify.py`, `extract_plate_crops.py`,
  `train_classifier.py`, `train_word_detector.py`, `test_plate_detection.py`, `test_outputs/`,
  `Tunisian license plate/`, `tunisian_plate/`, `arabic_test.jpg`, `image{1..5}.jpg`, `app.py`(?)
- `live_feed/frontend/` (separate; team owns the real UI), all `node_modules/`, `dist/`
- `**/__pycache__/`, `*.pyc`, the runtime volumes (`logs/`, `crops/`, `runs/`, review dataset/state/out)
- `sprints/`, `*.md` docs, `.git`

> Note `plate_recognition/app.py` and `vehicle_classification/` are **separate REST services**.
> If they're ever deployed too they get their **own** images — out of scope for the live_feed container.

---

## 10. Dockerization-critical risk register

1. **OCR stack missing from `requirements.txt`** (§2) — image will build but OCR breaks at runtime. Pin `paddleocr` + `transformers` (+ `paddlepaddle` if needed) from the working venv. **Highest risk.**
2. **PaddleOCR model download** (§3) — bake `PP-OCRv6_medium_rec` at build; else first-run network fetch (fails offline/cloud-locked).
3. **Hardcoded `orchestrator/.env` path** (§4) — inject `OPENROUTER_API_KEY` as env; make dotenv path optional.
4. **GPU base image** — `nvidia/cuda` + run with NVIDIA Container Toolkit; verify torch CUDA + paddle GPU match the CUDA version.
5. **Volumes** (§5) — forget these and calibration/dataset vanish on redeploy.
6. **Per-camera VRAM** — N× model load; size instance by VRAM (prior discussion). MPS optional for better sharing.
7. **Eager OCR load on import** (`ocr.py:75`) — live_feed loads a **second, unused** OCR engine at first-frame import (see §8). Compounds per-camera startup time + VRAM (×N). Fix with the lazy-init refactor before/with dockerization.

---

## 11. Open decisions before the cleanup sprint
1. ~~`live_feed/run.py` — remove or keep?~~ **RESOLVED: remove** — it's broken (crashes on the
   first frame, §8). If a headless CLI is later wanted, write a thin wrapper over `app`'s loop.
2. `VEHICLE_CONF` — drop entirely, or keep as a documented (currently unused) knob?
3. ~~`plate_category_cls.pt` + root `yolo26n*.pt` — confirm nothing else uses them?~~ **RESOLVED: training-only** (repo-wide grep — only the train scripts). Definitely exclude from the image; removing from the repo is optional (they're training inputs/outputs).
4. Debug `frontend/` — leave in repo (excluded from image), or also give it its own optional `node→nginx` container?

Note: the `ocr.py` eager-load (§8) is **not** a judgment call — it's a lazy-init refactor that
must preserve `plate_recognition/app.py`. Everything else in §8/§9 is removal-justified; the
remaining §11 items are the only true judgment calls. Once decided, the cleanup + Dockerfile +
compose sprint can be written against this map.
