# Disposable PostgreSQL 16 fixture (P4)

These are **15 real database tests**, opt-in. They passed on the authorized existing
PG16 server on 2026-10-12; see [results](../../docs/t1/db1_p4_postgres_results.md).
They do not substitute injected cursors for PostgreSQL. Default discovery skips
the real fixture unless a provisioning mode and private administrator connection
are explicitly supplied. Choose exactly one mode:

- `DB1_P4_ISOLATED=1`: the host declares this is a dedicated disposable local
  PostgreSQL 16 test server, never a shared/production instance.
- `DB1_P4_EXISTING_SERVER=1`: the host authorizes creation/removal of fresh
  disposable test databases and roles on an existing local server. This does not
  claim that the server itself is dedicated or authorize modifying existing
  databases/roles. Use provisioning database `postgres`, or the authorized `intern`
  fallback if maintenance connectivity is unavailable.

Both modes require `DB1_P4_ADMIN_DSN`: local TCP (`127.0.0.1`, `localhost` or `::1`)
and a provisioning superuser. Dedicated-server mode continues to require database
`postgres`. Check maintenance connectivity before choosing the `intern` fallback.
Provide credentials privately in the environment; do not paste them in command
arguments, files, logs or reports. Provisioning connections use autocommit and never
apply schema or insert application records in their provisioning database.

Then run, using the existing environment:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration
```

There is no automatic server installation, model execution or environment/default
DSN fallback. PostgreSQL must be 16; connect timeout is five seconds. The maintenance
connection is used only for fixture provisioning/cleanup, not analytical operations.
If these variables are absent no database connection is attempted. Dedicated-server
identity is a host assertion; local TCP alone is not proof of safe provisioning.

Every fixture creates its database from `template0` and names its database, NOLOGIN
owner and LOGIN application role with a
fresh random UUID suffix. Both created roles are NOSUPERUSER NOBYPASSRLS. Application
operations use an actual login connection as the normal role, not administrator
connections switched to an application role. Administrative setup/grants/mutations
are separate. Cleanup drops only exact names successfully created by this fixture;
its created-resource list covers partial setup. Cleanup attempts every tracked
resource even after a drop error, then checks exact names for absence and fails
if cleanup is incomplete. Database DROP WITH FORCE scopes termination to that
disposable database. Never invoke supervisor check_contract.py.

Application passwords are generated privately and sent to CREATE ROLE only as
SCRAM verifiers. Administrator sessions suppress statement and parameter logging
through session options; server configuration is unchanged. Application sessions
do not inherit those superuser-only options.

The unmodified archived `01_shared.sql` and `02_vehicle.sql` are applied under the
fixture owner. `03_dev_seed.sql` was inspected but is not applied: the fixture loads
explicit labelled synthetic test identities from the construction tests instead.
It uses the supervisor-created vehicle lane and calendar, and explicit test-only
model/class/grouped-colour/category/media registrations. No media object is uploaded;
registrations are synthetic metadata for reference tests, not object existence proof.

`acceptance_proposal.sql` is a **proposed service-owned supporting schema**, not
supervisor/platform-approved DB1 SQL. Setup inserts the adapter's schema/equality
compatibility metadata. The application role gets SELECT on registration/fact/
receipt tables, INSERT on facts/receipts, schema USAGE and identity sequence USAGE.
It receives no UPDATE/DELETE grants or ownership. Receipt tables force the exact
`app.org_id` RLS mechanism; compatibility is immutable untenant schema metadata.

The 15 test methods cover insertion/omissions, atomic rollback, exact receipts and
REAL rounding, reconnect retries, concurrent first delivery, missing/cross-tenant
context and connection reuse, registration failures, source and selected crop
media, legacy facts without receipts, a normal role, failure preserving the outbox,
and the supervisor's direct manufacturer FK. The latter expects SQL rejection
of a nonexistent manufacturer with null vehicle-model key and checks the explicit
adapter guard also rejects it. It does not enable manufacturer production.

The 2026-10-12 run establishes these SQL/setup/grant/locking/RLS/concurrency/REAL
outcomes for this synthetic fixture on PostgreSQL 16.14. It does not approve the
receipt schema for production deployment or validate object-store contents,
production authentication, network-loss commit ambiguity or production workloads.

`test_fixture_safety.py` adds nine connection-free regression tests for mode
guards, suppressed parsing errors and cleanup scope/failures. Full discovery runs
these alongside the real fixture; it must report all 15 real tests executed without
a fixture skip before claiming PostgreSQL integration success.
