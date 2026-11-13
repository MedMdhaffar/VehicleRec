# Plate-owned Redis requests and replaceable DB1 consumer

Historical execution record. The subsequent 2026-11-07 fixes, restart tests and
limitations are in [current results](vehicle_restart_results.md). The real-model
evidence below belongs to the reviewed November 5 source, not a new patched-source
inference run.

Executed milestone: 2026-11-05 (Africa/Tunis). This is a bounded opt-in local
implementation, not the completed production architecture. The real-model MP4
path passed through native Redis. The DB1 consumer passed pure/injected failure
checks and has gated normal-role PostgreSQL tests. **Native PostgreSQL execution
is BLOCKED:** no explicitly configured owned disposable target was available.
There are no native DB commit receipts from this run.

## Starting snapshot and scope

The immediate archive is `vehicle_mp4_publication_order_review.zip`, SHA256
`449bcda14d9630431bfacc8d8fbccf0c22209ed8287362d0052bb3c8f90f934b`.
All 223 files in its source manifest were verified against archive hashes and
matched the working tree byte for byte before editing. Git HEAD was not used as
the baseline. The pre-existing dirty status and HEAD are retained in evidence.
No applicable AGENTS.md was found in the workspace ancestry or repository.

The read-only supervisor contract archive remains SHA256
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.
Supervisor SQL and historical schemas/fixtures are unchanged. The existing P4
fixture, proposed acceptance SQL, fixture guide and contract ZIP are attributed
as pre-existing supplemental dependencies, rather than newly authored or
platform-approved SQL. No installation, download, Docker, global service change,
commit or push was performed. Unrelated changes were preserved.

## Implemented ownership and state

Input alone opens/decodes MP4 and stamps the paired original receipt. Engine
owns all pixels, detections/tracking/association/quality, private crops, configured
executor bindings, actual starts/models/retirement and scalar execution evidence.
Plate owns requests, normalization/parsing/fusion/eligibility and attempt selection.
Colour remains scalar-only. Aggregator owns class consensus, authenticated terminal
joining and immutable canonical natural delivery. The replaceable consumer alone
resolves supplied registrations and writes final facts through the existing
PostgreSQL adapter. org_id remains identity, not authorization. No provider or
SQLite target was introduced; legacy outbox tests remain historical compatibility
checks, and the new consumer does not import SQLite.

The [v2 state/event and stream tables](vehicle_plate_requests_v2.md) are normative
for this bounded implementation. They were written before loop implementation
and updated with final source. The new schema/codec/worker state are explicitly
versioned. Reviewed v0/v1 envelopes and fixed-plan paths retain their semantics.
The v2 path never admits future regions or declares them not-started just to fill
an old transcript. It bridges to a v1 terminal only after actual admitted work is
sealed. Final natural delivery remains the reviewed `db1.delivery.v1` contract.

| Event/state | Recorded decision and required evidence |
|---|---|
| Engine crop offer | Closed detection; bounded original candidates/capabilities/budget; zero promised operations |
| Plate request | Stable UUID5, complete parent/session/crop/attempt binding, distinct request sequence, exact dependency generations |
| Engine admission | Accepted work or explicit rejection; published before native invocation is enabled |
| Engine evidence | Original operation/model/outcome/scalars and SHA256 generation; emitted after native retirement |
| Plate continuation | Shared canonical `needs_region_ocr` predicate; exact whole/marker dependencies for either regional order |
| Plate generation seal | Exact sorted request IDs and revision; distinct from detection closure or terminal decision |
| Engine sealed response | Exact admitted set, no rejected work, settled original transcript and confirmed retirement |
| Plate terminal | Independently reconstruct/compare the transcript, then canonical Plate decision |
| Aggregator final | Required Colour/Plate terminals and matching closure; complete natural byte parity |
| Crop completion | Fenced Plate terminal handoff/ACK confirmed, original terminal authenticated, then retryable release |
| DB delivery | Complete natural validation, supplied registrations, canonical projection, transactional facts/receipts, then Redis XACK |

Known whole failure stops futile continuation; failed marker never requests
regions. Canonical private eligibility still requires marker evidence even when
whole OCR already contains enough text. Unsupported capability/budget exhaustion
or Engine rejection blocks unfinished enabled work. Unknown or unretired work
waits. Neither timeout nor EOF proves cancellation, no plate or unreadability.
Changed evidence blocks unfinished work. An accepted terminal is retained when
late conflicts arrive, with a diagnostic/conflict hash; it cannot be replaced.

## Recovery, acknowledgment and resource limits

Metadata workers reuse the reviewed fenced state/output/ACK journal. They retain
waits and immutable request/result outputs before XACK and repair pending intents
before later transitions. Changed retry conflicts remain detectable. Journals
use normal 60-second live Plate leases. Worker-kill tests accelerate only a
**confirmed dead** owner's remaining lease under an exact-token comparison;
no live-owner lease was shortened.

Engine uses a separate explicit in-process request/admission/execution ledger.
Request redelivery reuses original admission/evidence and never re-infers in that
live session. Request XACK follows confirmed admission publication. Native work
is outside Redis transactions, and this boundary relies on live process memory.
Engine process death does not restore native state, pixels or exactly-once
inference. Old-session requests cannot run against reused handles. Unavailable
lifetimes remain explicit waits; unavailable sessions/crops receive explicit
rejections when a live bound session can answer.

All Engine publications use one bounded FIFO with monotonic explicit IDs. Partial
publication cannot overtake an earlier packet. Each input's progress and exact
bytes remain retained. Collection-to-publication reservation failures are repaired
from retained evidence intents. The host pumps request/seal and native work while
waiting for metadata settlement; workers alternate pending/new and stream turns.
No unbounded queue, silent eviction or global dependency wait was added.

Crops remain pinned while followups are possible, native use is active/unretired,
or scalar handoffs are unconfirmed. Explicit generation sealing and Plate/Engine
completion control release. Existing native exclusion, model-binding checks,
runtime quarantine and retryable cleanup remain in force. Timeout/stop retains
unresolved resources. The exact count/byte/time/poll limits are in the v2 contract;
the default real run used four frames, 180 admission seconds, 80 API calls maximum,
16 lifetimes/attempts, a 32 MiB crop budget and an 8 MiB scalar publication budget.
Native execution remains serialized, with no throughput claim.

## DB1 construction and transaction behavior

`NaturalConsumer.prepare()` strictly authenticates the complete v1 delivery using
the existing codec/join/domain validators, then binds trusted org/run/camera.
Registration records are explicitly supplied typed assertions, never inferred
from message org identity, model names, filesystem paths or environment secrets.
Original unresolved natural context stays separate from the resolved SQL context.
Only matching camera/run/detector/source metadata can be resolved; synthetic
registrations require the test environment. Missing models, dimensions, source
media, quarantined/unfinished/conflicting deliveries and unsettled omissions
cannot commit. Null crop media remains null; no upload/registration is invented.

`RowBuilder.project_natural()` extracts the original supported SQL projection
from the legacy facade. Both paths use that one projection and the existing P1
validation and registration policies. `db1_delivery.py` extracts the existing
accepted DTO/serializer without changing its historical byte format. DB1Adapter
no longer imports the SQLite outbox.

The existing PG16 adapter checks a normal NOBYPASSRLS role, forced tenant RLS,
references, receipt compatibility and supporting timestamps. It locks logical
identities/rows, inserts all parent/child facts and immutable acceptance/row
receipts in one transaction, with synchronous commit. Acceptance additionally
binds the complete original natural envelope, so changed provenance conflicts
even when analytical facts are identical. Identical retries verify accepted
receipt/history and analytical rows. Unknown commit outcomes receive no XACK;
a fresh application connection re-enters locked database history reconciliation.
Confirmed commit precedes Redis XACK; death or lost XACK after commit is safe to
retry. Redis handoff ACKs are never database commit acknowledgments.

For host wiring, construct `NaturalConsumer` with a loaded v1 codec,
`TrustedTenant`, trusted run/camera, immutable `Resolutions`, an authorized normal
application connection factory and `environment='test'` for synthetic records.
Pass it to `RedisDB1Consumer(client, journal, consumer, name=...)`; poll `step()`
with a 50 ms idle backoff. It performs no DDL, provisioning, environment DSN
discovery or registration lookup. The supporting receipt schema is test-local /
proposed, not platform-approved deployment SQL.

## Executed checks and failures

Evidence is in `_review/evidence/` in the review ZIP; exact subprocess command
arrays, elapsed times and results are in `full-01/commands.json` and
`commands-additional.json`. Counts below overlap; they must not be added as
unique coverage.

| Check | Observed result |
|---|---|
| Existing event contracts | Schema valid; 18 valid, 24 schema-reject, 16 application-reject; normalization/retry/strict JSON/drift pass |
| DB1 historical fixtures | 122 passed, zero failed |
| Affected pure/domain/boundary/compatibility suite | 372 passed, 64.589 s unittest time |
| Broad owned native Redis suite | 39 passed, 242.769 s; no skips |
| Request/consumer/native checks before final conflict guard | 33 passed, 72.333 s; no skips |
| Final domain/DB consumer checks, including identical-fact provenance conflict | 27 passed, 6.454 s |
| Additional native regional followups | Both regional orders exercised through Redis; see `native-regions.log` |
| PostgreSQL fixture discovery | Nine connection-free guard tests passed; two fixture classes skipped before connecting. **Zero native DB validation** |
| One real-model smoke | Passed; results below |
| Private extraction replay | All final source bytes compared before/after extracted tests; exact commands/results in `_review/replay.json` and extracted logs |

Development failures are preserved separately, not counted as passes:

- The shared projection initially changed the reviewed missing-registration error
  precedence. The existing reproducing regression failed, then the facade was
  fixed to retain that precedence.
- Completion compared tuple order instead of canonical set ordering. The full
  completion regression exposed it; comparison now uses canonical transcript
  bytes and exact sets.
- Two test-only defects were corrected: a missing synthetic regional fixture key,
  and an incorrect bounded-wait keyword. A fairness assertion also needed to wait
  for the independent Aggregator after Plate completion.
- The evidence reservation regression failed by timing out after an injected
  capacity error. The host now retries the retained scalar intent, with original
  bytes and no inference repeat. `evidence-capacity-red.log` and `-green.log`
  retain the before/after results.

A final review found that seal/revision/provenance semantic errors could be
diagnosed without a sticky block. A new native reproducer failed, then the worker
was fixed to freeze unfinished lifetimes on all bound reducer semantic errors.
The same native regression passed. This one-line error-path change occurred after
the real smoke; its clean inference path did not change and inference was not
rerun. `seal-conflict-red.log` and `-green.log` preserve the result.

The broad run includes legacy/native recovery and publication-order tests. Final
focused checks and extraction checks cover the new source after repairs. There
was exactly one real inference attempt in this milestone, and it passed. The
Paddle runtime emitted a ccache availability warning; nothing was installed.

## One captured real MP4 run

The exact smoke command is retained in sanitized `real-01/report.json` and
`commands-additional.json`. It is the existing smoke command with explicit local
assets and `--request-loop-v2 --device cpu --threads 1 --max-frames 4
--admission-seconds 180 --delivery-timeout 30`. The reviewed existing
`test_video.mp4`, vehicle/Plate weights, marker weights and local Paddle OCR
artifacts were re-fingerprinted before inference. Preflight includes packages,
artifacts/configuration and twelve runtime source fingerprints. Eleven match
final source; the metadata worker conflict guard was tightened afterward, as
detailed below. Its original/final hashes are retained in cleanup evidence. No second capture, reference inference, provider, GPU inference,
model download or heuristic adjustment occurred.

One source open; four successful decodes/admitted frames; four actual vehicle
predict calls/four observed forwards; four Plate predict calls/four observed
forwards; eight HSV/quality measurements; four eligible Plate observations; one
selected crop attempt and one superseded-buffer release. Plate generated two
stable requests and received two admissions, zero rejections: one whole OCR and
one marker. Both executed successfully with original actual model identities.
No regional operation was requested: the existing policy found whole/marker
sufficient. Two lifetimes closed/finalized; zero waiting/quarantined lifetimes,
blockers, retained crop bytes/handles or native activity at completion.

Capture/settlement elapsed 14.5615 s. Requested CPU/one-thread profile; actual
vehicle and marker records report one Torch thread, OCR reports Paddle CPU,
while Plate detector placement reports seven Torch threads (a native runtime
observation, not a guarantee of the requested thread count). All parameter
placements are CPU. Maximum active native lane count was one. This tiny bounded
measurement is not throughput/accuracy validation or a scheduling-overlap result.

| Lifetime | Plate terminal | Complete natural bundle SHA256 |
|---|---|---|
| 1 | read, actual local OCR model; text omitted from report | `ef5e0c1d224d954c8aa3f5acaf69e375d00190e89d4c65f17a00e4b93503dde5` |
| 2 | no_detection omission | `abe04ea654f85b3cc4aae8d9b727f548c98da5ebd07e43772a952a14fa05fe68` |

Both complete canonical bundles, including facts/omissions/provenance, equal the
direct reference bytes. Lifetime 2 finalized before lifetime 1; this is supported.
Original receipts, candidate/attempt IDs, request dependencies/generations,
actual models and per-fact timestamps are retained in the sanitized report. UTC
receipt dates are November 4 at 23:37–23:38, which is November 5 in Africa/Tunis.

Every listed physical stream has exactly two entries: crop offers, requests,
admissions, executions, generation seals, Engine sealed responses, lifecycle,
Colour evidence, Colour terminals, Plate terminals and final natural deliveries.
All existing consumer groups report pending=0. `db1.delivery.v1` has no database
consumer group in this real smoke: PostgreSQL was not configured, so these two
natural deliveries were **not committed to DB1**. Native Redis was 8.2.11,
port 0/private Unix socket and directory mode 0700, persistence off, 64 MiB
maxmemory/noeviction and one logical DB. Four owned child processes exited 0,
no live child remained, the socket directory was removed, models closed and the
selected crop was released exactly once. No expensive inference was rerun for
reporting or packaging.

## PostgreSQL gate and remaining work

Native tests are in `tests/db1_integration/test_natural_consumer.py`, using the
pre-existing disposable P4 normal-role/NOLOGIN-owner/RLS design and read-only
supervisor SQL. Application connections are never the provisioning superuser.
No target credentials were printed or copied. To execute later, first establish
an owned disposable local PG16 target and supply the existing fixture's private
administrator DSN plus exactly one authorized isolation/provisioning mode in the
environment, following `tests/db1_integration/README.md`. Then run:

```bash
PYTHONDONTWRITEBYTECODE=1 VEHICLE_REDIS_SERVER=/absolute/path/to/existing/redis-server /home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration
```

Do not enable a fixture mode just because credentials exist. The new native
consumer tests include a full injected MP4/request/Redis/final delivery through
the real normal-role adapter, concurrent identical commits, lost commit reply,
death before XACK and tenant isolation. They remain unexecuted here.

Manufacturer inference, Engine process-crash recovery, Redis persistence/restart
and power-loss guarantees, production registration/authorization, replacement by
the supervisor's production database service, RTSP/deployment/throughput and
separate Engine raw-trace persistence remain outside this validated milestone.

## Changed files and review package

Fifteen reviewed paths changed: the six architecture documents requested for
inspection, the existing MP4 smoke script, DB1 adapter/outbox/shared builder,
Engine adapter, real-video Plate ledger/runtime, and Redis journal/worker.
Eighteen new paths add the v2 schema, six-module request package, pure delivery
DTO/consumer, two architecture reports, four test files and three validation /
packaging scripts. The exact path list and every source hash are in
`_review/manifest.json`; unchanged reviewed and supplemental dependencies are
attributed there. Historical fixtures and supervisor SQL are byte-identical.

The ZIP contains the scoped patch against the verified reviewed snapshot,
complete required source dependencies, sanitized actual evidence and source /
evidence hashes. Replay applies that patch in a private baseline extraction,
adds the clearly attributed pre-existing fixture dependencies, compares every
final source byte, executes relevant pure/native extracted tests without
inference, compares all source bytes again and removes the extraction. Archive
CRC, unique safe paths and all manifest hashes are checked. Models, media, raw
crops, database files, credentials, environments/caches and unrelated artifacts
are excluded. ZIP SHA256 is supplied externally to avoid self-referential hashing.

## Completion checklist

| Item | Status | Validation boundary |
|---|---|---|
| 1. Plate-owned semantic requests | IMPLEMENTED + VALIDATED | Canonical whole/marker/regional policy; both regional orders |
| 2. Versioned Redis contracts | IMPLEMENTED + VALIDATED | Closed v2 codec/schema; v0/v1 compatibility |
| 3. Engine validation/deduplication | IMPLEMENTED + VALIDATED | Live-session identity/dependency checks; no repeated native invocation |
| 4. Attribution/crop handshake | IMPLEMENTED + VALIDATED | Actual starts/models/retirement; explicit confirmed completion |
| 5. Metadata recovery/fair host | IMPLEMENTED + VALIDATED | Fenced intents, lost replies, waits, capacity and multiple lifetimes |
| 6. Aggregator/parity | IMPLEMENTED + VALIDATED | Full canonical natural bytes, injected and real captured evidence |
| 7. Replaceable DB1 consumer | IMPLEMENTED + VALIDATED | Pure/injected transaction and ACK failures; native checks gated |
| 8. Native Redis | IMPLEMENTED + VALIDATED | Owned Redis 8.2.11; metadata process recovery, not server restart |
| 9. Native PostgreSQL commit/ACK | BLOCKED | No explicitly configured owned disposable target |
| 10. Bounded real MP4 smoke | IMPLEMENTED + VALIDATED | One four-frame CPU attempt; two matching bundles |
| 11. Compatibility/integrity | IMPLEMENTED + VALIDATED | Historical contracts/fixtures/SQL retained; affected checks passed |
| 12. Patch replay/archive/cleanup | IMPLEMENTED + VALIDATED | ZIP emitted only after exact replay, extracted tests and cleanup checks |

The live publisher API retains retryable original packets after transport errors;
the MP4 convenience driver fails closed on an uncaught transport exception rather
than claiming automatic Engine recovery. Callers can retry the live publisher
within their bounded host policy. No resume-after-process-death is supplied.
