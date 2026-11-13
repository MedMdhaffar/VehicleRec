# Data dictionary — v1-draft.1

> **DB1 reconciliation required (2026-10-08).** DB1 development contract v0 (2026-10-08) is the target database interface. This earlier draft is retained for producer evidence and proposal history; its tables, columns, vocabularies, cardinality and retry rules are not a DB1 specification. See [DB1 alignment](db1_alignment.md). No acceptance of unresolved semantics is claimed.

Working technical proposal, not executable SQL, deployed schema or frozen contract. Four facts and eight dimensions preserve the approved topology. Source mapping remains in [Phase 1](../../T1_PHASE1_MAPPING.md); definitions/decisions are in [contract_decisions.md](contract_decisions.md).

`NN` means NOT NULL; `NULL` means nullable. All fields are required on the wire, including nullable fields with explicit null, unless writer-derived/database-only. All *_key primary keys except date_key are generated BIGINT; FKs are resolved, never separately generated. BIGINT wire indices/IDs: 0..2^63−1; INTEGER counts: 0..2^31−1. Positive generated keys/generation; dates within year 1..9999. TEXT labels refer to versioned manifest, unknown by canonical label, never assumed numeric key. Every DOUBLE PRECISION must be finite. Empty/whitespace-only labels, metrics, source references and names are invalid. Error codes: nullable TEXT, 1–64 ASCII characters, `^[A-Z][A-Z0-9_]{0,63}$`, registered bounded code, no exception dumps.

## Cardinality and constraints

Each event references exactly one camera/date/run/type. Each recognition fact has UNIQUE(vehicle_event_key), so at most one row per event; guarded completed ingestion requires exactly one of each of the three, inserted atomically with event. Every recognition `(vehicle_event_key,pipeline_run_key)` has composite FK to event's same unique pair. Camera/run/type/category/colour/manufacturer dimensions are shared across events; each model belongs to one manufacturer. No event represents permanent physical-vehicle identity. No plate requirement exists on event.

States for all five recognition attributes are NN TEXT enum known/inferred_unknown/not_attempted/unsupported/inference_error. Known requires resolved value; other states require null text/model or unknown dimension label, and null score/metric. inference_error requires its attribute error code; other states require null error. Known categorical keys cannot reference unknown. A confidence/metric pair is both null or finite score [0,1] and registered metric. Crop quality uses finite >=0 unbounded scale and a separate paired metric. Known results may have null confidence.

## fact_vehicle_event

| Field | Type | Null | Key / rule / wire mapping |
|---|---|---|---|
| vehicle_event_key | BIGINT | NN | Generated PK; no wire field. |
| observation_id | UUID | NN | UNIQUE; top-level observation_id, one vehicle lifetime. |
| pipeline_run_key | BIGINT | NN | FK dim_pipeline_run; resolved from run_id. |
| camera_key | BIGINT | NN | FK dim_camera; resolved from camera_id. |
| track_id | BIGINT | NN | Top-level raw shared-vehicle tracker ID >=0. |
| track_generation | INTEGER | NN | Top-level >=1; lifetime reuse/reset counter. |
| date_key | INTEGER | NN | FK dim_date; UTC date of event.first_seen. |
| vehicle_type_key | BIGINT | NN | FK dim_vehicle_type; event.vehicle_type or unknown. |
| first_frame | BIGINT | NN | event.first_frame >=0, decoded-frame index run/camera. |
| last_frame | BIGINT | NN | event.last_frame >= first_frame, last visible contributing frame. |
| first_seen | TIMESTAMPTZ | NN | event.first_seen; host receipt UTC of first sighting. |
| last_seen | TIMESTAMPTZ | NN | event.last_seen; host receipt UTC of last sighting, >= first_seen. |
| sighting_time_basis | TEXT | NN | event value fixed host_frame_receipt in initial version; source_capture deferred. |
| track_duration_seconds | DOUBLE PRECISION | NN | event value >=0, last-first monotonic receipt, not media/FPS or recognition interval. |
| frames_observed | INTEGER | NN | event value distinct contributing decoded frames, 1..last-first+1. |
| final_reason | TEXT | NN | track_lost/end_of_input/service_stop/camera_outage/tracker_reset. |
| finalized_at | TIMESTAMPTZ | NN | Host UTC after settled snapshot closure; >= last_seen in host-only version. |
| vehicle_type_authority | TEXT | NN | **Proposed addition**: classification_consensus_v1/colour_latest_detector_v1/unavailable. Exactly one authoritative producer for stored label. |

UNIQUE(pipeline_run_key,camera_key,track_id,track_generation); UNIQUE(vehicle_event_key,pipeline_run_key). observation/message/run IDs stay unchanged on retry. Camera/run/ID/generation conflicts and immutable content conflicts are errors, never upserts. Unknown type requires null type score. Same-frame observation requires first_seen and last_seen to equal as UTC instants, zero monotonic duration and exactly one observed frame (semantic consistency rules). The separate event_at/finalized_at rule remains exact wire-string equality.

## fact_plate_recognition

| Field | Type | Null | Key / rule / wire mapping under plate |
|---|---|---|---|
| plate_fact_key | BIGINT | NN | Generated PK. |
| vehicle_event_key | BIGINT | NN | UNIQUE; composite event/run FK. |
| pipeline_run_key | BIGINT | NN | Same event run, resolved top-level run_id. |
| plate_text | TEXT | NULL | Complete canonical text, 1–128 characters, leading zeros retained; known iff non-null. |
| recognition_state | TEXT | NN | Text state, independent of category. |
| error_code | TEXT | NULL | Text inference_error only. |
| plate_category_key | BIGINT | NN | FK dim_plate_category; wire plate_category label. |
| plate_category_state | TEXT | NN | Independent detailed category state. |
| plate_category_error_code | TEXT | NULL | Category inference_error only. |
| confidence | DOUBLE PRECISION | NULL | Selected text score; paired confidence_metric. |
| confidence_metric | TEXT | NULL | OCR mean recognition score or plate-agent joint self-report registered name. |
| category_confidence | DOUBLE PRECISION | NULL | Selected category score, independent provenance. |
| category_confidence_metric | TEXT | NULL | Private-rule evidence minimum or plate-agent joint self-report. |
| selected_crop_frame | BIGINT | NULL | Event interval inclusive; required non-null for known text or known category; null allowed if no accepted result selects a crop. |
| selected_crop_score | DOUBLE PRECISION | NULL | Selected crop quality, finite >=0, not probability. |
| selected_crop_score_metric | TEXT | NULL | plate_crop_weighted_area_v1; paired with score. |
| recognition_attempts | INTEGER | NULL | Submitted crop operations including failed, >=0; whole/split OCR inside one operation counts once. |
| local_latency_ms | DOUBLE PRECISION | NULL | OCR call wall intervals on selected crop incl preprocessing/decoding; no queue/marker/parser/agent. |
| total_recognition_latency_ms | DOUBLE PRECISION | NULL | First crop submit to final resolution, incl queue/rechecks/retries; no publication time. |
| text_source | TEXT | NN | **Proposed addition**: local_ocr/agent/none. |
| category_source | TEXT | NN | **Proposed addition**: private_rule/agent/none. private_rule resolves private only. |
| normalization_profile | TEXT | NULL | **Proposed addition**: private_tn_v1/opaque_complete_v1, present iff text known. |
| agent_api_calls | INTEGER | NULL | **Proposed addition**: total actual HTTP attempts across crop operations, >=0. |
| agent_retries | INTEGER | NULL | **Proposed addition**: calls beyond first within each operation, >=0; not total calls minus one. |

Known text or category requires a supporting selected crop; quality/confidence may be null when unmeasured. Failed attempts need not select a crop. No selected crop implies null quality pair/local latency. Measured recognition_attempts=0 implies null crop/latencies and no selected source; known/attempted states cannot have measured zero. Unknown count is null, not zero. When both measured, agent retries <= calls; calls=0 requires retries=0, and calls>0 requires retries<calls. When all three counters are measured, `0 <= agent_api_calls - agent_retries <= recognition_attempts`; this difference counts crop attempts that made an initial agent call and must be positive when calls>0. Never infer null counters. Known agent-sourced text or category forbids measured agent_api_calls=0; null is allowed. The plate counter is independent of the colour agent. If both latencies measured, total >= local. Text and category sources may differ; known category can coexist with unknown text and vice versa. Known private category + known text requires private profile; detailed categories other than private have no validated structural parser here.

## fact_colour_recognition

| Field | Type | Null | Key / rule / wire mapping under colour |
|---|---|---|---|
| colour_fact_key | BIGINT | NN | Generated PK. |
| vehicle_event_key | BIGINT | NN | UNIQUE; composite event/run FK. |
| pipeline_run_key | BIGINT | NN | Same event run. |
| colour_key | BIGINT | NN | FK dim_colour; wire colour canonical label/unknown. |
| colour_state | TEXT | NN | Result state, not implementation lock/decision reason. |
| error_code | TEXT | NULL | Final colour inference_error only. |
| colour_confidence | DOUBLE PRECISION | NULL | Exact selected colour score. |
| colour_confidence_metric | TEXT | NULL | temporal_winner_weight_share_v1 or colour_agent_self_report_v1. |
| colour_samples | INTEGER | NULL | Retained valid local aggregation samples, >=0, still counts local evidence after agent override. |
| type_confidence | DOUBLE PRECISION | NULL | Score for exact stored event type/lifetime, not an unrelated latest label. |
| type_confidence_metric | TEXT | NULL | vehicle_type_consensus_v1 or vehicle_detector_latest_score_v1 matching authority. |
| selected_result_source | TEXT | NN | **Proposed addition**: local_temporal/agent/none; preserve override provenance. |

The separate colour agent is not counted by plate.agent_api_calls. Grouped silver/gray and brown/beige remain grouped. Known local temporal result with measured samples requires >=1 sample. Unknown colour requires null colour score, regardless of positive diagnostic winner share. Colour state does not control type score; type score depends independently on event's label/authority. Type-conf location is retained for architectural compatibility.

## fact_manufacturer_recognition

| Field | Type | Null | Key / rule / wire mapping under manufacturer |
|---|---|---|---|
| manufacturer_fact_key | BIGINT | NN | Generated PK. |
| vehicle_event_key | BIGINT | NN | UNIQUE; composite event/run FK. |
| pipeline_run_key | BIGINT | NN | Same event run. |
| manufacturer_key | BIGINT | NN | FK dim_manufacturer; wire manufacturer label, currently unknown. |
| manufacturer_state | TEXT | NN | Current capability unsupported. |
| manufacturer_error_code | TEXT | NULL | Final manufacturer inference_error only. |
| model_key | BIGINT | NULL | Wire model name resolved within manufacturer; present iff model known. |
| model_state | TEXT | NN | Independent model state; current unsupported. |
| model_error_code | TEXT | NULL | Final model inference_error only. |
| manufacturer_confidence | DOUBLE PRECISION | NULL | Exact selected manufacturer score; no current metric/capability. |
| manufacturer_confidence_metric | TEXT | NULL | Future registered metric; currently null. |
| model_confidence | DOUBLE PRECISION | NULL | Exact selected model score; currently null. |
| model_confidence_metric | TEXT | NULL | Future registered metric; currently null. |
| observations_count | INTEGER | NULL | Frame/crop outputs retained in manufacturer aggregation, >=0; 0 confirmed absent, null unmeasured. |

Known model requires known manufacturer; composite FK (model_key,manufacturer_key) -> dim_vehicle_model unique pair. Keep known manufacturer if model unsupported. Do not equate observations_count with all sightings.

## dim_camera

| Field | Type | Null | Rule |
|---|---|---|---|
| camera_key | BIGINT | NN | Generated PK. |
| camera_id | TEXT | NN | UNIQUE, stable registered ID, 1–80 ASCII alnum/._-, first alnum; no secrets, never silently truncated. |
| camera_name | TEXT | NULL | Current descriptive metadata. |
| location | TEXT | NULL | Current descriptive location; historical changes not preserved (SCD1). |
| stream_type | TEXT | NULL | Proposed rtsp/rtsps/uploaded_video, registry-managed. |
| active | BOOLEAN | NN | Administrative enabled state; not worker running/connectivity. |

Uploaded videos resolve an explicitly registered replay camera/source ID; static-video placeholder alone is not identity. Mutable descriptions are not transport payload. Registry provisioning is future operational work.

## dim_date

| Field | Type | Null | Rule |
|---|---|---|---|
| date_key | INTEGER | NN | PK YYYYMMDD, derived UTC first_seen, not surrogate. |
| full_date | DATE | NN | UNIQUE Gregorian UTC date. |
| day | INTEGER | NN | Day of month 1..31 consistent with full_date. |
| month | INTEGER | NN | 1..12 consistent with full_date. |
| month_name | TEXT | NN | Fixed English names from manifest, not host locale. |
| quarter | INTEGER | NN | (month−1)//3+1. |
| year | INTEGER | NN | 1..9999 from full_date. |
| weekday | INTEGER | NN | ISO Monday=1..Sunday=7. |

## dim_pipeline_run

| Field | Type | Null | Rule / mapping |
|---|---|---|---|
| pipeline_run_key | BIGINT | NN | Generated PK. |
| run_id | UUID | NN | UNIQUE; coordinated execution, shared by all outputs. |
| service_name | TEXT | NN | Nonempty, non-whitespace stable service reference; immutable run_context.service_name. |
| input_source | TEXT | NN | Nonempty, non-whitespace stable source/artifact reference, no embedded credentials; immutable run_context.input_source. |
| output_video | TEXT | NULL | Mutable optional primary artifact reference; additional artifacts in completion metrics. |
| frames_processed | BIGINT | NULL | Mutable >=0, each processed camera frame once, no EOF/service/annotation double count. |
| duration_seconds | DOUBLE PRECISION | NULL | Mutable finite >=0 host-monotonic elapsed execution incl drain, excluding later annotation export. |
| processing_fps | DOUBLE PRECISION | NULL | Mutable finite >=0, frames/duration if both measured and duration>0, else null. |
| metrics | JSONB | NN | Object, default {}; immutable provenance namespace plus separate mutable completion namespace. |

metrics.provenance holds schema/label/normalization/metric versions, immutable code_commit, configuration_sha256, model_artifacts, started_at, input_kind, capability configuration and its artifact reference when provided. metrics.completion holds optional ended_at, counters/timings and diagnostic/artifact references. All source/artifact references and component names must contain a non-whitespace character when present. JSON Schema enforces this for run_context.input_source, service_name, model_artifacts[].artifact_ref and model_artifacts[].component; completion/configuration artifact references remain future writer rules outside this envelope. Reference envelope fields map into provenance; they are not additional dimension columns. Immutable envelope contains no completion totals, output_video or ended_at. Values unresolved/unfinished stay null. Do not sum run measures after joining events. Metadata changes require explicit namespace/scope; conflicting provenance for same run is invalid.

## dim_vehicle_type

| Field | Type | Null | Rule |
|---|---|---|---|
| vehicle_type_key | BIGINT | NN | Generated PK (corrected column name). |
| vehicle_type_name | TEXT | NN | UNIQUE car/van/bus/truck/motorcycle/bicycle/unknown per draft manifest. |

Aliases camper→van, tractor→truck, cyclist→bicycle intentionally collapse raw classes; preserve raw diagnostic evidence. Person is excluded from unified vehicle producer, not treated as unknown vehicle.

## dim_plate_category

| Field | Type | Null | Rule |
|---|---|---|---|
| plate_category_key | BIGINT | NN | Generated PK. |
| category_name | TEXT | NN | UNIQUE private/temporary/diplomatic/military/foreign/other/unknown; candidate taxonomy, uncertain definitions flagged in manifest. |

## dim_colour

| Field | Type | Null | Rule |
|---|---|---|---|
| colour_key | BIGINT | NN | Generated PK. |
| colour_name | TEXT | NN | UNIQUE manifest label including unknown; preserve grouped colours. |

## dim_manufacturer

| Field | Type | Null | Rule |
|---|---|---|---|
| manufacturer_key | BIGINT | NN | Generated PK, including unknown. |
| manufacturer_name | TEXT | NN | UNIQUE canonical label; current manifest only unknown. |

## dim_vehicle_model

| Field | Type | Null | Rule |
|---|---|---|---|
| model_key | BIGINT | NN | Generated PK; no fabricated unknown row, facts use null. |
| manufacturer_key | BIGINT | NN | FK dim_manufacturer, must reference actual non-unknown manufacturer. |
| model_name | TEXT | NN | Nonempty verified canonical name; current catalogue empty. |

UNIQUE(manufacturer_key,model_name), UNIQUE(model_key,manufacturer_key). Same name across manufacturers allowed. No arbitrary dimensions are auto-created from predictions.

## Envelope-only fields and admission boundary

schema_version INTEGER const 1 denotes wire family, while all linked manifests remain v1-draft.1. message_id UUID is one finalized transport message per observation; ingestion must retain a durable operational ID/equality record or equivalent immutable snapshot outside this analytical topology (implementation deferred to T3). event_at TIMESTAMPTZ equals event.finalized_at; no additional event-time column needed. run_id/camera_id resolve FKs; track ID/generation/observation ID map directly. run_context/capabilities/manifest versions are fingerprinted provenance. JSON keys mirror scalar names except canonical dimension labels replace database keys; no producer surrogate keys or repeated per-result run identities are needed. All three blocks inherit the one enclosing run/observation context.

Schema checks shapes/enums/ranges/required fields/states/score pairs/configuration implications. Application admission additionally checks arithmetic, chronological ordering, event_at equality, canonical normalization/completeness, actual capabilities and score provenance, catalogues and metric registry, trusted receipt/clock history, stable identities/retry equality and atomic persistence. Validation of draft fixtures does not execute those operational checks against real producers or a database.
