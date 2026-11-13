# Engine-private Plate execution adapter results — 2026-11-04

Completed the bounded injected adapter path: explicit work plan → recorded executions → immutable reviewed Plate transcript → existing Redis Plate worker → Aggregator → natural-v1 delivery. Recognition is injected; Redis is native. Manufacturer remains unsupported. This is not real OCR integration or a deployed architecture.

## Changed files and baseline

New source: `vehicle/common/engine_plate.py`, `vehicle/common/engine_plate_injected.py`; tests: `vehicle/common/tests/test_engine_plate.py`, `vehicle/common/tests/test_engine_plate_redis_integration.py`; entry point: `scripts/vehicle_engine_plate_integration.py`. This report and the five architecture documents (`vehicle_component_ownership.md`, `vehicle_execution_and_recovery.md`, `vehicle_data_visibility.md`, `vehicle_redis_contracts.md`, `vehicle_transition_plan.md`) comprise the exact 11-file patch.

Immediate reviewed baseline: `vehicle_plate_redis_review.zip`, SHA256 `dde9b19d57401953ab3d287181bdafb8bed8683838cf37a08f0b0c0c04c803cb`. Existing schemas, policies, transport/worker/journal implementations, historical fixtures, legacy sources and unrelated work are unchanged against that baseline. Initial/final git status and per-file attribution accompany the ZIP; the repository already had substantial uncommitted work. The scoped patch is against the reviewed ZIP, not repository HEAD.

## Interfaces and ownership

`CropBinding` ties the original frozen `PlateCandidate` to a private handle and declared bytes. `PrivateCrop` supplies binding, read access, idempotent release and a release acknowledgment. Successful admission transfers ownership; rejected admission transfers nothing. Original associated, ambiguous and rejected candidate observations are retained through `observe_candidate`, even without executable crops. Receipt, identity, quality, geometry and lifetime checks reuse reviewed canonical helpers.

Frozen `CropPlan` / `OperationPlan` declare expected attempt, operation IDs, sequence, dependencies, kind and executor binding. The constructor requires all expected attempt IDs; admission sealing rejects missing declared work. These plans are explicitly tagged injected stand-ins, not hardcoded Plate follow-up logic. One crop’s whole/marker/regional work remains one attempt. Regional operations receive the exact declared whole/marker generations, either order; later marker geometry cannot substitute for an earlier dependency.

`InjectedExecutor` binds synthetic/injected model identity to invocation and scalar decoding. `ExecutionContext.started(actual_model)` records actual observed start, independently of configuration; changed attribution blocks publication. `retired()` explicitly confirms no further pixel use. `NativeRequest` exposes a read-only, contiguous BGR8 view only inside Engine; byte length and original SHA256 are checked before invocation. `ExecutionRecord` retains outcomes; `snapshot()` delegates semantic eligibility to reviewed `PlateMetadata`. Engine performs no text normalization, fusion, ranking or follow-up decisions.

Pending, known not-started, executed success, executed failure and unknown outcomes are distinct. Malformed output after a confirmed invocation becomes failed with observed model and no scalar invention. Failure/uncertainty without proof of start has no attributed model. Failed dependencies become not-started without invocation. Retrying an identical admitted plan returns retained records; changed plan/resource/binding conflicts.

`PlateExecutionAdapter` separates lifetime closure, detection completeness, admission completeness and execution settlement. Closure alone leaves pending work waiting. Stop prevents admission; it optionally drains admitted work with a bounded wait, or records unsubmitted work as not-started. Timeout does not establish cancellation. Explicit stop/uncertainty settlement may freeze a conservative terminal omission while active work remains pinned. Late results are retained privately without rewriting sealed evidence.

One process-wide adapter lane serializes invocation across instances. It remains charged until both worker return and explicit retirement; callable return, timeout and Future completion alone do not permit release. This lane does not yet arbitrate legacy vehicle detector/GPU calls. The host must bound adapter instance count.

`prepare_publication` validates through the existing codec and freezes one completed revision-0 payload, batch ID and explicit stream ID. `publish` uses existing PRODUCE reconciliation; missing/unexpected acknowledgment or connection failure leaves publication unconfirmed. Retries reuse bytes/IDs and never reinvoke recognition. Only the exact requested stream ID confirms handoff. `cleanup` releases confirmed evidence, or explicitly discarded stopped-host evidence, only when safe; acknowledged releases are never repeated, including exceptions after a release side effect. Unretired resources remain pinned. No local durable outbox is introduced.

## Explicit bounds

Per adapter: one lifetime; 16 observed candidates; 8 handles; 8 MiB per crop and 16 MiB total crop allocation; 16 attempts; 32 operations (at most 16 per attempt); 16 synthetic executor bindings; 8192 context string bytes; 4 MiB retained scalar reservation; one prepared publication up to 6 MiB. Admission reserves 131072 base bytes, 16384 per candidate, 32768 per attempt and 65536 per operation, including escaped scalar text/IDs/dependencies. Actual snapshot bytes are checked too. Native raw output is bounded to 65536 bytes; decoded OCR text to 4096 UTF-8 bytes. Drain timeout is limited to 0–5 seconds. Capacity rejects before admission and never silently truncates evidence. These are adapter-owned logical/resource bounds, not a bound on trusted callback memory or total process RSS.

## Fresh executed checks

Existing environment: `/home/intern/.venv/bin/python` (Python 3.11.15), real jsonschema 4.26.0 with Draft 2020-12 FormatChecker; redis-py 7.4.0. No packages installed. Exact command arrays, durations and logs are in `_review/engine_plate_evidence/checks.json` and matching logs.

All pure commands use `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python` followed by:

- `-m unittest vehicle.common.tests.test_engine_plate -q`: **30 passed**.
- `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_redis_plate vehicle.common.tests.test_engine_plate -q`: **230 passed**, including those 30 (overlapping coverage).
- `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q`: **57 passed**.
- `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q`: **33 passed**.
- `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q`: **24 passed**.
- `contracts/db1/validate_fixtures.py`: **122 passed, zero failed**.
- `contracts/validate_examples.py`: valid18; schema rejection24; application rejection16; normalization8; retry3; strict JSON4; vocabulary drift3, all passed.

Focused tests cover both regional orders, differing actual models and exact dependency generations, retry/conflict/no-repeat behavior, admission/crop/memory bounds, malformed decode, unknown versus not-started, closure/stop/pinned retirement, changed crop fingerprint, uncertain scheduler submission, original rejected evidence, publication confirmation and multi-resource cleanup retries. A fresh subprocess import test rejects heavy inference, SQL and client modules and passes. No SQLite/outbox/adapter database tests were run.

## Native Redis execution and evidence

Existing user-owned binary `/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`; no build/download/install this task. Version output: `Redis server v=8.2.11 sha=00000000:1 malloc=libc bits=64 build=d8a8c744cbac1a9`. Binary SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`.

Final exact audited command (helper source is included in the ZIP):

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_engine_plate_native_audit.py scripts/vehicle_engine_plate_integration.py /home/intern/part-time/aziz/engine-plate-validation/native-adapter > /home/intern/part-time/aziz/engine-plate-validation/native-adapter.log 2>&1
```

Portable direct invocation is the same environment followed by `scripts/vehicle_engine_plate_integration.py`; the helper instruments owned child cleanup, injected calls and observed parity receipts without changing production behavior.

Final run: **5 methods passed in 46.767 seconds, no skips**, UTC 2026-11-04 01:39:26.883355–01:40:14.949375 (Tunis 02:39:26–02:40:14). Separate Colour, Plate and Aggregator processes use only a private temporary native Redis instance. Server settings: port0, loopback bind, private 0700 directory/Unix socket, save empty, appendonly no, 64 MiB maxmemory, noeviction, one database. Final directory `/tmp/vehicle-redis-0__1kvh8` and socket were removed. Audit records 46 owned children: 36 exit0, eight actual SIGKILL crash injections, two SIGTERM teardown exits; all reaped, none left running. Only task-owned processes were stopped.

The five methods prove whole plus both regional orders; direct byte/hash parity and duplicate ACK without reinvocation; before-send connection failure and lost reply after Redis EVAL; retry-safe release failure; closed-pending stop/explicit uncertainty/late retirement; actual-start malformed decoder failure with dependent work not-started; and both Plate/Aggregator worker crash/reclaim at prepared, published, before_ack and acknowledged boundaries (eight kills).

Final audit recorded **53 injected callbacks, zero real model invocations**. Synthetic whole OCR v2, marker v1, left OCR v5, right OCR v9 identify the actual injected bindings. **15 parity receipts** record equal expected/actual SHA256 and canonical bytes, fact IDs, original per-fact timestamps and truthful omissions. Exact byte equality includes full provenance. See `native-adapter/parity-receipts.json` and `injected-invocations.json`; no pixels or raw image assets are exported.

Earlier current-task native development runs also passed: an initial unittest discovery accidentally included nine imported existing transport methods plus five new methods (**14 total**); this was corrected by module-qualified imports. A subsequent adapter-only five-method run passed before final receipt instrumentation. Their logs are labelled development evidence, not additional unique tests. Prior reviewed ZIP executions are historical and preserved separately. No skips or historical results are counted as final success.

## Review and limits

`vehicle_engine_plate_review.zip` preserves repository-relative source paths and includes all reviewed local dependencies, exact scoped patch, source/evidence SHA256 manifests, initial/final status, fresh commands/logs, native PID cleanup, receipts and replay verification. Packaging applies/checks the patch in a private baseline extraction, compares all 206 source bytes, and reruns 230 boundary plus 57 Plate legacy tests there; these are package verification, not additional unique coverage. Baseline ZIP remains unchanged. Credentials, environments, caches, databases, binary/model/media assets and supervisor SQL are excluded.

Engine process-crash recovery, capture/tracker recovery, persisted Engine traces, real OCR/model/device resource integration, distributed follow-up requests, Redis-server restart/power-loss durability, PostgreSQL and DB1 consumer delivery remain unsupported/unexecuted here. Retirement, scalar decoding and release acknowledgments are trusted injected executor/resource contracts; real bindings must prove these explicitly. No inference/provider/video/RTSP, database connections, Docker, installs/downloads, migrations, supervisor SQL/historical fixture edits, commits or pushes occurred. No production-readiness or throughput claim follows from this bounded test. Stop here; real local OCR integration is next.

## Follow-up: three reproduced adapter findings — 2026-11-04

This section records the current follow-up. Results above describe the earlier reviewed adapter milestone and are historical for this follow-up. Immediate baseline is `vehicle_engine_plate_review.zip`, SHA256 `29f189b797b2253f622a1a1e2701b4c5a11ee72972f76352d69f2fb213c147ac`; it remains unchanged. Exactly three existing reviewed files change: `vehicle/common/engine_plate.py`, `vehicle/common/tests/test_engine_plate.py`, and this report. All other 203 reviewed files, including policies, schemas, v0/v1 transport, retirement protection, injected fixtures and the native harness, are byte-identical. Initial/final status is packaged to distinguish unrelated working-tree changes. No applicable AGENTS.md was found in the repository or ancestors.

### Reproductions and fixes

Added six test methods before changing implementation. On the unfixed adapter they produced eight assertion failures including subtests; the valid closure-extension control passed. Exact commands and red output are in `reproduction-commands.json` and `reproductions-before.log`. These expected red results are retained as reproductions, not counted as passing checks.

1. Changing a resource's binding and matching pixels after admission, or changing binding inside `read()` (with unchanged or matching new pixels), previously bypassed the original crop authority. Execution now reads the immutable admitted `CropBinding`, verifies the resource against it before and after `read()`, validates bytes/size/hash against that admitted binding, and uses its original candidate in `NativeRequest`. Mismatch records not-started without invocation, latches `CROP_BINDING_CONFLICT`, and blocks publication. A structurally equal replacement binding does not replace the admitted candidate object. No retirement/cleanup shortcut is introduced.
2. A journal with a different org previously sent valid payload into another tenant's stream. Publication now validates the actual journal stream key's existing org tag against the admitted lifetime before preparation/EVAL. Mismatch raises `PARENT_MISMATCH` with no EVAL, no confirmed publication and resources retained. The regression verifies a previously prepared exact payload/IDs survive rejection; identical retry with the correct journal succeeds, invokes Redis once, releases once and performs no additional recognition. The existing Journal format and transport implementations remain unchanged.
3. Closing an open lifetime previously omitted the retained last receipt from cross-checking. The canonical `verify_shared_receipts` call now includes both retained and proposed lifetimes plus original candidates. Changed UTC, monotonic or media PTS on the same recorded last frame raises `FRAME_RECEIPT_MISMATCH` before accepted state changes. Tests verify each field, subsequent valid closure, identical retries, valid new-frame extension, and rejection of a later incompatible closure without changing accepted state. No competing timestamp policy is added.

### Checks executed in this follow-up

Existing Python 3.11.15 environment, real jsonschema 4.26.0 / Draft 2020-12 FormatChecker, redis-py 7.4.0. No packages installed. Commands below use `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python` followed by:

- `-m unittest vehicle.common.tests.test_engine_plate -q`: **36 passed** (six added methods; includes fresh pure-import protection).
- `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_redis_plate vehicle.common.tests.test_engine_plate -q`: **236 passed**, including the same 36; overlapping coverage.
- `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q`: **57 passed**, legacy provider paths are mocked.
- `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q`: **33 passed**.
- `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q`: **24 passed**.
- `contracts/db1/validate_fixtures.py`: **122 passed, zero failed**.
- `contracts/validate_examples.py`: valid18/schema rejection24/application rejection16, normalization8/retry3/strict JSON4/vocabulary drift3, all passed.
- `git diff --check`: passed. Scoped Python syntax, local document links, archive CRC, path exclusions and manifest hashes checked during packaging.

Commands/return codes/durations are in `_review/engine_plate_evidence/checks.json` and matching logs, with focused/reproduction commands in `reproduction-commands.json`.

### Native Redis: current failure retained

The unchanged native harness was additionally attempted using the existing Redis 8.2.11 binary with SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`. No inference/models/video/database access occurred. Exact command:

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_engine_plate_native_audit.py scripts/vehicle_engine_plate_integration.py /home/intern/part-time/aziz/engine-plate-fix-validation/native-adapter > /home/intern/part-time/aziz/engine-plate-fix-validation/native-adapter.log 2>&1
```

The completed separate run executed five methods: **FAILED (failures=10)**; exit 1. No skipped method is treated as success. The native transport regression gate is **not passing in this follow-up**. Worker excerpts show `STALE_OWNER` with the unchanged 300 ms lease configuration and recovery waits fail. A prior concurrent attempt also failed and was interrupted during teardown; both attempts and their cleanup evidence are included. The separate attempt rules out concurrency with the pure suite as the sole explanation. No transport/lease implementation or test deadline was changed to make the gate pass, and this failure is not attributed to a proven root cause. Earlier native success above is historical, not current success.

Completed run UTC interval 2026-11-04T11:10:31.042681+00:00–2026-11-04T11:14:14.520199+00:00. Settings remain private loopback/Unix socket, port0, appendonly no, save empty, maxmemory67108864/noeviction, one database. Audit records 42 owned children with exit-code counts {0: 38, -9: 4} and cleanup_passed=True. Injected callbacks=53, real model invocations=0; 5 successful parity checks are recorded individually and do not turn the failed suite into success. Worker crash/reclaim, Redis-server restart/power-loss durability and Engine crash recovery remain separate claims.

For the interrupted first attempt, the immediate audit reported cleanup failure while shutdown was in progress. The preserved post-exit verification confirms all 20 recorded PIDs and its directory/socket are absent. Only task-owned processes were signalled. Final completed-run cleanup is recorded in its own audit. No shared Redis was used.

### Review artifact and remaining limits

`/home/intern/part-time/aziz/vehicle_engine_plate_followup_review.zip` includes the exact three-file `_review/engine_plate.patch` against the reviewed adapter ZIP, all 206 local source/schema/test/document dependencies, per-file and evidence hashes, red reproductions, current results and earlier evidence under `prior_engine_plate_review`. Replay performs `git apply --check` and applies the patch in a private baseline extraction, then compares all 206 source files byte-for-byte. It reruns 236 boundary and 57 Plate legacy tests from that extraction; these are package dependency verification, not extra unique coverage. The archive excludes credentials/.env, environments, model/media assets, binary/cache/database files and supervisor SQL. No unrelated files are attributed to this follow-up.

The three fixes are verified by fresh regressions. The additional native transport gate's failure is an explicit remaining validation blocker; real OCR must not be represented as ready based on this follow-up. This task does not extend transport or fix unrelated lease/recovery behavior. Existing injected executor trust, bounded resources, timeout/retirement protection and unsupported Engine process-crash recovery remain as documented. No real models/providers, video, database work, installs/downloads, migrations, commits or pushes occurred.

## Native integration gate diagnosis and repair — 2026-11-04

The earlier native validation blocker is resolved by this follow-up. The adapter's three reviewed binding/org/receipt fixes remain byte-identical, as do canonical policies, schemas, historical fixtures and every fencing Lua script. The old failure sections above are historical; the fresh evidence for this repair is below.

### Proven cause and sequential comparison

Compared private source extractions on this VM, sequentially with no test-suite overlap: preceding `vehicle_engine_plate_review.zip` (SHA256 `29f189b797b2253f622a1a1e2701b4c5a11ee72972f76352d69f2fb213c147ac`) followed by current-before-repair `vehicle_engine_plate_followup_review.zip` (SHA256 `fdc9463e60386c57340034850d905d35b118700ad7ceaf5b803ce03d34fa8540`). Each ran the same adapter parity method covering whole and both regional orders against a separate owned Redis 8.2.11 instance with identical settings and forced 300 ms worker leases.

The preceding baseline failed two scenario assertions (38.469 seconds); current-before-repair failed three (40.503 seconds). Thus the failure predates the reviewed adapter fixes. Timing instrumentation measured actual synchronous work rather than treating `BLOCK 50` as an upper bound. Baseline Aggregator Plate decode reached 461.862 ms, reduction 596.500 ms, and a blocking poll 481.673 ms; current-before-repair reached 499.434 / 561.900 / 536.397 ms respectively. Other baseline polling reached 498.881 ms. The 300 ms lease had expired (PTTL -2) before PREPARE; the baseline rejected 21 PREPARE attempts with `STALE_OWNER`, current-before-repair rejected 48. This is a budget/configuration failure plus lease erosion in blocking polling, with correct fail-closed fencing, not corrupted adapter evidence.

A controlled counterfactual ran the untouched preceding source again with its **existing normal defaults**, by removing only the harness's forced `--lease-ms 300`: Colour 2000 ms; Plate/Aggregator Plate 60000 ms. The same three parity scenarios passed (33.561 seconds). No production default, assertion timeout, eventually deadline or schema limit was increased. Timings are measurements on this VM, not latency guarantees or throughput results.

### Scoped repair and reproducing checks

Changed source is `vehicle/common/redis_scalar/worker.py`, `vehicle/common/redis_scalar/journal.py`, `vehicle/common/tests/test_redis_scalar_integration.py`, new `vehicle/common/tests/test_redis_leases.py`, and this report: exactly five patch paths. Worker defaults are centralized with unchanged values: Colour/Aggregator 2000 ms and Plate/Aggregator Plate 60000 ms. Explicit short leases remain supported for deliberate expiry tests.

Worker polls are now nonblocking, count-one bounded scans with the existing round-robin and independent pending/new fairness. The CLI's existing 50 ms idle backoff remains. Pending-intent repair still precedes polling. A guarded renewal after a selected poll prevents processing under ownership that expired during the poll. Handoff performs another guarded renewal after reduction/serialization and before PREPARE. It never reacquires a new lease to commit an old owner's computed result. If work exceeds the configured lease, renewal fails and input stays pending for recovery. No background renewal thread, stale-error suppression or unconditional lease extension was added. Every mutation, publication and ACK still checks the unchanged Lua fence atomically.

Four injected lease tests were added before implementation changes; three failed on the unfixed source, and the existing-default control passed. They reproduce blocking scans, failure to recheck ownership after polling, and CPU/serialization carrying expired ownership into PREPARE. After repair, all four passed within the **42-test** affected pure lease/transport run. The red log is preserved with Mock invocation repr removed to avoid retaining payloads.

The native harness now uses normal role defaults for ordinary processing. For crash tests it still kills the actual child. After confirmed child exit, it may shorten **only that dead consumer's exact still-current lease** to 1 ms using compare-and-PEXPIRE Lua; it never shortens a live/new competitor's lease. This explicitly accelerates the test clock after SIGKILL instead of waiting out a 60-second production lease. It does not prove sub-10-second production failover; a real replacement may wait the remaining lease. Existing 10-second eventual assertions and 3-second child waits are unchanged.

Deliberate short-lease native tests remain separate. One now runs real reduction then injects a 350 ms delay under a 300 ms lease: no accepted state/output/ACK is written, the input remains in PEL, and a normal-budget replacement reclaims and completes it. The old owner still cannot renew. The competing-consumer case explicitly uses 300 ms and verifies expired-owner rejection for renewal, PREPARE, PUBLISH, FINISH and duplicate ACK; state/output stay unchanged. All Lua script hashes are identical to the reviewed follow-up baseline.

### Fresh checks, exact commands and cleanup

Existing environment `/home/intern/.venv/bin/python`, Python 3.11.15, real jsonschema 4.26.0 (Draft 2020-12 FormatChecker), redis-py 7.4.0. No packages installed. Every native invocation uses existing binary `/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`, version `Redis server v=8.2.11 sha=00000000:1 malloc=libc bits=64 build=d8a8c744cbac1a9`, SHA256 `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`.

Every private instance: port0, bind127.0.0.1, 0700 temporary directory/socket, appendonly no, save empty, maxmemory67108864, noeviction, one database. No shared Redis, server restart/power-loss test or PostgreSQL connection. Only owned processes were stopped.

Comparison command pattern (executed once per case, in the order described):

```sh
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/native_lease_audit.py ROOT OUTPUT vehicle.common.tests.test_engine_plate_redis_integration.EnginePlateRedisIntegrationTests.test_adapter_whole_and_both_regions_exact_parity_ack_and_no_reexecution
```

`ROOT` was `/home/intern/part-time/aziz/engine-plate-native-repair-validation/baseline`, then `/home/intern/part-time/aziz/engine-plate-native-repair-validation/before`; outputs were the sibling `comparison-baseline` / `comparison-current` directories. The third run reused `baseline` with `VEHICLE_DIAG_NORMAL_LEASES=1` and output `baseline-normal-settings`. All exact command arrays and source/binary fingerprints are in `_review/native_gate_evidence/commands.json` and manifests. Audit/worker timing helper sources are included.

After repair the same audit command used repository ROOT `/home/intern/part-time/aziz/pipeline` and these module/output pairs, **sequentially**, without the normal-leases override:

- `vehicle.common.tests.test_engine_plate_redis_integration` → `native-adapter`: **5 passed, no skips**. All eight actual worker-kill role/phase combinations passed. Exact canonical bytes/hashes, duplicates/ACK, no reinference, pending/late retirement, malformed output, failed publication and lost replies passed. Recorded 15 parity receipts, 53 injected calls and zero real model calls. The audit records 46 owned server/worker children, all reaped; private directory/socket removed.
- `vehicle.common.tests.test_redis_scalar_integration` → `native-v0`: **12 passed, no skips**, including the new 300 ms expiry/reclaim test and expanded stale-fence assertions.
- `vehicle.common.tests.test_redis_plate_integration` → `native-plate`: **9 passed, no skips**, including partial writes/lost replies, crash/reclaim, two revisions, fairness, conflicts and bounded payloads.

These are **26 native methods**, with no skipped method counted as success. Sanitized worker stderr, exit statuses and pending-state summaries before teardown are retained for failed comparison runs and successful runs. Timings cover acquisition/renewal, polling, decode, reduction, handoff/PREPARE, publication, FINISH/ACK and repair, recording duration/TTL/status only. They contain no raw payloads, lease tokens, credentials or crop data. Additional diagnostic PTTL calls add observer overhead; uninstrumented comparison failure from the preceding review remains historical supporting evidence. Assertion failures in the comparison runs are intentional before-evidence, not fresh passes.

Pure command prefix is `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`:

- `-m unittest vehicle.common.tests.test_redis_leases vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_redis_plate -q`: **42 passed**.
- `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_redis_plate vehicle.common.tests.test_engine_plate vehicle.common.tests.test_redis_leases -q`: **240 passed**, including the above 42 and all 36 adapter tests (overlapping coverage).
- `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q`: **57 passed**; provider routes mocked.
- `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q`: **33 passed**.
- `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q`: **24 passed**.
- `contracts/db1/validate_fixtures.py`: **122 passed, zero failed**.
- `contracts/validate_examples.py`: valid18/schema-reject24/application-reject16, normalization8/retry3/strict JSON4/vocabulary drift3, all passed.

Exact return codes/durations are in `checks.json`; native counts/times/PIDs/settings are in per-run audits. `timing-summary.json`, `expired-prepare-timelines.json`, `pending-summaries.json`, `crash-boundaries.json` and `protected-hashes.json` distinguish before/after evidence. All comparison/native temporary directories and sockets were removed; owned children were reaped. Actual pre-repair failure runs are retained rather than relabelled as successes.

### Review and limits

`/home/intern/part-time/aziz/vehicle_engine_plate_native_repair_review.zip` contains an exact five-path `_review/native_gate_repair.patch` against `vehicle_engine_plate_followup_review.zip` (SHA256 above), all 207 source/schema/test/document dependencies, source/evidence hashes, status attribution and replay verification. `git apply --check` and application in a private extraction reproduce all 207 source files byte-for-byte; 202 prior files remain unchanged. The extraction reruns the 78 focused lease/transport/adapter tests as package verification, not extra unique coverage. Earlier archives' evidence is historical and nested separately. No credentials/.env, native binary, database, cache, model/media assets or supervisor SQL are bundled.

The gate is resolved within existing metadata transport scope. Configured leases still must cover indivisible work and scheduler/network stalls; explicit under-budget leases fail closed. The harness's post-death lease acceleration is a test convention, not a production failover mechanism. No Engine process-crash recovery, real OCR/video, provider, PostgreSQL, database delivery or server restart/power-loss durability is established. No model/provider/video/database work, downloads/installations, migrations, commits or pushes occurred. Real local OCR remains a subsequent slice.
