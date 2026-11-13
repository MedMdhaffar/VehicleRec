# Narrow DB1 PostgreSQL adapter (P4)

`DB1Adapter(validation=caller_loaded_p1_contracts).deliver(connection, entry,
tenant=trusted_tenant, resolutions=explicit_resolutions)` accepts a recovered frozen
`AcceptedEntry`, an externally authenticated/authorized `TrustedTenant(org_id,
authority)` and frozen `Resolutions`. The caller supplies an idle autocommit psycopg
connection to PostgreSQL 16; no DSN, connection lookup or connection creation is
implemented. The adapter does not infer authority from entry org or implement
caller authentication. Do not construct TrustedTenant from an untrusted body.
Entries must come from the trusted local outbox loader, not user-submitted JSON.

It rejects caller transactions, other server majors, superuser/BYPASSRLS execution
or session roles, absent/disabled forced RLS tables, and receipt schema compatibility
mismatch. A connection configured by the host with leaked session-wide tenant state
must be reset by the host before checkout; the adapter always overrides tenant scope
transaction-locally and never uses session-wide tenant state. It sets `app.org_id`
through `set_config(...,true)`, the exact supervisor mechanism, before any application
query. Search path is transaction-local pg_catalog; tables are schema-qualified.
Synchronous commit is transaction-local on; lock/statement timeouts default to
2000/10000 ms and are configurable from 1–60000 ms. These bound PostgreSQL statement
and lock waits, not arbitrary client/kernel/network hangs. The connection factory
should provide its own bounded connect/network policy.

Inside one transaction, the adapter checks actual organization, SCD2 camera/version/
timezone/validity, run/detector/source/start/service version, vehicle lane, date,
vehicle class/group, exact grouped colours/categories, route/model names/versions/
tasks and media registration. Supplied records alone are not authorization or
actual registration; all relevant keys are looked up under RLS. It reuses P1
snapshot semantics against real lookup records. Supporting timestamps must lie in
run/camera validity; local dates must exist in the registered calendar. File input
media must be a registered clip with matching org/camera and nullable-or-matching
run ownership. Selected crop references require exact org/run/camera/kind and
encoded metadata, plus supplied original service/frame support. Nullable media
remains nullable; terminal omissions create no invented analytical rows.

Object-store existence, upload completion and raw crop bytes are not revalidated.
The local outbox retains accepted natural object metadata, not original raw crop
bytes; DB1 has no raw-crop fingerprint/frame registration columns. P3a's original
support admission and a trusted accepted-entry origin remain necessary. Supplied
support claims cannot independently prove object contents or plate accuracy.
No new manufacturer producer is enabled: current make is always omitted. The
separate `check_manufacturer_reference` structural helper refuses nonexistent
manufacturers even with null vehicle-model keys; it provides an explicit service rejection in addition to the supervisor
direct FK. Its tests are not claims of current manufacturer capability.

## Equality and the proposed supporting schema

See `tests/db1_integration/acceptance_proposal.sql`: separately provisioned
`db1_delivery.compatibility`, `acceptance` and `row_receipt`, version v1. Runtime
performs no DDL. This is **proposed**, needs platform review, and is not a supervisor
SQL amendment. Its compatibility metadata pins exact P1 schema hashes and equality/
format versions. Every receipt and fact commits together.

The whole accepted canonical outbox envelope is retained as bytea plus digest. It
covers logical identity, all natural/SQL channels, diagnostics, metadata, issues and
terminal decisions, independent of local sequence. Per-row UID/kind receipts retain
exact natural and SQL bytes/hashes as well. Equality therefore never reconstructs
numbers from PostgreSQL REAL. Advisory transaction locks on the vehicle identity and
all UID/kind identities are acquired in deterministic order. READ COMMITTED is
required; history is read/rechecked after locking. Hash collisions only serialize
unrelated work. The algorithm supports identical/conflicting concurrent first
submission in design; its real PostgreSQL tests remain blocked in this VM.

New deliveries reject any visible fact/vehicle identity lacking trustworthy receipt
history. Identical deliveries require byte-equal whole acceptance, consistent row
receipts and existing matching facts. REAL integrity checks use PostgreSQL casts of
the original exact Decimal parameter after receipt equality; Python double-rounding
is avoided. Other columns are checked directly. Receipts establish original exact
content equality; binary REAL checks merely detect changed/missing analytical data.
Historyless facts are not automatically backfilled or treated as identical.

`DeliveryResult(disposition='new'|'identical', acceptance_sha256, fact_count,
omission_count)` is returned only after commit context exits successfully.
`DeliveryConflict`, `ReferenceRejected`, `HistoryMismatch` and `InvalidAcceptance`
are safe typed rejections. `DatabaseDeliveryError` exposes SQLSTATE without payload
or credential details; `commit_outcome='unknown'` marks an error at the commit
boundary, while statement failures report `not_committed`. Uniqueness collisions
fail closed as conflicts. All error paths preserve the local entry; normal success
also leaves it pending. No outbox delivered mark/delete, Redis or acknowledgment
implementation exists. Exception chaining of driver/schema details is suppressed.

Registrations are expected to follow the platform's versioned/immutable lifecycle.
Lookups are not platform registration writes and do not lock catalogue records
against all concurrent administrative changes. Reference mutation/retention,
receipt immutability/authorization and schema lifecycle need operational policy.
This module is independently unit-tested and ready for the explicitly gated
[isolated test fixture](../../tests/db1_integration/README.md); it is not database-
integration-validated or approved for deployment.
