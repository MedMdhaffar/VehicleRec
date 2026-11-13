# P4 — narrow PostgreSQL adapter, revalidated 2026-10-10

Implemented the independently testable DB1 adapter and a gated disposable PostgreSQL
16 test fixture. **Real database integration is blocked and not claimed.** The VM
has an available driver but no usable PostgreSQL 16 server/provisioning path. T1
remains unfrozen; T2 remains paused. No Redis transport or production deployment
was implemented.

Read P4/P5 and gap decisions, the P3b1 report, actual builder/records/P1 validation,
local outbox/coordinator boundaries, and supervisor archived SQL/README/seed. The
archive was read only. Existing reports were context, not database proof.

## Files and interfaces

- `vehicle/common/db1_adapter.py`: `DB1Adapter(validation=..., lock_timeout_ms=2000,
  statement_timeout_ms=10000)`, pure `prepare(entry, tenant)`, database
  `deliver(connection, entry, tenant=..., resolutions=...)`, frozen `TrustedTenant`
  and `DeliveryResult`, typed rejections/storage errors, structural manufacturer
  reference guard. No connection creation, DDL, inference or outbox mutation.
- `vehicle/common/tests/test_db1_adapter.py`: **22 injected/unit tests**, not
  PostgreSQL integration. They exercise actual adapter logic, real temporary SQLite
  accepted entries, injected registration cursors and injected driver faults.
- `tests/db1_integration/test_postgres.py`: **15 opt-in real database test methods**;
  fixture skipped here, none executed. Real normal-role connections are required.
- `tests/db1_integration/acceptance_proposal.sql`: separately proposed service-owned
  receipt schema; supervisor SQL remains unchanged. Not platform-approved schema.
- `tests/db1_integration/README.md`: opt-in provisioning and scoped cleanup.
- `vehicle/common/db1_adapter.md`, common README link, this report.

Public delivery consumes frozen recovered `AcceptedEntry`, its exact canonical
projections, trusted tenant context and original explicitly supplied `Resolutions`.
It generates no UIDs/timestamps and performs no producer evidence change. Terminal
omissions stay decisions in the receipt envelope, not SQL fact rows. The original
four P3a fixes and P3b1 format/compatibility fingerprints are unchanged.

## Environment findings and isolated fixture

Executed preflight using the existing VM environment:

- Python **3.11.15**; available **psycopg 3.3.4**. psycopg2 and pg8000 unavailable.
- `psql`, `postgres`, `initdb`, `pg_ctl` are absent from PATH.
- `/usr/lib/postgresql`, `/usr/local/pgsql`, `/opt/postgresql` and
  `/var/run/postgresql` are absent.
- `/usr/bin/docker` exists. `docker image ls --format ...` failed: permission denied
  for `/var/run/docker.sock`. `sudo -n docker image ls --format ...` failed:
  **a password is required**. Available images/server versions could not be checked.

Therefore no suitable PostgreSQL 16 server or provisioning access is established.
No model/dependency downloads or installations, Docker pulls, privilege/group/global
setting changes, shared/production database connections or supervisor checker calls
were made. **No PostgreSQL database or role was created**, so database cleanup did
not execute. Unit-test temporary SQLite stores were scoped and removed normally.

The prepared fixture requires both `DB1_P4_ISOLATED=1` and a privately supplied
`DB1_P4_ADMIN_DSN` for a host-declared **dedicated disposable local PG16 server**.
It permits only local TCP and maintenance database `postgres`, checks server major
16 and administrative provisioning privileges, and uses a five-second connect
limit. Environment credentials are not printed. Local TCP alone is not evidence
that a server is safe to provision; the explicit isolated-server declaration is
necessary. With no opt-in, discovery attempts no database connection.

Fixture database/owner/application roles use fresh random UUID-suffixed names
`db1p4_db_*`, `db1p4_owner_*`, `db1p4_app_*`. A created-resource list constrains cleanup
to exact successfully created names, including partial setup. Administrative DDL,
registration seeding, grants and intentional corruption/constraint changes are
separate from application operations. Owner is NOLOGIN; application role is actual
LOGIN NOSUPERUSER NOBYPASSRLS, with SELECT registrations/facts/receipts, INSERT
facts/receipts, schema/identity-sequence USAGE and no UPDATE/DELETE or ownership.
Application tests connect as that role, not as an administrator switched to it.

The fixture applies unmodified archived `01_shared.sql` and `02_vehicle.sql` under
its owner. `03_dev_seed.sql` was inspected, not applied: explicit synthetic test
registrations are used instead, with the supervisor's vehicle lane/calendar and
separate exact grouped-colour/recognizer/category/media records. Synthetic media
registration metadata is not an upload or actual object-store existence proof.
The supervisor `check_contract.py` is never invoked. Its fixed resource names are
not used or dropped.

## Tenant, transaction and reference rules

`TrustedTenant(org_id, authority)` is an explicit authenticated/authorized **host
assertion**, not an authorization implementation or proof from a payload. Missing
context or payload/context org mismatch fails pure admission before any application
query. Never derive it from an untrusted request's org_id. Accepted entries must
come from the trusted outbox loader, not request-supplied JSON.

Delivery requires an idle autocommit connection to PG16 so the adapter controls the
whole transaction and commit boundary. It sets the exact supervisor RLS mechanism
using transaction-local `set_config('app.org_id', ..., true)` before application
lookups. It checks execution and session roles are normal NOBYPASSRLS, and all nine
tenant tables (supervisor RLS tables plus proposed receipts) enable/force RLS.
Search path is transaction-local pg_catalog; target tables are qualified. Synchronous
commit is transaction-local on. Lock and statement timeouts default to 2000/10000 ms,
configurable 1–60000 ms. Caller/network/kernel stalls need separate connection/host
bounds. READ COMMITTED is required for the post-lock history recheck.

Actual registration queries check:

- Organization, camera natural/key/org/timezone/SCD2 validity and uniqueness at run
  start; run natural/key/org/camera/lane/detector/start/service version/source/input.
- Vehicle lane/service, detector and selected recognition model names/versions/tasks,
  class name/vehicle group, exact grouped colours and approved category mappings.
- Each fact's registered camera-local date and supporting receipt within camera/run
  validity; host replay receipt remains the original timestamp authority.
- Supplied input clip and selected crop media: database presence, exact org/run/
  camera/kind, timestamp, bucket/object key, encoded fingerprint/size/type/dimensions.
  Input clips may have null run_key; crop ownership must match the run. Selected
  crop supplied service/frame support must match the immutable fact/diagnostic.
  Nullable references remain nullable when permitted by P1.

P1 snapshot semantics are reused against actual lookup records, rather than treating
supplied numeric keys as database registration. Media ownership/kind checks cover
links for which supervisor fact-to-media FKs only enforce key existence. No registration
writes occur in the adapter. Versioned registrations are expected to be immutable;
lookups do not lock every dimension against concurrent administrative changes.
Registration mutation/retention and authorization need platform operational policy.

Object-store existence, raw crop contents/fingerprint and uploads are not independently
rechecked. P3a admitted raw crop support before local acceptance; the outbox retains
accepted natural registered-object metadata, not original crop bytes. DB1 does not
store raw-crop frame/fingerprint support columns. Supplied support assertions alone
cannot prove accuracy, registration authorization or object contents.

## Manufacturer reference checks — corrected source review, 2026-10-10

The previous report incorrectly claimed a missing direct manufacturer FK. Fresh
inspection of the unchanged archive confirms `manufacturer_key int REFERENCES
dim_manufacturer` in `fact_vehicle_make`. Its SHA256 is unchanged. This was a source
review error, not a supervisor schema update. No platform migration for this alleged
gap is required.

Current make production stays unsupported omission. The explicit structural helper
`check_manufacturer_reference` looks up a nonnull manufacturer even with null vehicle-
model key, and checks model-to-maker ownership when present. It complements the SQL
FK with a typed service rejection. A source regression now asserts the direct FK
exists in the actual archive. The unexecuted real test expects ForeignKeyViolation
for a nonexistent manufacturer/null model, using a savepoint to restore transaction
state after the expected rejection. This is source-validated test design, **not an
executed PostgreSQL constraint result**.

## Persistent equality design: proposed supporting schema

Supervisor facts have per-table UNIQUE UID; vehicle additionally has unique
(run_key, track_id). Those constraints detect collisions but cannot establish exact
natural payload equality. REAL values can erase exact original numeric differences.
`ON CONFLICT DO NOTHING` is not used as the acceptance policy.

Proposed `db1_delivery` v1 is separate from DB1:

- `compatibility`: immutable supporting-format/equality version and exact P1 row/
  diagnostic schema fingerprints.
- `acceptance`: one org/logical-vehicle receipt retaining the exact canonical accepted
  outbox envelope and SHA256. This covers natural/SQL channels, diagnostics, metadata,
  issues and terminal decisions, independent of the local sequence number.
- `row_receipt`: unique kind/UID receipt with exact canonical natural and SQL bytes/
  hashes, tied to the vehicle acceptance. Receipt tables force the supervisor tenant
  policy. Application role has no receipt UPDATE/DELETE permission.

Runtime does not create/migrate this schema. It needs separate platform approval
and provisioning before delivery; its fixture use is not schema approval.

Transaction advisory locks on the vehicle logical identity and all kind/UID identities
are acquired in deterministic order; same identity retries re-read history after
locking. A changed envelope or UID collision conflicts. A byte-equal envelope requires
consistent per-row receipt bytes/hashes and existing matching facts before identical
retry is returned. Visible facts without trustworthy history reject; there is no
silent backfill from rounded SQL. Parent/children and all receipts insert in one
transaction, with parent first. Terminal omissions create no analytical rows.

SQL REAL integrity is checked only after canonical receipt equality, with PostgreSQL
casting the original exact Decimal parameter as on insertion. Python Decimal-to-
double-to-binary32 conversion is avoided because it can double-round. Receipt bytes,
not binary REAL, establish equality. Non-REAL SQL fields are checked directly.
This design's actual PG16 advisory locking, transactions, casts and concurrent
retry behavior remain unexecuted integration gates.

## Commit results and local pending state

Only after successful commit context exit does delivery return frozen
`DeliveryResult(disposition='new'|'identical', acceptance_sha256, fact_count,
omission_count)`. Safe typed errors distinguish invalid acceptance, reference
failure, payload conflict, missing/inconsistent history and database errors.
`DatabaseDeliveryError` retains SQLSTATE; commit-boundary failures mark outcome
`unknown`, statement/closed-connection errors mark `not_committed`. Driver/schema
exception chaining is suppressed to avoid exposing credentials/full row details.

Both success and failure preserve the local outbox record, pending status and
canonical bytes. No delete, delivered marker, Redis acknowledgment or coordinator
cleanup implementation is added. An uncertain commit must retry against persistent
history after obtaining a usable connection. No successful delivery is inferred
from an exception.

## Exact executed checks

The repeated P4 request was revalidated on 2026-10-10. The fresh preflight found
the same unavailable PostgreSQL/provisioning access; no database was contacted.
The source review corrected the manufacturer-FK statement and the corresponding
unexecuted fixture expectation, and added an archive-source regression. No adapter
delivery behavior, outbox format, supervisor SQL or historical fixture changed.

Every Python command used the existing VM prefix
`PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`.

| Arguments following prefix | Executed result |
|---|---|
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**, complete P1 with format checking. |
| `-m unittest vehicle.common.tests.test_db1_adapter` | **22 passed**, final run **5.991 s**; injected/unit checks only. |
| `-m unittest discover -s tests/db1_integration` | **0 executed tests**, **1 fixture skip**, 0.000 s. No genuine PostgreSQL results. |
| `-m unittest vehicle.common.tests.test_db1_rows` | **72 passed**, 1.688 s. |
| `-m unittest vehicle.common.tests.test_db1_outbox` | **23 passed**, 15.425 s; includes existing real SQLite/subprocess checks, not PostgreSQL. |
| `-m unittest vehicle.common.tests.test_foundations` | **21 passed**, 0.014 s. |
| `-m unittest vehicle.common.tests.test_coordinator_cleanup` | **6 passed**, 1.040 s. |

Injected checks cover exact immutable admission, missing/cross-tenant trusted context,
terminal omissions, blockers/lost payloads, registration/SCD2/model/class/date/
colour/media mismatches, missing manufacturer/null model, ordered locks and staged
inserts, exact history retry/conflicts, historyless facts, REAL integrity query logic,
normal roles/forced RLS guards, PG16/connection boundaries, post-commit return and
injected statement/uncertain-commit failure preserving the local entry. They do not
prove PostgreSQL behavior. Initial mock context-manager setup errors were corrected;
final runs include added receipt/history/RLS and exact-cast checks.

Real fixture methods are prepared for parent/colour/plate and omission insertion,
child rollback, reopened retries, same-UID Decimal conflicts/REAL rounding,
concurrent identical/conflicting first delivery, tenant absence/access/isolation,
reference/category/date/media failures, selected crop registration, legacy facts,
manufacturer FK rejection and normal-role failure preserving outbox state. **None ran.**
Unchanged inference suites/models were not rerun; affected pure construction,
foundation, cleanup and durable local persistence suites were executed.

Static checks passed: AST parsing of all three changed Python files, whitespace
checking of all eight changed files, and `git diff --check` (exit 0). This is not a
PostgreSQL SQL parser/integration check. `sha256sum -c /tmp/p4-supervisor.sha256`
verified the archive; `sha256sum -c /tmp/p4-fixtures.sha256` verified all eight
fixtures unchanged. Archive SHA256 remains
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.

## Remaining gates and next scope

P4's database exit condition is **not met** without actual isolated PG16 execution.
Provision a dedicated local PG16 server and fixture access, then run the opt-in
fixture and fix any concrete SQL/grant/concurrency/REAL failures it exposes. A driver
alone is insufficient. No mocks are offered as substitute integration evidence.

Platform review/provisioning of the supporting receipt schema, actual registration/
media/tenant interfaces and reference immutability/retention remain separate decisions. This is an independently tested adapter interface,
not approval for production connection/deployment.

Later Redis/acknowledgment integration should call this boundary from immutable
pending accepted entries, acknowledge only a successful committed result, and retain
entries through uncertain outcomes. That later phase must define outbox delivered/
retention policy, transport retries and crash reconciliation. Neither Redis nor those
acknowledgment/state transitions exist here.

Supervisor archive/SQL, historical fixtures, P3a review fixes and P3b1 format were
preserved. No shared/production database connection, uploads, model execution, RTSP
change, training, T2 work, T1 freeze, commit or push occurred.
