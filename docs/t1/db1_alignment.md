# T1 alignment with DB1 development contract v0

Review date: 2026-10-08. **Target interface: DB1 development contract v0 (2026-10-08).** This document reconciles the earlier T1 proposal with that interface; it does not assert supervisor acceptance of the remaining semantic choices or a T1 freeze.

Routine service gaps identified here are resolved in [db1_gap_resolution.md](db1_gap_resolution.md), with delivery phases in [db1_implementation_plan.md](db1_implementation_plan.md). The question list below remains useful provenance; the later documents are authoritative for current service decisions.

## Evidence and scope

Read the six files inside the supervisor-supplied [db1_contract.zip](../../db1_contract.zip): `db1_contract/README.md`, `01_shared.sql`, `02_vehicle.sql`, `03_dev_seed.sql`, `check_contract.py`, and `Vehicle Star Schema (1).html` (SVG tables, relationships, rules and interaction script). Archive SHA-256: `62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`. SQL is the precise constraint evidence; the diagram provides a readable overview. The referenced `DB1_MODEL.md` is not in the archive and was not read.

Compared [Phase 1 source mapping](../../T1_PHASE1_MAPPING.md), [decisions](contract_decisions.md), [dictionary](data_dictionary.md), [validation](validation.md), and the [earlier contracts package](../../contracts/README.md). Runtime source references below use the Phase 1 aliases and links, with current static checks of profile schemas, plate results, attempt state, crop processing, live summaries and manufacturer guard. E = existing evidence; D = derivable; N = new collection/context; U = unavailable capability. A derivation is conditional on truthful shared vehicle association, not evidence that integration already exists.

No supplied files were modified, SQL applied, ingestion implemented, database connected, or checker run. The checker calls cleanup **before setup and in finally**, forcibly dropping fixed database `db1_contract_check` and role `db1_contract_check_rw`. Its advertised checks are source evidence only, not executed validation. Its sample data deliberately includes two plate rows and two make rows for a vehicle, and only tests identical-payload no-op replay. No T2 work, commit or push is part of this change.

## 1. Exact producer-to-column mapping

### Shared ownership, context and key resolution

The platform owns `dim_org`, `dim_lane`, `dim_camera`, `dim_model`, `dim_class`, `dim_date`, `dim_run`, and `media_object`. Vehicle owns `dim_plate_category`, `dim_colour`, `dim_manufacturer`, `dim_vehicle_model`, and the four vehicle facts. Do not reproduce the earlier private camera/date/run/type tables. Shared development schema application is provisioning, not permission to fork platform tables. Development run/media writes follow DB1; production sends natural identifiers and run lifecycle to platform ingest, which resolves keys. Put future resolution behind one small adapter boundary.

| DB1 destination | Current source → exact mapping / gap |
|---|---|
| `dim_org.org_id` | N: explicit authorized deployment/request organization UUID. No current producer organization field. Never infer from camera text or use dev orgs as production defaults. `name` is platform metadata. |
| `dim_lane.lane_key` | D: resolve `dim_lane.name='vehicle'` (service `vehicle_service`); never hard-code seed identity. |
| `dim_camera.camera_key` | E/N: PS/PR `camera_id` → registered `dim_camera.camera_id`; resolve the authorized SCD2 version using agreed effective-time policy. `name`, `site`, `stream`, `timezone`, `valid_from`, `valid_to` are registry metadata, not inference. `cam-1`, `static-video`, truncation and a raw RTSP URL do not prove registered identity. |
| `dim_run.run_id` | E/N: plate manager session UUID or API `job_id` can be natural **text** IDs for their own execution. A new coordinated run must have one stable ID for one lane/camera start-to-stop. Separate service jobs cannot be retrospectively joined by numeric track ID. |
| `dim_run.run_key, org_id, lane_key, camera_key` | Resolve run ID to generated key and preserve its authoritative org/camera/lane. Each camera gets its own run. Camera SCD2 version is fixed by the run, not re-resolved per recognition. |
| `dim_run.model_key` | N: registered actual detector `(dim_model.model_name, version)` → key; nullable if no detector. This is an ML model, never a vehicle model. |
| `dim_run.service_version` | N: explicit deployed service/code version; no equivalent authoritative serialized field now. |
| `dim_run.source_kind, source_media_key` | D/N: RTSP → `stream`; uploaded video → `file`, requiring pre-registered input clip `media_object.media_key`. Local upload filename is not this FK. |
| `dim_run.started_at, ended_at, end_reason` | N: absolute UTC start/stop timestamps and truthful lifecycle cause. Current monotonic run timing and generic stream/video end strings need instrumentation/mapping. `ended_at` null while open. No DB1 enum for `end_reason`. |
| `dim_class.class_key` | E/D: VP serialized `vehicle_type` (internal `profile.type`) or CD `profile.type` → resolve reviewed `dim_class.name`, requiring `class_group='vehicle'`. One authority for the shared vehicle lifetime; vocabulary gaps below. |
| `dim_date.date_key` | D/N: each fact's own absolute `ts` → date in **run camera version's timezone**, formatted integer YYYYMMDD → existing date row. Platform generates 2024–2035; service never inserts dates. Different recognitions may have different dates from vehicle first sighting. |
| `dim_model.model_key` | N: resolve registered `(model_name, integer version)` for each actual recognition route. PP `model_version` descriptive string is provenance evidence, not a surrogate key or automatically a registered integer version. |

Generated fact PKs (`vehicle_id`, `plate_read_id`, `colour_read_id`, `make_read_id`) are database outputs, with no producer measurement. Generated dimension keys are lookup results, never raw class indices, tracker IDs or fixed dev-seed numbers. Vehicle models resolve by `(manufacturer_key, name)`; manufacturer, colour and plate category by registered `name`. Do not create catalogue members ad hoc from predictions.

Every fact carries `org_id, run_key, camera_key, date_key, ts, uid`. Its `(run_key,org_id,camera_key)` FK must match `dim_run`; recognitions additionally reference `(run_key,track_id,org_id,camera_key)` in `fact_vehicle`. `uid` is N: allocate and retain a UUID **per logical fact row**, not just per enclosing message. UUID uniqueness is per fact table. Current crop `sample_id` may be reusable for a plate row only after the recognition grain and durable allocation policy are agreed; it is not a vehicle UID or a substitute for colour/make UIDs.

RLS is enabled and forced on camera, run, media and all four facts. Use a normal role without superuser/BYPASSRLS, establish `app.org_id` for each scoped transaction/session, and prevent pooled connections carrying a previous org. Unset/empty setting hides org-scoped rows; shared catalogues are not all RLS-filtered. RLS does not establish caller authorization by itself. The README grant sketch is not a complete verified deployment privilege setup (identity-sequence privileges also need provisioning review).

### `fact_vehicle`

| Column | Producer evidence → DB1 mapping |
|---|---|
| `track_id` | E/N: VP/CD raw numeric ID only after a shared **vehicle** tracker and run-wide identity allocator exist. PR tracks plate crops; do not relabel them vehicles. DB1 requires unique `(run_key,track_id)` across all resets/reconnects. |
| `first_ts`, `last_ts` | N: absolute first/last vehicle sighting instants. VP's internal `first_seen/last_seen` are monotonic and omitted by `to_dict`; CD's timestamp fields are unused defaults; PR has frame indices. None directly supplies these columns. |
| `ts` | D from newly collected `first_ts`; SQL enforces exact instant equality. Neither finalization time nor crop-processing time. |
| `class_key`, `class_conf` | E/D: selected authoritative VP `vehicle_type/type_confidence` or CD type/score, associated with this same vehicle. Resolve class name; only retain finite [0,1] score supporting that exact label/authority, else nullable `class_conf`. Type score now belongs here, not in colour fact. |
| `frames_observed` | E/D/N: VP `frames_seen` or distinct shared tracker contributing frames, positive integer. PR `candidate_count/observed_frames` count usable plate crops, not vehicle sightings; CD active output rows can include unseen tracks. |
| `best_media_key` | E(partial)/N: actual selected vehicle crop uploaded and registered → `media_key`; nullable. PR best plate crop is not vehicle evidence. |

Preserve raw tracker ID/reset epoch in diagnostics, but allocate a non-reused BIGINT analytical `track_id` per vehicle lifetime within a run. Proposed implementation: run-level monotonic allocator plus `(tracker epoch, raw ID, lifetime)` association map retained through resets; recognition rows inherit that allocated ID. Alternatively stop/start a run at reset if supervisor chooses that lifecycle. The old generation column cannot disambiguate DB1 rows because it does not exist. A connection prefix `s<generation>-<id>` is text and does not cover same-connection reuse.

### `fact_plate_read`

| Column | Producer evidence → DB1 mapping |
|---|---|
| `track_id` | N: explicit plate-to-shared-vehicle association → allocated vehicle ID in this run. Never equate independent trackers' integers. |
| `model_key` | N: actual registered OCR/recognition model, NOT NULL even for failed/no-plate rows. Agent routes or compound OCR+marker routes require registration semantics, below. |
| `ts` | E(partial)/N: selected attempted crop's **capture/receipt instant**. `state.cached_attempt.crop_frame` identifies supporting crop, but needs a frame-time ledger. PP `result.timestamp` is generated after local processing and is not crop time. No selected crop leaves this required timestamp unresolved. |
| `outcome` | D/N: accepted complete normalized text → `read`; attempted unresolved text → candidate `unreadable`; confirmed absent usable plate → candidate `no_plate`. Exact rejection/error/no-crop admission remains a question. No SQL representation of old independent text/category states. |
| `plate_text` | E/D: `state.final_result.plate_text_normalized`, preserving leading zeros, iff admitted `read`; null for `unreadable/no_plate`. SQL requires non-null for read but does not enforce nonempty, canonical or complete text. Avoid live raw-text fallback. |
| `plate_category_key` | E/D conditional: selected `plate_category` → approved registered category name/key; null when unresolved/incompatible. `private` is not automatically `TN`; see vocabulary review. |
| `conf` | E/D: selected result `text_confidence` when appropriate, nullable when unmeasured/unaccepted. Live `confidence` is category confidence and must not be copied here as OCR score. Agree text-vs-joint-agent score semantics; DB1 has no metric column. |
| `attempts` | E(partial)/N: crop recognition-operation count at agreed row scope, including failures. `len(state.attempts)` covers completed recorded attempts, not all submitted/exceptional operations. DB1 requires **>=1**, unlike the old nullable/zero count. Whole/left/right OCR calls and HTTP retries are distinct counters. |
| `crop_media_key` | E(partial)/N: selected attempt's crop (`crop_path` currently local) → object upload, registered media row → key; nullable. Do not select later `best_crop` if another crop supported result. |
| `crop_score` | E/D: `cached_attempt.crop_score`, weighted pixel-area quality formula, not confidence. Nullable; discard nonfinite invalid-crop sentinel. DB1 SQL imposes no quality bound or metric pairing. |

SQL allows any number of plate rows for a vehicle, each with its own UID, timestamps/model/evidence. It does not define whether rows are per attempt, per selected recognition cycle, or per final selected result. Rechecks may be new logical rows; delivery retries of the same row are not new recognition rows. Do not silently change current selection policy (cached attempt, replacement based on category confidence) or discard non-selected diagnostics.

### `fact_vehicle_colour`

| Column | Producer evidence → DB1 mapping |
|---|---|
| `track_id` | N: same shared vehicle association; current colour pipeline is separate uploaded-video tracking, not live integration. |
| `ts` | N: agreed selected colour observation/aggregate instant. No trustworthy absolute timestamp is currently serialized. Aggregate timestamps need an explicit policy. |
| `model_key` | N: actual registered colour route, NOT NULL. Current local temporal HSV route and optional agent are not automatically the seeded ML classifier. |
| `colour_key`, `conf` | E/D: CD final `profile.colour/colour_conf` → reviewed canonical dimension member and supporting score. Unresolved/incompatible label → null key; preserve raw label and reason. Proposed null score for undecided result needs semantic agreement; SQL alone permits a score with null colour. |
| `state` | E/D conditional: `_colour_decision` or new outcome state → agreed text semantics. SQL accepts any non-null text; checker uses `undecided`. Old known/inferred_unknown/not_attempted/unsupported/inference_error is not an enforced DB1 enum. |
| `samples` | E/D: retained valid local `_colour_samples` count, including retained local evidence after agent override if agreed. Required integer >=0; old unmeasured null cannot be written as zero. Capture truthful measurement/scope. |

Multiple colour rows are allowed. Retain local/agent source and score formula outside DB1 fact columns until an approved evidence convention exists.

### `fact_vehicle_make`

| Column | Producer evidence → DB1 mapping |
|---|---|
| `track_id`, `ts`, `model_key` | N/U: same shared vehicle ID, agreed absolute recognition instant and actual registered inference network (NOT NULL). No current manufacturer/model inference produces these. |
| `manufacturer_key`, `vehicle_model_key` | U: current capability unavailable; nullable keys, no invented brand/model and no reserved unknown member supplied. Future maker resolves `dim_manufacturer.name`; vehicle model resolves within that maker. `vehicle_model_key` is the car model; `model_key` is the ML network. |
| `manufacturer_conf`, `vehicle_model_conf` | U: null; no actual scores or registered metrics. Future finite [0,1] scores must support the exact values. |
| `observations` | U/N: required measured integer >=0 at agreed scope. No manufacturer inference observations today; zero only if a defined operation genuinely observed none, never a fabricated substitute for missing measurement. |

VA explicitly rejects `enable_manufacturer=True`. Seed `vehicle_make` model and maker catalogue do not establish executable capability. DB1 has no make/model state or error columns, and requires an ML model for every make row. Whether to omit unavailable recognition rows or represent a configured non-attempt with null keys requires supervisor clarification; do not manufacture a model invocation to satisfy the FK.

### Object-storage evidence

| `media_object` column | Mapping / missing collection |
|---|---|
| `media_key` | Generated; referenced by run input clip, vehicle best crop or plate selected crop. |
| `org_id`, `run_key`, `camera_key` | Explicit authorized context. Input clip may have null run; produced evidence should retain complete run/org/camera association. |
| `ts`, `kind` | Actual evidence instant and kind (`clip`, `vehicle_crop`, `plate_crop`); absolute timestamp instrumentation missing for crops. |
| `bucket`, `object_key` | N: successful MinIO/S3 object placement. PP/PD local `crop_path` and `original_image_ref` are source diagnostics, not valid stored locations. Never store local paths/URLs as these references. |
| `content_type`, `bytes`, `width`, `height`, `sha256` | N/D: measured uploaded artifact metadata (crop dimensions available in memory); checksum of actual stored bytes, not model weights or synthetic fixture hash. Width/height nullable in SQL. |

`dim_model` registration is platform-owned: `(model_name,version)` unique, with `task`, optional `base_model`, `weights_path`, `map50`, and registration timestamp. Map actual loaded detector, OCR, marker/compound recognition, HSV/temporal algorithm or VLM routes under a reviewed registration policy. `weights_path` is model registry metadata, not permission to put crop paths in `media_object`. Do not infer accuracy (`map50`) from confidence or treat synthetic seeded models as validated deployments.

## 2. Earlier T1 proposals superseded or requiring reconciliation

| Earlier proposal | DB1 target / consequence |
|---|---|
| Four private facts/eight dimensions; private `dim_vehicle_type`, `dim_pipeline_run` | Four facts/four vehicle dimensions on eight platform-owned shared tables. `dim_class`, `dim_run`, `dim_model`, org/lane/media context replace the private topology. Keep earlier source mapping as evidence. |
| `fact_vehicle_event`, `fact_plate_recognition`, `fact_colour_recognition`, `fact_manufacturer_recognition` | `fact_vehicle`, `fact_plate_read`, `fact_vehicle_colour`, `fact_vehicle_make`; use actual DB1 column names/types from section 1. |
| Unique recognition per event; exactly three result rows atomically with event | No unique recognition-per-vehicle constraint. Parent vehicle FK remains mandatory. Required row emission/completion and transactional grouping are unresolved, not inherited from the old envelope. |
| Observation UUID + camera/raw ID/generation source tuple | DB1 per-row `uid`; vehicle natural identity `(run_key,track_id)`. No observation/message/generation columns. Old IDs can remain diagnostics if useful. |
| Multi-camera coordinated run; UUID-only run ID | One camera/lane run; natural run ID is text. No old metrics/provenance/completion JSONB or run counters columns. |
| SCD1 camera; UTC first-sighting date for all results | Camera SCD2; each row's camera-local date of its own `ts`. Camera version/timezone lookup must be explicit. |
| Type confidence in colour fact; unknown dimension members | `class_conf` on vehicle; nullable recognition keys. No seeded unknown class/colour/category/maker. Vehicle class is mandatory and unresolved class policy needs review. |
| Five independent state enums, metric/error pairs, category confidence | Only plate outcomes and free-text colour state exist. Make/model states, category state/confidence, sources/metrics/errors are absent. Preserve diagnostic evidence; do not claim these extra columns exist. |
| Nullable/zero plate attempts; nullable counts | Plate attempts required >=1; colour samples/make observations required nonnegative integers. No-crop and unmeasured-count semantics need review. |
| Proposed latency, frame, duration, finalization/closure and provenance columns | No direct fact columns for these (except frames_observed and run end_reason). Keep useful instrumentation/evidence outside facts under an agreed convention. |
| Immutable complete-envelope conflict rejection | DB1 README requires per-row `ON CONFLICT (uid) DO NOTHING`. This silently ignores changed payloads too. Stronger equality/conflict detection is an unresolved adapter policy, not accepted DB1 behavior. |
| T1 labels/normalization/manifests and fixture approval assumptions | Retained proposals; cannot use existing JSON-schema fixture passes as DB1 compatibility checks. |

### Vocabulary incompatibilities

The development seed is a starter catalogue, not a declaration that every producer label has an approved equivalent. Look up actual registered names; never hard-code identity keys or silently extend shared catalogues.

| Producer / old draft | DB1 dev seed | Alignment action / unresolved meaning |
|---|---|---|
| car, bicycle, motorcycle, bus, truck | Same names in `dim_class` | Resolve exact labels in vehicle group. Raw detector indices are not keys. |
| van; camper→van; unknown | Neither van nor unknown seeded | Mandatory class FK cannot encode absence. Request registered van/unknown policy or explicit rejection/quarantine. Do not convert van to car/truck without acceptance. Tractor→truck and cyclist→bicycle remain lossy prior proposals needing review. Exclude person from vehicle facts despite shared person class existing. |
| white, black, red, blue, green, yellow | Same names in `dim_colour` | Exact label lookup where result is admitted. |
| silver/gray, brown/beige | Separate grey/silver and brown; no beige | Cannot split grouped evidence or equate brown/beige with brown. Null FK pending approved mapping/catalogue; preserve grouped raw label. |
| orange, purple, unknown | Not seeded | Null unresolved/incompatible colour key; catalogue expansion or mapping needs review. |
| private, temporary, diplomatic, military, foreign, other, unknown | TN, RS, foreign, diplomatic | Foreign/diplomatic names overlap but admission/meaning still require review. Private→TN and temporary→RS are **questions**, not approved aliases. Military (agent groups police/security), other and unknown have no seed counterparts. Null category until valid mapping. |

Normalization remains unsettled: OCR strips non-ASCII symbols, private parser emits Arabic marker, agent uses another normalizer, while checker text is `123 TU 4567`. That sample does not approve `TU`, Unicode marker aliases, completeness or opaque-text normalization rules.

## 3. Producer gaps and exact review questions

Existing live integration is plates only; type/colour have independent uploaded-video trackers. No shared vehicle-to-plate association, run-wide lifetime allocator, organization context, absolute frame-time ledger, durable per-row UID snapshots, model registry resolution or object uploads currently establishes a DB1-ready producer.

1. **Camera/run context:** Which registered org/camera IDs are supplied for live and uploaded-video jobs, and which SCD2 version is selected at run start (effective at started_at or explicitly provisioned)? Must camera version/timezone changes close the run? What is the authorized production natural-ID resolution/provisioning interface?
2. **Identity/reset:** Keep one run across reconnect/reset with allocated non-reused track IDs, or close/start a run at reset? How will the shared vehicle tracker associate plate/colour outputs and distinguish raw-ID reuse within one tracker epoch?
3. **Recognition grain:** One row per crop attempt, completed recognition cycle, or final selection? Are replacement results new rows with new UIDs? Are unavailable colour/make rows omitted? Which rows constitute a completed vehicle, and which transaction boundaries are required?
4. **Changed-payload retries:** Is silent first-write-wins acceptable when the same UID carries changed text/score/evidence, as `DO NOTHING` permits, or must the adapter compare immutable row payloads and reject conflicts? Where are stable row IDs/payloads persisted across delivery retries/crash? New UID for a changed payload must not masquerade as a retry.
5. **Absolute time:** For file runs, trusted original capture timeline or explicitly labelled replay-receipt time? Which clock/discontinuity policy is accepted? What instants define aggregated colour/make rows? The old 1-second tolerance is still a proposal. Every row needs its own local date; e.g. `2026-10-08T23:30:00Z` in Africa/Tunis yields `20261009`, not UTC `20261008`.
6. **Plate failure/no crop:** How distinguish confirmed no_plate, crop rejection, attempted unreadable, inference_error, disabled and unsupported routes using only three outcomes? What required `ts` is used when there is no selected crop? What does `attempts>=1` count for no_plate when OCR attempts are actually zero? Should a detector-only no-plate decision count as an operation, or should emission be deferred pending contract clarification? Do not invent crop time or inflate attempts. Which registered non-null OCR model applies if no OCR ran?
7. **Category/text/score:** Approve category aliases/admission and canonical complete plate text; retain category even with unreadable text? Define plate `conf` for local OCR vs agent joint confidence. SQL permits failed text with category/score/crop; whether to retain those is a semantic question. Where should independent category state/confidence and suppressed errors be preserved?
8. **Unavailable make/model and colour route:** Omit absent capabilities or emit null-valued rows? If emitting, how truthfully satisfy non-null model_key and measured count? How register local HSV/temporal routes, compound OCR/marker and VLM agent models; what does each model version identify? Seed rows cannot substitute for an executed model.
9. **Class/colour vocabulary and authority:** Which producer is authoritative for vehicle class? Register van/unknown or reject those observations? Approve grouped colour preservation/expansion and class aliases, and define free-text colour states and sample scope after agent override. Do not infer lost distinctions.
10. **Evidence:** Which object store/provisioning route, upload-before-reference behavior and diagnostic manifest format are approved? How are selected crops distinguished from best-but-unrecognized crops, and how are score formulas, capabilities, raw tracker IDs, failures and clocks retained without extra DB1 columns?

### Supervisor review: manufacturer FK finding

The requested concern is **not a missing FK in this supplied revision**: `02_vehicle.sql` declares `manufacturer_key int REFERENCES dim_manufacturer` directly on `fact_vehicle_make`. Thus a non-null nonexistent manufacturer is rejected even when `vehicle_model_key` is null. The composite `(vehicle_model_key,manufacturer_key)` FK alone would be skipped for null model under default MATCH SIMPLE, but the direct FK closes that hole here. The HTML also labels manufacturer_key as an FK. The checker tests a valid maker with null model, wrong model/maker, and model without maker; it does **not** explicitly test nonexistent maker with null model. Ask supervisor to confirm this direct FK is retained in delivered DB1 migrations and add that case to their later validation. No SQL changes or checker execution here.

### Supervisor review: media organization consistency gaps

These are real constraint gaps in the supplied SQL:

- `fact_vehicle.best_media_key`, `fact_plate_read.crop_media_key` and `dim_run.source_media_key` reference only `media_object.media_key`. They do not enforce matching organization, camera, run, evidence kind or timestamp. FK existence plus media-table RLS is not a composite reference-integrity guarantee.
- `media_object` has `(run_key,org_id,camera_key)` FK to run, but default MATCH SIMPLE skips it if run_key **or camera_key** is null. Input clips legitimately have null run, yet there is no separate `(camera_key,org_id)` FK to camera; produced media with run non-null/camera null also bypasses run/org consistency. The diagram's individual camera/run FK labels are more general than the actual SQL enforcement.

Ask whether supervisor will strengthen org consistency for fact/run media references (and optional media camera/run relationships) in the delivered contract, or require ingest ownership checks pending that change. Independently require future adapter validation of media org, expected camera/run and kind; do not claim those checks or DB constraints already exist. Input clips may need a distinct association policy because they predate runs. Keep supplied SQL unchanged.

## 4. Minimal implementation phase plan

1. **Smallest next phase: T1 interface reconciliation only.** Obtain answers to row grain/emission, no-crop timestamp/attempt/model, class/category/colour vocabulary, clock basis, model registration and retry-conflict questions; choose one-camera run-wide identity policy. Produce a DB1-specific row contract and pure adapter specification/fixtures with stable per-row UIDs, named natural-ID lookups and explicit diagnostics. Cover reset/reuse, multiple recognitions, changed-payload replay, midnight local dates, unavailable make, no crop and mismatched media org. Preserve old fixtures as historical draft evidence. No ingestion or database connection is needed for this phase.
2. **Producer context/instrumentation, after that specification:** one shared vehicle/plate association, org/camera/run lifecycle, run-wide allocated track IDs, absolute frame-time ledger, actual attempt/sample counts, selected evidence provenance and durable row snapshots. Integrate only available capabilities; do not enable nonexistent make/model prediction.
3. **Key/media adapter and guarded ingestion, separately authorized later:** registered catalogue/model resolution, object upload/metadata, normal-role RLS context and media ownership checks; parent vehicle before recognition rows, stable-UID replay handling per agreed policy. Development run creation stays behind the production lifecycle boundary. Validate on a separately provisioned disposable database through a safe harness, never by running the fixed-name destructive checker against an existing environment.

These phases concern DB1-facing T1 alignment and subsequent producer/persistence work only. They do not schedule T2, approve unresolved semantics, or imply that implementation has started.
