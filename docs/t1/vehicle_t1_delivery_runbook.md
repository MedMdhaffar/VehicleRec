# WAYCON Vehicle T1 operator handoff — 2026-11-08

Run from `/home/intern/part-time/aziz/pipeline`, or an extracted final review root.
Use the existing `/home/intern/.venv/bin/python`, PostgreSQL 16 on explicit
`127.0.0.1:5433`, and native Redis 8.2.11 at the path below. No installation or
server restart is needed. Each command requires a new output directory and fresh
run/namespace. Reports contain hashes/counts, not pixels or readings.

## Native database gate

The private DSN must use the provisioning superuser and database `postgres`, or
the authorized `intern` maintenance fallback. The maintenance database receives
only provisioning/catalog queries. Schema, registrations, receipts and facts go
to fresh `template0` databases. Do not put credentials in command arguments,
chat, reports or the review ZIP. Supply a pre-existing mode-0600 file containing
only the libpq DSN, or privately set `DB1_P4_ADMIN_DSN` in the environment.

The packaged helper reads the private file into the child environment, uses the
canonical fixture guard, requires explicit host/port, verifies PG16 and provisioning
superuser before DDL, and snapshots the maintenance relation catalog and existing
fixture resource names. It selects `DB1_P4_EXISTING_SERVER=1` and removes isolated
mode. Application work still uses the fixture's actual LOGIN, NOSUPERUSER,
NOBYPASSRLS non-owner role, with forced org RLS and minimum grants.

```bash
export PYTHONDONTWRITEBYTECODE=1
export VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server
export WAYCON_ADMIN_DSN_FILE=/private/path/to/populated-dsn-file
/home/intern/.venv/bin/python _review/evidence/run_native_gate.py \
  --output /tmp/waycon-t1-native-UNIQUE
```

The evidence copy of this helper is also available in the external delivery
directory as `run_native_gate.py`. The helper uses these existing entry points:

```bash
# After DB1_P4_ADMIN_DSN has been supplied privately to the environment:
export DB1_P4_EXISTING_SERVER=1
unset DB1_P4_ISOLATED
/home/intern/.venv/bin/python -m unittest discover -v -s tests/db1_integration
/home/intern/.venv/bin/python scripts/vehicle_bounded_run.py \
  --mode injected --transport plate-requests-v2 --redis-profile aof-always \
  --database isolated-fixture --redis-server "$VEHICLE_REDIS_SERVER" \
  --output /tmp/waycon-t1-bounded-UNIQUE \
  --org 12345678-1234-4234-8234-123456789012 \
  --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb \
  --run t1-UNIQUE --camera injected-camera --namespace t1-UNIQUE \
  --timezone Africa/Tunis --frames 4 --seconds 90 --settlement-seconds 30
```

`--database isolated-fixture` is the existing runner CLI name; it uses the P4
fixture's selected existing-server provisioning mode, not isolated-cluster mode.
The native discovery should execute **38 methods: 29 actual PG methods and nine
connection-free guards**, with no skips/errors/failures. Two existing methods use
owned Redis for request-loop delivery and AOF SIGKILL/restart after PG commit.
The seven added gate methods cover separate-connection commit visibility before
real XACK, receipt immutability, SQL rollback with a real pending delivery, lost
commit reply/history, lost XACK reply without another DB transaction, physical
input binding, conflicts, and two populated tenants under the real restricted login.
`T1_NATIVE` JSON lines record asserted counts/hashes, exact delivery IDs, PEL
state, role properties and policies. Faults wrap actual SQL/commit/transport calls;
neither PostgreSQL transactions nor Redis ACKs are replaced.

The runner should report `outcome=passed`, four decoded/admitted frames, two
lifetimes, complete canonical parity, two committed/acknowledged targets, zero
waiting targets and native validation true. Expect two acceptance digests,
zero pending/lag across groups, released native/crop work and owned-resource
cleanup true. These are injected recognition results on real infrastructure;
the synthetic capture does not validate MP4 decoding or model accuracy.

An absent/empty DSN blocks before provisioning. Discovery can exit zero with
fixture skips; **that is BLOCKED, never PASS**. The bounded runner returns 2 with
`ISOLATED_POSTGRES_NOT_CONFIGURED`. An executed assertion failure is FAIL until
resolved and rerun. Preserve failed attempt evidence independently of later passes.

## Regression and contract gate

```bash
/home/intern/.venv/bin/python scripts/vehicle_restart_validate.py \
  --redis-server "$VEHICLE_REDIS_SERVER" --output /tmp/waycon-t1-regression-UNIQUE
```

This runs the reviewed seven overlapping handshake gates, contracts, 122 DB1
fixture checks, 378 pure methods, 65 owned-Redis methods and native discovery.
Main pure/native methods are 443 unique methods; focused/replay runs overlap and
are not additional unique coverage. Inspect every job's return code and skip flag.
The broad validator permits a PostgreSQL skip in its aggregate exit code, so its
exit zero alone cannot establish the native PostgreSQL verdict.

## Retry and recovery

The DB1 consumer alone writes final facts. Facts and immutable receipt history
commit atomically; XACK follows a confirmed result and successful connection exit.
A rollback retains the PEL entry with no partial rows. A lost commit reply or death
before ACK retries original accepted bytes through database history and returns
`identical`, then ACKs. Changed accepted bytes/provenance conflict and remain pending.

If Redis executes XACK but its reply is lost, the live runner binds the confirmed
result to that exact physical input, verifies original bytes, group cursor and
exact PEL state, and completes it without a second transaction. An unread entry
cannot borrow a prior entry's result; genuinely pending work still needs retry.
Input attempts, confirmed transactions, successful ACK replies and completed
physical deliveries are separate report counters. Recognition is not repeated.

Owned AOF tests use appendonly=yes, appendfsync=always, noeviction, private 0700
socket/directory and TCP port 0. SIGKILL/restart applies only to owned Redis.
Only the same live Engine can reconnect with its retained owner/handles. A new
Engine process requires a new run and fresh namespace; do not reset old keys to
pretend it recovered. SIGKILL evidence is not host power-loss durability evidence.

## Cleanup and freeze

The canonical fixture tracks only exact random names it successfully creates,
drops that database and its two roles, then checks absence. It attempts cleanup
after partial setup and reports any failure. It never drops pre-existing resources,
restarts PostgreSQL, changes its configuration or creates tables in `intern`.
Compare the helper's before/after catalogs and fixture-resource inventories.
On cleanup failure preserve the exact resource list; rerun scoped fixture cleanup
with the privileged connection after inspection. Do not wildcard-delete databases,
roles, directories, keys or unrelated processes.

Persistent successful runs remove their owned broker directory only after
settlement checks. Failed runs retain it; inspect `cleanup.retained_path` and
unresolved state. Preserve that evidence. No SQLite database is in the delivery
path. Existing legacy/local outbox tests are outside the native delivery path.

The report, runbook and final review ZIP are the handoff. The ZIP contains all
266 unchanged reviewed sources plus four additions, a scoped patch, manifests,
redacted execution evidence and clearly labelled historical model receipts.
`_review/replay.json` records patch check/apply, comparison of every resulting
source and focused execution from a private extraction. `preservation.json`
records unchanged supervisor SQL archive/SQL members and historical fixtures.
The external `.zip.sha256` verifies the ZIP; verify it with `sha256sum -c` from
the delivery directory. The ZIP excludes credentials, model/media/crop assets,
Redis/database files, caches and environments.

Real-model MP4 evidence is historical (2026-11-04) and retains its original
222-file source manifest and preflight source hashes. It ran models with native
Redis and no PostgreSQL. Current database evidence uses injected recognition.
These executions are separate; no combined real-model-to-PG run is claimed.
