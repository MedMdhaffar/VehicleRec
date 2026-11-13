# Real local Plate executors and crop smoke — 2026-11-04

Completed the bounded crop proof: existing local JPEG → Engine-private real local OCR/marker bindings → existing Redis Plate worker → Aggregator → natural-v1 bundle. Recognition actually ran locally. Parent/class/association and Colour were explicitly injected. No full-video, RTSP, database delivery or deployed microservice claim is made. Manufacturer remains unsupported.

## Baseline and changed files

Reviewed native repair baseline `vehicle_engine_plate_native_repair_review.zip`, SHA256 `c3b45947fa60f89dd1ca79b62c045f3fbf35f1df9adca5cf3fc17c6569f0dc72`. No AGENTS.md was found in the repository or ancestors. Existing unrelated tracked/untracked changes were preserved and are recorded separately in the ZIP.

New source: `vehicle/common/engine_plate_artifacts.py`, `vehicle/common/engine_plate_local.py`, `vehicle/common/plate_crop_smoke.py`; new test: `vehicle/common/tests/test_plate_local_executors.py`; explicit entry point: `scripts/vehicle_plate_crop_smoke.py`; this report. `vehicle/common/engine_plate.py` receives the minimal typed local-executor extension. The five architecture documents receive dated status notes. These are exactly 12 scoped paths; 213 packaged local source/schema/test/document files support review and replay. All schemas, canonical Plate/Colour/Aggregator/natural policies, legacy implementations/defaults, Redis codecs/workers/journal/fences, historical fixtures and the native lease repair remain unchanged.

## Installed assets, identities and effective placement

Interpreter `/home/intern/.venv/bin/python`, Python 3.11.15. Installed: paddleocr3.7.0, paddlepaddle3.3.1, paddlex3.7.1, torch2.11.0+cu128, ultralytics8.4.46, opencv-python4.13.0.92, numpy2.3.5, redis7.4.0, jsonschema4.26.0. Inspected the installed TextRecognition implementation before binding it; `predict()` materializes native result iteration and `close()` delegates predictor cleanup. No downloads/installations were used. The ccache warning in the native log is informational; no package installation was attempted.

OCR directory `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec`, whose YAML declares `Global.model_name=PP-OCRv6_medium_rec`:

| File | SHA256 |
|---|---|
| inference.pdiparams (76465087 bytes) | `1b01c79a914587933f615569e75de54f2e638ebb5d3f3b3c1b38c24ede8c7319` |
| inference.json (221814 bytes) | `0b2e25e990bd072f1bf77d59d67d508bce6c4bd44af6624e0fb27d6da2cd00e8` |
| inference.yml (150580 bytes) | `991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129` |

Marker checkpoint `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/tounes_word_detector.pt`, 5371461 bytes, SHA256 `f8956bd78992661bf5a88905f355b48168c0e5d643c3f7635795cfc266d11293`. Observed checkpoint metadata: producer version8.4.46, date `2026-06-18T17:21:12.638095`. No semantic training version is invented from that producer string.

Torch reports CUDA available (build12.8); Paddle is CPU-only and reports device `cpu`. Both recognizers were explicitly initialized/executed on **CPU**, with one CPU thread; marker parameter devices were exactly `[cpu]`. No GPU model invocation was requested. Inference was serialized through the existing process-wide adapter lane; no other model calls ran concurrently in the smoke.

`LocalArtifact` is a frozen content-addressed contract over exact files/sizes/hashes, observed runtime versions, upstream identity, CPU device and preprocessing/configuration. Binding implementation and reused marker decoder/configuration hashes are included. Paths remain private; content-equivalent relocation does not change the digest. Verification occurs before invocation; an altered artifact or loaded-runtime identity is rejected without a model call and blocks publication. `LocalExecutor` coexists with the unchanged explicitly synthetic `InjectedExecutor`; synthetic-name restrictions were not simply removed.

Actual wire model identities:

- OCR: `local-ocr-sha256:db00e4c6cc5994722c8bb255f607c360e9e80b57b099a86a227e6f1ff71296d7`.
- Marker: `local-detect-sha256:de01015c2ecfa4bc47e9f47ff5dd59ceaa81c4bd36a28cd0c4c15acd5750d293`.

The integer model `version=1` denotes the `engine-local-artifact-v1` content-addressed binding representation. It is not a guessed training release or database registration. Exact upstream names, package versions and artifact identities are in the bound digest material and `artifact-bindings.json`. All bound files/implementation were rehashed after execution and remained identical to the reviewed final source. No SQL surrogate keys are needed.

## Engine interfaces and failure semantics

`ArtifactFile` / `LocalArtifact` / `LocalExecutor` define explicit immutable real-artifact provenance. `NativeCPU` owns installed API initialization, preparation, direct predict calls and close. `LocalPlateBinding` owns one shared lazy runtime, original allocation reconstruction/copies, operation-kind execution, native scalar decoding, call counts and coordinate lineage. `EngineCrop` is an idempotently released private byte allocation. These objects and loaded models never cross Redis.

The adapter still validates the original admitted binding before/after read, uses its immutable candidate, validates journal org before EVAL, and cross-checks retained/proposed closure receipts. Existing retry, pending/closure separation, native retirement, resource pinning, exact-ID publication, fencing and v0/v1 transport protections remain. The only adapter extensions are accepting the typed `LocalExecutor`, the explicit fixed-smoke plan tag and coded local not-started/artifact conflicts.

Initialization runs lazily **inside** the native lane and before `started()`. Failed initialization is known-not-started, model null, code `INITIALIZATION_FAILED`, with no predict count. Artifact mismatch is `ARTIFACT_BINDING_CONFLICT` and blocks publication. Ineligible region geometry is known-not-started with no borrowed model attribution. Input preparation precedes start; `started(actual_loaded_artifact.model)` occurs immediately before the synchronous native predict API. Invocation failure after start keeps actual attribution. Invalid native structures become a bounded invalid envelope rejected during adapter decode as `OUTPUT_DECODE_FAILED`, not invented empty OCR/marker evidence.

CPU Paddle predict fully materializes output and Ultralytics uses `stream=False`; explicit retirement occurs when synchronous invocation and local decoding are quiescent. Timeout/Future completion is never used as cancellation proof. `OutcomeUnknown` deliberately does not retire, keeps the lane/crop pinned and prevents model close. Tests exercise that path with controlled runtimes. The real CPU backend exposes no native timeout/cancellation API in this slice.

Model close is idempotent, refuses active work and retains its object on close failure for retry. Crop cleanup still requires confirmed publication or explicit stopped-host discard; acknowledged releases are not repeated. Publication failure retains the immutable bytes/IDs and resources; the smoke host explicitly reports retained unconfirmed work on failure, without silently draining it. Engine process-crash recovery/durable local persistence remains unsupported.

## Pixels, preprocessing and policy ownership

The smoke source reads each selected encoded crop and decodes BGR8 before adapter admission, capturing one UTC/monotonic receipt then; PTS is null. It does not open video. The adapter verifies original pixel byte count/hash; the local binding reconstructs `(height,width,3)` with uint8 and creates an independent copy before any native preprocessing. Tests deliberately mutate native input and prove the admitted allocation/hash are unchanged.

OCR uses the artifact's own BGR RecResizeImg preprocessing (declared YAML shape3×48×320); Engine does not normalize or parse plate text. The installed single-crop TextRecognition response `rec_text` / `rec_score` (optionally under `res`/`json`) is structurally decoded without cleanup, rounding, ranking or semantic fusion. The legacy wrapper normalizes text and converts exceptions to empty outputs, so it is deliberately not called. Legacy behavior is preserved. Marker preparation and box decoding reuse the existing `TounesMarkerLocalizer._prepare/_decode`: direct 512-square stretch, existing native detection floor0.05, explicit CPU. Native 512-space boxes are scaled/clamped by the legacy decoder into original crop pixels.

Whole, marker, left and right are one crop attempt. Regional input is always a fresh slice of the **original** BGR allocation, full height: left columns `[0,x1)`, right columns `[x2,width)` for the exact dependency-bound marker box; empty/out-of-bounds geometry does not execute OCR. Original candidate/hash/size, marker output, kind, exact whole/marker dependency IDs and actual models preserve invertible crop lineage. No extra schema field was needed. Sanitized sidecar lineage records explicit input rectangles as well.

Plans are explicitly `fixed-local-smoke-plan-v1`, supplied before admission. Crop1 runs left then right; crop2 right then left. This is smoke orchestration, not a production Plate follow-up loop or distributed request protocol. All normalization, parsing, fusion, eligibility, attempt ranking and final selection remain in reviewed PlateMetadata. Aggregator authenticates the domain result and performs canonical class consensus/join; it selects no second Plate winner.

## Actual bounded crop execution

Exact supervised command (also in `commands.json`):

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/audit_real_plate_smoke.py /home/intern/part-time/aziz/plate-real-executor-validation/real-smoke /home/intern/.venv/bin/python scripts/vehicle_plate_crop_smoke.py --ocr-dir /home/intern/.paddlex/official_models/PP-OCRv6_medium_rec --marker /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/tounes_word_detector.pt --crops /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/test_output/data_capture/crops/2a925714-5c16-460a-bb47-3d15232d2e75.jpg /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/test_output/data_capture/crops/8de1052b-0071-4b8c-b83e-df5b52c714f6.jpg --output /home/intern/part-time/aziz/plate-real-executor-validation/real-smoke --org 12345678-1234-4234-8234-123456789012 --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --namespace plate-real-crop-20261104 --run-id plate-real-crop-20261104 --admission-seconds 180
```

Two existing crop files were declared before execution, chosen by filename rather than recognition output. **They are content-identical:** one unique 166×53 BGR image. Both file SHA256 `608fc4143170ee16b89d112aff3121fb310847c291105981eca754861691f98d`; pixel SHA256 `192cdd280fb8efba27967c3e9f6f3b9038352b4670a1b6f50e58b52355f91843`. This exercises two declared fixture lifetimes and both regional orders; it is not evidence of recognition diversity or accuracy. No threshold/output tuning or crop reselection occurred.

Parent/class/association/Colour context is explicitly synthetic, unresolved development data. There is no actual vehicle/plate detection or association in this crop proof. Candidate detector confidence and real crop quality are null/unmeasured. Injected class/Colour samples and their identities/quality are marked as injected; they share the crop's original receipt. All database registration keys are null. The private smoke harness currently requires its explicitly supplied fixture org and identity namespace; it is not a general tenant deployment entry point.

Bounds: two crop files, two lifetimes/attempts, four fixed operations per attempt, at most eight native predict API calls. Encoded input ≤2MiB, admitted BGR ≤8MiB per crop; existing adapter count/handle/evidence/publication bounds are unchanged. Admission elapsed window180s starts before private Redis startup, after artifact inspection. Actual host elapsed73.981s; supervisor elapsed82.724s including imports/monitoring. Native calls are not forcibly cancelled at a wall-clock deadline. Runtime/library allocations and internal warmup/kernel forwards are not separately bounded/counted as API calls or process RSS.

Actual calls: **whole OCR2, marker2, left OCR2, right OCR2**, all successful native operations; initialization/invocation/decode failures0, not-started0, unknown/waiting0. Both Plate outcomes were truthfully **unreadable**. No fallback existed. Each eligible lifetime produced exactly one natural-v1 delivery, with Manufacturer unsupported omission. The same immutable scalar snapshot fed direct construction and Redis; reference construction performed zero recognition calls.

| Lifetime | Original receipt UTC | Direct/Redis canonical SHA256 | Outcome |
|---|---|---|---|
| 1 | 2026-11-04T12:11:18.808829Z | `8233be8c4789b0c4b8ce8ec055fac0ff29d82683c85f76f59be341f3e5e64590` | unreadable |
| 2 | 2026-11-04T12:12:24.164127Z | `3fe04eb04a0845532d4ece4f7397673def2511953d6aedeb4bce4bd3a19d1379` | unreadable |

Full sanitized receipts retain fact IDs, per-fact timestamps, model identities, operation outcomes, input hashes, dependency lineage, omissions and equal-byte assertions. Raw OCR text, bundle bodies and pixels are not exported. All facts retained the supporting original receipt; publication/model completion did not replace it. Four publish API attempts included an identical confirmed retry per crop; only two Plate evidence entries were required, with no further recognition. Each crop acknowledged release exactly once; both model runtimes closed; no unconfirmed evidence remained.

Actual Redis 8.2.11 binary `/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`, SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`. Private port0/loopback, 0700 Unix socket/directory, save empty, appendonly no, maxmemory64MiB/noeviction, one database. Normal worker leases: Colour2000ms, Plate/Aggregator Plate60000ms. Three separate metadata worker processes and the private server all exited0. Supervisor exit0; all owned PIDs absent, no supervisor termination intervention; `/tmp/vehicle-redis-ll20q5pu` and socket removed. No shared service was connected. Worker crash recovery is distinct from Engine crash and Redis server restart/power-loss durability.

## Tests actually executed, and historical evidence

Injected native runtimes ran before actual initialization and tested artifact mismatch, real loaded-identity mismatch, BGR shape/bytes/hash/immutability, strict installed response decoding, marker scale, exact dependency crops in both orders, ineligible geometry, initialization/invocation/decode failure, timeout/unknown retirement, cleanup retry and publication no-repeat. Existing36 adapter tests retain the three reviewed fixes. Final focused run: **50 passed =14 new +36 existing**. Final broad boundary run: **254 passed**, including the same new/focused tests. Coverage overlaps.

Legacy suites executed in this task: Plate57, Colour33, classification24 passed; legacy provider paths were mocked. P1 fixtures122 passed, zero failed, with real jsonschema4.26.0/FormatChecker. Event checks: valid18/schema-reject24/application-reject16; normalization8/retry3/strict JSON4/vocabulary drift3 passed. Exact arrays/return codes/durations and log paths are in `checks.json`, `commands.json`, `validation-summary.json`. Boundary/focused commands use `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest` followed by the explicit modules recorded there. Final boundary log is `release-boundaries.log`; initial broad253 and intermediate254 runs are development evidence, not extra unique coverage.

An intermediate crop-host reference test found missing injected Colour quality before real execution; it was corrected to explicitly injected scalar quality. Earlier passing legacy/source checks are not mislabelled as model runs. Final manifest immutability and zero-invocation smoke guards were tested after the actual smoke and the captured real receipt revalidated offline against them. All actual bound recognizer implementation/artifacts/model identities remained unchanged; recognition was not rerun for reference.

Fresh existing native adapter regression command:

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_engine_plate_native_audit.py scripts/vehicle_engine_plate_integration.py /home/intern/part-time/aziz/plate-real-executor-validation/native-regression
```

**Five methods passed in139.470s, no skips**, including all eight actual worker-kill boundaries, ACK/reclaim, parity, lost replies, duplicate/no-reexecution and retirement. This separate run used **53 injected callbacks, zero real model calls**, 15 parity receipts, 46 owned children all reaped, and its private directory/socket removed. It must not be conflated with the eight real calls/two receipts above. Earlier native repair baseline26-method results are historical, not re-executed as26 here.

`git diff --check`, scoped syntax/whitespace/local links, protected artifact hashes, ZIP CRC and source/evidence hashes are verified during packaging. Patch replay checks/applies against the reviewed native repair ZIP in a private extraction, compares all213 source files byte-for-byte and executes **92 focused local/adapter/transport/lease tests** from the extraction. That is dependency verification, not additional unique coverage. No models run from the extraction.

## Review artifact and limits

Review ZIP `/home/intern/part-time/aziz/vehicle_plate_real_executor_review.zip` contains the exact12-path follow-up patch, required213 local dependencies, source/evidence SHA256 manifests, initial/final status with attribution, sanitized execution receipts, actual commands, runtime/model fingerprints, cleanup proofs and patch replay verification. Earlier review evidence is nested and labelled historical. Models, crop/media assets, credentials/.env, environments, databases, caches, binaries and supervisor SQL are excluded. Baseline ZIP is unchanged.

Remaining gaps: fixed smoke plans are not Plate's production follow-up loop; native runtime retirement relies on the explicit synchronous CPU API profile; no hard cancellation guarantee; no Engine crash/outbox recovery, trace persistence, MP4 detector/tracker/association integration, RTSP, database consumer, PostgreSQL or server power-loss durability. Readability and production readiness are not established by this one-unique-image smoke. Full MP4 integration follows after review. No SQLite/Docker, installs/downloads, schema deployment, historical fixture/SQL edits, commits or pushes occurred.

## Follow-up: stage binding and failed-initialization cleanup — 2026-11-04

The two reproduced findings are fixed. This section records the current follow-up; the actual real smoke and results above remain historical evidence. Immediate baseline is `vehicle_plate_real_executor_review.zip`, SHA256 `9acda9ae684c71751fc92d9832fd16f0b61f47d43d5f58852b2b47e27fa601bd`, retained unchanged. Exactly five existing source/report files change: `vehicle/common/engine_plate_artifacts.py`, `vehicle/common/engine_plate.py`, `vehicle/common/engine_plate_local.py`, `vehicle/common/tests/test_plate_local_executors.py`, and this report. The other208 packaged dependencies are byte-identical. Applicable instruction search found no AGENTS.md. Initial/final git status preserves unrelated changes, including pre-existing tracked/untracked Python caches; no cache files enter the patch or archive.

### Regressions first and scoped repairs

Added five regression methods before implementation changes. The unfixed implementation produced **8 failing assertions and 2 errors across5 methods** (including six OCR stage-swap subcases). The errors expose the absent explicit kind field. Logs and exact commands are retained; these are expected red reproductions, not successful checks. The partial-cleanup test was then strengthened to include another failed explicit close retry before success.

`LocalExecutor(executor_id, artifact, kind, invoke, decode)` now requires a frozen explicit `kind` consistent with its model task. The factory's existing `LocalPlateBinding.executor(name,kind)` signature is unchanged. Adapter admission compares the descriptor kind with each operation before mutating its ledger or taking crop ownership, raising `EXECUTOR_STAGE_CONFLICT` on mismatch. This rejects all six whole/left/right OCR stage swaps even though each uses the same OCR task/model. Marker/task checks remain intact. InjectedExecutor and legacy entry points are unchanged.

At invocation, the captured factory kind is compared with `request.plan.kind` before input reconstruction, initialization or prediction. Mismatch is known-not-started with no attributed model and explicit retirement; the adapter latches it as a blocker, so no final publication can contain mislabelled regional evidence. A regression also forges descriptor metadata around a captured whole callback after admission: the callback still refuses a regional request, performs no left prediction and blocks publication. Correct whole/left/right wrappers still share one OCR runtime: one initialization, exactly one prediction for each bound stage, in either regional order.

The binding now owns `self.runtime` **before** initialization and marks it unavailable until initialization succeeds. If initialization raises and immediate cleanup also raises, the partial object remains retained with `cleanup_pending=True`, `closed=False`. Further operations fail known-not-started with `RUNTIME_CLEANUP_PENDING` before any new allocation/initialization/prediction, so no resource can be overwritten or used half-initialized. Both initialization and cleanup failure phases are recorded without exception bodies.

`close()` keeps that object and pending state across further close failures. Only an acknowledged successful close clears pending and marks the binding closed; repeat close does nothing. Successful immediate cleanup after a failed initializer still clears the object, preserving the old retry behavior. Failed ordinary model close also quarantines its retained runtime, preventing use during an unresolved cleanup. Active/unknown native work still rejects close through the existing retirement guard. The new partial-resource regression verifies one factory call, one failed initialization, zero predictions, failed immediate cleanup, another failed close, then successful/idempotent cleanup. Existing successful-model cleanup retry coverage remains.

No policies, schemas, native algorithms, crop transforms, decoder, lease defaults, broker/journal/fencing scripts or historical fixtures changed. The reviewed original-binding, journal-org, closure-receipt, timeout/retirement, publication retry and immutable-hash protections are preserved and exercised by the existing36 adapter tests. No MP4 or transport feature expansion was added.

### Existing real-smoke applicability; no repeated inference

**No actual models were initialized or executed in this follow-up.** The previous smoke's8 real CPU predict API calls and two unreadable parity receipts remain valid historical successful-path evidence. Its recorded stage lineage is correctly matched, all initialization/cleanup phases succeeded, and both models/crops were released. Thus neither reproduced bad path occurred in that smoke.

Static comparison against the reviewed ZIP verifies identical ASTs for `NativeCPU`, `artifact`, `marker_tools` and `raw_ocr`, plus byte-identical BGR/regional-cropping and post-initialization preparation/invocation/decode blocks. Model weights, decoder/config files, runtime versions and parameters are unchanged. New tests exercise stage rejection, resource retention and valid shared-runtime execution without repeating expensive real inference.

The local binding module is part of each content-addressed artifact identity. Its guard changes therefore produce **new model fingerprints**. Old model IDs, bundle hashes and receipts are not rewritten or presented as fresh proof for that fingerprint. `real-smoke-applicability.json` records the old/current identities, unchanged native paths, artifact hashes and precise applicability statement. A future real run must use the new identities; this follow-up does not claim a new real-smoke execution. This preserves immutable prior evidence and avoids a needless inference repeat for changes limited to descriptor checks and failure paths.

### Fresh executed checks

Existing `/home/intern/.venv/bin/python`, Python3.11.15, real jsonschema4.26.0 with Draft2020-12 FormatChecker, redis-py7.4.0. No packages installed. All pure commands use `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`:

- `-m unittest vehicle.common.tests.test_plate_local_executors vehicle.common.tests.test_engine_plate -q`: **55 passed** (19 local binding tests, including5 added, plus36 adapter tests).
- `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_redis_plate vehicle.common.tests.test_engine_plate vehicle.common.tests.test_redis_leases vehicle.common.tests.test_plate_local_executors -q`: **259 passed**, including the above55; overlapping coverage.
- `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q`: **57 passed**, providers mocked.
- `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q`: **33 passed**.
- `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q`: **24 passed**.
- `contracts/db1/validate_fixtures.py`: **122 passed, zero failed**.
- `contracts/validate_examples.py`: valid18/schema-reject24/application-reject16; normalization8/retry3/strict JSON4/vocabulary drift3, all passed.

Exact command arrays, return codes and durations are in `_review/local_executor_followup_evidence/checks.json` and `execution-commands.json`; corresponding logs distinguish red reproductions and green results. Fresh subprocess import protection is included in the existing adapter checks. No SQLite/database execution was run.

Fresh native command:

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_engine_plate_native_audit.py scripts/vehicle_engine_plate_integration.py /home/intern/part-time/aziz/plate-local-followup-validation/native-regression
```

**All5 methods passed in129.739s; no skips.** Includes all8 actual Plate/Aggregator worker-kill boundaries, reclaim/repair/ACK, duplicate/no-reinference, publication failures/lost replies, direct parity and late retirement. The native harness uses explicitly injected recognition (53 callbacks, zero actual model calls) and real Redis; 15 exact parity receipts. This is separate from the historical actual model smoke.

Existing Redis8.2.11 binary SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`. Settings: private port0/loopback, 0700 Unix socket/directory, save empty, appendonly no, 64MiB/noeviction, one database. Existing normal leases Colour2000ms, Plate/Aggregator Plate60000ms and explicit post-death test-clock handling are unchanged. All46 owned server/worker children were reaped; `/tmp/vehicle-redis-9rcinjhn` and socket removed. Only task-owned processes were stopped. Engine process-crash and Redis restart/power-loss durability remain unproven/separate.

### Review package and remaining scope

`/home/intern/part-time/aziz/vehicle_plate_real_executor_followup_review.zip` contains the exact5-file `_review/local_executor_followup.patch` against the reviewed real-executor ZIP, all213 source/schema/test/document dependencies, source/evidence SHA256 manifests, initial/final working-tree attribution, sanitized red/green evidence, native cleanup and real-smoke applicability proof. Replay checks/applies the patch in a private baseline extraction, compares all213 source files byte-for-byte and executes97 focused local/adapter/transport/lease tests there; these are package dependency verification, not extra unique coverage. Prior review evidence is explicitly historical. ZIP CRC, paths, local links, source/patch/evidence hashes and `git diff --check` are verified. Models, crop/media assets, credentials/.env, environments, databases, caches, binaries and supervisor SQL are excluded. Baseline ZIP is unchanged.

No real inference repeat, MP4/video/RTSP expansion, providers, database work, installations/downloads, schema deployment, commits or pushes. Fixed smoke plans remain distinct from production follow-up decisions, and Engine crash recovery remains unsupported. Full MP4 integration follows review.
