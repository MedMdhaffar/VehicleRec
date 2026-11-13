# Vehicle bounded transition and validation

Current milestone (2026-11-07): atomic Plate completion observation and pre-mutation
tenant validation are repaired. A separate owned AOF/always Redis profile validates
process restart with exact metadata replay and the same live Engine ledger. The
bounded injected runner reports explicit state and canonical parity. Native
PostgreSQL remains BLOCKED without an explicitly configured disposable target.
See [current results](vehicle_restart_results.md), [operator guide](vehicle_operator_guide.md)
and [v2 contract](vehicle_plate_requests_v2.md). Statements and execution counts
in the earlier stage records below are historical, including their then-current
limitations. This is not a deployed or complete production architecture.

Supervisor-confirmed target, recorded 2026-11-01. Implementation status: scalar in-memory boundaries plus bounded Redis codec/workers. Real native Redis 8.2.11 validation passed all 11 integration methods in two complete runs. Worker-crash recovery is tested; Redis-server restart and power-loss durability are not. PostgreSQL and inference remain disconnected. This is not a deployed architecture.

RTSP/MP4 reading and decoding belong to an Input block before Engine. Engine owns pixels, detection, tracking, association, image quality and every local model invocation. Colour, Plate and Manufacturer are metadata-only domain services. Aggregator owns class consensus, terminal joining and immutable natural DB1 delivery. Redis Streams is the target broker. Only the replaceable DB1 consumer resolves database keys and writes final facts. Supervisor has confirmed Engine's separate PostgreSQL raw execution trace boundary; its physical schema, credentials and retention still need platform contracts. Every boundary carries explicit org_id, which is an identity field and does not establish authorization.

No AI/provider fallback exists anywhere in the target. No SQLite participates in the target. Legacy stores, providers, source and behavior remain preserved during transition. Hybrid scheduling means ordered tracking per camera with bounded independent branches; native/GPU model access is initially serialized.

## First bounded slice implemented

Pure Colour scalar policy is extracted from `temporal.py` and re-exported by that legacy module. Class consensus is extracted from `pipeline.py` and reused there. `core.py` and `natural_policy.py` share identity, serialization, validation and natural field construction with the retained RowBuilder compatibility facade. `NaturalBuilder`, `ColourMetadata` and `Aggregator` provide the new in-memory boundaries. They require no SQL keys, SQLite records, crop bytes, model initialization or inference imports.

The 17-test synthetic scalar suite in `vehicle/common/tests/test_colour_aggregator.py` covers enabled Colour, disabled Plate and unsupported Manufacturer; waiting enabled Plate with no final delivery; insufficient Colour as undecided without fallback; inconsistent identities/provenance/receipts; immutable canonical bundles and changed retries; exact natural compatibility with RowBuilder; pure imports in a fresh process. It does not modify historical fixtures. Review regressions also cover Colour-owned canonical replay of terminal state/label/confidence, rejected-attempt receipt conflicts, accepted output regressing to waiting, and omission ordering directly at NaturalBuilder.

Checks executed for this slice and exact results are recorded in [slice results](vehicle_colour_slice_results.md). The following earlier audit baseline is retained as historical evidence only; its counts are not claimed as current runs.

## Earlier audit baseline (historical results; current runs recorded separately)

Existing interpreter `/home/intern/.venv/bin/python`, with `PYTHONDONTWRITEBYTECODE=1`, no installs/downloads/model loading. Relevant source was inspected before selecting tests; SQLite/temp-file/database fixture suites were excluded.

| Command | Observed result |
|---|---|
| `... contracts/validate_examples.py` | Valid Draft2020-12 schema; 18 valid,24 schema-reject,16 application-reject examples; normalization8, retry3, strict JSON4 and drift3 checks pass |
| `... contracts/db1/validate_fixtures.py` | 122 passed,0 failed; synthetic schema/semantic fixtures only |
| `... -m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup` | 99 tests passed (21 foundation,72 row-construction,6 cleanup); injected evidence/consumers, no models/providers/persistence |

The revision uses documentation-only checks: local links/fences/whitespace, embedded synthetic JSON/hashes/referenced natural-row shapes, request/dependency/seal and coordinate example consistency, and a protected-source/schema/fixture/archive fingerprint. The 99/122 results above are the earlier audit baseline, not new runtime or proposed-protocol test results. Python AST inventory and archive checksum were checked in that audit. No PG/Redis connections, supervisor checker execution, SQL application, uploads, Docker, model/provider execution, runtime refactoring, installs, commit or push. Existing test coverage outside these selected suites was inspected, not claimed rerun. These checks do not validate the proposed Redis runtime, infrastructure durability, production authentication, model accuracy or throughput.

Review revision 2 documentation checks passed: all five local documents and anchors/fences/whitespace;11 embedded JSON objects; computed body/seal/natural-row hashes; referenced existing natural-row schemas; same-attempt request ordering/dependency examples; exact-seal positive/negative oracles including rejected/missing/extra decisions; tagged coordinate transform examples; and C1 omission consistency. A combined fingerprint of258 protected source/config/schema/fixture/script/test/archive files matched the pre-revision baseline. No runtime tests, Redis/PG connections or models were run in this revision. These are specification checks, not validation of an implemented distributed protocol.


## Limits and migration gates

This is an in-memory proof, not deployed microservices. Existing API/runtime paths are unchanged. No Redis/PostgreSQL connection, model/provider execution, installation, migration, supervisor SQL modification, SQLite extension, commit or push is part of the slice. RowBuilder still enforces legacy resolution and readiness behavior; its historical agent/Plate compatibility remains available only through legacy evidence paths.

The scalar wire codec and recoverable Redis state/output/ACK adapter described in [Redis contracts](vehicle_redis_contracts.md#exact-next-redis-wiring-step) are now implemented against the same fixtures. The isolated native Redis recovery harness now passed all 11 methods; real inference and PostgreSQL remain disconnected. Then migrate Engine pixel extraction into ordered camera processing behind the Input receipt boundary, retain serialized native access, and prove admission/settlement/reclaim behavior. Plate/manufacturer terminal implementations, trusted tenant routing, trace PG schema/access/retention and replaceable DB1 consumer wiring remain later work.

Rollback for this slice is continued use of the legacy runtime entry points; no runtime switch was made. Preserve original stores/source and existing outputs until explicit migration/archival authorization.

## Bounded scalar Redis follow-up

The explicit scalar codec, complete-batch Colour consumer, persisted Aggregator joins and fenced state/output/ACK journal are implemented in `vehicle/common/redis_scalar/`. Original in-memory slice limits/results above describe the preceding implementation stage. Current transport contracts, executable commands, recovery guarantees and native validation results and limits are recorded in [Redis integration](vehicle_redis_scalar_integration.md). Legacy entry points, RowBuilder, source decoding, inference and final database delivery are not switched by this follow-up.

Native execution evidence, integrity/build commands, exact results and cleanup are recorded in [native validation](vehicle_redis_native_validation.md). Earlier blocked counts remain historical evidence of preceding stages.

## Bounded local MP4 integration (2026-11-03)

The first real-video Input → Engine → Redis → Colour → Aggregator path is implemented and tested with one existing local MP4 and existing local vehicle weights. Input owns the single decode pass/paired receipts; Engine owns all pixels, model execution, tracking/lifetimes, crop quality and HSV. Engine disables profile class consensus for this path (legacy default unchanged); Aggregator alone selects class consensus. Immutable scalar evidence is teed to in-memory and separate Redis-worker paths. The final bounded smoke produced 7 detector forwards, 63 HSV/quality samples and 9 exactly matching natural bundles, with Plate disabled and Manufacturer unsupported. No provider, database, trace persistence or RTSP is added. Commands, fingerprints, per-lifetime receipts, intermediate run, current regressions, failure retention and cleanup limits are in [real-video results](vehicle_real_video_results.md). This is a bounded parity demonstration, not a throughput or production claim.


### Plate metadata slice — 2026-11-03

After the reviewed Colour real-video milestone, the first bounded Plate metadata slice now consumes injected complete local transcripts and produces versioned natural Plate delivery in memory. It reuses canonical scalar/P1 policies and preserves the legacy SQL/provider facade. No real Plate inference or distributed followup loop is validated. The next slice must explicitly define Plate/v1 Redis message contracts and immutable reducer/journal joins before introducing Engine Plate execution. See [executed results and limitations](vehicle_plate_metadata_results.md).


## Plate scalar Redis integration — 2026-11-04

The bounded Plate scalar Redis path is implemented and validated with injected evidence. Dedicated Plate/v1 Aggregator workers reuse the reviewed policies and journal; natural-v1 bytes match direct construction. No real OCR, distributed recognition requests, provider/video execution, SQL/database delivery or Engine trace persistence is added. Engine Plate execution/transcript capture and distributed local followups remain later integrations. See [Plate Redis results](vehicle_redis_plate_results.md).

## Engine-private Plate adapter — 2026-11-04

Completed the Engine-private injected Plate execution adapter stage. Five native Redis methods passed, including eight actual worker kills; 30 focused tests are included in 230 affected boundary tests. Recognition is injected (53 calls in the final native run; zero real model calls). Next is separately bounded real local OCR integration, including real executor start/retirement guarantees and shared native scheduling with legacy Engine inference. Engine crash recovery and PostgreSQL raw trace persistence remain later tasks. This stage is not deployed.

Interfaces, exact fresh checks, limits and review evidence: [Engine Plate results](vehicle_engine_plate_results.md).

## Real local Plate crop slice — 2026-11-04

Completed the bounded real local Plate executor/crop proof: eight actual CPU predict API calls (2 whole, 2 marker, 2 left, 2 right), two unreadable Plate facts, exact parity and safe cleanup. Existing adapter native regressions passed five methods. Full MP4 detection/association and production Plate follow-up orchestration remain subsequent review slices; PostgreSQL trace persistence is still separate.

Current interfaces, fingerprints, executed evidence and limits: [real executor results](vehicle_plate_real_executor_results.md). Earlier milestone descriptions above are historical for this stage.


## Bounded combined MP4 milestone — 2026-11-04

The new opt-in combined MP4 implementation connects existing real vehicle/class/HSV measurements and real Plate detection/association/execution to the reviewed v1 transport. This is a bounded validation host, not a deployed architecture or throughput/accuracy claim. Distributed Plate semantic request/admission/recovery, Engine recovery, trace persistence and replaceable PostgreSQL delivery remain later tasks.

Fresh execution status, exact bounds, identities and limitations: [combined MP4 results](vehicle_mp4_colour_plate_results.md). Earlier stage descriptions above remain historical.
