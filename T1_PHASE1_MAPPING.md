# WAYCON vehicle_service — T1 Phase 1 mapping

> **DB1 reconciliation required (2026-10-08).** DB1 development contract v0 (2026-10-08) is the target database interface. This earlier draft is retained for producer evidence and proposal history; its tables, columns, vocabularies, cardinality and retry rules are not a DB1 specification. See [DB1 alignment](docs/t1/db1_alignment.md). No acceptance of unresolved semantics is claimed.

Inspection date: 3 October 2026. Scope: evidence mapping only; no application, database, transport or model implementation.

## Evidence and approval boundary

Repository root `/home/intern/part-time/aziz/pipeline`; origin fetch/push `https://github.com/MedMdhaffar/VehicleRec.git`; branch `main`; commit `a8950aa9f974bf8e46178e2973b3a2d632a86fce`. No applicable `AGENTS.md` found in the repository or its ancestors. The initial checkout was clean; the subsequently supplied PDF is untracked. This mapping adds only this document.

Read all seven pages, including tables, of [Vehicle_Service_Star_Schema_Report.pdf](Vehicle_Service_Star_Schema_Report.pdf), revision 1.0, 3 October 2026. SHA-256: `5612d3d6c41ef5d083b2e00d3e61d2d47f58aad634a2ab98641ee61ce0eb7140`.

The four facts and eight dimensions are the approved architecture, per the request and report. The UUID/generation rules, complete-message atomic ingestion, state vocabulary, score provenance, time conventions and immutable-replay rules are corrected-report technical additions whose acceptance is still required. The earlier two-table proposal is superseded. Neither technical acceptance, database execution nor T1 freeze is asserted here.

README identifies `vehicle/` packages as the API runtime copies; `services/` contains standalone copies. Evidence below uses runtime code. This is static inspection, not a claim that a camera, credential, model or external agent was exercised successfully.

## Source reference legend

Paths are relative to this repository. Each table reference names a file alias and symbol; links locate the defining code.

| Alias | File and relevant symbols |
|---|---|
| PS | [vehicle/license_plate_service/service.py](vehicle/license_plate_service/service.py:27): `run_video_analysis`, `RTSPManager.start`, `_run`, `stop` |
| LR | [vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py](vehicle/license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py:412): `run_rtsp`, `RTSPFrameReader`, `_build_live_runner`, `_retire_stale_tracks`, `_rtsp_summary`, `live_track_results` |
| PR | [vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py](vehicle/license_plate_service/run_video_pipeline/run_video_prototype.py:162): `TrackState`, `RecognitionAttempt`, `VideoPrototypeRunner`, `crop_quality_evidence`, `CountingReader` |
| PR-live | [vehicle/license_plate_service/run_rtsp_pipeline/run_video_prototype.py](vehicle/license_plate_service/run_rtsp_pipeline/run_video_prototype.py:162): same runner/state symbols; identical to PR at inspection |
| PP | [vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py](vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py:47): `PrototypePlatePipeline.process_crop` |
| PT | [vehicle/license_plate_service/run_video_pipeline/pipeline.py](vehicle/license_plate_service/run_video_pipeline/pipeline.py:125): `analyze_plate_crop`, `assemble_plate_detection`, `_read_safely`, `_locate_safely` |
| PO | [vehicle/license_plate_service/run_video_pipeline/ocr.py](vehicle/license_plate_service/run_video_pipeline/ocr.py:16): `normalize_plate_text`, `digits_only`, `PlateReader._parse`, `read_result` |
| PN | [vehicle/license_plate_service/run_video_pipeline/private_plate_parser.py](vehicle/license_plate_service/run_video_pipeline/private_plate_parser.py:59): `parse_private_plate`, `PrivatePlateCandidate.normalized_plate` |
| PA | [vehicle/license_plate_service/run_video_pipeline/agent.py](vehicle/license_plate_service/run_video_pipeline/agent.py:48): `normalize_agent_text`, `AgentPrediction`, `OpenRouterPlateAgent.analyze` |
| PV | [vehicle/license_plate_service/run_video_pipeline/routing.py](vehicle/license_plate_service/run_video_pipeline/routing.py:20): `route_plate`; [schemas.py](vehicle/license_plate_service/run_video_pipeline/schemas.py:64): `UnifiedPlateResult`, `PlateTiming`, `PlateCategory` |
| PD | [vehicle/license_plate_service/run_video_pipeline/data_capture.py](vehicle/license_plate_service/run_video_pipeline/data_capture.py:58): `ReviewStore.capture`, `mark_track_final` |
| CD | [vehicle/vehicle_color_service/vehicle_color_service/detector.py](vehicle/vehicle_color_service/vehicle_color_service/detector.py:115): `ColourPipeline.process_frame`, `_observe`, `_apply`, `_fallback_if_needed`, `finalize_all`, `_public_profile`, `main` |
| CT | [vehicle/vehicle_color_service/vehicle_color_service/temporal.py](vehicle/vehicle_color_service/vehicle_color_service/temporal.py:130): `SUPPORTED_COLOURS`, `make_colour_observation`, `aggregate_colour_observations`, `decide_colour` |
| CS | [vehicle/vehicle_color_service/vehicle_color_service/schemas.py](vehicle/vehicle_color_service/vehicle_color_service/schemas.py:26): `VehicleProfile`; [config.py](vehicle/vehicle_color_service/vehicle_color_service/config.py:47): `TAXONOMY`, `LABEL_ALIASES`, colour configuration |
| CA | [vehicle/vehicle_color_service/service.py](vehicle/vehicle_color_service/service.py:7): `run_color_analysis` |
| VP | [vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py](vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py:43): `VehiclePipeline.process_frame`, `_finalize`, `finish`, `aggregate_vehicle_type`, `update_track_vehicle_type` |
| VS | [vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/schemas.py](vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/schemas.py:9): `VehicleProfile`, `to_dict` |
| VA | [vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/service.py](vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/service.py:14): `VehicleAnalysisService.__init__`, `process_video`, `finish` |
| VT | [vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/classification.py](vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/classification.py:6): `PUBLIC_VEHICLE_TYPES`, `normalize_streetdet_class_id` |
| API | [plate views](vehicle/license_plate_service/views.py:20), [colour views](vehicle/vehicle_color_service/views.py:16), [classification views](vehicle/vehicle_classification_manufacturer_service/views.py:16): separate `analyze` endpoints, separate UUID `job_id` creation |

LR imports its runner from PR-live, but PR-live and the RTSP prototype's orchestration import shared processing modules from `run_video_pipeline`. The API exposes that sibling package through PS `_ensure_pipeline_import_path`. The duplicated RTSP files should not be mistaken for independent capabilities.

## Availability notation

**E** = existing value; **D** = derivable from current runtime evidence; **N** = requires new collection or explicit producer/context plumbing; **U** = unavailable in the current live pipeline. Multiple letters indicate a partial source or an offline-only source. **W** = future writer-managed surrogate/FK resolution, not a producer measurement; no fact/dimension models currently exist in the three apps' `models.py` files.

There is only one implemented RTSP service: plates. Colour and vehicle classification are separate uploaded-video pipelines with separate trackers. Thus E/D entries for those services describe offline capabilities, not live outputs. No shared vehicle-to-plate association exists. Joining their numeric track IDs would fabricate identity. All mappings below depend on resolving that grain/context gap; they are not a ready-to-ingest unified event.

## fact_vehicle_event

| Field | Source and status | Conversion, interpretation and loss risk |
|---|---|---|
| vehicle_event_key | W; no current source | Database-generated BIGINT; never reuse a tracker ID as this key. |
| observation_id | N; PR/PR-live `TrackState` and VP `VehicleProfile` have no lifetime UUID | Allocate once per tracked lifetime, retain through closure and transport retries. PP `sample_id` is per crop attempt, not this identity. |
| pipeline_run_key | W/N; PS session UUID and API job UUIDs are separate contexts | Resolve one shared `run_id`; existing independent jobs cannot be assigned a common execution retrospectively without evidence. |
| camera_key | W/N; PS `RTSPManager.start(args.camera_id)`, PR `camera_id` | Live camera ID exists; `cam-1` default/truncation and offline `static-video` do not establish durable registered identity. Resolve from configured canonical ID. |
| track_id | E; PR `TrackState.track_id`, VP/CS `VehicleProfile.track_id` | Raw integer fits BIGINT. Live strings `s<connection_generation>-<id>` are display/source composites, not integer IDs. Plate IDs and vehicle IDs are unrelated. |
| track_generation | N; LR `connection_generation` is only partial evidence | Starts at 1 per run/camera/ID lifetime; increment on reuse/reset. Reconnect count alone does not cover same-connection reuse or stale-state resurrection. |
| date_key | D/N; no usable current UTC first-sighting timestamp | Derive UTC YYYYMMDD from newly collected `first_seen`, not prediction timestamp, local date or finalization date. |
| vehicle_type_key | D/U; VP `profile.type`, CD `profile.type`, VT/CS taxonomies | Resolve canonical label or unknown. Live plate tracker supplies no vehicle type. Pick one authoritative type producer; colour latest label and classification aggregate are different policies. |
| first_frame | E/U/N; PR `TrackState.first_frame`; VP `profile.first_frame`; CD lacks stored first frame | Zero-based processed decoded-frame index. Plate value starts at first usable associated plate crop, not first visible vehicle frame. Colour needs first-frame collection. |
| last_frame | E/U; PR `TrackState.last_frame`; VP `profile.last_frame`; CD `profile._last_seen_frame` | Last contributing visible detection; never closure frame or EOF payload frame. Plate-only visibility cannot be relabelled vehicle visibility. |
| first_seen | N; VP `profile.first_seen` is monotonic; CS default 0 is never updated by CD | Collect frame-receipt UTC or trusted capture UTC at the frame boundary. PP `timestamp` is crop-processing time; neither that nor monotonic values identifies first sighting. |
| last_seen | N; VP `profile.last_seen` is monotonic; PR has only frame index | Same clock/basis as first sighting. Colour's unused zero field is not measured time. |
| sighting_time_basis | N; no current event field | Use `host_frame_receipt` after receipt timestamps are collected. `source_capture` requires trustworthy capture UTC; nominal FPS/relative video position does not establish it. |
| track_duration_seconds | D/N/U; VP difference of internal monotonic `last_seen-first_seen`; PR `run` reports frame-span/FPS duration | VP gives processing-sighting elapsed interval, but times are omitted from serialized VS output. Live plate monotonic sighting times need collection. Reject PR duration as a direct mapping: `(last-first+1)/fps` is media-span duration, not the contract metric. |
| frames_observed | D/U; PR `candidate_count`/`observed_frames`; VP `frames_seen`; CS `frames_seen` | Count actual contributing observations, ideally distinct frame indices; verify one contribution per frame. PR count means usable plate crops. CD returns active profiles even while temporarily unseen, so do not count output rows as sightings. |
| final_reason | N; LR stop/reconnect paths, PR `finish` triggers, VP `_finalize`, CD loss/EOF branches | Translate actual lifecycle cause to enum; OCR trigger `track_disappeared` or `strong_quality` is not an event closure reason. Current generic `stream_end`/`video_end` conflates causes. |
| finalized_at | E(partial)/N; PD `mark_track_final` UTC timestamp | Existing stamp closes selected-attempt metadata only, and is absent if no result. Collect one host UTC closure stamp for every observation after results settle. Do not use PP prediction time. |

## Recognition fact context shared by all three tables

| Field in each table | Source and status | Meaning and risk |
|---|---|---|
| vehicle_event_key | W/N; no current unified event reference | Resolve from stable observation UUID. Plate/camera display IDs, crop `sample_id` and video `job_id` are insufficient. |
| pipeline_run_key | W/N; API separate jobs; PS RTSP manager UUID | Use the event's same shared run, enforce composite FK. A matching integer tracker ID in another job is not matching run context. |

These two rows explicitly apply to `fact_plate_recognition`, `fact_colour_recognition` and `fact_manufacturer_recognition`.

## fact_plate_recognition

| Field | Source and status | Conversion, interpretation and loss risk |
|---|---|---|
| plate_fact_key | W; no current source | Generated BIGINT, one row per event. |
| plate_text | E/D; PR `state.final_result.plate_text_normalized`; PP/PN/PA | Preserve TEXT and leading zeros. Accept only resolved, nonempty normalized text; compact PD/LR views fall back to raw text and must not be copied blindly. Normalizers are inconsistent; see decisions below. |
| recognition_state | D/N; PR selected `UnifiedPlateResult.status`, PP, PA `AgentOutcome` | Accepted plus valid nonempty canonical text -> known; completed rejection/unresolved OCR -> inferred_unknown; no submitted crop -> not_attempted. Suppressed OCR errors and overloaded unavailable require additional evidence to distinguish failures. |
| error_code | N; PA `warning`, PO/PT logged exceptions, Future failure | Record bounded machine code only for final inference_error. Warnings are not bounded codes. PA unavailable can mean disabled/missing key or exhausted network retries. |
| plate_category_key | E/D subject to acceptance; PP `plate_category`, PV `route_plate`, PA `prediction.category` | There is actual private-structure routing and optional multi-category agent capability here. Do not derive category from marker absence or map tounes/other automatically. Until rules/capabilities are accepted, keep unknown with an explicitly agreed category-state policy. |
| plate_category_state | N/D; category, route, status and capability configuration | Must be independent of plate-number state. Current single status cannot express accepted category with unresolved text or the converse. Report's blanket unsupported assertion needs scope clarification. |
| plate_category_error_code | N; PA outcome diagnostics, PT marker failure | Bounded category-specific final failure code; not OCR exception text. Null outside inference_error. |
| confidence | E/D; selected result `text_confidence`; PO `PlateReader._parse` | Local mean OCR recognition score (rounded 4 decimals); split-region resolution retains whole-crop OCR score, not fused confidence. Agent branch copies its single declared confidence for both text and category. Require known text; otherwise null. PD/LR generic confidence is CATEGORY score. |
| confidence_metric | N/D; PP `source`, `model_version`, PT `resolution_source` | Emit a versioned metric such as OCR mean recognition score; distinguish whole/split provenance. Agent's joint self-reported score is not independently calibrated text confidence: null pair unless accepted as that metric. |
| category_confidence | E/D; selected `category_confidence` | Private-local value is `min(ocr_confidence,marker_confidence)` from routing, not classifier probability; agent is a single self-reported joint score. Null if category unresolved or unsupported. |
| category_confidence_metric | N/D; PV `route_plate`, PP source | Name private-rule evidence minimum or agent joint self-reported confidence explicitly; never derive a new category score from OCR alone. Null both fields if semantics unaccepted. |
| selected_crop_frame | E/D; PR `state.cached_attempt.crop_frame` | Use crop that produced selected result, not `state.best_frame` which may belong to another/unrecognized crop. Null when no selection; retain frame within event interval. |
| selected_crop_score | E/D; PR `state.cached_attempt.crop_score`, `crop_quality_evidence` | Area × detector-confidence factor × sharpness × contrast × exposure × border factor. Not probability and may exceed 1; discard nonfinite invalid-crop sentinel, not cast to confidence. |
| selected_crop_score_metric | N/D; PR `crop_quality_evidence` | Version and name exact weighted pixel-area ranking formula. Preserve formula/config provenance rather than calling it accuracy. |
| recognition_attempts | D/N; PR `len(state.attempts)`, `submitted_qualities`; `CountingReader.calls` | Track crop-level OCR attempts including failures. Completed attempt objects cover ordinary attempts, not uncaught Future failures; collect starts/failures. Low-level whole/left/right calls and agent API retries are separate counts. No attempt = 0, missing evidence = null. |
| local_latency_ms | E(partial)/N; selected `result.timing.ocr_ms`, PT `analyze_plate_crop` | `ocr_ms` measures OCR calls including reader preprocessing/decoding and any split calls; confirm intended whole/split scope. Current summary's `local_latency_ms=local_total_ms` includes marker localization and parsing, so it is broader than report's OCR-only duration. Null absent/unmeasured, not summary default 0. |
| total_recognition_latency_ms | N; PR `_submit`/`_drain` lack wall instants; PP `timing.total_ms` exists | Collect first submission and final resolution monotonic instants, milliseconds including worker queue and rechecks/retries. Existing per-crop total starts when worker executes, excludes queue and earlier crop attempts; direct copying misrepresents latency. |

Live UI is lossy: LR `live_track_results` supplies category confidence under generic `confidence`, falls back to raw plate text and converts error/unknown to unavailable. PR `record_attempt` also chooses replacements using category confidence; final selection must be read from `cached_attempt`, not latest attempt or maximum OCR score. Preserve that selection policy in diagnostics; do not silently redefine it during mapping.

## fact_colour_recognition

All measured colour/type values below are offline; the implemented live plate service does not invoke CD.

| Field | Source and status | Conversion, interpretation and loss risk |
|---|---|---|
| colour_fact_key | W | Generated BIGINT. |
| colour_key | E/D/U; CD `profile.colour`, `_public_profile`; CT labels | Canonical label or unknown. Keep silver/gray and brown/beige grouped. Agent can replace local winner; store final label/source. |
| colour_state | D/N/U; CD `_colour_decision`, `_colour_attempts`, `_colour_samples` | Known non-unknown final label -> known; attempted short/final/max unknown -> inferred_unknown; no inference -> not_attempted. Existing values `early_lock`, `short_unknown`, etc. are decision reasons, not contract enums. Live lacks integrated colour capability. |
| error_code | N/U; CD `_colour_agent_error`, `_agent_colour` exception handling | Missing API key is not proof local inference failed; optional fallback failure with a usable local final label must not make event inference_error. Failures are not consistently retained as bounded codes. |
| colour_confidence | E/D/U; CD `profile.colour_conf`, CT winner share | Local temporal score = winning sum(confidence×quality)/total valid weight. Agent fallback is self-reported score. Unknown decisions can retain positive share; publish null for unknown. Single-image branch uses direct HSV score, a third metric. |
| colour_confidence_metric | N/D/U; CD `_colour_source`, `_colour_decision`; CT aggregation | Distinguish temporal winner weight share, agent self-report and single-frame HSV score. They are not interchangeable probabilities. |
| colour_samples | E/D/U; CD `_public_profile`: `len(_colour_samples)` | Counts valid frame-level outputs retained in aggregation, including all valid labels, not frames_seen, attempts, rejected samples or agent calls. Agent overwrite uses a crop result; clarify whether count describes local supporting evidence or selected-result aggregation. |
| type_confidence | E/D/U; CD `type_conf`; VP `aggregate_vehicle_type` | CD latest frame changes type and score together. Safe only if event type comes from that same final CD label, with provenance. VP score supports its aggregate type but belongs to another tracker/run today. If event chooses another type or known label is unsupported by score, null. |
| type_confidence_metric | N/D/U; CD `process_frame`; VP `aggregate_vehicle_type` | Name latest detector score or consensus aggregate formula separately. Do not attach a latest score to a locked/aggregate label from a different observation. |

CS declares `_type_votes`/`_type_locked` but CD never uses them. Its `first_seen`/`last_seen` are unused defaults. Those declarations do not prove an implemented locked type or timestamp collection. CA keeps the last serialized vehicle by numeric track ID; a reused ID overwrites a previous lifetime. Intermediate active profiles are emitted during missed frames, and EOF records use one-past-last frame as a closure marker, not a sighting.

## fact_manufacturer_recognition

VA explicitly raises if `enable_manufacturer=True`; CLI exposes no enabling option. Manufacturer crop-pool configuration constants are not inference. No manufacturer/model result fields or model capability are implemented in this runtime package.

| Field | Source and status | Conversion, interpretation and loss risk |
|---|---|---|
| manufacturer_fact_key | W | Generated BIGINT. |
| manufacturer_key | U/W; VA `__init__` rejects capability | Resolve reserved unknown member; never infer brand from vehicle type. |
| manufacturer_state | U/D; VA capability guard | Publish unsupported in current producer. This is capability absence, not a failed inference. |
| manufacturer_error_code | U | Null for unsupported; do not store initialization exception as prediction error. |
| model_key | U | Null; no model inferred. |
| model_state | U/D; no model predictor/schema | Unsupported independently of manufacturer state. |
| model_error_code | U | Null for unsupported. |
| manufacturer_confidence | U | Null, not zero or detector score. |
| manufacturer_confidence_metric | U | Null paired with absent score. |
| model_confidence | U | Null. |
| model_confidence_metric | U | Null. |
| observations_count | U | No aggregation outputs; use 0 when explicitly establishing no attempts, null for unmeasured historical output. Do not copy frames_seen. |

Shared vehicle_event_key and pipeline_run_key are mapped above. Keep this fact in the approved model even while unsupported. Future known model requires known manufacturer and matching `(model_key,manufacturer_key)`; no current data can supply that pair.

## Eight dimensions

### dim_camera

| Field | Source and status | Conversion and risk |
|---|---|---|
| camera_key | W | Generated BIGINT. |
| camera_id | E/N; PS `RTSPManager.start`, LR `args.camera_id` | Register stable nonsecret ID; prevent default/truncation collisions. PR static-video is a generic placeholder, not unique source identity. |
| camera_name | N; no source | Optional configured description; do not fabricate from RTSP URL. |
| location | N; no source | Optional configured location. SCD1/current metadata loses historic moves, explicitly accepted by report. |
| stream_type | D/N; PS start/run_video_analysis, LR RTSP reader | Configure canonical rtsp/rtsps/video value; distinguish upload from camera. Report has no controlled vocabulary yet. |
| active | N; PS manager `running` is transient | Required configured camera-enabled state; do not map worker running/connection failure directly to administrative active. Define semantics. |

### dim_date

No date dimension currently exists. All fields are D/W from a valid newly collected UTC first_seen calendar date; no source exists in plate summary sufficient to recover it.

| Field | Source and status | Conversion and risk |
|---|---|---|
| date_key | D/W; event first_seen | Integer YYYYMMDD, not generated surrogate. |
| full_date | D/W; event first_seen | UTC DATE, unique. |
| day | D/W; full_date | Day of month 1–31. |
| month | D/W; full_date | Month 1–12. |
| month_name | D/W; full_date | Choose fixed language/locale in seed manifest; host locale must not change labels. |
| quarter | D/W; full_date | `(month-1)//3+1`. |
| year | D/W; full_date | Calendar year. |
| weekday | D/W; full_date | ISO weekday Monday=1, Sunday=7. Never substitute local date/time. |

### dim_pipeline_run

| Field | Source and status | Conversion and risk |
|---|---|---|
| pipeline_run_key | W | Generated BIGINT. |
| run_id | E(partial)/N; PS session UUID, API per-service job UUID | Hex UUID strings can be parsed as UUID, but none is shared by three recognition producers. Establish one coordinated execution UUID; keep reconnect epochs under it. |
| service_name | D/N; CA/adapter returned `service`, PS RTSP lacks shared name | Set stable nonempty coordinated producer name. Independent service names alone do not prove coordinated execution. |
| input_source | E/D/N; PR report `video`, VA `video`, LR `source_ref` | Use stable artifact/camera reference without credentials. Live `rtsp-camera:<id>:session=<n>` is connection-specific; full args.url must not be persisted unredacted. |
| output_video | E/D; PR `output_video`, PS recording path, CA/adapter artifact URLs | Optional durable artifact reference; current local paths/API URLs need retention semantics. Multiple service artifacts require an agreed primary reference and/or metrics references. |
| frames_processed | E/D; LR `processed_frames`, PR `frames`, VA `frames_processed`; CA `frames` | CA counts stdout payloads including EOF, so its frames value is off by an EOF record. Count processed decoded frames, not outputs or repeated annotation pass; define shared-run total without triple-counting same frame. |
| duration_seconds | E/D/N; VA `processing_seconds`; PR `analysis_seconds`/`total_wall_seconds`; LR loop timing | Monotonic execution duration. PR report `duration_seconds=frames/source_fps` is media duration, not this field. LR excludes final recognition drain in FPS denominator; collect/persist consistent end-to-end run duration. Colour adapter has no measured run duration. |
| processing_fps | D/N; frames_processed / selected monotonic duration | Use same scope and unrounded counters/duration; null at zero/unfinished duration. Existing live FPS excludes shutdown drain; PR FPS uses analysis not annotation. Do not mix these with a different duration field. |
| metrics | E(partial)/N; PR summaries, VA performance/ROI events, PP model_version, package config | JSON object; capture commit, actual loaded model artifact hashes/versions, configuration fingerprint and UTC start/end before publication as appropriate. Names/paths/config declarations are not hashes or evidence of effective deployment. Preserve stage/media timings and diagnostic provenance separately. |

### dim_vehicle_type

| Field | Source and status | Conversion and risk |
|---|---|---|
| vehicle_type_key | W | Generated BIGINT resolved by label; corrected name. |
| vehicle_type_name | E/D/U; VT `PUBLIC_VEHICLE_TYPES`, CS `VEHICLE_LABELS`/`TAXONOMY` | car, van, bus, truck, motorcycle, bicycle, unknown. camper→van, tractor→truck, cyclist→bicycle intentionally reduce raw taxonomy detail. Classification excludes person; CD's tracking path can map it to unknown because it does not apply VehicleDetector.predict's person filter. Record aliases and producer policy. |

### dim_plate_category

| Field | Source and status | Conversion and risk |
|---|---|---|
| plate_category_key | W | Generated BIGINT; unknown resolved by label. |
| category_name | E/D subject to validation; PV `PlateCategory`, PA `AgentCategory`, PP private route | private, temporary, diplomatic, military, foreign, other, unknown. Agent prompt groups police/security with military: obtain acceptance of that meaning. Never promote binary marker evidence to this taxonomy. Labels exist; that alone does not validate prediction rules. |

### dim_colour

| Field | Source and status | Conversion and risk |
|---|---|---|
| colour_key | W | Generated BIGINT. |
| colour_name | E/D/U; CT `SUPPORTED_COLOURS`, CD fallback | white, black, silver/gray, red, orange, yellow, green, blue, purple, brown/beige, plus unknown. Splitting either grouped label would invent precision. |

### dim_manufacturer

| Field | Source and status | Conversion and risk |
|---|---|---|
| manufacturer_key | W | Generated BIGINT, including unknown. |
| manufacturer_name | U/N; VA capability guard | Only reserved unknown supported by current producer. Future canonical catalogue/aliases need an explicit seed source, not imagined brands. |

### dim_vehicle_model

| Field | Source and status | Conversion and risk |
|---|---|---|
| model_key | W/U | Future generated BIGINT; facts use null until model known. |
| manufacturer_key | W/U | No current association source; future model rows must reference their actual manufacturer. |
| model_name | U/N | No current source or verified vocabulary. Seed future `(manufacturer,model)` catalogue; same name across brands is allowed, uniqueness is within manufacturer. |

## Lifecycle and identity gaps

| Closure case | Current behavior and evidence | Smallest necessary adjustment |
|---|---|---|
| Track loss | PR disappearance after 5 frames triggers OCR for eligible tracks; state stays in map. LR retires stale states after 300 frames only when no pending Future; metadata only if selected result exists. VP/CD remove lost tracks after their configured loss gap. | Distinguish recognition trigger, tracker loss and observation closure. Close every lifetime once, including tracks with no crop/attempt; include cause and settled results. |
| EOF | PR finish drains and marks selected predictions; CD main calls finalize_all after normal loop; VA finish follows loop. Neither CD nor VA places event finalization inside resource-cleanup finally. | Close all remaining observations as end_of_input on normal EOF and explicitly distinguish read error/max_frames. Preserve last visible frame. |
| Stop | PS stop sets Event; LR finally drains with generic stream_end. | Record service_stop separately from camera failure and limits; agree max_frames/max_seconds meaning. |
| Outage | LR reader internally retries. Old runner is finished as disconnect only after a successful reconnect generation is returned, or stream_end at terminal shutdown. | Detect outage boundary immediately, retain last receipt time, close old lifetimes with camera_outage; do not time sightings through outage/drain. |
| Tracker reset/reuse | LR recreates tracker per connection generation; display prefix helps within one invocation. PR setdefault merges same ID until retirement; CA overwrites same numeric ID; VP can have multiple finalized profiles with same ID. | Maintain per-run/camera/ID generation ledger, UUID per lifetime, explicit reset closure and new generation. No durable observation/message ledger exists today. |
| Abrupt crash/Future error | LR finally attempts cleanup but exceptions can prevent summary; Future.result can raise. No complete-event durable publication/recovery. PD fsyncs attempt diagnostics only. | Specify snapshot completeness now; durable retry/recovery remains T3. Do not equate append-only crop logs with complete observation transport. |

## Decisions needed before schema generation

1. **Authoritative observation grain and shared context.** The approved grain is a vehicle lifetime, while current live producer tracks plate crops. Choose a shared vehicle tracker/context and vehicle-to-plate association; do not rename plate lifetimes as vehicle events. This is a producer gap, not a reason to replace the four-fact/eight-dimension design. Existing independent uploaded jobs cannot be unified by matching track numbers. Specify how unavailable colour/type are represented until integrated; manufacturer/model remain unsupported.

2. **Category capability statement needs qualification.** Report page 5 correctly prohibits marker-only classification, but its instruction to keep detailed category unsupported does not fully describe this checkout: PV has a multi-evidence private rule and PA can predict six categories when explicitly enabled and credentialed. There is no inspected validation evidence establishing acceptance of those capabilities. Clarify whether v1 intentionally excludes them, or admits the private rule/optional agent after validation. Unsupported is defined as no capability; disabled existing capability is not_attempted, completed unresolved inference is inferred_unknown, and failed attempts need errors. Do not turn every category into known based on marker evidence, or claim no category code exists.

3. **Locked-type premise differs from implementation.** Report's warning is a useful constraint, but CD assigns latest type and confidence together; VP has consensus aggregation, not a locked label paired with unrelated latest confidence. Specify the event's type authority and metric; retain null confidence wherever exact-label provenance cannot be established. CS unused lock/vote fields are not evidence otherwise.

4. **Time bases need one explicit v1 policy.** Sighting UTC, monotonic interval, media position and processing closure are distinct. For host receipt, collect UTC and monotonic times at successful read before inference. For recorded video, state that this measures replay receipt/processing sightings, not original scene duration. If actual recording chronology is wanted, trusted capture timestamps and a documented duration basis are needed; report permits source_capture UTC while unconditionally defining interval by a monotonic clock. Clarify whether duration is always host-monotonic (potentially very different from source time) or a source timeline interval. Media-span diagnostics can remain in metrics. Define rollback handling for UTC so required first_seen<=last_seen is not silently repaired by inventing timestamps.

5. **Latency/count scopes.** Rename/reinterpret current summary values when mapping: local_total_ms includes marker/parser, crop total excludes queue/earlier attempts, media duration is not run duration, and colour adapter frames includes EOF. Define whether an OCR crop attempt with split OCR counts once (recommended) and whether agent fallback colour_samples denotes local supporting samples or only selected-result contributors. Require null for unmeasured values and explicit zero only when measured none.

6. **Normalization and vocabulary manifests.** PO normalization removes all non-ASCII alphanumerics, including Arabic marker and punctuation, and uppercases; PN constructs `<series> تونس <registration>` preserving digit strings; PA collapses whitespace and canonicalizes some private markers but otherwise trusts agent-normalized/visible text. A single versioned producer/evaluator normalizer is absent. Specify meaningful symbols, accepted full text, category-marker rules, Unicode digits, whitespace/case and completeness without silently using raw fallback. Version current grouped colours, vehicle aliases, category meanings and locale choices. No exporter/evaluator shared normalization contract was found in these paths.

7. **Schema completeness/ambiguities in corrected report.** Apart from date_key, page 2 says all `*_key` *primary* keys are generated; recognition/context FKs must be resolved, never generated independently. Recognition state, metric/error and envelope fields are described but exact nullability, error-code bounds, schema_version encoding and event_at meaning are not fully specified. Require all five recognition attribute states (text, category, colour, manufacturer, model), plus the event type's unknown-label policy; agree event_at=finalized_at for v1 or another explicit instant. Clarify colour_samples after agent replacement and known category with unresolved text. Run totals change separately while event/recognition payloads are immutable: exclude mutable run completion fields from semantic retry comparison. These need technical agreement, not architectural redesign.

The transport fields also need producer plumbing: no observation-level `message_id` exists; PP's fresh `sample_id` UUID identifies an inference attempt. PD's `schema_version=2` describes diagnostic storage, not the report's finalized envelope. Allocate a separate message UUID once at snapshot closure, preserve it and observation/run IDs and semantic payload on delivery retries, and publish all three result blocks together. Crop rechecks are inference attempts within an observation; transport retries are replay of an unchanged closed observation. No current UI snapshot or diagnostic append provides this complete message.

## Minimum recommended adjustment set

Retain all approved tables and relationships. Finalize the grain/time/category/type/normalization decisions above; define one producer-side event context (run UUID, camera, observation UUID, raw track ID/generation, frame counters, receipt clocks) and one closure snapshot with all states. Add only the missing instrumentation and explicit capability/error/metric metadata in a later implementation phase. Use unknown dimension members/null model and null unsupported scores rather than placeholders that imply inference. Keep raw labels, OCR attempts, replaced crops, route reasons and transient failures as diagnostics; their exclusion from analytical facts is intentional and becomes harmful only if diagnostics are discarded or their meaning is relabelled.

Static review and table extraction were the verification methods. No application code was changed, models executed/trained, tables created, migrations run, Redis implemented, branches switched or remote contents fetched. This phase does not freeze T1.
