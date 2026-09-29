# Sprint G — Dockerize live_feed (ONE container, all cameras, batched) — 📋 PLAN (rewritten for F2–F5 + H)

Package **live_feed** as a single GPU container that runs **all cameras in one batched process**
off a **shared TensorRT engine**, via docker-compose. Backend only (the frontend stays separate).

> **⚠️ Rewritten.** The original draft predates **F2** (TensorRT + batching), **F3** (single
> batched multi-camera process), **F4** (generic RTSP cameras), **F5** (single-page frontend),
> **Sprint H** (training-data collector), and the review-agent string-`cam_id` rework. The old
> "**one container per camera, models loaded per camera**" design is **obsolete** — see §0.

Decisions (locked):
- **ONE container for ALL cameras** (Dahua via `CHANNELS` + RTSP via `RTSP_CAMERAS`). Models load
  **once, shared** (~2.4 GB VRAM flat regardless of camera count) — not N× per camera.
- **Bake the `.pt` weights** into the image; **build the `.engine` at startup** on the target GPU
  (TensorRT engines are GPU-specific — cannot be baked) and **persist it in a volume** so restarts
  skip the multi-minute rebuild.
- Secrets/config via compose **`env_file` (.env)**; **single GPU**, no MPS (one process needs no
  time-slicing); **CUDA 12.8** (matches torch/torchvision `+cu128`).
- Build context = `vehicle_services/`. New files at its root: `Dockerfile`, `.dockerignore`,
  `docker-compose.yml`, `.env.example`.

---

## 0. What changed vs the original draft (the delta to apply)
| Original (pre-F3) | Now |
|---|---|
| One container **per camera** (`LF_CHANNEL` selects one) | **One container, all cameras** via `CHANNELS` + `RTSP_CAMERAS` |
| Each container loads its **own models (~2–3 GB each)** | Models loaded **once, shared**; **~2.4 GB flat** ([[project_live_feed_batched]]) |
| `LF_CHANNEL=4`, port per camera | `CHANNELS="4 1"`, **single port** (5034); per-cam access via `?cam=<id>` |
| No TensorRT | **Vehicle model → `.engine` built at startup**; `tensorrt-cu12` 10.x dep (~4 GB); persist engine |
| Dahua VMS only | **+ RTSP cameras** (F4) → container must reach the RTSP LAN (e.g. `10.0.0.78:554`) |
| review volumes split per camera | review files are `ch{cam}_*` in **shared** dirs → one set of volumes |
| — | **+ `datasets/` volume** for the Sprint H training-data collector (grows; mount it) |

---

## 1. Prerequisites / risk register
- **Host:** NVIDIA driver for **CUDA 12.8** + **NVIDIA Container Toolkit** (`--gpus all`). On the
  RTX 5080 (Blackwell sm_120) TensorRT must be **≥10.8** (already pinned). WSL2 works via Docker
  Desktop GPU integration.
- **TensorRT engine build at first boot** (`multistream.Models`): if `VEHICLE_ENGINE_PATH` is
  missing and `tensorrt` is importable, ultralytics exports the `.engine` (can take **minutes**).
  → set a **long HEALTHCHECK `start-period`** and **persist the engine in a volume** so subsequent
  boots are instant. `tensorrt-cu12` 10.x must be in `requirements.txt` ([[project_live_feed_batched]] —
  TRT 11 breaks ultralytics' `EXPLICIT_BATCH`).
- **RTSP reachability:** the container must route to the RTSP cameras' LAN (`10.0.0.78:554`, TCP).
  → **`network_mode: host`** is the simplest (also reaches the Dahua VMS on the host); bridge +
  routing is the alternative.
- **OCR stack** (`paddleocr`+`paddlepaddle`+`transformers`) and **`lap`** are explicit pins in
  `requirements.txt` (NOT auto-installed) — keep them.
- **torch/torchvision `+cu128`** come from the `--extra-index-url` line; PyPI would be CPU-only.
- **opencv** needs `libgl1 libglib2.0-0` (+ `libgomp1`) or `import cv2` fails at runtime.
- **Dataset disk:** Sprint H writes frames+crops to `DATASET_ROOT`; mount it as a volume on a big
  disk. `DATASET_COLLECT_ENABLED=false` to turn it off per deployment.
- **VRAM:** one shared model set (~2.4 GB) + per-camera tracker/state (tiny). Adding cameras adds
  little VRAM but more GPU compute per tick — watch fps as cameras scale, not VRAM.

---

## 2. `Dockerfile` (vehicle_services/Dockerfile)
```dockerfile
# CUDA 12.8 + cuDNN runtime — matches torch/torchvision cu128 + tensorrt-cu12 10.x.
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/app \
    HF_HOME=/app/.cache/huggingface \
    PADDLE_PDX_CACHE_HOME=/app/.paddlex

# Python 3.11 (match the dev venv) + OpenCV runtime libs + curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
      software-properties-common curl ca-certificates && \
    add-apt-repository ppa:deadsnakes/ppa && apt-get update && \
    apt-get install -y --no-install-recommends \
      python3.11 python3.11-dev python3.11-distutils \
      libgl1 libglib2.0-0 libgomp1 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (cached layer). torch/torchvision/tensorrt pull from the cu128 index.
# NOTE: tensorrt-cu12 (~4 GB of libs) is what lets the .engine build at startup.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Bake PaddleOCR PP-OCRv6 into the image (downloads+caches at build; CPU is fine here).
RUN python -c "from paddleocr import TextRecognition; TextRecognition(model_name='PP-OCRv6_medium_rec', engine='transformers')" \
    || echo "WARN: PaddleOCR prefetch failed at build; downloads on first run"

# App code + baked .pt weights (.dockerignore strips training/datasets/frontend).
# Weights: common/models/vehicle_yolo26.pt,
#          plate_recognition/models/{best_matricule,tounes_word_detector}.pt
# The vehicle .engine is NOT baked — it builds at startup onto the target GPU.
COPY common/ common/
COPY plate_recognition/ plate_recognition/
COPY live_feed/ live_feed/

# Internal port (compose maps host:container, or host networking uses it directly).
ENV LF_PORT=8000
EXPOSE 8000
# Long start-period: first boot may spend minutes building the TRT engine.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD curl -fsS http://localhost:${LF_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn live_feed.app:app --host 0.0.0.0 --port ${LF_PORT}"]
```
Notes:
- **Build needs network** (PyPI + PyTorch index + PaddleOCR download).
- Single stage is fine (no compiled artifacts to discard). Image is large (torch-cu128 + tensorrt
  + paddle + transformers, multi-GB) — acceptable; trim later if needed.

---

## 3. `.dockerignore` (vehicle_services/.dockerignore)
Keep the runtime closure + the three `.pt` weights; drop everything else (and **the new
`datasets/` tree** + any built `.engine` — those are volumes, never image layers).
```
# vcs / docs / sprints
.git
**/*.md
sprints/

# other services / training (NOT runtime)
vehicle_classification/
live_inference/
training/
vehicle_class1/

# plate_recognition: keep only runtime modules + models/{best_matricule,tounes_word_detector}.pt
plate_recognition/cls_dataset/
plate_recognition/synthetic_data/
plate_recognition/tounes_word_dataset/
plate_recognition/runs/
plate_recognition/runs_cls/
plate_recognition/runs_word/
plate_recognition/civilian_crops/
plate_recognition/test_outputs/
plate_recognition/Tunisian license plate/
plate_recognition/tunisian_plate/
plate_recognition/synthetic_generator.py
plate_recognition/cluster_classify.py
plate_recognition/civilian_oneclass.py
plate_recognition/word_classify.py
plate_recognition/extract_plate_crops.py
plate_recognition/train_classifier.py
plate_recognition/train_word_detector.py
plate_recognition/test_plate_detection.py
plate_recognition/*.jpg
plate_recognition/yolo26n.pt
plate_recognition/yolo26n-cls.pt
plate_recognition/models/plate_category_cls.pt

# frontend (separate; team owns the real UI)
live_feed/frontend/

# built TRT engine — GPU-specific, built at startup into a volume (never bake)
**/*.engine

# Sprint H training datasets + runtime state (provided via volumes, not baked)
datasets/
live_feed/logs/
live_feed/crops/
live_feed/review_agent/state/
live_feed/review_agent/out/
live_feed/review_agent/dataset/

# python / common results
**/__pycache__/
**/*.pyc
common/results/
.venv*/
```
> After build, verify the image still has `common/models/vehicle_yolo26.pt` and
> `plate_recognition/models/{best_matricule,tounes_word_detector}.pt`.

---

## 4. Auto-start on boot (small `app.py` change)
The pipeline starts only on `POST /start`. For a self-contained container, add an **`AUTO_START`**
path so it begins on boot (the existing `start_live_feed.sh` does this with a `curl` after a sleep;
the container should not depend on an external curl).
- In `app.py` `_lifespan`, after the event loop is captured: if `os.environ.get("AUTO_START") == "1"`,
  schedule the **same logic as `start()`** in a background task/thread (guarded by `_started`).
- This starts **all** cameras (driver + per-cam encoder + per-cam review workers) — F3 already
  starts everything from one `/start`. No per-camera entrypoint needed.

---

## 5. `docker-compose.yml` (vehicle_services/docker-compose.yml)
**One service**, all cameras. Host networking (reaches Dahua VMS on the host *and* the RTSP LAN):
```yaml
services:
  live-feed:
    build: .
    image: vehicle-livefeed:latest
    env_file: .env
    network_mode: host          # reach VMS (host) + RTSP cameras (LAN 10.0.0.78:554)
    environment:
      - AUTO_START=1
      - LF_PORT=5034            # host networking → bind the real host port
      # engine + dataset live on volumes (see below); point the code at them:
      - VEHICLE_ENGINE_PATH=/app/engine_cache/vehicle_yolo26.engine
      - DATASET_ROOT=/app/datasets
    volumes:
      - ./live_feed/rois:/app/live_feed/rois               # calibration (bind; persists + seeds ch*.json)
      - lf_engine:/app/engine_cache                        # built TRT engine — persists across restarts
      - lf_datasets:/app/datasets                          # Sprint H training data (GROWS — big disk)
      - lf_logs:/app/live_feed/logs
      - lf_crops:/app/live_feed/crops                      # review-agent crops
      - lf_review_state:/app/live_feed/review_agent/state
      - lf_review_out:/app/live_feed/review_agent/out
      - lf_review_dataset:/app/live_feed/review_agent/dataset
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  lf_engine: {}
  lf_datasets: {}
  lf_logs: {}
  lf_crops: {}
  lf_review_state: {}
  lf_review_out: {}
  lf_review_dataset: {}
```
- **GPU:** the `deploy.resources…devices` block = `--gpus all`. One process → no MPS needed.
- **Networking:** `network_mode: host` is simplest for the mixed Dahua-on-host + RTSP-on-LAN setup
  (and on host net, `ports:` mapping doesn't apply — bind the real port via `LF_PORT`). If you must
  use a bridge: drop `network_mode`, add `ports: ["5034:8000"]`, `extra_hosts: ["host.docker.internal:host-gateway"]`,
  set `VMS_URL=http://host.docker.internal:5057`, and ensure the bridge can route to the RTSP LAN.
- **Engine volume (`lf_engine`)**: first boot builds `vehicle_yolo26.engine` there (minutes);
  every later boot reuses it instantly. **Rebuild it if you change GPU model** (engines are
  GPU-specific) — `docker volume rm vehicle_services_lf_engine`.
- **`rois` bind mount** seeds the repo's `ch*.json` and persists new calibrations.

---

## 6. `.env.example` (copy to `.env`, gitignored)
```
# ── Cameras ──
CHANNELS=4 1
RTSP_CAMERAS=hik1=rtsp://admin:__pw__@10.0.0.78:554/Streaming/Channels/101

# ── Dahua VMS (frames) ──
VMS_URL=http://host.docker.internal:5057   # host networking: http://<windows-host-ip>:5057
VMS_DEVICE_IP=192.168.40.200
VMS_DEVICE_PORT=37777
VMS_USERNAME=admin
VMS_PASSWORD=__set_me__
VMS_CHANNEL_OFFSET=-1

# ── OpenRouter (review agent) — agent self-disables if key absent ──
OPENROUTER_API_KEY=__set_me__
YOUR_SITE_URL=http://localhost
YOUR_SITE_NAME=vehicle_services

# ── Sprint H training-data collector ──
DATASET_COLLECT_ENABLED=true
DATASET_INTERVAL_SEC=5.0
DATASET_JPEG_QUALITY=85

# ── optional tunables ──
STREAM_TYPE=0
DISPLAY_FPS=15
SEND_LOWCONF_ONLY=0
```
Add `.env` to `.gitignore`.

---

## 7. Build & run
```
cd vehicle_services
cp .env.example .env            # fill VMS_PASSWORD, RTSP pw, OPENROUTER_API_KEY
docker compose build            # heavy first build (torch cu128 + tensorrt + paddle + transformers)
docker compose up -d live-feed
docker compose logs -f live-feed   # first boot builds the TRT engine (minutes) then starts all cameras
```

## 8. Verification / acceptance
- [ ] Image builds; `docker run --rm --gpus all vehicle-livefeed python -c "import torch;print(torch.cuda.is_available())"` → **True**.
- [ ] `import cv2`, `from paddleocr import TextRecognition`, `import ultralytics, lap, tensorrt` all succeed in the container.
- [ ] Image contains the three `.pt` weights; **no** `.engine` baked.
- [ ] First boot **builds** `engine_cache/vehicle_yolo26.engine`; restart **reuses** it (logs show no re-export; boot is fast).
- [ ] `AUTO_START=1` → pipeline starts all cameras on boot; `GET /health` lists every cam with fps.
- [ ] `GET /metrics?cam=4` and `?cam=hik1` show real fps; `GET /stream.mjpg?cam=hik1` shows the annotated RTSP feed; tracking + line counting work.
- [ ] Review agent: smoke passes (key set) or disables gracefully; `corrected_events` grow; the `hik1` worker runs (string cam_id, no `int()` crash).
- [ ] **Sprint H**: `datasets/{vehicle_detect,plate_detect,plate_type_cls}/` populate in the `lf_datasets` volume; fps unaffected vs collector off.
- [ ] PaddleOCR does **not** download at first run (baked) — confirm in logs.
- [ ] Calibration persists: `POST /rois?cam=4`, restart, config still present (bind mount).
- [ ] Logs/crops/review/datasets survive `docker compose down && up` (named volumes).

## 9. Notes / follow-ups
- **VRAM is now flat** (shared model) — the old "cameras × footprint ≤ VRAM" sizing no longer
  applies; scale is bounded by **GPU compute / fps per tick**, not VRAM. Beyond that ceiling →
  more replicas behind a load split or a shared inference server, not more cameras per process.
- **Engine portability:** the `.engine` is tied to the exact GPU/driver/TRT — rebuild (drop the
  `lf_engine` volume) on hardware change. Document the GPU the image is deployed on.
- **`requirements.txt` header comment** still says "per-camera container image" — stale wording;
  the pins are correct (tensorrt-cu12, cu128, OCR stack, lap, langchain). Fix the comment.
- **`app.py` port for RTSP-only setups:** `port = LF_PORT_BASE + CAMERAS[0]["channel"]` assumes the
  first camera is Dahua (has `channel`). With host networking we set `LF_PORT` explicitly, so this
  only affects the bare `python app.py` dev path — fine, but note it if `CHANNELS` is ever empty.
- **Image size:** multi-GB (tensorrt adds ~4 GB). Acceptable; consider trimming transformers / a
  CPU-less paddle later.
- **MPS:** not needed (single batched process). Only relevant if we ever run multiple replicas.
