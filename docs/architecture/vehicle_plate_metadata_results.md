# Bounded Plate metadata slice — 2026-11-03

Implemented and exercised **in memory with injected scalar evidence**. Plate is now an authoritative pure semantic boundary, joined with Colour and class consensus into `natural-db1-bundle-v1`. The current Colour-only v0 format, Redis codec/workers/journal/examples and MP4 smoke remain unchanged. This is not a deployed Plate service or a distributed followup loop. The dependency/attribution review fixes and their fresh executions are recorded in the final section below.

## Interfaces and ownership

`vehicle.common.plate_metadata` defines frozen, bounded records:

- `PlateTranscript`: original `VehicleIdentity` (explicit org/run/camera/lifetime), capability, candidates, complete expected attempt IDs, detection/admission completeness and explicit `open`/`settled`/`uncertain` settlement, plus scalar local policy configuration.
- `PlateCandidate`: original parent/segment/raw tracker/candidate identity; associated/ambiguous/unassociated status; crop admission; opaque crop reference and SHA256; original paired receipt including independent PTS; Engine-supplied frame rectangle, crop dimensions, detector confidence, nullable quality and quality algorithm identity.
- `PlateCropAttempt`: logical native attempt ID and original submission ordinal, candidate reference, expected operation IDs and immutable operation ledger. Whole OCR, marker and region followups on one crop remain **one attempt**.
- `PlateOperation`: globally unique operation ID, per-attempt unique request sequence and explicit earlier dependencies; local whole/marker/left/right stage, raw scalar output, explicit pending/not_started/succeeded/failed/unknown execution outcome and the actual versioned model/task. No configured/default model substitution exists.
- `PlateEvaluation`: waiting without a result, or terminal `PlateResult` with selected original attempt/receipt/quality/operation/model, or a supported omission. `PlateResult.validate` replays **Plate's** canonical policy; Aggregator has no competing Plate selector.

`PlateMetadata.evaluate(lifetime, transcript)` validates all original evidence, including rejected candidates. It performs semantic OCR normalization, private Tunisian parsing, conservative whole/marker/left/right fusion, local route admission, ordered per-segment recheck replacement and cross-segment quality/null/ordinal/ID selection. Only local private recognition is supported; unsupported operation stages/routes/tasks block explicitly. Invalid or ambiguous local text after eligible known OCR execution can be unreadable. No provider field, client, request or fallback exists in this interface.

`Aggregator.join_with_plate(owner, class_samples, branches, prior=None)` is an explicit v1 entry point. It shares existing class validation/consensus, checks receipts across owner/class/Colour/**all** Plate candidates, delegates terminal authentication to Plate and joins through `NaturalPlateBuilder.construct(owner, colour, plate, omissions=..., prior=...)`. The existing `Aggregator.join` and `NaturalBuilder.construct` remain v0. Enabled unfinished branches produce no final delivery. Disabled/unsupported Plate capability remains an explicit omission. An enabled branch cannot claim disabled terminal evidence.

`NaturalPolicy.plate` is now the single natural selected-attempt projection used by both the pure boundary and the legacy `RowBuilder._plate`. P1 admission, omission classification, unreadable selection, canonical JSON/hash rules, UUID5 row/attempt identities, original selected-crop timestamps, and real jsonschema delivery validation are shared. Per-lifetime original attempt ordinals are mapped to the same stable 1-based ranks as legacy construction. Optional crop media remains null; supplied crop references/fingerprints never fabricate registration. RowBuilder's SQL resolution, provider-evidence admission, SQL fields and legacy output behavior remain supported and tested.

Engine continues to own detection/tracking/association, pixels/crops, quality, transforms, native Paddle/marker model response decoding, every invocation, admission, execution status and settlement. Legacy native adapters and entry points remain compatible wrappers. Nothing in the new pure boundary opens a video, runs a model, queries SQL, or imports a provider/coordinator/transport implementation.

## Work completion, failures and immutability

Closure alone is insufficient: detection and admission must be explicitly complete, settlement must be closed, and every declared expected crop/operation needs its recorded outcome. Missing records, pending operations or open admission/settlement wait even on a closed parent. Dependencies must be earlier operations within the same crop; successful region results require successful whole/marker dependencies. Duplicate IDs/sequences/ordinals, changed segment attribution, wrong parents, invalid scores/geometry and conflicting success/output claims block.

`failed` identifies a known executed model; `not_started` cannot claim a model/output. Unknown execution requires explicit host uncertainty settlement; it produces `unsettled_operation` and no Plate row, including when another attempt could otherwise be read. Pending/missing work stays waiting. A timeout string is not an accepted execution status. Known failure before OCR can yield `failure_before_model`; marker execution alone does not invent an OCR model or unreadable evidence. Complete zero detections yields a truthful `no_detection` omission, **never a `no_plate` row**. Rejected/ambiguous/unassociated cases reuse P1 omission reasons.

A selected result uses the original crop and the **last executed OCR matching the local result route**, which must support the chosen whole/region result. An old whole result cannot borrow a later failed execution's model. Later failed crops cannot reattribute an earlier accepted reading. Original executed marker/OCR models, failed/rejected evidence, expected-work identities and settlement remain in canonical provenance.

Candidates, attempts, expected ID sets, dependencies and operations canonicalize independently of arrival ordering. Both v0 and v1 builders sort terminal omissions internally. V1 contains ordered parent/Colour/Plate facts where emitted, sorted terminal omissions, Colour provenance and `plate_provenance`. Equivalent inputs yield identical canonical bytes, hashes and UUIDs. A supplied prior accepted bundle cannot lose a row/omission, regress to waiting, change text/model/quality/crop reference/receipt or other original provenance, or switch bundle format without a conflict. Accepted bundles are frozen values; callers must retain and resupply `prior` for cross-call retry conflict protection.

Bounds: at most 128 candidates and logical crop attempts; 16 expected/recorded operations per crop; 128 IDs per bounded ID/dependency set (operation IDs within each crop are additionally limited to 16); IDs/model names/references/algorithm identifiers up to 256 UTF-8 bytes; OCR text up to 4096 UTF-8 bytes per successful operation; crop dimensions up to 65536; total canonical Plate provenance at most 4 MiB. Overflow blocks rather than truncating evidence. Integer/score limits reuse P1/legacy constraints. The maximum transcript is deliberately distinct from the smaller existing Redis scalar bounds; v1 is not sent through that codec.

## Reused scalar policies and future followups

The OCR comparison forms/`OCRResult` moved once into `run_video_pipeline.text_policy`, with existing OCR exports preserved. The parser now imports that pure module. `run_video_pipeline.scalar_policy` owns the extracted scalar fusion, local eligibility diagnostics and ordered cached-read replacement. Legacy image orchestration/routing/TrackState call these same functions with the existing configured thresholds. Native response decoding stays in `ocr.PlateReader._parse`, and marker coordinate/image work stays in existing Engine-side adapters. The new boundary uses no legacy configuration module or provider settings. Historical provider paths remain available only to legacy callers.

The existing pure `needs_region_ocr(whole, marker, policy=...)` expresses a possible local followup decision. A future distributed request would bind parent/candidate/crop/attempt IDs to a unique operation ID, sequence, dependencies, stage and policy identity; Engine would admit/execute and return original scalar outcomes plus actual models before explicit settlement. **This slice consumes completed transcripts only**; it implements neither distributed requests, Engine execution admission nor a request/reply transport loop.

## Files changed in this slice

New source/tests:

- `vehicle/common/plate_metadata.py`
- `vehicle/common/tests/test_plate_metadata.py`
- `vehicle/license_plate_service/run_video_pipeline/text_policy.py`
- `vehicle/license_plate_service/run_video_pipeline/scalar_policy.py`

Scoped changes atop existing source:

- `contracts/db1/reference.py` — shared quality/null/ordinal/ID ranking; preserves existing unreadable numeric coercion and legacy read ranking.
- `vehicle/common/aggregator.py` — explicit v1 join; v0 entry point preserved.
- `vehicle/common/db1_rows/__init__.py` — exports the new builder without SQL imports.
- `vehicle/common/db1_rows/natural.py` — explicit v1 builder, original v0 path reused unchanged.
- `vehicle/common/db1_rows/natural_policy.py` — shared Plate projection.
- `vehicle/common/db1_rows/builder.py` — delegates only natural Plate field projection, preserving legacy admission/resolution.
- `vehicle/license_plate_service/run_video_pipeline/ocr.py` — reexports extracted scalar definitions; native decoder unchanged.
- `vehicle/license_plate_service/run_video_pipeline/private_plate_parser.py` — pure scalar import.
- `vehicle/license_plate_service/run_video_pipeline/pipeline.py` — compatible scalar-policy wrappers around Engine image orchestration.
- `vehicle/license_plate_service/run_video_pipeline/routing.py` — compatible legacy image guard and route wrapper.
- `vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py` — delegates existing cached-read replacement.
- `vehicle/license_plate_service/run_video_pipeline/shared.py` — shared read ranking only.

Updated architecture/status: `vehicle_component_ownership.md`, `vehicle_execution_and_recovery.md`, `vehicle_data_visibility.md`, `vehicle_redis_contracts.md`, `vehicle_transition_plan.md` and this results report, all under `docs/architecture/`.

## Original slice checks (before the dependency review fixes)

CWD `/home/intern/part-time/aziz/pipeline`; commands below use `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`. Existing Python 3.11 and **real jsonschema 4.26.0** were used, with no packages installed. Full command arrays, return codes and elapsed times are in `_review/plate_evidence/checks.json`; stdout/stderr are adjacent logs. No skips are counted as passes.

| Python arguments | Final executed result |
| --- | --- |
| `-m unittest vehicle.common.tests.test_plate_metadata -q` | **30 passed**, focused pure/legacy bridge regressions (also included below) |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata -q` | **172 passed** |
| `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q` | **57 passed** |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 passed** |
| `-m unittest vehicle.common.tests.test_db1_outbox vehicle.common.tests.test_db1_adapter -q` | **45 passed**, existing temporary legacy-store and injected-driver compatibility checks, no PostgreSQL connection |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | valid 18; schema rejects 24; application rejects 16; normalization 8; retries 3; strict JSON 4; vocabulary drift 3 |

The new regressions cover whole and region reads; ambiguous/invalid/incomplete text; null quality/ties/best-attempt selection; ordered same-segment rechecks; last-matching actual models and no lending from later failures; parent/association/geometry/ID/dependency/score/bound blockers; rejected-candidate receipt conflicts with class samples; missing expected work and open admission; known/unknown execution and truthful omissions; equivalent ordering/retry byte/hash equality; accepted row/omission disappearance, changed text and changed provenance; legacy parent/Colour and actual local Plate row projection parity; compatible scalar wrappers/native response decoding without inference; and explicit current-Redis rejection of v1.

The import regression runs in a **fresh subprocess** with import hooks that reject OpenCV, NumPy, torch, Ultralytics, Paddle, provider clients/configuration, Redis, SQLite, SQL builder/records and coordinator. It imports Plate, Aggregator and the new natural builder successfully. Separate legacy wrapper tests intentionally import existing image adapters using fake arrays/native responses without loading models.

Development checks exposed and corrected an unmatched parenthesis while extracting rank policy, tuple-to-JSON conversion, and fixture assumptions (seven markerless digits have a unique 3+4 split, unlike the six-digit ambiguous region case; legacy crop geometry must match bytes). A codec-rejection regression initially expected the wrong error code; it now checks `INVALID_DELIVERY` from the actual existing `db1.delivery` envelope. Final checks listed above passed after corrections and shared projection extraction. Earlier 23/27/29-test focused and 169/171-test grouped runs are developmental executions, not additional independent coverage.

Static AST/whitespace checks and archive checks also pass. The packaging verification applies both the full exact overlay patch and the separated supplemental-dependency/scoped patches in private baseline extractions, comparing every packaged source byte and SHA256. The extracted snapshot reruns the grouped 172 boundary tests and 57 Plate tests to prove their local dependencies are included. These duplicate executions are verification, not extra unique tests.

## Review archive and baseline attribution

Archive: `/home/intern/part-time/aziz/vehicle_plate_metadata_review.zip`.

The last verified review baseline is `vehicle_real_video_review.zip`, SHA256 `5af6eab788f4409b63d1ef5b99b6961ade72a027103d4f2cbce94ce5151d17e0`. That archive did not include several existing legacy Plate and adapter/outbox dependencies needed by the selected tests. They are now included with explicit pre-existing attribution. Their absence from the old ZIP does **not** make their entire contents new Plate implementation.

`_review/plate_metadata.patch` is the exact complete overlay against that last verified archive. `_review/supplemental_preexisting.patch` separately supplies omitted pre-existing dependencies; `_review/plate_scoped.patch` then contains only this slice's 22 changed/new paths. The manifest records source hashes, per-path attribution and baseline provenance. For previously omitted files edited in this task, the four initially clean tracked Plate modules use their exact HEAD bytes as the supplemental baseline; the prior edited OCR block and untracked shared ranking are restored by inverses of the bounded extraction edits, retaining earlier work. The supplemental baseline has not had a separate historical test-validation claim. Initial git status and this distinction are retained in the archive. Existing unrelated tracked edits and untracked work remain preserved.

The archive includes all selected local source/schema/test dependencies, all five architecture documents and this report, manifests, scoped/full patches and actual fresh check evidence. Previous native Redis/video evidence, where retained, is labelled historical context. No historical smoke/integration result is counted as executed in this task. Models, video/crops, databases, credentials, .env files, virtual environments, caches, native binaries/build outputs and supervisor SQL are excluded. No commit or push was made.

## Limitations and next integration

There is no Plate Redis codec/worker/journal support, distributed followup loop, live Engine Plate transcript adapter or real OCR/model validation here. No model/provider/video execution or Redis/PostgreSQL connection was performed. Engine trace persistence, registration/authorization, media upload, database delivery and capture/tracker/crop recovery remain subsequent tasks. These injected tests prove boundary behavior and supported legacy parity, not recognition accuracy or production readiness.

The next bounded Redis step is to define and validate versioned Plate-transcript/terminal and natural-v1 envelopes with explicit expected-work/settlement, org/run/crop/operation correlation and faithful bounds; extend the immutable reducer/journal join and retry/conflict rules for Plate while preserving the existing Colour-only v0 route; then exercise injected complete transcripts through isolated native Redis workers. Engine admission/model execution and a distributed local followup loop require their own later implementation and failure/recovery tests.


## Dependency/attribution review fixes — 2026-11-03

Both reproduced findings are fixed without changing records, bundle versions, canonical policies, legacy entry points or transport. The exact follow-up changes only:

- `vehicle/common/plate_metadata.py`
- `vehicle/common/tests/test_plate_metadata.py`
- `docs/architecture/vehicle_plate_metadata_results.md`

Plate now retains the full validated operation graph for scalar interpretation. Each regional operation must explicitly identify **exactly one whole OCR and one marker execution** among its dependencies. Extra earlier dependencies may express ordering, but cannot introduce a second whole/marker binding. Successful left/right outputs considered for fusion must bind the **same** whole/marker executions; ambiguous or mixed bindings raise `INVALID_DEPENDENCY`. The canonical scalar fusion receives those exact executions' raw whole text/confidence and original marker confidence/geometry. A later marker with different geometry or reliability cannot lend its output to earlier region operations. This validates supplied metadata only; no crop geometry or native response is recomputed.

The supporting OCR set is the whole operation for a whole read, or the dependency-bound whole plus both successful regional operations for a fused read. Read attribution selects the last supporting OCR by the contract's recorded operation sequence, whether left or right was requested last, and retains that operation's actual model/version. The existing check against the last known executed OCR on the local route remains: a later failed or unsupported-by-the-result OCR cannot lend its model to stale text. A later independently valid whole OCR is admitted through the existing whole policy and remains eligible. Crop-attempt selection stays with Plate's existing canonical policies; Aggregator is unchanged.

All original operation IDs, sequences, dependencies, raw outputs and models remain in canonical provenance. Equivalent arrival/dependency ordering produces identical bytes/hashes and retries. A real change in request/execution sequence, supporting model or original provenance conflicts with a supplied accepted prior bundle. V0, standard v1 and legacy output compatibility are preserved; no version/Redis/schema expansion was made.

### Reproduction and fresh executions

Eight new regression methods cover weak-marker borrowing; changed marker geometry; mixed whole/marker generations; ambiguous dependency bindings; both regional orders with different model versions; later failed OCR in either order; original whole confidence; and fresh whole reads after regions. They also exercise exact retry equality and conflict on changed execution order.

The first six new regressions were added **before production edits**: the focused suite ran 36 methods and failed in **7 subtests**, reproducing the findings (`reproduced-before.log`). The final 38-method test file overlaid on the exact verified baseline production snapshot fails in **8 subtests** (`baseline-with-regressions.log`, expected exit 1). These failures are intentional reproduction evidence, not passes. A development check also caught an over-conservative first fix that rejected a fresh supported whole read after regions; that guard was corrected before the final runs.

All commands below were freshly executed in `/home/intern/part-time/aziz/pipeline` with prefix `env PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python`, using existing dependencies and real jsonschema 4.26.0. Exact command arrays, exit codes and durations are in `_review/plate_dependency_evidence/checks.json`.

| Python arguments | Fresh final result |
| --- | --- |
| `-m unittest vehicle.common.tests.test_plate_metadata -q` | **38 passed**, including 8 new regression methods |
| `-m unittest vehicle.common.tests.test_foundations vehicle.common.tests.test_db1_rows vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_redis_scalar vehicle.common.tests.test_real_video vehicle.common.tests.test_plate_metadata -q` | **180 passed**, includes the same 38 Plate tests |
| `-m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests -q` | **57 passed** |
| `-m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests -q` | **33 passed** |
| `-m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -q` | **24 passed** |
| `-m unittest vehicle.common.tests.test_db1_outbox vehicle.common.tests.test_db1_adapter -q` | **45 passed**, existing temporary legacy stores and injected drivers only |
| `contracts/db1/validate_fixtures.py` | **122 passed, 0 failed** |
| `contracts/validate_examples.py` | valid 18; schema rejects 24; application rejects 16; normalization 8; retries 3; strict JSON 4; vocabulary drift 3 |

No skips are counted as passes. The fresh-process import guard remains included. The legacy suites include fake-provider/error tests, never actual provider calls. No model, video, Redis or PostgreSQL execution was performed. Native Redis integration and prior real-video smokes were **not rerun**. Original milestone results above are historical for this follow-up.

Seven representative unaffected bundles (Colour v0; v1 whole read, standard regional read, invalid text, no detection, explicitly settled uncertainty, known OCR failure) were constructed in separate baseline/current Python processes from the same injected scalar fixture. **Every canonical byte and hash matched**, covering UUIDs, timestamps, facts, omissions and provenance. Full comparisons are retained in `unchanged-bundle-compatibility.json`. These are scalar compatibility checks, not model execution or new recognition evidence.

Static AST/whitespace checks pass. The follow-up patch is applied to a private extraction of the verified baseline; every source byte/hash must match the packaged snapshot. The extracted snapshot reruns the **180 boundary tests and 57 legacy Plate tests**, verifying packaged dependencies. These repeated checks are not extra unique coverage.

### Exact follow-up archive

Updated archive: `/home/intern/part-time/aziz/vehicle_plate_metadata_review.zip`.

The immediate verified baseline is the preceding Plate review ZIP, SHA256 `3f270d4c7d97aad9e9759b8af60f4745cecfa2a4de8d79c20e21f0d9f8a7249d`. `_review/plate_dependency_fixes.patch` is the exact **three-file** follow-up against that snapshot. The manifest records all source hashes, the immediate baseline and scoped attribution; the other 189 repository source/dependency snapshots are unchanged. Prior review manifests, patches, historical provenance and execution evidence remain under `_review/prior_plate_review/` and must not be attributed to this fix. Initial/current git status and fresh test/reproduction/compatibility logs are included.

The archive preserves all 192 repository-relative source/schema/test/document dependencies and excludes credentials, .env files, environments, models/video/crops, caches, databases and native binaries. No supervisor SQL or historical fixtures, unrelated working-tree edits, installations, transport code, models or providers were changed/executed; no commit or push was made. This remains a pure metadata correction. Plate Redis contracts/workers and distributed followups remain separate subsequent work.
