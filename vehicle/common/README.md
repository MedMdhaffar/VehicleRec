# P2a foundations

These modules are staged foundations, not inference-loop integration or a T1
freeze. Use the repository root on the Python import path (`vehicle.common`, or
`common` when running with `vehicle/` as the application root). The pure shared
policy `contracts.db1.association` must also be packaged/importable; no fixture
JSON or validator is loaded by runtime code.

- `db1_context.py`: frozen nested org/camera-version/run/model/resolution records.
  Every registration key and provenance field is supplied. Production rejects
  explicitly synthetic resolution. No manifest is loaded and no seed key is a
  default. Upstream must supply trustworthy provenance; this flag is not an
  authorization mechanism. Camera-version matching is checked at run start;
  P2b must handle a later version change by ending the run.
- `frame_receipt.py`: capture once immediately after successful decode and before
  inference. Pass that same immutable object to all observations on the frame.
  Inject both clocks, construct one `ClockPolicy` per run (provisional five-second
  default), and retain that policy for diagnostics. UTC is normalized to aware
  UTC; optional media PTS remains independent. Regressions, excessive paired
  divergence, invalid readings, and explicit OS-step notifications invalidate the
  epoch. A failed sample yields no receipt, retains the last good record, and
  cannot become stable automatically. A PTS problem or duplicate frame index is
  an input error, not a fabricated clock recovery.
- `vehicle_observation.py`: single-owner lifetime manager with immutable snapshots.
  Sightings retain service identity, count distinct frames, and update receipts.
  Call `lose(raw_id)` only when the tracker declares loss. Reuse after loss creates
  a new lifetime sequence. Reset/reconnect closes open lifetimes and advances the
  tracker epoch without resetting the run-wide positive BIGINT allocator.
  `close(service_id, VehicleClosure)` is idempotent, including after raw-ID reuse.
  `end(RunEnd)` ends the run exactly once; reconnect is recoverable camera-outage
  vehicle closure only. Exhaustion raises `TrackIdExhausted` without adding state.
- `plate_association.py`: re-exports the P1 pure geometry policy from
  `contracts/db1/association.py`. No duplicate algorithm or harness dependency.
  Supply active boxes from the same frame; lifetime segmentation is P2b work.

On `ClockEpochInvalid`, the P2b owner must call `lifecycle.clock_fault()` before
leaving the decode loop. This closes affected open lifetimes using their last
valid receipt and marks them quarantined. A subsequent observation also detects
and closes a faulty epoch, but callers must not rely on another observation
arriving. Explicit recovery creates a new run identity/context, clock and
lifecycle after an external stability decision. Constructing a new object alone
does not prove stability. Closed state and allocation are in memory; process
restart requires a new run. No concurrent access or durable recovery is provided.

Focused check from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests -v
```

Tests exercise synthetic sightings, clocks and supplied registrations. They do
not prove decoder-to-inference ordering in the existing loops, which are not
wired yet. Closed snapshots remain retained for review; integration needs a
bounded consumption/retention policy.

## P2b1 classification owner

Classification now optionally integrates these foundations through an explicit
`observation_clock`; its standalone JSON and raw public IDs remain unchanged.
`vehicle_frame.py` defines the immutable shared frame/box interface. See
[the classification integration report](../../docs/t1/db1_p2b1_results.md) for the
single-decoder consumer boundary, fault hooks and P2b2 API changes.

`FrameReceiptClock.capture(..., media_pts_reader=...)` samples paired clocks
before reading optional decoder PTS. The classification owner closes immediately
on clock faults. `VehicleLifecycle.release_closed(ids)` atomically releases
acknowledged closed snapshots; active IDs cannot be released and allocation is
never reused. Classification drains expose completed evidence and legacy profiles
separately; drain both for long-running consumption. Same-epoch raw sequence
counters remain scalar metadata, while obsolete epoch counters are discarded on
reset/reconnect. The original P2a validation notes above describe the foundation
stage; P2b1 tests additionally exercise injected decode-to-inference ordering.

## P2b2a shared-colour coordination

`ObservationCoordinator` is the sole classification-closure delivery owner for
registered consumers. It fans each frame/closure out, retains immutable completed
results, and acknowledges individual closures only after every consumer has
staged evidence. Pending delivery failures can be retried. Its queue has an
explicit limit and completion callback option; full queues fail explicitly and
preserve undelivered evidence. `drain_completed()` transfers combined records and
releases legacy profiles. Colour's final evidence copies identity/sample metadata
and owns retained crop bytes, without another decoder or vehicle model.

See [the P2b2a report](../../docs/t1/db1_p2b2a_results.md) for construction,
provenance, quarantine, release rules and the next plate-consumer scope. Previously
unclaimed standalone classification drains still work; after coordinator binding,
only that coordinator may peek/ack/drain owner closures or consumer results.

P2b2b adds `run_video_pipeline.shared.SharedPlatePipeline` as another registered
consumer of the same `ObservationCoordinator`. Put the repository and
`vehicle/license_plate_service/` on the host import path; use the same common
import root as classification/colour. Supply the plate detector, plate tracker,
explicit recognizer registrations and run context. `process_vehicle_frame` reads
same-frame service-ID boxes and uses `plate_association.associate_plate`; it opens
no capture and invokes no vehicle model. See
`docs/t1/db1_p2b2b_results.md` for the worker-return protocol, closure deadline,
capacity behavior and the controlled file smoke scope. Existing standalone video
and RTSP APIs remain independent. Shared RTSP reconnect wiring is not provided.

Controlled development smoke contexts now use environment='development' and
explicitly unresolved camera/model/run/lane/source-media keys (all None), with
actual local artifact fingerprint names and development provenance. Numeric
resolution claims are rejected for development; production/test still require
positive supplied keys. database_keys_resolved=False is an explicit barrier for
future row construction, not an authorization implementation. See
`docs/t1/db1_shared_smoke_results.md` and `scripts/db1_shared_smoke.py` for the
CPU file run, scoped offline/provider controls, hard subprocess deadline and
observed omission fix. No platform registration is inferred from smoke identities.

P3a adds [db1_rows](db1_rows/README.md): pure construction from immutable
`CompletedVehicle`, explicitly supplied frozen resolution records, caller-loaded
P1 schemas and an injected stable UUID policy. Natural delivery payloads and SQL
projections remain separate; callers inspect blocked diagnostics and retry
conflicts before accepting a proposal. The builder never acknowledges/drains the
coordinator. Optional colour `RecognitionIdentity` configuration supplies actual
supporting model attribution without synthetic defaults or algorithm changes.

P3a review fixes make coordinator cleanup retryable independently of the owner's
pending closure list. Staged results retain sink acceptance, owner acknowledgment
and successful consumer releases. Successful callback returns are not repeated
for cleanup failures; remaining releases retry without new finalization. Actual
classification acknowledgment and colour/plate release are idempotent for known
finalized identities. `drain_completed` raises `PendingCleanup` while acceptance or
cleanup remains pending; retry `flush_closures` first. See
[the source-review report](../../docs/t1/db1_p3a_review_fixes.md) for partial-side-effect
handling, tests and the in-memory acceptance boundary.

P3b1 adds an optional [local DB1 outbox](db1_outbox.md) and coordinator completion
sink. It commits eligible construction before callback return, preserves accepted
history across restart and performs no transport or owner/consumer cleanup itself.

P4 adds an optional [PostgreSQL adapter](db1_adapter.md) for immutable accepted
outbox entries and a separately proposed receipt schema. Actual PostgreSQL 16
integration is blocked on this VM; injected tests are documented separately.
