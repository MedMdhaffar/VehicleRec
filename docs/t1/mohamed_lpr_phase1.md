# Mohamed LPR phase 1: isolated scalar Plate policy

Implemented 2026-11-08 (Africa/Tunis). This is an application-rule port with
synthetic evidence, not an inference, accuracy, infrastructure or production test.
No existing T1 module imports it. There is no runtime mode selection, capture,
Django dependency, model loading, GPU dependency, Redis publication, DB1 write,
schema/catalogue change or model/image copying. No commit or push was made.

## Verified baselines and scope

Source: `/home/intern/part-time/mohamed/pipeline`, active Django tree
`vehicle/license_plate_service/run_video_pipeline/`, HEAD
`f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`. Source HEAD and clean status match
the audit. Relevant source Python hashes are recorded before/after; no drift.
The source checkout is read-only throughout this work.

Target review:
`/home/intern/part-time/aziz/waycon-t1-delivery-20261108/waycon_vehicle_t1_final_review.zip`,
SHA256 `b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
ZIP CRC, all 270 manifested source hashes, and their workspace counterparts were
verified. Initial differences: **none**. Existing dirty Git changes predate this
phase and are preserved; Git HEAD is not the T1 source baseline. All 270 reviewed
files remain byte-identical after this patch, including the existing private
Plate route, contracts, DB1 delivery, supervisor SQL archive and fixtures.

Only three repository files are added:

- `vehicle/common/mohamed_plate_policy.py`: standard-library-only immutable
  evidence records and pure `decide` function.
- `vehicle/common/tests/test_mohamed_plate_policy.py`: 39 synthetic test methods,
  including a fresh standard-library-only import/execution guard.
- `docs/t1/mohamed_lpr_phase1.md`: this source mapping, interface and validation report.

Execution evidence is outside the checkout at
`/home/intern/part-time/aziz/mohamed-lpr-phase1-validation-20261108/`.
`baseline.json` and `starting-git-status.txt` record the starting state;
`commands.json` and the named logs record validation; `final-verification.json`
binds final file hashes and preservation checks. No secrets or native readings
are collected. Tests use invented strings only.

## Rule attribution

All source paths in this table are below the source Django `run_video_pipeline/`.
The port's helper docstrings also identify the source functions.

| Rule | Source function and location | Port behavior |
|---|---|---|
| Decimal digits / comparison normalization | `ocr.py:18 normalize_digits`, `ocr.py:22 normalize_plate_text` | Decimal conversion preserves leading zeros; comparison uses source NFKC/whitespace/uppercase normalization. Raw evidence is retained separately. |
| Arabic category markers | `license_plate_plugin/parser.py:22 parse_nt_evidence` | Source Arabic boundaries, NFKC, diacritic/kashida removal; same limited conflict set (Shorta/Tunis, not an invented Idara marker rule). |
| NT precedence / local visual fallback | `license_plate_plugin/classifier.py:101 PlateCategoryClassifier._classify_image` | Explicit NT evidence wins before visual scoring. Its contradictory category markers still reject the number. A recorded Arabic OCR failure permits the source's visual fallback to three non-NT classes. |
| DINO winner / ties / margin | `license_plate_plugin/reference_index.py:177 ReferenceIndex.match` | Maximum reference cosine, source 1e-12 tie tolerance and numeric/lexical ID ordering; no similarity threshold or probability conversion. Negative similarities and ties remain observable, not automatically rejected. |
| Idara | `whole_plate_numbers.py:26 recognize_whole_number` | Two nonempty digit groups separated by an observed dash; normalize source dash variants. No invented field-length restriction. |
| NT / Shorta numbers | `whole_plate_numbers.py:26 recognize_whole_number` | One contiguous digit group after optional category-label removal. Ambiguous groups, other text and category contradictions reject. Leading zeros remain strings. |
| Private parsing | `private_plate_parser.py:59 parse_private_plate` | Explicit T/Arabic marker fixes the field boundary; otherwise enumerate 1–3 / 1–4 digit splits and require a unique split. Invalid explicit structures do not fall back to digits-only guessing. |
| Legacy Tunis | `pipeline.py:63 needs_region_ocr`, `pipeline.py:73 assemble_plate_detection`, `policies.py:30 validate_tunis` | Whole OCR first. A reliable marker and ambiguous whole result require matching confident regional digits; source reason ordering and conflict checks remain. |
| Marker-first Tunis | `pipeline.py:136 _analyze_tunis_sides`, `policies.py:30 validate_tunis` | Reliable marker and valid crop-local region bounds first, then both digit regions. No whole-crop registration OCR is assumed or fabricated. |
| Thresholds / native score handling | `config.py:24–37,43`, `ocr.py:93–95`, `pipeline.py:230–234` | Defaults .90 whole/private OCR, .50 marker reliability/private marker/regional OCR. Non-private acceptance uses native unrounded scores. Tunis retains the source's four-decimal reader rounding. |

These formats and thresholds are **application rules**. They are not legal format
definitions, independently calibrated confidence thresholds, or recognition
accuracy claims. There is no public-category or database-key mapping: results
retain `tunis`, `idara`, `nt`, `shorta` only.

## Local interface

Import the module explicitly; it is not exported through or attached to the live
Plate worker. Invoke:

```python
decision = decide(evidence, tunis_mode="legacy_whole", thresholds=Thresholds())
# Or explicitly choose "marker_first" for a separate local evaluation.
```

`tunis_mode` has **no default**. Omitting it raises TypeError; values such as
`"0"`, `"1"`, `""`, booleans and automatic mode are rejected. The source's
environment string `"0"` is truthy and selects the legacy branch; that ambiguity
is not copied. Tests explicitly exercise both modes. This patch selects neither
mode for integration or runtime use.

Inputs are immutable scalar records:

| Record | Fields / interpretation |
|---|---|
| `OCR` | `evidence_id`, `state`, `raw_text`, native `confidence`, opaque `model_ref`. Used independently for category OCR, registration whole OCR, left OCR and right OCR. |
| `ReferenceScore` | `reference_id`, source category (`tunis/idara/shorta`), raw cosine `similarity` in [-1,1]. |
| `DinoScores` | `evidence_id`, `state`, immutable reference-score tuple, `model_ref`, `index_ref`. No feature extraction or image access. |
| `Marker` | `evidence_id`, `state`, observed `detected`, confidence, optional crop-local integer xyxy `bbox`, `model_ref`. |
| `Evidence` | Optional role slots `category_ocr`, `visual`, `registration`, `marker`, `left`, `right`; optional original `(width,height)` `crop_size`. Describes one caller-grouped crop. |
| `Thresholds` | Explicit source application thresholds: `whole_ocr`, `private_ocr`, `marker_reliable`, `private_marker`, `regional_ocr`. No DINO confidence threshold exists. |

States are `succeeded`, `missing`, `not_started`, `pending`, `failed`, `unknown`.
None in a role slot means missing. Successful execution must carry valid scalar
payload and a model reference; unsuccessful execution cannot masquerade as an
empty successful reading. Validation errors use bounded messages without echoing
input text. Scores must be finite, not booleans. Local resource bounds are 4096
UTF-8 OCR bytes, 64 reference scores, 256-byte identities and bounded positive
crop dimensions; these are input bounds, not new recognition thresholds.

The result contains:

- Source category, decision source, raw visual similarity/margin/tie/reference ID
  where visual scoring was used, and category conflicts.
- `status=accepted|unresolved`, a source-compatible `reason` where applicable and
  a separate `reason_kind`: missing/pending evidence, inference failure, unknown
  outcome, invalid format, conflict, low confidence or insufficient visual evidence.
- `observed_text`: exact registration OCR text by role, not a fabricated combined
  whole reading. Category OCR text remains in its own evidence record.
- Accepted `registration_fields` as immutable string pairs. Rejected readings do
  not populate accepted registration/display fields.
- `normalized_registration`: digits only for NT/Shorta, two groups separated by
  ` - ` for Idara, `series + space + registration_number` for Tunis. This is a
  **local representation**, not a replacement for canonical T1 `plate_text`.
- Separate `display_label` and source-compatible `display_text`. NT/Shorta labels
  may derive from category classification and are never inserted into observed
  OCR or normalized registration. The Tunis marker/display label is also separate.
- Exact immutable `evidence_used`, explicit mode, threshold settings, crop size,
  boolean checks, warning codes and local policy version.

Only evidence actually consumed by a branch appears in `evidence_used`: NT does
not consume DINO scores, marker-first does not consume whole registration OCR,
and an unambiguous legacy read does not consume unsolicited regional results.
The function does not log the returned raw evidence. A future host must redact
readings before diagnostics; model/index references here are opaque supplied
identities, not proof of artifact verification or authentication.

## Source parity and intentional differences

The external `source_parity.py` tool verifies the pinned source HEAD, reads selected
source ASTs and executes their decision functions with scalar/shape-only doubles.
It does not import source packages, initialize native models or read images. It
uses the actual source category decision body, reference-selection tail, number
rules, parser, assembly and Tunis validators. The synthetic matrix compares
category/source, nearest reference and tie/margin, acceptance/reason, accepted
fields, display text and registration roles actually read.

**1,031 comparisons pass**: 13 category-marker cases, four visual-selection
cases, 243 whole-number decisions, 387 legacy Tunis decisions and 384 marker-first
decisions. Both source and port retain the rounded Tunis boundary: a native score
just below .90 may round to .90 for Tunis, while non-private policies compare the
unrounded native score. This is source parity, not a new accuracy claim.

Intentional local-interface differences:

1. Display labels are separate from observed/normalized registration, as requested.
   Source `normalized_number` is checked against `display_text`, not silently
   substituted into the new registration field or T1 delivery.
2. Missing, pending, not-started and unknown category OCR do not fabricate negative
   NT evidence or use the source's optional disabled-OCR mode. They remain unresolved.
   An actual recorded Arabic OCR **failure** retains the active source's visual
   fallback, with `category_ocr_failed_visual_fallback` retained even if the final
   number is accepted. No NT category is inferred from failed OCR or registration OCR.
3. Missing required marker/regional executions remain explicit missing evidence;
   an observed no-marker result is different. Completed empty OCR is an invalid
   observation, not a missing or failed execution. Errors return local unresolved
   reasons instead of source Pydantic/application error envelopes.
4. Explicit mode, scalar geometry, immutable evidence IDs, model/index references,
   input validation and resource bounds are local boundary requirements. They
   introduce neither a category threshold nor a catalogue mapping.

The source's number-rule reason ordering, NT precedence, marker-conflict handling,
local failed-Arabic-OCR fallback, digit boundaries and acceptance thresholds are
otherwise preserved for valid fully supplied evidence. The old T1 policy is not
changed to adopt any of these rules.

## Executed validation

All commands run from `/home/intern/part-time/aziz/pipeline` with
`PYTHONDONTWRITEBYTECODE=1` and `/home/intern/.venv/bin/python`:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_plate_policy -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase1-validation-20261108/source_parity.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_engine_plate vehicle.common.tests.test_plate_local_executors vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_db1_consumer
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/validate_examples.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
```

| Evidence log | Result |
|---|---|
| `new-tests.log` | 39 methods passed, no skips |
| `source-parity.log` | 1,031 source comparisons passed |
| `affected-t1.log` | 187 methods passed, no skips; existing pure Plate/Engine/request/DB1-consumer regressions |
| `legacy-plate.log` | 57 methods passed, no skips; includes an intentional mocked provider timeout, no external call |
| `event-contracts.log` | Schemas valid; 18 valid, 24 schema rejects, 16 application rejects; normalization 8, retry 3, strict JSON 4, vocabulary drift 3 passed |
| `db1-contracts.log` | 122 synthetic DB1 checks passed, zero failures; no database connection |

The regression suites use existing injected executors/transports. No owned Redis,
native inference or PostgreSQL suite was run for this isolated patch. Source
comparisons and regression repeats overlap behavior and are not added to a claim
of unique test-method coverage. Initial development tests mistakenly treated a
seven-digit private reading as ambiguous; source rules permit its unique 3/4 split.
The fixture now uses a genuinely ambiguous six-digit case. The initial failure
log is retained separately and is not counted as a pass.

## Next Engine-to-Plate contract needs (not implemented)

The records above are **local Python inputs**, not a new Redis/schema contract.
Before wiring, the next contract must define:

- Trusted org/run/camera and vehicle lifetime binding; raw plate tracker epoch,
  parent/segment/candidate identity; admitted crop ID/hash/size and original paired
  frame receipt. The local function trusts caller grouping and does not authenticate it.
- Distinct category-Arabic-OCR, DINO-reference-score, registration-whole-OCR,
  marker and left/right OCR evidence. Category success cannot itself supply digits.
- Per-execution request/operation ID, sequence, exact dependency IDs/generations,
  execution state, actual model artifact/runtime/device identity and retirement.
  Marker-first must not invent a whole-OCR prerequisite; legacy mode still needs it.
- Bounded complete reference-score sets with reference IDs/categories, frozen
  index/gallery/preprocessing/version fingerprints and score/tie semantics.
  Scores remain cosines, never category probabilities.
- A versioned explicit Tunis mode and application threshold/policy record, with
  no environment-string truthiness. Version normalization and separate observed,
  registration-field and display representations; do not redefine current T1 text.
- Missing/failure/unknown/pending/completed evidence, expected operation sets and
  admission/generation sealing, so unused or unretired work cannot imply completion.
- Terminal scalar category/number decision and supporting evidence IDs. Aggregator
  remains canonical-delivery owner; DB1 consumer remains sole persistence/ACK owner.

Those contracts, runtime bindings, category vocabulary/catalogue decisions and
real-model MP4 validation belong to later phases. The current T1 review remains
the preserved baseline rather than being silently reinterpreted by this module.
