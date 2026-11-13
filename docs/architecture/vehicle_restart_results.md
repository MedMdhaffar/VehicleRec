# Completion handshake, commit/ACK boundary and owned Redis restarts

Follow-up (2026-11-08): the DB runner's lost-XACK-reply completion defect is
reproduced and corrected in [follow-up results](vehicle_db1_runner_ack_results.md).
The original report below is the reviewed prior execution record. Its native
PostgreSQL gate remains blocked; its DB-disabled runner evidence did not validate
the subsequently confirmed database-enabled completion bug.

Executed 2026-11-07, Africa/Tunis. Both confirmed review findings are repaired and
validated with injected clients and native Redis. A separate persistent Redis
profile and bounded injected runner are implemented. **Actual PostgreSQL
transaction/commit-before-ACK validation is BLOCKED**: no explicitly configured
owned disposable target exists. No database connection was attempted and there
are no native commit receipts from this task. This is not the completed production
architecture.

## Starting snapshot and integrity

The exact starting archive was `vehicle_plate_requests_db1_review.zip`, SHA256
`e2b58a1168248a38aa2af199e327555c6600a124eeeed67dc7c10e559f5542b2`.
Its source manifest SHA256 was
`b987e85a6c8604536aea89a2d689d80cd2254efc32a412de3a637af3272087f5`.
All 246 manifested sources verified against the ZIP and matched the initial
working tree byte for byte. **No post-archive repairs were present.** Thus all
scoped changes are new work in this task, and all unchanged dependencies are
attributed to the reviewed baseline. Git HEAD
`a8950aa9f974bf8e46178e2973b3a2d632a86fce` is not the baseline. Applicable
AGENTS.md files were searched in the ancestry and repository; none were found.
The unrelated dirty working tree is preserved in `starting-git-status.txt`.
No commit/push, install, download, Docker, shared-service or global-service change
was performed.

The read-only supervisor archive `db1_contract.zip` remains SHA256
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.
Supervisor SQL, historical schemas/fixtures and canonical policy sources are
byte-identical to the reviewed baseline. Source/evidence manifests include every
file hash, aggregate baseline/final source fingerprints and exact scoped paths.
The aggregate fingerprint is SHA256 of sorted pretty JSON mapping path to SHA256,
with a trailing newline; it does not depend on Git metadata.

## Ownership and both repairs

Input alone opens/decodes and pairs original UTC/monotonic receipts. Engine owns
pixels/crops, detection/tracking/association/quality, allowed executor bindings,
request admission, actual model starts/identity/retirement and original scalar
evidence. Native execution remains serialized. Colour owns scalar interpretation
and temporal selection. Plate owns canonical text semantics and follow-up requests;
metadata services import no model runtime. Aggregator owns class consensus,
terminal joining and immutable natural delivery. The replaceable consumer alone
resolves explicit registrations, projects canonical facts and commits PostgreSQL
facts/receipts. org_id is correlation/tenant identity, not authorization.

The original host separately read pending, seen, proposed state and terminal
stream. A deterministic injected PREPARE between reads reproduced both a false
COMPLETION_MISMATCH before append and premature release after append. The new
read-only atomic Lua observation linearizes these Redis reads. It requires the
exact original sealed physical entry/bytes, logical input digest, group delivery
cursor and absent PEL entry, no pending intent, terminal state and exactly one
matching terminal. Python validates the complete original lifetime/session/seal
and canonical terminal/provenance digest. FINISH's fenced XACK and pending removal
remain the real completion boundary. There is no new receipt schema and no
weakened journal fence. Before PREPARE, after PREPARE or after append before FINISH,
the host waits and retains original crops/evidence. Lost FINISH replies reconcile
without reinference. Conflicting completed evidence blocks. Cleanup remains
retryable after confirmation.

An additional regression showed that absent PEL alone is insufficient for an
unread physical duplicate after prior logical completion. The observation now
also checks the exact group's last-delivered ID against that physical input ID,
using decimal string comparison without floating point ID truncation.

`V2Publisher` now calls canonical `Journal.require_org(codec.org)` first, before
Redis, Engine access, queue retention or group creation. Foreign codec/journal
binding produces zero Redis and zero Engine calls. Engine context and prepared
lifetimes retain org/run/camera/lifetime/session checks. The lower-level
`OrderedPublisher` is explicitly a byte/FIFO helper whose caller validates the
trusted partition; it does not independently authorize tenants.

## State/event and ACK table

The detailed versioned [v2 tables](vehicle_plate_requests_v2.md) remain normative.

| State/event | Meaning / next prerequisite | ACK or crop effect |
|---|---|---|
| Detection closure / crop offer | Original candidates and capabilities, no promised model work | Metadata persists its immutable decision/wait before input ACK |
| Plate request | Semantic operation, stable identity/sequence and exact evidence dependencies | Engine retains decision/ledger; XACK after confirmed admission publication |
| Accepted admission | Admitted work only | Enables native invocation; does not imply start/success |
| Rejection | Explicit reason, enabled work remains blocked | No invented no_detection or model evidence |
| Native start / return / retirement | Separate actual attribution and known retirement | Unknown/unretired work pins lane/crop; no timeout inference |
| Execution evidence | Original scalar operation and generation | Fenced metadata state/output intent before ACK |
| Plate generation seal | Exact requested/admitted IDs and revision, no future requests | Engine waits for exact settlement/retirement and confirmed scalar outputs |
| Engine sealed response | Original settled transcript and matching set | Plate prepares terminal; PREPARE/seen/proposed state are not completion |
| Plate terminal append | Immutable canonical decision | Still waiting until fenced FINISH/XACK removes pending |
| Atomic completion observation | Exact full binding, digest, output and physical ACK checked together | Progressive confirmation; retryable crop cleanup may proceed |
| Aggregator natural delivery | Required terminal join, exact captured-evidence reference parity | Redis handoff is not a DB commit |
| DB final delivery | Strict natural validation, supplied registration, shared canonical projection | Transaction commits all facts and immutable acceptance/row receipts, then XACK |
| Ambiguous DB commit | Reconnect/reconcile durable receipt and fact history | Identical retry or explicit conflict; no premature ACK |

All stream keys share `<namespace>:{vehicle-v1.<trusted-org-uuid>}:`.
Engine `engine-requests-v2` consumes `plate.requests.v2` and `plate.seals.v2`.
Plate `plate-requests-v2` consumes crops/admissions/executions/sealed v2 streams.
Colour `colour-v1` consumes `engine.evidence.colour`. Aggregator
`aggregator-plate-v1` joins lifecycle/Colour/Plate results into `db1.delivery.v1`.
Consumer `db1-natural-v1` ACKs only after confirmed DB commit, independently of
upstream Redis handoff ACKs. Journal PREPARE persists reducer state, waits, original
output intent and seen digest; publication repairs exact bytes/monotonic IDs;
FINISH fences, ACKs and removes pending. Pending repair precedes later transitions.
FIFO publication and retained per-input progress are preserved.

## Persistent broker profile and supported recovery

The historical ephemeral profile remains unchanged: AOF and snapshots disabled.
New `PersistentRuntime` uses an owned mode-0700 directory and Unix socket, port 0,
Redis 8.2.11 from the existing local binary. Effective settings were queried after
every start: `appendonly=yes`, `appendfsync=always`, `save=''`,
`no-appendfsync-on-rewrite=no`, `auto-aof-rewrite-percentage=0`,
`aof-use-rdb-preamble=no`, `aof-load-truncated=no`, `databases=1`,
`maxmemory=67108864`, `maxmemory-policy=noeviction`. AOF enabled/write health,
socket/directory permissions and directory inode/owner marker were verified.
The same data directory/configuration is retained across restart; no namespace is
reset or rebuilt to pass recovery. Data files never enter the review ZIP.

Ten deterministic cases cover graceful stop and SIGKILL at each of: before crop
handoff PREPARE, after terminal PREPARE, after terminal append, after FINISH and
after final natural delivery. Explicit phase hooks and SIGSTOP acknowledgments
freeze metadata workers; snapshots compare all original stream IDs/bytes, group
cursor/PEL IDs and consumers, journal hashes and ownership/lease/fence values.
Elapsed idle time/lease TTL is not claimed byte-identical. After restart the same
live Engine reconnects, workers resume/repair, canonical final parity holds and
whole/marker invocation counts remain one each. A separate case kills the metadata
owner after PREPARE, persists/restarts Redis, replaces the worker, repairs the
original intent and rejects the old fence. Test-only exact-token lease expiry is
applied only after confirmed owner process death; normal live leases stay 60 s.

A new host atomically claims only a fresh bounded namespace, recording full
org/run/camera/session. It refuses occupied state. Same-live-object reconnect
verifies that exact owner and uses the original native ledger/crops/queued bytes.
Missing/changed owner fails closed. Metadata restart, broker restart with live
Engine, and Engine death are separate. A new Engine cannot reuse old handles;
stale-session requests cannot execute against new crops. Unknown native outcomes
remain unresolved, and lost unaccepted frames are not recovered. New capture must
have an explicit new run/session and fresh namespace. Already accepted natural
deliveries can still replay downstream independently.

Process-kill/stop tests establish confirmed-command process restart behavior for
this owned profile; they do not establish host power-loss, storage-controller or
filesystem guarantees. They do not establish exactly-once native execution across
Engine death. The old real-model convenience CLI fails closed on broker connection
exceptions; it is not a persistent reconnect host.

## Bounded runner and operational status

`scripts/vehicle_bounded_run.py` explicitly selects injected capture/executors,
v2 transport, trusted identities, ephemeral/AOF profile and disabled/isolated-fixture
DB mode. It checks binary/capacities/namespace and DB configuration before work.
The injected capture uses `MP4Input`'s successful-decode receipt boundary and one
capture. This is a coordination test, not real MP4 decoding or model accuracy.
The existing real-model CPU CLI remains available with explicit local asset flags;
no real inference was rerun for these coordination-only changes. November 5 real
inference evidence remains historical and separate.

The bounded driver continues request/admission/evidence/seal/native progress while
waiting, alternates inputs/vehicles fairly and retries broker connection errors
with original FIFO bytes and the same ledger. EOF, frame/time limit and controlled
stop are distinct; unresolved timeout does not fabricate settlement. Failed
persistent runs retain their directory/path. A successful disposable run deletes
its directory only after native settlement/expected final validation and no
pending intents, PEL or consumer lag. It closes only owned child processes.

JSON status separates decoded/admitted frames; active/closed/waiting/finalized
lifetimes; requests/admissions/rejections; actual native starts and known
retirements; pending/unknown outcomes; confirmed evidence, seals and terminal
handoffs; crops/bytes/quarantine; final delivery; DB new/identical/conflict/unknown
commit and ACK; stream entries/PEL/lag; bounded codes and component diagnostics.
No raw text, pixels or credentials enter routine status. `NaturalConsumer.last_commit`
exposes the existing adapter result for observability without changing transaction
construction or ACK order.

The final injected AOF run decoded/admitted 4 frames from 1 capture, finalized 2
closed lifetimes, made 2 recognition requests (whole + marker), confirmed 2
admissions/evidence and 2 terminal handoffs, and produced 2 exact-parity natural
bundles. Actual starts were `vehicle` 4, `plate` 4, `whole_ocr` 1,
`marker` 1, with matching known retirements and maximum native concurrency 1.
No regions were requested. Final crops/bytes, active native work, queue, group
pending/lag and component diagnostics were zero. DB was disabled: zero commits,
not a native DB success. Final report records exact bundle hashes/settings/PIDs
and owned subprocess/directory cleanup.

Capacity: injected runner 1–8 frames, <=300 s admission/profile budget and <=120 s
settlement wait; native API budget 80, 16 handles/32 MiB crop storage, 8 MiB/crop;
16 bounded lifetimes, <=256 publication/stream entries, <=8 MiB retained scalars,
<=128 Engine input ledger entries, <=64 decisions per lifetime. Persistent profile
allows <=8 restarts, <=64 namespace keys, <=16 data files, <=96 MiB checked total
and 8 MiB per-file RLIMIT_FSIZE. Total disk/runtime/queue checks occur between
controller turns; they are not an OS disk quota or an interruptible native API
watchdog. Native calls remain serialized; no throughput improvement is asserted.
Host waiting backs off 25 ms, metadata idle 50 ms, no unbounded drain/busy loop or
silent eviction. The synthetic device is explicitly CPU; no shared GPU was used.

## PostgreSQL gate and canonical compatibility

Configuration discovery recorded only presence booleans. `DB1_P4_ADMIN_DSN`,
`DB1_P4_ISOLATED` and `DB1_P4_EXISTING_SERVER` were all absent. The exact missing
requirements are an authorized owned disposable local PG16 target, private admin
DSN and exactly one explicit provisioning mode. Credentials alone never authorize
execution. The DB-enabled runner returned `ISOLATED_POSTGRES_NOT_CONFIGURED`
before capture, Redis startup, provisioning or model work. Nine connection-free
fixture guards passed; two native class skips are **not native validation**.

The existing normal-role fixture/provisioning/cleanup, unchanged supervisor SQL,
canonical `RowBuilder.project_natural` and `DB1Adapter` transaction/receipt
implementation are reused. New optional runner provisioning supplies explicitly
synthetic registrations and checks NOSUPERUSER/NOBYPASSRLS, no application table
ownership and nine forced-RLS tenant tables. No second DB transaction/projection
implementation was created. Natural identities remain separate from resolved SQL
keys; original receipts/model provenance remain canonical.

Gated native tests include actual Redis and PG commit/ACK, lost commit reply,
worker death before XACK, concurrent identical retries, changed provenance,
missing registrations, parent/child rollback, normal-role cross-org read/write and
owned AOF Redis SIGKILL after PG commit before XACK. These native additions are
**IMPLEMENTED + UNVALIDATED** here. DB/role cleanup is similarly unexecuted; no
native receipts or isolation claim is invented. Existing injected consumer and
adapter tests validate conflict/commit/ACK control flow only. Exact prerequisites
and command are in the [operator guide](vehicle_operator_guide.md).

## Validation commands and results

The review package's `_review/evidence/accepted-checks/commands.json` contains
actual command arrays, return codes and timings. `commands-additional.json`
records focused/red/green probes, runner and top-level validation invocations.
The reproducible primary command was:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_restart_validate.py \
  --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  --output /home/intern/part-time/aziz/restart-milestone-validation/accepted-checks
```

The main suites passed **432 unique methods: 378 pure and 54 native Redis**,
with no skips in either suite. Gates 1/2 passed first; event contracts and all
122 P1 fixture checks passed. The PostgreSQL discovery executed nine safety
guards and skipped two native classes, so native PG remains blocked.
Seven focused gate methods overlap those suites; they are not seven extra unique
tests. Ten restart scenarios are subcases of one method. Native PostgreSQL guard
counts are separate. P1's 122 synthetic fixture checks and event-contract checks
are not native integration tests. Extracted-snapshot checks intentionally repeat
selected methods to prove package reproducibility and are not additional unique
coverage. See the actual logs/results for outcomes.

Intentional failures and corrections are retained: `review-red.log` has two
failures and one error reproducing the reviewed defects; the repaired pure/native
gates pass. The first unread-input probe had a missing reference mock and produced
a test-fixture error; after correcting the probe, `ack-cursor-red-fixed-test.log`
reproduced premature success. The cursor guard and final native terminal-content/
wrong-digest/full-session/full-lifetime checks pass. Earlier interrupted
`final-checks`/`gates12-final` invocations have partial logs, no completion verdict
and are not counted as passes. All accepted run logs are preserved separately.
No expensive inference was rerun for reporting or packaging.

## Scoped files, review replay and cleanup

The exact changed path list is `_review/manifest.json:scoped_paths`. New modules
are completion/ownership observation; owned AOF runtime; bounded driver, injected
source, status and optional DB fixture facade; four regression modules; runner,
validator and package builder; current results and operator guide. Modified files
are v2 host/worker, journal/test hooks, DB consumer observability, gated PG consumer
tests and architecture documents. Unchanged complete dependencies are included;
unrelated unmanifested installer/shared-service scripts are excluded.

`scripts/package_vehicle_restart_review.py` verifies the baseline hash and all
246 sources, creates the exact scoped patch, applies it to a private baseline
extraction, compares every final source byte, runs selected pure/native/contracts
checks there, compares bytes again and removes the extraction. The ZIP contains
262 complete manifested sources plus allowlisted sanitized evidence; safe unique
paths, CRC, every source/evidence SHA and forbidden artifact exclusions are checked.
`replay.json` records executed arrays, fingerprints and outcomes. The external
`.zip.sha256` identifies the final immutable archive; the archive cannot contain
its own hash. No credentials, model weights, media, crops, DB files or AOF/RDB data
are included.

Owned successful Redis processes/directories were stopped/removed. Cleanup and
original source/SQL integrity are recorded in `cleanup.json`; unrelated services
were left untouched. Interrupted ephemeral-test logs/phase markers are retained
as evidence outside the ZIP and explicitly inventoried; no AOF/DB data or live
native process from those interrupted tests remains. No unresolved live evidence
was deleted to claim a clean state.

## Acceptance and remaining work

| Item | Status |
|---|---|
| Atomic completion/crop-release handshake | IMPLEMENTED + VALIDATED |
| Tenant validation before mutation | IMPLEMENTED + VALIDATED |
| Native Redis regressions for both findings | IMPLEMENTED + VALIDATED |
| Canonical DB1 consumer compatibility | IMPLEMENTED + VALIDATED (pure/injected compatibility) |
| Actual PG transaction/commit-before-ACK validation | BLOCKED (no authorized disposable target) |
| Persistent Redis configuration/restart tests | IMPLEMENTED + VALIDATED |
| Exact metadata replay/parity after restart | IMPLEMENTED + VALIDATED |
| Stale-session/Engine-restart boundary | IMPLEMENTED + VALIDATED (refusal/uncertainty, no recovery) |
| Bounded runner | IMPLEMENTED + VALIDATED (injected/native Redis; native DB gate blocked) |
| Structured status/operator procedures | IMPLEMENTED + VALIDATED |
| Compatibility/contracts/SQL integrity | IMPLEMENTED + VALIDATED |
| Exact replay/archive/cleanup | IMPLEMENTED + VALIDATED (actual extraction/test/hash receipt in review package) |

Remaining: manufacturer inference; durable Engine process-crash recovery; host
power-loss guarantees; production registration and authorization; production
DB-service replacement and approved receipt deployment; RTSP deployment;
production throughput/resource tuning; separate Engine raw-trace persistence;
backend/frontend integration. These are not implemented by local restart tests.
