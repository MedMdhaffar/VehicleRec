# DB1 vehicle-row contract package — P1 candidate

Version: `db1-row-snapshot-v0` / `db1-diagnostics-v1`
Target: supervisor-supplied DB1 development contract v0 (2026-10-08)
Status: review candidate; not senior-validated, frozen, deployed or supervisor-approved

This package is the pure P1 contract described by [db1_gap_resolution.md](../../docs/t1/db1_gap_resolution.md) and [db1_implementation_plan.md](../../docs/t1/db1_implementation_plan.md). It contains no runtime pipeline, database client, model invocation, transport or object-store code. The older files directly under `contracts/` describe the historical private star-schema proposal and remain unchanged.

## Projections and equality

Each row snapshot has two deliberately separate projections:

1. `delivery_payload` is the service-owned immutable natural payload. It contains the per-row UUID, organization UUID, natural run/camera/model/catalogue/media identities and measured evidence. It contains no DB-generated surrogate key. The producer allocates and freezes this payload before delivery. Its canonical SHA-256 is the **natural delivery hash**.
2. `resolved_sql_row` contains the exact writable DB1 fact columns after natural IDs have been resolved. It omits only the table's `GENERATED ALWAYS AS IDENTITY` primary key (`vehicle_id`, `plate_read_id`, `colour_read_id` or `make_read_id`). Its canonical SHA-256 is the **resolved SQL-row hash**.

An identical retry must match both projections already retained for that UID. Same UID with a changed natural delivery hash is a producer conflict. An unchanged natural payload with a changed resolved SQL-row hash is a key-resolution/context conflict. Neither may be hidden by `ON CONFLICT (uid) DO NOTHING`. A separately requested reprocessing result is a new logical row with a new UID.

Canonicalization sorts object keys, retains explicit nulls, retains array order, normalizes UUID strings to lowercase, converts aware RFC3339 timestamps to UTC with six fractional digits, and represents mathematically equivalent finite JSON numbers identically (`1`, `1.0`, and `1.00` compare equal; negative zero becomes zero). Missing and explicit null remain different. Duplicate object properties and `NaN`/`Infinity` are rejected before schema validation. Other strings remain exact after JSON decoding.

The fixture lookup block is test infrastructure, not a delivery field. Every lookup declares `synthetic: true`; its numeric keys are fictional and must never be treated as seed or production identities.

## Constraint layers

The schemas encode exact JSON representations of DB1 writable fact columns and basic shape/nullability. The pure validator separates three layers:

| Layer | Examples |
|---|---|
| DB1 v0 constraints | required fact columns; PostgreSQL integer ranges; `fact_vehicle.ts = first_ts`; plate outcome/text equivalence; attempts >= 1; score ranges; make/model FK shape. |
| Stricter service rules | one-camera parent context; each row's local date; exact selected plate attempt evidence; route/model task; class group; colour state/value rules; approved catalogue mapping; media org/run/camera/kind; immutable retry equality. |
| Proposed platform adjustment | faithful `no_plate`: nullable model, attempts 0, outcome-evidence timestamp and null crop/result fields. It is documented and fixture-tested only as a proposal. It is not valid under the supplied DB1 v0 SQL and is never emitted by this package. |

The direct `fact_vehicle_make.manufacturer_key → dim_manufacturer` FK already exists. Future make-row fixtures exercise the valid structural shape without claiming that the current producer can infer make or model.

## Plate evidence and omissions

Every emitted plate row is built from one selected attempt. Its timestamp, crop media, crop score, text/category/confidence and model identity all come from that attempt. The reference selector considers only associated, accepted crops for which a recognizer actually executed and a receipt timestamp exists. For unreadable results it chooses the highest finite crop score, breaking ties by earliest submission ordinal. It never borrows a model from another attempt. If no eligible attempt exists, no DB1 v0 row is emitted.

Omission diagnostics distinguish:

- `no_detection`: no plate detection associated with the vehicle; this is not a claim that the physical vehicle had no plate.
- `rejected_crop`: detections existed but no crop passed admission.
- `ambiguous_association`: eligible vehicle parents tied within the provisional margin.
- `unassociated_crop`: no eligible vehicle parent.
- `disabled_capability`: configured capability was disabled.
- `unsupported_capability`: the integrated producer lacks the capability.
- `failure_before_model`: an eligible crop existed but no registered recognizer executed.

DB1 v0 cannot truthfully store the no-crop cases because `fact_plate_read` requires attempts >= 1, a model key and a crop-based timestamp. They remain omissions. The proposed adjustment in the design documents is not applied here.

`text_complete=true` is necessary for an agent-sourced `read`, but it is only a producer assertion. It does not independently prove visual transcription completeness or accuracy. Local private reads also require structural validity. Common text normalization is lossless with respect to punctuation/scripts and leading zeros; category meaning is independent.

## Provisional service configuration

`catalogue_requirements.v0.json` versions the plate-association thresholds and initial clock tolerance. The association rule uses 0.80 containment, a 0.10 winning margin and weights 0.70/0.30. Clock-pair divergence above 5 seconds marks the epoch invalid. Boundary fixtures define implementation behavior. These values have no empirical accuracy validation and are not DB1 or supervisor requirements.

Grouped `silver/gray` and `brown/beige` remain exact vehicle-owned catalogue requirements. They are never split or silently mapped to the seed's `grey`, `silver` or `brown`. `private → TN` and `temporary → RS` are prohibited. Foreign and diplomatic mappings remain domain-gated, so fixtures keep their category key null unless a synthetic lookup explicitly marks a mapping approved.

## Files

- `rows.v0.schema.json`: natural delivery and exact resolved SQL-row projections for all four facts.
- `diagnostics.v1.schema.json`: minimal finalized-row/omission/quarantine sidecar; it cannot contain plate text or local paths.
- `catalogue_requirements.v0.json`: required classes, vehicle-owned catalogues and provisional algorithm configuration.
- `model_registrations.v0.json`: natural model registrations and selected-route rules.
- `reference.py`: pure reusable association, selection, time, omission, normalization and canonicalization helpers.
- `fixtures/`: positive/negative row and diagnostic cases plus pure semantic vectors.
- `validate_fixtures.py`: strict decoder, metaschema/schema validation with format checking, and semantic fixture runner.

## Run

Use an existing interpreter that provides `jsonschema`:

```bash
/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
```

The script reports `jsonschema` availability and version. If unavailable, it exits with a limitation instead of pretending its semantic checks replace JSON Schema/metaschema validation.

## Validation boundary

Passing fixtures makes this contract reviewable. It does not freeze T1. T1 requires senior review, validation and an explicit frozen-contract decision before P2/P3 producer integration can be considered against a stable target.

Schema and fixtures cannot establish that a real crop belongs to a vehicle, a timestamp was captured before inference, a model or artifact actually ran, a transcription is complete, a score supports its value, a camera timezone is authoritative, media bytes exist, or a caller is authorized. JSON Schema also cannot reliably detect arbitrary credentials or secrets hidden in otherwise permitted free-text identifiers. Those require producer evidence, trusted registries, access controls and later integration checks.

Retry fixtures are in-memory comparisons. They do not prove persisted deduplication, concurrent first-delivery safety, transaction rollback, acknowledgement ordering or crash recovery; those remain P4/P5 work.

## P1 correction limits and selection

Canonical numbers use exact Decimal tuple operations, independent of ambient
precision. Equivalent encodings (including signed zero) have equal bytes. Inputs
are limited to 4096 coefficient digits and absolute stored exponent 100000,
including zero; larger inputs reject. Scientific notation keeps output bounded
rather than expanding powers of ten. These are service input limits, not SQL
constraints. Canonical byte hashes must be regenerated for this corrected profile
before any persisted outbox is introduced.

Timestamps require explicit-offset RFC3339 with zero to six fractional digits;
unsupported precision rejects before datetime parsing. Supported equivalent
instants canonicalize to UTC microseconds. Leap seconds are unsupported.

Plate delivery and SQL outcomes must match. No usable crop still omits the v0
row. Unreadable selection requires submitted, associated, accepted crop evidence
with a recognizer that actually executed, all from the same attempt. Measured
finite nonnegative quality ranks ahead of null (unmeasured) quality; measured
scores rank descending. Ties and unmeasured fallback use earliest submission
ordinal, then lexical attempt ID. Invalid measured quality is ineligible. Null
quality stays null and must never imply failure before model execution.

## Unresolved shared plate operations

The optional diagnostic settlement object records incomplete, submitted-operation
count and unresolved-operation count. `unsettled_operation` is a plate omission
with at least one submitted and unresolved operation, no selected attempt and no
row UID/payload. It cannot be relabelled failure_before_model or unreadable.
Deadline-expired running work or a lost worker outcome has unknown execution;
a queued operation cancelled before execution is settled and uses its ordinary
omission policy. This diagnostic extension changes no SQL/delivery row contract.

P3a runtime construction reuses `row_policies.py` for pure P1 row semantics and
native shared-plate completeness admission. The fixture validator imports those
policies and separately enforces its synthetic-lookup marker. `strict_loads`
exposes the existing strict JSON policy for immutable result bytes, without I/O.

Diagnostics v1 additionally permits `construction_blocked` **only on quarantine
records**. Its bounded error code distinguishes inconsistent completed evidence,
parent projection unavailability and changed-payload conflicts. This reason does
not assert a clock discontinuity. The new
`fixtures/construction_diagnostics.v1.json` covers valid quarantine and invalid
omission usage; historical fixtures and rows v0 are unchanged. Native construction
metadata is separate from the strict diagnostic envelope. See
[the pure builder boundary](../../vehicle/common/db1_rows/README.md).
