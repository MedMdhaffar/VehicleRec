# Vehicle Redis handoff contract and next wiring step

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

## Preceding in-memory stage and natural shape

At the preceding stage, Redis was not connected and Python frozen records were the tested contracts. The new `vehicle-redis-scalar-v1` wire schema and adapters are now implemented; their real Redis validation now passed the bounded native harness as detailed below. Existing historical `vehicle_event_v1.schema.json` and DB1 fixtures are unchanged. Historical schemas allowing agents describe legacy data only and do not add routes to this target.

The implemented `natural-db1-bundle-v0` object contains `schema_version`, explicit `org_id`, ordered `facts` (row_kind and DB1 delivery), sorted terminal `omissions` (domain/reason), and `colour_provenance` (original attempts and Colour decision reason). Natural facts are validated against existing DB1 delivery definitions. It is not a resolved SQL row snapshot or a deployed wire protocol. `NaturalBundle` retains canonical bytes and SHA256; each fact retains canonical row UUID. Attempt IDs and per-attempt receipts, quality and local algorithm provenance are preserved. The DB1 consumer will resolve keys without changing accepted natural bytes.

## Proposed initial stream topology

Use one authorized organization partition initially, with a single hash-tagged prefix `veh:{v1.<org_uuid>.p00}:`. Tenant routing comes from trusted deployment/run bindings. Check explicit org/run/camera in every decoded message against that binding.

| Suffix | Producer | Independent groups |
|---|---|---|
| `engine.lifecycle` | Engine | colour-v1, plate-v1, manufacturer-v1, aggregator-v1 |
| `engine.evidence.colour` | Engine | colour-v1, aggregator-evidence-v1 |
| `engine.evidence.plate` / `.manufacturer` | Engine | matching domain-v1, aggregator-evidence-v1 |
| `results.colour` / `.plate` / `.manufacturer` | named metadata domain | aggregator-v1 |
| `db1.delivery` | Aggregator | db1-contract-v1 |
| `db1.receipts` | replaceable consumer | retention-v1, api-status-v1 |

Metadata domains have separate fan-out groups. Multiple replicas of one domain share its group only after fenced single-writer reduction is implemented. Create groups at `0-0` before producers. Stream IDs are transport cursors, never vehicle/attempt identity or fact time. Input-to-Engine pixels remain a bounded local boundary, not Redis image messages. No provider request stream or agent capability exists.

## Exact next Redis wiring step

This subsection records the original step specification. Code is implemented and the 11-method native harness passed; PostgreSQL delivery remains unwired.

Implement a versioned scalar codec and Redis adapter around **these tested interfaces**, first using injected scalar fixtures. Encode `ColourMeasurement`, closed lifetime/class samples, explicit branch capabilities, `ColourResult` and natural bundle without SQL keys or pixels. Preserve paired UTC/monotonic/PTS receipt semantics. Validate closed field sets, strict JSON (duplicate keys/nonfinite rejected), enum/range/count/byte limits, explicit org binding and canonical body hashes before invoking reducers.

Then use `XREADGROUP` on `engine.evidence.colour` with `colour-v1` and on lifecycle/results with `aggregator-v1`. Retain original message/attempt IDs and canonical bytes. Add same-slot bounded state/output/ACK journal operations that durably retain reducer state and outputs before `XACK`. Publish Colour terminal bytes to `results.colour`, and a ready immutable natural bundle to `db1.delivery`. A waiting join persists its dependency set and emits no delivery. Same ID/different bytes quarantines as conflict. Crash/reclaim tests must cover before/after output publication and ACK, duplicate/reordered events and unfinished branches before using real inference. No connection or Redis package installation is part of the current slice.

## Later consumer and trace wiring

Only the replaceable DB1 consumer registers/resolves keys and writes final facts. Preserve exact natural acceptance plus resolved projection history; final input ACK follows confirmed PostgreSQL commit or reconciliation of an identical prior commit. Unknown commit does not mean rollback. Trace-only Engine persistence has its own intents and confirmation; it is not a second final-fact consumer. Physical trace schema, roles/RLS, retention and infrastructure persistence/HA remain platform decisions. No supervisor SQL or historical contract fixtures are changed.

## Bounded scalar Redis follow-up

The explicit scalar codec, complete-batch Colour consumer, persisted Aggregator joins and fenced state/output/ACK journal are implemented in `vehicle/common/redis_scalar/`. Original in-memory slice limits/results above describe the preceding implementation stage. Current transport contracts, executable commands, recovery guarantees and native validation results and limits are recorded in [Redis integration](vehicle_redis_scalar_integration.md). Legacy entry points, RowBuilder, source decoding, inference and final database delivery are not switched by this follow-up.

Native execution evidence, integrity/build commands, exact results and cleanup are recorded in [native validation](vehicle_redis_native_validation.md). Earlier blocked counts remain historical evidence of preceding stages.

## Bounded local MP4 integration (2026-11-03)

The first real-video Input → Engine → Redis → Colour → Aggregator path is implemented and tested with one existing local MP4 and existing local vehicle weights. Input owns the single decode pass/paired receipts; Engine owns all pixels, model execution, tracking/lifetimes, crop quality and HSV. Engine disables profile class consensus for this path (legacy default unchanged); Aggregator alone selects class consensus. Immutable scalar evidence is teed to in-memory and separate Redis-worker paths. The final bounded smoke produced 7 detector forwards, 63 HSV/quality samples and 9 exactly matching natural bundles, with Plate disabled and Manufacturer unsupported. No provider, database, trace persistence or RTSP is added. Commands, fingerprints, per-lifetime receipts, intermediate run, current regressions, failure retention and cleanup limits are in [real-video results](vehicle_real_video_results.md). This is a bounded parity demonstration, not a throughput or production claim.


### Plate metadata slice — 2026-11-03

An explicit in-memory `natural-db1-bundle-v1` adds Plate facts or supported Plate omissions plus `plate_provenance`. The Colour-only `natural-db1-bundle-v0`, Redis schema/codec/workers/journal/examples and real-video smoke stay unchanged. The current codec explicitly rejects v1. Plate request/result event schemas, bounds/decoders, reducer/journal state and distributed local followup correlation are subsequent work; no v1 Redis wiring exists here. See [executed results and limitations](vehicle_plate_metadata_results.md).


## Plate scalar Redis integration — 2026-11-04

`vehicle-redis-plate-v1` is now an opt-in closed envelope/schema and codec for Plate batches/terminals, explicit v1 lifecycle and natural-v1 delivery. Streams `engine.evidence.plate`, `engine.lifecycle.plate`, `results.plate` and `db1.delivery.v1` use the existing same-org hash-tag convention; the v1 Aggregator also reads unchanged `results.colour`. Roles/groups/journal profiles are separate from v0. V0 limits/default messages/retained state are not upgraded. New per-envelope/state/intent limits and two-revision semantics are specified in the linked results. See [Plate Redis results](vehicle_redis_plate_results.md).

## Engine-private Plate adapter — 2026-11-04

The Engine adapter now constructs existing revision-0 Plate batches from explicit plans and recorded injected execution. Existing schemas, codecs, workers, journal and domain policies are unchanged. Prepared publication retains exact batch ID, explicit stream ID and bytes across failed/lost replies. Success requires the requested stream ID acknowledgment. No follow-up protocol is added. The adapter-produced path passed five isolated native Redis methods with exact natural-v1 parity and downstream worker recovery.

Interfaces, exact fresh checks, limits and review evidence: [Engine Plate results](vehicle_engine_plate_results.md).

## Real local Plate crop slice — 2026-11-04

Existing revision-0 contracts, schemas, policies, codecs, journal and fences are unchanged. Real crop-generated scalar evidence passed direct/Redis canonical byte/hash parity for two declared lifetimes. Initializer/no-start-only evidence cannot be labelled a passing real-recognition smoke. Fixed orchestration is not a distributed follow-up protocol.

Current interfaces, fingerprints, executed evidence and limits: [real executor results](vehicle_plate_real_executor_results.md). Earlier milestone descriptions above are historical for this stage.


## Bounded combined MP4 milestone — 2026-11-04

The combined host uses the existing opt-in natural-v1 lifecycle, Colour batch, adapter-produced Plate batch, three metadata workers and unchanged fenced journal/codecs/schemas. Its direct reference receives exactly the same original immutable scalar evidence, without repeat inference or new receipts. Colour-only v0 defaults remain unchanged. PostgreSQL DB1 delivery and Engine trace persistence are unwired.

Fresh execution status, exact bounds, identities and limitations: [combined MP4 results](vehicle_mp4_colour_plate_results.md). Earlier stage descriptions above remain historical.
