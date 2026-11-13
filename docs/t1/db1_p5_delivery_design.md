# P5 delivery and recovery design

Status: review proposal, 2026-10-12. This document defines an implementable P5
boundary. It changes no runtime behavior and does not approve production deployment.

This proposal is based on the actual [P5 requirements](db1_implementation_plan.md),
[gap decisions](db1_gap_resolution.md), [P3b1 outbox behavior](../../vehicle/common/db1_outbox.md),
[P4 adapter boundary](../../vehicle/common/db1_adapter.md), [latest real PostgreSQL
results](db1_p4_postgres_results.md), supervisor archive, current Python sources and
their unit/integration tests. The supplied supervisor SQL and receipt proposal are
inputs to the design; neither is changed here.

## Basis and design decision

P3b1 already provides the first durable handoff: SQLite commits one immutable
`db1-local-outbox-v1` accepted envelope before the coordinator may release its
in-memory evidence. P4 accepts a recovered `AcceptedEntry`, a separately obtained
`TrustedTenant`, the original `Resolutions`, and an idle normal-role PostgreSQL 16
connection. Its real tests on PostgreSQL 16.14 proved atomic new delivery, exact
identical retry, conflict detection, RLS isolation, concurrent first delivery and
rollback. A successful `DeliveryResult` is returned only after PostgreSQL commit.
Both `new` and `identical` therefore authorize the same local delivered transition.

The P5 plan requires durable snapshots, restart replay, bounded retry and
acknowledgment only after downstream commit. It does not prescribe Redis or any
other broker. Older P4 notes mention later “Redis/acknowledgment integration,” but
there is no Redis stream, key, consumer-group, topology, retention or security
contract in the project. Redis is consequently neither a current requirement nor
an approved assumption.

The recommended first deployment shape is a co-located delivery worker reading the
durable SQLite outbox directly. In that shape SQLite is the durable producer-to-
worker transport and no broker improves correctness. If the platform later requires
workers on another host or process isolation through a broker, add an at-least-once
`DeliveryTransport` implementation without changing the state machine below. Redis
Streams is one possible implementation after its topology and retention rules are
approved; it must never become the only copy of accepted history.

P5 delivery does not recover or resume inference. Open lifetimes, pending OCR,
tracker state, retained crops, unaccepted completed evidence and coordinator cleanup
progress remain in memory. After restart, new frames require a new explicitly
registered run until native run-state recovery exists. Delivery reads only already
accepted entries. It never calls a model, reconstructs a crop, finalizes a lifetime
or invokes coordinator cleanup.

## Components and boundaries

The components have deliberately narrow ownership:

| Component | Responsibility | Must not do |
|---|---|---|
| `LocalOutbox` v2 | Atomically retain the accepted v1 envelope, its exact resolution snapshot, destination bindings and delivery state; expose validated inspection and leasing. | Open PostgreSQL connections, infer authorization, publish raw credentials, run inference or clean coordinator evidence. |
| `OutboxReader` | Select due work by keyset/lease, validate compatibility and return an immutable `DeliveryWork` record. | Rebuild an accepted entry from producer state or treat a local sequence as delivery identity. |
| `DestinationRegistry` | Resolve a stable non-secret destination ID to a connection factory and expected receipt compatibility. | Store a DSN/password in SQLite or accept an unregistered endpoint from a message. |
| `TenantAuthorizer` | Authenticate the running service and authorize the accepted organization for the configured destination, returning `TrustedTenant`. | Trust the envelope's `org_id`, a transport field or a persisted authority string as authorization. |
| `ResolutionCodec` / supplier | Persist and decode the exact immutable `Resolutions` used at acceptance; validate its digest and relationship to the accepted envelope. For legacy entries, attach only an authoritative historical snapshot through an explicit operator action. | Re-run recognition, invent numeric keys, infer registrations from fact rows or silently replace a snapshot. |
| `DatabaseWorker` | Obtain a bounded normal-role connection, call `DB1Adapter.deliver`, classify the result/error and request a durable state transition. | Mutate the accepted envelope, issue DDL, acknowledge transport before state is durable or clean producer evidence. |
| `DeliveryTransport` (optional) | Provide at-least-once notification, claim/redelivery and acknowledgment when deployment requires a broker. | Be the system of record, define tenant trust or treat message acknowledgment as DB delivery proof. |
| `EvidenceStager` and `MediaRegistrar` (later P5 slice) | Durably stage selected evidence, upload by deterministic object identity, register it, and return immutable `MediaResolution` records before row acceptance. Reconcile staged/uploaded but unreferenced objects. | Run recognition, invent media keys, put local paths into DB1 or make PostgreSQL delivery depend on transient crop memory. |
| `DeliveryInspector` | Report safe counts, ages, state, attempts and stable identifiers; support explicit resume/rebind operations with audit metadata. | Emit plate text, full payloads, credentials, DSNs or exception bodies. |

The acknowledgment path has three distinct meanings which must not be conflated:

1. `ObservationCoordinator` acknowledgment/release occurs after durable SQLite
   acceptance. It frees in-memory producer evidence and is already independent of
   P5 delivery.
2. PostgreSQL acknowledgment is only a returned `DeliveryResult` with disposition
   `new` or `identical`, after the adapter's commit context has exited.
3. Optional transport acknowledgment removes a notification from broker redelivery
   only after the matching local delivered state is durable.

## Durable records and identity

The accepted envelope remains the authority for immutable row content and terminal
omissions. P5 must not change its acceptance marker or rewrite its bytes to express
delivery. Delivery state is separate metadata bound to the accepted record.

The proposed v2 SQLite store has these logical records:

### Accepted envelope

The existing `accepted(sequence, identity, payload, digest, payload_bytes)` record
is retained byte-for-byte. `sequence` remains only a local pagination cursor.
The delivery identity is:

```text
DeliveryKey = (
    logical_identity_canonical,
    accepted_envelope_sha256,
    destination_id,
    destination_fingerprint
)
```

`destination_id` is a stable operator-defined name such as
`vehicle-db1-production-v1`; it is not a hostname or DSN. The non-secret
`destination_fingerprint` binds that name to the intended PostgreSQL cluster/
database, normal application-role profile and expected
`db1-delivery-receipts-v1` compatibility digest. A destination reconfiguration
that changes this fingerprint creates a new explicit binding; it must not inherit
an old delivered marker merely because the local sequence or friendly name matches.

### Resolution snapshot

`accepted_resolution` stores one canonical `db1-resolution-snapshot-v1` document
per accepted identity with its SHA-256 and byte length. It contains the complete
frozen `Resolutions` graph used by `RowBuilder`: run, organization, camera SCD2
version, lane, detector, classes, grouped colours, plate categories, recognition
models and every referenced media registration, including their provenance fields.
The codec uses the same strict JSON, exact timestamp and finite-number rules as P1.
It reconstructs only the existing frozen record types.

For new v2 acceptances, accepted envelope and resolution snapshot commit in the
same `BEGIN IMMEDIATE` transaction. An identical acceptance must have the same
resolution digest. A differing resolution snapshot for an already accepted logical
identity is a permanent local conflict even if it would currently construct the
same rows. This preserves the actual input to the acceptance decision.

The resolution snapshot is consistency evidence, not current authorization and not
proof that registrations still exist. `DB1Adapter` continues to check the actual
database under tenant RLS on every new or identical delivery attempt.

### Destination state and attempt history

`delivery_state` has one row per `DeliveryKey` and also records
`resolution_sha256`, current state, safe diagnostic code, attempt count, next due
time, lease owner/generation/expiry, last SQLSTATE when safe, PostgreSQL disposition,
adapter `acceptance_sha256`, created/updated/delivered times and optional transport
notification identity. A uniqueness constraint covers the full `DeliveryKey`;
another constraint prevents two accepted digests for the same logical identity and
destination fingerprint from being treated as one delivery.

`delivery_attempt` is append-only fixed-schema metadata keyed by delivery key and attempt
number. It stores start/end time, worker instance, outcome class, safe code,
commit-outcome classification and lease generation. It contains no payload, plate
text, DSN or raw exception. This is operational retry history; the accepted envelope
and PostgreSQL receipts remain the equality history.

The state machine is:

```text
pending ──claim──> in_flight ──new|identical──> delivered_unacked ──ack──> delivered
   │                    │
   │                    ├── transient/unknown ──> retry_wait ──due──> pending
   │                    ├── missing resolution ─> blocked_missing_resolution
   │                    ├── reference rejected ─> blocked_reference
   │                    ├── attempts exhausted ─> retry_exhausted
   │                    └── conflict/history/corruption ─> quarantined
   └── incompatible store/config ───────────────> incompatible
```

For direct SQLite delivery, `delivered_unacked` may transition immediately to
`delivered` in the same local transaction because there is no broker acknowledgment.
With a broker it is retained until `DeliveryTransport.ack` succeeds. A matching
redelivery that finds `delivered` is acknowledged without another PostgreSQL call.
A matching redelivery that finds `delivered_unacked` retries only the transport
acknowledgment. Either could safely call PostgreSQL again and receive `identical`,
but avoiding the unnecessary call makes the state boundary observable.

Terminal omission decisions remain inside the immutable accepted envelope and in
the PostgreSQL acceptance receipt. They do not become fact rows, separate queue
messages or mutable status flags.

## Commit and acknowledgment protocol

One delivery attempt follows this order:

1. In a short SQLite write transaction, claim one due `DeliveryKey` with a new
   fencing generation and finite lease. Commit before any network operation.
2. Reload and validate the accepted bytes, digest, resolution bytes/digest,
   destination binding and store compatibility. A missing or invalid dependency
   causes a durable blocked/quarantined transition without contacting PostgreSQL.
3. Resolve the destination through trusted host configuration. Obtain fresh
   `TrustedTenant` authorization for `(org_id, destination_id)` and a bounded,
   reset normal-role connection. Neither value comes from the payload.
4. Call `DB1Adapter.deliver(connection, entry, tenant=tenant,
   resolutions=resolutions)`. Do not wrap it in a caller transaction.
5. Only if the call returns `DeliveryResult(disposition='new'|'identical')`, verify
   `result.acceptance_sha256 == accepted.digest`. In a new short SQLite transaction,
   compare the lease generation and complete the matching delivery state. Record
   the disposition and digest, then commit with SQLite `synchronous=FULL`.
6. If a broker is present, acknowledge its message only after step 5 commits. On
   successful ack, durably transition `delivered_unacked` to `delivered`. The direct
   worker completes `delivered` in step 5.

There is no distributed transaction between SQLite, PostgreSQL and a broker. The
protocol is deliberately at-least-once and relies on P4's exact persistent receipts:

- If PostgreSQL commits but the response is lost, the adapter raises an unknown
  outcome, local state stays retryable and the next attempt obtains `identical`.
- If PostgreSQL returns success but the local state commit fails, no broker ack is
  allowed. Redelivery repeats PostgreSQL safely and then persists `identical`.
- If local delivered state commits but transport ack fails, broker redelivery sees
  the matching durable delivered state and retries only the ack.
- If transport ack succeeds but the final `delivered` state update fails,
  `delivered_unacked` remains durable. Reconciliation repeats the idempotent ack or
  verifies that the exact notification is no longer pending, then finalizes local
  state. It never calls PostgreSQL merely to infer broker state.
- A worker that loses its lease may finish a PostgreSQL call, but its stale fencing
  generation cannot overwrite newer local state. The newer owner may deliver again;
  PostgreSQL receipts reduce it to `identical`.

No exception, connection closure, SQLSTATE alone, transport receipt or timeout may
infer delivery success. `new` and `identical` are the only authorizing results.

## Restart, trusted context and resolution recovery

At startup, the worker opens the store using exact compatibility checks. Expired
`in_flight` leases become claimable; non-expired leases remain owned. It scans by
`next_attempt_at`, then stable sequence/destination order. It never asks producers
to recreate a completed observation.

New v2 entries recover their original `Resolutions` from the canonical resolution
snapshot accepted in the same transaction. The worker validates all frozen types,
the snapshot digest, unique keys/natural identities, and its relationship to the
accepted facts before calling the adapter. Actual registrations are still checked
by PostgreSQL.

Existing v1 stores do not contain the original complete `Resolutions`. Migration
must preserve their accepted bytes and place each destination in
`blocked_missing_resolution`. An operator may attach a historical resolution
snapshot only from an authoritative registration/audit source, after the migration
tool proves that it is structurally compatible with the accepted envelope. The
tool records the snapshot digest, source reference and operator action. It must not
derive omitted fields from SQL projections, choose current keys by name, register
objects, rerun inference or modify the accepted envelope. If the historical snapshot
cannot be established, the entry stays retained and blocked with the safe diagnostic
`MISSING_ORIGINAL_RESOLUTION`.

`TrustedTenant` is never persisted as reusable authority. On every database attempt,
`TenantAuthorizer.authorize` authenticates the running service and verifies that the
accepted organization may use the configured destination. A stored organization,
resolution provenance string or broker message can select the authorization request,
but cannot satisfy it. Authorization failure is retained as `blocked_authorization`.

## Evidence upload and media registration boundary

The full P5 plan also requires upload-before-reference and reconciliation of
uploaded-but-unreferenced evidence. This is upstream of accepted-row delivery. The
current outbox stores registered media metadata but deliberately stores neither crop
bytes nor local paths, so a database worker cannot safely add this responsibility.

Before a `MediaResolution` may enter the atomic acceptance transaction, a later
`EvidenceStager` must durably write the selected immutable bytes and a canonical
journal record containing organization, run/camera/track support, media kind,
content SHA-256, byte count and deterministic object identity. The journal commit
precedes upload. `MediaRegistrar` uploads idempotently by that identity, verifies the
remote content identity, creates or finds the exact authorized media registration,
and durably records its returned resolution. Only then may the resolution supplier
construct `Resolutions` and call outbox acceptance. The accepted envelope references
only this completed registration.

A crash before journal commit leaves coordinator evidence subject to the existing
in-memory limitation. A crash after journal commit resumes from the secured spool
without inference. A crash after upload but before registration retries the same
object identity. A crash after registration but before acceptance retries/reconciles
that registration and may produce an identical acceptance. A reconciler reports
staged, uploaded and registered objects which never become accepted references;
deletion follows an approved orphan-retention policy, never an automatic delivery
side effect. Coordinator evidence is released only after the final SQLite acceptance
callback succeeds, as today. P5 database delivery and its acknowledgments never call
coordinator release or evidence deletion.

This media path cannot be implemented safely until object-store credentials,
bucket/object naming, authenticated registration API, local spool protection and
orphan retention are defined. The recommended first slice therefore exercises
already registered or nullable media exactly as P4 does and leaves this boundary as
a separately reviewed follow-up.

## Failure classification and retry policy

| Failure | Durable classification | Automatic action |
|---|---|---|
| Connect failure, SQLSTATE class `08`, deadlock `40P01`, serialization `40001`, lock timeout `55P03`, statement/cancel timeout `57014`, or explicitly retryable host error | `transient_database` | Retry with backoff while budget remains. |
| `DatabaseDeliveryError(commit_outcome='unknown')` | `unknown_commit` | Retain pending and retry against PostgreSQL receipts; never infer success. |
| Local state write/commit error after DB success | `local_state_unknown` in process diagnostics only if it cannot be persisted | Do not ack transport; retry the same delivery key. |
| `ReferenceRejected` | `blocked_reference` | Stop automatic retries. Operator may resume unchanged work after registration repair; never alter the snapshot. |
| Missing resolution or failed tenant authorization | `blocked_missing_resolution` / `blocked_authorization` | Stop automatic retries and expose safe remediation code. |
| `DeliveryConflict` | `quarantined_payload_conflict` | Permanent quarantine; require investigation. Never retry automatically. |
| `HistoryMismatch` | `quarantined_history_mismatch` | Permanent quarantine; reconcile DB facts/receipts operationally. Never backfill silently. |
| `InvalidAcceptance`, digest mismatch or decoded record failure | `quarantined_invalid` / store-wide `corrupt` | Stop the item or store before DB access. Preserve files for inspection. |
| Store, adapter, destination or receipt compatibility mismatch | `incompatible` | Refuse delivery until an explicit migration/configuration decision. |
| Retry budget exhausted | `retry_exhausted` | Stop automatic attempts; manual resume starts a new bounded retry epoch. |

Recommended defaults are one initial attempt plus 11 retries, exponential full-
jitter delay with a 1-second base and 5-minute cap, a 30-second lease renewed only
between bounded operations, and at most one claimed item per worker until real load
evidence supports more. Connection, statement and lock bounds remain explicit;
the adapter's present defaults are 10 seconds and 2 seconds for statement and lock
waits. The connection factory must add connect and network bounds. These defaults
cap automatic churn without turning a temporary outage into infinite hot-looping.
Operators can manually resume a retry-exhausted item without changing its delivery
key or accepted bytes.

SQLite claims use `BEGIN IMMEDIATE`, compare-and-swap on lease generation, and never
hold a transaction during PostgreSQL or transport I/O. Multiple processes may
compete safely through separate SQLite connections on a supported local filesystem.
P4 advisory locks and receipts remain the final defense when separate stores,
duplicate broker messages or expired leases cause concurrent DB calls.

Inspection exposes state counts, total logical bytes, oldest pending age, next due
time, attempts, destination ID/fingerprint, accepted digest, resolution digest,
logical identity digest, safe failure code and SQLSTATE. Raw logical identity may
contain operational identifiers, so detailed inspection requires an authorized
view. Default logs use shortened digests and never include payloads or plate text.
Operator actions (`resume`, `attach_resolution`, `bind_destination`, future
`archive`) require a reason and append a safe audit event.

## Storage, capacity, retention and compatibility

Inspection is state-specific:

- `inspect_pending` covers `pending`, `retry_wait`, expired `in_flight` and
  `delivered_unacked` work due for action.
- `inspect_blocked` covers missing resolution/authorization, reference rejection,
  retry exhaustion, quarantine and incompatibility.
- `inspect_delivered` returns durable successes with their destination and digest.
- `inspect_all` remains keyset-paginated and validates every returned binding.

Capacity accounts separately for accepted envelope bytes, resolution bytes, delivery
state rows and bounded attempt/audit metadata, plus counts by pending/blocked/
delivered status. Operational monitoring must also measure the actual SQLite/WAL/SHM
files and free disk because logical limits do not cover page/index/WAL overhead.
Pending or blocked entries are never evicted to admit new work. Identical acceptance
and delivery retries remain possible at configured capacity.

The safe default is indefinite retention and no automatic deletion of accepted
payloads, resolution snapshots, terminal omissions, delivered markers or retry
history. This matches the current append-only acceptance rule and keeps local proof
for DB receipt reconciliation. A later retention policy must specify minimum DB
receipt age, backup/export, plate-data access, legal retention, successful receipt
verification for every destination and how tombstones prevent an old identity from
being accepted as new. Until that policy exists, capacity exhaustion stops new
acceptance rather than deleting history.

Adding P5 tables changes the SQLite store contract. The proposed compatibility
document separates:

```text
store_schema_version = 2
accepted_envelope_format = db1-local-outbox-v1
resolution_format = db1-resolution-snapshot-v1
delivery_state_format = db1-delivery-state-v1
identity/canonicalization/schema/source fingerprints = existing exact values
```

`LocalOutbox` must continue refusing a v1 database when opened by v2 runtime and
vice versa. An explicit offline migration command creates a new v2 file beside the
v1 source, opens and fully validates v1, byte-copies each accepted identity/payload/
digest without decoding and re-encoding it, revalidates the copy, marks resolution
missing, fsyncs/checkpoints/closes it, and only then reports it ready. It never edits
or replaces the source automatically. Operator-supplied destination bindings and
historical resolutions are separate explicit steps. Rollback is reopening the
untouched v1 file with the old runtime.

Any change to accepted envelope bytes, UUID namespace/policy, canonicalization,
P1 schema fingerprints, pinned builder/reference/policy sources, resolution codec,
delivery key, destination fingerprint or state interpretation requires a documented
compatibility version and migration decision. Existing stores must never be opened
and silently rewritten to the current code's interpretation.

## Optional external transport

`DeliveryTransport` is required only when the approved deployment separates the
durable outbox from the database worker. Its minimum semantics are at-least-once
publish/claim/ack, bounded claim recovery and opaque message identifiers. A
notification contains a version and the complete `DeliveryKey`; it contains no DSN,
password or tenant authority. If the worker cannot access the source SQLite store,
the transport contract must additionally carry authenticated canonical accepted and
resolution bytes or use a shared durable retrieval service. That security and trust
boundary is not defined today and blocks a remote-worker design.

If Redis Streams is selected later, use a provisioned stream and consumer group per
destination policy, reclaim abandoned pending entries after the delivery lease, and
`XACK` only after durable local delivered state. Publisher response loss may create
duplicate notifications; consumers deduplicate by `DeliveryKey`. Stream trimming
must not remove the only notification for undelivered work, and the SQLite scan must
be able to republish missing notifications. Redis persistence alone never replaces
SQLite accepted history or PostgreSQL receipts. Exact stream names, ACLs, TLS,
payload exposure, maximum lengths and outage retention require platform approval
before implementation.

## Verification plan

The core failure matrix is mandatory:

| Fault point | Expected durable state after restart/redelivery | Required proof |
|---|---|---|
| Crash before evidence-stage journal commit | No recoverable media claim; live coordinator evidence is retained only if its process survives. | Injected/subprocess spool test; document native-state limitation. |
| Crash after evidence staging, before/during upload | Secured staged bytes and deterministic object identity resume upload without inference. | Real selected object-store idempotency test. |
| Crash after upload, before/after media registration | Retry/reconcile the same object; record an orphan until registered and accepted, never invent a media key. | Real object-store plus registration integration test. |
| Crash before SQLite acceptance commit | No accepted or delivery record; coordinator evidence remains if process survives, otherwise native unfinished state is outside P5. | Real subprocess kill around SQLite transaction. |
| Crash after accepted envelope but before resolution/destination state commit | Impossible for new v2 acceptance because all commit together. | SQLite fault injection and subprocess kill. |
| Crash after complete SQLite acceptance, before first DB call | Pending exact envelope/resolution; replay uses identical IDs/times/omissions. | Reopen test plus digest equality. |
| Crash during/after DB statements before PostgreSQL commit | No partial facts/receipts; retry produces `new`. | Real PostgreSQL transaction test. |
| PostgreSQL commits, response lost before local success commit | Local work remains retryable; retry returns `identical`, then marks delivered. | Real PostgreSQL connection fault at commit boundary, not an injected cursor. |
| Crash after DB result, before local delivered commit | Same as lost response: retry receives `identical`. | Real PostgreSQL plus subprocess kill at local seam. |
| Local delivered commit succeeds, process crashes before transport ack | `delivered_unacked`; broker redelivery performs ack without duplicate facts. | Real selected transport integration test. |
| Transport ack has side effect then reports failure | Repeated ack/redelivery is harmless; local state remains delivered. | Real selected transport fault/reclaim test. |
| Restart with missing legacy resolution | Entry retained as blocked; no DB call, inference or invented key. | Migration/reopen test. |
| Two local workers claim same item | One active fence; stale state update rejected. | Real multiprocess SQLite test. |
| Two stores or duplicate messages deliver same exact envelope | PostgreSQL returns one `new`, remaining `identical`; one fact set. | Real PostgreSQL concurrent test. |
| Same logical identity/UID with changed accepted bytes | Local binding or PostgreSQL receipts quarantine conflict; no delivered mark. | Injected state test plus real PostgreSQL conflict test. |
| Reference disappears or is wrong | `blocked_reference`, no automatic hot retry, accepted bytes retained. | Real PostgreSQL registration mutation test. |
| SQLite state commit fails after DB success | No transport ack; later DB retry is identical and state recovers. | Real PostgreSQL with injected SQLite failure. |
| Store/destination/receipt compatibility differs | Delivery stops before facts; no store rewrite. | Injected compatibility tests and real DB receipt mismatch test. |

Injected tests cover state transitions, backoff calculation, fencing, codecs,
classification, secret-safe diagnostics, migration validation and interfaces. They
must be labelled as injected and cannot establish PostgreSQL or transport behavior.
Real PostgreSQL 16 tests cover commits, lost commit responses, exact retries,
concurrency, RLS, reference failures and rollback through a restricted login. Real
transport tests are required only after a transport is selected; mocks do not prove
claim expiry, redelivery, acknowledgment or retention. Subprocess SIGKILL tests cover
SQLite commit seams but do not claim host power-loss guarantees.

## Recommended first implementation slice

The smallest useful slice is the local direct-delivery path. It proves the hard
SQLite/PostgreSQL reconciliation boundary without prematurely choosing Redis or
wiring service startup.

Proposed files and exact public boundaries:

- Modify `vehicle/common/db1_outbox.py` only under an explicit store-schema v2
  implementation. Add atomic resolution/destination persistence, leasing, state
  transitions and state-specific inspection while preserving accepted v1 payload
  bytes and existing admission behavior. Its new acceptance boundary is
  `accept(completed, resolutions, *, destinations: tuple[Destination, ...])`, and
  `OutboxCompletionSink` receives the fixed configured destinations at construction.
  A successful return means accepted envelope, exact resolution snapshot and all
  initial destination rows committed together; it still does not mean DB delivery.
- Add `vehicle/common/db1_resolution.py` with
  `encode_resolutions(Resolutions) -> FrozenJSON` and
  `decode_resolutions(bytes) -> Resolutions`. It performs strict canonical decoding
  and has no lookup/network behavior.
- Add `vehicle/common/db1_delivery.py` with immutable
  `Destination(id, fingerprint, receipt_compatibility)`, `DeliveryKey`,
  `DeliveryWork`, and protocols:

  ```python
  class DestinationRegistry(Protocol):
      def resolve(self, destination_id: str, fingerprint: str) -> ConnectionFactory: ...

  class TenantAuthorizer(Protocol):
      def authorize(self, org_id: UUID, destination_id: str) -> TrustedTenant: ...

  class DeliveryStore(Protocol):
      def claim_due(self, worker_id: str, now: datetime, limit: int = 1) -> tuple[DeliveryWork, ...]: ...
      def record_success(self, work: DeliveryWork, result: DeliveryResult) -> None: ...
      def record_failure(self, work: DeliveryWork, failure: DeliveryFailure) -> None: ...

  class DatabaseWorker:
      def deliver_one(self, work: DeliveryWork) -> DeliveryOutcome: ...
  ```

- Add `scripts/migrate_db1_outbox_v1_to_v2.py` as an offline, source-preserving
  migration tool. It takes explicit source/output paths and destination metadata,
  never credentials, and leaves legacy entries blocked until original resolutions
  are attached.
- Add `vehicle/common/tests/test_db1_resolution.py`,
  `test_db1_delivery_state.py`, `test_db1_delivery_worker.py`, and extend the real
  `tests/db1_integration/test_postgres.py`. Add subprocess fixtures for every SQLite
  commit seam. No service main loop or external transport is part of this slice.
- Update `vehicle/common/db1_outbox.md`, `db1_adapter.md` and the P5 result report
  with exact compatibility, recovery and test evidence.

Acceptance criteria for the slice:

1. A new acceptance atomically persists unchanged v1 accepted bytes, exact
   resolution bytes and one explicit destination binding; reopening yields the same
   digests and terminal omissions.
2. The worker running through a normal NOBYPASSRLS PostgreSQL login reaches durable
   delivered state only from adapter `new` or `identical` with the matching digest.
3. Real PostgreSQL tests reproduce response loss/local-state failure and recover by
   exact identical retry with one analytical fact set and intact receipts.
4. Process-kill tests prove accepted-before-delivery recovery and no half-created
   local resolution/state record. Multiprocess tests prove leases and fencing.
5. Missing resolution, authorization failure, permanent conflict, history mismatch,
   corruption and incompatibility retain work with distinct safe states and cause no
   unbounded retry.
6. Pending, blocked and delivered inspection and capacity metrics are separate;
   no accepted payload/history is automatically deleted.
7. A v1 store is rejected by v2 runtime until the explicit migration tool copies it
   to a new file. Source checksums and accepted envelope bytes match exactly before
   and after migration; missing legacy resolution remains blocked.
8. Existing outbox, adapter, coordinator and 15 real P4 tests continue to pass.
   New injected and real tests report their scopes separately.

## Review decisions and dependencies

Recommended defaults for review are: direct co-located worker first; SQLite remains
the durable transport; one explicit destination per initial deployment; indefinite
accepted-history retention; no automatic pruning; one worker item at a time; 12
total automatic attempts with full-jitter exponential backoff; permanent errors
blocked/quarantined; and fresh tenant authorization on every DB attempt.

The following decisions remain concrete dependencies:

1. **Production receipt schema approval and immutability policy.** P4 proved the
   proposed schema only in disposable tests. Safe production delivery requires its
   provisioned compatibility row, RLS/grants and retention rules.
2. **Stable destination identity.** Operations must supply a non-secret destination
   ID/fingerprint and a bounded normal-role connection factory. Host/database strings
   alone are insufficient protection against accidental retargeting.
3. **Trusted authorization provider.** The host must define how a worker authenticates
   and obtains `TrustedTenant` for an organization/destination. Payload org IDs are
   not authority.
4. **Resolution snapshot source.** New work can persist the callback-supplied
   `Resolutions`; legacy v1 entries need an authoritative historical source or must
   remain blocked. Current outbox bytes cannot safely reconstruct it.
5. **Retention/security policy.** Platform owners must define access, encryption,
   backup, capacity and retention for payloads containing plate text and for
   diagnostic/attempt metadata before production use. The safe interim is no delete.
6. **Deployment topology.** A broker becomes required only if the approved worker
   cannot read the local outbox. That decision must also define authenticated payload
   retrieval, state ownership and transport retention. It is the dependency that
   determines whether Redis Streams, another transport or no external broker is used.
7. **Object storage/media registration.** P5's broader plan requires upload before
   media reference and orphan reconciliation. Current accepted entries contain only
   already supplied media registrations and no crop bytes. The first delivery slice
   can deliver nullable/already-registered media exactly as P4 does, but it cannot
   implement upload/reconciliation until the platform supplies the object-store,
   registration and retention contract.

Items 1–5 block safe production activation. They do not block implementing and
testing the proposed first slice with injected providers and the disposable P4
PostgreSQL fixture. No Redis connection, database provisioning, model execution,
upload or producer cleanup belongs in this design task.
