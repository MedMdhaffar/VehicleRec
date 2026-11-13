# DB1 gap resolution for the vehicle service

Decision date: 2026-10-08. Target interface: supervisor-supplied DB1 development contract v0 (2026-10-08). This document turns the gaps in [db1_alignment.md](db1_alignment.md) into service decisions. It does not change the supplied contract, freeze T1, or claim supervisor approval. The earlier private star-schema contract remains historical source evidence only.

## Decision boundary and source notation

The DB1 SQL and README inside [db1_contract.zip](../../db1_contract.zip) define the target tables and keys. The archive remains unchanged. Current runtime evidence is cited by repository-relative file and symbol:

- `VP`: `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py` — `VehiclePipeline`, `aggregate_vehicle_type`, `update_track_vehicle_type`.
- `VS`: `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/schemas.py` — `VehicleProfile.to_dict`.
- `VA`: `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/service.py` — `VehicleAnalysisService`.
- `VT`: `vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/classification.py` — `PUBLIC_VEHICLE_TYPES`, `normalize_streetdet_class_id`.
- `PR`: `vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py` — `TrackState`, `RecognitionAttempt`, crop selection and recheck logic.
- `PP`: `vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py` — `PrototypePlatePipeline.process_crop`.
- `PS`: `vehicle/license_plate_service/service.py` — `run_video_analysis`, `RTSPManager`.
- `LR`: `vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` — `RTSPFrameReader`, `_retire_stale_tracks`, `live_track_results`.
- `PD`: `vehicle/license_plate_service/run_video_pipeline/data_capture.py` — `ReviewStore.capture`, `mark_track_final`.
- `CD`: `vehicle/vehicle_color_service/vehicle_color_service/detector.py` — `ColourPipeline`, `_observe`, `_fallback_if_needed`, `_public_profile`.
- `CT`: `vehicle/vehicle_color_service/vehicle_color_service/temporal.py` — `SUPPORTED_COLOURS`, `decide_colour`.
- `CS`: `vehicle/vehicle_color_service/vehicle_color_service/schemas.py` — `VehicleProfile`.
- `VC`: `vehicle/vehicle_color_service/vehicle_color_service/config.py` — `TAXONOMY`, colour and agent configuration.

Implementation phases are numbered in [db1_implementation_plan.md](db1_implementation_plan.md): P1 row specification and pure fixtures; P2 shared observation context and instrumentation; P3 row construction and validation; P4 isolated database adapter checks; P5 transport and recovery.

## Decisions made

### G1. Authorized organization, camera, run and shared-key context

| Record | Resolution |
|---|---|
| Current behavior | API views allocate unrelated hexadecimal `job_id` values; `PS.RTSPManager.start` allocates a plate session UUID and truncates/falls back to `cam-1`. No producer carries an authorized `org_id`, camera SCD2 key, lane key, registered model key or camera timezone. Uploaded plate, colour and classification jobs are independent. |
| Chosen solution and rationale | A caller-authenticated service context supplies `org_id` and registered `camera_id`; request body values alone are not authority. Resolve exactly one camera version where `camera_id` and `org_id` match and `valid_from <= started_at < valid_to`, treating null `valid_to` as open-ended. Reject zero or multiple matches. Bind that `camera_key`, its timezone, the `vehicle` lane, and one service-generated text `run_id` for the run lifetime. A camera-version change closes the old run and starts a new one. A file run must first resolve its input clip media key. This follows DB1 SCD2 and one-camera-run semantics without duplicating shared tables. |
| Producer fields and DB1 columns | New `RunContext(org_id, run_id, camera_id, camera_key, camera_timezone, lane_key, run_key, detector_model_key, source_kind, source_media_key, started_at)` feeds `dim_run` and every fact's `org_id`, `run_key`, `camera_key`; each row derives its own `date_key`. `service_version`, `ended_at` and bounded service `end_reason` are explicit run lifecycle fields. |
| Decision owner | Service decision for context propagation, version selection and fail-closed behavior. Platform dependency for authoritative org/camera authorization, SCD2 records, natural-ID resolution and production run lifecycle endpoint. |
| Validation | Pure fixtures reject caller/body org mismatch, missing/ambiguous camera versions, camera version outside effective interval, non-vehicle lane, file run without input media and context changes within a run. P4 uses a normal RLS role and verifies no-org/cross-org invisibility and transaction isolation. |
| Phase and dependencies | P1 specifies lookup inputs/results; P2 propagates immutable context; P3 validates it; P4 exercises real lookups. Depends on platform-provided authorized org/camera identity and registered shared rows. |

For pooled database connections, every adapter transaction uses `BEGIN; SET LOCAL app.org_id = <authorized org>; ...; COMMIT`. A checkout/check-in hook clears unexpected session state, but correctness relies on `SET LOCAL` inside every transaction. No query or write occurs before it. Superuser and `BYPASSRLS` roles are forbidden for service operation.

The service run-end vocabulary is `end_of_input`, `service_stop`, `camera_outage`, `clock_discontinuity` and `process_error`. Normal file EOF maps to `end_of_input`; configured frame/time limits and an operator stop map to `service_stop`; only a terminal camera outage closes the run as `camera_outage`. A recoverable reconnect closes active vehicle lifetimes but leaves the run open. Unexpected decode/runtime failure maps to `process_error`. These values are service conventions because DB1 leaves `end_reason` as unconstrained text.

### G2. Shared vehicle observation, run-wide identity and reset handling

| Record | Resolution |
|---|---|
| Current behavior | `VP.VehiclePipeline` and `CD.ColourPipeline` each key profiles by their own tracker integer. `PR.TrackState` tracks plates, not vehicles. `LR` prefixes plate IDs with a connection generation, while state maps can merge reuse until retirement. None supplies a non-reused vehicle ID across resets. |
| Chosen solution and rationale | One shared vehicle tracker owns a `VehicleObservation`. On lifetime creation, a run-scoped monotonic allocator assigns the next positive signed BIGINT. It never reuses a value within the run. Store raw tracker ID, tracker epoch and raw lifetime sequence only in diagnostics. Classification consensus from `VP.aggregate_vehicle_type` is the sole class authority because it aggregates evidence for the lifetime; colour's latest detector label remains diagnostic. A recoverable reconnect keeps the run open, closes all active lifetimes with `camera_outage`, increments the tracker epoch, recreates tracking and allocates fresh service IDs. Explicit reset closes lifetimes with `tracker_reset`. Terminal outage, EOF or stop closes both lifetimes and run. A process restart starts a new run until P5 recovery exists. |
| Producer fields and DB1 columns | Allocated ID → `fact_vehicle.track_id` and all recognition `track_id` values. Consensus label/score → `class_key`/`class_conf`. Shared first/last sightings and distinct observation count → `first_ts`, `last_ts`, `ts`, `frames_observed`. Raw ID/epoch/final reason stay in diagnostics. |
| Decision owner | Service decision. Platform coordination is required only for missing shared class members described in G8. |
| Validation | Fixtures cover raw ID reuse in one epoch, reuse after reset, reconnect closure, monotonic allocation, BIGINT bounds, no recognition before its parent, and identical raw IDs from independent trackers. Assertions ensure every recognition inherits the parent's exact run/org/camera/track tuple. |
| Phase and dependencies | P1 defines lifetimes and allocator invariants; P2 implements shared context/tracking instrumentation; P3 constructs parent/child rows. Depends on integrating the recognition paths around one vehicle observation. |

The allocator is in-memory during P2. Because a process restart creates a new `run_id`, reuse after a crash cannot collide within the old DB1 run. P5 may resume a run only after durably restoring its allocator high-water mark and open-lifetime state.

### G3. Plate-to-vehicle association and reassociation

| Record | Resolution |
|---|---|
| Current behavior | Plate tracking and vehicle tracking are independent. `PR` receives plate boxes and plate track IDs; no inspected code establishes a vehicle parent. Matching equal numeric IDs would fabricate identity. |
| Chosen solution and rationale | Associate each plate crop on the same decoded frame against active shared vehicle boxes. A vehicle is eligible when the plate centre lies inside or on its boundary and at least 80% of plate area is contained by it. Score eligible candidates as `0.7 * containment + 0.3 * (1 - distance)`, where `distance` is Euclidean distance from the plate centre to `(vehicle centre-x, vehicle top + 0.75 * vehicle height)`, divided by the vehicle-box diagonal and clipped to `[0,1]`. Accept one candidate, or the best candidate only when it leads the runner-up by at least 0.10; otherwise mark the crop ambiguous and do not use it in an analytical recognition. Zero-area plate/vehicle boxes are ineligible. This geometry is deterministic, favours physical containment, and explicitly rejects overlaps between nearby vehicles. |
| Producer fields and DB1 columns | Accepted association supplies the parent's `track_id`, `org_id`, `run_key`, `camera_key` to a future `fact_plate_read`; selected crop frame/time supplies `ts`; plate attempt evidence supplies outcome/text/category/confidence/count/crop fields. Association score, candidate IDs and ambiguity reason remain diagnostics. |
| Decision owner | Service decision. Thresholds are versioned algorithm configuration and may be tuned with labelled data without a platform schema change. |
| Validation | Pure geometry fixtures cover one candidate, no candidate, nested/overlapping vehicles, exact ambiguity margin, plate at a boundary and stale vehicle boxes. Labelled-video evaluation must report false association and rejection rates before production enablement. |
| Phase and dependencies | P1 specifies the pure function and fixtures; P2 supplies same-frame boxes and receipt time; P3 admits only associated attempts. Depends on shared vehicle tracking and preserved frame indices. |

Association is per crop, not per coincident tracker number. Crops from one plate track may change vehicle candidates while observations are active. If the accepted parent changes, close the old plate-recognition segment and start a new segment with empty attempts; never move accumulated attempts or an allocated row UID to the new parent. Ambiguous crops do not advance either segment. Once a vehicle closes or a finalized DB1 row UID is allocated, it is immutable and cannot be reassociated.

### G4. Recognition row grain and emission

| Record | Resolution |
|---|---|
| Current behavior | `PR.TrackState` keeps multiple crop attempts and a cached selected attempt. `CD` updates profiles per frame and finalizes a temporal decision. `VA` rejects manufacturer enablement. The earlier draft required exactly one row of each recognition type, but DB1 permits many. |
| Chosen solution and rationale | Initial release emits at most one finalized selected plate row and one finalized colour row per vehicle lifetime. It never writes per-frame results. The service-internal logical-result ordinal is fixed at `1`; DB1 permits future additional rows with new ordinals/UIDs without changing keys. Manufacturer/make rows are omitted while capability is unavailable. A colour row is emitted only if at least one colour inference operation ran. With an accepted catalogue label it uses state `decided`; after real attempts but no accepted label it uses `undecided`; an operation-level failure with no usable result uses `error`. Disabled/unavailable colour or no usable crop with zero attempts produces no row. |
| Producer fields and DB1 columns | Final plate selection → one `fact_plate_read`; final colour selection → one `fact_vehicle_colour`; unavailable manufacturer → no `fact_vehicle_make`. `CD._colour_attempts` determines attempted status, `len(_colour_samples)` maps to `samples`, final source controls model and timestamp. Omission reason and capability snapshot remain diagnostics. |
| Decision owner | Service decision. The true `no_plate` representation needs the G7 platform adjustment; interim omission is a service compatibility rule. |
| Validation | Emission matrix covers known, attempted-undecided, runtime error, disabled, unavailable, no crop, local decision, agent override and absent manufacturer. It asserts no active/per-frame row, at most one initial row per attribute/lifetime, and a new UID for any future new logical result. |
| Phase and dependencies | P1 defines the matrix; P2 records attempts/capabilities; P3 emits rows. Future multi-result policies are later work and do not block initial T1. |

“One initial row” is a producer rule, not a claim that DB1 enforces uniqueness. Delivery of the same logical row reuses its UID. A later reprocessing or explicitly requested new recognition is a new logical result and receives a new UID; it is not a retry.

### G5. Absolute time, replay time and camera-local dates

| Record | Resolution |
|---|---|
| Current behavior | `VP` stores host monotonic first/last values but omits them from `VS.to_dict`; `PR` stores frame indices and `PP` timestamps after processing; `CD` tracks frame indices but no supporting sample times. Current file pipelines expose FPS/media position separately and do not provide trustworthy capture UTC. |
| Chosen solution and rationale | At every successful decoded-frame receipt, before inference, capture one UTC `timestamptz`, one monotonic reading and media PTS when available. All observations on that frame reference the immutable receipt record. Live and file runs initially use receipt UTC; file diagnostics declare `time_basis=host_replay_receipt` and retain PTS without converting it to UTC. Vehicle first/last timestamps use first/last contributing vehicle frame. Plate timestamp uses the selected supporting crop frame. Local colour timestamp uses the last valid sample included in its final aggregate. An agent override uses the timestamp of the best vehicle crop actually sent to the agent, requiring that crop's frame/time to be retained. `fact_vehicle.ts = first_ts`. Each fact independently derives `date_key` by converting its own `ts` through the bound camera IANA timezone. |
| Producer fields and DB1 columns | New `FrameReceipt(frame_index, utc, monotonic, media_pts)` feeds all fact `ts` fields, vehicle first/last times and each fact's local `date_key`. `CD._best_crop` gains supporting frame/time metadata; each valid colour sample gains frame/time provenance. PTS and basis remain diagnostics. |
| Decision owner | Service decision for initial host-receipt basis and row timestamp rules. Platform owns camera timezone correctness and date coverage. Source-capture time is deferred until a trustworthy source contract exists. |
| Validation | Pure timezone fixtures cover Africa/Tunis midnight, DST-capable zones, UTC offsets, different vehicle/recognition dates, invalid zone, and dates outside seeded 2024–2035 range. Clock fixtures cover UTC regression, monotonic regression/restart and large UTC/monotonic divergence. No test assumes a one-second threshold. |
| Phase and dependencies | P1 specifies conversions and clock states; P2 captures records and support provenance; P3 derives rows. Depends on registered IANA timezone and `dim_date` coverage. |

Clock discontinuity detection compares consecutive receipt pairs. Any UTC regression, monotonic regression/restart, explicit OS clock-step notification, or `abs(delta_utc - delta_monotonic) > 5 seconds` marks the clock epoch invalid. Five seconds is the initial versioned service default, configurable only at run start and recorded in run diagnostics; it is not a DB1 requirement and does not inherit the old one-second proposal. Affected open lifetimes are closed from their last valid receipt and quarantined from normal row construction; the service starts a new run after a stable epoch. It never clamps, sorts or reconstructs UTC values.

### G6. Stable row UIDs, immutable payloads and retries

| Record | Resolution |
|---|---|
| Current behavior | `PP` allocates `sample_id` per crop; `PD` makes plate prediction records append-only, but there is no vehicle/colour/make row identity or complete DB1 row snapshot. DB1 recommends `ON CONFLICT(uid) DO NOTHING`, which also ignores changed payloads. Earlier fixtures compare messages only in memory. |
| Chosen solution and rationale | Allocate UUIDv4 once when a logical DB1 row is finalized. Canonicalize its immutable natural payload using sorted JSON keys, explicit nulls, RFC3339 UTC strings, decimal-safe JSON numbers and no database-generated keys; store SHA-256 of those canonical bytes. The local outbox record contains row kind, UID, payload and hash. An identical UID/hash retry is a no-op. Same UID with a different hash or payload is a conflict and is quarantined before SQL. Database `DO NOTHING` remains the final duplicate guard, not the equality mechanism. `PP.sample_id` stays crop-attempt identity and is never reused as vehicle/colour UID. |
| Producer fields and DB1 columns | One stable `uid` for each `fact_vehicle`, `fact_plate_read`, `fact_vehicle_colour` or future make row. The immutable payload includes all non-generated columns of that row. Diagnostic record links row UID to selected crop sample ID without conflating them. |
| Decision owner | Service decision. P5 chooses the durable transport/outbox implementation and operational retention. No DB1 column change is required. |
| Validation | Fixtures prove key-order-insensitive equality, exact string/time identity, numeric normalization, null handling, identical retry, changed score/text/context conflict, reused UID across logical rows, and new logical result/new UID. Restart tests later restore pending snapshots without regenerating UID or time. |
| Phase and dependencies | P1 defines canonicalization/equality fixtures; P3 constructs immutable snapshots; P5 implements durable outbox/restart/ack behavior. Depends on a secured local persistence location and retention policy. |

P1–P3 may use an in-memory snapshot store in tests. The P5 design uses a small append-only outbox or transactional local store with fsync/atomic publication, acknowledgement state and payload hashes. It must survive process restart; implementation is deliberately outside T1 producer-row construction.

### G7. Plate outcomes, attempts and the truthful `no_plate` gap

| Record | Resolution |
|---|---|
| Current behavior | `PR` counts completed `RecognitionAttempt` objects, while submissions or failed futures can be missing. `PP` assigns a processing-completion timestamp and always describes a selected crop result. DB1 requires `attempts >= 1`, non-null OCR `model_key` and non-null crop-based `ts` even for outcome `no_plate`. A vehicle with no detected plate truthfully has zero OCR attempts, no OCR model invocation and no crop timestamp. |
| Chosen solution and rationale | `read`: at least one submitted crop recognition operation and accepted complete canonical text from the selected attempt. `unreadable`: at least one submitted crop recognition operation, a detected crop and no accepted text after attempts settle; runtime failure detail stays diagnostic. `no_plate`: the vehicle lifetime ended after plate-detection observation but produced no associated usable plate crop and therefore has attempts `0`, null OCR model and no crop. Under unchanged DB1 v0, omit this row because any insert would fabricate evidence. Do not relabel it unreadable, set attempts to one, name a model that did not run, or invent a crop timestamp. |
| Producer fields and DB1 columns | Submitted crop operation ledger → `attempts`; selected `RecognitionAttempt.result` → `outcome`, `plate_text`, `plate_category_key`, `conf`; selected attempt frame receipt → `ts`; actual executed route registration → `model_key`; uploaded selected crop → `crop_media_key`; attempt quality → `crop_score`. No-plate omission is recorded in diagnostics. |
| Decision owner | Read/unreadable semantics and interim omission are service decisions. Faithful storage of `no_plate` is a platform-contract change. |
| Validation | Matrix covers accepted text, rejected text, OCR exception, failed future, several attempts, no detected plate, unassociated/ambiguous plate, crop upload failure and agent/local routes. Assert attempts count submitted crop operations once, not low-level whole/split calls or HTTP retries. Assert no-plate creates no v0 row. |
| Phase and dependencies | P1 defines outcomes/counting and the platform proposal; P2 adds submission/outcome ledger; P3 constructs read/unreadable rows and omission diagnostics. Faithful no-plate insertion waits for platform response. |

Smallest proposed DB1 adjustment, kept separate from accepted v0:

1. Make `fact_plate_read.model_key` nullable.
2. Change attempts constraint to `attempts >= 0`.
3. Define `ts` as the outcome evidence time: selected crop time for `read`/`unreadable`, vehicle last inspected frame time for `no_plate`.
4. Add checks: `no_plate` requires `attempts = 0`, null `model_key`, null text/category/confidence/crop/score; `read` and `unreadable` require `attempts >= 1` and non-null `model_key`; `read` alone requires text.

No supplied SQL is edited. Until the platform accepts equivalent semantics, analytics must derive “no plate row observed” from the parent plus diagnostics/capability context rather than treating absence as a stored `no_plate` fact.

Initial text admission is also fixed. The local private route produces `read` only when its parser/fusion result is structurally valid and supplies a nonempty complete normalized plate. Common normalization applies Unicode NFC, converts Unicode decimal digits to ASCII, collapses whitespace, uppercases ASCII letters, and preserves other scripts and punctuation; controls, replacement characters and unreadable placeholders reject completeness. The agent response must gain an explicit boolean `text_complete` field in its strict structured schema and prompt. An existing agent response without `text_complete=true` cannot produce `read`, even if its current status is accepted; it produces `unreadable` after a real crop attempt. Non-private complete text may use the same lossless common normalization without asserting a category.

For `read`, the selected attempt remains `PR.cached_attempt` after applying the existing accepted-result replacement policy. For `unreadable`, only submitted, associated, accepted crops with a registered recognizer that actually executed are eligible. Prefer finite nonnegative measured quality, highest first; null quality is unmeasured and falls back deterministically to earliest submission ordinal. Ties use submission ordinal then lexical attempt ID. Invalid measured quality is ineligible. Keep crop, receipt and executed-model evidence together from the selected attempt; never borrow a route from another attempt. `failure_before_model` applies when no eligible operation executed a recognizer. Null quality alone does not imply this omission.

### G8. Class, colour and plate-category vocabularies

| Record | Resolution |
|---|---|
| Current behavior | `VT` normalizes camper→van, cyclist→bicycle and tractor→truck; low confidence becomes unknown. DB1 seeds vehicle classes bicycle/car/motorcycle/bus/truck only, while `fact_vehicle.class_key` is mandatory. `CT.SUPPORTED_COLOURS` groups silver/gray and brown/beige and includes orange/purple; DB1 seed splits grey/silver, has brown, and omits grouped/orange/purple labels. Plate runtime has private/temporary/diplomatic/military/foreign/other/unknown; seed has TN/RS/foreign/diplomatic. |
| Chosen solution and rationale | Preserve exact producer evidence. Request shared `dim_class` additions `van` and `unknown`, both group `vehicle`; keep exact existing bicycle/car/motorcycle/bus/truck mappings. Until those rows exist, quarantine affected vehicle rows instead of coercing class. Keep camper→van, cyclist→bicycle and tractor→truck as versioned producer normalization because they express the current model taxonomy; retain raw class diagnostics. Add vehicle-owned colour members `silver/gray`, `brown/beige`, `orange`, `purple`; exact common labels map directly, null represents undecided. Do not split grouped labels. Do not map private→TN or temporary→RS. Map foreign/diplomatic only after authoritative definitions confirm the same meaning; otherwise leave category null. |
| Producer fields and DB1 columns | Consensus class label → `dim_class.name`/`fact_vehicle.class_key`; final colour label → `dim_colour.name`/`colour_key`; admitted plate category → `dim_plate_category.name`/`plate_category_key`. Raw/model labels and normalization version stay diagnostics. |
| Decision owner | Colour catalogue additions are a vehicle-service catalogue decision for a future service-owned migration. Shared class additions require platform coordination. Plate jurisdiction/category meanings require authoritative domain definitions and labelled examples; this is a genuine external dependency. |
| Validation | Registry fixtures accept exact supported labels, reject unknown catalog entries, preserve grouped strings, reject prohibited grouped-to-single mappings, and keep category null when meaning is unverified. Model-labelled examples validate any future category mapping and complete-text normalization. |
| Phase and dependencies | P1 publishes required catalogue manifest and lookup fixtures; P3 enforces exact mapping. Platform must provision shared class rows. Domain review gates category-key population, not plate text storage when text is independently complete. |

The earlier plate normalization draft remains candidate technical evidence, not an approved jurisdiction policy. T1 can implement deterministic, lossless common preprocessing and store only demonstrably complete text, while category remains nullable.

### G9. ML-model registration and route attribution

| Record | Resolution |
|---|---|
| Current behavior | `PP.UnifiedPlateResult.model_version` is a descriptive string; `CD` runs local HSV/temporal logic with optional OpenRouter agent; run/classification code loads detector artifacts from paths. DB1 seed names are examples and do not prove that those exact artifacts/routes ran. Every recognition requires `model_key`; `dim_run.model_key` is the detector. |
| Chosen solution and rationale | Maintain a service model-registration manifest keyed by stable `(model_name, integer version)`. A version identifies immutable artifact hash plus preprocessing/prompt/config contract. Register: the actual vehicle detector as task `detect`; local OCR as task `ocr`; each optional plate agent provider/model/prompt bundle as task `vlm`; local HSV-temporal colour as `vehicle_colour_hsv_temporal`, task `classify`, with null weights path and versioned algorithm/config hash; and each colour agent route as task `vlm`. The final selected route determines recognition `model_key`; run detector determines `dim_run.model_key`. Compound local marker/category logic stays diagnostic unless it independently produces the selected value and receives its own registered route. Seed rows are usable only if their registration metadata matches the loaded route manifest exactly. |
| Producer fields and DB1 columns | Loaded artifact/config identity → `dim_run.model_key`; selected plate route → `fact_plate_read.model_key`; selected local/agent colour route → `fact_vehicle_colour.model_key`. `dim_model` natural fields are name/version/task/base_model/weights_path; hashes/config/source stay in the service manifest. `vehicle_model_key` always means car model and is unrelated. |
| Decision owner | Service decides registration names, version bumps and selected-route attribution. Platform must provision/resolve `dim_model` rows and define the production registration interface. |
| Validation | Startup validation refuses missing, ambiguous or task-mismatched registrations and artifact/config hash drift. Fixtures cover local-to-agent override, agent failure with retained local result, detector vs recognizer keys and ML-model/car-model confusion. |
| Phase and dependencies | P1 defines manifest shape and lookup cases; P2 records actual routes/config hashes; P3 resolves selected natural identity; P4 verifies keys. Depends on platform model registration/provisioning. |

### G10. Colour timestamp, state and agent override

| Record | Resolution |
|---|---|
| Current behavior | `CD._observe` appends valid temporal samples but does not retain their frames/times. `_fallback_if_needed` sends `_best_crop` to an agent and may overwrite local label/confidence/source; `_best_crop` lacks supporting frame/time. `_public_profile` exposes a decision string and valid sample count. |
| Chosen solution and rationale | Extend colour observations with frame/time provenance and retain best-crop frame/time. A local final result uses state `decided`, local model key, last contributing valid sample time, exact accepted colour and winner-share score. A local undecided result after attempts uses state `undecided`, local model key, last attempted crop time, null colour/confidence and retained valid sample count. A terminal local operation failure uses `error`. An accepted agent override uses state `decided`, agent model key, the submitted best-crop time and agent score, while `samples` still records retained valid local samples; the local candidate/share and override reason remain diagnostics. Failed agent fallback never erases a usable local result. |
| Producer fields and DB1 columns | `CD._colour_attempts`, `_colour_samples`, `_colour_decision`, `_colour_source`, best-crop provenance → `ts`, `model_key`, `colour_key`, `conf`, `state`, `samples`. Agent error and local evidence remain diagnostics. |
| Decision owner | Service decision. Colour catalogue availability and agent model provisioning are dependencies covered by G8/G9. |
| Validation | Fixtures cover early/final/short locks, zero valid samples after real attempts, invalid samples, local result above/below fallback threshold, accepted override, missing credential, network failure and best-crop timestamp selection. |
| Phase and dependencies | P1 specifies output states; P2 instruments provenance; P3 constructs rows. Depends on frame receipts and model registrations. |

### G11. Object-storage evidence and media integrity

| Record | Resolution |
|---|---|
| Current behavior | `PD.ReviewStore` writes local JPEG paths and JSONL; `PR.save_track_crops` writes local files. DB1 requires object-store bucket/key metadata and media keys. Its fact media FKs reference only `media_key`; `media_object`'s composite run FK is skipped when nullable components are null, so all org/camera/run relationships are not enforced by SQL. |
| Chosen solution and rationale | Encode selected evidence, compute SHA-256/bytes/content type/dimensions, upload first under a deterministic credential-free object key containing org/run/row UID/kind, then register `media_object`, then reference its key. Never place a URL or local path in DB1. If evidence storage is unavailable, emit the otherwise valid fact with nullable media key and diagnostic `storage_status=unavailable`; do not block recognition or substitute a path. Before using a media key, the adapter checks its org, expected run/camera, kind and selected-evidence UID. Input clips may have null run but must match authorized org/camera; produced media must have all three context values. |
| Producer fields and DB1 columns | Selected vehicle/plate bytes and frame receipt → `media_object(org_id,run_key,camera_key,ts,kind,bucket,object_key,content_type,bytes,width,height,sha256)`; returned key → `best_media_key` or `crop_media_key`; input clip → `source_media_key`. Local `crop_path` remains temporary operational state only. |
| Decision owner | Service decision for upload ordering, validation and nullable fallback. Platform dependency for object-store credentials, bucket policy, media registration and eventual orphan cleanup. Stronger DB constraints are a platform-contract improvement. |
| Validation | Pure metadata/hash tests; simulated upload/register failures; reject URL/path object keys, wrong hash/size, wrong org/run/camera/kind and selected-vs-best crop confusion. P4 inserts cross-org/cross-run media through controlled fixtures and proves adapter rejection even where SQL would permit it. |
| Phase and dependencies | P1 specifies metadata; P2 retains selected bytes/provenance; P3 builds requests; P4 checks adapter behavior; P5 handles retry/orphan reconciliation. |

The supplied direct `fact_vehicle_make.manufacturer_key REFERENCES dim_manufacturer` already protects a known maker when `vehicle_model_key` is null. No service-side workaround or contract change is needed. P4 should still add the missing explicit validation case for a nonexistent manufacturer with null vehicle model.

### G12. Minimal versioned diagnostics

| Record | Resolution |
|---|---|
| Current behavior | Plate `PD` has prediction/debug JSONL with sample, quality, replacement and finalization details; colour/classification outputs have different JSON structures. The earlier envelope proposed more analytical columns than DB1 supplies. |
| Chosen solution and rationale | Use one small `db1-diagnostics.v1` sidecar record per finalized logical row or deliberate omission. It contains schema version, row kind/UID, run ID, service track ID, raw tracker ID/epoch, closure or omission reason, time basis/frame/PTS, association decision, selected route/model natural ID, attempt/sample counters, selected crop sample ID, error code, media storage status and immutable payload hash. Do not duplicate plate text, image bytes, credentials or exception dumps. Keep existing detailed plate review evidence by reference where retention allows. This preserves necessary provenance without creating a monitoring platform. |
| Producer fields and DB1 columns | Sidecar explains construction of all DB1 row columns but is not inserted into DB1. `payload_sha256` binds it to the row snapshot; crop sample IDs link existing `PD` evidence. |
| Decision owner | Service decision. Platform retention/access policy must cover diagnostic/outbox data containing identifiers; plate text remains only in secured payload/DB storage. |
| Validation | JSON schema rejects unknown fields, secrets, local paths, plate text and unbounded exceptions; verifies required fields by emitted/omitted state and stable version. Retention/security review precedes production. |
| Phase and dependencies | P1 specifies schema/fixtures; P2 populates producer evidence; P3 emits sidecars; P5 persists and expires them. |

## Genuine external dependencies

Only these items require an external platform or domain decision; routine service behavior above is settled:

1. Platform provisioning and production resolution for authorized org, SCD2 camera, vehicle lane, run lifecycle, input media and model natural IDs.
2. Shared `dim_class` additions for `van` and `unknown`, plus sufficient `dim_date` coverage. Until provisioned, affected parent rows are quarantined rather than coerced.
3. A DB1 contract decision for truthful `no_plate` storage. Under v0 the service omits the row; the minimal adjustment is specified in G7.
4. Authoritative plate-category definitions and verified examples for TN, RS, private, temporary, diplomatic, foreign, military/police/security and other. Text recognition can proceed independently; uncertain categories remain null.
5. Object-store bucket/credential/retention policy, media registration API, and eventual handling of uploaded-but-unreferenced objects.
6. Platform retention/access rules for personal plate text and the secured outbox/diagnostic records.

The missing media-organization constraints remain a platform-contract concern, handled defensively by adapter validation in G11. The direct manufacturer FK is present and requires no platform clarification beyond ensuring the delivered migration retains it.

## T1 boundary

P1 fixture success produces a reviewable contract candidate only. T1 requires senior validation of that candidate, resolution or explicit deferral of review findings, and an explicit frozen-contract decision. Neither the pure validator nor this document performs that review or freezes T1.

P2 shared-observation instrumentation and P3 producer row construction are separately staged implementation after the contract target is frozen. Their tests validate producer conformance to the frozen contract; they cannot retroactively establish that the contract itself was accepted. P4 isolated database adapter checks and P5 transport/recovery belong to T3 or later persistence work. T2 remains paused. Future source-capture timestamps, multiple recognition rows per attribute, manufacturer/model capability and calibrated association/category accuracy are later capability work unless separately scheduled.
