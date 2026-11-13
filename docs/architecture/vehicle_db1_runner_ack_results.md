# DB1 runner completion after an ambiguous Redis ACK

Follow-up executed 2026-11-07–08 (Africa/Tunis). The confirmed database-runner bug
is fixed. Validation uses injected database outcomes with owned native Redis;
no real model inference was repeated. **Native PostgreSQL remains BLOCKED**:
no authorized disposable target/provisioning configuration is available.
This follow-up does not complete production architecture or durable Engine recovery.

## Baseline and scope

Verified baseline: `vehicle_restart_commit_ack_review.zip`, SHA256
`c7304fe406bc88b7377bf3a0dfb9b80cba447b62679990a8d9acc752545f00a6`.
All 262 manifested source files verified and matched the working tree byte for
byte before editing; no pre-existing follow-up repairs were present. Manifest
SHA256: `ed0ab54c5fd5d4f4fe12b3d8cfe9a96a3c56db4dcbb7cc9fe66f594e2372b03b`.
Aggregate starting-source fingerprint:
`7604fcf1847b77909ac6416a29f6a8b7d85ad75bb63056d38830b68289056241`.
Git HEAD was not used as the baseline. Applicable AGENTS.md searches found none.
The pre-existing dirty status is retained and unrelated changes are preserved.

The reviewed Plate completion Lua, tenant checks, Engine ownership/native lane,
crop handshake and persistent Redis profile remain byte-identical. Canonical
Plate/Colour/class policies, DB1 builder/adapter, supervisor SQL archive and all
historical contract/fixture files are unchanged. No installation, download,
Docker, SQLite target, production connection, commit or push was performed.

## Reproduction and correction

Before the implementation changed, a native Redis regression exercised the actual
runner with a deliberately injected database fixture. Both distinct natural
deliveries committed; Redis executed both XACKs. The first XACK reply was lost.
The runner returned `DB_WAITING`, because `done` counted successful `step()` returns
rather than delivered targets. `red.log` records that failing assertion. The same
runner regression passes after the correction, with two completed targets and
only one successful ACK callback reply. No PostgreSQL behavior is inferred from
the injected transaction stand-in. Packaging additionally reproduces this failure
in a private unpatched baseline extraction before applying the fix.

`real_video.db_delivery.TargetDeliveries` retains a bounded exact target set:
physical stream ID, original bytes and SHA256. `RedisDB1Consumer.last_delivery`
binds one input to its confirmed `DeliveryResult` and any successful XACK reply.
It resets before every read/claim, including a failing read. The old uncorrelated
`last_commit` remains a compatibility observation and is never used for completion
or transaction counting. A callback from `NaturalConsumer.process` supplies the
confirmed result after adapter delivery and successful connection-context exit,
before the committed hook and ACK. ACK cannot run without this input's result.
The canonical adapter and database receipt/history reconciliation are unchanged.

The runner completes the exact target set, independent of successful call count.
After a lost ACK reply it atomically observes, in read-only Lua:

1. The physical stream entry still exists with the exact original scalar bytes.
2. The expected consumer group exists and its last-delivered ID has reached that
   physical input. Decimal stream IDs compare as Python integers without float loss.
3. That exact physical ID has no PEL entry.

Only an already correlated confirmed DB result plus that observation establishes
completion. An empty PEL alone is insufficient: an unread target remains waiting.
Missing/changed original bytes block. A genuinely pending entry retries through
canonical database history and then ACKs. The consumer may return `identical` on
retry, but completion still counts that physical target once. Separate physical
entries carrying one natural delivery are separate transport targets; canonical
DB history deduplicates their facts. Changed accepted content/provenance still
conflicts and is not ACKed. No Redis mutation/namespace reset is used for observation.

| Event | Retained progress | Completion |
|---|---|---|
| Read/claim fails | Current delivery reset; prior targets retain their own proofs | No stale transaction counted for another input |
| DB rollback | Input/attempt recorded, no confirmed commit, no ACK | Waiting; retry actual adapter/history |
| DB commit reply uncertain | Unknown-commit attempt, no invented result/ACK | Wait for confirmed history reconciliation |
| DB result confirmed | Exact entry/payload/result retained | Still needs ACK proof |
| XACK fails before execution | Original entry remains pending | Retry DB history, then ACK |
| XACK executes, reply lost | Confirmed DB result retained, no successful ACK reply | Atomic cursor/bytes/PEL observation completes target |
| Observation reply lost | Original commit proof retained | Re-observe without another DB transaction if already ACKed |
| Changed payload or receipt | Explicit binding/history conflict | No completion/ACK for conflicting target |
| Unread input, empty PEL | Cursor has not reached input | Waiting |

The ledger is bounded to 256 physical targets and 8 MiB retained input bytes;
runner capacities remain narrower. One bounded consumer step per turn, finite
per-target observations, 25 ms waiting backoff and the existing absolute settlement
budget apply. Capacity does not evict accepted state. Runtime/profile checks remain
active. This is a live-run ledger, not a durable new runner recovery service.
The supported Engine/broker restart boundaries from the reviewed milestone remain.

## Truthful status

Database transaction observations are separate from retry attempts and delivery
completion. `new`/`identical` count confirmed transaction results, not completed
targets; a retry may add an identical result for the same physical target.
`transactions_confirmed` is their sum. `delivery_attempts` and `retry_attempts`
count actual inputs offered to the consumer, while `step_calls` includes empty or
failed reads. `unknown_commits`, `not_committed`, `redis_errors` and `conflicts`
retain their distinct meanings.

`targets`, `completed` and `waiting` describe physical target completion.
`ack_callbacks_completed` counts successful XACK callback replies, including zero
if another consumer already acknowledged the committed input. It is not a delivery
completion count. `acknowledgements_reconciled` counts completed targets for which
no successful ACK reply was observed. Per-target status contains only IDs, hashes,
commit/ACK states and supporting acceptance hash, with trusted org/run/camera at
the enclosing database status. No raw text, pixels or credentials are exported.
The whole-group PEL/lag remains independently observable in structured Redis status.
Failure paths save current DB progress rather than losing their partial counters.

## Validation and execution evidence

`_review/evidence/commands.json` records exact executed command arrays and outcomes;
main validator command arrays/timings are in `accepted-checks/commands.json`.
The primary command, using the existing environment/binary, is:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_restart_validate.py \
  --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  --output /home/intern/part-time/aziz/db-runner-ack-validation/accepted-checks
```

The full validator passed all 443 unique main methods (378 pure-suite and 65
native-suite), with no skips in those suites. Reviewed gates passed first, event
contracts passed, all 122 P1 checks passed, and the nine connection-free PostgreSQL
guards passed. Both native PostgreSQL classes skipped and are not native passes.

The focused initial expansion passed 19 methods: ten new ACK/runner methods and
nine existing consumer checks. A further connection-scope regression verifies
that commit callbacks/ACKs do not run after rollback or failed connection-context
exit. Main suites include 443 unique methods, 378 pure-suite and 65 native-suite
methods (the latter also contains the connection-free callback check). Focused
red/green checks, seven reviewed gates and extracted-snapshot repeats overlap
those methods and are not added to the unique count. Actual final outcomes are
recorded in validator and replay receipts, rather than inferred from discovery.

Coverage includes successful XACK/lost reply, pre-XACK failure, next-read failure,
mixed new/identical results, physical duplicates, changed-payload conflict,
unread/empty-PEL and genuinely pending inputs, lost observation replies, missing
original bytes, rollback and uncertain commit history. The actual two-lifetime
runner finishes without a false timeout and does not repeat recognition during DB
retries. Reviewed completion races, tenant rejection, persistent Redis stop/SIGKILL,
metadata replay, compatibility and contract checks also run. No live-owner lease
was shortened. Intentional pre-fix failure evidence is separate from passes. A focused
rerun in parallel with the broad suite exposed an undersized two-second test
startup/settlement window: it failed with SETTLEMENT_WAITING before the database
fixture was created, and the test then raised KeyError while inspecting it. This
was not the database ACK defect. The regression now uses the runner's normal
30-second settlement window and asserts the database fixture was reached before
inspecting it. The failed attempt is retained as startup-window-failure.log.
Live Redis lease durations and production budgets were not changed. The final serial
ACK/runner module passed all 11 methods using that normal window.


## PostgreSQL gate

Configuration discovery recorded only presence booleans: `DB1_P4_ADMIN_DSN`,
`DB1_P4_ISOLATED` and `DB1_P4_EXISTING_SERVER` were absent. Native execution requires
an explicitly owned/authorized disposable local PG16 target, private provisioning
DSN and exactly one fixture provisioning mode. No connection was attempted.
Connection-free guards and native class skips do not count as PostgreSQL validation.
The injected adapter is explicitly a test stand-in, not a PostgreSQL substitute.

When an authorized target is configured privately, use the unchanged fixture's
normal NOSUPERUSER NOBYPASSRLS application-role/RLS design and invocation in the
[operator guide](vehicle_operator_guide.md). No supplied administrator identity is
used as the application role. Test-local receipts and synthetic registrations do
not establish platform approval or production authorization.

## Package and cleanup

The follow-up archive contains the exact patch against the verified 262-source
baseline, complete final dependencies, attributed source/evidence manifests,
original red/green evidence and exact command arrays. The package builder verifies
the baseline, reproduces the old defect in a private extraction, applies the
scoped patch, compares every intended final source byte, runs extracted affected
checks, compares bytes again and removes that extraction. Safe unique paths,
CRC and all manifested hashes are verified. The external `.zip.sha256` identifies
the final ZIP. No models/media/crops, credentials, DB files, AOF/RDB data, caches
or unrelated artifacts are included.

Changes are limited to the consumer's correlated progress callback, the runner's
target drain, validator coverage, new target ledger/tests, report/operator updates
and the follow-up packager. Source fingerprints and exact changed paths are in
`_review/manifest.json`. Cleanup receipts verify only owned processes/resources,
unchanged supervisor/historical files, preserved reviewed fixes and unrelated
working-tree status. Native PostgreSQL resource cleanup is not applicable because
no resources were created. No unresolved live evidence is deleted to claim cleanup.

Remaining limitations are unchanged: native PG validation until an owned target
exists, manufacturer inference, durable Engine crash recovery, host power-loss
guarantees, production registration/authorization and DB-service replacement,
RTSP deployment, throughput/resource tuning, raw-trace persistence and UI integration.
