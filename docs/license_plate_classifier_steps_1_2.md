# License-plate classifier integration audit: steps 1–2

Date: 2026-10-29. Scope: establish import boundaries and add an in-memory classifier interface. Changes remain in the working tree; no commit or deployment was performed.

The root `AGENTS.md` was read before editing. No additional `AGENTS.md` applies inside `LicensePlatePlugin`. The existing plugin source, its README, extraction report, historical implementation documentation, configuration and tests were inspected. The historical documentation describes the experiments; the extracted package already provides a frozen gallery and reusable classifier.

## 1. Files changed

All paths below are absolute. “Modified” means changed from the source present at the start of this task, even though Git currently reports the entire plugin directory as untracked.

| Status | Full path | Purpose |
|---|---|---|
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/classifier.py` | Add validated `classify_bgr`; share feature extraction, matching, OCR and decisions through `_classify_image`; preserve file interface. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/ocr.py` | Accept an optional diagnostic path; otherwise submit a private BGR array directly to Paddle. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/config.py` | Add `load_default_config`, anchored to this source checkout. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/__init__.py` | Export `load_default_config`. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/README.md` | Document shared installation/imports, array API, provenance, diagnostics and opt-in verification. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/tests/test_classifier_interfaces.py` | Twelve focused tests using mocked inference with real preprocessing. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/tests/test_integration_imports.py` | Opt-in fresh-process audit of actual Django/standalone video and RTSP bindings and installed plugin resolution. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/tests/test_real_interface_parity.py` | Separate opt-in full-classifier and Arabic-OCR file/array parity tests on all 12 references. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_classifier_steps_1_2.md` | This audit report. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_classifier_steps_1_2.patch` | Unified diff of the five modified files and four added source/documentation files. The patch excludes itself to avoid recursive content. |

No pipeline runner, Django service, detector, tracker, crop selector, registration reader, result schema, model asset, gallery, configuration JSON, category route, acceptance threshold or external-agent implementation was changed. Pre-existing working-tree changes were preserved. Temporary build/test environments, baseline copies and diagnostic files were placed under `/tmp`.

## 2. Reviewable diff

Open [license_plate_classifier_steps_1_2.patch](license_plate_classifier_steps_1_2.patch). It includes complete new test files and this report, with additions against `/dev/null`. Modified files are compared with copies taken immediately before editing, rather than Git HEAD: the plugin itself was already untracked. This makes this task's changes reviewable independently of the pre-existing imported plugin and unrelated working-tree edits. The patch uses repository-relative `a/` and `b/` paths.

## 3. Confirmed active import and recognition paths

### Django video

- [views.py:20](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/views.py:20): `analyze` saves the upload and calls `run_video_analysis`.
- [service.py:16](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:16): `_ensure_pipeline_import_path` inserts the absolute Django application directory into `sys.path`.
- [service.py:29](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:29): `run_video_analysis` imports `run_video_pipeline` implementations from that directory and constructs the reader, pipeline and runner.
- [run_video_prototype.py:578](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:578): `_submit` snapshots the selected crop into a recognition task.
- [run_video_prototype.py:529](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:529): `_recognize` calls `self.pipeline.process_crop` at line 546.
- [prototype_pipeline.py:59](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py:59): calls `run_video_pipeline.pipeline.analyze_plate_crop`.
- [pipeline.py:135](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py:135): first registration-number OCR call, `_read_safely(reader, detection.crop, detection.bbox)`.

### Django RTSP

- [service.py:130](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:130): worker imports `run_rtsp_pipeline.run_rtsp_prototype.run_rtsp` after the same import-path setup.
- [run_rtsp_prototype.py:18](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:18): imports RTSP-local config, agent, store, marker localizer, reader, detector, prototype pipeline and duplicate video runner. These duplicate modules are genuinely imported.
- [run_rtsp_prototype.py:412](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:412): constructs those objects and runs frames through the RTSP-local `VideoPrototypeRunner`.
- [run_rtsp_pipeline/run_video_prototype.py:546](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_video_prototype.py:546): the duplicate runner calls its **injected** pipeline instance's `process_crop`. In an RTSP session that is `run_rtsp_pipeline.prototype_pipeline.PrototypePlatePipeline`, despite the runner module also importing the video prototype class.
- [run_rtsp_pipeline/prototype_pipeline.py:11](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py:11): explicitly imports `analyze_plate_crop` from **`run_video_pipeline.pipeline`**; its call is at line 59. It also imports video routing, schemas and agent types.
- Consequently RTSP-local `pipeline.py` is **not** the active recognition orchestrator. RTSP-local `ocr.py` does provide `get_reader`, but that module imports **video** config and uses the same configured PP-OCRv6 registration model.

### Standalone video and RTSP

The same call graph applies under `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/`:

- [Video CLI/runner:529](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/run_video_prototype.py:529), [video prototype:59](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/prototype_pipeline.py:59), [registration call:135](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py:135).
- [RTSP imports:18](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:18), [RTSP runner call:546](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_video_prototype.py:546), [RTSP prototype's video import:11](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py:11).

Fresh subprocesses verified these module identities separately for Django and standalone code. They did not initialize detectors or connect a camera.

### Future insertion point

For each pipeline root, the common future boundary is **inside `run_video_pipeline.pipeline.analyze_plate_crop`, immediately before line 135**, after best-crop selection and before the first registration OCR call. This reaches both video and RTSP because both prototypes bind that function. The crop is `detection.crop`, a BGR NumPy array; `detection.bbox` remains in source-frame coordinates.

A future owner should initialize/inject a classifier once and call `classify_bgr(detection.crop)` at that boundary, with category evidence passed explicitly into downstream processing. The current task does not insert that call or decide downstream routing. Hooking only `run_video_pipeline.prototype_pipeline.process_crop` would miss the RTSP-local prototype; editing RTSP-local `pipeline.py` would miss its active orchestrator.

## 4. Shared plugin import strategy

Use the existing editable-package mechanism in each runtime environment, pointing to the **same absolute source checkout**:

```bash
/path/to/runtime/python -m pip install --no-deps --no-build-isolation -e \
  /home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin
```

Prerequisites are the existing Python dependencies and setuptools build backend. There is no runtime `sys.path` injection for this plugin, no model copying and no dependence on shell working directory. Both Django and standalone code use `from license_plate_plugin import PlateCategoryClassifier, load_default_config`. The new helper at [config.py:82](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/config.py:82) locates the bundled JSON using `__file__`; that JSON already resolves model, vendor and gallery paths relative to itself. Explicit `load_config(absolute_config_path)` remains supported.

Verification installed the plugin editable into `/tmp/plate-plugin-check-env`, without fetching packages or changing production environments. The test environment was created with `/home/intern/.venv/bin/python -m venv --system-site-packages`; a temporary `.pth` exposes `/home/intern/.venv/lib/python3.11/site-packages` as its runtime dependencies. From unrelated temporary working directories, with `PYTHONPATH` removed, both process modes resolved the exact same plugin `__init__.py` and gallery. The actual Django `vehicle/.venv` lacks Torch, Paddle and OpenCV; installing this plugin there alone would not provide those dependencies when using `--no-deps`.

Keep the full source directory in place for this editable deployment. Standalone wheel asset bundling is outside this change.

## 5. Interfaces and behavior

```python
import cv2
from license_plate_plugin import PlateCategoryClassifier, load_default_config

classifier = PlateCategoryClassifier(load_default_config())  # Initialize once.

# Existing behavior: file provenance and saved lossless OCR submission.
file_result = classifier.classify("/data/plate.png", "/tmp/plate-file")

# New behavior: no input image or diagnostic file is saved.
plate_bgr = cv2.imread("/data/plate.png")
array_result = classifier.classify_bgr(plate_bgr)

# Optional diagnostic OCR submission; use a distinct directory per retained call.
array_debug = classifier.classify_bgr(plate_bgr, "/tmp/plate-array")

# Both interfaces retain reference/evaluation options.
excluded = classifier.classify_bgr(
    plate_bgr,
    exclude_reference_ids=("5",),
    exclude_groups={"group_id": "example-group"},
    expected_category="nt",
)
```

`classify_bgr` at [classifier.py:59](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/classifier.py:59) requires a nonempty `uint8` NumPy array with shape `(H,W,3)`, assumed BGR. Invalid values raise `PluginError` before processing/output. A contiguous private copy protects even read-only or strided caller storage. Metadata identifies `kind=bgr_array`, dtype, color order and dimensions, with no fabricated path or file hash. Existing file input keeps its original `path`, file `sha256`, width and height keys.

Both interfaces call `_classify_image` at line 101. DINO preprocessing, four-view R2 extraction, matching/exclusions, Arabic normalization, positive-NT override, conflict and OCR-failure behavior remain shared and unchanged. Models and index still load only in initialization. No confidence or rejection threshold was added.

`ArabicRecognizer.recognize` at [ocr.py:58](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/license_plate_plugin/ocr.py:58) now makes its path optional. Without a path it passes a private BGR array to `TextRecognition.predict`. Installed PaddleX source `/home/intern/.venv/lib/python3.11/site-packages/paddlex/inference/common/reader/image_reader.py`, `ReadImage.read`, accepts NumPy arrays directly as BGR. No temporary file is necessary. With diagnostics enabled it retains the existing lossless PNG/path-input behavior. `ocr.submitted_path` is null when absent. Native Paddle `input_path` metadata can differ between interfaces and is preserved truthfully.

The plugin itself does not save the original input crop for array calls. “No files” concerns classifier inputs/diagnostics; underlying libraries can maintain their own caches during model initialization.

## 6. Verification and limitations

All commands used `-B`/`PYTHONDONTWRITEBYTECODE=1` to avoid modifying repository bytecode caches.

| Check | Result |
|---|---|
| Plugin unit suite | 23 discovered: **20 passed**, 3 opt-in tests skipped. Includes 12 new interface tests and 8 existing core tests. Models/index matching mocked in interface tests; geometry, PNG I/O and parser are real. |
| Installed-plugin import audit | **1 test passed**, with separate fresh Django and standalone subprocesses, unrelated cwd and no `PYTHONPATH`. Verified video/RTSP recognition bindings and identical plugin/config paths. |
| Real Arabic OCR parity | **1 test passed across all 12 reference crops** (24 real recognitions); full-classifier test skipped in this run. Raw text, native confidence, native payload except path provenance, and other non-timing evidence matched exactly between file and array. Caller pixels were preserved. |
| Full real classifier parity | **Attempt failed during DINOv3 initialization**, before any classification: missing `torchmetrics` imported through vendored `hubconf.py` → segmentor modules → `dinov3.eval.utils`. No loader/model/configuration workaround was introduced. End-to-end DINO/R2/decision parity remains unverified with real inference. |
| Django service suite | **7 tests passed**; system check reported no issues. Inference is mocked in API tests. |
| Changed Python files | Parsed successfully with `ast.parse`. |

Reproduction commands (from repository root; absolute test paths also work):

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -v

PLATE_PLUGIN_IMPORT_AUDIT=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -p test_integration_imports.py -v

PLATE_PLUGIN_REAL_OCR=1 PYTHONDONTWRITEBYTECODE=1 \
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_CACHE_HOME=/tmp/plate-plugin-paddle \
MPLCONFIGDIR=/tmp/plate-plugin-matplotlib OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  timeout 90s /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -p test_real_interface_parity.py -v

PLATE_PLUGIN_REAL_INFERENCE=1 PYTHONDONTWRITEBYTECODE=1 \
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
  /tmp/plate-plugin-check-env/bin/python -B -m unittest discover \
  -s services/license_plate_service/LicensePlatePlugin/tests -p test_real_interface_parity.py -v

PYTHONDONTWRITEBYTECODE=1 vehicle/.venv/bin/python -B vehicle/manage.py test license_plate_service --noinput
```

The first unbounded Arabic-only attempt stalled during initialization and was interrupted; the bounded run with CPU thread limits completed successfully in 17.7 seconds. Full real-classifier parity is retained as an opt-in test so it can be rerun in a complete environment. The source environment reports Torch `2.11.0+cu128`, OpenCV `5.0.0`, NumPy `2.3.5`, differing from parts of the historical successful environment.

These are interface/equivalence checks, not an accuracy evaluation or accuracy-improvement claim. No live camera, video inference or external AI call was performed.

## 7. Compatibility and remaining work

The file API signature, diagnostic naming, result structure and decision semantics are preserved. Additions are `classify_bgr`, `load_default_config`, optional OCR diagnostic path, array-specific input provenance, and null `submitted_path` when no file exists. Array callers must not assume file-only input keys are present. Shared classifier use across concurrent requests has no new thread-safety guarantee; future ownership/serialization must be explicit. Diagnostic array submissions use a fixed filename within the supplied directory, so use separate directories when preserving multiple calls.

Remaining integration work: provide complete runtime dependencies, verify full real-classifier parity, establish classifier lifecycle per worker, inject classification before registration OCR, define explicit category mapping/evidence persistence and category-specific local registration workflows, and later remove the external-agent route as separately requested. Existing agent behavior remains unchanged by this task. The four-category gallery, forced classification behavior and development-data limitations remain as before.
