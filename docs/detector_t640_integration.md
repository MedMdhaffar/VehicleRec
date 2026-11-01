# t-640 plate detector integration audit

Date: 2026-10-30. Changes remain uncommitted; no push or deployment.

## Scope and inspected evidence

Read root `AGENTS.md`, actual Django and standalone video/RTSP source, setup and
launch scripts, shared/backend/plugin tests, classifier integration audits,
`license_plate_local_completion.md`, `whole_plate_number_policies.md`, and the
previous detector diagnostic. Existing unrelated working-tree edits and earlier
reports were preserved. Historical experiment/gallery detectors were not changed;
they are not active service entry points.

Only the later evidence file
`verification/plate_detector_diagnostic_20261030T040811_752788Z/plate_ground_truth_20261030/predictions_t640_20261030_163702_031951.json`
was used as t-640 comparison evidence. Its `model` field explicitly identifies
`yolo-v9-t-640-license-plate-end2end`; earlier t-256 predictions were excluded.
Nine copied original vehicle images and main-vehicle manual draft annotations
are available in that folder. Those annotations remain user-review pending,
cover the main plate only, and were used only for visual comparison—not inference.
These are reused development examples, not an independent accuracy evaluation.

## Confirmed active construction paths

| Entry point | Actual route and detector owner |
|---|---|
| Django upload | `license_plate_service.views.analyze` → `service.run_video_analysis` → `run_video_pipeline.plate_detector.PlateDetector(config.PLATE_DETECTOR_PATH)` → `VideoPrototypeRunner` |
| Django RTSP | `service.RTSPManager` sets the Django app import root → `run_rtsp_pipeline.run_rtsp_prototype.run_rtsp` → RTSP-local `PlateDetector` → RTSP-local video runner |
| Standalone video | launcher selects `services/license_plate_service` → `python -m run_video_pipeline.run_video_prototype` → video-local `PlateDetector` |
| Standalone RTSP | same standalone root → `python -m run_rtsp_pipeline.run_rtsp_prototype` → RTSP-local `PlateDetector` and runner |
| Direct/helper paths | `pipeline.analyze_image` calls the new `detect_and_crop`; RTSP-local pipeline imports the video detector; the additional RTSP-local video CLI also imports the replaced video detector |

All four `plate_detector.py` adapters and all four configuration copies were
updated. Each root's two adapters share its `run_video_pipeline.onnx_detector`
backend. Both roots' implementations match. Django upload was exercised by a
real multipart request, and standalone video by its real CLI. RTSP construction,
provider failure cleanup, reconnect ownership and imports are controlled tests;
**no real RTSP camera test was available**.

## Adapter and coordinate contract

The existing contract remains `(x1, y1, x2, y2, confidence)` in original-frame
coordinates. Inputs must be nonempty original BGR `uint8` H×W×3 arrays.
The supported `open-image-models==0.6.0` local-file factory is used:

```python
create_detector(Path(local_onnx), backend="yolo_v9",
                class_labels=("License Plate",), conf_thresh=0.001,
                batch_size=1, providers=providers, sess_options=options)
```

Passing a local `Path` bypasses the factory's registered-name branch, which
otherwise calls `download_model()`. Missing files or a SHA-256 mismatch fail
before the factory is called. The exact pinned bytes and `[1,3,640,640]` input
shape exclude accidental t-256 substitution.

Inspected library source letterboxes with OpenCV linear interpolation, pad 114,
converts BGR→RGB and HWC→CHW, divides by 255 and produces float32. Its postprocessor
subtracts padding, divides by the letterbox ratio, and returns integer original
coordinates. The adapter does **not** resize or scale those boxes again. It clips
coordinates safely, rejects invalid/empty boxes and invalid scores, preserves
confidence, and applies the configured confidence filter. Existing application
duplicate filtering and crop validation remain in their existing callers.
Crops are copied from original frame pixels with zero default crop padding.
ByteTrack's float32 Nx4 xyxy/N confidence/zero class arrays and association
padding remain unchanged; actual library inverse mapping and tracker contracts
are covered by tests.

Each owner loads once under its existing lock; repeated calls and reconnect
runner initialization reuse the owner's session. There is no per-frame model
construction. Library 0.6.0 normally catches native inference exceptions and
returns empty results. A small session proxy records those exceptions so the
adapter raises an explicit inference error instead of misreporting no detections.
A real blank frame and mocked empty results verify the genuine no-detection path.
Native tensor capture is opt-in for audits; API diagnostics contain no tensors.

## Model provenance, dependencies and providers

The exact model manifest is [plate_detector_t640_model.json](plate_detector_t640_model.json).

- Identifier: `yolo-v9-t-640-license-plate-end2end`.
- Actual filename: `yolo-v9-t-640-license-plates-end2end.onnx`.
- SHA-256: `c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076`.
- Existing stable local copy reused at
  `/home/intern/.cache/open-image-models/yolo-v9-t-640-license-plate-end2end/yolo-v9-t-640-license-plates-end2end.onnx`.
  No duplicate weights were created and no model download was needed here.
- Source: [official release asset](https://github.com/ankandrew/open-image-models/releases/download/assets/yolo-v9-t-640-license-plates-end2end.onnx),
  release tag [`assets`](https://github.com/ankandrew/open-image-models/releases/tag/assets).
  The release page identifies commit `a9c244f`; asset bytes are pinned by SHA-256,
  not by assuming the release URL is immutable. ONNX metadata contains no separate revision.
- ONNX opset 17, producer PyTorch 2.3.1, float32 input `[1,3,640,640]`.
- The graph embeds `NonMaxSuppression`: score floor **0.001**, IoU **0.45**,
  maximum **100 boxes per class**. These are fixed graph settings; they were not edited.
- Installed/tested: open-image-models 0.6.0; ONNX Runtime GPU distribution 1.25.1
  (executed with CPU provider); OpenCV 4.13.0.92; NumPy 2.3.5; Torch 2.11.0+cu128;
  PaddleOCR 3.7.0; PaddlePaddle 3.3.1. Full versions are saved with verification.
- Package code is [MIT licensed](https://github.com/ankandrew/open-image-models/blob/main/LICENSE);
  ONNX Runtime is MIT. Upstream YOLOv9 code is
  [GPL-3.0](https://github.com/WongKinYiu/yolov9/blob/main/LICENSE.md).
  No separate checkpoint license/model card accompanied the cached ONNX or its
  metadata; package MIT is not presented as independent proof of checkpoint or
  training-data licensing.

The dedicated runtime was missing open-image-models; 0.6.0 was installed into
`.runtime/plates/venv` using a cached distribution. No other project environment
was modified. Setup installs the small detector requirements with `--no-deps`
after the CV stack to avoid adding `opencv-python-headless` alongside the
existing OpenCV provider. Keep one ONNX Runtime distribution, not simultaneous
CPU and GPU wheels. The existing GPU distribution also supplies CPU execution.

## Configuration and obsolete options

| Setting | Meaning / initial value |
|---|---|
| `PLATE_DETECTOR_PATH` | Local exact-SHA ONNX copy; defaults to the shared cache path above |
| `PLATE_CONF` / `--conf` | 0.25 initial compatibility setting; also library registered default, **not calibrated from the nine scores**. Values below embedded 0.001 or above 1 fail |
| `DETECT_IMGSZ` / `--imgsz` | Only 640 is supported; obsolete 960/arbitrary sizes fail explicitly |
| `PLATE_DEDUP_IOU` | Existing application duplicate-removal threshold 0.8, after graph NMS |
| `PLATE_DETECTOR_PROVIDERS` | Ordered comma-separated ORT providers; default `CPUExecutionProvider` |
| `PLATE_DETECTOR_ALLOW_CPU_FALLBACK` | Default 0; only explicit 1 permits unavailable-provider/session fallback to CPU |
| `PLATE_DETECTOR_INTRA_OP_THREADS` | Default 2 |
| `PLATE_DETECTOR_INTER_OP_THREADS` | Default 1 |

Ultralytics augmentation/imgsz/NMS arguments are not forwarded to the ONNX API.
Library output filtering is fixed at 0.001 so it cannot silently remove candidates
above a caller's supported application threshold. Effective detection is still
limited by the graph's internal NMS and floor; raw saved outputs are graph-selected
outputs, **not all internal/pre-NMS proposals**.

Diagnostics record requested, configured, available and actual session providers,
warnings, thread counts, precision, model identity and initialization time.
`get_available_providers()` listed CUDA/TensorRT/CPU, but explicit CUDA preflight
failed because `libcudnn.so.9` was unavailable. The adapter rejected the resulting
CPU session, as required. An explicit fallback-enabled preflight passed and
reported CPU plus warnings. Normal verification requested and used only
`CPUExecutionProvider`. Neither a Conda environment name nor provider availability
is presented as proof of usable GPU execution. GPU performance is unverified.
ORT's implicit run-time provider retry is disabled unless fallback is enabled;
per-node CPU placement in an otherwise valid GPU session is a separate ORT concern.

## Real nine-image results

A detector-only CPU preflight also passed in the existing `cuda-dev` environment
with ONNX Runtime 1.31.1 and actual `CPUExecutionProvider`; full suites and
performance measurements below use the dedicated persistent runtime.

Actual integrated adapter initialization/inference and downstream recognition ran
under Python `socket.socket.connect` and `socket.create_connection` denial, with
zero attempts. This guards Python connection calls, **not OS-wide networking**.
Factory loading used the local file; no external recognition fallback exists.

[Full-resolution annotations, original-pixel crops, JSON/CSV and contact sheet](../verification/detector_t640_integration_20261030T154403_661761Z/nine_images/)
are retained. Green boxes are model detections; blue boxes are main-only manual
draft annotations. Every integrated box and confidence exactly matches the
explicitly named saved t-640 predictions (zero coordinate/score deltas), including
image 05's second/background plate. No requirement of one detection per image was imposed.

Visual inspection of the contact sheet and native crops indicates all nine main
plate numbers and category markings remain readable. This is not verified
complete-border accuracy: several crops are tight. Image 04 ends eight pixels
above the pending draft box's bottom; images 03/05 also lie slightly inside that
annotation. All boundary deltas are saved separately. No padding or threshold
change was added to make these examples pass.

| Image / box | Detection score | Predicted category | Raw registration OCR | Formatted result | Status / reason |
|---|---|---|---|---|---|
| 01 / 0 | 0.848518 | shorta | 14330 | shorta 14330 | recognized |
| 02 / 0 | 0.833192 | shorta | 27211 | shorta 27211 | recognized |
| 03 / 0 | 0.882774 | tunis | 105395 | 105 تونس 395 | recognized |
| 04 / 0 | 0.897241 | tunis | 1509603 | 150 تونس 9603 | recognized |
| 05 / 0 | 0.906864 | tunis | 122937 | 122 تونس 937 | recognized |
| 05 / 1 | 0.672458 | tunis | 1809618 |  | unresolved / insufficient_marker_evidence |
| 06 / 0 | 0.876089 | idara | 17 - 138957 | 17 - 138957 | recognized |
| 07 / 0 | 0.872292 | idara | 17 - 148885 | 17 - 148885 | recognized |
| 08 / 0 | 0.868056 | nt | 214172 | 214172 nt | recognized |
| 09 / 0 | 0.842973 | idara | 17 - 138956 | 17 - 138956 | recognized |

All nine main crops produced accepted formatted numbers under existing policies.
The background crop is unresolved (`insufficient_marker_evidence`), with no
background ground truth or accuracy claim. No classifier, OCR, number parser,
acceptance threshold, marker model, track scoring, trigger, queue, or result
replacement rule was changed to obtain these results.

## Tests, real service checks and performance

- **Mocked/shared pipeline suites:** 50 tests pass separately against each root.
  New coverage includes local exact-model loading, load-once reuse, native BGR
  preservation, actual library coordinate conversion, confidence, multiple/empty
  detections, clipping/invalid boxes, duplicates, exact pixel crops, ByteTrack
  shape/dtype, missing/wrong assets, obsolete sizes, GPU rejection/explicit fallback,
  swallowed inference errors, all runner bindings and worker/camera cleanup.
- **Backend:** 20 tests pass across LP, colour, manufacturer and API apps. API
  tests verify new detector wiring and explicit startup failures; expensive
  inference is mocked in those unit tests. Django system checks pass.
- **Plugin/import suite:** 23 discovered, 21 pass, 2 inference opt-ins skipped.
  Fresh-process Django/standalone imports pass. Real crop inference above was run
  separately and is not one of those mocked/import checks.
- **Real standalone video:** bounded 60-frame CLI completes, writes annotated
  video/metrics/crops, and returns two accepted tracks. The actual outputs are
  `179 تونس 8065` and `187 تونس 7438`; this video has no independently verified
  number ground truth. Earlier observations must not be treated as ground truth
  to label either digit sequence correct. No number policy was adjusted.
- **Real Django multipart upload:** HTTP 200 for 30 frames, recognized
  `179 تونس 8065`, annotated output converted to browser H.264, correct t-640/CPU
  metadata in the response. Zero guarded Python socket connection attempts.
- **Frontend:** ESLint and production build pass using existing local Node 22.16.0.
  No browser tool, Playwright installation or Chromium executable was available;
  **a real browser upload smoke test was not performed**. HTTP multipart inference
  is not claimed as a browser test. No frontend source changed.
- **RTSP:** no camera available. Construction/import/provider-error cleanup tests
  pass; a real RTSP stream/reconnect test remains unverified.
- **Structural/integrity:** detector/config copies agree; AST checks preserve
  tracker arrays, crop scoring, track state, recognition submission/processing,
  worker ownership and recheck methods. Hash checks preserve marker sources and
  weights, number policies, registration readers and unrelated vehicle services.
  No LP Ultralytics loading remains in active plate adapters; Ultralytics remains
  for the untouched marker and ByteTrack/unrelated services.

Performance is CPU float32, ORT intra/inter-op 2/1, OMP/OpenBLAS/MKL threads 2:

| Measurement | Observed |
|---|---|
| Detector-only initialization | 0.144 s, including local session construction |
| Warm detector benchmark | 45 calls after nine warm-ups; mean 60.99 ms, median 60.88 ms, p95 62.77 ms |
| Detector-only process peak RSS | 252,841,984 bytes |
| Isolated 60-frame complete video wrapper | 32.427 s including initialization, inference, exports; approximately 1.85 source frames/s end-to-end |
| Video worker model initialization | 6.453 s (includes classification, OCR, marker and detector) |
| Video analysis throughput | 2.614 FPS; frame loop 3.401 FPS (different boundaries from wrapper) |
| Detector timing inside video after first frame | 59 calls: mean 242.306 ms, median 123.390 ms, p95 609.113 ms |
| Video analysis + annotation | 24.345 s; recognition drain 5.314 s |
| Recognition queue | Capacity 4, peak pending 1, zero deferred attempts, two recognition attempts, mean eligibility wait 0.095 ms |
| Complete video process peak RSS | 1,836,077,056 bytes |

The isolated detector measurement excludes decode/load/warm-up/drawing; ORT `run`
returns CPU arrays synchronously. Integrated video timings include CPU contention
with recognition. Earlier standalone and upload smoke runs overlapped and are
retained as smoke evidence, not the final isolated throughput benchmark. No
controlled old/new throughput comparison was run, so no speedup or real-time
claim is made. The nine examples establish neither production precision/recall,
mAP nor calibrated error rates.

## Exact setup, checks and launch commands

From `/home/intern/part-time/mohamed/pipeline`:

```bash
# Existing dedicated runtime: small adapter dependency update, then verify local bytes.
.runtime/plates/venv/bin/python -m pip install --no-deps -r services/license_plate_service/requirements-detector.txt
.runtime/plates/venv/bin/python -B scripts/provision_plate_detector.py
# If the asset is genuinely missing, explicit setup may download the pinned file:
.runtime/plates/venv/bin/python -B scripts/provision_plate_detector.py --download
# Full fresh runtime setup is also available:
# bash scripts/setup_local_plate_runtime.sh

export REGISTRATION_OCR_MODEL_DIR=/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec
export PLATE_DETECTOR_PROVIDERS=CPUExecutionProvider
export PLATE_DETECTOR_ALLOW_CPU_FALLBACK=0
export PLATE_CONF=0.25 DETECT_IMGSZ=640 PLATE_DEDUP_IOU=0.8
export PLATE_DETECTOR_INTRA_OP_THREADS=2 PLATE_DETECTOR_INTER_OP_THREADS=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export CUDA_VISIBLE_DEVICES='' PYTHONDONTWRITEBYTECODE=1
# Unset any old .pt override; the default points to the existing t-640 cache.
unset PLATE_DETECTOR_PATH
bash scripts/run_local_plate.sh preflight

.runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
PLATE_PIPELINE_FLAVOR=django .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
bash scripts/run_local_plate.sh django test license_plate_service vehicle_color_service vehicle_classification_manufacturer_service api --noinput
PLATE_PLUGIN_IMPORT_AUDIT=1 .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/LicensePlatePlugin/tests -v

# Use fresh output directories; do not overwrite earlier verification.
.runtime/plates/venv/bin/python -B scripts/verify_t640_integration.py --output verification/t640_nine_rerun
.runtime/plates/venv/bin/python -B scripts/verify_t640_integration.py --flavor standalone --output verification/t640_nine_standalone_rerun
.runtime/plates/venv/bin/python -B scripts/benchmark_plate_detector.py --output verification/t640_benchmark_rerun.json
bash scripts/run_local_plate.sh verify --mode standalone --frames 60 --output verification/t640_video_rerun
bash scripts/run_local_plate.sh verify --mode django --frames 30 --output verification/t640_upload_rerun

# Backend, terminal 1:
bash scripts/run_local_plate.sh django runserver 8002 --noreload
```

Frontend, separate terminal from repository root:

```bash
export PATH="$PWD/.runtime/tooling/node-v22.16.0-linux-x64/bin:$PATH"
npm --prefix frontend run lint
npm --prefix frontend run build
VITE_API_URL=http://127.0.0.1:8002 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Open `http://127.0.0.1:5174`, upload a video and initially set frame limit 30.
The existing CORS configuration allows port 5174. Model readiness is checked by
`preflight`; the lightweight health endpoint alone does not load models.

Standalone video and RTSP (same model/recognition environment as backend):

```bash
bash scripts/run_local_plate.sh video /absolute/video.mp4 --max-frames 60
bash scripts/run_local_plate.sh rtsp --url 'rtsp://camera-host/stream' --max-frames 60
```

GPU request, only after installing a compatible CUDA/cuDNN ONNX Runtime setup:

```bash
unset CUDA_VISIBLE_DEVICES
export PLATE_DETECTOR_PROVIDERS=CUDAExecutionProvider
export PLATE_DETECTOR_ALLOW_CPU_FALLBACK=0
bash scripts/run_local_plate.sh preflight
# Explicit opt-in if CPU fallback is acceptable:
PLATE_DETECTOR_ALLOW_CPU_FALLBACK=1 bash scripts/run_local_plate.sh preflight
```

Use the printed actual session provider list. The current tested environment
fails the strict GPU preflight; CPU commands above are the verified working path.

## Rollback and deliverables

The old `best.pt` files remain in place with unchanged hashes. Their active
Ultralytics adapters and 960 default can be restored with the task-specific
source patch, preserving earlier working-tree edits and generated audit evidence:

```bash
# Review/check first; do not use git reset against this mixed working tree.
git apply --reverse --check docs/detector_t640_source.patch
git apply --reverse docs/detector_t640_source.patch
unset PLATE_DETECTOR_PATH DETECT_IMGSZ
```

Reverse application requires no intervening conflicting source edits. The
retained ONNX cache and installed small dependency may remain; they are inactive
after rollback. Audit/generated files are excluded from the source rollback patch.

- [Complete task-specific patch](detector_t640_integration.patch): all task edits
  plus source additions, documentation, logs and machine-readable evidence.
  Large generated PNG/JPEG/MP4 artifacts remain available in the linked verification
  folder rather than being embedded in the patch; patch files exclude themselves.
- [Source-only rollback patch](detector_t640_source.patch).
- [Full-path file inventory with purposes](detector_t640_inventory.md).
- [Verification artifacts](../verification/detector_t640_integration_20261030T154403_661761Z/):
  nine-image JSON/CSV/annotations/crops/contact sheet, prior-prediction deltas,
  draft boundary comparisons, benchmark, standalone/upload outputs, provider
  failure/fallback logs, test logs, and protected-source hash/AST checks.

Earlier reports and the t-640 evidence JSON were not modified. The Tunis marker
checkpoint, marker processing, category classifier, OCR policies, tracking
safeguards, and vehicle-colour/manufacturer services were preserved. No external
recognition route was restored. No commit, push or deployment was performed.
