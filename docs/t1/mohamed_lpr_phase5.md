# Mohamed LPR Phase 5 — opt-in Tunis delivery, PASS

Validated 2026-11-08. The corrected bounded execution demonstrated real CUDA
recognition → sealed v3 Redis decision → existing Aggregator → existing DB1
consumer → PostgreSQL 16.14 → confirmed Redis ACK, within one run. A preceding
attempt stopped at projection before DB delivery; it is retained separately,
with its real binding mismatch and correction explained below. The passing
trace does not combine recognition from one run with database results from another.

T1 v2 remains the default. Nothing subscribes the DB1 consumer directly to the
v3 review stream. No production writes, shared catalogue changes, catalogue
contract edits, supervisor SQL edits, RTSP, commits or pushes occurred. Test
registrations were seeded only inside fresh disposable databases through the
existing explicitly authorized P4 fixture.

## Starting state and patch

Read the Phase 4 completion report; closed sealed-result validation in
`mohamed_lpr_stream_codec.py`; v3 harness/Engine adapter; Aggregator/natural
builders; DB1 consumer/adapter, catalogue/model contracts and P4 fixture guards.
Recorded all starting non-cache files and expanded Git status before edits.
The read-only Mohamed checkout remains clean at
`f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`. All locked artifacts, source/vendor
code, character dictionaries and eight DINO references still verify.

Archive SHA256 remains
`b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
All 270 reviewed files matched at the start. The only intentional difference
among them is the optional reader injection in `db1_consumer.py`; the other
269 remain byte-identical. Phase 1–4 reports and policy/contract/model code
remain unchanged. The Phase 4 review driver has an explicit optional hook;
its ordinary invocation still produces review results only.

| Changed/new file | Responsibility |
|---|---|
| `vehicle/common/db1_consumer.py` (modified) | Optional `plate_reader`, defaulting to the original v2 terminal validator. Transaction, reference validation, history, persistence and ACK logic remain unchanged. |
| `scripts/vehicle_mohamed_lpr_redis_mp4.py` (modified) | Explicit callable review hook after scalar parity; checks that delivery invokes no recognition, reports delivery counters and bounded error codes. Default hook is None. |
| `vehicle/common/mohamed_lpr_delivery.py` | Separate closed delivery envelope and typed Tunis result; full proof validation, truthful selected fields and existing Aggregator construction. No I/O, model calls or numeric keys. |
| `vehicle/common/mohamed_lpr_delivery_stream.py` | Fenced projector from sealed review to canonical DB1 delivery, with independent group, output recovery and duplicate protection. |
| `vehicle/common/mohamed_lpr_test_database.py` | Explicit disposable P4 host and separate-connection row/receipt checks; never a production registration source. |
| `vehicle/common/tests/test_mohamed_lpr_delivery.py` | Injected recognition with native Redis/PG16 success, failure, retry, omission and default-v2 cases. |
| `scripts/vehicle_mohamed_lpr_db1_child.py` | CUDA producer/projector; private control handshake with a separate DB1 consumer. No DB credentials or psycopg in CUDA. |
| `scripts/vehicle_mohamed_lpr_db1_smoke.py` | Explicit test host, temporary verified Redis client, PG preflight, restricted consumer, verification and owned-resource cleanup. |
| `docs/t1/mohamed_lpr_phase5.md` | This trace, representability rules, commands and limits. |

Evidence directory:
`/home/intern/part-time/aziz/mohamed-lpr-phase5-validation-20261108/`.
`starting-state.json`, `starting-git-status.txt`, exact command JSONs/logs,
`real-e2e/` (failed attempt), `real-e2e-corrected/` (passing execution), and
`final-verification.json` retain the audit trail. Actual readings/message bodies
are excluded from shared summaries. Neither the older failed Phase 4 evidence
nor its passing completion evidence was overwritten.

## Representability and ownership

The existing **fact-row contract is sufficient for the exercised read**. Its
v2 Engine transcript cannot truthfully contain Arabic classification/DINO
operations or their original dependencies. The new opt-in outer envelope is
`vehicle-mohamed-tunis-delivery-v1`; its kind remains `db1.delivery.v1` and its
bundle remains `natural-db1-bundle-v1`. The current closed fact schemas and SQL
are unchanged. The code in `TunisDeliveryCodec.decode` is the closed transport
validator; it recomputes the existing Aggregator's complete canonical bundle.

A distinct `TunisTranscript`/`TunisResult` validates the unchanged Phase 1/2 Plate
decision by replaying every exact source message, including retirement and
settlement. It does not fabricate a v2 transcript, remove its missing dependencies,
or rerun the old private policy to take ownership away from v3 Plate. It satisfies
the existing typed Plate-result interface used by Aggregator/NaturalPlateBuilder.
The consumer's explicitly supplied reader authenticates this terminal; its
default reader still authenticates v2 exactly as before.

| Required value | Truthful representation |
|---|---|
| Vehicle | Exact org/run/camera, service/raw tracker ID, epoch and lifetime sequence; v3 vehicle hash must match the Engine closure. Existing Aggregator consensus uses original class receipts. SQL parent identity is unchanged. |
| Frame/crop/association | Original paired receipt, original-frame bbox, crop/frame hashes, public crop/candidate ID and plate segment. Receipt hash must equal v3 frame ID. Source scope and full operation/dependency graph remain attached. |
| Canonical registration | The accepted string fields become `<series> تونس <registration_number>` through the current private canonical profile. Leading zeros are retained. This is distinct from raw OCR, v3 normalized registration and display labels. |
| Selected attempt | One logical crop attempt, not four model operations. Stable attempt UUID derives from the original vehicle/segment and public crop ID. The original registration operation ID is retained in provenance. |
| Route | `local_private`, only for the authorized `tunis → private` projection. There is no relabeling of other categories. |
| Model | An explicitly supplied OCR binding must match the actual successful registration `Provenance.model_ref`. Natural model name/version/task resolve through supplied registrations; SQL `model_key` is checked against the actual DB row. No borrowing from Arabic OCR, DINO, marker or a configured v2 default. |
| Confidence | Native registration OCR score, not DINO cosine or a manufactured category probability. The unchanged receipt mechanism preserves exact pre-REAL values. |
| Trace preservation | Full original sealed envelope and all source log messages are retained as JSON strings in Plate provenance and the canonical delivery. Strings preserve v3 float/int hash identity across the existing P1 serializer. The DB acceptance stores the complete canonical delivery; row receipts store exact natural/SQL projections and hashes. |

SQL has no frame-index, route or attempt-UUID column: these remain truthfully
represented in the selected natural delivery/diagnostics, full v3 provenance
and immutable acceptance/row receipts. SQL `ts`, `track_id`, parent/run/camera,
category/model keys and `plate_text` are checked independently against them.
No extra SQL columns or guessed values were introduced.

An explicit binding to the loaded generic PP-OCRv6 export is required. In the
disposable host its model name is the actual `model_id` plus full provenance
fingerprint; registration version 1 names that test binding, not a claimed
training release or fine-tuning. The real run's model artifact/configuration/
preprocessing/runtime/device fingerprints are retained in its CUDA summary.

The first projection deliberately admits only **accepted Tunis, generation 1,
legacy_whole, whole-registration evidence, one complete Plate session per
vehicle**. Marker-first, regional/fused reads, rechecks and multiple-session
selection need their own delivery attribution/selection contract and cannot be
silently collapsed into this attempt. Missing original receipt/context/binding
blocks projection. Unresolved/unmapped/mode gaps produce a durable review-only
reason with no DB1 delivery or recognized plate row. No new recognition threshold
or legal-format claim is made.

## Category and catalogue decisions

The authorized mapping is to the existing producer category **`private`**, not
an assumption that a supervisor seed named `TN` has an approved equivalent
definition. The DB consumer still requires an approved supplied `private`
dimension and an actual matching database registration. No key is hard-coded
in the projector. Fixture key 31 and model key 82 are disposable test identities,
never production keys or new catalogue assignments.

| Source category | Current handling | Still-required catalogue/domain decisions |
|---|---|---|
| `tunis` | Accepted supported shape → `private`; unresolved/unsupported shape remains review-only. Missing private/model registrations cannot commit or ACK. | Production needs an actual approved private definition/member and exact executed-model registrations. No automatic `private=TN` seed alias. |
| `idara` | Review-only; no DB1 delivery. | Define administrative/government jurisdiction/category semantics; approve a producer vocabulary/mapping and actual member/key; approve number profile and selected local OCR route/model. Do not alias to diplomatic/foreign/private. |
| `nt` | Review-only; explicit NT evidence and its precedence remain in the retained proof. | Define NT's domain/jurisdiction and its relationship, if any, to temporary/RS; approve vocabulary/member/key and number/route/model contract. No `NT=temporary` assumption. |
| `shorta` | Review-only; never private or military by convenience. | Approve police/security semantics and any distinction from military/other; vocabulary/member/key, number profile and actual OCR route/model attribution. |

The existing catalogue requirement file has no approved entries for these three
source names. Adding those definitions/members/mappings remains separate work;
no keys or aliases were invented here.

## Redis, PostgreSQL and recovery validation

Input remains `lpr.plate.results.v3` under the v3 partition. The projector uses
`mohamed-tunis-projection-v1`, its own fence/journal and existing fixed-XADD /
output-before-ACK scripts. Output is the separate `db1.delivery.v1` stream under
the natural-delivery partition in the same fresh test namespace. It is not a
new live v2 subscription. This owned single-node test makes no Redis Cluster
cross-slot claim.

Identical sealed replay repairs/ACKs the retained output without another publish
or model call. Conflicting sealed retries remain pending and cannot replace the
accepted delivery. Source proof and canonical output are recoverable before
projector input ACK. The existing `RedisDB1Consumer` uses its own
`db1-natural-v1` group; its ACK still follows `DB1Adapter`'s confirmed commit.

Private PG16 connectivity/privilege/target gates passed on the authorized existing
`127.0.0.1:5433` endpoint. Only fresh random P4 databases/roles were provisioned.
Normal application logins were NOSUPERUSER/NOBYPASSRLS, owned no tables, and all
nine tenant tables forced RLS. The maintenance database received no test facts
or schema. Source media registrations inside the test database were explicitly
synthetic fixture references carrying the actual clip fingerprint/size/dimensions;
there was no upload or object-existence claim.

Final **22 methods passed without skips**: seven projection methods run in both
contexts, plus eight real PostgreSQL methods (nine disposable DB instances):

- Committed vehicle/private plate/colour rows and exact receipts visible from a
  separate restricted connection **while Redis pending was 1**, before XACK.
- Identical physical redelivery: one plate fact, one acceptance, three row
  receipts, no duplicate recognition.
- Real SQL division-by-zero after inserts: full rollback, zero facts/receipts,
  delivery still pending; retry commits once.
- Failure after confirmed commit before ACK: retry reconciles exact history,
  returns identical and ACKs without recognition or a new plate fact.
- Changed payload for the same natural identity: original rows/receipts remain,
  conflict is not acknowledged.
- Missing supplied registrations and missing actual database category/model
  rows: no acceptance and no ACK.
- Original v2 codec and default terminal reader still commit using PG16.

Other passing checks: **121** Phase 1–4 methods, **146** affected default T1 /
Aggregator / DB1 methods, **9** native Redis v2 regressions, existing event
schema/application fixtures, and **122** synthetic DB1 contract checks. These
are current runs; their recognition/transaction doubles are not presented as
real-model/PG execution. The new fixture methods use real PG and Redis with
injected model results.

## Real binding correction and passing smoke

`real-e2e/` retains the initial failed Phase 5 attempt (exit 1). It opened one
decoder, decoded four frames and executed all four recognition stages once,
but stopped before DB delivery; no plate fact was committed. Its cleanup passed.
The mismatch was reproduced from the actual T1 Engine implementation: retained
native candidates have `crop_accepted=False` until **v2** preparation. The v3
Engine has its own admitted/executed/retired graph and does not set that v2 flag.
The projector now preserves the original flag and validates v3 admission from
the complete settled graph. A new regression uses the real unset flag. It does
not rewrite it or pretend v2 preparation executed. Final fixture tests passed
again before a fresh bounded corrected smoke.

`real-e2e-corrected/summary.json`: **PASS**, parent and CUDA child exit 0.
Run ID: `mohamed-phase5-c0e30287f81f48ed83a171f0f18ffb85`.
Namespace: `cuda-v3-202469d66b914c818c6e`.
Explicit `legacy_whole`, 4 frames, one decoder, at most 8 crops, 240 seconds
admission; normal T1 frame/association/handle/byte limits remain enforced.
Redis import/reachability, CUDA and locked artifact checks passed before decoding.

The exact local **Redis 7.4.0** payload was RECORD-verified and staged outside
both checkouts (119 files, same setup as Phase 4). Only that package/metadata
was added to the CUDA subprocess's PYTHONPATH. No downloads/shared environment
changes occurred; the temporary package/control directory was removed. The
separate DB1 host used the existing psycopg environment; credentials never
entered the CUDA child. A fresh CUDA import probe still finds no installed Redis.

| Successful execution | Observed result |
|---|---|
| Model operations | Vehicle 4, plate detector 4, Arabic OCR 1, DINO scoring 1, whole PP-OCRv6 1, marker 1, regional OCR 0. |
| Scalar parity | One accepted Tunis decision; entire Redis/memory decision equal; 0 comparison inference calls. |
| Committed rows | Vehicle 1, private plate 1, colour 1; make 0. |
| Receipts visible before ACK | Acceptance 1, row receipts 3; exact natural/projection bytes and hashes checked from a separate connection while input pending=1. |
| ACK | XACK reply 1; DB1 pending 0. |
| Other pending work | Projector 0; v3 Plate 0; Engine 0; pending journal intents false. |
| Native/crop cleanup | 4 recognition operations retired; 0 unretired; one crop released once, 0 remaining crops/handles. |
| Resources | Owned database/roles removed, Redis stopped/private AOF removed, temporary Python/control directory removed. |

Reconciliation identifiers (no readings):

- Sealed decision SHA256: `36e41028b874c14d0106b1160c5975a18f0cb0c21a7f247c2ab31649b610bbc3`.
- Natural delivery SHA256: `414ab3ee93531056d43bf4e5e79aa8c635f559db441cb0c22fae957e9c24fdfe`.
- Committed acceptance digest: `68ad9c29d25d0d812a1129de6d31a5dab87b3b8a6b6c825d5e4c2c73094aa6f1`.
- Plate row UID: `bf9ee352-3080-52b9-abdd-aa4b4fb7f4b2`.
- Plate delivery/projection hashes and frame-0 receipt/SQL timestamp equality are
  in `before_ack.row_hashes` / `before_ack.frame_receipts`; SQL text/model/category
  equality was asserted without exporting the reading.

Passing stage totals (seconds): vehicle .721, plate .509, Arabic .287, DINO .224,
registration 1.334, marker .139. Host elapsed 23.007 s; command wall 23.169 s.
There were **two Phase 5 native attempts**, each four frames: the first failed
before DB, the second passed end-to-end. Thus total Phase 5 calls across both are
vehicle/plate 8 each and the four recognition stages 2 each. Neither run repeated
inference for parity. Only the passing run is claimed as end-to-end evidence.
Only legacy Tunis was exercised by real inference; no accuracy or other-category
coverage claim follows from this clip.

## Exact commands

All commands ran from `/home/intern/part-time/aziz/pipeline` with
`PYTHONDONTWRITEBYTECODE=1`. Tests used the existing Redis 8.2.11 executable via
`VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`.
For the real PG tests the private wrapper set **only** existing-server mode and
loaded DB1_P4_ADMIN_DSN from the approved mode-0600 file; its value is never
printed or included in command JSONs. No isolated-cluster mode was enabled.

```bash
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_delivery -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_plate_policy vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_lpr_engine vehicle.common.tests.test_mohamed_lpr_streams
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_db1_consumer vehicle.common.tests.test_db1_runner_ack vehicle.common.tests.test_db1_adapter vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_real_video
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_requests_redis -v
/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
/home/intern/.venv/bin/python contracts/validate_examples.py
/home/intern/.venv/bin/python scripts/vehicle_mohamed_lpr_db1_smoke.py --admin-dsn-file /home/intern/.db1-p4-dsn.mLCSWr --output /home/intern/part-time/aziz/mohamed-lpr-phase5-validation-20261108/real-e2e-corrected --cuda-python /home/intern/miniforge3/envs/cuda-dev/bin/python --source-root /home/intern/part-time/mohamed/pipeline --ocr-cache /home/intern/.paddlex/official_models --video /home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/test_video.mp4 --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server
/home/intern/.venv/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase5-validation-20261108/verify.py
```

`commands.json` and per-command JSONs/logs retain argv/exit/duration. The retained
`pg_gate.py` / `check.py` implement private fixture configuration; `cuda-command.json`
records the exact CUDA child command, UUID output/run ID, control path and four
temporary environment values. The failed command differs in its output directory
(`real-e2e`) and is retained. Development failures also covered tuple/JSON handling,
numeric identity, bounded state strings, the existing P4 lane and lease teardown;
their logs are not counted as final passes.

## Remaining scope

This is an explicit test-only opt-in delivery demonstration, not a live default
switch or approval of the proposed acceptance schema for production. Production
still needs actual approved registrations/source media and a deployment decision;
other categories/modes, multiple Plate sessions and rechecks require the named
contracts rather than inferred mappings. Redis Cluster, Engine process/crop
recovery, RTSP, media upload, provider fallback and production traffic were not
added. The successful trace uses the current DB1 consumer's confirmed-commit ACK
behavior and leaves the verified v2 default intact.
