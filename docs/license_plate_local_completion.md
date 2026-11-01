# Local Tunisian license-plate integration: completion audit

Date: 2026-10-30. Branch: `main`. All changes remain in the working tree; no commit, push or deployment was performed.

## 1. Scope and status

Read and verified the root `AGENTS.md`, actual Django/standalone/plugin source, both prior classifier audit documents and their recorded evidence. Prior audit documents and patches are preserved unchanged. The earlier documents do not contain one unified numbered eight-step plan; the following table summarizes their eight integration workstreams against the requested final outcome.

| Workstream | Status | Evidence / limit |
|---|---|---|
| 1. Shared plugin imports and active paths | Complete | Installed editable checkout; fresh Django/standalone process import audit passes. |
| 2. In-memory classification API and file compatibility | Complete, preserved | Plugin algorithm/API unchanged; real file/array parity on all 12 references passes. |
| 3. Local runtime readiness | Verified persistent host runtime; clean-host/GPU unverified | Dedicated `.runtime/plates/venv`, no `/tmp` deployment dependencies or cache symlinks; explicit local registration assets. |
| 4. Worker lifecycle and scheduling | Implemented and tested | One classifier and registration reader per serialized worker, startup preflight, bounded running+queued tasks, explicit deferral/expiry and drain. |
| 5. Classification before registration OCR | Complete | Every submitted attempt classifies once, selects a policy, then recognizes or explicitly resolves to missing-policy evidence/error. |
| 6. Category-specific number validation | Partial by evidence | `tunis` implemented; `nt`, `idara`, `shorta` explicitly unresolved pending format/layout support. No claim that their number recognition is complete. |
| 7. Eliminate external recognition | Complete | Four client modules deleted; no active constructor/call/setting/CLI option; legacy request options rejected. |
| 8. Results, persistence and end-to-end verification | Implemented; evaluation limited | Debug/export/API/UI/annotations updated; real crops, standalone and Django upload pass. No camera or independent accuracy dataset available. |

The normal pipeline is now fully local and autonomous. **It is not a validated four-category number recognizer:** three number policies intentionally remain evidence-blocked, and unknown-category rejection/independent accuracy are unvalidated.

## 2. Every source/documentation change

Initial contents were preserved under `/tmp/plate-local-before` for task-specific comparison; this backup is not a runtime dependency. Generated runtime packages, caches, frontend build outputs and verification media are excluded from source inventory and ignored by Git. Their locations are documented below. Model assets, vendored DINOv3 code, plugin preprocessing, gallery and conservative NT decision rule were not edited.

| Status | Full path | Purpose |
|---|---|---|
| Modified | `/home/intern/part-time/mohamed/pipeline/.gitignore` | Exclude persistent runtime, development tooling and generated Python metadata. |
| Modified | `/home/intern/part-time/mohamed/pipeline/README.md` | Document local-only invocation, policy limits and persistent offline setup. |
| Modified | `/home/intern/part-time/mohamed/pipeline/frontend/src/App.jsx` | Display validated numbers only, explicit unresolved/error reasons and identity conflicts; separate OCR score. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/tests/test_integration_imports.py` | Verify current RTSP/video bindings without the removed global reader API. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/README.md` | Document local-only invocation, policy limits and persistent offline setup. |
| Deleted | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/agent.py` | Remove the obsolete external vision client entirely. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/config.py` | Remove license-plate external-model/key/environment settings; preserve local thresholds. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/data_capture.py` | Persist compact validated fields and detailed category/policy evidence, final identity decisions, reviews and export. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/ocr.py` | Use explicit local OCR assets, preserve raw text and decimal digits, propagate errors; no runtime weight download. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/pipeline.py` | Classify once, dispatch number policy, preserve partial OCR evidence/region coordinates and explicit failures. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/plate_detector.py` | Fail clearly on missing local detector weights before any potential model resolution. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/private_plate_parser.py` | Normalize decimal digits with leading zeros preserved; retain private structure and ambiguity safeguards. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Remove all remote construction/routing; retain serialized ownership and build explicit local outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/routing.py` | Route only validated local results or explicit unresolved/error outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` | Inject local worker dependencies, publish compact outcomes/scheduling, record expired deferred work and preserve cleanup. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_video_prototype.py` | Bound scheduling, drain/defer explicitly, compare category plus validated identity, persist conflicts, annotate validated text and measure stages. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/schemas.py` | Define separate category/number evidence, explicit local result/status/reason fields and queue timing. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/README.md` | Document local-only invocation, policy limits and persistent offline setup. |
| Deleted | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/agent.py` | Remove the obsolete external vision client entirely. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/classification.py` | Project category-only evidence without probability claims; retain failure context and offline initialization. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/config.py` | Remove license-plate external-model/key/environment settings; preserve local thresholds. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/data_capture.py` | Persist compact validated fields and detailed category/policy evidence, final identity decisions, reviews and export. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/ocr.py` | Use explicit local OCR assets, preserve raw text and decimal digits, propagate errors; no runtime weight download. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py` | Classify once, dispatch number policy, preserve partial OCR evidence/region coordinates and explicit failures. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/plate_detector.py` | Fail clearly on missing local detector weights before any potential model resolution. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/private_plate_parser.py` | Normalize decimal digits with leading zeros preserved; retain private structure and ambiguity safeguards. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Remove all remote construction/routing; retain serialized ownership and build explicit local outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/routing.py` | Route only validated local results or explicit unresolved/error outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/run_video_prototype.py` | Bound scheduling, drain/defer explicitly, compare category plus validated identity, persist conflicts, annotate validated text and measure stages. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/schemas.py` | Define separate category/number evidence, explicit local result/status/reason fields and queue timing. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/tests/test_classifier_runtime.py` | Test category/number validation, normalization, errors, ownership, replacement, persistence and saturation under network denial. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/README.md` | Document local-only invocation, policy limits and persistent offline setup. |
| Deleted | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/agent.py` | Remove the obsolete external vision client entirely. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/config.py` | Remove license-plate external-model/key/environment settings; preserve local thresholds. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/data_capture.py` | Persist compact validated fields and detailed category/policy evidence, final identity decisions, reviews and export. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/ocr.py` | Use explicit local OCR assets, preserve raw text and decimal digits, propagate errors; no runtime weight download. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/pipeline.py` | Classify once, dispatch number policy, preserve partial OCR evidence/region coordinates and explicit failures. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/plate_detector.py` | Fail clearly on missing local detector weights before any potential model resolution. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/private_plate_parser.py` | Normalize decimal digits with leading zeros preserved; retain private structure and ambiguity safeguards. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Remove all remote construction/routing; retain serialized ownership and build explicit local outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/routing.py` | Route only validated local results or explicit unresolved/error outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` | Inject local worker dependencies, publish compact outcomes/scheduling, record expired deferred work and preserve cleanup. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_video_prototype.py` | Bound scheduling, drain/defer explicitly, compare category plus validated identity, persist conflicts, annotate validated text and measure stages. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/schemas.py` | Define separate category/number evidence, explicit local result/status/reason fields and queue timing. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/README.md` | Document local-only invocation, policy limits and persistent offline setup. |
| Deleted | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/agent.py` | Remove the obsolete external vision client entirely. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/classification.py` | Project category-only evidence without probability claims; retain failure context and offline initialization. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/config.py` | Remove license-plate external-model/key/environment settings; preserve local thresholds. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/data_capture.py` | Persist compact validated fields and detailed category/policy evidence, final identity decisions, reviews and export. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/ocr.py` | Use explicit local OCR assets, preserve raw text and decimal digits, propagate errors; no runtime weight download. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py` | Classify once, dispatch number policy, preserve partial OCR evidence/region coordinates and explicit failures. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/plate_detector.py` | Fail clearly on missing local detector weights before any potential model resolution. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/private_plate_parser.py` | Normalize decimal digits with leading zeros preserved; retain private structure and ambiguity safeguards. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Remove all remote construction/routing; retain serialized ownership and build explicit local outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/routing.py` | Route only validated local results or explicit unresolved/error outcomes. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py` | Bound scheduling, drain/defer explicitly, compare category plus validated identity, persist conflicts, annotate validated text and measure stages. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/schemas.py` | Define separate category/number evidence, explicit local result/status/reason fields and queue timing. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py` | Construct local-only Django workers; reject obsolete remote request settings. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/tests.py` | Update local wiring checks and test rejected legacy request options. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/views.py` | Return a clear validation response for removed legacy remote options. |
| Added | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/policies.py` | Explicit category dispatcher, public-category aliases and evidence-limited local number validation. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/policies.py` | Explicit category dispatcher, public-category aliases and evidence-limited local number validation. |
| Added | `/home/intern/part-time/mohamed/pipeline/scripts/setup_local_plate_runtime.sh` | Provision a dedicated persistent feature runtime, optionally reusing an explicitly selected persistent host CV installation. |
| Added | `/home/intern/part-time/mohamed/pipeline/scripts/run_local_plate.sh` | Launch Django/video/RTSP with persistent caches and installed plugin, anchored to repository location. |
| Added | `/home/intern/part-time/mohamed/pipeline/scripts/verify_local_plate.py` | Run bounded real crops/standalone/Django checks under socket denial; write auditable results and performance. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_local_completion.md` | This completion/status/evidence audit. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/license_plate_local_completion.patch` | Complete task-specific diff; excludes itself to avoid recursion. |

The complete task-specific diff is [license_plate_local_completion.patch](license_plate_local_completion.patch). It includes new files and full deletions against the captured starting state, excludes unrelated/pre-existing changes, and excludes the patch itself. The audit report is included in the patch.
## 3. Active execution paths and ownership

The standalone and Django copies implement equivalent behavior. Existing top-level package names still require selecting one service root per process; the plugin itself uses the same editable installation, not cwd-dependent import injection.

- **Django video:** [service.py:29](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:29) → factory injection at line 55 → [VideoPrototypeRunner](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:499) → `_initialize_models` at line 569 → selected-crop `_submit` → `_recognize` → [PrototypePlatePipeline.process_crop](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py:49).
- **Django RTSP:** [service.py:143](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/service.py:143) calls [run_rtsp](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:408). RTSP constructs its **own** prototype and duplicate runner at lines 460–465. That prototype imports `analyze_plate_crop` from **`run_video_pipeline.pipeline`**. Its runner calls the injected RTSP-local prototype instance; it does not substitute the video prototype merely because the duplicate runner imports that name.
- **Standalone video:** [CLI/runner](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/run_video_prototype.py:1066) constructs the same local factory/reader/pipeline dependencies.
- **Standalone RTSP:** [RTSP construction](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:460) follows the same RTSP-local-instance/video-recognition-function binding.
- **Actual shared recognition:** [pipeline.py:216](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py:216) wraps `_analyze_plate_crop`; classification occurs at line 143, policy selection at line 154, then whole-crop registration OCR. `policies.py` selects the evidence-supported policy. The standalone counterpart is [pipeline.py](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py:216).

Each runner initializes classifier, registration reader, marker model and detector on its single worker before frames are processed. The classifier retains its existing checkpoint/architecture/index compatibility checks. Missing registration assets, wrong model identity, missing marker/detector files or classifier initialization failures surface before frame processing. The registration reader now belongs to the worker instead of using the prior active global reader cache. Classifier access remains protected by the pipeline lock; there is no shared classifier singleton.

Shutdown releases classifier/reader references on the worker and joins queued work. Marker/detector objects are local to the job and released with job objects. RTSP closes capture/writer in `finally`; startup exceptions reach Django session `status=error`. A reconnect drains/releases the old worker and constructs a new owner. Native model hangs do not have a new hard-kill timeout; serialized thread shutdown can wait for native inference. Bounded verifier processes use external timeouts.

## 4. Category mapping, formats and evidence limits

The explicit dispatcher is [policies.py](/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/policies.py). These public aliases are application contracts, not claims of universal legal registration formats:

| Plugin category | Public category | Policy | Number acceptance |
|---|---|---|---|
| `tunis` | `private` | `tunis_private` | Existing private parser + marker + confidence + exact digit evidence. |
| `nt` | `temporary` | `nt_awaiting_evidence` | Unresolved: `number_policy_evidence_missing`. |
| `idara` | `administrative` (additive) | `idara_awaiting_evidence` | Same explicit missing-support outcome. |
| `shorta` | `police` (additive) | `shorta_awaiting_evidence` | Same explicit missing-support outcome. |

### Implemented `tunis` rules

Retain the existing 1–3 digit series and 1–4 digit registration fields; explicit `T`/`تونس` establishes their boundary. Markerless digits enumerate legal splits and remain ambiguous unless unique. **Even a unique parse or a category prediction is insufficient for acceptance:** a reliable YOLO `تونس` marker and adequate registration OCR are required.

Whole-crop OCR runs unchanged on selected BGR pixels. Only ambiguous digit strings with a reliable marker receive left/right digit-region OCR. Region fields must be ASCII digits after decimal normalization, meet original length/confidence conditions and concatenate **exactly** to whole-crop digits before fusion. Defaults remain whole OCR ≥0.90, reliable marker ≥0.50 and region OCR ≥0.50, with existing environment overrides unchanged. No similarity cutoff or category-probability threshold was added.

Conflicting whole/region readings, invalid or ambiguous fields, missing marker evidence, low OCR evidence, and conflicting explicit category-marker evidence remain unresolved with machine-readable reasons. NT parser “conflict” is not blindly treated as a contradiction: a `تونس` reading agrees with a `tunis` category; explicit `شرطة` evidence does not. OCR/marker backend failures are errors, with completed partial OCR retained.

Raw OCR is preserved separately. ASCII, Arabic-Indic and other Unicode decimal digits normalize deterministically to ASCII; leading zeros stay strings. Uncertain letters are never guessed as digits. Spaces/hyphens are treated as separators where the existing private parser allows comparison; uncertain characters no longer disappear during OCR normalization. No non-private parser, fixed region coordinate or guessed prefix was introduced. Derived recognition regions carry explicit crop-local xyxy boxes; the original detector bbox remains in source-frame coordinates.

### Why the other number policies are incomplete

Evidence inspected:

- [Reference manifest](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/assets/references/manifest.json): 3 `idara`, 4 `nt`, 2 `shorta`, 3 `tunis` development crops with registration strings, but null physical/group IDs.
- [Saved successful per-image OCR](/home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin/last_successful_pipeline_documentation/SUCCESSFUL_PER_IMAGE_RESULTS.csv): `nt` category OCR includes incomplete readings such as `نت68`; `shorta` ID 8 reads `300` versus label `14330`. These outputs support category evidence, **not verified number transcription**.
- Historical `LicenseExp` manifests/recrop records reuse these development images. Its Arabic extended manifest supplies manual **category-marker** boxes, not a complete independently validated number-layout specification.
- Four extra PNGs at the plugin root have no verified category/number/group labels. Different file hashes do not establish independent vehicles or trustworthy unsupported-category examples.

The three `idara` labels (`17 - 138957`, etc.) do not establish a universal prefix/field length. The four `nt` and two `shorta` labels likewise cannot establish universal length, marker position, layout or separator rules. No authoritative format specification or independent number-layout dataset was supplied/found in the repository. Consequently these policies do not invoke the private parser or duplicate number OCR; they return explicit unresolved support status. Classifier Arabic text is kept as category-only debug evidence.

Required to finish: independently labelled complete crops for each intended layout, category and full registration strings preserving zeros, number-field/marker region annotations, known valid/invalid format rules or an authoritative specification, and physical-vehicle/group identities for leakage control. Separate development and final evaluation sets are required. This was asked of the user; no additional dataset/specification has been provided.

## 5. Local outcomes, errors and replacement

Each submitted attempt classifies anew, including better-crop rechecks. Results separate `predicted_category`, category decision source, raw OCR, validated fields, normalized number, selected policy, OCR score and visual similarity.

- `status=accepted`, `recognition_status=recognized`: implemented checks passed; only validated fields/number are published as recognized text.
- `status=unresolved`: explicit reason such as `number_policy_evidence_missing`, `ambiguous_fields`, `invalid_format`, `conflicting_readings`, `insufficient_marker_evidence` or `insufficient_ocr_confidence`.
- `status=error`: `classification_failure`, `ocr_failure`, `marker_localization_failure` or `processing_failure`; partial evidence survives where available. No retry invokes a remote service or approval flow.

The plugin’s own Arabic OCR failure still preserves a successful visual category, as before. That is distinct from registration OCR failure. The four-way forced category never alone authorizes a number, and missing policy support is not a claim that a plate is foreign/unsupported.

Crop scoring, recognition triggers and the initial/better-crop two-attempt policy remain unchanged. Identity now consists of **plugin category plus validated registration fields**. Equal digits in different categories are different identities. Unresolved/error rechecks never erase accepted results. An accepted recheck can replace an unresolved initial result. Matching accepted identities can confirm a better crop when the same policy’s OCR confidence is comparable; the retained `replacement_confidence_gain` parameter (default 0.02) now supplies the allowed OCR-score tolerance for this same-identity comparison. Conflicting accepted identities never replace the accepted initial identity merely through a larger confidence value. Conflicts and sample IDs are persisted and exposed explicitly. An unresolved recheck with a different predicted category sets a separate `category_conflict` flag without pretending it established a second validated identity.

Track-finalization events preserve selected sample ID, all attempt IDs, conflict flag and replacement reason. Keeping an accepted initial identity with a visible conflict is conservative retention, not proof the conflict is resolved correctly. No manual approval is required during normal recognition; review/export functionality is offline annotation tooling only.

## 6. Persistence, API and frontend

Internal schemas add typed `NumberRecognition`, distinct from `CategoryClassification`. Compact predictions, video track summaries and RTSP results add predicted category/decision source, policy, validated registration fields, normalized number, recognition status/reason, sample ID and attempt timing. Detailed OCR, category-only text, checks, similarities, region boxes and partial errors stay in debug records. Original selected crops and source-frame bboxes are retained; existing JPEG crop persistence remains JPEG, not a claim of lossless pixel archival.

Existing `plate_category=private` remains for recognized `tunis`. Existing `confidence`/`category_confidence` remain legacy registration-evidence scores, not calibrated category probabilities. An additive `ocr_confidence` is explicit; cosine similarity is never substituted into these scores. Raw OCR remains in debug/review fields, but unresolved numbers have empty public display text and no validated normalized number. The frontend shows predicted/public category aliases with decision-source tooltips, an explicit OCR-confidence heading for plate results, “Unresolved”/“Error”, a readable reason and identity/category-conflict notices instead of raw text or misleading “Pending”. Video annotations display validated numbers or reasons, decision source, status, attempt ID and latency; Arabic private marker is displayed as `T` in OpenCV’s ASCII annotation.

Review/export gains policy/fields/status/reason and category evidence. CSV `predicted_category` preserves its prior public-category meaning; additive `plugin_category` carries the classifier category. Final conflict/replacement metadata is merged without overwriting raw prediction debug evidence.

Intentional compatibility changes: external-agent fields/options/metrics are removed; statuses now use `accepted`/`unresolved`/`error`, routes are `private_local`/`local`/`unknown`, and `administrative`/`police` are additive public labels. Django rejects `agent_enabled` even when false (HTTP 400); CLI parsers reject `--agent-enabled` as an unrecognized option. There is no hidden reactivation through old environment variables. Compact clients should use validated-number/status fields, not raw OCR fallbacks.

## 7. Bounded scheduling and resources

`PLATE_MAX_PENDING` (positive integer, default 4; constructor override available) bounds **running plus queued recognition futures**. Per-track pending and two-attempt safeguards remain. A full queue records a per-track deferred trigger, eligibility time and source reference without copying another crop into an executor task or blocking the frame loop. When capacity frees, deferred work uses that track’s current best crop. `queue_wait_ms` measures eligibility-to-execution, including capacity deferral; it is separate from inference time.

- End of video, stream stop and reconnect submit eligible end-of-source tracks, retry deferred submissions and drain them before model release.
- Processing failure also drains already eligible/deferred work before cleanup; no hard inference timeout was added.
- RTSP stale pending work is not retired while executing. A stale **unscheduled deferred** track at the existing 300-processed-frame retention limit records `queue_capacity_retention_limit`, the retained crop/bbox/source and a scheduling event. It is explicitly unresolved, not silently abandoned or counted as actual inference. An already accepted result is retained if only its recheck expires. This prevents deferred crop state from accumulating forever during sustained overload.
- Existing diagnostic history remains bounded. Video retains track history as before. The task bound does not impose a new hard cap on all detections/tracks within a frame, disk usage or native inference duration.

Metrics report capacity, pending snapshots, deferred events, eligibility/queue delay, classification/registration latency, classifier and total worker initialization. Actual independent camera reconnect delay is unmeasured. Synthetic 100-track stress verifies capacity never exceeds 2, all eligible retained tracks finalize, and shutdown drains work. Real reference-crop burst submits 12 tracks with capacity 2 and records 10 deferrals. This is scheduling stress, not representative sustained production traffic.

## 8. Persistent runtime and reproducible setup

The verified runtime is `/home/intern/part-time/mohamed/pipeline/.runtime/plates/venv/bin/python` (Python 3.11.15). It explicitly inherits the persistent host CV site-packages at `/home/intern/.venv/lib/python3.11/site-packages` and installs the pinned classifier supplement into its own environment. No unrelated environment was upgraded. It depends on that persistent host installation, **not** `/tmp`; the default clean setup path below installs backend dependencies instead of inheriting it.

```bash
# From repository root, clean dedicated environment:
bash scripts/setup_local_plate_runtime.sh

# Alternatively, explicit reuse of this host's existing persistent CV installation:
PLATE_PYTHON=/home/intern/.venv/bin/python \
PLATE_BASE_SITE_PACKAGES=/home/intern/.venv/lib/python3.11/site-packages \
  bash scripts/setup_local_plate_runtime.sh

export REGISTRATION_OCR_MODEL_DIR=/absolute/path/to/PP-OCRv6_medium_rec
bash scripts/run_local_plate.sh django check
bash scripts/run_local_plate.sh django runserver
bash scripts/run_local_plate.sh video /absolute/video.mp4 --max-frames 60
bash scripts/run_local_plate.sh rtsp --url 'rtsp://camera-host/stream' --max-frames 60
```

Setup follows `vehicle/requirements.txt`, its existing classifier supplement and editable plugin installation. It never provisions a system-wide environment. The tested stack includes Torch 2.11.0+cu128, Torchvision 0.26.0+cu128, PaddleOCR 3.7.0, PaddlePaddle 3.3.1, NumPy 2.3.5, OpenCV 5.0.0, Ultralytics 8.4.46 and the pinned TorchMetrics/OmegaConf supplement. The backend’s clean-file OpenCV pin is 4.13.0.92; a full fresh installation of that exact large backend stack (including its CUDA/TensorRT dependencies) was not performed. This host-reuse runtime is verified; arbitrary deployment hosts are not claimed ready.

Assets:

- Plugin DINOv3 checkpoint: `LicensePlatePlugin/assets/models/dinov3/dinov3_vits16_pretrain_lvd1689m-08c60483.pth`, existing SHA-256 `08c60483bc63c04f533611e34bf70b120eedb7240f469bc16e9e20bf344b941d`.
- Unchanged vendored code and frozen gallery under `assets/vendor/dinov3` and `assets/reference_index/reference_r2.{npz,json}`.
- Bundled category Arabic OCR at `assets/models/arabic_PP-OCRv5_mobile_rec`.
- Local detector/marker files remain in each pipeline’s existing `models/` directory, with existing explicit path overrides.
- Number OCR must be installed before startup. Verification uses persistent `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec`. An absolute model directory, three inference files and matching YAML model identity are required; missing assets error instead of triggering a weight download.

The launcher sets writable caches under `.runtime/plates/cache/{torch,paddle,matplotlib,yolo}`, disables model-source checks and automatic dependency installation, and uses the installed package. The unchanged official DINO loader populates its managed cache from the **existing local file URL**, not an internet download. No source model was substituted or copied into application packages; no temporary model/cache symlink is used. Package/toolchain downloads occurred only during setup.

`PLATE_CLASSIFIER_DEVICE` selects visual inference device; `PLATE_OCR_DEVICE` selects number OCR device (CPU defaults). The plugin’s category Arabic device remains its bundled configuration. GPU choices must be compatible with the host and have not been validated here. Real checks explicitly hide CUDA and limit CPU threads to 2. API startup and offline recognition need no external AI key or manual approval.

## 9. Tests and real verification

### Mocked correctness and structural checks

- Pipeline suite: **31 tests pass against each service root** in separate processes. Coverage includes dispatch, Arabic-Indic/ASCII digits and leading zeros, ambiguity/invalid/conflicting readings, marker/region safeguards, classification/OCR/marker errors, partial evidence, initialization/reuse/reconnect cleanup, bounded saturation, expiry, 100-track stress, final/error drain, category-aware identity conflicts, compact results, review/export, rejected legacy flags and absent client modules. New local checks deny socket connections. These are not accuracy measurements.
- Backend: **17 tests pass** using explicit labels `license_plate_service vehicle_color_service vehicle_classification_manufacturer_service api`; Django system checks pass. An initial unlabelled invocation from repository root discovered zero tests and was not counted as validation; it was rerun with these labels.
- Plugin/import suite: **23 discovered, 21 pass, 2 inference opt-ins skipped** with `PLATE_PLUGIN_IMPORT_AUDIT=1`. The fresh-process audit resolves the same installed plugin and actual Django/standalone video/RTSP bindings from unrelated working directories.
- Real file/array parity was run separately: **one opt-in test passes across all 12 reference crops (24 real calls)**, with the Arabic-only opt-in skipped in that invocation. Evidence and decisions match excluding timing/provenance. No plugin model, representation, gallery or decision rule changed.
- Frontend `npm run lint` and `npm run build` both pass. Node/npm were initially absent; official Node 22.16.0 Linux tooling was installed **only under `.runtime/tooling`**, with SHA-256 verified against the official checksum list (`f4cb75bb036f0d0eddf6b79d9596df1aaab9ddccd6a20bf489be5abe9467e84e`). Existing frontend dependencies were used; no system tooling or package lock was modified. Browser/manual visual interaction was not performed.
- Changed Python sources parse, equivalent service copies match, whitespace checks and patch reverse-check pass.

### Actual inference and network denial

`scripts/verify_local_plate.py` denies Python socket `connect` and `create_connection`, including model initialization, and fails if either is attempted. This is a Python connection guard, not an OS-wide packet-capture/network namespace claim. Local `file://` checkpoint cache reads and local FFmpeg file conversion remain permitted.

| Real check | Result |
|---|---|
| Complete reference crops, all categories | 12 actual classifier calls, real marker/registration models where applicable, capacity 2; 3 validated `tunis` numbers, 9 explicit missing-policy outcomes; zero socket attempts. |
| Actual Django multipart upload | HTTP 200, 30 actual frames, one recognized `tunis` track (`179 تونس 8065`), local H.264 conversion; zero socket attempts. Uses actual view/service/runner/model path, not a mocked adapter. |
| Standalone video | 60 actual frames, two recognized `tunis` tracks (`179 تونس 8065`, `187 تونس 9438`), zero socket attempts. |
| Live RTSP | **Not run:** user confirmed no test camera available. Reconnect/stop ownership, failures and deferred-work retirement are controlled tests only. |

Verification artifacts are ignored, persistent files under `.runtime/verification/`: `crops/verification.json` and captures; `django_current/verification.json` and upload media; `standalone_final/verification.json`, metrics, annotations/captures; `parity.log`. Several earlier smoke iterations are also retained there, not overwritten or confused with the named final runs. They are development data, not production output or an independent test set.

Reproduction (repository root, after setup):

```bash
PYTHONDONTWRITEBYTECODE=1 .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
PLATE_PIPELINE_FLAVOR=django PYTHONDONTWRITEBYTECODE=1 \
  .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
bash scripts/run_local_plate.sh django test license_plate_service vehicle_color_service vehicle_classification_manufacturer_service api --noinput
PLATE_PLUGIN_IMPORT_AUDIT=1 PYTHONDONTWRITEBYTECODE=1 \
  .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/LicensePlatePlugin/tests -v

# Real checks: run separately to avoid distorting performance with concurrent workloads.
export REGISTRATION_OCR_MODEL_DIR=/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 CUDA_VISIBLE_DEVICES=''
timeout 180s bash scripts/run_local_plate.sh verify --mode crops --output .runtime/verification/crops_new
timeout 180s bash scripts/run_local_plate.sh verify --mode django --frames 30 --output .runtime/verification/django_new
timeout 180s bash scripts/run_local_plate.sh verify --mode standalone --frames 60 --output .runtime/verification/standalone_new
```

Parity uses `PLATE_PLUGIN_REAL_INFERENCE=1`, the persistent Torch/Paddle/Matplotlib cache variables from the launcher, and unittest discovery with `-p test_real_interface_parity.py`. Test diagnostics use temporary directories cleaned by the tests; **deployment models/environments/caches do not rely on `/tmp`**.

Frontend checks used `.runtime/tooling/node-v22.16.0-linux-x64/bin` on `PATH`, then `npm --prefix frontend run lint` and `npm --prefix frontend run build`. Package/toolchain downloads were installation-time operations; no external AI call was made at any point.

## 10. Accuracy and performance: measured scope

### Development-only recognition observations

The crop manifest is the classifier reference/development gallery, with unknown physical independence. Runtime smoke matching can self-match that gallery. Do **not** interpret category matches on these inputs as independent category accuracy. The parity test excludes each query reference for equivalence verification, but that is still development leave-one-out data.

| Annotated category | Crops | Recognized numbers | Exact matches to saved number annotations | Unresolved | Errors |
|---|---:|---:|---:|---:|---:|
| `idara` | 3 | 0 | 0 | 3 | 0 |
| `nt` | 4 | 0 | 0 | 4 | 0 |
| `shorta` | 2 | 0 | 0 | 2 | 0 |
| `tunis` | 3 | 3 | 3 | 0 | 0 |
| Total | 12 | 3 | 3 | 9 | 0 |

On these reused development annotations only, recognized coverage is 3/12 (25%), unresolved rate 9/12 (75%), complete-number matches counting unresolved as not correct are 3/12, and observed accepted-number errors are 0/3. The three matched private fields are `150/9603`, `122/937`, `105/395`. These small, reused counts **do not establish independent accuracy, calibrated error rates or improvement**. No independent category accuracy, complete-number accuracy, false-accept/reject rejection tradeoff or production accepted-result error rate is available.

No suitable labelled independent known/unsupported-category split with physical deduplication was available. The four unlabelled extra PNGs and related recrops cannot support threshold selection or final evaluation. Unknown rejection remains unvalidated; no cosine cutoff was invented. No reliable foreign/unsupported identification is claimed. The historical 60-frame smoke also reported two accepted private numbers, but has no independent verified ground truth or controlled performance baseline. No accuracy or speedup claim is made from comparing those reports.

### Performance observations

CPU-only, two requested CPU threads, serialized inference; not a production capacity benchmark:

| Measurement | Standalone final, 60 frames | Django current, 30 frames |
|---|---:|---:|
| Total process elapsed (verification wrapper) | 27.246 s | 18.824 s |
| Worker model initialization | 5928.75 ms | 6327.73 ms |
| Classifier portion of initialization | 4936.04 ms | 4874.36 ms |
| Classification per attempt, mean | 637.19 ms (2 attempts) | 809.97 ms (1 attempt) |
| Total classification + registration per attempt, mean | 1726.59 ms | 1762.32 ms |
| Eligibility/queue wait, mean | 0.776 ms | 0.326 ms |
| Detector time per frame, mean | 267.51 ms | 297.77 ms |
| Reported processing throughput | 3.313 frames/s | 3.298 frames/s |
| Outstanding task maximum | 1 / capacity 4 | 1 / capacity 4 |
| Peak process RSS | 1,838,440,448 bytes | 1,907,097,600 bytes |

The 12-crop burst recorded 10 deferrals at capacity 2; mean eligibility wait 2700.14 ms, maximum 9665.34 ms, mean classification 616.65 ms and cold worker initialization 14666.99 ms. All retained work completed; no GPU-memory number was inferred. `nvidia-smi` could not communicate with the NVIDIA driver on this host, and GPU inference/configuration remains unverified.

Video throughput is below the source 25 FPS. The detector/frame loop is a material bottleneck; classification/registration also add serialized work. Model initialization now has explicit timing outside frame processing. Prior measurements had different timer/load boundaries and host conditions, so the earlier 2.92 s / 5.69 s per-attempt observations are not a controlled before/after benchmark. No real-time or optimization claim follows from these measurements. Synthetic queue saturation is not representative sustained RTSP load; real reconnect delay and long-running overload/memory/disk behavior remain unmeasured.

## 11. Evidence of external execution removal

- Deleted all four `run_{video,rtsp}_pipeline/agent.py` clients across both roots.
- Active prototypes no longer accept, construct, import or call an external client. Routing has only validated local or explicit unresolved/error outcomes.
- Removed license-plate external API-key/model/URL/retry/environment settings and CLI switches. Legacy Django options are explicitly rejected rather than ignored or activated.
- Removed external execution metrics and active LP README/key instructions. Historical audit patches/documents remain preserved as historical evidence, not runtime instructions.
- Source scans of all active LP pipeline Python files find no `OpenRouter`/`OPENROUTER_`; import-spec tests find no agent modules. Remaining `agent_enabled` strings in the Django LP app are the validation rejection and its tests only. No frontend remote control was present.
- Real initialization/crop/video/upload checks deny socket connections and record zero attempts.
- `langchain-openai` and shared external credentials used by **vehicle colour** remain untouched: its detector/config explicitly use them. Removing unrelated feature dependencies or credentials would violate scope. Those are not license-plate execution paths.

## 12. Remaining blockers and unverified behavior

1. **Non-private number policies are not complete.** Supply verified independent `nt`/`idara`/`shorta` field/layout data or authoritative rules before implementing acceptance. Until then their category predictions return explicit unresolved support status.
2. **Unknown/unsupported rejection and independent accuracy are not validated.** Need labelled known and unsupported plates, physical/group IDs, separated development/threshold/final-evaluation splits, and verified complete registration fields. No probability/cosine threshold or foreign identification claim is made.
3. **No live camera:** user explicitly reported no RTSP test available. Real reconnect delay, stream stop under load, recording continuity and long-run resource use remain unverified.
4. **Runtime portability/GPU:** the persistent host-reuse environment works; clean full-stack provisioning and deployment-host GPU checks were not performed. Registration assets must be provisioned before normal startup. Editable installs need the full plugin directory and assets retained; a source-only Git checkout is not sufficient if large weights are ignored/unprovisioned.
5. **Capacity and native duration:** futures are bounded and stale deferred work is explicit, but there is no new hard native inference timeout, global job quota, disk-retention policy or validated production throughput. Accepted identity conflicts remain visible for downstream consumers; retaining the initial result is not an independent correctness guarantee.

All available independent implementation work described here is local and autonomous. No commit, push, deployment, external AI call or fabricated test/format/accuracy result was performed.
