# P3a — pure DB1 row construction, 2026-10-09

Implemented construction from immutable completed classification/colour/plate
observations. Natural payloads, resolved SQL projections, P1 diagnostics and native
construction metadata remain distinct. Construction performs no inference, file
lookup, SQL, network, transport or coordinator acknowledgment. All exercised
registration records are explicitly synthetic **test** records.

The P2b1/P2b2a/P2b2b and shared-smoke reports were read as context. Actual owner
aggregation, completed evidence, colour finalization, plate execution/selection,
coordinator claim/finalize/release, P1 schemas/helpers/manifests/fixtures and
supervisor archive SQL/README/seed were inspected. Prior reported passes were not
used as proof; current checks below were executed. No real models/video were rerun.

## Changed files and public interfaces

New package `vehicle/common/db1_rows/`:

- `records.py`: frozen `RunResolution`, `Dimension`, `ResolvedModel`,
  `MediaResolution`, `Resolutions`. Explicit SQL integer bounds, natural identities,
  camera registration, calendar validity, duplicate identities/keys/object-store
  identities, model-key collision and crop-support metadata checks.
- `validation.py`: `ContractValidation(row_schema, diagnostic_schema)`, owning
  caller-loaded schema copies. Draft 2020-12 and `FormatChecker` are mandatory;
  timestamps reuse P1 precision checking, external schema references are rejected.
- `builder.py`: `RowBuilder(identity_policy=..., validation=...).construct(
  completed, resolutions, prior=None) -> Construction`;
  `UUID5Policy(namespace)`, `FactResult`, `FrozenJSON`. `Construction.ready_rows`
  excludes blocked/conflicting proposals. `.snapshot()` is a review copy, not a
  delivery action. Exact retained canonical bytes are immutable; `.value()` returns
  independent copies with exact fractional Decimals and mathematical integer keys.
- `__init__.py` exports these interfaces; `README.md` documents acceptance,
  resolutions, media, hash scope, identity mappings and limits.

Other changes:

- `vehicle/common/observation_evidence.py`: frozen `RecognitionIdentity` for
  explicit executed OCR/classify/VLM names, INT versions and tasks, without keys.
- Colour `evidence.py`: optional executed supporting-model identity and defined
  last-attempt receipt in `FinalColourEvidence`.
- Colour `shared.py`: optional `local_model` / `agent_model`, checked against
  classify/VLM routes and read-only for the run; finalization freezes the identity
  of the actual supporting local/agent route. Existing None defaults preserve
  standalone/shared compatibility but cannot make a DB1-ready colour row.
- Colour `tests/test_shared_colour.py`: two producer provenance/constructor tests.
  Existing detector, temporal formulas, thresholds and agent admission are intact.
- Plate `shared_evidence.py`: `RecognizerModel` accepts an explicit VLM task in
  addition to its compatible OCR default. Existing agent records claiming OCR are
  blocked by construction; no task is silently rewritten.
- Plate `shared.py`: the existing analytical native-result completeness admission
  is factored through the pure P1 policy helper. Crop/model execution eligibility,
  selection authority and aggregation/replacement logic remain unchanged.
- `contracts/db1/row_policies.py`: reusable P1 row semantics extracted from the
  fixture harness, plus the shared native plate admission helper. The harness alone
  retains the fixture-only synthetic-key check. Runtime never imports the harness.
- `contracts/db1/reference.py`: pure `strict_loads` exposes exact Decimal,
  duplicate-property and nonfinite-number rejection; file `strict_load` delegates.
  Existing normalization, canonicalization, unreadable selection and retry helpers
  are reused.
- Diagnostics schema: one new reason, `construction_blocked`, allowed only for
  quarantine records. P1 previously had no truthful reason for malformed evidence,
  missing eligible parent projection or a payload conflict. Error codes retain
  the exact distinction; this does not claim a clock fault.
- New `fixtures/construction_diagnostics.v1.json` and validator coverage: valid
  blocked quarantine / invalid omission. **Historical fixtures, rows v0 and both
  manifests were not edited.** Contract/common READMEs link the boundary.
- `vehicle/common/tests/test_db1_rows.py`: 65 construction tests, including an
  actual injected coordinator completion callback and retry after release.
- This report.

Supervisor `db1_contract.zip` was read only. Its SHA256 remains
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`;
checksum verification passed. No supervisor SQL/checker/seed was modified or run.

## Field, evidence and resolution mapping

All projections use exact supervisor insert columns and omit generated fact PKs.
Natural payloads identify org/run/camera/model/dimensions/media independently of
SQL numeric keys. Format and semantic validation applies to every generated
resolved snapshot; natural-only eligible payloads receive their P1 delivery schema.

| SQL fields | Evidence and supplied resolution |
|---|---|
| All facts: `org_id` UUID, `run_key` BIGINT, `camera_key` BIGINT | Original context org/natural run/camera and complete matching `RunResolution`; camera includes its SCD2 validity interval and timezone. |
| All facts: `date_key` INT, `ts` TIMESTAMPTZ, `uid` UUID | Camera-local date from that fact's supporting receipt, registered calendar membership, injected stable row identity. |
| All facts: `track_id` BIGINT | Allocated service lifetime ID; raw tracker IDs never become row ownership. |
| Vehicle: `first_ts`, `last_ts` TIMESTAMPTZ | Original first/last shared receipts; `ts=first_ts`; ordered timestamps/frame span checked. |
| Vehicle: `class_key` SMALLINT, nullable `class_conf` REAL, `frames_observed` positive INT | Exact final classification label/confidence and observed count; supplied approved vehicle-group class, no aggregation rerun. Person is excluded. |
| Vehicle: nullable `best_media_key` BIGINT | Null currently: classification retains no owned selected crop. Supplying a purported selected vehicle media record is blocked rather than borrowing colour's best crop. |
| Colour: `model_key` BIGINT, nullable `colour_key` SMALLINT / `conf` REAL, `state` TEXT, `samples` nonnegative INT | Supporting executed local/agent identity, exact approved grouped label, authoritative final state/confidence and retained valid sample count. Undecided/error fields remain explicit null. |
| Plate: `model_key` BIGINT, `outcome` TEXT, nullable `plate_text` TEXT / `plate_category_key` SMALLINT / `conf` REAL, `attempts` positive INT | Same selected original operation's executed route/model, admitted canonical text/confidence/category, total crop-operation ledger length; unreadable fields explicitly null. |
| Plate: nullable `crop_media_key` BIGINT / `crop_score` REAL | Matching selected owned crop and supplied media record, or explicit null; score is the selected operation's measured finite quality or null. |

Run resolution additionally checks lane, actual detector, file/stream source,
input clip registration, start and service version against the original context.
Organization/camera/run/model/media mismatch blocks projections. A missing eligible
vehicle projection blocks recognition projections. Unprovisioned development
contexts remain SQL-ineligible; their natural payloads can still be inspected.
Provision a resolved run before capture rather than relabel old development
observations. Synthetic registration is test-only. No production catalogue or
model defaults are taken from synthetic manifests or supervisor development seeds.

The supplied records establish internal consistency, **not** authorization,
platform registration, catalogue existence, upload completion, artifact loading
or object existence. Media object SHA256/bytes refer to the claimed encoded object;
a separate raw-crop SHA256, dimensions, frame and receipt bind supporting evidence.
A local path/raw fingerprint cannot substitute for a registered object record.
Input media permits null run_key, matching supervisor SQL; its claimed org/camera
and natural run still must match. This service requires explicit media camera
ownership even though the database permits null camera_key. Crop dimensions and
SHA256 have stronger positive-size/hex admission than the SQL media constraints. Missing optional crop media yields null SQL
references with `media_unavailable`, whereas provided mismatches block construction.

Service admission is deliberately stronger than database constraints: positive
service/key identities, finite nonnegative quality, supported colour states,
explicit completeness/model-route evidence (including required native read
confidence/category fields and nonnull confidence) and omission of no-crop `no_plate` under
DB1 v0. Integer bounds follow SQL types. Confidence is in [0,1]; quality must fit
REAL range, with nonzero underflow rejected. Server-side REAL binary rounding is
not simulated or tested; canonical construction preserves the admitted number.

## Supporting times and ownership

- Vehicle uses first receipt. Local decided colour uses the last retained valid
  contributing sample; local undecided/error uses the frozen last-attempt receipt.
- Accepted colour agents use the receipt of the actual submitted owned crop,
  possibly earlier than later vehicle samples. Failed agents preserve usable local
  support and local model attribution.
- Plate support, crop bytes, quality, normalized text, confidence and execution
  attribution must agree with the same selected ledger operation and original
  vehicle/segment. The builder preserves the producer's selected read authority;
  unreadable ranking reuses P1 measured-first/null-quality deterministic fallback.
  An unreadable ledger containing an admitted executed read is inconsistent.
- Same-frame receipts across the owner and both consumers must agree; lifetime
  endpoint receipts and retained bytes/geometry are checked. Original parent/run/
  camera/raw lifetime/closure consistency is checked independently of detection
  counters. Shared/unassociated global counts never establish analytical ownership.
- File evidence remains `host_replay_receipt`. Media PTS and processing completion
  never supply ts/date. Negative native PTS stays in native metadata, with P1 PTS
  null; it is neither clamped nor interpreted as recording time.

Quarantine blocks **all** facts. Unsettled plate operations omit plate facts with
explicit incomplete/unresolved operation counts; independently settled vehicle and
colour facts remain eligible. No late operation is reclassified as unreadable or
failure_before_model. Zero operations, no detection, rejected/ambiguous/unassociated
crops, disabled/unsupported capability, submission/capacity policy failure and
executed unreadable OCR remain distinguishable. `failure_before_model` can mean
policy prevented dispatch and is not a claim that OCR raised an exception.

Grouped colours remain `silver/gray`, `brown/beige`, etc.; no seed aliases are
invented. Unknown plate category maps to null; other producer category claims
require an approved same-name mapping. Missing category/grouped-colour mappings
produce diagnostics rather than guesses. Manufacturer capability remains absent;
make is always unsupported omission, with P1 structural examples unchanged.

## Identity, equality and acceptance

`UUID5Policy` requires an explicit stable namespace. Canonical name arrays contain
org UUID, natural camera/run identity and service track ID; fact identity adds
fact kind, operation identity adds native segment and attempt ID. Native raw-ID
reuse with a new service ID therefore receives new fact UIDs. Segment changes
cannot transfer attempt UUID ownership.

P1 diagnostic epoch is native zero-based epoch plus one. Native run-wide operation
ordinal maps to one-based ascending rank within the finalized vehicle's ledger,
not a recognizer/provider retry count. Native segment/ordinal BIGINT values, native
attempt ID, supporting receipts and mapped IDs/ranks remain in a separate frozen
construction metadata sidecar. P1's strict envelope is not extended with arbitrary
native properties. Final evidence does not retain full geometry scores, so
association remains null; the builder does not fabricate them from counters.

Delivery SHA256 covers exact P1 canonical natural payload bytes: UID, selected
attempt UUID, receipt timestamp(s), results/model identity, explicit nulls, operation
or sample counts and supplied natural media identity. It excludes numeric SQL keys,
closure/PTS and native metadata. Separate SQL SHA256 covers the entire canonical
projection, including numeric resolution keys. Neither hash covers all historical
attempt result bytes, camera validity fields, detector artifacts or crop bytes
outside an explicitly supplied media identity. P1 canonicalization retains exact
Decimals independent of decimal context, equivalent number/instant/UUID equality,
explicit-null distinctions and existing numeric-size bounds.

Caller-supplied prior **accepted** construction supports in-memory retry comparison.
Identical natural/SQL payloads remain identical with the same UID. Changed payload,
changed SQL resolution, changed UID policy or loss of a prior projection conflicts
and cannot enter `ready_rows`. Resolving previously absent SQL is permitted only
when natural payload remains identical; adding media after accepting a null-media
payload changes the natural hash and conflicts. No hidden cache/deduplication store
is maintained. Persisted idempotence, concurrency and crash recovery are untested.

Builder calls leave coordinator evidence/pending state untouched. The integration
test checks pending state during construction, then allows callback acceptance,
checks consumer release and reconstructs an identical retry from retained completed
evidence. A future host must raise/reject or retain proposals according to its
acceptance policy before returning from a coordinator callback, since normal
callback return triggers owner/consumer acknowledgment.

## Exact executed validation

Existing VM interpreter `/home/intern/.venv/bin/python` (Python 3.11.15),
jsonschema 4.26.0. Every command below used `PYTHONDONTWRITEBYTECODE=1`.

| Command after the environment prefix | Executed result |
|---|---|
| `/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py` | **122 passed, 0 failed**; prior 120 plus two new construction diagnostic checks; Draft 2020-12 format checking enabled. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_db1_rows` | **65 passed**, final run 1.435 seconds. |
| `/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_foundations` | **19 passed**, 0.009 seconds. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/tests` | **24 passed**, 0.171 seconds. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/vehicle_color_service/vehicle_color_service/tests` | **33 passed**, final run 0.077 seconds; prior 31 plus two producer provenance tests. |
| `/home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests` | **57 passed**, 0.139 seconds. Provider error message in this suite is from its injected timeout test, not a real provider call. |

Additional executed checks: AST parsing of all 14 changed Python sources passed;
whitespace checking of all 20 changed files passed; `git diff --check` passed;
`sha256sum -c /tmp/p3a-supervisor.sha256` passed. The syntax/whitespace check used
the same existing interpreter and did not import models or produce bytecode.

Builder tests cover resolved supported facts, missing/unresolved development
resolution, org/run/camera/model/media mismatches, camera-local midnight and
receipt/PTS separation, original attempt/segment and null-quality consistency,
zero/submission-failed/cancelled/unsettled/quarantined omissions, grouped labels and
unresolved categories, native ID/epoch/rank mapping (including large global IDs),
deterministic retries/conflicts, absent make, immutable evidence/results/explicit
nulls, exact decimals/REAL admission, schema formats and malformed evidence.
The actual coordinator test uses injected detector/tracker/recognizer/clocks; it
opens no capture and invokes no real model/provider. It verifies three vehicle
inference calls for three frames and three supported fact constructions.

Initial implementation tests exposed OCR task admission and integer decoding from
P1 scientific canonical bytes; both were corrected. Later targeted checks tightened
shared-frame consistency, missing receipts, invalid flags/counts, registered
calendar/object uniqueness and conflict handling. The final results above include
those corrections. Unrelated suites and real models were not repeatedly rerun.

## Remaining dependencies and next scope

This is ready as a **pure proposal boundary**, not a production delivery path.
Subsequent work must supply platform-approved organization/camera SCD2/run/lane,
actual detector/recognizer/algorithm registrations, dates and required dimension
mappings. Current smoke evidence is intentionally unprovisioned development
metadata, redacted rather than retained full recognition evidence, and cannot be
claimed as registered SQL rows.

Hosts must explicitly bind the colour algorithm/prompt identities to actual
configured implementations and use VLM attribution for plate agents. Supporting
classification best-crop retention is still missing; best_media remains null.
Grouped colour and producer plate-category registrations remain domain dependencies;
no alias mapping is guessed. Selected crop media registration must supply real
encoded-object identity and raw supporting-evidence bindings. Input clip
registration is required for resolved file runs.

A future coordinator completion sink can call `RowBuilder.construct` before
accepting evidence, retain stable UUID namespace/prior accepted state, inspect
omissions/conflicts and then pass accepted natural payloads to a separately designed
registration/media/delivery boundary. That later boundary must address durable
retry identity, deduplication/conflict policy, transport, media existence and
registration/authorization. None is implemented or claimed here.

RTSP reconnect wiring, the unrelated classification CLI dispatch issue,
manufacturer inference and DB1 v0 no-crop/no_plate limitations remain unchanged.
No SQL execution, database connection, Redis, upload, provider execution, training,
T2 work, T1 freeze, commit or push occurred.
