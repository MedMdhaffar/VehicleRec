# Local DB1 acceptance outbox (P3b1)

`LocalOutbox` uses Python's SQLite module for local acceptance only. Import it and
`OutboxCompletionSink` from `vehicle.common.db1_outbox`. Supply an explicit filesystem
path, stable UUID namespace and caller-loaded `ContractValidation`. The parent
storage directory must already exist. There are no development registrations,
namespace/path defaults, application-database connections or automatic migrations.

```python
outbox = LocalOutbox(path=supplied_local_path, namespace=stable_namespace,
                    validation=loaded_contracts, max_entries=10000,
                    max_payload_bytes=64 * 1024 * 1024, lock_timeout=2.0)
sink = OutboxCompletionSink(outbox, resolutions=supplied_resolutions_for_completed)
coordinator = ObservationCoordinator(classification, consumers, on_completed=sink)
# Finish capture, flush closures until accepted/cleaned, shut down consumers,
# then close the outbox. Failed acceptance/cleanup retains coordinator evidence.
```

The resolution callback receives immutable completed evidence and returns explicitly
supplied `Resolutions`; this module performs no registration lookup. No inference
loop is wired automatically. `sink(completed)` returns `Acceptance(entry, identical)`
only after commit. The coordinator ignores this return value, then uses its existing
retryable acknowledgment/release protocol. The sink performs neither action.

`accept(completed, resolutions)` starts `BEGIN IMMEDIATE`, validates compatibility,
loads accepted history under the write lock, constructs through RowBuilder, rejects
ineligible proposals, and inserts all accepted vehicle facts/terminal omissions in
one transaction. It then commits before returning. Another connection/process must
wait for that lock before reading history for acceptance. `RowBuilder` alone compares
natural/SQL row retries. Previously accepted payload disappearance remains conflict.
Unchanged retries return the original accepted entry without rewriting diagnostics,
metadata or hashes. Terminal omissions are also immutable decisions: a changed or
new row replacing one raises `ACCEPTED_DECISION_CONFLICT`.

`AdmissionRejected.proposal` retains review diagnostics in memory, and `.code`
distinguishes `CONSTRUCTION_INELIGIBLE`, `PAYLOAD_CONFLICT` and
`ACCEPTED_DECISION_CONFLICT`. It does not authorize acceptance. Blocked, unresolved,
quarantined, conflicting or unsettled proposals reject the whole transaction; a
resolved parent is mandatory. Valid first-time terminal omissions can accompany
eligible rows. Missing optional registered crop media remains an explicit nullable
reference where P1 admits it. Accepting a development natural-only proposal and
resolving it later is deliberately unsupported here; the pure builder still supports
that transition. Rejections are not persisted and must not cause coordinator drain.

## Format and compatibility

SQLite `user_version=1`, two private local tables:

- `compatibility(id=1, metadata BLOB)`: exact canonical compatibility document.
- `accepted(sequence INTEGER PRIMARY KEY, identity BLOB UNIQUE, payload BLOB,
  digest TEXT, payload_bytes INTEGER)`: one pending-transport accepted vehicle per
  logical identity, `(org UUID, natural run ID, natural camera ID, service track ID)`.

The envelope is canonical P1 JSON, format `db1-local-outbox-v1`, decision
`accepted_pending_transport`. Each fact records kind, decision (`row` or
`terminal_omission`), original retry status/issues and canonical UTF-8 JSON strings
for delivery, SQL, diagnostic and metadata channels, with separate SHA256 hashes.
Absent delivery/SQL remain explicit null. Its outer hash covers all stored channels,
decisions, identity and metadata. `payload_bytes` counts that complete envelope,
including escaping overhead. No pickle or float round-trip is used. Reload returns
frozen `AcceptedEntry`, `Construction`, `FactResult` and `FrozenJSON` records, with
exact Decimal decoding and canonical explicit-offset UTC timestamps/nulls.

Compatibility includes schema/format versions, UUID5Policy version and namespace,
canonicalization version, exact row/diagnostic schema hashes, and source fingerprints
of the P1 reference helpers, row policies and builder. **Even a source-only change**
in those files requires an explicitly designed compatibility/migration decision.
Changed schemas/policy/namespace/version reject opening. There is no migration.

Open streams/validates all retained records and SQLite quick_check under the write
transaction. Reads/inspection also validate envelope/channel hashes, canonical
encoding, decisions, UUID policy, parent/diagnostic identities, row schemas with
format checking, and reused P1 date/equality/route/parent semantics. Stored lookup
views assert internal natural/SQL consistency; they do not prove current platform
registration. Acceptance originally uses the builder's actual supplied resolution
checks. Corruption raises `CorruptStore`; incompatible metadata raises
`IncompatibleStore`. Hashes detect accidental corruption, not malicious rewrites,
record deletion or authenticated provenance. No raw payload is emitted by this
module; corruption validation traceback details are suppressed.

## Durability, limits and recovery

Every connection sets and verifies WAL, synchronous FULL (2), busy timeout
(default 2000 milliseconds) and automatic checkpoint threshold 1000 pages.
`settings` exposes the effective values. Lock timeout is configurable from 0.001
to 60 seconds. Connections are SQLite's default single-thread-owner connections;
concurrent callers use separate connections. Only local filesystems with SQLite
locking, shared WAL access, correct fsync behavior and persistent database/WAL/SHM
files are supported. Filesystem permissions, disk encryption, retention, backups,
free space and stable namespace configuration belong to the host. Do not copy only
the database file while WAL is active or delete sidecars as cleanup. A successful
SQLite FULL commit is the local acceptance boundary; this is not downstream delivery.

`max_entries` counts accepted completed vehicles, not individual fact rows.
`max_payload_bytes` bounds the sum of canonical persisted envelope lengths, not
SQLite page/index/header bytes, duplicated identity columns, WAL/SHM files, retained
reader snapshots, temporary I/O, or evidence memory. Both limits are checked under
the write lock before insert, and at reopening. Identical retries work at capacity.
Capacity exhaustion raises `CapacityExceeded` without removing accepted entries.
Disk-full/lock/open/read/write errors raise `StorageError`; uncertain commit outcomes
must be retried against durable history. OS/storage I/O stalls are not bounded by
the lock timeout. There is no automatic deletion, delivered flag or rejection log.

`inspect_pending(after_sequence=0, limit=100)` returns a keyset page (limit 1–1000)
of immutable accepted vehicles. Use the last sequence as the next cursor.
`load(logical_identity(completed))` reloads prior construction; an inspector can
also supply the recovered `entry.logical_identity` after restart. Inspection does
not accept, deliver, acknowledge, delete or regenerate recognition. Acceptance
is durable independently of subsequent coordinator cleanup. Commit followed by
callback error can retry to identical acceptance; successful callback followed by
cleanup failure skips the sink and retries only cleanup.

Unfinished lifetimes, trackers, capture, retained crops, unaccepted completed evidence
and unsettled OCR remain in memory. They are not recovered from this outbox. Hosts
need a new explicit run for subsequent capture until separate native-state recovery
exists. Pending entries are ready for a later transport design, not a platform
registration proof, delivered acknowledgment or persisted coordinator cleanup state.
Tests use temporary ext4 stores and real child-process SIGKILL at explicit commit
boundaries. They do not prove host power-loss/controller/filesystem guarantees.
