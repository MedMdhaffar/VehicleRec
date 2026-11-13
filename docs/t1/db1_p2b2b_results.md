# Shared plate evidence — P2b2b, 2026-10-09

Plate is now an optional consumer of the existing classification/colour
ObservationCoordinator for file runs and externally decoded file frames. One
classification owner supplies the image, receipt, same-frame vehicle boxes and
service lifetime identity. Plate retains its existing plate detector/BYTETracker,
crop scoring, local OCR/parser/router and optional provider logic. It constructs
no capture or vehicle detector/tracker. Standalone Django, video review and RTSP
entry points remain on their existing paths.

## Changed files

- `vehicle/license_plate_service/run_video_pipeline/shared.py`: shared consumer,
  parent segmentation, bounded asynchronous operations, owner-only result
  application, deterministic selection and claim/finalize/release protocol;
  `PrototypeRecognizer` adapts the existing crop pipeline.
- `vehicle/license_plate_service/run_video_pipeline/shared_evidence.py`: frozen
  explicitly supplied recognizer registrations, execution/outcome, attempt,
  selected supporting attempt and final lifetime evidence. No SQL row schema.
- `vehicle/common/observation_evidence.py`: reusable immutable scalar parent
  identity and owned image bytes. Colour `evidence.py` reexports these as its
  existing VehicleIdentity and OwnedColourCrop names; its inference/state machine
  is unchanged.
- Plate `prototype_pipeline.py`: optional receipt, execution recorder and
  `persist_review=False` boundary. Defaults retain completion timestamp and
  standalone review persistence. Shared results use the supporting crop receipt
  and do not write review artifacts. All OCR/parser/routing algorithms and their
  thresholds remain unchanged.
- Plate `agent.py`: strict boolean `text_complete` schema/prompt claim, default
  false for compatible parsing of old responses; optional execution hook at
  provider dispatch, after enabled/key/JPEG checks. Standalone accepted/rejected
  behavior remains unchanged. Shared completeness evidence is added only when
  the shared recorder is supplied.
- `run_video_pipeline/tests/test_shared_plate.py`: 53 injected tests.
- `vehicle/common/README.md` and this report.

The existing coordinator protocol is reused without modification. No standalone
video runner, OCR/parser/router, classification aggregation, colour algorithm,
RTSP loop, supervisor contract archive or SQL file was changed in this step.

## Association and segmentation

Only current VehicleFrame vehicles are association candidates. The dependency-free
P1 geometry rule is called through `vehicle/common/plate_association.py`; raw plate
and raw vehicle tracker ID equality plays no role. The existing deduplication,
TrackerDetections adapter, matching back to original detection and crop helpers
are reused. Ambiguous/unassociated detections remain diagnostics and never create
recognition operations. Ambiguous counts belong to candidate service vehicles;
unassociated counts are frame-level diagnostics copied to vehicles present on
that frame, not an assertion that the crop belongs to any one vehicle.

Each accepted plate parent change seals the previous segment and starts a fresh
TrackState with a run-unique segment ID. Previous operations remain with their
original service vehicle. An ambiguity/unassociated interruption also separates
later evidence. Plate disappearance uses the existing configured frame-gap policy
(default five frames); reuse after that gap gets a fresh segment even under the
same service parent. Vehicle loss/reset creates a new service parent independently.
Reuse without a loss/gap/reset signal cannot be distinguished from a repeated
sighting of the same plate by this tracker API.

VehicleIdentity copies run/service/raw/epoch/lifetime scalars at admission. Each
operation owns immutable bytes, shape/dtype/bbox and the exact FrameReceipt of its
supporting best crop. Recognition receives a new writable array. Later decoder
buffer mutations, best-crop changes or parent reassociations cannot alter those
bytes/receipts or transfer a result to another segment.

## Attempt and execution protocol

The ledger record exists before executor.submit, including failures to submit.
Attempt IDs are run-scoped ordinals, independent of UUIDs allocated by standalone
review. Up to two operations per segment reuse existing initial-crop and
substantially-better recheck thresholds; segment exit uses the existing minimum
candidate threshold. Recognition results rebuild TrackState's accepted replacement
policy in submission order, not worker completion order. No per-frame diagnostic
history is retained. Submission failures consume an operation slot rather than
retrying indefinitely without an audit record.

Workers receive crop bytes and a recognizer, never a segment/lifecycle manager.
They return frozen WorkerOutcome records containing serialized bounded result
bytes, worker-local execution records and a separate completion monotonic reading.
Owner `poll`, frame processing and closure apply them to the original operation.
Split whole/region OCR and provider retries remain one crop operation; repeated
route/model executions are deduplicated within that operation's evidence.

Injected recognizers implement:

```python
def recognize(plate_crop, record_execution, *, receipt):
    # Call only at actual recognizer entry, never merely on route selection.
    record_execution(Execution('private_local', supplied_local_model))
    return existing_unified_result
```

PrototypeRecognizer instruments local OCR entry and actual provider transport
entry. It does not mark the agent executed when disabled, missing a key, or unable
to prepare JPEG. Caller-supplied RecognizerModel(name, positive_version, task='ocr')
identifies the actual artifact/prompt registration; no synthetic catalogue key is
looked up or defaulted. Registration truth/artifact verification is an upstream
responsibility. Local invocation and provider request evidence do not independently
prove a remote model ran successfully or the transcription is accurate.

Cancelled queued operations have no fabricated execution. Worker recognition
exceptions retain any execution records already made. An unexpected failed future
without a returned envelope has unknown execution rather than claiming that no
model ran. Errors retain bounded codes, not arbitrary exception text.

## Selected evidence and DB1 v0 limits

A local read requires an accepted private-local result and structurally valid
parser evidence. An agent read requires accepted agent source/route and explicit
strict boolean text_complete=true. The flag is a completeness claim, not proof.
Both use the P1 lossless normalization; incomplete/placeholder text is not read.
The existing accepted-result replacement rule chooses within a segment. When a
vehicle has several independent plate segments, choose among their admitted read
winners by measured quality first/highest, then earliest operation ordinal.
This deterministic multi-segment policy does not assert jurisdiction/category
accuracy.

If no read is admitted, the P1 select_unreadable_attempt helper selects from the
settled ledger without duplicate implementation. Finite nonnegative measured
quality ranks ahead of null; all-null quality uses earliest ordinal/lexical ID.
Invalid measured quality remains in the audit ledger but is ineligible. Selected
receipt, bytes, bbox, quality, text, route and actual executed model all come from
one operation; descriptive model_version strings never substitute for execution.
The result JSON's timestamp is not reconstructed into a receipt.

FinalPlateEvidence contains the complete submitted-operation ledger, selected
support, parent first/last receipts, closure cause, quarantine and omission reason.
True zero-operation/no-crop cases remain omitted under DB1 v0, with separate
no_detection, ambiguous_association, unassociated_crop, rejected_crop, capability
and failure_before_model diagnostics. No no_plate row, fake attempt/model or crop
time is fabricated. No SQL rows are constructed here.

## Closure, deadlines and budgets

The coordinator exclusively claims owner closures and consumer results. Plate
finalize_vehicle seals segments, settles futures under a configured deadline,
caches one immutable result, and only then releases working state. Repeated
closure returns the same staged result; after transfer it returns None and never
runs OCR again. Colour may already have staged its result when plate finalization
fails; retry uses that same colour object before acknowledging either consumer.
A failing completion sink likewise leaves evidence pending for retry.

Default closure drain is **one second per vehicle**, configurable and finite.
After the deadline, queued futures are cancelled. Running work that cannot be
cancelled is detached from analytical state and recorded as settlement_timeout
with unknown execution. The lifetime is omitted with unsettled_operation and
settlement_incomplete=true; no analytical read/unreadable is claimed before known
settlement. Original closure cause/quarantine are preserved. Late outcomes cannot
mutate the finalized object. Unknown execution after unexpected executor failure
uses the same conservative incomplete admission.

Python threads cannot forcibly interrupt an executing model/provider call. A
late worker may continue consuming resources, and Python's executor exit hooks
may wait at process termination. The consumer's closure/shutdown does not wait
indefinitely. Hard execution deadlines/process termination require a supervised
process executor later; no such recovery is claimed here.

Configured bounds, with defaults:

- 16 in-flight futures including detached running work, one default worker;
- 128 retained working segments, at most 16 per vehicle;
- two submitted operations per segment;
- 8 MiB per retained crop and 64 KiB per serialized recognizer result;
- 128 staged final plate results; coordinator combined queue also defaults to 128.

The candidate best crop plus its TrackState array and at most two operation crops
are bounded per segment; retained evidence holds bytes rather than full frames.
These are ceilings, not preallocated buffers. Set smaller budgets for the host's
memory envelope. Pending/final evidence is never silently evicted. Admission
raises PlateCapacityError before additional dispatch when capacity is full.
At segment closure, a would-be exit submission blocked by in-flight capacity gets
an explicit submission_failed/closure_capacity_exhausted ledger entry, allowing
bounded finalization without hidden crop loss or another queued worker.

`poll()` reaps detached running futures only when done. A cancelled queued future
still owns its crop in Python's executor queue until that queue removes the work
item. The default executor wrapper keeps this slot charged until retirement,
preventing repeated cancellation behind a stalled worker from growing retained
crop memory. An injected executor's cancelled future must expose a boolean
resources_released property to reclaim its slot; opaque cancelled futures remain
conservatively charged. The externally supplied executor owns its queue cleanup. `release_finalized` transfers a
staged result to the coordinator; `drain_completed` transfers combined immutable
evidence and releases legacy classification profile history. Closed service IDs
are compacted into intervals; no lifetime/crop snapshot is retained as a tombstone.
Owner snapshots/profiles are released through the existing acknowledgement path.
After owner finish/stop and closure delivery, call plate.shutdown() idempotently;
it rejects shutdown while parent working states remain. Externally supplied
executors belong to their caller. Concurrent access to the coordinator/consumer
is unsupported; the default recognizer worker is serialized. Hosts supplying
parallel executors must supply a thread-safe recognizer or serialize model access.

## Wiring boundary and controlled smoke scope

Keep the repository root plus vehicle/license_plate_service/ on the import path
(the existing plate package uses run_video_pipeline absolute imports). Use one
common/classification/colour import root consistently, as documented in P2b2a.
There is no sys.path mutation in the shared runtime modules.

```python
from run_video_pipeline.shared import SharedPlatePipeline, PrototypeRecognizer
from run_video_pipeline.shared_evidence import RecognizerModel
from vehicle.common.observation_coordinator import ObservationCoordinator

recognizer = PrototypeRecognizer(
    existing_crop_pipeline,  # Existing reader, marker localizer and explicit agent.
    local_model=supplied_local_registration,
    agent_model=supplied_agent_registration,  # None when not executing an agent.
)
plate = SharedPlatePipeline(
    context, detector=existing_plate_detector, tracker=existing_plate_tracker,
    recognizer=recognizer, drain_timeout=chosen_deadline,
)
coordinator = ObservationCoordinator(
    classification_service, {'colour': colour, 'plate': plate},
    on_completed=accept_combined_evidence,
)
try:
    coordinator.process_video(path, max_frames=chosen_limit)
finally:
    # Retry any failed staged delivery before releasing evidence/shutting down.
    coordinator.flush_closures()
    plate.shutdown()
```

For an external file decoder, call capture_receipt immediately after successful
read, then coordinator.process_frame(image, index, receipt=receipt). On external
EOF use coordinator.finish(RunEnd.END_OF_INPUT, trigger='end_of_input'); bare
finish/stop means service_stop. File limits preserve service_stop/frame_limit,
EOF end_of_input, errors process_error and clock faults clock_discontinuity.
Reset is notification after the host actually resets tracker state; reset the
plate tracker too if its state was discarded. No RTSP reconnect implementation
or automatically recovered invalid clock epoch is added.

Next scope is an explicitly bounded real-video smoke host using this Python
boundary, a supplied registered context and verified model artifact registrations.
Use one small labelled local file, known max_frames and CPU/GPU resource budgets;
keep agent disabled initially, retain combined in-memory evidence and inspect
association margins, IDs, crop receipts, operation counts and end causes. Then
review representative unique/overlapping vehicles and unreadable crops, before
any provider-enabled or RTSP phase. Do not use the standalone plate video run as
a second decoder. No web/CLI dispatch change, DB row building or persistence is
needed for this smoke host.

## Executed validation

Existing /home/intern/.venv/bin/python; no dependencies installed:

- P1: **114 passed, 0 failed**.
- P2a foundation: **18 tests passed**.
- Classification integration: **24 tests passed**.
- Shared colour: **30 tests passed**.
- Focused plate/shared coordinator: **53 tests passed**. Injected futures include
  queued cancellation, worker/submission errors, split OCR/provider retry counts, out-of-order parent completion,
  a running timeout/late return, completion inside a positive deadline, staged
  colour retry, limits/EOF, reset/loss/fault, bounds, standalone compatibility and
  immutable same-frame crop provenance and cancelled-queue retirement accounting.
- Django system check: no issues.
- Existing plate/colour CLI help and application-root shared import smoke: passed.
- Source AST parsing, standalone detector/OCR/parser/router and colour algorithm
  comparisons to HEAD, source whitespace and git diff --check: passed.
- Supervisor db1_contract.zip SHA-256 comparison to the pre-step baseline: passed.

Commands from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check
```

The tests inject decode/detection/tracking/recognition and mock provider responses;
one timer-controlled Future completes during the drain. No real video/model/GPU,
provider network, accuracy calibration, RTSP reconnect, durable recovery,
database-dependent Django test suite or deployment was exercised. The unrelated
classification CLI dispatch issue documented in P2b1 remains unchanged and did
not block these tests. No SQL/database/Redis/uploads/training/T2/freeze/commit/push
was performed.
