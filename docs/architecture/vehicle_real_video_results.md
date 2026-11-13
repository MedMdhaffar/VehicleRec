# Bounded real-video integration — 2026-11-03

The final-code smoke passed: **one local MP4 decode pass, 7 detector forwards, 63 local HSV/quality measurements, 9 closed lifetimes and 9 unique natural DB1 deliveries**. All nine in-memory and Redis bundle byte strings/hashes matched exactly. Plate is disabled and Manufacturer unsupported. No database or trace connection, provider, installation, download, RTSP, Plate/manufacturer inference, SQL/fixture change, commit or push was used. The result establishes bounded local parity, not recognition accuracy, real-time throughput or production readiness.

## Source changes and ownership

| File | Ownership / public interface |
|---|---|
| `vehicle/common/real_video/input.py` | `MP4Input` owns the only VideoCapture open/read/release; `DecodedFrame` pairs the pixels with the original immutable FrameReceipt |
| `vehicle/common/real_video/engine.py` | `Engine.process(packet)` invokes the existing detector/tracker, VehiclePipeline/lifetimes, local crop quality and HSV; `ClosedScalars` freezes class and Colour evidence; pending evidence is ACKed explicitly only after confirmed publication |
| `vehicle/common/real_video/host.py` | `run_capture`, `Publisher` and `reference_bundle` implement bounded orchestration, retained publication plans and the immutable scalar tee |
| `vehicle/common/real_video/local.py` | Explicit offline local artifact loader, content/config/code identities, model forward/call instrumentation and release; no model initialization on import |
| `vehicle/common/real_video/runtime.py` | Private native Redis and two separate existing metadata-worker processes; explicit owned-process cleanup |
| `vehicle/common/real_video/__init__.py` | Import-safe package marker |
| `scripts/vehicle_real_video_smoke.py` | Explicit bounded real-video runner, exact delivery comparison, artifacts/report/failure evidence |
| `vehicle/common/tests/test_real_video.py` | Eight injected tests for EOF, stop/time/frame bounds, receipt identity/order, publication failure/retry, no provider route, bounded failure and Aggregator-only consensus |
| `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py` | Add `class_consensus_enabled=True`; legacy default behavior remains. New Engine explicitly sets false and emits original per-frame class evidence to Aggregator |
| Five existing architecture documents | Record this bounded implementation stage and link to this report |
| `docs/architecture/vehicle_real_video_results.md` | This command/result/limitation report |

Applicable repository/ancestor instruction paths were checked; no AGENTS.md was found. Git status was inspected first. Pre-existing source edits and untracked work remain preserved. The previous verified native review archive (SHA256 `8432fa62357d6a07ef7f288ed3d0a00e62bf2d18a2c4da24f6d56eaba955a556`) is the exact follow-up baseline. Other existing source/schema/policy/fixture bytes are unchanged; no broad architecture audit was repeated.

## Input, execution and completeness

Input opens the existing local MP4 once; the same capture supplies FPS/size/declared-frame metadata and every frame. A paired UTC/monotonic receipt is sampled immediately after each successful decode and before Engine processing; PTS is read separately from that decoder. Engine accepts those exact receipt objects through the existing ordered VehiclePipeline interface and never opens/reopens a source. File-byte hashing for fingerprints is separate from decoding; it is not a second decode/inference pass.

Engine uses the existing local YOLO vehicle model, BYTETracker, lifetime allocator/closure behavior, confidence/label normalization, enhanced crop-quality calculation and HSV multi-ROI extraction. All pixels/copies and the loaded model remain local to Input/Engine, and no crop bytes enter messages or parity artifacts. Whole/colour metadata workers do not import inference runtimes. Source FPS (25 Hz here) configures BYTETracker; it is not an achieved processing rate.

The new path skips legacy profile consensus via the opt-out. Aggregator alone computes canonical vehicle-class consensus. Colour alone applies canonical scalar Colour policy. HSV extraction is pinned to multi ROI, no illumination normalization, adaptive neutral mode, HSV features; quality is explicitly enhanced. No provider interface exists in Engine, and offline/autoinstall/provider environment flags are pinned before inference imports (`YOLO_OFFLINE=true`, `YOLO_AUTOINSTALL=false`, `COLOR_AGENT_ENABLED=false`). Legacy provider defaults cannot enable a route.

Every original tracked row contributes a ClassSample and every executed valid crop contributes its scalar ColourMeasurement, including original attempt ID, receipt, confidence, quality and versioned algorithm identity. No class samples/attempts/provenance are silently dropped to satisfy bounds. The host admits at most 20 frames (configured lower for the smoke), Engine bounds its ledger at 20 observations per lifetime and 64 total lifetimes, and the existing codec/journal byte/count bounds still apply. Invalid crops, processing errors or bound exhaustion latch a blocker and prevent constructing/publishing a misleading final bundle. A failed/partial Engine record is diagnostic evidence, not an eligible completion.

At closure, `ClosedScalars` is frozen and offered to two paths: the existing in-memory Colour/Aggregator boundary and the existing Redis complete-batch/lifecycle contracts. The same evidence supplies both. No reference decoder, model invocation, image extraction or replacement timestamp is created. Publisher retains the exact encoded plan and explicit stream IDs, reuses the validated PRODUCE script and retries identical bytes. The complete batch enumerates all original attempt IDs. Plate disabled and Manufacturer unsupported produce the two truthful omission records; neither domain executes a model.

## Explicit unregistered context and provenance

Trusted local smoke org: `2f4a997e-2cda-4c82-b198-dab670e1cc1f`; camera: `local-mp4-vehicle-video`; timezone: Africa/Tunis. These are explicit local routing labels for the private test, not claims of production registration or message-derived authorization. RunContext is development/file with **all SQL keys absent**, synthetic=false and input-SHA provenance. No camera/run/model/database key is fabricated or resolved.

Detector identity is `local-yolo26-sha256:<actual weights hash>`, version 1. HSV and quality names contain the actual code/config/package execution digest; version 1 denotes this local content-identity protocol, not a fabricated database catalogue version. The report also records actual checkpoint/library version, class names, detector confidence/normalization settings, source files, tracker YAML and pinned HSV/quality modes. The final execution fingerprint is `25c95ee9aaa3f34c25ae58a6878c94e6955aaea5e8543ed3c1fd715ca7ff0de7`. Host filesystem paths are recorded but excluded from the digest material.

## Exact final smoke command

From `/home/intern/part-time/aziz/pipeline`:

```bash
timeout --signal=TERM --kill-after=15s 150s env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_real_video_smoke.py --video vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/video.mp4 --weights vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/models/vehicle_yolo26.pt --tracker vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server --output /home/intern/part-time/aziz/real-video-20261103-cpu-8f-final --org 2f4a997e-2cda-4c82-b198-dab670e1cc1f --camera local-mp4-vehicle-video --timezone Africa/Tunis --run-id real-video-20261103-cpu-8f-final --namespace video_smoke_20261103_final --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --device cpu --threads 2 --max-frames 8 --max-seconds 60 --delivery-timeout 30 > /home/intern/part-time/aziz/real-video-smoke-final-launch.log 2>&1
```

Exit **0**. Upper bounds: 8 frames, 60-second **frame-admission** window, 30-second delivery wait and an outer 150-second wall timeout with 15-second forced-kill grace. The admission check is cooperative at frame boundaries; it cannot preempt an in-flight native decode/inference call. Completing that frame and finalizing/publishing closed evidence can run past 60 seconds. This occurred here: **7 frames**, stop reason `time_limit`, capture/finalization elapsed **62.06085388804786 seconds**. The outer timeout did not fire. The script requires a new output directory and has no implicit destination/model/tenant defaults.

Private Redis 8.2.11 was launched on a unique 0700 directory/socket, TCP port 0, save="", appendonly=no, databases=1, maxmemory=67108864 and noeviction. Colour and Aggregator were distinct child processes running the validated worker commands with explicit socket, namespace, org, identity namespace and consumer names. Their original entry points/codec/journal were reused unchanged.

## Input/model fingerprints and placement

| Artifact | Bytes / SHA256 |
|---|---|
| Existing vehicle-service `video.mp4` (1920×1080, declared 1501 frames, 25 FPS) | 35,662,657 / `5d3ab09ea6c2fd74e0c0067ffd8edf29d9a78037f6476f972f2d47a287f48077` |
| Existing `models/vehicle_yolo26.pt` | 44,056,921 / `f0574db3428cf12af8c07c40aaa6e042f9cf830f1b22d2ee9ea4dcd370acfc29` |
| Existing `trackers/bytetrack_vehicles.yaml` | 147 / `0629767f781ccae6a81f23c6df04df325912f6d31fd28bc226b5e4f82d41103d` |
| Reused native Redis executable | `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393` |

These artifacts and all final report source fingerprints were rehashed after execution and matched. Libraries actually available: ultralytics 8.4.46 (checkpoint also reports 8.4.46), torch 2.11.0+cu128, opencv-python 4.13.0.92, NumPy 2.3.5, lap 0.5.13, redis-py 7.4.0 and real jsonschema 4.26.0. No packages or weights were downloaded/installed.

Actual placement: requested/resolved CPU; all model parameter devices `cpu`; torch threads 2 and OpenCV reported threads 1. CUDA was available with an NVIDIA A100-SXM4-80GB MIG 1g.10gb, but no model was placed/executed there. Detector predict adapter calls **7**, post-load forward-hook calls **7**, quality calls **63**, HSV calls **63**. Plate/manufacturer/provider execution counts **0**. The same input was not inferred a second time for the reference path.

## Per-lifetime parity

All nine lifetimes observed frames 0..6 with 7 original class samples and 7 original Colour attempts each. The shared first receipt is `2026-11-03T14:49:29.421249Z`, monotonic `1315633.405363813`, PTS 0; last receipt is `2026-11-03T14:50:21.292969Z`, monotonic `1315685.277082632`, PTS 0.24. These are host replay receipts, not source recording UTC. Closure is service_stop due to the bounded time window, not fabricated EOF.

| Service track | Canonical bundle SHA256 (both paths) |
|---|---|
| 1 | `d89b607f36aa1d19be2ac5fa5fb9dbeba793f7bda51b53876a3cf9980a114e15` |
| 2 | `8f6957ad476fdfb9e080e41195de164c215ba44b7076855ba9062a48b3f46215` |
| 3 | `4f05a3e153589b7a8ede51d4c7be98245a3d10788befe63b8fb15c4d1c198eb3` |
| 4 | `26dce343fc05f8e84fa952c11fa7dac78b2050b72bb94809264b239974e6f435` |
| 5 | `363424dffb9a819f7d465787095bb9d3615f987a7c9612b7d35c342b09ea1478` |
| 6 | `7f0e947d8604241b8d62cda9cd881515808ce6c7c9f932b1949686923c905564` |
| 7 | `055a7a705ca19ddf531367df477370626cc01bcd1218c53382719269165fa6f5` |
| 8 | `bfe657789fdfe604d930c669cf70989e1256fa92a418bfda35b0471151b04462` |
| 9 | `4a2f95bb8633881240c9de9ef1f7c69a93fe1460f486def0ba83c20e3683a6c0` |

Each delivery contains exactly parent and Colour facts, with truthful Plate disabled/Manufacturer unsupported omissions. Canonical equality compares the entire bundle, including fact IDs, per-fact timestamps, scalar confidence/quality values, original attempt provenance and omissions. `report.json` provides per-lifetime fact IDs/timestamps, provenance hashes and stream receipts; `closed-*.json`, `batch-*.json`, `reference-*.json`, `delivery-*.json` and `captured-closed-evidence.json` provide exact original data. There are exactly nine distinct logical final deliveries, zero metadata diagnostics and zero unsent closed lifetimes.

## Intermediate results and fresh checks

An initial smoke using `--max-frames 12 --max-seconds 60`, output `/home/intern/part-time/aziz/real-video-20261103-cpu-12f`, run-id `real-video-20261103-cpu-12f-v1` and namespace `video_smoke_20261103` passed nine-lifetime parity with 12 detector forwards and 108 HSV/quality calls (66.57822770881467 seconds including finalization/publication). Review then identified that the reused legacy pipeline still computed unused profile class consensus. The default-compatible opt-out was added and explicitly tested, then the final-code smoke above was run. These are **two separate validation runs**, each one decode/inference pass; neither reruns inference for its reference. Total actual vehicle forwards across both runs: 19; total HSV/quality calls: 171 each. Intermediate artifacts are retained separately and are not claimed to fingerprint the final source.

No test failures occurred. An initial unit invocation accidentally imported the prior SliceTests class and discovered 24 tests (17 old + 7 new); that unused import was removed. The focused final suite is **8 new tests**, not 24 new tests. Subsequent tests passed with the class-consensus opt-out and failure blocker. The final smoke stopped by its time limit rather than reaching the 8-frame cap; this is recorded explicitly.

All following checks were executed now with `PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python` and real jsonschema 4.26.0:

| Exact arguments / check | Executed result |
|---|---|
| `-m unittest vehicle.common.tests.test_real_video -q` | **8 passed** |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video -q` | **142 passed** |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 passed**, including unchanged legacy consensus behavior |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | Valid 18, schema-reject 24, application-reject 16; normalization 8, retry 3, strict JSON 4 and drift 3 passed |
| Final evidence/fingerprint/owned-process verification | Passed; all 9 exact bundle pairs, code/model/input hashes and cleanup checked |
| Source AST/whitespace/local links, `git diff --check`, scoped archive/patch checks | Passed |

The eight injected cases cover EOF (no receipt generated for failed decode), frame cap, controlled stop, time cap, receipt-before-inference and shared identity, a second-publication exception retaining unACKed closures and identical retry without new inference, provider defaults forced irrelevant, explicit capacity blocker, model/capture cleanup, and Engine not invoking class consensus. Previous 11-method native Redis validation is historical and was not rerun for unchanged codec/workers/journal; the two real-video runs use real native Redis and separate metadata workers now.

## Failure retention, cleanup and limits

`Engine.pending` and its canonical closure owner are not drained on publication errors. Only a successful return from both idempotent producer publications permits explicit acknowledgment/release. Partial or uncertain replies retain the original encoded plan/stream IDs; an injected retry test compares all original arguments exactly. Host failure stops capture, closes active lifetimes with the actual error/stop cause, latches processing blockers where necessary, closes model/input resources and exports actual unsent/closed scalar evidence with a failed report. No synthetic result is substituted. This is bounded in-memory retention plus an explicit host failure artifact, not a durable automatic outbox or crash-recovery system.

Final cleanup: model reference/forward hook released, capture released, Colour PID 3466020, Aggregator PID 3466021 and Redis PID 3466016 all exited 0 and were independently absent from /proc afterwards. The private temporary socket/directory was removed; no live owned child remained. The preliminary run also reports all three owned children exited 0 and no surviving runtime directory. No unrelated process was stopped.

Capture/tracker/crop recovery after Engine crash is **not provided**. A hard crash can lose unaccepted RAM evidence; a new run/identity is required for a new replay. Do not infer Redis-server restart/power-loss durability, accuracy, concurrency/GPU safety, throughput, production authorization or production readiness. No RTSP, Plate recognition, manufacturer model, PostgreSQL delivery or Engine trace persistence is implemented. Trace integration remains separate subsequent work. EOF/control/failure tests are injected; the real bounded smoke stopped before physical EOF. Natural rows are not database commits.

## Scoped review package

`/home/intern/part-time/aziz/vehicle_real_video_review.zip` includes repository-relative changed files and review dependencies, exact `_review/real_video.patch` against the preceding verified archive, a manifest with hashes/attribution, and `_review/real_video_evidence/final/` containing the actual scalar messages, reference/delivery pairs, parity report and owned process logs. `_review/real_video_evidence/intermediate-12f/` clearly separates the earlier run. Legacy pipeline edits preceding this task remain attributed to their previous review baseline. Models, video, pixels/crops, settings directories, credentials/.env files, environments, caches, databases and SQL are excluded.
