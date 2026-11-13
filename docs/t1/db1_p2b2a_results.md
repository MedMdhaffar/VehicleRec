# Classification-owned colour evidence — P2b2a, 2026-10-09

Shared colour is an optional Python integration. The existing standalone colour
pipeline, Django adapters and public JSON continue to use their existing APIs.
No plate path, SQL row builder, database, transport or T1 freeze is introduced.

## Files changed in this step

- New `vehicle/vehicle_color_service/vehicle_color_service/shared.py`:
  `SharedColourPipeline(context, ...)` consumes `process_vehicle_frame(image,
  VehicleFrame)` and uses service_track_id for state. It constructs no YOLO model,
  calls no vehicle tracker and opens no capture. Only vehicles in the same-frame
  tuple receive attempts. It reuses the existing `_observe`, `_apply`, fallback
  admission and `decide_colour` functions. The standalone entry points are
  deliberately rejected on this shared class to prevent accidental extra inference.
- New `vehicle/vehicle_color_service/vehicle_color_service/evidence.py`: frozen
  identity, sample, owned-crop and final-colour records. Evidence is independent
  of legacy public profiles and is not a SQL row or P1 diagnostic envelope.
- `vehicle/vehicle_color_service/vehicle_color_service/detector.py`: injectable
  model for standalone tests; injectable estimate/quality hooks; count crop
  operation submission before inference so failed operations remain attempts.
  Successful public output and all recognition thresholds/formulas are preserved.
- New `vehicle/common/observation_coordinator.py`: one classification owner,
  registered synchronous consumers, reserved closure delivery, immutable combined
  completed results, bounded completion queue and optional completion callback.
- Classification nested `pipeline.py` and `service.py`: claim/peek/acknowledge
  closure interfaces. Existing drain behavior is preserved until a coordinator
  claims it; subsequent unregistered drains are rejected. Individual successfully
  delivered closures can be released without discarding failed deliveries.
- New `vehicle/vehicle_color_service/vehicle_color_service/tests/test_shared_colour.py`:
  injected detector/decoder, HSV estimates, crop quality and agent responses.
- This report and `vehicle/common/README.md`: current interface and limitations.

Colour `temporal.py`, `colour.py`, `config.py`, `schemas.py`, the Django colour
service, classification aggregation and plate code were not changed in this step.
The unrelated classification CLI dispatch issue remains as documented in P2b1.

## Identity, frame and crop ownership

Inspection confirmed that VehicleFrame, VehicleBoxObservation, VehicleLifetime,
FrameReceipt and the nested supplied RunContext registration records are frozen.
Classification sightings replace lifetime snapshots; they do not mutate the
snapshot embedded in an earlier VehicleFrame. Colour nevertheless copies scalar
service/raw/epoch/lifetime identity into VehicleIdentity rather than retaining a
mutable profile or lifecycle manager reference. Supplied context and receipt are
safe immutable references. No raw-ID equality establishes colour parentage.

Each retained valid sample copies numerical label/confidence/quality/weight,
clipped bbox and the actual shared receipt. Mutable ColourObservation diagnostic
maps remain internal to the unchanged temporal machinery; they are not part of
final evidence. A shared frame must have increasing frame_index and all its
vehicle lifetimes must belong to this run, be open, and have that exact receipt.
Duplicate service IDs, stale vehicles and closed IDs reject.

Colour only reads the shared image. Inference receives an owned crop copy; even
an injected mutating estimator cannot change the decoder buffer. Best crops are
retained as immutable bytes with shape/dtype/bbox and supporting receipt. The
agent operation receives another owned array, with a byte snapshot made BEFORE
invocation. Later best-crop replacement cannot change the actual agent-submitted
crop record. `OwnedColourCrop.array()` returns a fresh writable copy.

Best crop selection remains the existing largest-area rule, with earliest crop
retained on equal area. Temporal sample locking, short-track rules, confidence
shares and optional agent admission thresholds remain unchanged. A failed or
rejected agent response retains a usable local decision and its local provenance.

## Final evidence and timestamps

`FinalColourEvidence` includes explicit run/service identity, vehicle first/last
receipts, closure cause/quarantine, outcome state, optional label/confidence,
source, support receipt, local attempts, valid sample records/count, local errors,
agent attempts/error, best crop and actual agent crop. It also preserves the local
candidate/winner share when an agent overrides the result.

| Evidence state | Supporting receipt |
|---|---|
| Local decided | Last retained valid sample contributing to the temporal aggregate, including a valid losing-label sample |
| Accepted agent override | Actual submitted crop receipt; may precede the final or current best crop |
| Attempted undecided | Last submitted local crop operation |
| Operation failure with no usable local result | Last submitted local crop operation; error state/count retained |
| Disabled/unavailable/zero-attempt omission | No colour supporting timestamp is invented |

Unavailable capability uses `unsupported_capability`, disabled uses
`disabled_capability`, and no usable crop uses `rejected_crop`. Zero attempts never
become an attempted undecided/error record. The agent count counts submitted crop
fallback operations, not provider RPC retries. A configured but failed fallback
records bounded error codes rather than exception text/credentials. Agent model
registration/key resolution and SQL validation remain future work.

Clock quarantine is preserved even if stored valid evidence can still yield a
colour label. Final aggregation/fallback uses retained crop evidence, never a new
fabricated receipt or a claim that the invalid clock recovered. Later row building
must honor the quarantine flag.

## Single-owner orchestration

Repository-root imports for a host that already has supplied run context:

```python
from vehicle.common.observation_coordinator import ObservationCoordinator
from vehicle.vehicle_color_service.vehicle_color_service.shared import SharedColourPipeline

colour = SharedColourPipeline(context)  # Explicit supplied RunContext.
coordinator = ObservationCoordinator(classification_service, {"colour": colour})
```

`classification_service` must already have an observation_clock with that context.
Django application-root hosts with `vehicle/` on sys.path use `common` and
`vehicle_color_service.vehicle_color_service.shared` (and the equivalent nested
classification service path). Colour's inner directory is a namespace package;
adding the outer Django package to sys.path makes `vehicle_color_service.shared`
resolve to the outer package and fail. The standalone CLI's own application
working directory remains supported separately. Choose one import root consistently.
There is no manifest loading or production seed default.

External decoder ownership:

```python
# Immediately after the single decoder's successful read:
receipt = coordinator.capture_receipt(index, media_pts_reader=read_media_pts)
update = coordinator.process_frame(image, index, receipt=receipt)
# Transfer results regularly to your own consumer:
completed = coordinator.drain_completed()
```

File ownership: `coordinator.process_video(...)` delegates to classification's one
capture and vehicle detector/tracker and installs the consumer callback. It also
flushes closures after EOF, limits, stop, errors and faults even if no further
observation arrives. Long file delivery can use
`ObservationCoordinator(..., on_completed=accept_completed)` to synchronously
transfer each completed combined record without retaining it in the coordinator
queue. The callback must return only after accepting ownership of the immutable
result; an exception keeps it pending for explicit retry. If its own external
side effects can fail ambiguously, the callback needs its own idempotency rule.
No durable/exactly-once external delivery is claimed.

Use coordinator `reset_tracking`, `stop`, `finish` and `clock_fault` wrappers.
Reset remains a notification after the host resets/replaces the actual tracker.
Direct owner control calls are possible but require a subsequent explicit
`coordinator.flush_closures()`; they are not silently observed by another decoder.
The coordinator claims classification closure delivery and each consumer's pending
result delivery. Independent drains cannot steal notifications or staged evidence.
A future plate consumer registers alongside colour in the same coordinator.

For each closure, the coordinator asks every consumer for its immutable final
result before acknowledging classification or releasing consumer pending state.
It then owns a CompletedVehicle containing the parent/classification closure and
all consumer results. Consumer working state is removed only after its final
record is stored. Repeated delivery of a pending colour closure returns that same
record; repeated cleanup after transfer never re-runs colour/agent inference.
A failing second consumer leaves earlier staged results and the owner closure
available for retry. Missing colour state at closure becomes a zero-attempt
omission, which is important after a partial processing failure.

## Bounds and release

- Active colour state holds at most the existing temporal sample limit plus one
  best crop and one submitted agent crop per lifetime. Attempts/errors are scalar
  counters; invalid attempts are not retained in an unbounded history.
- Completed colour state is transferred into immutable records before active
  state is removed. Consumer pending records are released only after the
  coordinator owns the combined result. Uncoordinated shared users must explicitly
  call `drain_finalized()`; nothing silently evicts pending evidence.
- The combined queue defaults to 128 results; max_pending is explicitly
  configurable. At capacity, PendingResultsFull interrupts processing rather
  than discarding a closure. Classification fails closed with process_error when
  backpressure interrupts its callback. Existing queued results and undelivered
  owner/consumer closures remain intact. Drain completed results and call
  flush_closures again until the backlog transfers; this does not resume a stopped
  run. A completion callback avoids queue buildup for a synchronous accepting host.
- Colour deduplication uses merged closed-ID intervals, not one retained snapshot
  or tombstone per lifetime. Tests cover many later closures behind one long-lived
  low ID, and out-of-order closure. Intervals compact after gaps close.
- `coordinator.drain_completed()` also releases legacy classification profile
  history. A full file report intentionally retains that legacy history unless
  the host explicitly drains it. A completion callback can likewise drain legacy
  profiles once it owns the combined record. Classification scalar raw-sequence
  metadata and ROI event history retain their documented P2b1 behavior.

Caller-retained evidence/images are outside the owner's budget. Active memory
scales with concurrent lifetimes and decoded crop size; there is no automatic
active-lifetime eviction. This is synchronous in-memory coordination, without
concurrent-access synchronization, persisted recovery or transactional delivery.

## Executed results

Existing `/home/intern/.venv/bin/python`, no installs:

- P1 validator: **114 passed, 0 failed**.
- Foundation suite: **18 tests passed**.
- Classification integration suite: **24 tests passed**.
- Shared-colour suite: **30 tests passed**, with parameterized scenarios.
- Django system check: **no issues**.
- Existing standalone colour CLI help and application-root shared import smoke:
  passed without constructing models.
- Static comparison to HEAD: colour algorithms, temporal rules, thresholds and
  schemas unchanged; standalone process_frame/finalize_all/fallback/_apply ASTs
  unchanged. Colour detector modification is limited to injection and counting.
- Source parsing, source whitespace, git diff --check and supervisor archive
  SHA-256 baseline comparison passed. No supervisor artifact was modified.

Commands from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check
git diff --check
```

## Remaining limits and next plate scope

Tests inject detection/tracking results, local estimates/quality and agent replies.
No real video decode, YOLO/GPU execution, colour accuracy/calibration, live agent
provider, RTSP reconnect, object storage, database-dependent Django test suite or
crash recovery was exercised. Django/CLI standalone entry points are not switched
to shared mode. The unrelated classification adapter `-m ...cli` dispatch issue
is unchanged; P2b1 documents it separately.

Next scope is plate association/attempt integration through the same coordinator:

1. Extend `license_plate_service/run_video_pipeline/run_video_prototype.py`:
   `VideoPrototypeRunner.process_frame` accepts the shared receipt and parent
   service-ID boxes. Keep plate detection/OCR algorithms, call the reusable P1
   association rule, and reject ambiguous/unassociated crops analytically.
2. Preserve crop receipt/identity in TrackState.consider and RecognitionAttempt.
   Record operation submission in _submit BEFORE dispatch; preserve failed
   futures and the actual executed model/route through _recognize and _drain.
   Do not count split OCR calls or HTTP retries as additional crop operations.
3. Segment attempts on parent change, with fresh logical result state; raw plate
   tracker equality must never supply the vehicle identity. Agent read completeness
   admission and unreadable selected-attempt provenance remain explicit work.
4. Extend PrototypePlatePipeline.process_crop/schema evidence to use supporting
   crop receipt instead of processing completion time. Add a plate consumer with
   the coordinator claim/finalize/release protocol so it receives the SAME parent
   closure as colour; it must not independently drain classification.
5. Refactor plate file/RTSP orchestration around ONE decoder owner. Live reconnect
   wiring remains staged work and must preserve run/epoch rules. No new decoder,
   vehicle inference, row builder or persistence belongs to this next evidence step.

No plate edit, SQL, database, Redis, upload, training, T2, freeze, commit or push
was performed by P2b2a.
