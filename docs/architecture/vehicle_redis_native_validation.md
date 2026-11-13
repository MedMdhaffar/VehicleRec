# Real native Redis validation — 2026-11-01

**All 11 integration methods passed against real Redis 8.2.11, twice, without skips.** No transport or harness source fix was needed; no features were added. The previous missing-runtime blocker is resolved. The two executions took 42.874s and 42.973s and both exited 0. These are eleven distinct integration methods executed twice, with eight actual child-kill boundary subcases in each run.

## Binary source, integrity and build

No suitable existing native binary was found in PATH or searched user/native installation locations. Existing prerequisites were available: GNU Make 4.3, GCC 11.4.0, curl, tar and sha256sum. No missing prerequisite, package-manager change, sudo, Docker, system service or global installation was needed. The user explicitly authorized the following user-owned build, resolving the preceding task's no-download restriction.

Selected release: **8.2.11** from [official Redis releases](https://download.redis.io/releases/redis-8.2.11.tar.gz). The [official version policy](https://redis.io/docs/latest/operate/oss_and_stack/install/version-mgmt/) lists 8.2 as an extended supported line, with EOL September 1, 2030. Published integrity information came from the [Redis hash repository](https://github.com/redis/redis-hashes), downloaded over HTTPS. This verifies the published SHA256; no independent signature verification is claimed.

| Artifact | SHA256 |
|---|---|
| redis-8.2.11.tar.gz, published and independently computed | `fba14b77c4bdcb2d5418b6741fc0cdaac4b78bc73eab9b2e5f3c255d597f16fa` |
| Downloaded official hash manifest snapshot | `4eaf249871f1b620f49c362b4f25babcebd7e85871dfb7d3a1d1d6ac18e5707e` |
| Built redis-server binary | `d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393` |

All download/build/output files are outside the repository, in `/home/intern/part-time/aziz/redis-native-validation`. The source archive, build tree and executable are retained there for reproducibility; none is globally installed or included in the review ZIP. TLS/modules were not built because this harness uses only a private native Unix socket and Redis Streams/core Lua.

Commands executed (from the build/output directory unless otherwise stated):

```bash
curl --fail --location --proto '=https' --tlsv1.2 --output redis-hashes.README https://raw.githubusercontent.com/redis/redis-hashes/master/README
curl --fail --location --proto '=https' --tlsv1.2 --output redis-8.2.11.tar.gz https://download.redis.io/releases/redis-8.2.11.tar.gz
```

The hash row for redis-8.2.11.tar.gz was extracted from that downloaded manifest and compared with `hashlib.sha256(archive.read_bytes()).hexdigest()` using a Python assertion before extraction. Result: **MATCH**. The row and result are retained in `integrity.txt`.

```bash
tar -xzf redis-8.2.11.tar.gz
make -C redis-8.2.11 -j2 MALLOC=libc BUILD_TLS=no BUILD_WITH_MODULES=no OPT=-O2 redis-server > build.log 2>&1
/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server --version
```

Build exit: **0**. Exact version output:

```text
Redis server v=8.2.11 sha=00000000:1 malloc=libc bits=64 build=d8a8c744cbac1a9
```

## Harness inspection and exact execution

Applicable ancestors and repository files contained no AGENTS.md. Git status was inspected first and the preceding verified review ZIP was copied as the exact baseline (SHA256 `2591e4afed394abadc52c1b578a2daff99bb2e5402630f0dd7bc214a5c5b85cf`). The runner and all eleven integration methods were inspected before execution. They create their own temporary directory/socket and only their own server/workers, use no FLUSH or shared configuration/keys, deliberately kill child workers at controlled phase markers, and close/reap children and remove the private directory.

From `/home/intern/part-time/aziz/pipeline`, the unchanged required runner was invoked directly:

```bash
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_redis_integration.py > /home/intern/part-time/aziz/redis-native-validation/integration-initial.log 2>&1
```

Result: **11 tests, 42.874s, OK, exit 0**. No failures, errors or skips.

A second run captured complete ownership/settings/cleanup evidence using an external evidence-only wrapper. It executes that same unchanged script via runpy, records harness-created Popen objects and exit statuses, queries settings only through the harness's own private client, and copies its logs/phase markers immediately before its own directory is removed. It changes no transport, scheduling, fault injection, timing, assertions or test-harness source.

```bash
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/redis-native-validation/audit_native.py > /home/intern/part-time/aziz/redis-native-validation/integration-audited.log 2>&1
```

Result: **11 tests, 42.973s, OK, exit 0**. No failures, errors or skips. Expected injected Lua errors/timeouts and SIGKILLs are exercised and reconciled by test assertions; they are not hidden failed test runs. No scoped runtime fix or reproduction rerun was necessary.

## Actual settings and observed isolation

The audited instance used `/tmp/vehicle-redis-j8a14rhg/r.sock`; the directory and socket were both mode **0700**, measured from the filesystem. That path has been removed by cleanup. The first run used its own different temporary instance; no shared server connection was made.

| Queried server setting | Actual value |
|---|---|
| Redis version | 8.2.11 |
| TCP port / bind | 0 / 127.0.0.1 |
| unixsocketperm / directory mode | 700 / 0700 |
| databases / protected-mode | 1 / yes |
| maxmemory | 67108864 bytes (64 MiB) |
| maxmemory-policy | noeviction |
| save | empty; snapshots disabled |
| appendonly | no |
| appendfsync | everysec, inactive because appendonly=no |

## All eleven methods executed

| Integration method | Executed result |
|---|---|
| `test_both_aggregator_streams_continuously_ready_are_fair` | Passed: balanced source/pending/new service and six deliveries |
| `test_capacity_preserves_input_and_pending_output_intent` | Passed: vehicle/output capacity leaves input pending/intent retained |
| `test_changed_batch_identity_conflicts_and_original_retry_still_works` | Passed: original ID retained, changed ID conflicts, true retry succeeds |
| `test_child_kill_at_handoff_publication_and_ack_boundaries` | Passed: actual SIGKILL at four phases for each of two worker roles, reclaim/recovery |
| `test_competing_consumers_and_stale_fence_rejected` | Passed: competing processes and stale mutation/publication/ACK rejection |
| `test_identical_duplicate_changed_content_and_malformed_cross_org` | Passed: duplicates, conflicts, malformed/cross-org rejection, one final delivery |
| `test_invalid_closure_cannot_poison_later_valid_join` | Passed: later valid closure works in both arrival orders |
| `test_lost_command_replies_reconcile_without_extra_publication` | Passed: replies lost after PREPARE, PUBLISH and FINISH |
| `test_partial_lua_write_is_repaired_not_rolled_back` | Passed: intentional error after XADD, retained intent repaired without extra publication |
| `test_restart_waiting_and_other_ready_vehicle_progresses` | Passed: killed/restarted waiting reducer, other vehicle delivers |
| `test_separate_processes_exact_bytes_both_input_orders` | Passed: both arrival orders match the existing in-memory canonical bytes/hash |

Both runs executed every row above. The canonical NaturalBundle hash remains `c492561f33cf1c6c69273fc095fc3cde9ebf94218e96097617a9735996642da6` for the main fixture.

## Fresh regression checks

All used the existing `/home/intern/.venv/bin/python` with `PYTHONDONTWRITEBYTECODE=1`, redis-py 7.4.0 and real jsonschema 4.26.0. These are executions in this validation task, not earlier historical counts.

| Exact Python arguments | Actual result |
|---|---|
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar -q` | **134 passed**, including fresh-process pure imports |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 passed** |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | Valid 18; schema-reject 24; application-reject 16; normalization 8, retry 3, strict JSON 4 and drift 3 passed |

Documentation links/whitespace, unchanged source/schema/fixture comparison, archive CRC/hash/exclusion checks and exact documentation-patch application were also checked. All transport/harness/Python/schema/fixture/example snapshots remain unchanged. The scoped patch updates only seven existing architecture/results documents and adds this native validation report.

## Cleanup and guarantee limits

The audited run recorded **40 owned children**: one native Redis server and 39 workers. All were reaped by the unchanged harness: **31 exited 0**, and **9 exited -9**. Eight killed workers were matched to the exact prepared/published/before_ack/acknowledged markers across Colour and Aggregator; the ninth kill was the waiting-worker restart test. The eight phase markers, role/PID mapping and return codes are preserved in audit evidence.

The wrapper found **zero live children before its fallback cleanup**, so it terminated no extra processes. No child remained live afterwards. The single private temporary directory/socket was removed. The recorded PIDs were independently checked absent from /proc after completion. No unrelated processes were stopped. Logs were copied before deletion; no Redis data files were created or retained. The build tree/binary and external evidence directory remain user-owned outside the repository, with no running Redis service.

These results validate **worker process termination/restart, fencing, reclaim and handoff recovery against a continuously running Redis server** under the tested settings. Redis-server restart, power loss, disk persistence, backup/restore, failover/HA, arbitrary key loss/deletion, production authorization and throughput remain untested. Snapshots and AOF were deliberately disabled; no durability claim follows from these passes. PostgreSQL delivery, inference/providers and production entry points remain disconnected/unchanged. No supervisor SQL, historical fixture, SQLite, commit or push work occurred.

## Review evidence

Updated review ZIP: `/home/intern/part-time/aziz/vehicle_redis_scalar_review.zip`.

- `_review/native_validation.patch`: exact documentation-only patch against the preceding verified archive.
- `_review/native_evidence/integration-initial.log` and `integration-audited.log`: raw complete 11-method transcripts.
- `_review/native_evidence/native-audit.json` and `audited-runtime/`: actual owned commands/PIDs/exit statuses/settings, server/worker logs and phase markers.
- `_review/native_evidence/provenance.json`, `integrity.txt`, `redis-hashes.README`, `build.log`, `regression-summary.json` and `audit_native.py`: release/build/integrity evidence, executed regression summary and external evidence wrapper.
- `_review/manifest.json`: per-file hashes and scoped/historical attribution.

Raw build/runtime outputs are packaged from the external directory as review metadata, never copied into tracked source. The ZIP excludes Redis executables/source tarballs/object files, credentials/.env files, environments, weights, videos, caches, databases and SQL.
