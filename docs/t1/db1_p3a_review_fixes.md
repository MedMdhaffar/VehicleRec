# P3a source-review fixes — 2026-10-09

All four reported defects were reproduced against the inspected source before
changing implementation. The initial builder/coordinator regression batch ran
72 tests: **13 failing assertions and 3 escaping OverflowErrors**, including
parameterized scenarios. The fixes below now pass the complete requested checks.
No durable delivery, database, transport or real inference was introduced.

## Changed source and regressions

- `vehicle/common/db1_rows/builder.py`: owner-based retry matching, disappearance
  conflicts, original proposal diagnostics/accepted identities, parent ordering,
  result-route execution attribution and exact numeric admission.
- `vehicle/common/observation_coordinator.py`: independent per-result acceptance,
  acknowledgment and release progress, cleanup retries and protected draining.
- Classification `pipeline.py`: idempotent acknowledgment and recovery from
  partial lifetime release before owner bookkeeping completes.
- `vehicle/common/vehicle_observation.py`: allocator spent-range query and
  `VehicleLifecycle.is_released`, without retaining per-ID acknowledgment tombstones.
- Colour and plate `shared.py`: idempotent release of known finalized IDs with
  owner checking; unknown/live/invalid IDs are rejected.
- `vehicle/common/tests/test_db1_rows.py`: seven additional review tests, covering
  the four findings, disappearing SQL projections, parent blocking and unrelated
  overflow propagation.
- `vehicle/common/tests/test_coordinator_cleanup.py`: six new tests with actual
  shared consumers and injected inference/futures. Before/after-side-effect
  classification and both consumer failures, earlier successful colour release,
  partial lifecycle release, idempotence, sink rejection and drain protection.
- `vehicle/common/tests/test_foundations.py`: two additional spent-range/released-ID
  tests, including nondefault allocator start and BIGINT exhaustion boundary.
- Common and builder READMEs, plus this report, document the revised semantics.

No contract schema, manifest or fixture edits were required. Supervisor archive
and all existing fixture files were fingerprinted before changes and verified
unchanged afterward. Other existing working-tree changes were preserved.

## 1. Accepted payload disappearance

**Reproduction.** A valid accepted read was followed by the same owner identity
with an empty plate ledger, no selected attempt, outcome omitted and no_detection.
Old code returned omitted. Invalid selected text returned blocked; unsettled plate
and quarantined lifetime variants likewise failed to conflict with accepted rows.
Matching happened only inside the new delivery-is-present branch.

**Fix.** Prior facts are matched by row kind plus org UUID, natural run ID, natural
camera ID and allocated service track ID from the immutable owner. Matching no
longer depends on the new payload. Losing an accepted natural payload or SQL
projection, changing payload/projection or changing UID policy yields
`retry=conflict`, excludes the fact from `ready_rows`, and retains accepted identity.

The primary P1 diagnostic is quarantine/construction_blocked/PAYLOAD_CONFLICT and
carries the **original accepted UID**. Immutable construction metadata retains:

- `accepted_row_uid`, `accepted_delivery_sha256`, `accepted_sql_sha256`;
- `proposal_diagnostic`: the entire original valid P1 diagnostic, including its
  new omission/validation reason, error code and settlement counts.

Original proposal issues remain alongside the conflict issue. This preserves
no_detection, invalid selected text, unsettled_operation and clock_discontinuity
without disguising them. P1 requires incomplete settlement to have reason
unsettled_operation, so the primary conflict envelope excludes settlement and
preserves it unchanged in the original proposal sidecar. No schema relaxation was
needed. Original quarantine metadata remains true; no analytical rows are eligible.

Parent eligibility runs **before** retry comparison. A conflicting/blocked parent
removes child SQL eligibility; an accepted child whose projection then disappears
also conflicts and preserves its PARENT_NOT_READY proposal diagnosis. The builder
does not restore prior SQL into a new proposal or mutate prior accepted results.

Tests cover accepted-to-omitted, invalid, unsettled, quarantined and lost-resolution
states, unchanged retries, first-time omission and parent blocking. The existing
unresolved-to-resolved test continues to permit an unchanged natural payload gaining
its first resolved projection. Previously accepted null media gaining new media
continues to conflict, as documented in P3a.

## 2. Acceptance and retryable cleanup

**Reproduction.** Classification acknowledgment removed its pending closure before
consumer release. A release exception left combined evidence stranded: later
flushes consulted only classification's now-empty closure list. Acknowledgment
exceptions also caused duplicate sink calls or lost cleanup, depending on whether
its side effect had completed. Draining could discard stranded evidence.

**Fix.** `_completed` retains the immutable combined result; `_cleanup` retains
per-service-ID progress independently of classification's pending closure list:

1. The completion callback is captured when the result is staged. Acceptance is
   recorded only after that callback returns successfully.
2. Owner acknowledgment runs and is recorded only after successful return.
3. Each consumer release runs and is recorded separately after successful return.
4. Evidence/progress for callback mode is removed only after all required cleanup
   completes. Without a callback, cleaned evidence remains for the legacy explicit
   drain, as before.

`flush_closures` retries existing progress before staging new closures. Accepted
callbacks are not reinvoked merely because acknowledgment/release failed; already
successful consumer releases are skipped. Staged results are reused without new
finalization/recognition. A failed callback retains unaccepted evidence and may be
invoked again on retry. A callback that performs a side effect then raises has not
signaled acceptance; this scope cannot prove whether that external side effect
occurred.

Classification acknowledgment is idempotent for known released run IDs. If lifetime
release succeeded but subsequent acknowledgment bookkeeping failed, retry skips
already released lifetimes, clears profile lifetime references and removes the
owner closure. The allocator's contiguous spent range plus current lifetime map
identifies released IDs in constant historical storage. Unknown/unallocated/live
IDs still fail; nondefault allocator starts and BIGINT exhaustion are respected.
`VehicleLifecycle.release_closed` itself retains its existing validation semantics;
re-closing a released lifetime still raises KeyError.

Colour/plate release checks owner authorization and known finalized identity, then
uses a harmless repeatable pending-record removal. An exception after that removal
can therefore be retried. A successfully released colour consumer is skipped when
plate cleanup later fails. Generic future consumers must implement this same
idempotent release contract; an indefinitely failing implementation leaves pending
cleanup and raises rather than discarding evidence.

`drain_completed` raises `PendingCleanup` while acceptance or required cleanup is
pending. It does not clear `_completed`, `_cleanup` or legacy history in that case.
After flush succeeds, no-callback results drain once; callback-accepted results do
not reappear in drain. `max_pending` still bounds staged evidence, including pending
cleanup, and progress is removed when cleanup completes. No unbounded retry registry
or per-observation acknowledgment history was added.

Tests inject exceptions **before and after** classification acknowledgment and each
consumer release, plus after the lifecycle release inside acknowledgment. They
assert a single successful sink call, unchanged staged evidence, no repeated plate
finalization, one release for the successful other consumer, safe repeated release,
unknown/wrong-owner rejection and protected draining. Actual shared consumer
algorithms receive only injected detections/recognition results; no model runs.

## 3. Read-result execution attribution

**Reproduction.** One operation recorded private_local OCR A followed by OCR B, while
SelectedPlate still named A. The old any-matching-execution test accepted A.

**Fix.** For a read, construction finds the **last execution matching the actual
result route**, then requires its route/model to equal SelectedPlate. A stale model
produces RESULT_EXECUTION_MISMATCH, even if it executed earlier in that operation.
The existing crop/score/parent/segment checks remain. The rule matches the producer's
existing reverse execution lookup; no new inference or result-selection authority
was introduced.

Tests reject A after same-route B, accept correctly selected B with explicit model
resolution, accept a local result when an agent execution follows, and accept an
agent result when a later local execution follows. The result route, not simply the
last global execution, determines attribution. Ledger length remains crop-operation
count; extra route executions do not become extra attempts. Unreadable attribution
retains its existing last-executed-route rule and P1 quality selection.

## 4. Bounded numeric rejection

**Reproduction.** Oversized integer class confidence and crop quality reached
math.isfinite, which attempted float conversion and raised OverflowError outside
construction admission. Signaling Decimal NaN also failed to produce INVALID_SCORE.

**Fix.** Supported numeric types/bools are checked first. Decimal finiteness uses
`is_finite`; math.isfinite is used only for floats. Exact nonnegative magnitude
checks occur before conversion, using the exact largest binary32 value
`2**128 - 2**104` for quality and one for confidence. Bounded floats convert explicitly
through Decimal.from_float; integer/Decimal handling stays exact. Existing nonzero
REAL underflow rejection remains. Malformed scores yield INVALID_SCORE diagnostics.
No broad OverflowError handler was added.

Tests exercise positive/negative `10**1000`, huge finite Decimal, quiet/signaling
NaN, infinity and oversized crop quality. Existing exact-decimal, finite-score,
REAL overflow/underflow, explicit-zero and null-quality tests still pass. A separate
regression verifies an unrelated identity-policy OverflowError propagates rather
than being mislabeled as bad evidence or a score error.

## Executed checks

All commands used the existing VM `/home/intern/.venv/bin/python`, Python 3.11.15,
with `PYTHONDONTWRITEBYTECODE=1`. No dependencies were installed. P1 uses jsonschema
4.26.0, Draft 2020-12 and FormatChecker.

| Command after environment prefix | Final executed result |
|---|---|
| `/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**; all original schemas/manifests/fixtures and P3a diagnostic cases. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_db1_rows` | **72 passed**, 1.687 seconds. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_foundations` | **21 passed**, 0.009 seconds. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_coordinator_cleanup` | **6 passed**, 0.466 seconds, with parameterized before/after scenarios. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests` | **24 passed**, 0.213 seconds. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests` | **33 passed**, 0.082 seconds. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests` | **57 passed**, 0.136 seconds. The provider timeout message is from an injected test, not provider execution. |

Static checks also passed: AST parsing of all nine changed Python files, whitespace
checks of all 12 changed source/report files, and `git diff --check` (exit 0).
`sha256sum -c /tmp/p3a-review-supervisor.sha256` verified the supervisor archive;
`sha256sum -c /tmp/p3a-review-fixtures.sha256` verified all eight fixture files.
The unchanged archive SHA-256 is
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.

The original regression reproduction command was the combined builder/cleanup
unittest invocation. After implementation it passed 72 tests, then 78 with the
additional cleanup and identity regressions. The final separate results above
include all changes; relevant suites were run once after those source changes.

## Remaining limitations

Acceptance and cleanup progress are **in memory and single owner**. Process loss
loses progress; no persisted idempotence, concurrent delivery or crash recovery is
claimed. Callers retry `flush_closures` after transient cleanup failures and keep
the coordinator alive; an unrecoverable consumer error remains explicit pending
cleanup. A failing cleanup stops that flush before later closures are processed;
there is no background retry, durable sink or automatic invalid-run recovery.

Prior constructions must be caller-supplied accepted results under a stable logical
identity policy. Proposed conflicting payloads are retained for review but excluded
from ready_rows; original accepted data is not overwritten. Diagnostic sidecars
are not SQL columns or a persisted conflict registry. Model execution entries
remain supplied provenance claims, not independent proof of recognition accuracy.

P3a's actual registration/media/domain-mapping dependencies, missing selected
classification crop, unsupported manufacturer and DB1 v0 no-crop semantics remain.
No real video/models, RTSP/provider execution, SQL/database/Redis/upload integration,
training, T2 work, T1 freeze, commit or push occurred. Supervisor archive/SQL and
historical fixtures remain unchanged.
