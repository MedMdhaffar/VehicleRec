# P4 — real PostgreSQL integration results, 2026-10-12

**All 15 real integration tests passed: zero failures, errors or skips.** The
restricted application login performed delivery on PostgreSQL 16.14. Disposable
databases and roles from both successful runs and an intentional setup-failure
probe were removed and independently checked for absence. The existing server
remains running. This supersedes the PostgreSQL availability/integration blocker
in the earlier P4 reports, not their production limitations or deployment gates.

## Authorization and environment

The user explicitly authorized creating/removing disposable databases and roles
on the existing internship server. This execution used the existing server at
`127.0.0.1:5433`; it did not assert that the whole server was dedicated to tests.
The supplied administrator account successfully connected to maintenance database
`postgres` in autocommit mode. The `intern` fallback was therefore unnecessary.
Read-only catalog snapshots were taken in `intern`; no tables or application
records were created there.

- Server: PostgreSQL **16.14**, version number **160014**, Debian package
  `16.14-1.pgdg13+1`, x86_64.
- Python environment: `/home/intern/.venv/bin/python`.
- Driver: existing **psycopg 3.3.4**; nothing installed.
- Earlier read-only preflight confirmed `fsync`, `full_page_writes` and
  `synchronous_commit` enabled. Delivery sets transaction-local synchronous commit.

Credentials entered through a hidden terminal prompt were used only in process
memory/private process environment. Neither supplied DSN nor password was written
to files, arguments, reports or logs. The execution harness suppressed driver
exception details. Generated application passwords were submitted to CREATE ROLE
as SCRAM verifiers. Administrator connection options suppressed statement and
parameter logging for those sessions only; no server configuration files, global
settings, existing role privileges or service lifecycle were changed.

## Fixture changes

`tests/db1_integration/test_postgres.py` preserves `DB1_P4_ISOLATED=1` for a
host-declared dedicated local PG16 server with provisioning database `postgres`.
It adds mutually exclusive `DB1_P4_EXISTING_SERVER=1`, explicitly authorizing fresh
disposable databases/roles on an existing local server. Existing-server mode
allows maintenance `postgres`, or authorized `intern` as a fallback. Both require
a private `DB1_P4_ADMIN_DSN`, local TCP and a provisioning superuser. Missing opt-in
still skips before connecting; conflicting modes and other database/host choices
are rejected. Neither mode applies application SQL in its provisioning database.

Fresh UUID-named databases use `template0`. Each has a NOLOGIN NOSUPERUSER NOBYPASSRLS
owner and a separate LOGIN NOSUPERUSER NOBYPASSRLS application role. Only within
the disposable database, setup applies unchanged archived `01_shared.sql` and
`02_vehicle.sql`, explicit synthetic registrations, proposed receipt schema and
compatibility metadata. It does not apply `03_dev_seed.sql` or invoke the supervisor
checker. The existing receipt proposal itself was unchanged.

Cleanup closes setup connections before dropping resources, traverses only exact
successfully created names in reverse order, continues after individual drop
errors, and verifies every tracked name is absent. It reports incomplete cleanup
as failure. Database DROP WITH FORCE is confined to the tracked disposable database.
Nine connection-free regression tests cover mode guards, suppressed connection
parsing errors, partial-setup cleanup, continued cleanup after a drop error and
remaining-resource detection. No adapter implementation changes were needed.

The original 15 assertions were preserved. Existing test methods gained explicit
current/session-role checks, owner flags, effective grant checks, tenant visibility
checks for receipt tables and direct cross-tenant receipt insertion rejection.

## Executed commands and counts

All Python commands used `PYTHONDONTWRITEBYTECODE=1` and the existing virtual
environment, from `/home/intern/part-time/aziz/pipeline`.

| Command | Result |
|---|---|
| `/home/intern/.venv/bin/python /tmp/p4-existing-server-runner.py` | First run: **15 passed**, 0 failures/errors/skips, **5.757 s**. Final strengthened run: **15 passed**, 0 failures/errors/skips, **6.258 s**. Each run used fresh resources and verified cleanup. |
| `/home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration -p test_fixture_safety.py -v` | **9 passed**, **0.006 s**; connection-free fixture regression tests. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_db1_adapter vehicle.common.tests.test_db1_rows vehicle.common.tests.test_db1_outbox vehicle.common.tests.test_foundations vehicle.common.tests.test_coordinator_cleanup` | **144 passed**, **28.906 s**: adapter 22, rows 72, outbox 23, foundations 21, coordinator cleanup 6. |
| `/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**, format checking enabled. |
| `/home/intern/.venv/bin/python /tmp/p4-partial-setup-runner.py` | Intentional setup-failure probe: expected one setup error, **0 test bodies**, **0.383 s**; all resources removed and baseline catalogs preserved; harness exited successfully after those checks. |
| `sha256sum -c /tmp/p4-supervisor.sha256` | Supervisor archive matched its historical baseline. |
| `sha256sum -c /tmp/p4-fixtures.sha256` | All eight historical DB1 fixture files matched their historical baselines. |
| `git diff --check` | Passed. New/changed fixture files also passed direct syntax and trailing-whitespace checks. |

The temporary integration harness took hidden credential input, checked maintenance
connectivity, privately set existing-server opt-in and administrator connection,
loaded exactly `PostgreSQL16Tests` with unittest, and independently checked cleanup
and existing catalog snapshots. It retained only credential-free result metadata.
No fixture skip was counted as integration success. The standard future invocation,
after privately supplying the documented mode and connection environment, is
`/home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration`;
full discovery includes the nine safety regressions alongside the 15 real tests.

## Real database results

- **Normal role and RLS:** execution and session users matched the separate
  application login; SUPERUSER/BYPASSRLS were false. Owner LOGIN/SUPERUSER/BYPASSRLS
  were false. Delivery verified enabled and forced RLS on all nine required tenant
  tables. Facts and receipts were invisible without tenant context and to the
  other tenant. A direct cross-tenant acceptance insert was rejected by PostgreSQL
  with InsufficientPrivilege. Tenant settings cleared after transaction completion,
  and reuse of the same connection still returned an identical retry.
- **Grants:** application SELECT/INSERT were available on the four fact tables and
  two tenant receipt tables; UPDATE, DELETE and TRUNCATE were unavailable. Receipt
  compatibility INSERT and CREATE on both application schemas were unavailable.
  Delivery succeeded with schema USAGE and required sequence USAGE grants.
- **Insertion and omissions:** one vehicle, colour and plate fact committed with
  one acceptance and three row receipts. Manufacturer omission produced no make row.
- **Retry and numeric equality:** reopening the login connection returned
  `identical` without duplicate vehicle insertion. Exact canonical equality
  survived SQL REAL rounding; a different exact payload with the same identity
  conflicted even when REAL storage could obscure the difference.
- **Concurrency:** simultaneous identical first deliveries returned one `new` and
  one `identical`; conflicting first deliveries returned one `new` and one conflict.
  Both scenarios retained exactly one acceptance.
- **Rollback:** an intentionally injected child CHECK violation produced the typed
  database error, left no parent fact or acceptance and preserved the pending local
  outbox entry. A closed application connection also preserved the pending entry.
- **References and history:** missing/wrong camera, model, date, grouped colour,
  category and media registrations were rejected. Registered selected-crop delivery
  and retry passed; wrong run/kind and missing media failed. A preexisting fact
  without receipts was not accepted as an identical retry. PostgreSQL enforced the
  supervisor's direct manufacturer FK with a null vehicle-model reference, and
  the adapter's explicit structural guard rejected the nonexistent manufacturer.

No unexpected integration failures were reproduced. No assertions were weakened,
no supervisor SQL changed and no adapter fixes were necessary. Expected negative
cases remained passing tests. The separately injected setup error tested teardown
after schema/grants were applied but before synthetic registrations; it is not an
integration-suite failure or success count.

## Scoped cleanup and integrity

Each suffix below identifies exactly one `db1p4_db_<suffix>` database,
`db1p4_owner_<suffix>` owner and `db1p4_app_<suffix>` application role:

| Execution | Resource suffix | Post-cleanup catalog result |
|---|---|---|
| First integration run | `79cc11f4b1cf494da932837288bb72ae` | All three absent |
| Final integration run | `a0540d759ae14486a554cb2305205b9e` | All three absent |
| Intentional partial-setup probe | `795a826d085f4bdc8e3e771acb898db7` | All three absent |

Independent before/after snapshots matched for database identities/ownership/ACLs
and role identities/flags/configuration. The `intern` user-relation catalog also
matched its baseline. The server remained connectable after every execution.
Cleanup did not stop the server, scan/drop by prefix or touch preexisting resources.

Supervisor archive SHA256 remains
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.
In addition to historical checksums, a pre/post manifest checked **70** archive,
DB1 fixture/validator and historical example files: **zero changed**.

## Remaining limitations

These results validate the synthetic P4 scenarios on PG16.14, not production
deployment, authentication or data registration policy. The proposed receipt
schema is authorized only for these disposable tests; production approval remains
outstanding. `TrustedTenant` still depends on trusted host authentication and
authorization. Synthetic media metadata does not establish uploaded object
existence or byte accuracy. Real network loss at commit, crash recovery, long-run
load and administrative registration races were not tested. Scoped cleanup after
ordinary setup failures was exercised; abrupt process death/network loss can still
require recovery using the exact created names.

No PostgreSQL installation, Docker use, server-wide configuration change,
existing-role privilege change, supervisor checker, model execution, Redis,
upload, commit or push occurred. T1 freeze and T2 progression were not performed.
