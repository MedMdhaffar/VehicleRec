# Controlled shared real-video smoke — 2026-10-09

The corrected shared classification/colour/plate path processed **150 real frames**
and delivered **five vehicle lifetimes**, using existing local models. No mocked
inference was substituted. Final run02 passed in **82.308 seconds**
(parent wall time; child 81.259 seconds), with zero pending state
and no outer timeout. This demonstrates this file/configuration's integration and
provenance behavior; it does not validate detection, association, transcription,
colour accuracy or physical absence of plates.

## Invocation and local scope

From `/home/intern/part-time/aziz/pipeline`:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/db1_shared_smoke.py \
  --device cpu --max-frames 150 --timeout 300 --drain-timeout 20 \
  --output local_output/db1_shared_smoke/run02
```

Initial run01 used the same command with output run01. It completed in 94.087
seconds, but subsequent evidence review exposed the omission-reason defect below.
Run02 is the corrected confirmation run; no recognition thresholds were tuned.
Existing output directories are never overwritten. Choose a new directory beneath
`local_output/db1_shared_smoke/` for a later invocation.

Reusable host: `scripts/db1_shared_smoke.py`. The parent starts one child in its own
process group, enforces **300 seconds**, and uses SIGKILL only on that group if the
limit is exceeded. It also cleans the group after child exit. Running OCR threads
cannot defeat the parent timeout. That timeout branch was not triggered in these
successful runs; no hard-kill recovery/durable delivery is claimed.

The child uses classification process_video's single real OpenCV capture and
vehicle detector/ByteTrack owner, SharedColourPipeline, SharedPlatePipeline and
the existing ObservationCoordinator. Delegating counters call actual decoder and
models. A capture/inference spy asserts one decode/receipt/vehicle inference per
processed frame; it does not return mock results. The association callback copies
frozen metadata and checks candidates/selected service IDs against the actual
same-frame VehicleFrame without recalculating geometry.

Combined completion callbacks validate provenance, write redacted metadata, then
regularly release legacy completed profile history. Finally the host calls
coordinator.finish, flush_closures, plate.shutdown and poll, and closes the OCR
engine after work drains. It records pending counts before exit. Scoped outputs:

- `run02/preflight.json`: exact local artifact paths/fingerprints;
- `run02/summary.json`: limits, environment, effective settings and measured totals;
- `run02/completed.jsonl`: immutable-result metadata, original attempt/parent IDs,
  receipts and crop fingerprints, without text or retained crop bytes;
- `run02/associations.jsonl`: same-frame status/candidate scores/receipt records;
- `run02/verification.json`: post-run checks against emitted metadata and current
  artifact fingerprints;
- `run02/supervisor.json`, `host_snapshot.py`, `progress.json`, `worker.log`:
  supervision/source snapshot and scoped initialization/runtime logs.

`run01/` preserves the initial results/source snapshots for the fix comparison.
`preflight/frame0.jpg` is the sole retained new image, used to confirm the local
clip contains vehicles and plates. No new video, crop-review store, raw OCR result
JSON, provider output or raw plate text is retained in summaries. Credentials are
not printed; inherited credential variables are removed from the child environment.

## Preflight and artifacts

Input is the existing plate test_video.mp4: **271 frames, 1920×1080, 25 fps**.
The first 150 frames were used. Its overlaid source clock is not treated as UTC;
file evidence uses host_replay_receipt, with original decoder PTS separate.

All required assets existed, including the cached PP-OCRv6_medium_rec graph,
parameters and inference configuration. Model paths are explicit and local. No
models were downloaded or dependencies installed; no mock fallback or alternative
artifact was used. Fingerprints below are SHA-256:

| Role | Exact path | Bytes | SHA-256 |
|---|---|---:|---|
| `marker_model` | `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/tounes_word_detector.pt` | 5371461 | `f8956bd78992661bf5a88905f355b48168c0e5d643c3f7635795cfc266d11293` |
| `ocr/inference.json` | `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.json` | 221814 | `0b2e25e990bd072f1bf77d59d67d508bce6c4bd44af6624e0fb27d6da2cd00e8` |
| `ocr/inference.pdiparams` | `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.pdiparams` | 76465087 | `1b01c79a914587933f615569e75de54f2e638ebb5d3f3b3c1b38c24ede8c7319` |
| `ocr/inference.yml` | `/home/intern/.paddlex/official_models/PP-OCRv6_medium_rec/inference.yml` | 150580 | `991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129` |
| `plate_model` | `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/models/best.pt` | 20328389 | `d7a69b324a58e24223a48371f5a70a5e222357d0edaa47dd435bca7cc8c831a0` |
| `plate_tracker` | `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/trackers/bytetrack_plates.yaml` | 439 | `2cf1cb6791ed4b60eb380ee4ac589d990bc816e2af8025488f33cadbc4020ea3` |
| `vehicle_model` | `/home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/models/vehicle_yolo26.pt` | 44056921 | `f0574db3428cf12af8c07c40aaa6e042f9cf830f1b22d2ee9ea4dcd370acfc29` |
| `vehicle_tracker` | `/home/intern/part-time/aziz/pipeline/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml` | 147 | `0629767f781ccae6a81f23c6df04df325912f6d31fd28bc226b5e4f82d41103d` |
| `video` | `/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/test_video.mp4` | 2381732 | `36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2` |

Local recognizer bundle fingerprint, covering those OCR files, marker model and
recognition/routing/parser/config source fingerprints:
`4a3acd06730eeca04883e217112dd998c4234efe179310038035cfc88c800e81`. Source component hashes are in preflight.json.
The executed host snapshot SHA-256 is `7443cf18ad2f73fe692541997f97270504b2bed9962d9e5091569d90717b6260`.

VM Python 3.11.15; torch 2.11.0+cu128; ultralytics 8.4.46; paddleocr 3.7.0;
paddlex 3.7.1; numpy 2.3.5; imported cv2 5.0.0. Installed metadata includes
paddlepaddle 3.3.1 and paddlepaddle-gpu 3.3.0; the installed paddle version module
identifies 3.3.1. CUDA is available with one device, but **CPU** was explicitly
selected for vehicle, plate, marker and Paddle OCR. No global device/configuration
was changed. The tracker frame-rate setting was explicitly 25.

Effective settings, recorded in run02 rather than inferred from development
catalogues: vehicle conf 0.10/imgsz 640/loss 15 frames; plate conf 0.25/imgsz 960;
marker imgsz 512/inference conf 0.05. Colour remains multi ROI, no normalization,
adaptive neutral mode, HSV features, max 20 valid samples. Plate policies remain
strong quality 12000/min 3 candidates, stable min 12 candidates/5 observations,
exit min 8 candidates, disappearance 5 frames, recheck improvements 1.50/1.25,
replacement confidence gain 0.02. No accuracy tuning occurred.

## Explicit development context and limits

Run ID: `development-smoke-c2ffe49dea9e4570ba87abad5bc76ddf`. Camera identity explicitly names the local input fingerprint,
with supplied Africa/Tunis timezone and a run-local validity start. Org/run identity
is freshly scoped development metadata; it is not platform registration. Detector
and recognizer names embed actual artifact/bundle fingerprints with development
version 1; these are **unprovisioned development identities**, not catalogue keys
or platform-provisioned model registrations.

The foundation previously required numeric database resolution for every
context, preventing this unprovisioned development smoke without invented keys. It now has an explicit development environment where camera/model/run/
lane/source-media database keys are all None. Production/test contexts still
require resolved positive keys; development rejects numeric resolution claims.
`database_keys_resolved` is false for this smoke. No synthetic numeric database
key was supplied, and native development observations are not DB1-ready rows.

Limits: 150 frames; hard outer 300 seconds; explicit **20-second per-lifetime OCR
closure drain**, replacing the shared consumer's default one second only in this
host. The drain is finite and below the outer limit; the outer deadline bounds
aggregate run/closure time. Clock divergence tolerance remains fixed at five
seconds. Pending plate capacity is eight operations, working segments 128,
per-vehicle segments 16, staged finals 128, crop max 8 MiB/result max 64 KiB,
and two operations per segment. No accepted evidence is silently evicted.

All optional providers are explicitly disabled: OpenRouterPlateAgent(enabled=False)
and SharedColourPipeline(agent_enabled=False), independent of environment keys.
Child-only offline flags and a Python TCP/IP connect guard prevent accidental
model/network retrieval; YOLO autoinstall is disabled and its config directory is
scoped under the run. **Three outgoing connect attempts were blocked** in each
run. Their library origins were not logged, so they are not attributed to a
specific dependency. No provider route executed, colour agent attempts were zero,
and all actual models loaded from the listed local files. Native-library network
sandboxing is not claimed beyond these child controls.

## Observed results and evidence checks

| Measurement | Corrected run02 |
|---|---:|
| Successfully decoded / processed / receipt records | 150 / 150 / 150 |
| Decoder opens / releases | 1 / 1 |
| Vehicle process / model predict calls | 150 / 150 |
| Plate detector calls | 150 |
| Completed vehicle lifetimes | 5 |
| Same-frame plate associations | 174 associated, 9 unassociated, 0 ambiguous |
| Crop recognition submissions | 2 |
| Completed operations / executed local route | 2 / 2 |
| Low-level OCR calls / marker predict calls | 4 / 2 |
| Submission, worker or local OCR errors | 0 |
| Cancelled operations / settlement timeouts | 0 / 0 |
| Plate result states | 2 admitted reads, 3 omissions |
| Closures | 3 track_loss, 2 service_stop |
| Run end | service_stop, trigger frame_limit |
| Quarantined lifetimes / clock faults | 0 / 0 |
| Cleanup errors / pending work or result snapshots | 0 / 0 |

The two crop operations account for four whole/region OCR calls; split calls do
not increase operation counts. Algorithmic read admission is not a transcription
accuracy assessment. The three zero-operation omissions are explicitly different:

- Service ID 5: zero observed plate detections, no accepted crop, no operation;
  reason no_detection, not proof of a physical missing plate.
- Service ID 2: no accepted associated crop, unassociated same-frame detections;
  reason unassociated_crop, not OCR failure.
- Service ID 3: one accepted associated crop but no submitted operation because
  the unchanged candidate policy did not trigger; reason failure_before_model.
  This is neither an OCR exception nor unsettled asynchronous work.

Global association records count each tracked detection once. Unassociated
per-vehicle diagnostics are copied to vehicles present on that frame, so those
counts must not be summed or interpreted as physical crop-parent claims.

Supporting receipts (full monotonic/PTS values and attempt IDs are in completed.jsonl):

| Service / raw vehicle ID | Closure | Plate state / omission | Operations | Selected plate frame / receipt UTC | Colour state | Colour support frame / receipt UTC |
|---|---|---|---:|---|---|---|
| 1 / 1 | service_stop | read / — | 1 | 2 / 2026-10-09T01:16:32.860776+00:00 | decided | 4 / 2026-10-09T01:16:33.745646+00:00 |
| 2 / 2 | service_stop | omitted / unassociated_crop | 0 | — | decided | 4 / 2026-10-09T01:16:33.745646+00:00 |
| 3 / 4 | track_loss | omitted / failure_before_model | 0 | — | undecided | 33 / 2026-10-09T01:16:46.195171+00:00 |
| 4 / 6 | track_loss | read / — | 1 | 62 / 2026-10-09T01:17:01.048203+00:00 | decided | 39 / 2026-10-09T01:16:49.123424+00:00 |
| 5 / 10 | track_loss | omitted / no_detection | 0 | — | decided | 79 / 2026-10-09T01:17:10.302812+00:00 |

The host verified exact run/service/raw/epoch/lifetime identity equality across
classification, colour and plate; equal closure/quarantine; and unique finalization.
For every selected plate it located exactly one original attempt and checked
parent/segment, crop object, crop receipt, score and actually recorded route/model
all belong to that attempt. Receipts fall within the parent's contributing frame
interval. Agent routes cannot enter this smoke. Decided local colour support is
the last retained valid contributing sample. Undecided colour retains its defined
attempt receipt. Old evidence is not modified by later owner sightings.

All post-run checks in verification.json passed, including artifact fingerprints
still matching. Every recorded owner/consumer/combined queue and active-state
count after cleanup is zero; no smoke child remained in the process listing.

## Concrete fixes and contract consistency

1. `vehicle/common/db1_context.py`: optional nested resolution keys plus an explicit
   unresolved development RunContext. This removes the need to invent database
   keys for the requested local smoke, while retaining production/test checks.
   Foundation regression covers development/production/test separation and
   rejects partially populated numeric development keys.
2. `run_video_pipeline/ocr.py`: optional explicit local model directory/device,
   required local-file validation and unchanged default constructor behavior.
   Plate detector and marker localizer have optional device forwarding; defaults
   remain unchanged. Tests check constructor options and absence of implicit
   device changes in standalone mode.
3. Shared colour has an explicit per-instance agent_enabled=False boundary,
   overriding credentials and even injected agents. It changes no colour formula
   or standalone algorithm; its regression forces ambient agent settings enabled
   and verifies no call/crop/attempt.
4. Shared plate exposes frozen same-frame association audit records; no extra
   detection, tracker or geometry computation is introduced.
5. Run01's service ID 3 had one accepted crop, zero submitted operations and one
   unassociated interruption. Finalization gave association diagnostics precedence
   and mislabelled it unassociated_crop. Finalization now applies ambiguous/
   unassociated omission reasons only when no usable associated crop was admitted.
   With an admitted but unscheduled crop, the existing P1 omission helper yields
   failure_before_model. The zero-operation ledger remains truthful. A focused
   regression reproduces accepted crop + interruption + unchanged high candidate
   threshold; run02 confirms the corrected reason on actual input.

P1 diagnostics previously rejected `unsettled_operation` and had no representation
for shared `settlement_incomplete`. The smallest semantic extension adds optional
settlement {incomplete, attempts, unresolved_attempts} and the plate-only omission
reason unsettled_operation. It requires positive submitted/unresolved counts, no
selected attempt, no row UID/payload, and incomplete work cannot use
failure_before_model or a row reason. Native settlement_incomplete maps to this
explicit diagnostic admission, not unreadable. Four rejection fixtures and two
valid cases cover unresolved omission and the candidate-policy zero-operation
case. The delivery-row contract, no-crop omission policy and supervisor SQL are
unchanged. Neither real run observed unsettled work; cancellation/late-future
behavior remains covered by the injected regression suite.

Changed/new files in this smoke scope: scripts/db1_shared_smoke.py; common context
and foundation tests; plate OCR, detector, marker, shared consumer/evidence and
focused tests; shared colour and its focused tests; P1 diagnostic schema, valid/
invalid diagnostic fixtures and README; common README; this report. Earlier
classification integration changes remain in the workspace but were not edited
for this smoke. No unrelated CLI dispatch repair was made.

## Executed checks and remaining readiness

Using the existing VM interpreter, without installs:

- P1 validator: **120 passed, 0 failed** (after diagnostic updates).
- Foundation tests: **19 passed** (context validation affected).
- Classification integration tests: **24 passed** (context compatibility affected).
- Shared colour tests: **31 passed** (explicit provider-disable boundary affected).
- Focused plate tests: **57 passed**, including device/local-artifact options,
  immutable audit callback and the observed omission regression.
- Two real smoke subprocesses completed inside the five-minute limit; corrected
  run02 emitted the expected reason and passed provenance/post-run checks.
- Source parsing, git diff --check and supervisor archive SHA-256 comparison to
  the pre-smoke baseline passed. Input/model fingerprints remained unchanged.

Commands for affected checks:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
```

No missing artifact/dependency blocker remains for this CPU file smoke. Accuracy,
other videos/devices, actual clock faults/ambiguous associations/OCR failures,
real settlement timeouts and the supervisor kill branch were not exercised by
these real runs. Those injected cases are not presented as real-video outcomes.
RTSP reconnect, provider execution, persisted recovery, DB-dependent Django tests,
SQL rows, databases, Redis, uploads, training and T2 were not exercised or added.

The shared path has evidence suitable for beginning a pure row-construction
implementation, with the observed omission defect fixed and unresolved-operation
admission explicit. **This development run cannot produce DB1-ready rows**:
platform-provisioned org/camera/version/run/lane/media/model registrations are
absent and every numeric resolution key is None. Row construction must resolve
those identities, honor quarantine/settlement and no-crop omissions, allocate
row/retry identities and validate SQL/delivery semantics. Native run-scoped attempt
IDs and zero-based tracker epochs are not directly the P1 diagnostic UUID/epoch
envelope; that future boundary needs an explicit mapping. Media persistence and
actual database insertion remain outside this step. No freeze, commit or push.
