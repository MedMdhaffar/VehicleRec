# License-plate classifier runtime integration audit

Date: 2026-10-29. Changes remain in the working tree; nothing was committed or deployed.

## 1. File inventory and diff

Repository instructions, actual construction/recognition code, the plugin implementation, and `docs/license_plate_classifier_steps_1_2.md` were read before editing. No additional instructions apply in the edited directories. Existing unrelated changes were preserved. Initial contents were saved under `/tmp/plate-stage3-before` before edits so the diff excludes earlier work. In particular the imported plugin and earlier reports were already untracked, and `service.py`, tests and requirements already contained user changes.

| Status | Full path | Purpose |
|---|---|---|
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py` | Call classification before registration OCR; propagate evidence and return unresolved local results on classification errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/schemas.py` | Add typed internal category evidence and a separate classification timing field without serializing category evidence into public results. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Accept an explicit factory; own and serialize classifier lifecycle, propagate evidence, and suppress registration/agent routing on failed classification. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/data_capture.py` | Persist the compact typed category evidence in prediction_debug.jsonl; leave compact predictions unchanged. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py` | Initialize/release the classifier on the single recognition worker; clean up workers on startup and processing failures; inject the factory in the CLI. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Accept an explicit factory; own and serialize classifier lifecycle, propagate evidence, and suppress registration/agent routing on failed classification. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/data_capture.py` | Persist the compact typed category evidence in prediction_debug.jsonl; leave compact predictions unchanged. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_video_prototype.py` | Initialize/release the classifier on the single recognition worker; clean up workers on startup and processing failures; inject the factory in the CLI. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` | Inject the local classifier factory into the RTSP-local pipeline; existing startup/finally cleanup covers model errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py` | Call classification before registration OCR; propagate evidence and return unresolved local results on classification errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/schemas.py` | Add typed internal category evidence and a separate classification timing field without serializing category evidence into public results. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Accept an explicit factory; own and serialize classifier lifecycle, propagate evidence, and suppress registration/agent routing on failed classification. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/data_capture.py` | Persist the compact typed category evidence in prediction_debug.jsonl; leave compact predictions unchanged. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/run_video_prototype.py` | Initialize/release the classifier on the single recognition worker; clean up workers on startup and processing failures; inject the factory in the CLI. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Accept an explicit factory; own and serialize classifier lifecycle, propagate evidence, and suppress registration/agent routing on failed classification. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/data_capture.py` | Persist the compact typed category evidence in prediction_debug.jsonl; leave compact predictions unchanged. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_video_prototype.py` | Initialize/release the classifier on the single recognition worker; clean up workers on startup and processing failures; inject the factory in the CLI. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` | Inject the local classifier factory into the RTSP-local pipeline; existing startup/finally cleanup covers model errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py` | Inject the classifier factory into Django video; catch RTSP import/startup failures in session status. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/tests.py` | Test Django video factory injection and RTSP startup error status. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/requirements.txt` | Include the shared supplemental DINOv3 dependency file. |
| Modified | `/home/intern/part-time/mohamed/pipeline/README.md` | Document installation, shared runtime entry points, lifecycle, caches, persistence and the remaining agent route. |
| Added | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/classification.py` | Local-only classifier factory and projection of plugin results into typed evidence, including explicit classification errors. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/classification.py` | Local-only classifier factory and projection of plugin results into typed evidence, including explicit classification errors. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/requirements-classifier.txt` | Pin the official DINOv3 hub dependency closure and compatible editable build backend. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/tests/test_classifier_runtime.py` | Shared focused integration tests, run separately against Django and standalone roots. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_classifier_runtime.md` | This audit, including environments, paths, checks and measured latency. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_classifier_runtime.patch` | Complete task-specific unified diff, including additions; excludes itself to avoid recursion. |

The complete reviewable patch is [license_plate_classifier_runtime.patch](license_plate_classifier_runtime.patch). It compares this task’s edits against the captured starting state, includes new files as additions from `/dev/null`, and includes this report. Only the patch itself is omitted to avoid recursive content. Prior step-1/2 artifacts are unchanged. Model assets, vendored model source, plugin preprocessing/decisions/gallery, detection/tracking/scoring/triggers and replacement logic were not edited.
## 2. Active paths verified against code

The bindings described in the previous report are confirmed by fresh-process import checks and exercised by the integration tests.

### Django video

1. [views.py:20](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/views.py:20) saves an upload and calls `run_video_analysis`.
2. [service.py:16](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:16) exposes the absolute Django application directory for the legacy top-level pipeline imports. [service.py:34](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:34) imports the new video factory; line 58 injects it into `PrototypePlatePipeline`.
3. [run_video_prototype.py:477](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:477) constructs the runner. Line 526 submits initialization to its single recognition executor and waits before processing frames.
4. [run_video_prototype.py:586](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:586) submits selected crops; `_recognize` at line 537 calls the injected pipeline’s `process_crop`.
5. [prototype_pipeline.py:82](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py:82) calls `run_video_pipeline.pipeline.analyze_plate_crop` with the owned classifier.
6. [pipeline.py:135](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py:135) classifies `detection.crop`, before the unchanged registration call at line 147.

### Django RTSP

[service.py:131](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:131) runs the camera worker and now imports `run_rtsp` inside the guarded error-status block. [run_rtsp_prototype.py:18](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:18) imports the shared video classifier factory, while keeping RTSP-local reader/detector/store/prototype/runner imports. Its construction at line 469 injects the factory at line 474.

The RTSP-local runner uses its **injected RTSP-local prototype instance**, not the video prototype imported elsewhere in the runner module. [run_rtsp_pipeline/prototype_pipeline.py:11](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py:11) imports the **video** `analyze_plate_crop`; its call at line 82 therefore reaches the new hook. RTSP-local `pipeline.py` remains inactive and unchanged. RTSP-local `ocr.py` provides the registration reader but imports video config. RTSP reconnects finish the previous runner before constructing the next one.

### Standalone video and RTSP

The corresponding active files under `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/` have the same contents and line locations:

- [Video CLI construction:1014](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/run_video_prototype.py:1014): injects `create_classifier` at line 1019; runner initialization at line 526, selected-crop submission at line 586.
- [Video prototype recognition:82](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/prototype_pipeline.py:82) → [classification hook:135](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py:135) → registration OCR at line 147.
- [RTSP construction:469](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:469) injects the same factory into the RTSP-local prototype, whose [video-function import:11](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py:11) and call at line 82 reach that same hook.
- Duplicate RTSP-local `run_video_prototype.py` executables also receive factory injection and lifecycle handling. Inactive RTSP-local recognition/schema duplicates were left unchanged.

Both entry-point families import the **same installed `license_plate_plugin` checkout**. No plugin-path insertion based on cwd, plugin asset duplication into Django, or shared global classifier was introduced. Legacy video/RTSP package imports still require choosing the appropriate pipeline root per process, as before.

## 3. Classifier ownership, lifecycle and failures

`PrototypePlatePipeline` requires an explicit `classifier_factory`. Each runner creates one `ThreadPoolExecutor(max_workers=1)` and initializes the classifier **on that recognition thread**, waiting for initialization before any frame processing. The factory at [classification.py:9](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/classification.py:9) calls `PlateCategoryClassifier(load_default_config())`. The plugin performs its existing checkpoint hash, architecture, asset and reference-index compatibility checks. No model is loaded per frame or attempt.

The pipeline owns that instance until runner shutdown. A per-pipeline lock serializes initialization, classification plus registration, and release; the executor also serializes recognition tasks. An already-owned pipeline rejects a second initialization. Separate jobs construct separate pipelines and classifier instances. RTSP reconnects drain and release the old worker’s classifier, then load one for the new worker, reusing it across all attempts in that session.

`close()` releases model references on the recognition worker and joins the executor. `finish()` is idempotent and closes in `finally`, so finalization/drain errors do not leave a worker running. Unreadable video and frame-processing errors also close the initialized worker. Initialization exceptions shut down the executor immediately and surface a contextual `Plate category classifier initialization failed: ...` error. For RTSP, the existing `finally` closes the camera and writer; an unconstructed runner is not drained. Django records `running=false`, `status=error` and the reason. RTSP module import errors now reach the same status path.

`analyze_plate_crop` passes the original selected BGR crop to the plugin’s copying `classify_bgr` interface without an output directory. The registration reader subsequently sees the same original array and source-frame bbox. Classification runs only for submitted recognition tasks, including an existing substantially-better-crop recheck; the selection policy and two-attempt maximum are unchanged.

A classifier exception or unusable classifier result becomes typed `classification.status=error` evidence. Registration OCR and marker localization are skipped; the prototype forces the existing `unknown` route with `status=error`, empty registration fields and an explicit warning. It neither calls the agent nor silently accepts the attempt. The result is persisted normally, and subsequent queued tasks continue. Existing cache/recheck replacement rules still decide the final track result, so an unsuccessful recheck cannot replace an accepted prior attempt.

The plugin’s own Arabic OCR failure is different: if its visual category succeeds, the plugin retains that visual decision. The integration records `arabic_ocr_failed=true` but continues registration, matching the plugin’s policy.

## 4. Evidence and API compatibility

`CategoryClassification` at [schemas.py:6](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/schemas.py:6) carries:

- completion/error status and an explicit error string;
- final category, visual category and decision source;
- nearest reference ID, cosine similarity and predicted-category margin;
- NT evidence and conflict/Arabic-OCR-failure indicators;
- total measured classification, visual-feature/match and Arabic-OCR durations in milliseconds.

Both local and unified internal results have a typed `classification` attribute, excluded from ordinary Pydantic serialization. [data_capture.py:106](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/data_capture.py:106) explicitly persists it as a separate `classification` object in `prediction_debug.jsonl`. RTSP-local stores implement the same debug field. Compact `predictions.jsonl`, public track summaries and live result fields retain their existing registration category/text/confidence meanings. No native Paddle payload, image tensor or feature vector is copied into those responses. The existing review export is unchanged.

`PlateTiming.classification_ms` records the new stage separately. Existing local OCR/marker/parser timings retain their registration-only scope; total recognition time naturally includes classification. Cosine similarity is named as similarity, never substituted for `category_confidence`, and is not treated as a calibrated probability. No thresholds or known-category mapping policies were added.

Internal callers constructing `PrototypePlatePipeline` must now supply a factory, and direct `analyze_plate_crop`/`analyze_image` callers must supply an initialized classifier for successful recognition. Missing classification is an explicit unresolved attempt, not an OCR-only compatibility bypass. All repository runtime construction sites were updated.

## 5. Runtime environments and dependency setup

Observed environments:

| Environment | Finding |
|---|---|
| `/home/intern/part-time/mohamed/pipeline/vehicle/.venv/bin/python` | Repository-documented Django Python 3.11.15 environment. Contains Django but initially lacks Torch, Paddle and OpenCV. Not bulk-upgraded in this task. It requires the setup below before inference. |
| `/home/intern/.venv/bin/python` | Existing full CV environment used by prior standalone checks, Python 3.11.15. Initially missing `torchmetrics` and other official hub-import dependencies. Left unchanged. CLI execution uses whichever interpreter is explicitly selected; no repository daemon/launcher fixes one interpreter for standalone execution. |
| `/tmp/plate-plugin-check-env/bin/python` | Editable plugin verification environment, inheriting the CV environment’s site-packages through a temporary `.pth`. Missing dependencies installed here. Used for successful real parity, real standalone video, Django checks and all integration tests. This is the verified runtime; default production environments are not claimed to have been upgraded. |

Added `services/license_plate_service/requirements-classifier.txt` pins the official hub closure: TorchMetrics 1.8.2, Lightning Utilities 0.15.3, OmegaConf 2.3.0, ANTLR runtime 4.9.3, ftfy 6.3.1, submitit 1.5.3, cloudpickle 3.1.2, termcolor 3.2.0, scikit-learn 1.8.0, regex 2026.4.4, Pillow 12.2.0, setuptools 79.0.1. `vehicle/requirements.txt` includes it. Torch/Torchvision, Paddle, NumPy, OpenCV, detector/tracker and Django requirements remain in the existing backend dependency file.

The installation resolving the missing dependencies was made only in `/tmp/plate-plugin-check-env`; it downloaded Python dependencies, never model weights or external-AI data. Local DINOv3 loading initially needed a writable Torch cache. Setting `TORCH_HOME=/tmp/plate-plugin-torch` allowed the **unchanged** official loader to copy the existing local checkpoint into its normal cache via a `file://` URL. No vendor/model/preprocessing/gallery change was used to bypass initialization.

Reproducible ordinary setup, using repository conventions:

```bash
cd /home/intern/part-time/mohamed/pipeline/vehicle
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e \
  /home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

The original backend requirements include CUDA 12.8 Torch and TensorRT dependencies; their existing host/GPU constraints still apply. A clean install of that entire large backend stack was not performed here. The supplemental requirements were checked with pip’s offline dry-run against the verified environment and were all satisfied. The tested environment has Torch 2.11.0+cu128, Torchvision 0.26.0+cu128, PaddleOCR 3.7.0, PaddlePaddle 3.3.1, NumPy 2.3.5, OpenCV 5.0.0 and Ultralytics 8.4.46. OpenCV differs from the backend file’s existing 4.13.0.92 pin; fresh-provisioned exact-stack behavior remains to be checked on the deployment host.

For an existing CV environment, install the supplemental file and the same absolute editable checkout in that environment. For standalone use of the backend environment:

```bash
cd /home/intern/part-time/mohamed/pipeline/services/license_plate_service
../../vehicle/.venv/bin/python -m run_video_pipeline.run_video_prototype /absolute/video.mp4 --max-frames 60
../../vehicle/.venv/bin/python -m run_rtsp_pipeline.run_rtsp_prototype --url 'rtsp://camera-host/stream' --max-frames 60
```

Keep plugin assets/configuration together. Use writable `TORCH_HOME`, `PADDLE_PDX_CACHE_HOME` and `MPLCONFIGDIR` as needed. For offline registration OCR, the existing `PP-OCRv6_medium_rec` assets must be cached. Verification reused the existing `/home/intern/.paddlex/official_models` through a temporary cache symlink; it did not download a registration model. The plugin’s Arabic OCR assets remain its bundled files. Disable `--agent-enabled` during local verification and set `OPENROUTER_ENABLED=false`.

## 6. Verification results

### Real inference

- **Full classifier file/array parity passed:** all 12 reference crops, excluding their own reference IDs, with 24 actual full-classifier calls. Evidence and decisions matched exactly after excluding timing and source-specific metadata. DINOv3, frozen matching and Arabic OCR were real, not mocked. Caller pixels remained equal. The test completed in 46.1 seconds; the separate Arabic-only test was intentionally skipped in that run because full parity includes it.
- **Bounded standalone video passed:** original `test_video.mp4`, first 60 frames at 1920×1080/25 FPS; actual detector, tracker, category classifier, Arabic OCR, registration OCR, marker detector, persistence and annotation all ran. Two tracks produced two recognition/classification attempts and local accepted results, with zero external calls. Both persisted categories were `tunis`; compact registration categories remained `private`.
- No external AI service was called. CLI agent flag was absent, API key was empty and external enable environment flag was false.

### Mocked/structural verification

- **11 focused runtime tests passed against standalone; the same 11 passed against Django’s pipeline root in a fresh process.** Coverage includes original-array identity/pixels/bbox, classifier-before-registration order, evidence serialization, errors suppressing registration and agents, visual fallback after Arabic OCR failure, one model load on a recognition thread, reuse and rechecks, concurrent caller serialization, new ownership after reconnect, worker cleanup on unreadable video/startup failure, and RTSP camera cleanup before any frame read.
- **9 Django service tests passed**, including new factory-injection and RTSP status-error tests. Django system checks reported no issues. Inference is mocked in service adapter tests.
- **Plugin suite: 23 discovered, 21 passed and 2 real-inference opt-ins skipped**, with import-audit opt-in enabled. This includes a real import audit in separate processes with unrelated cwd and no `PYTHONPATH`, plus the existing 20 focused plugin checks (mocked expensive inference where applicable).
- Changed Python files parsed successfully. Corresponding standalone and Django pipeline files match byte-for-byte. The task-specific patch reverse-check succeeds without applying changes.

Commands used (from repository root unless noted):

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/tests -v
PLATE_PIPELINE_FLAVOR=django PYTHONDONTWRITEBYTECODE=1 \
  /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/tests -v
PYTHONDONTWRITEBYTECODE=1 /tmp/plate-plugin-check-env/bin/python -B \
  vehicle/manage.py test license_plate_service --noinput
PLATE_PLUGIN_IMPORT_AUDIT=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -v

PLATE_PLUGIN_REAL_INFERENCE=1 PYTHONDONTWRITEBYTECODE=1 \
TORCH_HOME=/tmp/plate-plugin-torch PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
PADDLE_PDX_CACHE_HOME=/tmp/plate-plugin-paddle MPLCONFIGDIR=/tmp/plate-plugin-matplotlib \
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  timeout 150s /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -p test_real_interface_parity.py -v
```

Actual video command, from `services/license_plate_service`:

```bash
PYTHONDONTWRITEBYTECODE=1 TORCH_HOME=/tmp/plate-plugin-torch \
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_CACHE_HOME=/tmp/plate-plugin-paddle \
MPLCONFIGDIR=/tmp/plate-plugin-matplotlib YOLO_CONFIG_DIR=/tmp/plate-plugin-yolo \
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
OPENROUTER_ENABLED=false OPENROUTER_API_KEY='' \
  timeout 180s /tmp/plate-plugin-check-env/bin/python -B -m run_video_pipeline.run_video_prototype \
  /home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/test_video.mp4 \
  --max-frames 60 --output /tmp/plate-stage3-smoke/annotated.mp4 \
  --metrics-output /tmp/plate-stage3-smoke/metrics.json \
  --capture-root /tmp/plate-stage3-smoke/captures \
  --selected-crops-dir /tmp/plate-stage3-smoke/selected_crops
```

Artifacts are under `/tmp/plate-stage3-smoke`, including metrics, annotations, selected crops and debug evidence. No generated media was placed in the repository. No real camera/reconnection session, full HTTP-upload inference run, independent accuracy evaluation, clean backend install or GPU-inference performance test was performed. RTSP construction/failure/reconnection ownership is covered by mocked tests and actual import checks.

## 7. Measured latency and queue observations

The real CPU smoke run recorded:

| Measurement | Result |
|---|---:|
| Frames / candidate observations | 60 / 88 |
| Recognition and classification attempts | 2 / 2 |
| Classification durations | 3509.20 ms and 2322.21 ms |
| Mean classification time | 2915.71 ms per recognition attempt |
| Visual feature/match durations | 3072.98 ms and 2227.56 ms |
| Arabic category OCR durations | 434.66 ms and 94.33 ms |
| Mean existing local registration path | 2776.94 ms |
| Mean total recognition | 5692.89 ms |
| Recognition executor concurrency | 1 |
| Maximum outstanding recognition tasks | 1 |
| Mean outstanding recognition tasks | 0.653 |
| End-of-video recognition drain | 3.965 s |
| Frame loop / analysis / annotation | 107.491 / 111.456 / 2.796 s |
| Reported total run wall time | 114.253 s |
| External agent calls | 0 |

These timings exclude model initialization before `runner.run`. Classification timing is direct measured added work per attempt, not a controlled before/after throughput benchmark. Detection averaged 1769.03 ms per frame in this CPU run, so overall throughput is not attributable to classification alone. The source clip covered only 2.4 seconds; this test does not establish real-time capability or production latency.

Outstanding-task snapshots include running and queued work. There was never more than one outstanding task in this sample, so no multi-task backlog was observed. The executor’s existing queue remains unbounded; no backpressure policy was introduced. Rechecks still submit new category work, verified with mocked better-crop submissions; this particular video run had no rechecks. No accuracy improvement is claimed.

## 8. Remaining work

- Provision the documented runtime on the actual deployment host; verify the exact clean-installed stack and GPU choices there.
- Define category-specific number-recognition/parsing policies and explicit category mappings. Current acceptance and public registration category semantics remain unchanged.
- Remove the remaining external-agent route completely in its own change. Successful classifications with insufficient registration evidence can still enter the old route if explicitly enabled; this is **not yet the final fallback-free implementation**.
- Evaluate physically independent plates, broader category/gallery coverage and accuracy. The existing forced four-category classifier, lack of rejection policy and limited development gallery remain unchanged.
- Validate real RTSP operation, reconnect/load latency, concurrent-job memory demand, deployment lifecycle and queue capacity under sustained traffic.
