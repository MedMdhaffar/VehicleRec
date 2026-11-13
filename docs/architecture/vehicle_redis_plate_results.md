# Bounded Plate metadata Redis integration — 2026-11-04

Implemented and tested with **injected scalar evidence**:

`Engine transcript publisher → private Redis → Plate worker → Plate terminal → v1 Aggregator → Redis natural-v1 delivery`.

The final native Plate harness passed **9 methods**, and the existing v0 native harness passed **11 methods freshly**. No skips count as success. Direct in-memory and Redis-connected natural bundle bytes/hashes match. This is a bounded metadata transport proof, not real OCR integration, a deployed service architecture, database delivery, throughput/accuracy validation or Redis-server/power-loss durability.

## Public interfaces and ownership

- `PlateCodec`, loaded by `load_plate_codec(org, identity_namespace)`, validates the opt-in `vehicle-redis-plate-v1` contract. It uses the existing lifetime/receipt/number/class/Colour decoders and shared P1 canonical JSON. `pack_transcript` preserves original scalar evidence while sorting semantically unordered sets; operation sequence remains explicit.
- `plate_synthetic.fixture` and the explicit `python -m vehicle.common.redis_scalar.plate_synthetic` publisher supply clearly injected Engine-labelled transcripts. They never open video/crops or initialize models. The actual publisher CLI ran in its own child process, including an identical stable-stream-ID retry.
- Worker role `plate` consumes transcripts through the **unchanged reviewed `PlateMetadata`**. Only terminal evaluation produces `plate.terminal`. Waiting retains original accepted batch/revision evidence; it produces no terminal or natural delivery.
- Role `aggregator_plate` joins explicit v1 lifecycle, unchanged Colour terminals and Plate terminals. It calls the **unchanged `Aggregator.join_with_plate` and `NaturalPlateBuilder`**, validates available receipt/batch/lifetime correlations before retaining a partial join, and publishes canonical natural-v1 bytes.
- The existing journal implements the same fenced PREPARE/output/ACK protocol for new versioned roles, with a separate larger byte profile. Its Lua protocol is reused unchanged.

Engine retains all detection/tracking/association, crop pixels/quality/transforms, OCR/marker execution, execution admission/status/settlement and actual model attribution. Plate owns scalar interpretation/selection; Aggregator owns class consensus/join and natural construction. Manufacturer remains unsupported. No AI/provider fallback, provider fields, SQL keys, SQLite target or PostgreSQL connection is introduced. Only the future replaceable DB1 consumer may resolve keys/write final facts. Engine trace persistence remains separate.

The Plate dependency fixes, Aggregator, both natural builders, legacy RowBuilder, canonical P1 policies, v0 schema/examples, historical fixtures, MP4 runtime and legacy entry points are byte-unchanged from the reviewed baseline except the four explicit transport modules below. The new codec replays domain-owned validation; it does not accept an upstream chosen Plate reading or implement another winner policy.

## Versioned envelopes, routing and identity

Every new envelope has exactly `schema_version="vehicle-redis-plate-v1"`, `policy_version="plate-local-v1"`, `event_id`, configured `org_id`, `kind`, `body_sha256` and `body`. Payload routing never selects a tenant. `org_id` is checked against the configured partition and lifetime/parent fields; it is **not authorization**. Runtime/run/camera registration, producer authentication and production ACLs remain external contracts.

| Kind | Stream suffix | Body / consumer |
| --- | --- | --- |
| `plate.batch` | `engine.evidence.plate` | Original lifetime, batch UUID, immutable revision 0/1 and full transcript; `plate-v1` group |
| `plate.terminal` | `results.plate` | Same original transcript/batch/revision, authenticated claims and canonical provenance SHA256; `aggregator-plate-v1` group |
| `vehicle.closed.plate` | `engine.lifecycle.plate` | Explicit `delivery_version="natural-db1-bundle-v1"`, lifetime/classes, expected Colour and Plate batch UUIDs, Plate capability, unsupported Manufacturer; `aggregator-plate-v1` |
| `db1.delivery.v1` | `db1.delivery.v1` | Lifetime, original lifecycle/Colour/Plate attachments, exact natural-v1 bundle and hash; no database consumer added |
| Existing `colour.terminal` / scalar-v1 | Existing `results.colour` | Explicit bridge through the unchanged v0 codec; independent `aggregator-plate-v1` group |

Keys retain `<namespace>:{vehicle-v1.<trusted-org-uuid>}:<suffix>`. New journals/leases are `plate:journal`/`plate:lease` and `aggregator_plate:journal`/`aggregator_plate:lease`. They cannot read/upgrade `colour:journal` or `aggregator:journal` state. Use a dedicated explicitly chosen namespace for this opt-in v1 workload; an old Aggregator deliberately left on the same `results.colour` stream would independently wait for its own v0 lifecycle rather than upgrade its state. No existing runtime/default role is switched.

All groups start at `0-0`. V1 input/final streams are separate from v0 lifecycle/delivery. The old codec still rejects new envelopes/bundles. Only existing Colour terminals bridge into the v1 codec; old lifecycle, Colour batches and old natural deliveries are not silently reinterpreted as v1. The existing Colour worker/codec/default stream remain unchanged.

New event UUIDs are deterministic from the configured UUID namespace, trusted org, run/camera/service lifetime, wire version, kind, batch ID and revision. Batch IDs have explicit producer-pinned UUID identity. Event IDs must match at decode. Canonical body hashes, original accepted batch/revision identity and component event identity are retained. Identical accepted retries ACK without republishing; changed accepted IDs/content conflict, including identical evidence under a different Plate batch ID. New facts/attempt UUIDs remain the existing P1 natural identities.

The closed JSON schema validates original parents, receipts, candidates, operation IDs/sequences/dependencies, actual models/tasks, raw OCR/marker output, nullable quality, expected work, capability and settlement. Semantic admission/receipt consistency/claims are delegated to the existing pure policies. Natural rows, omissions and provenance are authenticated by reconstructing the whole bundle through the canonical Aggregator/builders, rather than copying a second natural-row policy into a schema; unknown inner bundle fields also fail exact canonical comparison. Duplicate JSON keys, malformed/nonfinite values, unknown fields, wrong bindings, unsupported versions and precision-losing scalar conversions reject before accepting vehicle state. Diagnostics retain only trusted org, source, fixed code and input hash.

No receipt is generated at publish/reclaim time. Original UTC, monotonic value and separate PTS round-trip through shared receipt helpers. Model and last-supporting-operation attribution, nullable quality, segment/crop/attempt IDs, all rejected candidates and every original operation remain in transcript/provenance. References/fingerprints prove no registration; optional registered media stays null. Disabled/unsupported wire inputs must use canonical empty settled transcripts, matching Aggregator's existing capability omission path; other disabled-input shapes are explicitly blocked rather than silently discarding provenance.

## Bounded completion of a retained wait

This is a **two-revision handoff**, not a distributed recognition-request loop or general progressive update protocol:

1. Revision **0** may be a completed transcript (ordinary path), or an immutable expected-work plan with missing/pending work/open admission. A wait is journaled and ACKed as a recoverable service handoff; no terminal is emitted.
2. A waiting revision 0 permits exactly one revision **1** completing that same batch/lifetime. Parent/capability/policy and expected crop IDs stay fixed. Existing candidates, crop identity/ordinal/expected operation IDs and request kind/sequence/dependencies/route cannot disappear/change. Existing known outcomes remain identical; pending/unknown outcomes may gain settlement/results, preserving any already known model. Only previously declared missing work and monotonic evidence/completeness extension are allowed. Revision 1 must evaluate terminal through PlateMetadata.
3. Terminal decisions cannot be replaced. Both accepted revisions and hashes remain retained, so initial or completion retries stay idempotent after finalization. Changed content under either accepted immutable revision conflicts.
4. A completion that overtakes an unprepared initial revision is **deferred in the source PEL without ACK, rejection or vehicle-state mutation**. Pending/new fairness can accept the missing initial handoff; reclaim then completes the original retained input. If that initial handoff never arrives, the completion remains pending and produces no delivery. This is not timeout-derived unreadability.

Unknown execution requires the existing explicit uncertainty settlement and yields conservative `unsettled_operation`, never fabricated unreadable/no-detection evidence. Closure alone proves neither complete admission nor settlement. Enabled missing work stays waiting. Known completed/failed local execution, unreadable output, no detection and capability omissions use existing P1 semantics.

Lifecycle/Colour/Plate may arrive in any order. Every available correlation and original shared receipt is checked before occupying a new immutable join slot, including receipts from rejected Colour/Plate evidence. A malformed or conflicting closure cannot poison later valid joining. The final v1 payload is emitted once only after all three expected handoffs are present, including the explicit Plate capability terminal.

## Effective bounds

All byte values below count UTF-8 **encoded bytes**, including JSON/envelope overhead. No evidence is truncated. The reviewed domain's **4,194,304-byte canonical Plate provenance limit** remains unchanged.

| Boundary | Explicit limit |
| --- | ---: |
| New Plate batch envelope | 6 MiB = **6,291,456** bytes |
| New Plate terminal envelope | 8 MiB = **8,388,608** bytes |
| Explicit v1 lifecycle envelope | **65,536** bytes |
| New natural-v1 delivery envelope, including original attachments | 12 MiB = **12,582,912** bytes |
| New wire JSON | depth **32**, **200,000** value nodes, arrays **128**, strings **4,096** characters, property names **256** characters; inherited 64-digit / ±300 adjusted-exponent numeric profile |
| Domain OCR text | **4,096 UTF-8 bytes** per successful operation; all existing typed parent/geometry/score/ID/model/work limits remain enforced |
| New retained vehicle state | 20 MiB = **20,971,520** bytes; read depth **40**, **500,000** nodes, arrays **128**, strings **4,096** characters |
| New pending journal intent | `2 * MAX_WIRE + 4096` = **25,169,920** bytes; at most **one** output |
| V0 wire / state / intent | unchanged **65,536 / 196,608 / 524,288** bytes, with unchanged old JSON profile |

Plate terminal transport carries full original transcript plus claims/provenance hash, avoiding a redundant second full provenance copy. Natural-v1 delivery also carries the reconstructed full bundle; its larger envelope accounts for the transcript and bundle's full provenance. New journal intents use `ensure_ascii=False`; quoting the already-encoded output can at most double its UTF-8 bytes, with 4,096 bytes reserved for fixed intent metadata. Escaped quotes/backslashes/newlines and Unicode are exercised. V0's serialization/default byte profile stays unchanged. Constructor overrides may only lower the selected journal profile limits.

The representative **32-attempt** large fixture measured batch **146,202**, terminal **146,661**, natural delivery envelope **299,496**, natural bundle **147,823**, Plate state **146,099** and Aggregator state **298,616** bytes. Its canonical bundle SHA256 is `3314bedaeb08c63960aa12f7aa3c6435692e72ba9a806c0e9f4f4ecd03ec9c05`. Real Redis transported the above-old-limit envelopes successfully. Over-global/per-kind wire limits and over-4-MiB provenance are rejected; state/intent/output capacity tests prevent partial PREPARE/ACK. `large-payload-sizes.json` records reproducible pure measurements for the same native fixture, not a throughput benchmark.

Default role limits remain 128 vehicles, 256 accepted/rejected events and 256 entries per output stream; hard count maxima remain 128/1,024/1,024. Producer streams use the existing 256-entry admission guard and stable explicit stream-ID reconciliation. No TTL, trim, eviction or automatic accepted-history deletion is added. Native test Redis has 64 MiB total `noeviction` memory, which can reject before configured logical count limits; that is explicit capacity/backpressure, not unlimited retention.

## Recovery and ACK meaning

New roles share the same-slot lease/fence, one-command PREPARE HSET, exact pending output bytes/fixed IDs, XRANGE/XADD reconciliation and verified output/XACK/intent-clear protocol. A new `plate-journal-v1` intent profile and role-specific state versions reject retained v0/unknown formats; no migration/inference is attempted. Pending-intent repair has priority over polling.

Byte-capacity checks run **before PREPARE**, retaining neither partial new state nor accepted-event markers and leaving the input pending. A full output stream may occur **after** PREPARE: the accepted state and exact intent remain, the source is unacknowledged, and repair retries the same output ID/bytes. Partial Lua writes and lost replies are reconciled, never assumed rolled back. This distinction is tested for both new roles. Invalid envelopes/semantic conflicts instead receive sanitized rejection handoffs without altering accepted vehicle evidence.

**Intermediate XACK confirms recoverable Redis service handoff only. It confirms neither PostgreSQL acceptance nor final database delivery.** No DB1 consumer or Engine trace writer is connected.

The existing fair scheduler rotates all three v1 input streams and independently alternates pending/new on each. With all streams/modes ready, each stream is dispatched every three non-repair turns and each pending/new lane every six. Empty scans are bounded to six polls (`COUNT=1`, new-read block 50 ms). Reclaim is permitted only under the fenced role lease. Waiting joins do not hold the pending output intent; a blocked publication intentionally blocks newer role outputs. New metadata roles default to a 60,000-ms lease for bounded larger JSON work; old roles retain 2,000 ms. Crash tests explicitly use 300 ms. These are scheduling/recovery bounds, not real-time performance claims.

## Exact executed checks

CWD `/home/intern/part-time/aziz/pipeline`. Existing interpreter `/home/intern/.venv/bin/python` (Python 3.11.15), real jsonschema **4.26.0**, redis-py **7.4.0**. No installations/downloads. All ordinary Python commands below have prefix `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`; full argument arrays, returns/durations and stdout/stderr are in the fresh evidence directory.

| Python arguments | Fresh executed result |
| --- | --- |
| `-m unittest vehicle.common.tests.test_redis_plate -q` | **20 passed** |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_redis_plate -q` | **200 passed**, includes the same 20 new transport tests |
| `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q` | **57 passed**, injected legacy compatibility only |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 passed** |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | valid 18; schema rejection 24; application rejection 16; normalization 8; retries 3; strict JSON 4; vocabulary drift 3 |

The new tests exercise codec round trips, all six component arrival orders, direct canonical parity, both regional orders/model versions/dependency binding, stale/failed operations, every required outcome, known model/nullable quality, immutable retry/batch identities, completion monotonicity/defer/recovery, tenant/run/camera/vehicle binding, unknown fields/numbers/versions, early cross-component receipts, v0/retained-version separation, wire/domain/state/intent/output limits and JSON escaping, stream/pending/new fairness/repair priority, and fresh-process imports with heavy/client/database modules forbidden.

Actual audited native commands (the audit wrapper source is included under review evidence):

```bash
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_plate_native_audit.py scripts/vehicle_redis_plate_integration.py /home/intern/part-time/aziz/plate-redis-validation/native-plate > /home/intern/part-time/aziz/plate-redis-validation/native-plate.log 2>&1
```

```bash
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /tmp/run_plate_native_audit.py scripts/vehicle_redis_integration.py /home/intern/part-time/aziz/plate-redis-validation/native-v0 > /home/intern/part-time/aziz/plate-redis-validation/native-v0.log 2>&1
```

| Native harness | Fresh final execution |
| --- | --- |
| `scripts/vehicle_redis_plate_integration.py` | **9 methods passed in 71.888 s**, no skips |
| Existing `scripts/vehicle_redis_integration.py` | **11 methods passed in 42.966 s**, no skips |

The nine new native methods cover separate worker processes/outcomes/six order variants; real publisher process and identical explicit-ID retry; retained-wait restart of both workers, rejected bad completion, another ready vehicle, and completion-before-initial recovery; **eight actual child kills across Plate and Aggregator × prepared/published/before_ack/acknowledged**; both-role post-XADD Lua errors and lost PREPARE/PUBLISH/FINISH replies; three continuously ready streams with pending/new backlogs; both-role state/intent byte rejection and repair, stream capacity; above-old-limit payloads and wire rejection; tenant/batch conflicts and invalid-closure recovery.

Native runtime exists: no missing-runtime blocker or skipped integration is reported as success. Missing runtime still exits 2 in the harness. The old 11-method harness is fresh compatibility evidence, not a historical rerun claim borrowed from earlier reports. Prior native/video milestone artifacts remain historical under the previous review snapshot. **No SQLite/outbox/adapter suites or PostgreSQL tests were run in this task.** Injected legacy provider/error tests do not make real provider calls.

Development runs initially exposed two incorrect Python float-vs-Decimal test equality assertions; contract equality is canonical P1 bytes, and those assertions were corrected. A reproducing test then caught completion-overtake rejection/ACK; it failed before the PEL deferral fix and passes now, including the real-Redis recovery case. Earlier successful eight-method development native runs preceded final receipt/capacity/recovery/publisher refinements and are clearly labelled preliminary, not additional final coverage. Final checks above passed after corrections. Static AST/whitespace and schema-loading checks pass; no integration skips are counted.

## Actual runtime and cleanup

The existing user-owned Redis binary was reused; no build/download/install was performed:

- Version: `Redis server v=8.2.11 sha=00000000:1 malloc=libc bits=64 build=d8a8c744cbac1a9`.
- Binary SHA256: `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`.
- Observed server settings in **both** fresh harnesses: `port=0`, `save=""`, `appendonly=no`, `maxmemory=67108864`, `maxmemory-policy=noeviction`. Launch commands use one database, bind 127.0.0.1 and a unique user-owned 0700 directory/Unix socket. No shared Redis/server configuration, Docker or system service was touched; no FLUSH operation was used.
- Final Plate run: **60 owned child processes** recorded/reaped, including server/workers/two publisher invocations: 48 exit 0, 10 intentional SIGKILL, two SIGTERM exits during owned teardown. V0 compatibility run: **40 owned child processes**: 31 exit 0 and nine intentional SIGKILL. Both servers exited 0. Audits confirm no created PID remained and each private directory/socket was removed. Only task-created processes were stopped.
- Final Plate execution audit UTC interval `2026-11-03T23:47:37.260371+00:00`–`2026-11-03T23:48:50.439564+00:00`, which is **2026-11-04 in Africa/Tunis**. Actual commands, PIDs, exit codes, server fingerprint and removed paths are in audit JSON.

The server remains running throughout each worker-crash test. Persistence is deliberately disabled. Results establish worker recovery/reclaim while Redis retains state, **not Redis-server restart, disk/power-loss durability, HA, production authorization or end-to-end database acceptance**.

## Changed files and review package

New source/schema/tests:

- `contracts/vehicle_redis_plate_v1.schema.json`
- `vehicle/common/redis_scalar/plate_codec.py`
- `vehicle/common/redis_scalar/plate_reducer.py`
- `vehicle/common/redis_scalar/plate_synthetic.py`
- `vehicle/common/tests/test_redis_plate.py`
- `vehicle/common/tests/test_redis_plate_integration.py`
- `scripts/vehicle_redis_plate_integration.py`

Scoped changes to existing transport: `vehicle/common/redis_scalar/codec.py`, `journal.py`, `reducer.py`, `worker.py`. The first adds optional bounds while preserving exact v0 defaults; the others add opt-in roles/profile/dispatch and recoverable completion deferral. No reviewed recognition/selection/natural-construction implementation changes.

Updated architecture documents: `vehicle_component_ownership.md`, `vehicle_execution_and_recovery.md`, `vehicle_data_visibility.md`, `vehicle_redis_contracts.md`, `vehicle_transition_plan.md`, `vehicle_redis_scalar_integration.md`, plus this report, under `docs/architecture/`.

Review ZIP: `/home/intern/part-time/aziz/vehicle_plate_redis_review.zip`.

Immediate baseline: reviewed `vehicle_plate_metadata_review.zip`, SHA256 `031744879ea467a28da1bb0319f0f5601f0322e9f1e2c7686b172c35dbf0d1db`. That baseline archive remains unchanged. The review contains `_review/plate_redis.patch`, an exact **18-file** follow-up against its source snapshot; 200 repository-relative source/schema/test/document dependencies; source hashes/attribution; initial/final git status; actual fresh logs, audit helpers and cleanup/capacity evidence. Older manifests/patches/executions are clearly under `_review/prior_plate_review/`, not attributed to this slice. Earlier local Plate/legacy/dependency edits remain preserved.

Patch verification applies the scoped patch in a private extraction of the immediate baseline and compares all 200 resulting source files byte-for-byte. The extracted snapshot also runs **200 boundary tests and 57 legacy Plate tests**, proving packaged local dependencies; these are duplicate validation executions, not extra unique coverage. ZIP names/CRCs/hashes, exclusions, AST and whitespace are verified. Credentials/.env, environments, models/video/crops, caches, databases, native binaries/build outputs and supervisor SQL are excluded. No commit or push was made.

## Remaining limitations and exact next step

No actual Engine Plate execution/transcript adapter, native OCR/marker inference, video input, distributed local-recognition requests, general progressive revision protocol, PostgreSQL/DB1 consumer, trace schema or Engine capture/tracker/crop crash recovery is added. Publishers of real evidence must retain exact source scalars/IDs until confirmed publication and explicitly handle failure; this CLI uses deterministic injected fixtures and reports publication failure, not a durable Engine outbox. Bounded accepted history/streams require a later retention/operator contract. Use explicitly segregated namespaces and trusted producer/ACL bindings; message org fields do not provide authorization.

The next integration is an Engine-private local Plate execution adapter that records actual operation/model IDs, original crop receipts/quality/geometry and complete expected-work/settlement into this tested contract, with independent admission/resource cleanup tests. If a distributed local followup loop is later required, define request/admission/correlation and recovery separately before implementing it. **Stop here: no real OCR or database integration is claimed or performed.**
