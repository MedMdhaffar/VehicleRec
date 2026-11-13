# Bounded scalar Redis implementation — executed results

Date: 2026-11-01. Current status: **all 11 real-native-Redis integration methods passed in two complete runs**, without skips or transport/harness fixes. The previous missing-runtime blocker was resolved through an explicitly authorized user-owned build of official Redis 8.2.11, outside the repository. Source integrity, exact commands, actual settings, raw evidence, regression results and cleanup are recorded in [native validation](vehicle_redis_native_validation.md). Earlier implementation/review execution records below are preserved as historical results, including their then-valid runtime blockers. Redis-server restart/power-loss durability, inference and PostgreSQL remain outside the tested scope.

## Files changed

| Repository-relative files | Ownership/change |
|---|---|
| `vehicle/common/redis_scalar/__init__.py`, `codec.py`, `reducer.py`, `journal.py`, `worker.py`, `synthetic.py` | New pure scalar codec/reducers, fenced Redis handoff, explicit Colour/Aggregator processes and deterministic scalar producer |
| `contracts/vehicle_redis_scalar_v1.schema.json` | New closed versioned scalar message schema; existing DB1 validation remains canonical |
| `contracts/redis_scalar_examples/colour.batch.json`, `vehicle.closed.json`, `colour.terminal.json`, `db1.delivery.json` | Four new versioned canonical synthetic message examples |
| `vehicle/common/tests/test_redis_scalar.py` | 18 pure tests (12 original + 6 review regressions), including actual existing in-memory fixture byte/hash equivalence, both orders, strict JSON/schema/limits, completeness, identity/conflicts, waiting, exact integer state reload, no silent float rounding and fresh-process imports |
| `vehicle/common/tests/test_redis_scalar_integration.py` | Eleven executable real-native-Redis tests (8 original + 3 review cases), with eight child-kill subcases and partial-write/lost-reply/capacity tests; now passed in native validation |
| `scripts/vehicle_redis_integration.py` | Explicit isolated-native harness runner; exit 2 when runtime unavailable |
| `docs/architecture/vehicle_component_ownership.md`, `vehicle_execution_and_recovery.md`, `vehicle_data_visibility.md`, `vehicle_redis_contracts.md`, `vehicle_transition_plan.md` | Current status and follow-up links; preserve original ownership and historical evidence |
| `docs/architecture/vehicle_redis_scalar_integration.md`, `vehicle_redis_scalar_results.md` | Full message/completeness/recovery/bounds/commands specification and this execution report |

No applicable AGENTS.md was found in the repository or applicable ancestors. Git status was inspected first. No legacy runtime source, canonical Colour/class policy, RowBuilder, supervisor SQL or historical fixtures was modified. Pre-existing tracked/untracked changes remain preserved. The prior verified review archive is the exact follow-up diff baseline, separate from Git HEAD's pre-existing changes.

## Public interfaces and commands

`Codec.encode/decode` uses strict bounded JSON and the existing canonical profile. `reduce_message` accepts explicit caller-owned scalar state and delegates to existing Colour/Aggregator boundaries. `Journal` requires an injected client and implements fenced prepare/publish/finish; it never opens connections. `Worker` requires explicit client, codec and trusted namespace/org/consumer. Explicit CLI startup is the only connection path.

The complete three-process Colour worker, Aggregator worker and scalar producer commands are in [integration specification](vehicle_redis_scalar_integration.md#exact-three-process-commands). They require a pre-existing explicitly chosen Redis destination. The native-runtime harness command is:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_redis_integration.py
```

## Initial Redis implementation checks (before this review follow-up)

All Python checks below used the existing `/home/intern/.venv/bin/python` with `PYTHONDONTWRITEBYTECODE=1`; no packages were installed. These are new session results. Counts in previous Colour slice/review reports remain historical and are not substituted for these executions.

| Exact arguments / check | Result executed in this session |
|---|---|
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar -q` | **128 tests passed**: existing 116 + 12 new codec/reducer tests |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 tests passed**; injected legacy doubles only |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 tests passed**; injected decoder/detector, no inference |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**, real jsonschema 4.26.0 |
| `contracts/validate_examples.py` | Schema valid; valid 18, schema-reject 24, application-reject 16; normalization 8, retry 3, strict JSON 4 and vocabulary/schema drift 3 passed |
| `-m unittest vehicle.common.tests.test_redis_scalar -q` | **12 tests passed** in its final standalone run |
| `scripts/vehicle_redis_integration.py` | **Exit 2: BLOCKED**; setUpClass skipped for missing native Redis. **0 real integration tests executed, 1 class skipped** |
| `-m vehicle.common.redis_scalar.worker --help` and `-m vehicle.common.redis_scalar.synthetic --help` | Both exit 0; no connection/startup |
| Four new message examples decoded through the actual Codec | Passed; final natural bundle hash `c492561f33cf1c6c69273fc095fc3cde9ebf94218e96097617a9735996642da6` |
| Pure import subprocess in new suite | Passed; no redis client, cv2, numpy, torch, ultralytics, paddle, openai, sqlite3, legacy SQL builder or coordinator imported |
| Changed-file Python AST/whitespace, architecture local links/anchors, `git diff --check`, prior archive protected-file comparison | Passed |

Intermediate results: pure suite checkpoints of 10 and 11 tests passed; the earlier 127-test combined run preceded the final precision regression (the 128-test run supersedes it). No pure unittest failures occurred. The initial redis-server version command failed because the executable was not found; the real integration harness blocker above is not integration success. During code review, journal snapshot decoding was corrected to restore P1 scientific-notation mathematical integers before domain validation, and the unexecuted harness settings reader was corrected for redis-py's string-key CONFIG GET response. The exact-number reload regression passes. No claim is made that these static corrections validate Lua/Redis behavior.

## Implemented protocol versus untested guarantees

The implementation explicitly retains exact output bytes/fixed stream IDs before ACK, handles partial Lua progress and uncertain replies, rejects stale owners and conflicting logical retries, persists incomplete joins and bounds polling/repair/state/streams. No automatic trimming/expiry of unfinished work exists. These mechanisms have been code-reviewed and have executable real-Redis tests, but **their actual Redis/process-crash guarantees have not been exercised here**.

The harness requests a user-owned 0700 private Unix-socket instance, port 0, save="", appendonly=no, maxmemory=64mb, maxmemory-policy=noeviction and databases=1. **No actual Redis version/settings were observed** because no server was launched. Even after that harness passes, it tests worker process restart/reclaim against a continuously running server, not Redis-server restart, power loss, persistence/HA, arbitrary restore/deletion, production ACL authorization, throughput or deployment. PostgreSQL/DB1 commits and native inference remain disconnected. Plate enabled stays unfinished; only disabled/unsupported domain omissions are supported for final output. Explicit retention/namespace rotation remains an operator contract.

## Review archive

Updated package: `/home/intern/part-time/aziz/vehicle_redis_scalar_review.zip`. Repository-relative files, canonical schemas/examples, executable tests/harness and review dependencies are included. `_review/redis_integration.patch` is the exact diff against the previous verified Colour review ZIP. Manifest SHA256 values and attribution distinguish this follow-up from original slice/review fixes and pre-existing changes. Required-file coverage, CRCs, hashes, safe paths and exclusions are verified. No credentials/.env files, virtual environments, weights, videos, caches, databases or supervisor SQL are included. Packaging modifies no source.

## Three-finding review follow-up — executed now

Date: 2026-11-01. The exact baseline is the preceding verified `vehicle_redis_scalar_review.zip` (SHA256 `44010fa2993bea3290e27a0dde71e15d59a4bca35282689e0845760f4a2ce2f6`). The follow-up changes only eight existing files: `vehicle/common/aggregator.py`; `vehicle/common/redis_scalar/codec.py`, `reducer.py`, `worker.py`; `vehicle/common/tests/test_redis_scalar.py`, `test_redis_scalar_integration.py`; and these two Redis results/protocol documents. No schema, examples, legacy entry points, RowBuilder, canonical class/Colour selection policy, supervisor SQL or historical fixtures changed.

1. Worker dispatch now rotates source priority and independently alternates pending/new preference for each source. Stream rotation advances before processing, including a failed dispatch. Retained-intent repair still takes priority and an empty scan remains bounded to four COUNT=1 polls. Two ready streams each receive service every two non-repair turns; all four continuously ready source/mode lanes receive service every four such turns. The pure test drives actual `Worker.step` with controlled always-ready poll responses; it tests selection logic, not Redis/PEL semantics. The native case preloads both streams with pending and new records, checks balanced dispatch across eight turns and then six deliveries.
2. Colour state retains `batch_id` alongside `batch`. Reducer retry admission compares the original accepted event ID and canonical body. A changed ID with identical content raises PAYLOAD_CONFLICT, leaving the original terminal correlation intact. Same-ID/same-content retries remain identical and do not republish. Legacy state lacking its original batch ID is not silently authenticated when it reaches the reducer. The native case checks a conflict diagnostic, unchanged terminal/state correlation, successful original retry and final delivery.
3. Existing Aggregator class-admission checks were extracted into `validate_class_samples`, shared by closure Codec/reducer admission and final Aggregator validation. These enforce typed immutable samples, unique frames, exact lifetime identity, receipt consistency/ranges, labels and canonical scores without selecting another class. Invalid closure decode happens before accepted-state lookup/retention; the reducer defensively validates before assigning its immutable closure slot too. The native case verifies no closure slot is poisoned, and that a later valid closure with the same event ID produces the original final bytes in both closure/terminal arrival orders.

Reproduction before fixes: the five targeted tests ran with **five failure entries**, including the duplicate-class-frame subtests; repair-priority/bounded-empty-scan already passed. This reproduced source starvation, changed-ID silent acceptance and invalid closure retention. After implementation, the expanded suite passed first 17 and then its final **18 tests**, including exact canonical delivery equality and pure imports. The extra shared-admission test checks explicit lifetime attribution.

All commands below were executed using the existing `PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`; real jsonschema **4.26.0** was verified at `/home/intern/.venv/lib/python3.11/site-packages/jsonschema/__init__.py`. Original counts above are historical checkpoints, not current execution claims.

| Exact Python arguments / check | Follow-up executed result |
|---|---|
| `-m unittest vehicle.common.tests.test_redis_scalar -q` | **18 tests passed** |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar -q` | **134 tests passed** (previous 128 + 6 review regressions) |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 tests passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 tests passed** |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | Schema valid; valid 18, schema-reject 24, application-reject 16; normalization 8, retry 3, strict JSON 4 and vocabulary/schema drift 3 passed |
| `scripts/vehicle_redis_integration.py` | **Exit 2, BLOCKED: missing native redis-server**; **0 integration tests executed**, 1 class skipped; 11 methods present, not counted as successes |
| Fresh-process pure-import check within the suite | Passed; no Redis client or heavy inference/SQLite/legacy SQL modules imported |
| Changed Python AST/whitespace, architecture local links, four versioned examples and `git diff --check` | Passed |
| Prior review archive comparison | Exactly eight scoped files differ; the other 152 repository-file snapshots, including all historical fixtures/schema/examples and unrelated source, are unchanged |

The review archive is updated at `/home/intern/part-time/aziz/vehicle_redis_scalar_review.zip`. `_review/redis_review_follow_up.patch` is the exact eight-file patch against the preceding archive; earlier slice/fix/integration patches remain explicitly historical context. Manifest attribution and SHA256 values identify the baseline and changed paths. ZIP CRCs, safe paths, exclusions, file hashes and patch applicability are verified. No install, Docker, shared Redis/PostgreSQL connection, inference/provider call, SQL/fixture alteration, commit or push occurred. Native Redis/process-crash and durability claims remain blocked/unverified; skipped integration tests are not success.

## Real native Redis validation — current execution

The missing native runtime is now resolved. Official Redis 8.2.11 was downloaded, SHA256-verified against published Redis integrity information and built with existing GCC/make outside tracked source. The unchanged `scripts/vehicle_redis_integration.py` ran with an explicit `VEHICLE_REDIS_SERVER` path: **11 tests passed in 42.874s, exit 0**. A second evidence-audited execution of the unchanged harness passed **11 tests in 42.973s, exit 0**. These are eleven distinct methods run twice, not twenty-two distinct test cases. No actual test failures occurred and no transport/test-harness source fixes were needed.

Fresh affected checks also passed: **134 common/boundary tests, 33 Colour tests, 24 classification tests, 122 DB1 fixture checks**, and the contract example suite. Eight kill-boundary subcases per native run exercised both workers at prepared/published/before_ack/acknowledged. The audited run recorded **40 owned child processes**, all reaped: 31 exits 0 and 9 exits -9 (eight boundary kills plus the waiting-worker restart kill). It recorded zero live children before fallback cleanup and zero surviving private directories. No unrelated process was stopped.

Actual Redis settings: version=8.2.11, port=0, bind=127.0.0.1, private Unix socket and directory modes=0700, databases=1, protected-mode=yes, maxmemory=67108864, maxmemory-policy=noeviction, save="", appendonly=no. appendfsync=everysec was configured but inactive because AOF was off. Redis-server restart, persistence/HA and power loss were not tested.

See [native validation](vehicle_redis_native_validation.md) for exact commands/checks and archive evidence. Only documentation is changed in this task; every previously packaged Python source/test and JSON schema/fixture/example remains byte-identical to the previous verified review archive. The updated ZIP includes `_review/native_validation.patch` and `_review/native_evidence/`; downloaded Redis source/binary/build outputs remain outside tracked source and the ZIP excludes executables, source tarballs, databases and credentials.
