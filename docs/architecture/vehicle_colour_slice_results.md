# First Colour-to-Aggregator slice — executed results

Date: 2026-11-01. These results were executed during this implementation session. Historical audit counts retained in the transition plan are separate records, not evidence of execution here.

## Changed files and ownership

| Files | Change |
|---|---|
| `docs/architecture/vehicle_component_ownership.md`, `vehicle_execution_and_recovery.md`, `vehicle_data_visibility.md`, `vehicle_redis_contracts.md`, `vehicle_transition_plan.md` | Confirm supervisor decisions, source decoding before Engine and paired receipt/timing semantics, remove provider fallback from target, replace superseded proposed contracts/allocations; retain historical implementation inventory and historical test descriptions |
| `docs/architecture/vehicle_colour_slice_results.md` | This implementation and executed-check record |
| `vehicle/common/colour_metadata.py` | Metadata-only `ColourMeasurement`, `ColourResult`, `ColourMetadata.finalize`; explicit original attempts, identities, receipts, quality and versioned algorithm provenance |
| `vehicle/common/aggregator.py` | `ClassSample`, `Branch`, `JoinResult`, `Aggregator.join`; Aggregator owns class consensus and all-enabled-terminal joining; waiting has no delivery |
| `vehicle/common/db1_rows/core.py`, `natural_policy.py`, `natural.py` | Shared identity/canonical serialization/lifetime/parent validation and natural projection; `NaturalBuilder`/`NaturalBundle` without SQL/crops |
| `vehicle/common/db1_rows/builder.py`, `records.py`, `__init__.py` | RowBuilder retains compatibility and SQL resolution behavior using the extracted policies; integer validation shared; lazy legacy exports keep SQL/coordinator dependencies out of new imports |
| `vehicle/vehicle_color_service/vehicle_color_service/scalar_policy.py`, `temporal.py` | Move canonical Colour scalar policy to pure module, re-export from legacy temporal module; pixel quality helpers stay separate |
| `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/consensus.py`, `pipeline.py` | Move canonical class consensus to scalar module and retain legacy import/call behavior; preserve pre-existing pipeline edits |
| `vehicle/common/tests/test_colour_aggregator.py` | 17 tests with synthetic scalar fixtures (12 original + 5 review regressions); no historical fixture changes |

No AGENTS.md was found in the repository, its scanned parent tree or applicable ancestor paths. Git status was inspected before edits. Pre-existing plate, detector, classification schemas/service and other untracked work remain preserved. Pipeline edits were limited to consensus extraction on top of its existing working-tree content. An incidental virtualenv bytecode modification caused by the first interpreter probe was restored to its initially clean tracked content.

## Interfaces and invariants

`ColourMetadata.finalize(lifetime, tuple[ColourMeasurement, ...])` returns frozen `ColourResult`. Engine supplies original scalar measurements; Colour alone selects the domain winner using the extracted policy. Every attempt includes original attempt ID, VehicleIdentity (including explicit org_id), FrameReceipt, confidence, quality, RecognitionIdentity and quality algorithm. No pixels, provider client or provider request parameter exists. Unknown/insufficient evidence produces DB1-supported `undecided`, null colour/confidence and real local algorithm attribution. Zero-valid-sample attempts retain provenance. Bounds are 128 attempts and 20 valid samples; exceeding a bound blocks explicitly.

`Aggregator.join(owner, class_samples, branches, prior=None)` requires explicit Colour/Plate/Manufacturer capabilities. It computes canonical class consensus, validates attribution without a second Colour winner, and joins terminal branches. Enabled unfinished Plate returns `JoinResult('waiting', ('plate',), None)`. Disabled Plate/unsupported Manufacturer are truthful terminal omissions. Completed Plate/Manufacturer are outside this slice and explicitly blocked.

`NaturalBuilder.construct(owner, colour, omissions=..., prior=None)` builds a frozen `NaturalBundle`. The bundle contains canonical natural parent/Colour deliveries, explicit org_id, omissions and original Colour attempt provenance. Facts reuse canonical UUID5 identity, DB1 delivery schema validation and per-fact supporting timestamps. Equivalent reordered input has identical canonical bytes/hash. Caller-supplied prior acceptance detects changed payload/provenance as coded `PAYLOAD_CONFLICT`; no new identity is allocated. Identity/provenance/receipt mismatches raise coded blockers. RowBuilder retains its old resolved-row readiness, diagnostics, omissions, SQL validation, agent evidence compatibility and retry semantics.

## Original slice checks (executed before review fixes)

All successful Python checks below used the existing interpreter, with **no installation**:

```text
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python
```

| Arguments / check | Executed result |
|---|---|
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator -q` | 111 tests passed: 99 existing + 12 new |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | 33 tests passed; injected legacy agent doubles only, no provider calls |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | 24 tests passed; injected capture/detector, no model execution |
| `contracts/db1/validate_fixtures.py` | 122 passed, 0 failed |
| `contracts/validate_examples.py` | Schema valid; 18 valid, 24 schema-reject, 16 application-reject; normalization 8, retry 3, strict JSON 4, vocabulary/schema drift 3 checks passed |
| Fresh subprocess import test within new suite | No cv2, numpy, torch, ultralytics, paddle, openai, sqlite3, legacy coordinator, SQL builder or SQL resolution records imported |
| `git diff --check` and explicit changed-file whitespace/local-document-link check | Passed after correcting a trailing blank line |

There were intermediate failed checks while implementing: the repository `vehicle/.venv/bin/python` ran one unittest loader error because jsonschema was absent; system python3 also could not import jsonschema. No packages were installed; the existing `/home/intern/.venv/bin/python` provides it. The initial new-suite run had 7 passes/1 failure due to a test expecting noncanonical timestamp spelling; the test now checks the existing canonical UTC spelling. A later 80-test combined run had 5 errors while provenance validation reconstructed exact-number JSON receipts without converting PTS back to the receipt's float representation; corrected before the passing runs. The final combined suite passes. An initial `python` launcher probe failed because only `python3` exists. Earlier successful intermediate runs (72, 80 and 107 tests) were development checkpoints, not additional distinct coverage.

## Limitations and exact next step

This is an in-memory proof; no deployed service architecture or durability claim. All runtime entry points continue legacy behavior. No broker/database connections, migrations, supervisor SQL or historical fixture modifications, models/providers, package installations, SQLite extension, commit or push occurred. PostgreSQL and Redis integration tests were not run. Natural delivery format is a Python-tested internal bundle, not a frozen deployed wire schema. RunContext is reused with unresolved development keys, not replaced with a new production authorization contract. Prior acceptance and waiting state remain caller-owned memory. Native scheduling, real inference/quality extraction, Plate/Manufacturer terminal implementations, tenant authorization, trace schema/retention and consumer transactions remain future work.

The exact next Redis step is to add a versioned scalar codec and recoverable transport adapter around these interfaces, first using the same injected fixtures. Use a trusted org partition, `engine.evidence.colour`/`colour-v1`, lifecycle/results inputs for `aggregator-v1`, `results.colour` for terminal output and `db1.delivery` for immutable ready bundles. Validate strict bounded JSON, org/run/camera binding and canonical hashes. Persist reducer state, dependency waits and output bytes in a same-slot state/output/ACK journal **before XACK**. Test duplicate/changed retries, reordered inputs and crash/reclaim around publication/ACK before enabling real inference. See [Redis wiring specification](vehicle_redis_contracts.md#exact-next-redis-wiring-step). PostgreSQL remains unwired at that step.

## Review fixes — checks executed in this follow-up

All four findings are fixed. Changes in this follow-up are limited to `vehicle/common/colour_metadata.py`, `vehicle/common/aggregator.py`, `vehicle/common/db1_rows/natural.py`, `vehicle/common/tests/test_colour_aggregator.py`, this results report and `vehicle_transition_plan.md`. Legacy RowBuilder and canonical policy implementations are unchanged.

1. `ColourResult.validate` reconstructs the original typed scalar attempts and delegates replay to `ColourMetadata.finalize`, using the same canonical Colour policy that produced the result. Changed final state/label/confidence raises `COLOUR_DECISION_MISMATCH`; inconsistent supporting evidence or provenance raises `MODEL_PROVENANCE_MISMATCH`. Aggregator implements no Colour winner policy. The policy-authority test now verifies a call to Colour's canonical policy during domain validation, replacing the earlier test that forbade replay entirely.
2. `ColourResult.original_receipts()` exposes receipts from every original attempt, including samples rejected by Colour. Aggregator checks these against class samples, owner receipts and terminal evidence. NaturalBuilder also includes all original receipts in its consistency check. The reproducer uses an invalid frame-1 sample with a different monotonic receipt from the class sample for frame 1.
3. Aggregator checks prior natural identity before its waiting return. Once a terminal natural bundle has been accepted, missing terminal input or regression to unfinished Plate raises `PAYLOAD_CONFLICT`. First-time unfinished Colour/Plate still returns waiting with no delivery. This remains caller-owned in-memory acceptance history.
4. NaturalBuilder sorts validated terminal omissions itself before constructing canonical bytes. A direct boundary regression verifies equal bytes/hashes and an identical prior retry with reversed omission order, without relying on Aggregator sorting.

The five new reproducing tests were executed against the unfixed implementation first: **5 tests ran, 8 failures** (four terminal-claim cases plus invalid receipt, missing accepted Colour, regressed Plate and omission ordering). After fixes, the complete slice suite passed **17 tests**. These are fresh executions, distinct from the original slice results above.

All following commands were executed using `PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`; real `jsonschema 4.26.0` was verified at `/home/intern/.venv/lib/python3.11/site-packages/jsonschema/__init__.py`.

| Exact Python arguments / check | Follow-up result |
|---|---|
| `-m unittest vehicle.common.tests.test_colour_aggregator -q` | 17 tests passed, including pure-import subprocess check |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator -q` | 116 tests passed: 99 legacy/foundation/cleanup + 17 slice tests |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | 33 tests passed |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | 24 tests passed |
| `contracts/db1/validate_fixtures.py` | 122 passed, 0 failed |
| `contracts/validate_examples.py` | Schema valid; valid 18, schema-reject 24, application-reject 16; normalization 8, retry 3, strict JSON 4 and vocabulary/schema drift 3 passed |
| `git diff --check` | Passed |
| Changed Python AST/whitespace and architecture local-link checks; comparison with previous review ZIP | Passed; only the six follow-up files differ among previously packaged repository files |

The updated review ZIP retains original repository-relative paths, schemas/dependencies, the identified original slice diff and clearly labeled pre-existing changes. `_review/review_fixes.patch` gives the exact follow-up diff against the previous verified review archive; its baseline SHA256 is recorded in the manifest. ZIP CRCs, required file coverage, exclusions and file hashes are verified after packaging. No new runtime dependencies, Redis/database connections, installations, models/providers, supervisor SQL changes, historical fixture changes, commits or pushes occurred.
