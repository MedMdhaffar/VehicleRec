# Whole-plate number recognition

Implemented from the user's application format requirements, 2026-10-30.
Uncommitted; no push or deployment. Earlier integration reports describe the
previous missing-policy behavior; this report supersedes only those capability statements.

## Inspection and scope

Read root `AGENTS.md`, the actual Django/standalone video and RTSP modules,
`LicensePlatePlugin/license_plate_plugin/manifest.py`, its reference
`assets/references/manifest.json`, and the prior reports
`license_plate_classifier_steps_1_2.md`, `license_plate_classifier_runtime.md`,
and `license_plate_local_completion.md` before implementation.

Visually inspected all 12 original reference crops in the contact sheet.
Idara references 1–3 show two groups and a dash, including `17 - 138957`.
NT references 4–7 show a registration and `ن ت`, including `214172`.
Shorta references 8–9 show `شرطة` and a registration, including `14330`.
Their manifest annotations agree with the requested templates; no contradiction
requiring clarification was found. Original images and annotations are unchanged.

The manifest supplies expected comparison values only, after inference returns.
The classifier already uses these reference images as its gallery, so these checks
cannot establish independent category or number accuracy. No outputs were patched
to match annotations, and no threshold was tuned against this set.

## Exact acceptance rules

All three policies classify the selected complete plate crop, then call the
existing registration reader exactly once on that same crop. Worker ownership,
load-once initialization, serialized inference, scheduling, crop selection,
recheck and conflict handling remain intact. There are no new regions, spatial
inferences, category-marker detectors, external APIs, VLMs, or training.

Unicode decimal digits become ASCII strings, preserving leading zeros.
Whitespace is collapsed; hyphen-minus and `‐‑‒–—−﹘﹣－` are normalized to `-`.
Unknown text is rejected, never converted into digits or arbitrarily discarded.

| Policy | Extraction | Validated fields | Accepted display |
|---|---|---|---|
| `idara_whole_plate` | Exactly two nonempty digit groups separated by a recognized dash; optional recognized Idara label | `first_group`, `second_group` | `group1 - group2` |
| `nt_whole_plate` | Exactly one contiguous digit group after accounting for optional `NT`, `N T`, `نت`, or `ن ت` | `registration_number` | `digits nt` |
| `shorta_whole_plate` | Exactly one contiguous digit group after accounting for optional `SHORTA`, `POLICE`, or `شرطة` | `registration_number` | `shorta digits` |

No fixed prefix or digit count is assumed. Idara never splits a continuous
number without a dash. Multiple whitespace/dash-separated groups in NT/Shorta
remain ambiguous, even when separated by a recognized label. Labels may occur
at either end; a label between numeric groups does not authorize merging them.
Optional Idara labels are `IDARA`, `إدارة`, `ادارة`. Latin labels are matched
case-insensitively with letter boundaries. Recognized Tunis labels `T`, `TUNIS`,
`تونس` are also checked for contradiction.

Acceptance requires all of:

1. Unambiguous extraction with all required fields present.
2. Native OCR score at least `WHOLE_PLATE_MIN_OCR_CONF` (default **0.90**).
3. No explicit contradictory category marker in registration OCR or the
   classifier's observed-marker evidence.

The threshold is an initial conservative application setting following the
existing private-plate convention, **not calibrated**. Non-finite/out-of-range
reader scores are rejected by the existing reader contract. Configuration must
be finite and between zero and one. Category predictions supply the literal
lowercase appended labels, never missing digits; `label_source=classification`
records this distinction. These three policies require no YOLO marker box.

Unresolved reasons include `empty_ocr`, `missing_registration_digits`,
`missing_idara_separator`, `ambiguous_digit_groups`, `unexpected_ocr_text`,
`contradictory_category_marker`, and `insufficient_ocr_confidence`. Native reader
exceptions produce `error/ocr_failure`; other processing exceptions remain
explicit errors. Rejected results have no public accepted fields or number.

## Integration and Tunis regression protection

`NUMBER_POLICIES` enables the three formats. The shared dispatcher branches
after whole-crop OCR and before all Tunis marker/region logic. Equivalent changes
exist in Django and standalone packages, including RTSP-compatible modules.
RTSP prototypes already delegate to the video dispatcher; direct RTSP dispatcher
parity is tested too.

The reader additionally retains its native unrounded score. New policies use it;
Tunis continues to use its existing rounded score. Its marker calls, conditional
region OCR, private parser, thresholds, and consistency checks are unchanged.
Structural checks confirm `needs_region_ocr`, `assemble_plate_detection`, and
`validate_tunis` are unchanged. Existing Tunis tests pass, including ambiguity,
marker/region consistency, failures, and rechecks.

Typed number evidence contains extracted candidates, normalized OCR, formatting
decisions, threshold, score, label provenance, checks, status, and reason.
Debug JSONL retains raw OCR and that evidence even when extraction is rejected.
Only accepted fields/display enter compact results; the existing frontend,
video annotations, RTSP results, and CSV exports consume the same formatted
number. Track identity remains category plus numeric string fields, preserving
leading-zero distinctions. No external recognition route was restored.

## Real OCR results and previews

All 12 crops were run through actual classification and worker-owned registration
OCR in each runtime root. All output texts, scores, fields, statuses, and reasons
agree between roots. The final runs deny Python socket connection calls and
record zero attempts; this is not an OS-wide network-isolation claim.

- [Per-reference table](../experiments/whole_plate_policies/django_final/REPORT.md)
- [CSV comparison](../experiments/whole_plate_policies/django_final/comparison.csv)
- [Full raw scores, fields, checks and timings](../experiments/whole_plate_policies/django_final/results.json)
- [Annotated contact sheet](../experiments/whole_plate_policies/django_final/contact_sheet.png)
- [Standalone comparison](../experiments/whole_plate_policies/standalone_final/REPORT.md)

Each run retains `original_1.png` through `original_12.png`, individually labeled
`preview_*.png`, persisted input crops, compact predictions, and debug records.
The labels are captions, not newly inferred spatial annotations.

**Observed outcomes:** 11 accepted, of which 10 match manifest registration
digits; 1 unresolved. Reference 6 reads `176828` correctly but scores
`0.8424720764160156`, below 0.90. Reference 7 reads `727750` at
`0.9514352679252625` and is accepted as `727750 nt`, disagreeing with expected
`72775`. This false acceptance is retained honestly. For the nine newly supported
crops: seven accepted matches, one accepted mismatch, one unresolved. All three
Tunis crops retain their prior recognized values.

## Checks and limitations

- **Mocked pipeline contracts:** 40 tests pass separately against each root.
  They cover templates, Arabic/ASCII digits, zeros, dash/space normalization,
  optional/contradictory labels, ambiguity, unknown letters, empty readings,
  native score boundaries, OCR exceptions, single whole-crop calls, zero marker
  calls, persistence, CSV exports, annotations, RTSP dispatch, identity conflicts,
  load-once serialization, queue/drain behavior and Tunis regressions.
- **Backend:** 18 tests pass across license-plate, colour, classification and
  shared API apps; Django system checks pass. The added API test uses real
  policy/persistence/compact projection with mocked inference and video adapter.
- **Plugin/import contracts:** 23 discovered, 21 pass, 2 opt-in inference tests
  skipped. Actual reference inference was run separately as described above.
- **Real video:** Django multipart upload returns HTTP 200 for 30 frames,
  producing H.264 output and recognized `179 تونس 8065`; standalone 30-frame
  run completes too. These smoke checks are not labeled accuracy evaluations
  and do not guarantee category coverage in that video.
- **Frontend:** ESLint and Vite production build pass using existing local Node
  tooling. No browser interaction or frontend source change was needed: accepted
  compact `plate_text` already renders the requested templates.
- **Structural:** implementation copies match; Tunis helper bodies match the
  pre-task snapshot; task patch reverses cleanly against this working state.

Logs are under `experiments/whole_plate_policies/checks/`. Independent accuracy,
calibrated confidence, GPU behavior, and real camera/RTSP streaming are unverified.
Only CPU inference was run. A high score can still describe the wrong digits;
format validation cannot correct that. Unknown categories/labels and unseen
layouts are not established by these reused development references.

## Rerun from repository root

Use the existing persistent runtime (or install the documented local runtime
and editable plugin first):

```bash
export REGISTRATION_OCR_MODEL_DIR=/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 CUDA_VISIBLE_DEVICES=''
export PYTHONDONTWRITEBYTECODE=1
export WHOLE_PLATE_MIN_OCR_CONF=0.90

.runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
PLATE_PIPELINE_FLAVOR=django .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/tests -v
bash scripts/run_local_plate.sh django test license_plate_service vehicle_color_service vehicle_classification_manufacturer_service api --noinput
PLATE_PLUGIN_IMPORT_AUDIT=1 .runtime/plates/venv/bin/python -B -m unittest discover -s services/license_plate_service/LicensePlatePlugin/tests -v

# Choose fresh output directory names to keep runs separate.
.runtime/plates/venv/bin/python -B experiments/whole_plate_policies/verify.py --output experiments/whole_plate_policies/rerun_django
.runtime/plates/venv/bin/python -B experiments/whole_plate_policies/verify.py --flavor standalone --output experiments/whole_plate_policies/rerun_standalone
bash scripts/run_local_plate.sh verify --mode django --frames 30 --output experiments/whole_plate_policies/rerun_upload
bash scripts/run_local_plate.sh verify --mode standalone --frames 30 --output experiments/whole_plate_policies/rerun_video

export PATH="$PWD/.runtime/tooling/node-v22.16.0-linux-x64/bin:$PATH"
npm --prefix frontend run lint
npm --prefix frontend run build
```

Test through the frontend using two terminals, both starting at the repository
root. Terminal 1 needs the model environment above:

```bash
bash scripts/run_local_plate.sh django runserver 8002
```

Terminal 2:

```bash
export PATH="$PWD/.runtime/tooling/node-v22.16.0-linux-x64/bin:$PATH"
VITE_API_URL=http://127.0.0.1:8002 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Open `http://127.0.0.1:5174`, upload footage, initially limit to 30 frames, and
inspect accepted/unresolved results. The current existing CORS configuration
allows port 5174. Restart Vite after changing API configuration. No live camera
was available; actual RTSP is not claimed as tested.

## Deliverable inventory and patch

See [the complete full-path inventory](whole_plate_number_inventory.md) for every
task-modified/added file and its purpose, including generated media and previews.
[The complete task-specific patch](whole_plate_number_policies.patch) compares
source files against snapshots captured before these edits, preserving unrelated
working-tree changes. It includes source, tests, documentation, reports, logs,
and generated evidence (binary Git patches for images/videos). Only the patch
itself is excluded to prevent recursion. The pre-existing model assets, reference
manifest, frontend changes, prior integration reports, and unrelated work are
not included as changes. No commit, push or deployment was performed.
