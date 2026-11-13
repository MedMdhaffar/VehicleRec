# Pure DB1 v0 construction

`RowBuilder.construct(completed, resolutions, prior=None)` takes a frozen
`CompletedVehicle` from `ObservationCoordinator`, explicit frozen `Resolutions`,
and an optional caller-owned previously accepted `Construction`. It returns four
`FactResult`s (vehicle, colour, plate, unavailable make). It never drains,
acknowledges, mutates or releases coordinator evidence. It performs no file,
network, model, database or transport operation.

Construct `ContractValidation(row_schema, diagnostic_schema)` with caller-loaded
P1 schema dictionaries and pass it and an explicit identity policy to `RowBuilder`.
The validator owns schema copies, enables `FormatChecker` including P1 timestamp
precision, and prohibits external schema references. Runtime modules import pure
`contracts.db1.reference` and `row_policies`, never `validate_fixtures`.

```python
validation = ContractValidation(supplied_row_schema, supplied_diagnostic_schema)
builder = RowBuilder(identity_policy=UUID5Policy(supplied_stable_namespace),
                     validation=validation)
proposal = builder.construct(completed_vehicle, supplied_resolutions,
                             prior=previously_accepted_construction)
for fact in proposal.ready_rows:
    delivery_bytes = fact.delivery.canonical
    sql_projection_bytes = fact.sql.canonical
    # Caller accepts/rejects the proposal; no persistence happens here.
```

`FactResult.delivery` and `.sql` are separate `FrozenJSON` objects. A natural
payload can exist while its SQL projection is blocked. `.value()` and `.snapshot()`
return independent review copies; mutations to those copies cannot change retained
results. Canonical bytes are JSON and preserve exact Decimal values. Mathematical
integers in scientific notation decode to Python ints for P1 schema checking;
fractional values decode to Decimal. `snapshot()` is a review proposal, including
conflicting projections when present; deliver only `ready_rows`, never all facts.

`FactResult.diagnostic` conforms to P1 diagnostics v1. `issues` gives explicit
construction errors. `metadata` is a separate immutable construction sidecar,
**not** an additional property inside the strict P1 envelope or a SQL column. It
retains native epochs, lifetime sequence, closure/quarantine, plate attempt/segment
IDs, global ordinals and their mapped UUIDs/vehicle-local ranks/receipts. Negative
native media PTS belongs here because P1 diagnostic PTS is nonnegative. It is not
changed into zero or interpreted as a UTC timestamp. Missing final geometry scores
are represented by diagnostic `association=null`, never reconstructed from global
counters. Sidecars are not delivery/SQL payload hashes.

## Supplied records and readiness

`RunResolution` binds org UUID, complete camera SCD2 registration, run identity/key,
vehicle lane, detector registration, file/stream source, input-media key, start,
service version and registered date keys. These must agree with the original
`RunContext`, including numeric keys and camera validity over the receipts.
Unprovisioned development contexts remain ineligible even if a caller supplies
numeric records later. Provision a resolved run before capture; do not rewrite
historical immutable development evidence to claim platform registration.

`Dimension` supplies exact names/keys and explicit approval (vehicle classes also
require group `vehicle`). `ResolvedModel` supplies the selected executed identity
and BIGINT key. Registry records reject duplicate identities/keys and recognition
keys colliding with the run detector. No manifests, fixture numeric keys or default
catalogues are loaded. Synthetic resolution is admitted only with environment
`test`, never development or production.

`MediaResolution` supplies a claimed object-store record and separate supporting
raw-crop SHA256, frame, timestamp, parent, dimensions and ownership. Registered
object SHA256/bytes describe the encoded object, **not** raw BGR bytes. Neither a
raw crop fingerprint nor local path becomes a media registration. Input clips may
have `run_key=None`, as supervisor SQL permits; supplied natural run/camera claims
must still match the input run. Selected plate crop registrations must match
actual selected owned bytes/receipt/geometry. No record means explicit nullable
media, with `media_unavailable`. A provided mismatch blocks construction.

Current classification exposes no owned selected best crop, so vehicle
`best_media`/`best_media_key` stay null. A supplied vehicle-crop registration cannot
be verified against that owner and blocks the vehicle projection; another
consumer's best crop is not silently borrowed.

Supplied approval/provenance records are assertions checked for internal
consistency. They do not independently prove authorization, object existence,
upload completion, platform registration, database catalogue availability or an
artifact matching its claimed model identity.

## Evidence and mapping

Vehicle class/confidence are copied from the owner's final aggregation. No
aggregation is rerun. `ts=first_ts`; last receipt and actual observed frame count
are retained. Recognition projections require an eligible parent projection.

Colour local decided support is the last retained valid sample. Undecided/error
support is the explicitly recorded last attempt. An accepted agent override uses
its actual submitted owned crop receipt and VLM model. Agent failure preserves
usable local evidence/model/support. `SharedColourPipeline` accepts optional frozen
`RecognitionIdentity` through `local_model`/`agent_model`; identities are read-only
for its run. Defaults remain None for compatibility and cannot produce colour rows.
The pipeline freezes the actual supporting route identity and last-attempt receipt
in `FinalColourEvidence`. No thresholds or temporal formulas change.

Plate evidence retains the producer's selected read authority. Read completeness
uses the same pure native-result admission helper as the shared producer;
normalization and unreadable selection reuse P1. Native reads require explicit
confidence/category fields and their producer's nonnull confidence. Model execution must be recorded
on the original parent/segment/attempt; selected bytes, score, text and timestamp
must match. Null quality uses measured-first, then earliest ordinal/lexical ID
fallback. Unreadable cannot conceal an admitted executed read. `private_local`
maps to P1 `local_private`; agent requires `task=vlm`. The native model record now
accepts explicit VLM task while retaining the legacy OCR default. Legacy agents
attributed as OCR are blocked, never relabelled by the builder.

Grouped colour names remain exact. Unknown plate category becomes explicit null;
a non-unknown producer category without an approved same-name mapping blocks its
projection. No `private -> TN`, `temporary -> RS`, or grouped-colour collapse is
inferred. Domain alias registration, if needed, is future work.

Every fact's `date_key` uses its own support receipt and original camera timezone.
File runs remain `host_replay_receipt`; PTS, processing completion and overlaid
source clocks never become recording UTC. Quarantined lifetimes block every fact.
Unsettled plate operations omit plate rows with settlement counts; independently
settled vehicle/colour facts remain eligible. Zero operations, no detection,
unassociated/ambiguous observations, crop rejection, disabled/unavailable
capability, policy/submission failure before a model and executed unreadable OCR
remain distinct. `failure_before_model` is not an assertion of an OCR exception.
Manufacturer examples in P1 are structural only; this producer always omits make.

Projections contain supervisor insert columns only, not generated fact PKs.
Schemas enforce exact column sets/nulls and SQL UUID/BIGINT/SMALLINT/INT/timestamp
ranges/formats. Score admission is finite, nonnegative, within REAL range
(including explicit rejection of nonzero subnormal underflow); confidence is
bounded by one. Service admission is stricter than SQL: positive service IDs,
finite nonnegative crop quality, allowed colour states/completeness rules and no
`no_plate` analytical row under DB1 v0. REAL server-side binary rounding is not
simulated or tested; canonical payloads preserve the original admitted number.

## Deterministic identity, equality and retries

The injected policy implements `row_uid(identity, row_kind)` and
`attempt_uid(identity, segment_id, native_attempt_id)` and returns UUIDs.
`UUID5Policy` requires a caller-supplied stable namespace. Its canonical array name
contains org UUID, natural camera ID, natural run ID and allocated service ID;
row names additionally contain `row` and fact kind, attempt names `attempt`, native
segment and attempt IDs. Raw IDs/epochs do not establish row identity. Native
zero-based tracker epoch maps to diagnostic `epoch=native+1`. Each vehicle's
operation ordinals map to the one-based ascending rank of their original run-wide
submission ordinals; operation counts remain ledger length. UUID names preserve
segment isolation; neither ID mapping nor rank creates extra OCR operations.

A natural digest is SHA256 of exact P1 canonical delivery bytes (including UID,
selected attempt UUID, model identity, explicit nulls, support and supplied media
natural identity). It excludes resolved numeric dimension keys, closure/PTS and
native diagnostic metadata. The SQL digest covers the separate canonical complete
SQL projection including supplied numeric keys. P1 canonical equality preserves
equivalent instants, UUID case, 1/1.0 and signed zero; object order is irrelevant,
missing nulls are different, distinct exact decimals remain different. Existing
P1 numeric bounds (4096 coefficient digits, absolute exponent 100000) apply.

The caller must retain a stable namespace/policy and pass prior **accepted**
construction to detect retries. Comparison matches logical fact parent identity,
compares delivery bytes and, when both exist, P1 natural/SQL retry equality. A
changed payload, changed SQL resolution, changed policy UID or disappearance of a
previous projection conflicts; conflicts are excluded from `ready_rows`. Resolving
an absent SQL projection is permitted only with identical natural payload. Adding
media to an already accepted null-media payload changes its hash and conflicts.
The builder retains no hidden retry registry. These tests establish in-memory
construction semantics only, not persisted deduplication, concurrency or crash
recovery.

Malformed top-level identity which cannot fit a P1 diagnostic (for example an
invalid service BIGINT) raises an explicit input error. Representable evidence or
resolution problems produce blocked diagnostics. `construction_blocked` is a P1
quarantine reason for invalid evidence, parent readiness and conflicts; it does
not assert a clock fault. Exact error codes distinguish these cases.

If used in `ObservationCoordinator.on_completed`, the host must raise or retain
results according to its acceptance policy before returning. Coordinator callbacks
that return normally acknowledge/release evidence. This package never installs a
callback or performs that acknowledgment itself.

## P3a source-review corrections

Retry matching uses the completed owner's stable org/run/camera/service identity,
even if the new natural payload vanished. Losing an accepted natural payload or
SQL projection conflicts. Conflict diagnostics retain the accepted UID, while
`metadata.proposal_diagnostic` preserves the complete original omission/validation
reason, error code and settlement counts. Accepted UID and natural/SQL hashes are
also preserved in metadata. The primary conflict envelope uses
`construction_blocked`/`PAYLOAD_CONFLICT`; incomplete settlement remains in the
original proposal diagnostic because P1 requires its `unsettled_operation` reason.
First-time omissions and unchanged natural-only to resolved transitions still work.
Parent eligibility is applied before retry comparison, so children cannot regain
SQL eligibility during conflict handling.

Read attribution must equal the last execution matching the result route, exactly
as the shared plate producer selects it. An earlier same-route model is not
eligible merely because it appears in the ledger. Numeric score validation checks
Decimal finiteness and exact numeric magnitude before conversion; oversized
integers and signaling NaNs produce `INVALID_SCORE`. No generic overflow catch
hides unrelated programming errors.
