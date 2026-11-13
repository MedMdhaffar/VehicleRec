# DB1 implementation plan for the vehicle service

Plan date: 2026-10-08. This plan implements the service decisions in [db1_gap_resolution.md](db1_gap_resolution.md) against DB1 development contract v0. It is design-only in this turn: no runtime code, SQL, database, model, transport, commit or remote operation was changed.

## Current staged implementation status

The initial design-only statement above describes the original planning turn.
On 2026-10-08, explicit scope authorized targeted P1 corrections followed by
P2a foundations without freezing T1 or wiring inference loops. See
[executed results and exact P2b entry points](db1_p2a_results.md) and
[foundation usage](../../vehicle/common/README.md). P2b instrumentation, P3 rows,
P4 database and P5 transport remain unimplemented by this change.

## Delivery rules

- Keep the supplied `db1_contract.zip` and every contained SQL/checker/diagram file unchanged.
- Treat [T1_PHASE1_MAPPING.md](../../T1_PHASE1_MAPPING.md) and the existing `contracts/` package as historical evidence. New executable specifications live under `contracts/db1/` so old private-schema fixtures cannot be mistaken for DB1 rows.
- Build pure decisions before producer integration, and producer rows before database or transport work.
- Use one-camera runs, one shared vehicle observation, run-wide non-reused BIGINT track IDs and per-row stable UUIDs.
- Do not emit make/model while unavailable. Under DB1 v0, omit true no-plate rows rather than fabricating attempts/model/time.
- T2 remains paused. P1 creates the T1 contract candidate; senior validation and an explicit freeze decision complete the T1 contract gate. P2/P3 are separately staged producer implementation after that gate. P4–P5 are T3 or later persistence work.

## Phase map

| Phase | Outcome | Task boundary | Depends on |
|---|---|---|---|
| P1 | DB1 row specification and pure fixtures | T1 contract candidate | Current source evidence and DB1 v0 files only |
| P1 review gate | Senior validation, revisions and explicit freeze decision | T1 completion | Passing P1 fixtures and identified reviewers; fixture success alone is insufficient |
| P2 | Shared observation context and producer instrumentation | Separately staged producer implementation | Frozen T1 contract; authorized context interface shape; no database required |
| P3 | Row construction and validation | Separately staged producer implementation | Frozen T1 contract and P2; catalogue/model manifests supplied as fixtures |
| P4 | Isolated database adapter checks | T3 | P3; separately provisioned disposable PostgreSQL 16 and normal role |
| P5 | Transport and recovery integration | T3/later | P3–P4; platform ingest/object-store/retention interfaces |

## P1. DB1 row specification and pure fixtures

### Purpose

Create the smallest executable specification for the exact DB1 row shapes and service rules. This phase does not import runtime models, open videos, use PostgreSQL or write object storage.

### Files likely affected

- New `contracts/db1/README.md` — scope, DB1 v0 provenance, accepted-vs-proposed boundary.
- New `contracts/db1/rows.v0.schema.json` — logical input and four fact-row shapes using DB1 column names; make rows are specified but not emitted by current capability.
- New `contracts/db1/diagnostics.v1.schema.json` — minimal sidecar from G12.
- New `contracts/db1/catalogue_requirements.v0.json` — exact shared-class, colour, category and model natural IDs; marks external/domain-gated entries.
- New `contracts/db1/model_registrations.v0.json` — detector/OCR/local-colour/agent registration conventions and artifact/config fingerprints.
- New `contracts/db1/fixtures/` — positive and negative row, association, time, emission and retry cases.
- New `contracts/db1/validate_fixtures.py` — pure structural/semantic validator with no database imports.
- Existing [db1_gap_resolution.md](db1_gap_resolution.md) and this plan only when decisions need synchronized clarification.

Do not rewrite `contracts/vehicle_event_v1.schema.json` or its fixtures. They document the historical private contract.

### Work items

1. Define pure types for `RunContext`, `FrameReceipt`, `VehicleObservation`, plate attempt ledger, colour evidence, model natural ID, media metadata and finalized DB1 row snapshots.
2. Encode exact DB1 types/nullability plus service constraints not enforced by SQL: context equality, class group, local date derivation, route/model task, media ownership, state/score compatibility, stable UID equality and one-initial-result policy.
3. Implement the plate/vehicle geometric association function from G3 as a pure fixture oracle.
4. Define plate emission matrix: read, unreadable, omitted no-plate, ambiguous/unassociated crop and delivery retry.
5. Define colour emission matrix: decided local, decided agent override, undecided after attempts, error after attempt, and omission with zero attempts/unavailable capability.
6. Define canonical immutable payload serialization and SHA-256 equality behavior.
7. Record the minimal proposed no-plate contract adjustment in the package README without changing supplied SQL or treating it as accepted.

### Meaningful acceptance checks

- The JSON schemas validate against their metaschema and reject unknown properties, non-finite numbers, invalid UUIDs/timestamps and wrong DB1 column names.
- Positive rows cover every emitted fact and every nullable evidence path.
- Negative rows cover cross-run/camera/org parentage, non-vehicle class, wrong model task, grouped-colour splitting, unapproved category aliases, missing attempts, changed-payload same UID, local path as media reference and media context mismatch.
- Association fixtures deterministically cover unique, absent and ambiguous candidates and reassociation segmentation.
- Time fixtures derive each row's local date independently, including an Africa/Tunis UTC-midnight crossing and a DST-capable zone; invalid timezone/date coverage rejects.
- `fact_vehicle.ts` equals `first_ts`; plate and colour times select their defined supporting receipts.
- True no-plate produces an omission diagnostic and no v0 plate row. No fixture fabricates `attempts=1`, OCR model or crop time.
- Text fixtures require structural validity for local private reads and explicit `text_complete=true` for agent reads; lossless common normalization preserves punctuation/scripts and leading zeros.
- Running the pure validator has no PostgreSQL client import, network call, model import or filesystem mutation outside test temporaries.

### Exit condition

Every decision in G1–G12 has at least one positive and one material negative fixture where applicable. The package can explain exactly why a proposed logical result is emitted, omitted, rejected or quarantined. This yields a review candidate; senior validation and an explicit freeze decision remain the T1 contract gate.

## P2. Shared observation context and producer instrumentation

### Purpose

Make current producers capable of collecting truthful evidence for P3 without constructing or inserting DB1 rows yet.

### Files likely affected

- New `vehicle/common/db1_context.py` — immutable run context, run-wide track allocator and tracker epoch.
- New `vehicle/common/frame_receipt.py` — paired UTC/monotonic receipt plus media PTS and discontinuity state.
- New `vehicle/common/vehicle_observation.py` — shared parent lifetime and closure cause.
- New `vehicle/common/plate_association.py` — pure association policy from P1.
- `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py` and `schemas.py` — attach shared lifetime, receipt evidence and consensus class authority.
- `vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py` — use parent association, record every submitted/completed/failed crop operation and selected crop receipt.
- `vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py` and `schemas.py` — preserve actual route/model natural ID and crop receipt rather than processing time as evidence time.
- `vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` and `vehicle/license_plate_service/service.py` — one run context across recoverable reconnects; close lifetimes and advance epoch.
- `vehicle/vehicle_color_service/vehicle_color_service/schemas.py`, `detector.py` and `temporal.py` — valid-sample and best-crop frame/time provenance, actual attempt outcomes and selected route.
- `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/service.py` — retain explicit manufacturer-unavailable capability; no make inference implementation.

Paths may be consolidated if a suitable existing common module is found during implementation; the logical ownership above must remain.

### Work items

1. Capture `FrameReceipt` immediately after every successful decode and before detector/inference calls. Preserve PTS separately for file runs.
2. Allocate a service track ID once for each shared vehicle lifetime; never expose raw tracker ID as DB1 identity.
3. Drive class from `VP.aggregate_vehicle_type` on the shared lifetime. Retain raw class inputs and aliases in diagnostics.
4. Associate plate crops to active vehicles using same-frame boxes. Segment on parent change and discard ambiguous evidence from analytical selection.
5. Record plate operation submission before worker dispatch, then success/failure completion. Count submitted crop operations once regardless of split OCR calls or HTTP retries.
6. Extend the strict plate-agent response with `text_complete`; responses lacking an affirmative value cannot create a read.
7. Add colour sample frame/time and best-crop frame/time; preserve local evidence when an agent overrides or fails.
8. Close active lifetimes on track loss, outage, reset, EOF and service stop. Keep the run over recoverable reconnects and start a new run after process restart or invalid clock epoch.
9. Emit versioned in-memory diagnostics records, with no DB1 row construction yet.

### Meaningful acceptance checks

- Unit tests prove receipt capture precedes inference and all observations from one frame share the same receipt object.
- Raw tracker ID reuse yields different service IDs; recoverable reconnect preserves run ID but closes old lifetimes and increments epoch.
- Independent plate/vehicle numeric IDs never associate by equality.
- Failed futures are present in the plate attempt ledger; whole/left/right OCR calls do not inflate DB1 attempt count.
- Agent colour override points to the actual submitted best crop and its receipt; failed fallback retains a usable local decision.
- Clock regression/discontinuity quarantines affected observations and starts a clean run/epoch without clamping timestamps.
- Manufacturer capability remains unavailable and generates no make row intent.

### Exit condition

A synthetic frame sequence can close a truthful shared vehicle observation with class, optional associated plate evidence, optional colour evidence, timestamps and diagnostics. It performs no database or transport operation.

## P3. Row construction and validation

### Purpose

Convert finalized observations into immutable DB1 row snapshots using only explicit resolution results. This is producer-conformance implementation after the T1 contract is frozen, not contract validation.

### Files likely affected

- New `vehicle/common/db1_rows.py` — deterministic row builders and omission/quarantine results.
- New `vehicle/common/db1_validation.py` — semantic validation against the P1 specification.
- New `vehicle/common/db1_catalogue.py` — interface for supplied natural-ID-to-key resolution results; no database implementation.
- New `vehicle/common/db1_diagnostics.py` — minimal sidecar construction.
- New `vehicle/common/db1_snapshot.py` — stable UID allocation, canonical payload and hash; in-memory test store only in T1.
- Relevant producer service adapters to call row builders only at lifetime closure.
- `contracts/db1/fixtures/` and validator tests expanded with real producer-shaped evidence.

### Work items

1. Build `fact_vehicle` first from a finalized shared observation and resolved run/class keys.
2. Build at most one initial plate and colour row linked to that parent. Omit unavailable make/model and v0-inexpressible no-plate.
3. Derive every row's local `date_key` from that row's `ts` and the bound camera timezone; verify supplied date lookup matches.
4. Select model key from the actual final route and reject missing/task-mismatched resolution.
5. Accept nullable media keys. When present, require adapter-supplied media metadata to match org/run/camera/kind/selected evidence before snapshot finalization.
6. Allocate each row UID once, freeze its canonical payload/hash and classify later delivery as identical retry, conflict or new logical result.
7. Produce a diagnostic sidecar for emitted, omitted and quarantined results without plate text or local paths.

### Meaningful acceptance checks

- Producer-shaped fixture sequences yield exact DB1 column dictionaries and no private-star-schema columns.
- Parent and child tuples match exactly; child construction before valid parent snapshot rejects.
- Class/colour/category/model resolution uses natural IDs and never assumes seed keys.
- Unknown/van class without provisioned key quarantines the parent and therefore its children; grouped colours remain exact.
- Read/unreadable plate rows use selected crop time/model/count; no-plate is omitted with explicit reason.
- Local/agent colour rows use correct time/model/score and honest sample count; zero-attempt colour is omitted.
- Identical retries retain UID and hash; changed immutable content under one UID is a conflict; explicit reprocessing gets a new UID.
- Media failure yields null media FK without leaking a local path; a mismatched media context rejects.
- No fact row contains raw tracker IDs, reset epochs, frame numbers, PTS, URLs, credentials or exception dumps.

### Producer-integration exit condition

P3 completes when a closed synthetic observation produces reviewable, immutable DB1 v0 row snapshots and omission diagnostics without a database. This demonstrates producer conformance to the previously frozen contract; it does not freeze the contract, deploy integration or approve unresolved external meanings.

## P4. Isolated database adapter checks

### Purpose

Implement and verify the narrow PostgreSQL adapter in an isolated environment. This belongs to T3, not T1.

### Files likely affected

- New `vehicle/common/db1_adapter.py` — transaction-scoped RLS context, lookups and inserts behind one module.
- New `tests/db1_integration/` — disposable-database integration tests.
- Test-only setup copied or safely orchestrated from the supplied contract without editing it.

Do not invoke the supplied `check_contract.py`: it forcibly drops fixed database and role names. Use a separately named, explicitly provisioned disposable database and role with cleanup scoped to that test fixture.

### Work items and checks

- Resolve camera SCD2 version, vehicle lane, run, class/model/catalogue/date and media keys under `SET LOCAL app.org_id` using a normal `NOBYPASSRLS` role.
- Insert parent then children in one adapter-controlled transaction where appropriate; use per-row UID duplicate guards only after application payload equality checks.
- Verify missing org is fail-closed, pooled transactions cannot leak org, cross-org/camera/run facts reject, and a file run requires input media.
- Add explicit nonexistent-manufacturer-with-null-vehicle-model rejection to confirm the existing direct FK.
- Prove adapter rejection for cross-org/cross-run/wrong-kind media references even where supplied SQL lacks a composite fact-to-media constraint.
- Test identical retry, same-UID changed-payload conflict before SQL, transaction rollback and concurrent first delivery.

### Exit condition

The adapter passes isolated PostgreSQL 16 tests as a normal role and has no database logic in producer modules. This does not authorize production connection or deployment.

## P5. Transport and recovery integration

### Purpose

Deliver immutable row snapshots reliably and recover after process failure. This belongs to T3 or later work.

### Files likely affected

- New `vehicle/common/db1_outbox.py` or an existing approved transport module if one is available then.
- Service startup/shutdown hooks for outbox recovery, run/lifetime recovery policy and acknowledgements.
- Object-storage client boundary and media registration adapter.
- Operational retention configuration for outbox, diagnostics, plate text and orphan evidence.

### Work items and checks

- Persist UID, canonical payload and hash before first delivery; fsync/commit atomically and acknowledge only after downstream commit.
- On restart, replay the exact snapshot without regenerating IDs/timestamps. Until run-state recovery is also durable, close the old run and create a new run for new frames.
- Upload evidence before media registration/reference, retry safely by deterministic object identity and reconcile uploaded-but-unreferenced objects.
- Bound retry/backoff and quarantine changed-payload conflicts; never silently replace immutable content.
- Exercise crash points before snapshot, after snapshot, after upload, after DB commit and before acknowledgement.

### Exit condition

Crash/restart and delivery tests prove no duplicate logical rows, no changed-payload acceptance, no UID/time regeneration, correct acknowledgement ordering and bounded evidence cleanup.

## External dependency gates

The plan continues without supervisor input for routine implementation choices. Only these gates can block the relevant behavior:

| Gate | Blocks | Interim behavior |
|---|---|---|
| Platform org/camera/run and model-resolution interface | P4 production-shaped adapter; P5 deployment | P1–P3 use explicit resolution fixtures and never infer authority. |
| Shared class rows `van` and `unknown` | Emitting those `fact_vehicle` rows | Quarantine affected parent/children; do not coerce class. |
| Faithful no-plate contract adjustment | Stored `outcome='no_plate'` | Omit v0 row and emit diagnostic reason. |
| Authoritative plate-category definitions/examples | Populating uncertain category keys | Keep category FK null; independently complete plate text may still be stored. |
| Object-store and retention policy | P4/P5 media persistence | Nullable media FKs; no local paths in DB1. |

## Smallest first implementation phase

Start with P1 only. The exact initial change set should be:

- `contracts/db1/README.md`
- `contracts/db1/rows.v0.schema.json`
- `contracts/db1/diagnostics.v1.schema.json`
- `contracts/db1/catalogue_requirements.v0.json`
- `contracts/db1/model_registrations.v0.json`
- `contracts/db1/fixtures/`
- `contracts/db1/validate_fixtures.py`

Its acceptance command should run only the pure fixture validator. It must verify schemas, row semantics, association, local dates, emission/omission, route/model attribution, media ownership metadata and immutable retry equality. The command must require no application dependency, model file, credential, network, database or supplied contract execution.

No runtime file should change in this first phase. That keeps the first review small: reviewers can settle the exact row behavior before instrumentation reshapes the producers.
