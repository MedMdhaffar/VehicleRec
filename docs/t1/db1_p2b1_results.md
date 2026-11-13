# Classification vehicle-observation owner — P2b1, 2026-10-09

This is optional classification integration into the existing P2a foundations.
T1 remains a review candidate. No plate/colour path, detector/tracker algorithm,
classification aggregation, SQL or row builder was changed.

## Files and behavior

Paths under `vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/`
are relative to `vehicle/`:

- `service.py`: optional keyword `observation_clock`, injected capture/PTS hooks,
  immediate post-decode clock capture, external-decoder receipt boundary,
  synchronous `on_observation(frame, VehicleFrame)` callback, explicit stop and
  reset notification, exception/fault closure, and resource cleanup via ExitStack.
- `pipeline.py`: uses VehicleLifecycle only when an explicit clock/context is
  supplied. Existing public `track_id` remains the raw tracker ID. Each contributing
  vehicle row updates its allocated service lifetime. The existing LOST_FRAMES
  rule declares loss; absent-but-still-active profiles are excluded from
  same-frame association candidates. Supports idempotent finish, explicit reset,
  clock-step notification and separate drain methods for completed evidence and
  legacy profiles.
- `schemas.py`: optional in-process `FrameUpdate.observation` and private profile
  lifetime reference. `to_dict()` retains the old field set and deep-copy behavior;
  no context, UTC receipt or service ID is silently added to public JSON.
- `observations.py`: lazy shared imports for integrated hosts. Standalone use does
  not require common modules. Hosts must choose one import root consistently.
- `tests/test_observation_integration.py`: injected decoder, detector/tracker and
  clocks; no model loading or database setup.

Shared foundation changes:

- `vehicle/common/frame_receipt.py`: optional PTS reader invoked AFTER paired clock
  sampling and BEFORE publishing the immutable receipt. PTS errors remain input
  errors; they do not invent UTC or invalidate an otherwise valid clock sample.
- `vehicle/common/vehicle_observation.py`: atomic `release_closed(service_ids)`
  rejects active IDs and frees acknowledged snapshots without reusing allocation.
  Reset/reconnect discards obsolete epoch sequence counters.
- `vehicle/common/vehicle_frame.py`: frozen `VehicleFrame(receipt, vehicles)` and
  `VehicleBoxObservation(lifetime, bbox, vehicle_type, type_confidence)`.
- `vehicle/common/README.md`: current classification integration and drain notes.

## Closure policy

| Trigger | Vehicle closure / run end | Additional owner diagnostic |
|---|---|---|
| Normal file EOF | end_of_input | end_trigger=end_of_input |
| Configured max_frames | service_stop | end_trigger=frame_limit |
| stop_requested, stop(), default finish(), KeyboardInterrupt | service_stop | end_trigger=explicit_stop |
| Decoder/inference/consumer/cleanup exception | process_error | end_trigger=process_error |
| Existing tracker-loss threshold | track_loss; run remains open | Closed lifetime carries cause |
| reset_tracking() notification | tracker_reset; run remains open, epoch advances | Closed lifetime carries cause |
| Invalid paired clock / explicit clock_fault(reason) | clock_discontinuity; run ends | Quarantined, last valid contributing receipt retained |

`finish()` never overwrites a prior closure cause or enqueues the same final
observation twice. Clock failure is handled at receipt capture, before another
inference or sighting is needed. Invalid runs cannot continue. Explicit clock-step
notifications must call the owner `clock_fault(reason)` so closure happens even
when no further frame arrives. Reset notification is sent AFTER the caller resets
or replaces its actual tracker; this code does not invoke a new tracker algorithm
or reset API. Resetting a faulty epoch closes it and cannot recover it.

## Concrete P2b2 consumer boundary

Construct `FrameReceiptClock` with an explicitly supplied `RunContext`, injected
UTC and monotonic clocks, and one fixed `ClockPolicy`. Pass it as
`VehicleAnalysisService(..., observation_clock=clock)`. No org, catalogue, model
or run identity is inferred from manifests. Production provenance checking is
still supplied-context validation, not authorization or a database lookup.

Choose ONE decoder owner:

1. File path: call `process_video(..., on_observation=consumer)`.
   Classification performs exactly one decode and vehicle inference. The consumer
   receives the same image and a `VehicleFrame` before the next decode. All boxes
   in that tuple contributed on `VehicleFrame.receipt.frame_index`. Each carries
   the same receipt through `lifetime.last_receipt`, plus distinct
   `service_track_id`, diagnostic `raw_track_id`, epoch and raw lifetime sequence.
2. Externally decoded path: immediately after a successful decoder read, call
   `receipt = service.capture_receipt(index, media_pts_reader=reader)` and then
   `update = service.process_frame(image, index, receipt=receipt)`. Supply
   `update.observation` to plate/colour. Never capture again or reconstruct time
   after inference. Missing/stale/copied/wrong-index receipts reject before
   inference and end the integrated run as a process error.

Standalone `process_frame(image,index)` / `process_video(...)` without a supplied
clock/context retain raw IDs and old JSON, and return no shared observation.
`on_observation` without context is rejected. The integrated video API requires a
file source context. Frame-level integration can serve a future RTSP owner, but
no reconnecting decoder has been wired in this change.

The P2b2 change scope is concrete:

- Refactor plate video/RTSP orchestration to consume this owner's image, receipt
  and same-frame service-ID boxes. Keep plate detection/recognition, use the shared
  P1 association rule with service IDs, carry crop receipt into operations, record
  submission/failure/model execution, and segment attempts when the parent changes.
  Existing `VideoPrototypeRunner.process_frame` currently owns a plate tracker and
  has no shared-parent receipt argument; that API needs extension. Its separate
  video/RTSP decode loops must be replaced or routed through one orchestrator.
- Add a colour observation method that accepts these vehicle boxes and service
  IDs. Current `ColourPipeline.process_frame` performs its own vehicle detection
  and owns raw-ID profiles; it needs refactoring for the shared-owner mode.
  Retain existing colour algorithms while preserving valid-sample and best-crop
  receipts, including the actual agent-submitted crop. Do not run the vehicle
  detector twice for the same frame.
- Consume closure evidence before releasing it; flush plate/colour state by
  service ID and preserve cause/quarantine. Do not use the equality of raw IDs to
  establish a parent. Synchronous consumers must treat the decoded image as
  read-only; asynchronous consumers must explicitly copy/retain the relevant crop
  with the immutable receipt.

## Storage ownership and draining

Only current per-vehicle lifetime snapshots and the latest frame interface are
updated; no per-frame history is retained by the observation owner. Completed
snapshots remain pending until explicit consumption:

- `service.drain_finalized_observations()` transfers the pending immutable evidence
  once, releases those closed entries from VehicleLifecycle and drops private
  receipt references on retained legacy profiles. Repeat drains return `()`.
- `service.drain_finalized_profiles()` transfers/clears the legacy JSON profile
  history. A full standalone report intentionally retains that history unless
  drained. Long-running frame consumers should drain BOTH interfaces regularly.
- Draining does not rewind the allocator or allow a closed ID to finalize again.
  Re-closing a released lifecycle ID raises KeyError, rather than retaining an
  unbounded set of tombstone snapshots. The pipeline never re-closes drained IDs.

Raw lifetime sequence counters are scalar run metadata retained per distinct raw
ID within the current epoch to distinguish reuse; old epochs are cleared. They
are not an observation history, but unique raw IDs can still grow this metadata.
Legacy ROI events and the full file report also retain their existing history.
Consumer-owned retained snapshots and images are outside the owner's memory
budget. There is no automatic eviction that silently loses finalized evidence,
no concurrent-access synchronization, persistence or crash recovery.

## Executed validation

Existing VM interpreter `/home/intern/.venv/bin/python` (Python 3.11), no installs:

- Complete P1 validator: **114 passed, 0 failed**.
- Existing P2a suite: **18 tests passed**.
- Focused P2b1 integration: **24 tests passed** (including parameterized scenarios).
- Django system check: **no issues**.
- Package CLI `--help` in its existing application directory: passed.
- Application-root import smoke: standalone imports do not load shared foundations;
  integrated imports use the same common clock class.
- Static comparison against HEAD: `aggregate_vehicle_type` and
  `update_track_vehicle_type` ASTs unchanged. Detector/tracker files unchanged.
- Source parsing, whitespace check and `git diff --check`: passed.
- Supervisor archive SHA-256 unchanged from the pre-edit baseline.

Reproduce from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check
git diff --check
```

## Untested behavior and remaining limits

No real video decode, YOLO/ByteTrack/GPU execution, RTSP reconnect, annotation
writer, ROI accuracy, plate/colour inference, database-dependent Django TestCase
suite, persistence or transport was exercised. Existing video readers report a
false read as EOF; distinguishing corrupt-file decode failure requires an explicit
reader error signal. OpenCV PTS is best effort and may report zero when unsupported;
it is never a source UTC claim. ROI/public legacy monotonic fields still follow
existing behavior; shared lifetime receipt fields are the DB1 evidence source.

The Django subprocess adapter and CLI remain standalone and do not supply context.
Inspection also found its existing command uses `-m ...cli`, while that module has
no main guard; the working package entry point is `-m ...` via `__main__.py`.
That pre-existing dispatch issue was not changed or validated as a full upload
run here. Duplicate `services/` code is not this inspected Django classification
path and was untouched.

External decoder callers must use the receipt boundary and finish/stop/fault hooks;
a manually injected timestamp cannot prove decode ordering by itself. No automatic
clock recovery, camera-version-change wiring, caller authorization or model
registration lookup is provided. Post-run JSON reporting remains outside the
already finalized observation lifetime. No freeze, commit or push was performed.
