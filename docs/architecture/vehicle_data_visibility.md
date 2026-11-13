# Vehicle data ownership and visibility

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

## Boundary data

| Boundary | Data | Visibility/ownership |
|---|---|---|
| Input → Engine | decoded frame plus immutable paired receipt, explicit org/run/camera | Pixels stay within Input/Engine; bounded queue |
| Engine → Colour | VehicleIdentity, original attempt_id, FrameReceipt, label/confidence, quality, versioned algorithm and quality_algorithm | Scalar only; Colour interprets, Engine owns measurement |
| Engine → Aggregator | closed lifetime and immutable normalized detector class samples | Aggregator owns class consensus; detector provenance is in explicit run context |
| Colour → Aggregator | frozen FinalColourEvidence without crop bytes, canonical original-attempt provenance | Single Colour decision; attribution validation only at Aggregator |
| Aggregator → DB1 consumer | immutable natural parent/Colour facts plus terminal omission reasons and attempt provenance | No SQL keys, resolved SQLite records, arrays, credentials or coordinator crop bytes |
| Engine → separate PG trace area | structured raw execution trace with explicit tenant/run/camera/attempt identities | Engine-owned trace-only writer; supervisor confirmed, physical/access/retention design pending |

The current `VehicleIdentity` reuses `RunContext`; fixtures explicitly use unresolved development contexts with all SQL surrogate keys absent. This preserves the canonical identity recipe without requiring registration lookup. The DB1 consumer alone supplies future key resolution. Tenant identity is not authorization; a later transport adapter must compare org_id against trusted run/partition bindings.

## Immutable delivery and provenance

Each DB1 natural fact uses its own supporting timestamp and canonical UUID5 row identity. Bundle bytes use existing P1 canonical JSON/hashing. Detached `value()` results may be mutated without changing retained bytes. Bundle hashing covers original attempt provenance as well as facts and omissions, so changed quality/provenance retries conflict even when the selected colour is unchanged. Equivalent reordered measurements reduce to identical canonical bytes. Aggregator does not replace receipt time with publish time.

Colour input identifies the actual local algorithm/version/task and quality algorithm. Different measurement algorithms within a lifetime, inconsistent parents, conflicting shared receipts, duplicate frames/attempts or attribution changes are explicit blockers. Provider secrets/routes and crop media registration do not exist in this interface. Insufficient valid scalar evidence remains undecided; attempted invalid scalar observations retain truthful attempt provenance.

## Target privacy and persistence limits

Domain/Aggregator Redis streams contain only approved bounded metadata. No encoded images, tensors, source credentials, provider content or arbitrary exception bodies. Pixels/crops/model state stay Engine-private; optional evidence object storage remains a separately approved integration. Trace access/retention and sensitive Plate text require platform policy before deployment. Trace identity cannot authorize facts or substitute for an immutable delivery receipt.

The preceding in-memory slice created no persistent state. The new Redis adapter retains scoped waiting metadata, exact outputs and dedupe history; no database persistence is added. Legacy SQLite, stored crops, provider responses and tests remain preserved as historical implementation, with their existing access and cleanup behavior. Their existence does not authorize new target SQLite storage or provider calls.

## Bounded scalar Redis follow-up

The explicit scalar codec, complete-batch Colour consumer, persisted Aggregator joins and fenced state/output/ACK journal are implemented in `vehicle/common/redis_scalar/`. Original in-memory slice limits/results above describe the preceding implementation stage. Current transport contracts, executable commands, recovery guarantees and native validation results and limits are recorded in [Redis integration](vehicle_redis_scalar_integration.md). Legacy entry points, RowBuilder, source decoding, inference and final database delivery are not switched by this follow-up.

Native execution evidence, integrity/build commands, exact results and cleanup are recorded in [native validation](vehicle_redis_native_validation.md). Earlier blocked counts remain historical evidence of preceding stages.

## Bounded local MP4 integration (2026-11-03)

The first real-video Input → Engine → Redis → Colour → Aggregator path is implemented and tested with one existing local MP4 and existing local vehicle weights. Input owns the single decode pass/paired receipts; Engine owns all pixels, model execution, tracking/lifetimes, crop quality and HSV. Engine disables profile class consensus for this path (legacy default unchanged); Aggregator alone selects class consensus. Immutable scalar evidence is teed to in-memory and separate Redis-worker paths. The final bounded smoke produced 7 detector forwards, 63 HSV/quality samples and 9 exactly matching natural bundles, with Plate disabled and Manufacturer unsupported. No provider, database, trace persistence or RTSP is added. Commands, fingerprints, per-lifetime receipts, intermediate run, current regressions, failure retention and cleanup limits are in [real-video results](vehicle_real_video_results.md). This is a bounded parity demonstration, not a throughput or production claim.


### Plate metadata slice — 2026-11-03

The Plate boundary accepts scalar candidate/association IDs, unregistered crop references/SHA256, frame/crop geometry, original paired receipts, quality/provenance and bounded raw OCR/marker transcripts. Pixels and loaded/native model responses remain Engine-private. Supplied references do not authorize access or prove registration. Final v1 natural bundles retain canonical original transcript provenance; registered crop media stays null. No provider fields or SQL resolution are introduced. See [executed results and limitations](vehicle_plate_metadata_results.md).


## Plate scalar Redis integration — 2026-11-04

Only bounded scalar Plate transcripts, original paired receipts/quality/geometry/model outcomes and canonical provenance hashes cross Redis. Crop references remain unregistered evidence. New v1 delivery includes the original lifecycle/Colour/Plate attachments for canonical reconstruction; no pixels, models, SQL keys or provider fields are introduced. Configured org partitioning is validated but is not production authorization. See [Plate Redis results](vehicle_redis_plate_results.md).

## Engine-private Plate adapter — 2026-11-04

`engine_plate` retains crop handles, read-only BGR8 views and raw invocation output inside Engine. Only original scalar candidates, receipts, quality, opaque crop reference/fingerprint, operations and observed model identity enter the existing Plate transcript. New injected executors carry synthetic model tags. Native Redis testing used no real images, video, recognition models, provider or database.

Interfaces, exact fresh checks, limits and review evidence: [Engine Plate results](vehicle_engine_plate_results.md).

## Real local Plate crop slice — 2026-11-04

Real OCR/marker models, original BGR8 allocations, private copies and regional transforms remain inside Engine. Only existing scalar transcripts and opaque crop hashes cross Redis. Runtime receipts export hashes, counts, coordinate lineage and outcomes, not pixels or raw OCR text. The two selected files are content-identical; no recognition accuracy/diversity claim follows.

Current interfaces, fingerprints, executed evidence and limits: [real executor results](vehicle_plate_real_executor_results.md). Earlier milestone descriptions above are historical for this stage.


## Bounded combined MP4 milestone — 2026-11-04

One paired Input receipt and separate media PTS flow unchanged into original class/Colour measurements and Plate candidate/operation scalars. Full-frame rectangles are clipped to original-resolution BGR crops inside Engine; regional coordinates remain relative to that immutable admitted crop and exact marker dependency. Ambiguous/unassociated projections are diagnostics, never inferred parent ownership. Review evidence exports hashes, counts and lineage rather than raw OCR text or full runtime payloads.

Fresh execution status, exact bounds, identities and limitations: [combined MP4 results](vehicle_mp4_colour_plate_results.md). Earlier stage descriptions above remain historical.
