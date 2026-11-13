# Bounded vehicle operator guide

This is an opt-in local coordination/test harness. Manufacturer inference, RTSP,
production registration/authorization, platform services and durable Engine
recovery are not implemented. Use existing binaries/environments only.

## 1. Prerequisites and configuration

Use `/home/intern/.venv/bin/python` and the existing Redis binary, for example
`/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`.
The injected runner needs the existing Python Redis, NumPy and OpenCV dependencies;
it uses a synthetic capture through `MP4Input` and explicitly injected native
responses. It does not test video decoding accuracy or model accuracy. Every run
requires org UUID, identity namespace UUID, explicit run/camera, timezone, transport,
resource budgets, unused output directory and fresh broker namespace. org is
correlation, not authorization. Never pass credentials in CLI arguments.

## 2. Preflight

The runner verifies its binary, capacities, simple bounded identities and requested
transport. `V2Publisher` rejects codec/journal tenant mismatch without Redis or
Engine calls, checks the Engine context, and atomically claims a fresh namespace.
A new Engine cannot attach to a nonempty namespace. Do not delete/reset keys to
bypass this check. For AOF, inspect effective settings in `report.json`:
`appendonly=yes`, `appendfsync=always`, `maxmemory-policy=noeviction`, port 0 and
private directory/socket. Database-enabled mode checks its explicit provisioning
configuration before opening capture or Redis; after provisioning it verifies
normal role, no application table ownership and forced RLS.

## 3. Injected end-to-end run

From the snapshot root, use a new output directory and explicit identities:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_bounded_run.py \
  --mode injected --transport plate-requests-v2 --redis-profile aof-always \
  --database disabled \
  --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  --output /tmp/waycon-run-example \
  --org 12345678-1234-4234-8234-123456789012 \
  --identity-namespace 98c2adf9-264e-4ac2-b6a1-3239fa02ecbb \
  --run example-01 --camera injected-camera --namespace example-01 \
  --timezone Africa/Tunis --frames 4 --seconds 90 --settlement-seconds 30
```

Expected: one capture, four decoded/admitted frames, two finalized lifetimes, exact
canonical reference parity from the same evidence, and no retained crops/native
work. Requests, starts, retirements, handoffs and DB commits are separate counts.
`--database disabled` produces no database commit. `--redis-profile ephemeral`
selects the unchanged nonpersistent profile. Routine JSON omits readings/pixels.

## 4. Optional bounded real MP4 run

Real inference is not needed for these coordination changes and was not rerun.
The existing `scripts/vehicle_mp4_colour_plate_smoke.py --request-loop-v2` accepts
explicit `--video`, `--vehicle-weights`, `--vehicle-tracker`, `--plate-weights`,
`--plate-tracker`, `--ocr-dir`, `--marker`, `--redis-server`, `--output`, `--camera`,
`--timezone`, `--run-id`, `--namespace`, `--org`, `--identity-namespace`, `--device cpu`,
`--threads 1`, `--max-frames 4`, `--admission-seconds 180`, `--delivery-timeout 30`.
Use only the already reviewed local assets, an unused output/run/namespace and the
same trusted identities. All asset flags are required; missing assets fail. Its
preflight records artifact fingerprints and actual native placement. TCP provider
access and auto-install are disabled. One decoder supplies original paired
receipts; direct reference consumes captured scalars without re-inference.
This older real-model CLI uses ephemeral Redis, fails closed on connection errors
and does not integrate the optional DB fixture or the new structured runner report.
It has not been validated as a persistent real-model restart host.

## 5. Isolated PostgreSQL validation

Native execution is currently **BLOCKED**: no owned target is configured.
A fixture skip is not validation. An operator must first establish ownership of
a disposable local PostgreSQL 16 target and privately supply `DB1_P4_ADMIN_DSN`.
Choose exactly one authorized provisioning mode: `DB1_P4_ISOLATED=1` declares a
dedicated disposable server; `DB1_P4_EXISTING_SERVER=1` explicitly authorizes only
fresh disposable databases/roles on a local server. Never infer authorization from
a DSN. See [fixture safety and provisioning requirements](../../tests/db1_integration/README.md).
The administrator is used only for provisioning; actual facts use a separate
NOSUPERUSER NOBYPASSRLS role, no table ownership, minimum grants and forced org RLS.
Supervisor SQL is unchanged; supporting receipts and registrations are test-local.
No installation, global-service changes or shared-schema modifications are allowed.

With authorized configuration already supplied privately:

```bash
PYTHONDONTWRITEBYTECODE=1 VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  /home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration
```

This includes actual Redis/PG consumer delivery, lost commit reply, death before
XACK, rollback, concurrent identical retry, changed provenance, RLS and SIGKILL
of owned AOF Redis after PG commit. Require all native classes to execute without
skips. Reuse the injected runner command with `--database isolated-fixture` for
full final-delivery commits and receipt digests. It uses the same fixture and
canonical adapter, synthetic registrations only, then verifies DB/role cleanup.
No credentials enter reports. Delivery retries perform no recognition.

## 6. Persistent Redis restart validation

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_restart_validate.py \
  --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  --output /tmp/waycon-validation-example
```

An unused output path is required. Gates 1/2 run first, then contracts, pure/native
suites and the gated database fixture. Focused gates overlap the main suites;
`test-methods.json` identifies unique main methods. Phase markers and stopped
workers synchronize confirmed boundaries; tests compare original bytes/PEL/journal
before resuming. Ten scenarios cover graceful stop and SIGKILL at five boundaries.
Replacement-worker fencing is separate from a live Engine reconnect. The test-only
lease clock advance applies only to the exact token of a confirmed dead owner;
normal 60-second live leases are unchanged. Never use it operationally.

## 7. Waiting, rejection and uncertainty

`waiting` retains original evidence and crops. Missing dependency, absent admission,
unconfirmed handoff, pending native outcome and capacity block are distinct.
Acceptance is not a native start; return is not retirement; publication is not a
DB commit. Rejections have explicit codes and never become no_detection. Terminal
conflicts preserve the original decision and block changed evidence. A timeout
proves none of cancellation, no plate, unreadability or safe resource release.
Inspect JSON counts, metadata states/diagnostic counts, PEL/lag and bounded error
codes. Do not expose raw readings, credentials or crops in routine diagnostics.

Database completion follows exact physical target IDs and payload bytes. Confirmed
transaction results (`new`/`identical`), input/retry attempts and completed delivery
targets are separate counts. A lost XACK reply is reconciled against original
bytes, the consumer-group delivery cursor and the exact PEL entry, using only a
confirmed result for that input. Empty PEL alone cannot prove an unread target
completed. `ack_callbacks_completed` counts successful callback replies;
`completed` counts targets with proven commit and ACK, including reconciled lost
replies. Prior `last_commit` is never attributed to a failing next read. Unknown
DB commits still reconcile through canonical receipt/history on retry. See the
[follow-up report](vehicle_db1_runner_ack_results.md) for exact states and tests.


## 8. Safe shutdown

SIGINT/SIGTERM requests controlled stop. The bounded driver closes detection and
continues pumping existing requests/evidence/seals to its settlement deadline.
It reports unresolved work if this cannot settle; it does not force a terminal.
Native APIs are not interruptible deadlines. Crops require known retirement,
generation closure, exact admitted-set settlement and atomic completed Plate
handoff before retryable release. Cleanup failure quarantines and can be retried.
Only owned subprocesses are stopped. Successful disposable persistent tests delete
their owned directory after validating settlement, no pending intents/PEL/lag and
expected final deliveries. Failed runs retain their broker directory and report its
path; do not erase that evidence to claim cleanup. Resource checks run between
host turns, not as a watchdog that can interrupt a stuck native API.

## 9. Recovery boundary

After a broker disconnect, only the same live Engine object may reconnect using
its original owner value, ledger, bytes and crop handles. It verifies ownership
before pumping; it never reinitializes a namespace. Metadata workers independently
repair fenced intents before later inputs. A new CLI process cannot resume an
old Engine session. After Engine death, stop that run, preserve its evidence,
record unknown native outcomes and lost unaccepted capture explicitly, then start
new capture with a new run/session and fresh namespace. Do not reuse old local
handles, reconstruct pixels or replay uncertain model calls. Already accepted
natural deliveries can be replayed downstream using original identities and
explicit registrations; DB receipt history reconciles retries. This is not Engine
crash recovery. Redis process-kill tests do not prove host power-loss, filesystem
or storage-controller durability. Production authorization/registration, DB service
replacement, throughput, RTSP, manufacturer, raw traces and UI integration remain.
