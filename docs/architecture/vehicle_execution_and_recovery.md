# Vehicle execution, timing and recovery

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

## Input boundary and time authority

Input emits a decoded frame and its original paired `FrameReceipt` immediately after each successful decode, before queueing or inference. UTC and monotonic readings are paired once at that point using the existing `FrameReceiptClock`; Engine must not stamp again when dequeueing. Preserve frame_index, UTC, monotonic and media_pts unchanged. File facts retain host replay receipt time; RTSP facts retain host frame receipt time. File PTS is source position only, never a substitute wall clock. Negative PTS remains diagnostic metadata where DB1 cannot represent it.

Queue delay is not decode time. Ordered camera processing must preserve receipt order and the existing discontinuity latch/tolerance. No clamping, retrospective clock inference or silent epoch reset. Source read/decode duration belongs to Input; queue wait and Engine execution timing are separate. Existing historical end-to-end intervals keep their documented start/end; introducing this queue must not relabel them as Engine-only latency. On clock discontinuity, quarantine the lifetime and require a new explicit run/epoch. Facts use their supporting receipt: parent first receipt, decided local Colour last valid sample, undecided Colour last attempted sample. Publication/commit time is never fact time.

## Hybrid execution

Input uses bounded decoded-frame queues; Engine processes tracking updates in camera order. Independent domain branches may proceed within explicit count/byte budgets. Initially serialize native/GPU model access; no throughput improvement is asserted. Colour accepts at most 128 scalar attempts and 20 valid samples per lifetime in this slice, and rejects excess with `CAPACITY_EXCEEDED`. Engine computes pixels and quality; domain services never receive images. Scalar metadata may be reordered in transit, but reduction orders it by original frame/attempt identity.

## Terminal join in this slice

Engine supplies a closed immutable lifetime and detector class samples. Colour finalizes scalar measurements with the existing confidence/quality policy. Insufficient evidence produces contract-supported `undecided`, null colour/confidence and real executed-algorithm attribution. There is no additional inference request or provider recovery path.

Aggregator waits for every enabled domain. Explicit disabled Plate and unsupported Manufacturer are terminal omissions, not no_detection or recognition failure. Enabled unfinished Plate yields `waiting` and no delivery, including no parent-only final delivery. Present terminal results for Plate/Manufacturer are blocked as unsupported by this bounded implementation. Aggregator computes class consensus using the same function as the legacy pipeline and constructs parent/Colour natural facts together. It does not recompute Colour's winner.

## Recovery limits and future persistence

Pure-boundary results/history remain caller-owned memory; the new Redis adapter separately retains scoped state and exact output intents. A prior accepted `NaturalBundle` must be passed back to detect changed retries; no crash durability or automatic deduplication is claimed. Immutable canonical bytes include original Colour attempt IDs, receipts, quality and algorithm provenance; equivalent input order produces equal hashes. Retry changes never allocate new natural identities. Coded blockers/conflicts stop construction; no partial final bundle escapes.

The new Redis consumers implement independent groups, fenced reducer ownership, complete batches, retained waiting state and state/output/ACK journals. Native worker-process recovery validation passed on Redis 8.2.11; server restart and power-loss durability remain untested. ACK only after recoverable handoff; final DB1 ACK only after confirmed fact commit. Unknown execution/commit remains unknown until reconciled, never synthesized as unreadable/no_detection. Reclaims retain original logical identities and bytes. Process loss requires fencing and explicit new Engine tracking run; Redis does not restore native tracker/model/crop memory.

Engine raw trace intents and trace PG commits are independent of natural DB1 delivery and final fact commits. Trace-only credentials cannot write final facts; no trace foreign key is added to natural facts. Trace event org/run/camera/track/attempt correlation is explicit. No trace SQL, connection or migration is implemented here.

## Historical implementation evidence

Existing `VehicleAnalysisService` and standalone colour/plate runners still read/decode and run mixed pipelines. Shared Colour still slices pixels, computes quality and supports legacy optional agents; shared Plate still owns native execution and file-only admission. `ObservationCoordinator` still synchronously joins completed consumer evidence. Existing SQLite outbox and direct DB adapters remain historical compatibility paths. None of those legacy runtime paths has been switched by this slice.

## Bounded scalar Redis follow-up

The explicit scalar codec, complete-batch Colour consumer, persisted Aggregator joins and fenced state/output/ACK journal are implemented in `vehicle/common/redis_scalar/`. Original in-memory slice limits/results above describe the preceding implementation stage. Current transport contracts, executable commands, recovery guarantees and native validation results and limits are recorded in [Redis integration](vehicle_redis_scalar_integration.md). Legacy entry points, RowBuilder, source decoding, inference and final database delivery are not switched by this follow-up.

Native execution evidence, integrity/build commands, exact results and cleanup are recorded in [native validation](vehicle_redis_native_validation.md). Earlier blocked counts remain historical evidence of preceding stages.

## Bounded local MP4 integration (2026-11-03)

The first real-video Input → Engine → Redis → Colour → Aggregator path is implemented and tested with one existing local MP4 and existing local vehicle weights. Input owns the single decode pass/paired receipts; Engine owns all pixels, model execution, tracking/lifetimes, crop quality and HSV. Engine disables profile class consensus for this path (legacy default unchanged); Aggregator alone selects class consensus. Immutable scalar evidence is teed to in-memory and separate Redis-worker paths. The final bounded smoke produced 7 detector forwards, 63 HSV/quality samples and 9 exactly matching natural bundles, with Plate disabled and Manufacturer unsupported. No provider, database, trace persistence or RTSP is added. Commands, fingerprints, per-lifetime receipts, intermediate run, current regressions, failure retention and cleanup limits are in [real-video results](vehicle_real_video_results.md). This is a bounded parity demonstration, not a throughput or production claim.


### Plate metadata slice — 2026-11-03

Plate metadata closure is insufficient for delivery. Enabled work waits until detection/admission is complete and every expected crop/operation has an explicit nonpending outcome. Explicit host-settled uncertainty produces `unsettled_operation`, never timeout-derived unreadable/no-detection evidence. Logical crop attempts and whole/region operations are separate. Receipts retain the original crop and all rejected candidates participate in cross-domain receipt checks. Engine admission/execution/settlement and crash recovery are not implemented by this pure service. See [executed results and limitations](vehicle_plate_metadata_results.md).


## Plate scalar Redis integration — 2026-11-04

The Plate Redis worker journals initial waits and supports one explicit completion revision with frozen expected-work identities and monotonic evidence extension. Accepted terminals are immutable. Separate versioned v1 journal roles reuse fenced PREPARE/publication/ACK and pending-intent repair. Worker-crash, reclaim, partial-write and lost-reply tests use owned native Redis; they establish no Redis-server restart or power-loss durability. Intermediate XACK confirms recoverable service handoff, never database acceptance. See [Plate Redis results](vehicle_redis_plate_results.md).

## Engine-private Plate adapter — 2026-11-04

The Engine-private adapter serializes injected native access across adapter instances. Invocation start/model observation and retirement are explicit separate acknowledgments. Timeout/closure does not imply cancellation or settlement; unretired work pins its crop and native lane. Completed revision-0 transcripts use existing explicit-ID Redis publication/reconciliation; confirmed publication permits retry-safe release. Process-crash recovery of Engine handles/ledger is unsupported. Worker crash/reclaim was tested with native Redis; server restart/power-loss durability was not.

Interfaces, exact fresh checks, limits and review evidence: [Engine Plate results](vehicle_engine_plate_results.md).

## Real local Plate crop slice — 2026-11-04

Real CPU recognizers are lazy-loaded inside the serialized adapter lane. Start is recorded immediately before native predict after preprocessing; synchronous return/decoding establishes retirement, while explicit uncertainty stays pinned. Crop/model cleanup follows quiescence and confirmed publication. The smoke used fixed plans and existing normal Redis leases; Engine crash recovery remains unsupported.

Current interfaces, fingerprints, executed evidence and limits: [real executor results](vehicle_plate_real_executor_results.md). Earlier milestone descriptions above are historical for this stage.


## Bounded combined MP4 milestone — 2026-11-04

The opt-in combined host serializes detector initialization/prediction/close and Plate executions using the same native lane. Unknown retirement blocks further native access and publication; it is not cancellation. Exact closed scalar packets and IDs remain in memory until publication confirmation. This is not Engine process-crash recovery or a durable outbox. Capture admission stops, lifetime closure, detection/admission completeness, settlement, publication and resource release remain separate states.

Fresh execution status, exact bounds, identities and limitations: [combined MP4 results](vehicle_mp4_colour_plate_results.md). Earlier stage descriptions above remain historical.
