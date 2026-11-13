# P3b1 — durable local acceptance outbox, 2026-10-09

Implemented **T3 local delivery staging only**. T1 remains unfrozen; T2 remains
paused. A successful completion callback now means construction is durably accepted
in an explicitly supplied local SQLite store, ready for later transport inspection.
It does not mean platform delivery or registered database insertion.

Read the P3a and review-fix reports, then inspected the actual builder, immutable
records, schema validation, coordinator cleanup protocol/tests, P1 policies/schemas,
implementation-plan T3/P5 guidance and supervisor archive README. No preexisting
outbox or separate supervisor SQLite specification was found. The latest task
explicitly authorizes local SQLite while T1 remains unfrozen. Prior reported passes
were context; current test results below were executed in this session.

## Changed files and public interfaces

- `vehicle/common/db1_outbox.py`: `LocalOutbox`, `OutboxCompletionSink`, frozen
  `AcceptedEntry`/`Acceptance`, `logical_identity`, typed admission/storage/capacity/
  compatibility/corruption errors. Public methods: `accept`, `load`, bounded
  `inspect_pending`, `close`; context-manager support.
- `vehicle/common/tests/test_db1_outbox.py`: 23 tests with temporary local stores,
  synthetic explicit test registrations, pure evidence, injected coordinator
  consumers and real subprocess commit-fault/concurrency tests.
- `vehicle/common/db1_outbox.md`: public usage, persisted format, compatibility,
  transaction/cleanup protocol, settings, filesystem assumptions and resource limits.
- `vehicle/common/README.md`: optional sink documentation link.
- This report.

No builder, coordinator, inference loop, P1 schema/helper, supervisor or fixture
source was changed. All four P3a fixes are preserved: stable logical retry matching
when payloads disappear; retryable acceptance/acknowledgment/release cleanup;
last-matching-result-route model attribution; exact bounded numeric admission.

Usage is explicitly optional:

```python
outbox = LocalOutbox(path=explicit_local_path, namespace=stable_uuid_namespace,
                    validation=caller_loaded_p1_contracts,
                    max_entries=10000, max_payload_bytes=64 * 1024 * 1024,
                    lock_timeout=2.0)
sink = OutboxCompletionSink(outbox, resolutions=explicit_supplied_resolutions)
coordinator = ObservationCoordinator(classification, consumers, on_completed=sink)
```

The supplied resolution function receives immutable `CompletedVehicle` and returns
frozen `Resolutions`. The adapter provides no registration lookup, model execution,
external provider, decoding, authorization or upload implementation. It returns
`Acceptance` only after commit or recognition of previously committed identical
acceptance. It neither acknowledges classification nor releases consumers.

## Persisted format and compatibility

Schema version is SQLite `user_version=1`; tables `compatibility` and `accepted`
are private local staging tables, not copies of supervisor/shared application tables.
One accepted record contains the whole completed vehicle's eligible facts and
validated terminal omissions. Its unique logical identity is org UUID / natural
run ID / natural camera ID / service lifetime ID. Sequence is a keyset inspection
cursor, not a SQL fact identity.

Canonical P1 JSON envelope `db1-local-outbox-v1` stores logical identity, acceptance
`accepted_pending_transport`, per-fact row/terminal-omission decision, original retry
status, issues, metadata and diagnostic channels. Delivery and SQL projection
remain distinct canonical UTF-8 JSON strings with separate SHA256 hashes. Each
channel has a hash; the outer digest covers the entire envelope. Explicit absent
channels are null. Retained values use `FrozenJSON` and immutable construction
records. Decimals never round-trip through float; timestamps stay canonical UTC
with supported precision. No pickle or retained crop bytes are stored.

Compatibility metadata pins UUID5Policy version/stable namespace, canonicalization
version, format/schema version, exact row/diagnostic schema fingerprints, and P1
reference/row-policy/builder source fingerprints. Any mismatch rejects reopening
with `IncompatibleStore`; there is no migration or silent reinterpretation.
Source-only changes in pinned files also require an explicit future compatibility
decision. Capacity and lock-wait configuration may change on reopen, but a capacity
below existing usage is rejected.

Opening checks SQLite quick_check and streams every record for validation. Reads
check stored envelope/channel hashes and canonical encoding, shape, decisions,
namespace-derived UID, parent identity, diagnostic digest/identity, schemas with
FormatChecker, and reused P1 equality/date/route/parent policies. Recovered lookup
views verify retained natural/SQL consistency, not fresh registration. Malformed
stored acceptance raises `CorruptStore`; raw schema-validation traceback details
are suppressed. Hashes do not authenticate provenance or detect every deliberate
rewrite/deletion. Supplied registrations/media remain consistency assertions, not
independent authorization/object existence/platform-registration proof.

## Atomic acceptance, retries and cleanup

`accept` obtains `BEGIN IMMEDIATE` **before reading history**, checks compatibility,
loads/validates prior accepted construction, invokes the existing RowBuilder,
validates admission, and inserts one complete vehicle envelope or recognizes
identical acceptance. It commits before returning. Each concurrent caller uses
its own connection; SQLite serializes the recheck and insert. There is no separate
read-before-lock acceptance decision that can race.

A changed natural/SQL payload, disappearing accepted payload/projection or invalid
retry is rejected using the builder's existing conflict policy. Original accepted
identities, diagnostics, metadata, hashes and bytes are not overwritten. Identical
retries return the original immutable entry and create no new sequence or record.
A payload-free terminal omission is also an immutable accepted decision; changes
or later replacement with a row reject with `ACCEPTED_DECISION_CONFLICT`, without
implementing a second natural/SQL comparison rule.

Admission is all-or-nothing. Blockers, conflicts, clock quarantine, incomplete plate
settlement, unresolved development/missing resolution and natural-only proposals
raise typed `AdmissionRejected`. The exception retains the review proposal and code
in memory; it does not persist a rejection log. A resolved parent is mandatory.
Valid first-time terminal omissions may accompany eligible rows, including current
unsupported manufacturer and unavailable consumer omissions. Unsettled work is not
converted to unreadable or failure_before_model. Missing optional registered crop
media remains nullable if P1 permits it. Pure RowBuilder's unresolved-to-resolved
transition remains valid; this outbox never accepts the unresolved first proposal.

Returning from the callback is the coordinator acceptance point. Classification
acknowledgment and consumer releases remain exclusively in its existing independent
cleanup progress. If commit succeeds but the callback then raises, the next callback
recognizes identical durable acceptance and lets cleanup proceed. If callback return
succeeds but cleanup fails, cleanup retries skip the sink entirely. Finalization/
recognition is not rerun; completed evidence remains pending and drain stays guarded.

## Effective storage settings and bounds

Existing VM: Python **3.11.15**, sqlite3 runtime **3.37.2**, jsonschema **4.26.0**.
Temporary test stores were on **ext4** (`findmnt -n -o FSTYPE -T /tmp`). Tests verify
WAL, synchronous FULL=2, busy timeout=2000 ms and automatic checkpoint=1000 pages.
Every connection checks these settings. The lock-timeout test uses 50 ms; public
configuration permits 1 ms–60 seconds. SQLite filesystem/open/read/write/lock
failures raise `StorageError`; uncertain commit failures require a retry against
history, not an assumption of rollback or success.

Defaults bound **10,000 accepted vehicles** and **64 MiB total stored envelope
bytes**. Both limits are transactionally checked; identical retries remain possible
at capacity. No capacity failure removes existing data. One vehicle may contain
multiple eligible fact rows and terminal omission records. These are logical
payload limits; SQLite pages/indexes/duplicate identity storage/WAL/SHM files,
reader-held WAL, temporary I/O and producer evidence memory are outside that byte
sum. There is no filesystem quota or forced checkpoint/deletion policy. Lock waits
are bounded; kernel/filesystem I/O stalls are not bounded by that timeout.

`inspect_pending(after_sequence=0, limit=100)` supports keyset pagination with page
limits 1–1000. Entries remain pending accepted transport work; inspection never marks
delivered or deletes. `load(entry.logical_identity)` recovers accepted construction
without a decoder, tracker, OCR or original crop. The host supplies a persistent
path/namespace, storage directory permissions, disk protection/retention/backups and
SQLite-compatible local filesystem locking/fsync. Database/WAL/SHM sidecars belong
to the store and must not be independently discarded or copied unsafely.

## Exact executed checks

All Python commands used the existing interpreter and environment prefix
`PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`. No dependencies installed,
models/providers executed or payload/raw plate text printed.

| Arguments following that prefix | Executed result |
|---|---|
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**; full P1 validator with format checks. |
| `-m unittest vehicle.common.tests.test_db1_rows` | **72 passed**, 1.671 s. |
| `-m unittest vehicle.common.tests.test_foundations` | **21 passed**, 0.014 s. |
| `-m unittest vehicle.common.tests.test_coordinator_cleanup` | **6 passed**, 0.947 s. |
| `-m unittest vehicle.common.tests.test_db1_outbox` | **23 passed**, 15.447 s, final run after settings/redaction checks. |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests` | **24 passed**, 0.395 s. |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests` | **33 passed**, 0.083 s. |
| `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests` | **57 passed**, 0.166 s; provider-timeout output is from the existing injected test. |

The focused outbox suite initially passed 20 tests; three added checks explicitly
assert distinct unsettled/clock reasons, supplied unresolved-development rejection
and corrupt-prior rejection without overwriting. The expanded 23-test suite passed;
the final rerun covered stricter effective-checkpoint verification and redacted
corruption exception chaining. Other suites were not rerun after documentation or
outbox-only adjustments because their code/contracts did not change.

Static/integrity checks also passed: AST parsing of both changed Python files,
whitespace checks on all five changed files, and `git diff --check` (exit 0).
`sha256sum -c /tmp/p3b1-supervisor.sha256` verified the supervisor archive;
`sha256sum -c /tmp/p3b1-fixtures.sha256` verified all eight fixture files.
The unchanged archive SHA256 is
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.

## Demonstrated process fault boundaries

All child processes use the same existing interpreter, temporary explicit stores
and synthetic test evidence, not mocked SQLite persistence:

- Injected Python exception immediately **before commit**, after insertion: rollback
  leaves no vehicle envelope; subsequent acceptance retains all three eligible facts.
- Real subprocess **SIGKILL in the before-commit seam**, after insert but before
  commit: reopening recovers zero accepted vehicles and retries accept one complete
  envelope. Already committed compatibility initialization remains valid.
- Real subprocess **SIGKILL in the after-commit seam**, immediately after commit
  and before normal callback return: reopening recovers exactly one acceptance;
  retry is identical and creates no duplicate.
- Injected exception **after commit/before callback return**: direct adapter and
  coordinator retries find that acceptance; no extra acceptance/recognition occurs.
- Injected consumer release failure **after successful callback acceptance**:
  coordinator skips reacceptance, retains staging and retries required cleanup;
  already released colour is not released again when plate cleanup retries.
- Two synchronized real subprocesses with separate connections: identical inputs
  produce one accepted plus one identical result; conflicting inputs produce one
  accepted plus one rejected result. The winner is deliberately not prescribed.

Also executed: exact Decimal/timestamp/null recovery; atomic parent/colour/plate
acceptance with manufacturer omission; immutable terminal omission retries;
conflicting/disappearing retry rejection after reopen; unresolved/blocked/quarantined/
unsettled admission and retained coordinator evidence; logical entry/byte capacity
rejection; bounded lock wait; paginated inspection; incompatible namespace/schema/
policy/version; envelope, inner hash, schema, parent, decision and SQL outcome
corruption rejection. Existing builder/foundation/cleanup suites cover the four
review fixes independently, including invalid/disappearing payloads and release
failures before/after side effects.

## Limitations and transport readiness

The tests demonstrate **process-crash** transaction/retry boundaries, not arbitrary
host power loss, controller write-cache behavior, damaged hardware, backup restore,
network filesystems or hostile database edits. Full SQLite commits depend on the
filesystem/storage honoring locking and fsync. Operational retention, encryption,
quotas, checkpoint monitoring and authenticated provenance remain host work.

Accepted construction/history recovers after restart; native unfinished lifetimes,
capture/tracker state, retained crop bytes, unaccepted completed evidence, pending
OCR and coordinator cleanup progress do not. Nothing here recovers an invalid clock
run or supplies new run registrations. New capture requires a new explicit run
until a separately implemented native-state recovery design exists. No durable
rejection log or diagnostic-only acceptance is claimed.

The local pending-entry interface is ready for subsequent **explicit transport**
design. That phase still needs platform registration/authorization, media-object
registration/retention, downstream acceptance and delivery acknowledgment/retry
policy, and host storage lifecycle. No pending entry is marked delivered here.
Nullable media/manufacturer/domain-mapping dependencies from P3a remain unchanged.

Supervisor archive/SQL and all historical fixtures were preserved. No PostgreSQL,
Redis, application-database connection, uploads, model/provider execution, RTSP
change, training, T2 work, T1 freeze, commit or push occurred.
