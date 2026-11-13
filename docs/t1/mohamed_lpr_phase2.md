# Mohamed LPR phase 2: scalar contract and in-memory orchestration

Implemented 2026-11-08. This opt-in review harness imports the unchanged Phase 1
policy. It uses injected scalar results; it has no model execution callback,
transport, capture, Django dependency, catalogue mapping or final DB1 record.
No existing T1 module imports this route. No runtime Tunis mode is selected.

## Starting state and preservation

The review archive is
`/home/intern/part-time/aziz/waycon-t1-delivery-20261108/waycon_vehicle_t1_final_review.zip`.
Its SHA256 is
`b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
ZIP CRC and all 270 manifested workspace source hashes pass, with **zero
differences** at the start. All three Phase 1 file hashes match their previous
verification. Existing dirty Git changes and untracked T1/Phase 1 files predate
this patch; they are recorded in `starting-git-status.txt`, not overwritten or
treated as this patch. Git HEAD is not the reviewed T1 baseline.

Mohamed's read-only checkout is clean at the pinned HEAD
`f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`. No source drift was found. Phase 1's
source attribution and its existing 1,031 scalar differential comparisons remain
in [mohamed_lpr_phase1.md](mohamed_lpr_phase1.md); these are earlier results,
not rerun or represented as new Phase 2 source comparisons.

Final verification checks all 270 reviewed files, all three Phase 1 files, all
other starting non-cache files under `vehicle/common`, `contracts`, and `docs/t1`, and the
source HEAD/status again. None changes. The v2 request/admission implementation,
its defaults, private-plate route and DB1 delivery/ACK behavior are preserved.

Only these five repository files are added:

| File | Responsibility |
|---|---|
| `vehicle/common/mohamed_lpr_contract.py` | Immutable scalar bindings, requests, results, provenance/index records, deterministic IDs and strict JSON codec; standard library plus Phase 1 only. |
| `contracts/vehicle_mohamed_lpr_v3.schema.json` | Closed Draft 2020-12 request/result shapes. This is a separate namespace, not a replacement for the current v2 schema. |
| `vehicle/common/mohamed_lpr_harness.py` | Plate-owned planning, Engine-owned admission/evidence/retirement ledger, crop recheck fence, immutable local seal; no executor. |
| `vehicle/common/tests/test_mohamed_lpr_harness.py` | 33 synthetic test methods, with category/mode/outcome/boundary matrices. |
| `docs/t1/mohamed_lpr_phase2.md` | This contract, behavior and verification report. |

Execution evidence is outside the checkout at
`/home/intern/part-time/aziz/mohamed-lpr-phase2-validation-20261108/`:
`baseline.json`, `starting-git-status.txt`, `commands.json`, per-command JSON/logs,
and `final-verification.json`. Logs contain synthetic tests, not captured readings.

## Versioned boundary

Envelope: `version="vehicle-mohamed-lpr-v3"`, `kind="request"|"result"`, `body`.
Requests additionally pin `policy_version="mohamed-lpr-plate-policy-phase1-v1"`.
The local seal is a Python review artifact, not a wire terminal or Aggregator
delivery event. There are no new Redis stream names, public exports or defaults.

| Record | Exact scalar fields and meaning |
|---|---|
| `Crop` | `org_id`, `run_id`, `camera_id`, `session_id`, `vehicle_lifetime_id`, `plate_track_id`, `plate_track_epoch`, `association_id`, `candidate_id`, `frame_id`, `frame_sha256`, `crop_id`, `crop_sha256`, `width`, `height`, `generation`. Exact frame/crop and vehicle/plate association claims, not a plate tracker ID reused as a vehicle ID. |
| `Request` | Version/policy, deterministic `operation_id`, per-generation `sequence`, exact `crop`, operation `role`, explicit `tunis_mode`, full Phase 1 `thresholds`, ordered immutable `dependencies`, and integer crop-local `region_xyxy`. No caller-chosen model path or backend. |
| `Dependency` | `operation_id` plus `result_sha256` of the complete retired result, including its request, crop generation, native payload, execution identity and actual provenance. All dependencies must already exist on the same crop/generation. |
| `Result` | Exact echoed `request`, `execution_id`, `executor_ref`, `outcome`, `retired=True`, optional actual `provenance`, `error_code`, and role-specific scalar payload. No result can substitute a different request, crop, mode, threshold setting, region or dependency. |
| `Provenance` | Actual `model_id`, `artifact_sha256`, `config_sha256`, `preprocessing_sha256`, `runtime_id`, `device`. Artifact hash identifies a retained manifest of all model export components; configuration hash identifies a retained effective manifest, including dictionaries and decoder options. These must describe what ran, not requested defaults. |
| OCR payload | `raw_text`, native `confidence` for `category_ocr`, `registration`, `left`, `right`. No normalized registration or display label is accepted as execution evidence. |
| Marker payload | `detected`, native `confidence`, optional crop-local integer xyxy `bbox`. Regional request rectangles use the original crop's full height and the marker's x boundaries, matching Phase 1. |
| DINO payload | Complete ordered tuple of `reference_id`, category (`idara/shorta/tunis`), cosine `similarity` in [-1,1], plus `ReferenceIndex(index_id,index_sha256,entries)`. Entries bind reference IDs and category assignments in exact scoring order. Score completeness/order/uniqueness is checked. No NT reference, category probability or similarity threshold. |

All role-inapplicable result fields must be null/empty. Successful execution
requires actual provenance and the correct payload. Failed/unavailable/unknown
execution has an error code and no successful payload; provenance may be absent
if no model was reached. A recorded successful empty OCR string is an observation
that the policy can reject, not a failed execution.

Before a result arrives, an admitted/requested operation contributes Phase 1
`pending` evidence. Terminal outcomes are `succeeded`, `failed`, `missing`
(execution unavailable), and `unknown` (recognition outcome unknown after known
retirement). Only Arabic category **failed** permits the source visual fallback.
Missing/unknown Arabic evidence cannot become a negative NT observation.
Missing results are never rescheduled in a loop. Actual still-running or
unretired native work must remain pending: it cannot be represented by a
`retired=True` unknown result merely to permit sealing.

Canonical UTF-8 JSON has sorted keys, no nonfinite values or duplicate keys, and
a 64 KiB envelope bound. All objects are closed. Python validation additionally
enforces byte limits, typed immutable records, finite native scores, deterministic
operation hashes, exact index contents, dependency hashes and geometry. JSON
Schema alone cannot enforce those cross-message/semantic rules. Digests identify
claims; they do not authenticate a producer or verify artifact bytes. The future
trusted Engine host must establish those facts from its own retained resources.

`operation_id` hashes the entire request except itself. Thus a crop/frame,
generation, policy, dependency-result or region change creates a new operation.
The result hash includes actual model/configuration/runtime/device/index identity.
Phase 1's opaque `model_ref` is the provenance-record hash; `index_ref` is the
complete reference-index-record hash. The full provenance remains available on
the Engine results rather than being collapsed into a model display name.

## Ownership and follow-up ordering

`PlateSession.plan()` consults **unchanged** Phase 1 `decide()` to find required
missing evidence. It returns only new requests. `EngineLedger.admit()` validates
the exact crop, order, capability role, policy binding, dependency topology and
dependency-result hashes; it performs no text interpretation. In-memory
admission is immediate and has no publication/ACK semantics. `Harness.inject()`
supplies already-retired scalar results to both ledgers. No method executes work.

| Operation | Plate condition | Required completed dependency chain |
|---|---|---|
| `category_ocr` | First operation | None. |
| `visual` | Arabic completed without explicit NT, or recorded Arabic failure | Exact category result; category failure is permitted for this source fallback. |
| `registration` | NT/Idara/Shorta number, or legacy Tunis whole reading | Category result and DINO result if DINO was required. |
| `marker` | Tunis only | Category chain; additionally registration result in `legacy_whole`. Registration failure stops before marker. |
| `left` and `right` | Reliable valid marker regions in `marker_first`; or the source's ambiguous-whole/reliable-marker regional condition in `legacy_whole` | Category chain + marker; additionally whole registration in legacy mode. Neither regional sibling depends on the other. |

Explicit NT always precedes DINO and skips it. Contradictory Arabic NT/category
markers still reject the number through Phase 1. Registration OCR cannot supply
missing category evidence. Category success alone never accepts digits.

Legacy Tunis orders category → optional DINO → whole registration → marker →
conditional regions. Marker-first orders category → optional DINO → marker →
regions, with **no whole-registration prerequisite**. Region requests use
`(0,0,marker.x1,height)` and `(marker.x2,0,width,height)`; only valid Phase 1
conditions cause those requests. Both siblings can be returned in either order.
Invalid marker evidence does not schedule regional reads. Full dependency
topology is checked without asking Engine to decide the category or parse text.

Both `Harness` and `PlateSession` require explicit `tunis_mode`. The string `"0"`
and other source environment truthiness values are rejected. Tests separately
exercise both modes. The source's original rules, thresholds, rounding, DINO tie
selection and category conflicts are preserved; no additional acceptance rule
is introduced. In particular, the source's Tunis four-decimal OCR rounding and
unrounded non-private boundary remain distinct. Formats are application rules.

The local decision is the unchanged Phase 1 `Decision`: observed raw registration
text by role, normalized registration (string digits, leading zeros preserved),
display label/text, category, acceptance/unresolved reason and evidence used.
Marker-first never fabricates a whole-crop OCR observation. Full Engine results
and `LocalSeal.request_results` retain supporting execution/provenance hashes.

## Replay, recheck and sealing

- Same canonical request replay returns `replay` without another admission;
  `plan()`/`advance()` never returns that operation for dispatch again. Same
  result replay leaves the decision and ledger unchanged. Deterministic replay
  in a fresh harness produces the same requests and seal for the same inputs.
- Request tampering fails its content-derived operation identity or exact
  admission binding. A conflicting result retry, including changed execution ID
  or provenance, blocks settlement and further harness scheduling. Original
  evidence is retained. There is no automatic model retry after a failure; retry
  here means message replay. A future genuine re-execution needs its own contract.
- Regional sibling results may arrive in either order. An unsolicited future
  operation or result with altered crop/dependencies is rejected. The harness
  does not buffer work that Plate has not requested or Engine has not admitted.
- Explicit `recheck(new_crop)` requires the same vehicle/track/association/session
  parent, a fresh crop ID and generation exactly +1. The trusted future crop
  selector must establish that it is better; this harness does not score pixels.
  Current Plate evidence resets and new operation identities are generated.
  Old-generation results are stale for Plate, but Engine retains every old
  admitted operation until its actual retirement. Rechecks are bounded at
  generation 64; one operation per role and at most six operations per generation.
- Seal requires the exact shared request/result set, no outstanding follow-up,
  no conflict and retirement of **all** admitted executions, including obsolete
  generations and a sibling no longer consumed after the other sibling fails.
  Premature sealing raises without closing the session. A complete failed,
  missing, invalid or unknown read may seal as a local **unresolved** decision;
  it cannot become an accepted registration or a DB1 delivery.
- A successful seal freezes the local decision. All later result injections are
  ignored with `sealed`; they neither mutate evidence nor schedule work. Sealed
  rechecks are rejected. This is deliberately an in-memory single-session test
  harness, without persistence, crash recovery, concurrency, authorization or
  native cancellation guarantees.

## Required concrete Engine bindings for the next patch

| Binding | What the Engine adapter must supply |
|---|---|
| Trusted parent and crop offer | Real T1 vehicle lifetime, plate tracker ID **and epoch**, established plate-to-vehicle association identity, session/candidate identity, paired frame receipt identity and digest, crop identity/digest/dimensions and generation. Retain the exact pixels/frame mapping; do not derive vehicle ownership from Mohamed's plate track alone. Define the byte representation hashed for pixels and the frame receipt resolver. |
| Arabic category OCR | The active Django Arabic OCR stage used by `PlateCategoryClassifier._classify_image`; return its raw output and native score or recorded failure. Keep classification/NT parsing in Plate. Fingerprint all actually loaded model components, character dictionary, preprocessing and effective config. |
| DINO reference scoring | Frozen approved reference index used by `ReferenceIndex.match`, actual DINO artifact/runtime/preprocessing and the entire ordered reference score vector. Engine must expose preselection scores, not only the source's winning category. Resolve and hash the actual eight-reference gallery/index and each reference's category assignment; the three-row synthetic index here is test data only. |
| Registration whole OCR | The active PP-OCRv6 execution used by `recognize_whole_number`/legacy Tunis reader, returning untouched raw text and native score. Bind the actual multi-file export, detector/recognizer components, dictionary, decoder settings and preprocessing. Phase 1 found exported files equal to official cache; this patch makes no fine-tuning provenance claim. |
| Tunis marker | The active marker localizer returning detection, native confidence and original-crop xyxy coordinates. Engine must preserve crop geometry and obey the exact request/generation; Plate retains reliability and follow-up decisions. |
| Regional OCR | Execute the requested left/right rectangles against retained original crop pixels; do not re-detect or silently resize coordinate space. Return independent raw readings/native scores and actual OCR provenance. Legacy whole/region agreement stays in Plate. |
| Execution lifetime | One stable admitted operation and execution identity, no execution on a request replay, explicit errors/unavailability, resource/crop retention and confirmed retirement before scalar handoff. Obsolete in-flight work must retire even when its recognition is discarded. |
| Host and mode binding | An explicitly reviewed Tunis mode and effective thresholds; no live default is chosen here. Bind actual artifacts/config manifests from an Engine-owned trusted resolver, never request-supplied paths. A later durable host must define admission/retirement recovery and seal delivery separately. |

Mohamed's capture loop, tracker, API, Django application, source writer and any
external fallback are not bindings for this harness. Aggregator retains canonical
delivery ownership; DB1 consumer retains PostgreSQL commit and subsequent Redis
acknowledgment ownership. Category vocabulary/catalogue and final record mappings
remain later work. This patch emits neither final DB1 records nor delivery events.

## Executed validation

All commands run from `/home/intern/part-time/aziz/pipeline` with
`PYTHONDONTWRITEBYTECODE=1`. Exact commands (also recorded as argv in
`commands.json`):

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_plate_policy -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_engine_plate vehicle.common.tests.test_plate_local_executors vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_db1_consumer
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/validate_examples.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
```

| Log | Result |
|---|---|
| `phase2-and-phase1.log` | 72 methods passed: 33 Phase 2 and 39 unchanged Phase 1, no skips. New schema metaschema and every operation/result round-trip checked within this suite. |
| `affected-t1.log` | 187 methods passed, no skips; existing pure/injected Plate, Engine, admission, review, publication-order and DB1-consumer regressions. |
| `legacy-plate.log` | 57 methods passed, no skips. Includes an intentional mocked provider timeout; no external call. |
| `event-contracts.log` | Schema valid; 18 valid, 24 schema rejects, 16 application rejects; normalization 8, retry 3, strict JSON 4, vocabulary drift 3 passed. |
| `db1-contracts.log` | 122 synthetic checks passed, zero failures; no database connection. |

The initial development log `new-tests-first.log` records one malformed test
fixture: its helper constructed a valid failure instead of the intended invalid
failure-with-success-payload. The test now mutates a successful result to check
that payload rejection. It is not counted as a passing run. Two additional stale
registration/conflict scheduling checks were added before the final 72-test run.
No native models, media, cameras, broker, database or real accuracy evaluation
were exercised. No commit or push was made.
