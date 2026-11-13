# Redis publication ordering repair — 2026-11-04

Current milestone (2026-11-05): the opt-in Plate-owned progressive Redis request
loop and replaceable natural DB1 consumer are implemented. Native PostgreSQL
execution requires an explicitly configured disposable target and is currently
blocked. See [current results](vehicle_plate_requests_results.md) and the
[v2 state/stream contract](vehicle_plate_requests_v2.md). Earlier stage status and
execution counts below are historical; this is not a deployed architecture.

Current focused repair: passed. All current execution uses injected scalar/model evidence and private native Redis. Distributed Plate follow-up was not implemented. The original real-inference smoke below is historical and predates this patch; its source fingerprints and execution must not be attributed to the repaired source.

## Baseline and root cause

The immediate reviewed baseline is `/home/intern/part-time/aziz/vehicle_mp4_colour_plate_review.zip`, SHA256 `860435ffe869a9c2b792f11f33ba1e976d53f6dc34dc2c429049a6d08ac481e6`. All 222 manifested source files matched the workspace byte-for-byte before editing. Its README, patch instructions and manifest were read and retained in the new evidence. No applicable AGENTS.md was found. The original tracked/untracked working-tree changes remain; Git HEAD is not this review baseline.

`V1Publisher.prepare` used `stream_id=f'{sid}-0'`. Creation of service tracks 1 then 2 does not order their closure: close 2 first appended `2-0`, then close 1 attempted `1-0`, which Redis rejects as below the stream top. Four-frame historical smoke execution did not exercise that order. The actual baseline publisher reproduces this with an ordering-enforcing fake, before the production fix. The final test suite also reproduces it in a private extraction of the reviewed baseline.

## Admission, progress and resource ownership

Natural identity remains the original service lifetime/DB1 identity. Event and Plate batch identities are still generated exclusively from the original lifetime and evidence; transport IDs never enter their bytes or canonical bundle hashes.

Successful preparation admits a FIFO plan and assigns the next positive transport sequence (`1-0`, `2-0`, ...), shared across that plan's three input streams: `engine.lifecycle.plate`, `engine.evidence.colour`, and `engine.evidence.plate`. A vehicle 2 that closes first therefore receives transport `1-0`, and later vehicle 1 receives `2-0`; the natural vehicle IDs stay 2 and 1. Multiple plans can be prepared. Publishing a later admitted plan returns False without any Redis mutation until the FIFO head has confirmed all three inputs. Preparation order determines transport order, independently of vehicle creation order or attempted publication order.

Each immutable plan retains its exact serialized lifecycle/Colour/Plate bytes, assigned ID, reference bundle, original closed evidence and batch identity. Per-input progress skips confirmed lifecycle/Colour appends; the Plate adapter retains its existing confirmation. A failure before any append or between appends leaves the head admitted. Ambiguous replies retry the exact ID and bytes through the unchanged `PRODUCE` Lua script, whose XRANGE equality check reconciles an accepted append before another XADD. No XADD `*`, duplicate physical append, trimming or deletion is introduced.

The Plate adapter returns its sealed publication packet on identical retries, after validating org, blockers, batch and transport identity, without repeating Plate interpretation or serialization. Candidate/plan/closure mutators still reject changes after sealing. Publisher retries validate original closed evidence and sealed transcript before publication. Conflicts fail explicitly, with no Redis mutation. Inference, Colour selection and class consensus are not repeated by retries.

Byte and lifetime limits are checked before a new packet is sealed or an ID consumed. Rejected admission retains the original closed work, completed evidence and crops; it does not poison a subsequent retry with an unusable reserved ID. Plans/references remain retained under the existing cumulative scalar byte limit (up to 8 MiB) and lifetime limit (up to 16); queue and progress state have the same lifetime bound. Existing crop, operation and Engine pending bounds continue to apply, with no truncation. Confirmed plans are not silently discarded to admit more work.

After all inputs confirm, the FIFO entry is retired before cleanup, and handoff confirmation remains retained. Cleanup failure can therefore be retried without republishing or predicting; later transport admission is safe because all earlier IDs already exist on all three streams. Crop release and native retirement still use the adapter's existing rules. A nonempty cleanup result returns False, retaining Engine pending ownership until cleanup completes. Unconfirmed evidence is never automatically discarded. Failed cleanup remains visible to the existing Engine quarantine/cleanup error path.

The host scans every pending lifetime instead of repeatedly retrying one sid. This permits an earlier prepared FIFO head to make progress even if Engine pending iteration order differs. A nonempty unsuccessful scan sleeps 10 ms and checks an explicit `limits.max_seconds` handoff deadline (default 180 s, bounded by the existing 300 s profile). Expiry raises `PUBLICATION_WAITING` and retains recoverable evidence. Checks occur between scans; a scan can also spend up to the existing five-second drain per retained lifetime, bounded by 16 lifetimes. This wait is not a native cancellation deadline. Unfinished enabled evidence stays waiting; no terminal outcome, model identity or successful execution is fabricated.

Supported scope is one serial in-process V1Publisher owner for one fresh private namespace over one bounded Engine run. Sequence IDs are never reset or reused during that owner's lifetime, even after confirmations. There is no durable Engine outbox, Engine process-crash/restart recovery, coordination between independent publishers, or safe reuse of a nonempty namespace by a new publisher. Private Redis uses no eviction; external stream deletion/trimming is outside this ownership contract. Redis handoff/XACK is not PostgreSQL commit acknowledgment.

## Changed files

- `vehicle/common/real_video/plate_host.py`: transport allocation, FIFO admission, per-input confirmation, byte/lifetime preflight and bounded host waiting.
- `vehicle/common/engine_plate.py`: reuse immutable sealed publication on retry.
- `vehicle/common/tests/test_mp4_publication_order.py`: 11 focused methods, with a six-case append interruption matrix and observable parity/resource assertions.
- `vehicle/common/tests/test_mp4_colour_plate.py`: update retry assertion to require retry of the unconfirmed Colour append, while skipping confirmed lifecycle work.
- `vehicle/common/tests/test_mp4_colour_plate_redis_integration.py`: four new native methods alongside two existing combined methods; exact parity for both closure orders, reverse publication admission, and before/after failures for all three inputs.
- `docs/architecture/vehicle_mp4_colour_plate_results.md`: current repair report plus clearly separated historical evidence.

## Red and green execution

Evidence directory: `/home/intern/part-time/aziz/mp4-publication-order-validation`. `executed-commands.txt`, `checks.json`, `native-checks.json`, `native-final-check.json`, logs and per-suite audits record the exact commands, counts and outcomes. Counts below overlap; repeated checks are not extra unique coverage.

The pre-fix regression ran eight methods and reproduced decreasing-ID errors, FIFO overtaking and unbounded host retries. Initial development also exposed incorrect fixture assumptions about which vehicle owned a crop and about the injected monotonic clock; those assertions were corrected. The first unguarded host regression was terminated, then given a finite call guard so the baseline hot loop fails safely. Its one retained injected fixture directory was identified from its exact contents/time window and removed; see `red-interrupted-cleanup-final.json`.

The final 11-method regression against a private byte-exact baseline extraction produced the expected red result: 6 failures and 8 errors (including subtests), with the Redis ID-ordering exception preserved in `baseline-red-final.log`. The extraction was removed. The final production source passes all 47 adapter/ordering methods (36 existing adapter plus 11 ordering methods), including a guard proving retries do not repeat scalar semantic evaluation. Earlier 41-method combined/ordering and final 10-method ordering development runs also passed.

| Check | Executed outcome |
|---|---|
| Initial affected boundary suite, including eight ordering methods at that point | 300 passed |
| Final adapter and ordering suite | 47 passed |
| Legacy Plate compatibility | 57 passed |
| Colour compatibility | 33 passed |
| Classification compatibility | 24 passed |
| DB1 P1 synthetic fixtures | 122 checks passed |
| Contract examples | valid 18; schema-reject 24; application-reject 16; normalization 8; retry 3; strict JSON 4; vocabulary 3 passed |
| Final private patched-baseline extraction | 141 focused injected tests passed; see `replay.json` |
| Syntax/whitespace | AST parsing for all five changed Python files, scoped trailing-whitespace checks and `git diff --check` passed |

Interpreter discovered and reused: `/home/intern/.venv/bin/python` (Python 3.11.15). Existing native binary: `/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`, Redis 8.2.11, SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`. No packages, models or binaries were installed/downloaded.

## Native Redis validation and receipts

All 32 final relevant native methods passed, zero skips/errors/failures. Native suites ran sequentially with owned child servers and workers. Settings: port 0, private 0700 directory and Unix socket, one database, maxmemory 64 MiB, noeviction, save empty, appendonly no. Every case uses an exclusive bounded namespace. Normal Colour lease remains 2000 ms and Plate/AggregatorPlate 60000 ms; existing fencing and confirmed-dead-owner test-clock behavior are preserved. No live-owner lease was shortened to speed execution.

| Suite | Methods passed | Owned processes | Intentional SIGKILL exits | Cleanup |
|---|---:|---:|---:|---|
| native-scalar | 12 | 40 | 9 | passed |
| native-plate | 9 | 60 | 10 | passed |
| native-adapter | 5 | 46 | 8 | passed |
| native-combined-final | 6 | 38 | 0 | passed |

The combined suite contains ten two-lifetime namespaces: both closure orders, close-2-before-close-1, reverse preparation/publication admission, and six before/after append interruptions. In each namespace all three input streams contain exactly two physical entries with IDs `1-0`, `2-0`; there are exactly two final deliveries. The six interruption receipts record affected-stream attempted IDs `1-0`, `1-0`, `2-0`, and assert equality of the original/retry serialized bytes. `ordering-receipts.json` records every namespace, physical count, original natural vehicle, transport ID and complete canonical bundle parity. Native joins compare the full canonical bundle bytes, not just hashes or row counts.

| Original vehicle | Bundle SHA256, identical for both closure orders |
|---|---|
| 1 | `c2d67d85e2d377b248d113bb459a1c5e0ed038aa5d643df19bdd9369733e785e` |
| 2 | `1cb6764bec1b8a092586d850cb66cc93a25688cf289fb92287d765101acdc6b3` |

The both-orders native test also compares each vehicle's complete lifecycle, Colour and Plate wire bytes across closure orders. Natural identity stays associated with the original service track as transport order changes. Existing native methods retain lease fencing, partial Lua repair, ambiguous replies and worker-crash recovery coverage.

An initial concurrent native attempt hit bounded waits under contention and was interrupted. It is not counted as a passing suite; its partial logs and audits remain under `attempt-01-interrupted`. Its supervisor reaped every owned process; final independent verification confirms every temporary directory/socket removed. The first sequential combined six-method run passed before the sealed-packet retry optimization and final receipt instrumentation; `native-combined-final` reruns all six against final source. The scalar suite's audit notes an edit to the unexecuted combined-test receipt code during its run; all executed scalar/worker code and production sources were unchanged. The final combined run validates that test code, and the final Plate/adapter/combined audits bind unchanged final source fingerprints.

Final native cleanup is independently checked in `cleanup-final.json`: every recorded owned PID is absent and every owned temporary directory/socket is removed, including interrupted attempts. The standard final runs need no forced cleanup beyond their intentional worker-kill tests. Worker recovery establishes neither Redis-server restart/power-loss durability nor Engine process-crash recovery.

## Exact commands and review package

All commands ran from `/home/intern/part-time/aziz/pipeline` unless explicitly marked as private extracted replay. Full expanded commands are retained in `executed-commands.txt`. Main drivers and the final focused check were:

```bash
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mp4-publication-order-validation/reproduce_baseline.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mp4-publication-order-validation/run_checks.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_engine_plate vehicle.common.tests.test_mp4_publication_order -q
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mp4-publication-order-validation/run_native.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mp4-publication-order-validation/run_final_combined.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mp4-publication-order-validation/package_review.py
```

Follow-up ZIP: `/home/intern/part-time/aziz/vehicle_mp4_publication_order_review.zip`. It contains the exact six-path patch against the current reviewed MP4 ZIP, all 223 final source/schema/test/document dependencies (217 unchanged), baseline and final SHA256 fingerprints, manifests, current red/green logs, sanitized native receipts/timings, cleanup evidence and actual validation tools. The original baseline ZIP is unchanged. Historical real-smoke execution evidence stays in that baseline archive.

`package_review.py` applies `git apply --check` and `git apply` in a private extraction of the reviewed 222-file baseline, verifies all 223 final file bytes, runs 141 relevant injected tests from the patched extraction, removes it, then verifies unique safe ZIP paths, CRC and every archived content hash. Exact replay receipt is `replay.json`; external archive SHA256 and byte count are `package-result.json`. Packaging is not extra unique coverage or inference execution.

No unresolved blocker remains. Legacy v0 wire behavior, reviewed semantic policies and architecture ownership are preserved. No distributed Plate follow-up, schema/policy redesign, supervisor SQL/historical fixtures, PostgreSQL connections/writes, SQLite, Docker, provider/model execution/downloads, package installation, shared-service connections, commit or push occurred. Remaining separate work includes durable Engine outbox/restart recovery and distributed Plate follow-up; neither is claimed by this repair.

---

# Historical pre-repair MP4 milestone and real-inference evidence

This entire section records the earlier source and execution. It is not execution of the repaired source.

# Bounded real MP4: vehicle + Colour + Plate — 2026-11-04

The opt-in combined MP4 host executes one real frame/model pass and publishes original scalar evidence through separate Colour, Plate and Aggregator workers to Redis natural-v1. It preserves the reviewed local executor, adapter and transport policies. This is a bounded local validation, not a deployed architecture.

## Baseline and source scope

Immediate baseline `/home/intern/part-time/aziz/vehicle_plate_real_executor_followup_review.zip`, SHA256 `2e9ed57cfad33d50276f86dfa0178e9f4a0af67bc753f825bb53dc75cc233930`, resolved and retained unchanged. No applicable AGENTS.md was found in repository/ancestors. Initial git status and pre-existing tracked/untracked changes (including caches and earlier architecture work) are preserved. Git HEAD is not this review baseline. The review manifest distinguishes this slice from all unchanged earlier dependencies.

Changed/new paths:

- `vehicle/common/engine_plate.py`
- `vehicle/common/real_video/runtime.py`
- `vehicle/common/real_video/plate_local.py`
- `vehicle/common/real_video/plate_engine.py`
- `vehicle/common/real_video/plate_host.py`
- `vehicle/license_plate_service/run_video_pipeline/crop_geometry.py`
- `vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py`
- `vehicle/common/tests/test_mp4_colour_plate.py`
- `vehicle/common/tests/test_mp4_colour_plate_redis_integration.py`
- `scripts/vehicle_mp4_colour_plate_smoke.py`
- `scripts/vehicle_mp4_colour_plate_integration.py`
- `docs/architecture/vehicle_component_ownership.md`
- `docs/architecture/vehicle_data_visibility.md`
- `docs/architecture/vehicle_execution_and_recovery.md`
- `docs/architecture/vehicle_redis_contracts.md`
- `docs/architecture/vehicle_transition_plan.md`
- `docs/architecture/vehicle_mp4_colour_plate_results.md`

## Interfaces and authority

`MP4Input` remains the sole source opener/decoder. It captures one paired UTC/monotonic receipt immediately after each successful decode, before either detector; media PTS remains separate. Engine accepts `DecodedFrame` and never reopens the MP4. Existing Colour-only Input/Engine entry points and v0 defaults are preserved.

`CombinedEngine` composes the existing authoritative `VehiclePipeline` lifecycle owner and real HSV/quality extraction with `PlateLedger`. `NativePlateDetector` reuses existing Plate prediction, deduplication, ByteTrack and canonical association. Real class observations go to Aggregator consensus; no Engine consensus is added. `V1Publisher.prepare/publish` freezes lifecycle/Colour/Plate bytes, stable explicit stream IDs and direct canonical reference from exactly those scalars. Reference construction makes zero extra model calls and creates no new receipts.

Colour owns scalar selection. PlateMetadata owns normalization, parsing, dependency-consistent regional fusion, eligibility and attempt selection. Aggregator joins and validates canonical natural-v1; it does not select a second Colour/Plate winner. Redis workers/codecs/schema/journal/fencing are unchanged. org_id is explicit and partition-checked, not authorization. Manufacturer is unsupported. No provider, SQLite target, PostgreSQL, database-key registration, trace persistence or database delivery is added.

The versioned `fixed-local-smoke-plan-v1` declares whole OCR, marker and both regional operations on one logical crop attempt; regional stages require valid exact-generation marker/whole dependencies. Two-crop injected tests exercise both regional orders and one shared OCR runtime. This fixed orchestration is not a production Plate follow-up loop; that semantic decision remains Plate-owned.

## Source discrepancies resolved and scheduling

The reviewed MP4 path was Colour-only. Existing Plate geometry/quality helpers lived in a module importing provider infrastructure. They were extracted verbatim into provider-free `crop_geometry.py` and re-exported by the legacy prototype; policy logic was not duplicated. Legacy tests verify compatibility.

Installed ByteTrack uses process-global `BaseTrack._count`, and lazy construction resets it. `TrackerIDs` saves/restores the Plate counter around constructor/update under the shared lane, protecting authoritative vehicle IDs. An installed-ByteTrack regression uses fake boxes, not model inference.

`engine_plate.native_access` uses the SAME native gate/readiness state as the reviewed adapter for synchronous CPU detector init/predict/close. Recognition uses the existing adapter retirement acknowledgments. Detector work refuses a busy/unretired lane. `NativeAudit` records public API entry/retirement, not arbitrary library internal kernels. Unknown retirement pins work; a timeout, returned Future or source stop never proves cancellation. No changes to stage-kind checking, initialization quarantine, immutable crop binding, tenant checking, receipt closure checks or journal fences were made.

## Geometry, admission and limits

Plate inference uses the same full decoded frame once per frame. Original detector integer rectangles are associated in frame space through the canonical geometry policy. Tracker boxes use existing 0.18 padding only for tracking/matching; they do not replace original crop coordinates. Crop geometry truncates integer coordinates and clamps to image bounds, then copies original-resolution BGR pixels. ROI-origin mapping is covered by injected tests; the real host uses origin(0,0). Regional coordinates remain crop-relative and slice only the immutable original admitted pixels using the declared marker dependency.

Every retained candidate keeps identity, raw tracker/segment identity, original receipt, association, confidence, clipped frame rectangle, original dimensions, opaque reference/pixel SHA256 and nullable measured quality/algorithm. Raw rectangles remain in sanitized association metadata. Ambiguous/unassociated diagnostics use the legacy conservative projection convention with no selected parent and no recognition eligibility; projection is not inferred ownership. Untracked detections have no fabricated raw ID: they block affected lifetimes. Entirely unrepresentable geometry also blocks. Tiny representable crops remain rejected evidence. Selected best-per-segment crops use existing numeric quality; all scalar candidate observations survive, including superseded buffers. No lower-quality crop becomes another attempt merely to fill the plan.

Smoke limits (all enforced, no truncation): frames4, frame-admission window180s, maximum configurable window8frames/300s, lifetimes16, raw Plate rows16/frame and128 total, candidates16/lifetime, segments/attempts2/lifetime, attempts16 total, private crop handles16, crop8MiB/handle and32MiB retained, decoded frame32MiB, transient per-frame eligible crop data32MiB, total native public API calls80, retained prepared scalar/reference bytes8MiB. Fixed operations reserve worst-case four calls/attempt plus detector frame budget before admission. Existing base ledger permits20 class/Colour samples/lifetime; four frames stay below it. Existing codec/schema/adapter/journal caps also apply, rather than silently truncating provenance. Logical byte limits do not bound model/library RSS or transient internal allocations.

Redis is private port0, 0700 Unix socket/directory, one database, maxmemory64MiB/noeviction, save empty, appendonly no. Worker normal leases remain Colour2000ms, Plate/AggregatorPlate60000ms. Existing v0 Aggregator default2000ms is unchanged. Intentional fencing tests retain explicit expiry; confirmed-dead-child test-clock acceleration never expires a live/new owner. Existing v1 limits are reused (Plate batch6MiB/terminal8MiB, closure64KiB/delivery12MiB, retained state20MiB, pending intent24MiB+4096bytes, max stream256 entries and128 journal vehicles with256 events; see codec/journal constants). Frame admission stops are not native cancellation deadlines.

## Closure, retention and cleanup

EOF maps to end_of_input; frame/time/controlled stops map to service_stop with explicit trigger. Processing errors/clock quarantine retain truthful causes. Injected tests cover EOF, controlled and elapsed stop, capacity, pending and unknown work. Enabled unfinished work emits no final bundle; it is not fabricated unreadable/no_detection. A returned execution without retirement blocks publication. Known failures retain the actual observed model.

Completed closed evidence remains in Engine until all original explicit-ID publications are confirmed. A lost reply retries the identical plan/bytes/IDs without repeating recognition. Partial lifecycle/Colour publication does not erase the pending closed lifetime. Confirmed handoff allows idempotent crop release; release failures retry without republishing or predicting. Model cleanup uses the shared lane and retains reviewed partial-initializer quarantine. There is no durable Engine outbox or capture/tracker/crop recovery after Engine crash. Intermediate XACK means Redis handoff, not database acceptance.

## Fresh checks before real execution

- boundaries: **passed**, 290 tests, unittest 95.585s; command elapsed100.287s, exit0.
- plate-legacy: **passed**, 57 tests, unittest 0.582s; command elapsed2.119s, exit0.
- colour: **passed**, 33 tests, unittest 0.120s; command elapsed2.071s, exit0.
- classification: **passed**, 24 tests, unittest 0.591s; command elapsed0.756s, exit0.
- p1-fixtures: **passed**, 122 fixture checks; command elapsed6.522s, exit0.
- event-contracts: **passed**, valid18/schema-reject24/application-reject16; normalization8/retry3/strictJSON4/vocabulary3; command elapsed7.461s, exit0.

The initial 290 boundary tests include 31 new combined methods and all reviewed LocalExecutor kind, partial initializer cleanup/quarantine, admitted crop before/after read, foreign journal, immutable receipts, retry, retirement and lease regressions. Legacy suites use mocked provider paths where present. Real jsonschema4.26.0 with FormatChecker is used, not a stub. Metadata import guards block image/inference/provider/SQL modules. Combined Engine imports block inference/provider/SQL modules; legacy ROI legitimately imports OpenCV. An initial intentionally over-strict Engine import probe rejected that existing OpenCV import; it was not an inference load or a production failure, and is retained as diagnostic history.

Development history: initial17 new methods had three fixture/assertion errors (budget exhausted before the intended admission assertion, nonexistent test field, and an unrestored cleanup mock). Those were corrected; intermediate26 and final31 passed. These are development runs, not extra unique coverage. The check runner initially used the prior external evidence directory; fresh outputs were relocated and all eight previous files restored byte-for-byte from the unchanged reviewed ZIP. No source or baseline archive was overwritten. All failures/corrections remain in scoped evidence.

Native suites ran sequentially on owned Redis8.2.11, recognition injected:

- native-scalar: **12 passed**, no skips/errors/failures; unittest139.783s, command144.618s. Owned children40; SIGKILL exits9; cleanup passed.
- native-plate: **9 passed**, no skips/errors/failures; unittest196.211s, command200.348s. Owned children60; SIGKILL exits10; cleanup passed.
- native-adapter: **5 passed**, no skips/errors/failures; unittest128.267s, command133.338s. Owned children46; SIGKILL exits8; cleanup passed.
- native-combined: **2 passed**, no skips/errors/failures; unittest17.523s, command20.961s. Owned children7; SIGKILL exits0; cleanup passed.

These include continuous stream fairness, pending/new fairness, repair priority, stale fencing, actual worker kills at all eight Plate/Aggregator boundaries, reclaim, partial Lua writes, lost replies, accepted-batch conflicts, invalid closure recovery, adapter exact parity/no-reexecution and the new combined lost-Colour-reply case. Real Redis tests use injected recognition and are separate from the real smoke below. No skipped integration test is counted as success. Worker-crash recovery does not establish Redis-server restart/power-loss durability.

Exact pre-smoke checks:

```bash
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_mp4_colour_plate_checks.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/check_mp4_imports.py
env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_mp4_native_suites.py
```

The packaged tools record/execute every expanded command. Native driver supplies `VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server` and runs scalar12, Plate9, adapter5, combined2 modules sequentially with sanitized lease/poll/decode/reduce/PREPARE/publication/repair timing, pending counts and coded worker stderr. Existing runnable harnesses remain available; new `scripts/vehicle_mp4_colour_plate_integration.py` runs the combined two methods explicitly (missing runtime exits2).

## Actual real smoke and reporting failure

Status **passed**. No component in this smoke was injected. Selected existing `vehicle/license_plate_service/run_video_pipeline/test_video.mp4` before inference, based on its documented historical preflight frame0 containing nearby vehicle/Plate geometry. The other inventoried vehicle MP4 was not substituted. No seek/replay/second decoder or threshold tuning occurred. Number of real attempts:2; no window/threshold adjustment. Attempt1 failed in final reporting (tuple-valued association metadata), losing final counter/parity report; its model invocation totals cannot be recovered and are explicitly unknown. It is not counted as success. Attempt2 repeats the identical four-frame window after the reporting-only fix; all successful-run counts below belong ONLY to attempt2. Supervisor elapsed182.890s.

Exact supervised command (the supervisor reaps only its descendants; it does not impose native cancellation):

```bash
/home/intern/.venv/bin/python /tmp/audit_real_plate_smoke.py /home/intern/part-time/aziz/mp4-colour-plate-validation/real-02-supervisor /home/intern/.venv/bin/python scripts/vehicle_mp4_colour_plate_smoke.py --video /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/test_video.mp4 --vehicle-weights /home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/models/vehicle_yolo26.pt --vehicle-tracker /home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml --plate-weights /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/best.pt --plate-tracker /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/trackers/bytetrack_plates.yaml --ocr-dir /home/intern/.paddlex/official_models/PP-OCRv6_medium_rec --marker /home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/tounes_word_detector.pt --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server --output /home/intern/part-time/aziz/mp4-colour-plate-validation/real-02 --org 12345678-1234-4234-8234-123456789012 --camera bounded-local-plate-mp4 --timezone Africa/Tunis --run-id mp4-colour-plate-20261104-02 --namespace mp4cp-20261104-02 --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --device cpu --threads 1 --max-frames 4 --admission-seconds 180 --delivery-timeout 30
```

Input metadata: `{"declared_frames": 271.0, "fps": 25.0, "height": 1080.0, "width": 1920.0}`.

Observed capture: `{"decoded_frames": 4, "elapsed_seconds": 157.64367771497928, "reason": "frame_limit"}`.

Counts: `{"closed_lifetimes": 2, "colour_measurements": 8, "colour_quality_measurements": 8, "confirmed_plate_detections": 4, "crop_attempts": 1, "crop_replacements": 1, "decoded_frames": 4, "delivered_lifetimes": 2, "max_native_active": 1, "native_active": null, "pending_lifetimes": 0, "plate_forward_hooks": 4, "plate_predict_calls": 4, "plate_quality_measurements": 4, "publication_attempts": 8, "quarantined_lifetimes": 0, "raw_plate_detections": 4, "source_opens": 1, "suppressed_plate_duplicates": 0, "vehicle_forward_hooks": 4, "vehicle_predict_calls": 4, "wire_publications": 6}`.

native_api_calls: `{"left_ocr": 1, "marker": 1, "plate": 4, "right_ocr": 1, "vehicle": 4, "whole_ocr": 1}`.

recognition_invocations: `{"detect": {"marker": 1}, "ocr": {"left_ocr": 1, "right_ocr": 1, "whole_ocr": 1}}`.

recognition_failures: `{"detect": [], "ocr": []}`.

recognizer_initializations: `{"detect": 1, "ocr": 1}`.

admission_outcomes: `{"eligible": 4}`.

blockers: `{}`.

vehicle_placement: `{"checkpoint_ultralytics_version": "8.4.46", "cuda_available": true, "gpu_name": "NVIDIA A100-SXM4-80GB MIG 1g.10gb", "model_names": {"0": "bus", "1": "camper", "2": "car", "3": "cyclist", "4": "motorcycle", "5": "person", "6": "tractor", "7": "truck", "8": "van"}, "parameter_devices": ["cpu"], "requested": "cpu", "resolved": "cpu", "torch_threads": 1}`.

plate_placement: `{"checkpoint_version": "8.4.46", "device": "cpu", "model_names": {"0": "license_plate"}, "parameter_devices": ["cpu"], "torch_threads": 7, "tracker_frame_rate": 25.0}`.

recognizer_placements: `{"detect": {"checkpoint_metadata": {"date": "2026-06-18T17:21:12.638095", "version": "8.4.46"}, "device": "cpu", "parameter_devices": ["cpu"], "torch_cuda_available": true, "torch_threads": 1}, "ocr": {"cuda_compiled": false, "device": "cpu", "paddle_device": "cpu"}}`.

cleanup: `{"children": [{"name": "aggregator_plate", "pid": 446745.0, "returncode": 0}, {"name": "plate", "pid": 446744.0, "returncode": 0}, {"name": "colour", "pid": 446743.0, "returncode": 0}, {"name": "redis", "pid": 446739.0, "returncode": 0}], "live_pids": [], "temporary_directory_removed": true}`.

The source reporter now shares the existing immutable-tuple-to-JSON conversion before canonical serialization. Two reproducing regressions failed before that fix (2 errors), then all 33 combined tests passed in25.341s; the final 292 boundaries passed in78.984s (command85.911s) and the two combined native methods passed again in16.397s (command20.554s), with no skips, before attempt2. Exact post-fix commands/durations are in release-check.json/release-native-check.json, with red/green and failed-attempt evidence retained. No transport/policy/model behavior was changed to obtain this repeat.

Public prediction API counts are separate from measured YOLO forward hooks (warmup/internal forwards may differ); Paddle internal forwards are not measured. API/retirement metadata is sanitized; raw OCR text and full runtime payloads are not exported. Zero provider calls and zero reference model calls. Actual receipt UTC/monotonic/PTS values are in sanitized report.json.

| Lifetime | Bundle SHA256 | Plate outcome | Complete canonical bytes |
|---|---|---|---|
| 1 | `e9885486459b498a802443e881c849346aad28303e931e5c8c5a3201f8239f73` | unreadable | equal |
| 2 | `11ca5c0067a0ba25b453c7c394873b13d61e6a87f57ac2ebd887a36033089a2d` | omitted | equal |

Each parity receipt includes fact UUIDs, original per-fact timestamps, original attempt IDs, operation kind/model/outcome, omissions and SHA256 of full canonical Plate provenance. Equality compares the COMPLETE canonical bundle bytes/hash, including all provenance, not just a text or row count. One physical/logical final is required per eligible closed lifetime. A readable plate is not required. See real-02/report.json, associations.json and preflight.json for exact receipts/lineage; these contain no raw reading.

The successful run retained four eligible Plate candidates for lifetime1, selected the original frame2 crop as its single attempt, and released one superseded best buffer. Lifetime1 produced parent/Colour facts and a truthful unreadable Plate fact; lifetime2 produced parent/Colour facts and a Plate no_detection omission. Manufacturer was omitted as unsupported in both. All four recognition operations succeeded; no known failure, unknown outcome, unsettled operation, quarantine or metadata diagnostic was observed. The only selected crop was released exactly once after publication; retained handles/bytes and pending closed work were zero. Prepared packet/reference bytes (32111) stayed in memory for parity/retry comparison.

Lifetime1: fact receipts `[{"uid":"f57e013f-49b3-553b-afd4-ef080e383772","ts":"2026-11-04T14:24:46.365485Z"},{"uid":"b782f341-a1b2-5e1e-bfbe-09591dacd453","ts":"2026-11-04T14:26:25.654454Z"},{"uid":"106ae389-f9ef-5fdf-86bc-40fe0a4da872","ts":"2026-11-04T14:25:58.630079Z"}]`; original attempts `["parent-1-crop-1"]`; Plate provenance SHA256 `0c9ead14f1ce1d0045580b03748208d55d2c4a57eeff1f3fc86d658ee0d91e40`; omissions `[{"domain":"manufacturer","reason":"unsupported_capability"}]`.

Lifetime2: fact receipts `[{"uid":"34ab2ace-8265-55ab-b0b8-d794c1e9d1bf","ts":"2026-11-04T14:24:46.365485Z"},{"uid":"e3928348-318a-5c9d-8ca5-6eaa12473a28","ts":"2026-11-04T14:26:25.654454Z"}]`; original attempts `[]`; Plate provenance SHA256 `edf215dfb8e2faf2af968d0de9f31dd1378401645602bc4b00e36441ff235591`; omissions `[{"domain":"manufacturer","reason":"unsupported_capability"},{"domain":"plate","reason":"no_detection"}]`.

## Fresh asset and binding fingerprints

Interpreter `/home/intern/.venv/bin/python`, Python3.11.15. Requested native profile CPU/one thread; effective placements above. Torch CUDA availability is informational, not Paddle GPU support. Packages: `{"jsonschema": "4.26.0", "lap": "0.5.13", "numpy": "2.3.5", "opencv-python": "4.13.0.92", "paddleocr": "3.7.0", "paddlepaddle": "3.3.1", "paddlex": "3.7.1", "redis": "7.4.0", "torch": "2.11.0+cu128", "ultralytics": "8.4.46"}`.

All actual local files were rehashed before inference; recognizer content IDs were freshly computed after the reviewed fixes. No previous crop-smoke IDs/receipts were reused. Native Redis binary8.2.11 SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`, reused without installation/build.

| Local asset | Bytes | SHA256 |
|---|---:|---|
| `/home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/video.mp4` | 35662657 | `5d3ab09ea6c2fd74e0c0067ffd8edf29d9a78037f6476f972f2d47a287f48077` |
| `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/test_video.mp4` | 2381732 | `36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2` |
| `/home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/models/vehicle_yolo26.pt` | 44056921 | `f0574db3428cf12af8c07c40aaa6e042f9cf830f1b22d2ee9ea4dcd370acfc29` |
| `/home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml` | 147 | `0629767f781ccae6a81f23c6df04df325912f6d31fd28bc226b5e4f82d41103d` |
| `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/best.pt` | 20328389 | `d7a69b324a58e24223a48371f5a70a5e222357d0edaa47dd435bca7cc8c831a0` |
| `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/tounes_word_detector.pt` | 5371461 | `f8956bd78992661bf5a88905f355b48168c0e5d643c3f7635795cfc266d11293` |
| `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/trackers/bytetrack_plates.yaml` | 439 | `2cf1cb6791ed4b60eb380ee4ac589d990bc816e2af8025488f33cadbc4020ea3` |
| `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.yml` | 150580 | `991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129` |
| `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.json` | 221814 | `0b2e25e990bd072f1bf77d59d67d508bce6c4bd44af6624e0fb27d6da2cd00e8` |
| `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.pdiparams` | 76465087 | `1b01c79a914587933f615569e75de54f2e638ebb5d3f3b3c1b38c24ede8c7319` |
| `/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server` | 9386328 | `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393` |

Plate detector content ID: `local-detect-sha256:a36b448f70e604362fe892a5c38911b12ef919c693e5cc2497a07f2f70ab3653`.

detect content ID: `local-detect-sha256:a4fd6579c4608011503d5d71fb201ed25bec13d0845192e95bc86767e54a3ff3`.

ocr content ID: `local-ocr-sha256:8f6bad0678a614a4e1dcf5d00d0f4ce8913272fa7102d4b0df73e7378056c09f`.

The manifest/preflight material binds model files, local implementation/config/tracker hashes, runtime versions and parameters, not invented database/training registrations. Source SHA256 manifest and post-run verification accompany the package.

## Review package and remaining work

`/home/intern/part-time/aziz/vehicle_mp4_colour_plate_review.zip` contains the exact17-path patch against the reviewed follow-up, 222 required source/schema/test/document files, attribution of 205 unchanged dependencies, baseline/source/evidence SHA256 manifests, fresh sanitized checks/smoke/process evidence and exact command tools. Historical evidence is kept in its original unchanged baseline archive and inventoried by hashes rather than recursively exporting prior runtime payloads. Synthetic source fixtures remain synthetic. Models/media/crops, credentials/.env, environments, caches/databases/binaries and supervisor SQL are excluded.

Packaging checks apply the patch in a private baseline extraction, compare all 222 source bytes, run 130 extracted focused injected tests and verify unique safe paths/CRC/content hashes. Extracted tests are packaging verification, not extra unique coverage or inference. Archive SHA256 is reported externally to avoid self-referential hashing.

The first bounded next task is Plate-owned distributed semantic follow-up request/admission/recovery, preserving immutable crop/operation dependencies and native retirement. Other separate platform work remains Engine process-crash/outbox/trace persistence, replaceable database consumer, authorization, RTSP and production scheduling/capacity. No OCR accuracy, sustained or real-time throughput, production readiness, Redis power-loss durability or PostgreSQL delivery is claimed. No schema/policy/supervisor SQL/historical fixture edits, installs/downloads, commits or pushes were performed.
