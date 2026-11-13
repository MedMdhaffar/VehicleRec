# Bounded scalar Redis integration

Status: native Redis validation completed on 2026-11-01 with user-owned Redis **8.2.11**. All **11 integration methods passed in two full runs**, without skips or source/harness fixes. redis-py 7.4.0 and real jsonschema 4.26.0 were reused. A verified official Redis source release was built outside the repository using existing tools; no package manager, global install, Docker, shared Redis, PostgreSQL, models/providers or runtime entry-point switch was used. Redis-server restart, disk/power-loss durability and production deployment are not established. Full commands, published checksum and raw evidence locations are in [native validation](vehicle_redis_native_validation.md).

## Ownership and scope

Input still reads/decodes before Engine. Engine owns pixels/tracking/association/quality/local inference. This follow-up injects scalar evidence and invokes existing ColourMetadata, ColourResult validation, Aggregator and NaturalBuilder; it does not add a recognition policy, inference path, Plate implementation or database consumer. No SQLite target or provider fallback. The replaceable DB1 consumer and separate Engine PostgreSQL trace writer remain disconnected.

`vehicle/common/redis_scalar/codec.py` implements the closed `contracts/vehicle_redis_scalar_v1.schema.json` contract. `reducer.py` holds pure explicit-state transitions. `journal.py` defines fenced recoverable Redis operations. `worker.py` and `synthetic.py` are explicit entry points; importing any of them creates no connection and imports no redis client or heavy inference module.

## Versioned wire messages and completeness

Every message is one UTF-8 `json` stream field. The envelope has exactly `schema_version="vehicle-redis-scalar-v1"`, UUID `event_id`, explicit `org_id`, `kind`, `body_sha256` and `body`. Four kinds are defined:

| Kind | Stream | Explicit body |
|---|---|---|
| colour.batch | engine.evidence.colour | Closed immutable lifetime, complete=true, expected_attempt_ids and all original scalar measurements |
| vehicle.closed | engine.lifecycle | Closed lifetime/class receipts/confidences, expected colour_batch_id, Plate capability and unsupported Manufacturer |
| colour.terminal | results.colour | Original lifetime, batch_id, Colour claims plus original complete attempt provenance |
| db1.delivery | db1.delivery | Lifetime, unchanged canonical natural bundle, bundle_sha256 |

A batch is complete only if `complete` is true and the nonempty unique expected ID set equals every supplied measurement ID exactly, with matching cardinality. Maximum 128 original attempts and 20 valid Colour samples. Closure does not prove completeness: lifecycle alone persists waiting. Colour retains both the original accepted batch event ID and its complete body in vehicle state. A retry must match both; identical contents under a different batch ID explicitly conflict, while true identical retries do not republish. Retained legacy state without an authenticated batch ID fails closed if it reaches the reducer; no batch identity is inferred or silently replaced. Incremental multi-message sample assembly is deliberately outside this slice. The expected batch ID also binds lifecycle and Colour terminal; full lifetime equality is required before joining.

Before retaining `vehicle.closed`, Codec and reducer call the shared Aggregator class-admission checks: unique class frames, consistent paired receipts (including lifetime endpoints), canonical score ranges/labels and exact lifetime attribution. Invalid closures retain only a diagnostic, not a vehicle slot or accepted-event marker; a later valid closure with the same event ID can still join either an already waiting or later Colour terminal. Class consensus itself is unchanged.

Scalar identity carries org/run/camera/service_track_id, raw track ID, tracker epoch and lifetime sequence. Context includes timezone, camera validity, source kind, original run start, detector identity and service/resolution provenance, with no database surrogate keys. This wire version accepts unresolved development contexts only. Receipts retain original frame index, UTC, monotonic and PTS. Decode restores float receipt/scalar types used by existing policies and exact mathematical integer identity types, including IDs above 2^53. Scalars that cannot round-trip through the domain float profile with identical P1 canonical bytes are explicitly rejected as NUMERIC_PRECISION rather than silently rounded; canonical JSON/hashes use existing P1 functions. Natural bundle bytes/hashes equal the in-memory fixture. No timestamp is generated at publish/reclaim time. Context timestamp spellings are producer-pinned; P1 canonical timestamp normalization applies to its existing recognized keys, including supporting `ts` values.

Strict decode rejects duplicate JSON keys, nonfinite values, unknown fields (including natural delivery facts/omissions/provenance), wrong types/bools as integers, identity/hash inconsistencies and bad terminal Colour claims. Per-message limits: 65,536 bytes, depth 16 before JSON parsing, 8,192 value nodes, arrays at most 128, strings at most 2,048 characters, property names at most 256, numeric mantissas at most 64 digits and adjusted exponent within ±300, plus narrower schema ranges. Domain task is local classify; no agent/provider capability exists.

Four complete canonical message examples are in `contracts/redis_scalar_examples/`. They are new synthetic examples, not modified historical fixtures.

## Trusted routing and groups

CLI requires an explicit destination, simple namespace, trusted org, stable UUID identity namespace and consumer name. Redis key prefix is `<namespace>:{vehicle-v1.<trusted-org-uuid>}:`. Payload org is compared with this deployment binding, never used to select another tenant's keys. Run/camera/lifetime/batch identities are validated across inputs. This is not production authorization: a trusted operator and Redis ACL isolation must control the destination/namespace and producer permissions; message claims alone do not authorize a run.

Colour reads engine.evidence.colour using colour-v1. Aggregator independently reads engine.lifecycle and results.colour using its own aggregator-v1 group on each. Groups are created at 0-0, never `$`. Results and final bundles are written to results.colour and db1.delivery. Diagnostics go to diagnostics.colour/diagnostics.aggregator and contain only trusted org, source, fixed error code and input hash, never raw hostile payloads or exception/connection text.

## Recoverable handoff and fencing

One leased active event-loop writer per role/namespace/org partition is allowed. Standby processes compete for the role lease. Acquire increments a generation; the token also contains a process-random owner identity. Every state, publication and ACK mutation compares that token. Lease renewal and release are conditional too. Expired/stale workers cannot change reducer state, publish or ACK. The counter does not expire; only the lease expires. PEL ownership is not authority.

For each accepted input:

1. Read bounded per-vehicle state and compute the next state/output using the existing pure boundaries. Same logical event ID with changed canonical bytes is an explicit PAYLOAD_CONFLICT diagnostic; original accepted state remains intact.
2. PREPARE checks fence, global revision and capacity. One multi-field **HSET command** stores the new state, seen marker/hash, counters/revision and one pending intent together. The intent contains exact output bytes, source entry and a fixed output stream ID `<revision>-0`; output envelope UUID is deterministic from natural vehicle identity and role. A prepared state is already recoverably accepted in Redis, even if its output is not yet visible.
3. Before doing any newer work, repair the single pending intent. For its fixed stream ID, XRANGE either verifies identical `json` bytes or XADD publishes those exact bytes. A lost XADD reply is reconciled by inspecting that ID. An unexpected existing payload, stream type/ID inconsistency or capacity error leaves the input unacknowledged and intent retained; no new output ID is allocated.
4. FINISH rechecks output bytes under the same fence, then XACKs the source entry and clears pending. If a Lua runtime error occurs after XACK and before clearing pending, repair verifies output and repeats XACK safely. A lost FINISH reply is likewise resolved from pending/seen/source state. Identical duplicates are ACKed under a fence only after prior pending work has been repaired.

Lua atomic execution is **not a rollback guarantee**. PREPARE uses one state-changing Redis command to avoid splitting accepted state/dedupe/intent. PUBLISH may have inserted an output before a later runtime error or lost reply. FINISH may have ACKed before failure clearing pending. Recovery explicitly handles those partial states. Scripts access only KEYS passed explicitly, all in the same hash slot. References: [Redis Lua scripting](https://redis.io/docs/latest/develop/programmability/eval-intro/), [XADD explicit stream IDs](https://redis.io/docs/latest/commands/xadd/).

Publication repair cannot create a second logical final delivery: a pending intent keeps its original fixed Redis ID and exact bytes, and retained vehicle state prevents producing another final result on equivalent component retries. New event IDs cannot replace already accepted component content. Altered bytes under a prior logical ID are quarantined through the same journal. Namespace streams must remain dedicated to these producers; out-of-band stream writes/deletion/restore can cause invariant failure and require explicit operator reconciliation, not automatic new publication IDs.

## Waiting, bounds and shutdown

Lifecycle-first and terminal-first arrivals are symmetric. Incomplete joins and enabled-but-unfinished Plate persist as waiting, ACK their accepted metadata and emit no final delivery. A waiting vehicle does not occupy the single pending intent or block another ready vehicle. Class consensus and terminal joining stay in Aggregator; Colour-owned replay authenticates terminal claims. No TTL/trim applies to state, dedupe history, unfinished joins, inputs or outputs.

Defaults per partition/role: 128 retained vehicle states, 256 accepted-event markers (including rejected-entry diagnostics), at most 196,608 bytes per state and 524,288 bytes per pending intent, one pending output, 256 entries per output stream. Configuration can lower bounds; hard maxima are 128 vehicles and 1,024 events/stream entries. Scalar producer also enforces an ingress stream limit of 256. Trusted external producers must enforce their own admission bound; the worker does not trim an overfilled external stream. On capacity exhaustion, the pending input remains in the PEL/stream or pending intent and is not silently dropped/ACKed. Accepted retained history is intentionally finite: rotation/archive/retention policies require a later explicit operator contract; this is not an indefinitely running production retention strategy.

Pending-intent repair remains the first priority and performs no input polling in its repair turn. Otherwise each poll reads/claims one entry at a time and rotates stream priority after each dispatch attempt, advancing before processing so a retried error cannot pin the first source. Each stream has its own pending/new preference, flipped after a dispatch on that stream; two correlated global toggles cannot starve a stream/mode lane. With both Aggregator streams continuously ready, each receives one dispatch every two non-repair turns. With pending and new both ready on each stream, each lane receives service every four non-repair turns. An empty scan is bounded to two sources × two modes (four polls), each COUNT=1. Fairness here is running-worker dispatch fairness, not a throughput or arbitrary repeated-crash guarantee. Reclaim uses XAUTOCLAIM with zero idle only after the exclusive fenced role lease has been acquired; previous producers cannot mutate state after losing that lease. Repair is O(1) lookup of a single pending intent, not an unbounded state scan. Redis commands time out after two seconds; stream polling blocks for 50 ms and idle/backoff sleep is 50 ms. A failed pending publication blocks newer work in that role to preserve fixed-ID ordering; ordinary waiting joins do not.

SIGINT/SIGTERM stops new polling after the current bounded operation, attempts retained-intent repair and conditional lease release, then closes the client. SIGKILL is recovered through lease expiry, pending repair and PEL reclaim. Unexpected Redis/client errors print only sanitized retry codes. Test-only --pause-at/--phase-file flags expose prepared/published/before_ack/acknowledged boundaries for parent-controlled child termination; phase files are not state stores.

## Exact three-process commands

Run from `/home/intern/part-time/aziz/pipeline` once an existing native Redis server is available. The following separate terminals use the same explicitly chosen private socket. These are reproducible demo commands; the validation harness used its own unique temporary socket rather than this demo path.

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m vehicle.common.redis_scalar.worker --role colour --redis unix:///tmp/vehicle-redis-demo/redis.sock --namespace vehicle_demo_v1 --org 12345678-1234-4234-8234-123456789012 --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --consumer colour-1
```

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m vehicle.common.redis_scalar.worker --role aggregator --redis unix:///tmp/vehicle-redis-demo/redis.sock --namespace vehicle_demo_v1 --org 12345678-1234-4234-8234-123456789012 --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --consumer aggregator-1
```

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m vehicle.common.redis_scalar.synthetic --redis unix:///tmp/vehicle-redis-demo/redis.sock --namespace vehicle_demo_v1 --org 12345678-1234-4234-8234-123456789012 --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb --consumer producer-1 --stream-id 1-0 --order closed-first
```

Repeat the same producer command/ID for an idempotent producer retry. Different vehicles require --track 2 and a higher --stream-id 2-0. Add --plate enabled to demonstrate an unfinished branch. --order colour-first changes source publication order only; the integration harness explicitly waits to exercise both actual Aggregator input orders. Credentials should be supplied using an approved external connection mechanism, not copied into commands/review ZIPs.

For a manually launched isolated server, first create a new user-owned private directory (choose a nonexisting path), then use the existing binary with `--port 0 --unixsocket /tmp/vehicle-redis-demo/redis.sock --unixsocketperm 700 --save "" --appendonly no --maxmemory 64mb --maxmemory-policy noeviction --databases 1 --dir /tmp/vehicle-redis-demo --daemonize no`. Do not point this command at shared server configuration or an existing data directory. Persistence is deliberately disabled for process-recovery tests; this establishes no disk/server/power-loss durability.

## Executable real-Redis harness

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_redis_integration.py
```

An existing binary outside PATH can be selected explicitly with `VEHICLE_REDIS_SERVER=/absolute/path/to/redis-server`. The harness creates its own 0700 temporary directory and unique 0700 Unix socket, spawns only its own server/workers, records actual Redis version/settings, and tears down only those children/resources. It never calls FLUSHDB/FLUSHALL or changes shared configuration/keys. Redis >=6.2 is required. Defaults requested by the harness are port=0, save="", appendonly=no, maxmemory=64mb, maxmemory-policy=noeviction and databases=1; **observed settings matched those values** on Redis 8.2.11; the audited run also verified private directory/socket modes 0700, bind=127.0.0.1, databases=1 and protected-mode=yes. AOF was off, so appendfsync=everysec was inactive.

Eleven real-Redis tests cover exact bytes/both orders; duplicates/conflicts/malformed/cross-org; waiting/restart/ready concurrency; competing consumers/stale fences; eight actual child SIGKILL cases across two roles and four handoff/publication/ACK phases; post-XADD Lua runtime error; lost replies after PREPARE/PUBLISH/FINISH; vehicle/output capacity preserving pending input; continuously ready streams with pending/new backlogs; changed batch identity with true retry preservation; and invalid closure followed by a valid join in either arrival order. All eleven methods were executed successfully in both native runs, including eight actual SIGKILL boundary subcases per run. These are worker-process recovery checks against a continuously running Redis server, not Redis-server/power-loss durability tests. The harness exits 2 for unavailable runtime rather than silently treating a skipped class as integration success. Current native results and review evidence are recorded in [native validation](vehicle_redis_native_validation.md); earlier blocked runs remain historical in [Redis results](vehicle_redis_scalar_results.md).


## Plate scalar Redis integration — 2026-11-04

The Colour-only specification and original results above remain the v0 compatibility route. An explicitly separate Plate/v1 codec, lifecycle route, worker roles/groups and journal byte profile now exist alongside it. The existing v0 native harness was rerun freshly; prior results above remain historical. See the current Plate report for actual new/native counts, exact limits, complete-input/wait completion rules, source identities, cleanup and remaining limitations. See [Plate Redis results](vehicle_redis_plate_results.md).
