# WAYCON Vehicle T1 — supervisor handoff

**PASS — all required native PostgreSQL/Redis checks passed.** Executed
2026-11-08 (Africa/Tunis). This accepts the bounded prerecorded-MP4 delivery
architecture against a disposable fixture; it does not deploy a production service.

Input owns decoding; Engine owns pixels and model execution; Colour and Plate
own their decisions; Aggregator emits canonical DB1 deliveries. The replaceable
DB1 consumer alone writes final facts and immutable receipts in one PostgreSQL
transaction, and acknowledges Redis only after confirmed commit.

The reviewed archive SHA256 is
`4f399efd37bb7d62e1cb51946e8af2f5b8121724c9dafab90fddda161d277a48`.
All **266 manifested source files** matched before work and remain byte-identical,
including the lost-ACK fix, Redis recovery, canonical payloads, supervisor SQL
archive and historical fixtures. Four additions supply acceptance coverage,
this report, the runbook and reproducible packaging. Existing dirty work is preserved.
No production-code repair was needed.

## Demonstrated today

| Check | Executed evidence |
|---|---|
| Native gate | **38 methods passed, zero skips**: 29 real PG tests and nine connection-free guards; `native-authorized-05/native.log` |
| Success | Separate restricted-role connection saw parent/Colour/Plate facts and acceptance + three row receipts while the exact Redis entry was still pending; real XACK followed |
| Rollback | Actual SQL error after fact/receipt writes left zero committed rows/receipts and a pending Redis delivery |
| Commit retry | Actual commit followed by an injected lost reply reconciled through accepted history; original rows/receipts unchanged; ACK only after confirmed retry |
| ACK retry | Redis executed XACK and lost its reply; the runner's exact-target ledger completed that input without another DB connection/transaction; unread/pending inputs could not borrow its result |
| Conflict | Identical delivery returned `identical`; changed accepted payload preserved every original fact/receipt, raised conflict and remained pending |
| Tenant isolation | Two populated real tenants; actual non-owner LOGIN, NOSUPERUSER NOBYPASSRLS role; nine forced RLS policies; own rows visible, foreign rows absent and cross-tenant writes rejected; receipt UPDATE/DELETE denied |
| Restart | Existing DB1 case SIGKILLed/restarted only owned AOF Redis after PG commit and retried history before ACK, without duplicate facts/receipts |
| Bounded runner | Real Redis + PostgreSQL together, injected recognition/capture: one capture, four decoded/admitted frames, two lifetimes, exact canonical parity, two commits and ACKs, zero waiting/PEL/lag, crops released |
| Regressions | **443 unique main methods passed**: 378 pure + 65 owned native Redis; seven overlapping focused gates passed; event contracts and 122 DB1 fixture checks passed |

The target was explicit client endpoint `127.0.0.1:5433`, PostgreSQL **16.14**,
existing-server mode only. Its internal address/port differs and is recorded in
preflight. The private superuser login provisioned fresh UUID-named databases
and roles; all DB1 operations used actual restricted logins. Maintenance database
`intern` had no user relations before or after. All created fixture resources
were removed; the server/configuration and pre-existing resources were untouched.
No Docker commands, production DB connection, commit or push was used.

Early missing/empty-credential attempts skipped and were BLOCKED; these are
retained separately. A helper initially rejected the forwarded server address
before provisioning. Two development runs of the new tenant-B fixture failed on
globally unique camera/media identities. B now has distinct camera, run and media
identities before canonical generation; the final full native suite passes.
These were harness/fixture corrections, not production delivery defects.
`accepted-checks/postgres-gate.log` is the earlier skipped attempt; the current
native verdict comes from `native-authorized-05`, not that aggregate exit code.

## Real models and production limits

The real-model MP4 smoke is **historical, 2026-11-04**: actual vehicle/plate YOLO,
Colour extraction, OCR/marker with native Redis; four decoded frames, two canonical
deliveries, no PostgreSQL. Its original 222-file manifest and six preflight source
hashes are retained under `historical/`. Original source fingerprint:
`fe24c554dfb3121e4c863da3405781da81227ddfb2be75c2a975495f3b8bae18`
(compact sorted path-to-SHA256 JSON; see `historical/README.json`). No inference or
evidence-generation code changed, so models were not rerun. Today's PostgreSQL
and Redis ran **together with injected recognition**. The historical models and
today's PostgreSQL validation ran **separately**; no real-model-to-PG run or
recognition-accuracy claim is made.

Production work remains: approve/deploy the proposed receipt schema and replace
the fixture with the production DB consumer/service, authenticated tenant context
and registration/authorization, operational monitoring, capacity/performance and
storage durability validation. RLS here assumes trusted `app.org_id` context; it
is not production authentication. Synthetic media registrations do not prove
object-store contents. RTSP, manufacturer recognition, durable Engine process
recovery, Engine trace persistence and UI remain outside T1. Same-live-Engine
Redis reconnection and owned-process SIGKILL tests do not prove Engine crash
recovery or host power-loss durability.

## Handoff files

External directory: `/home/intern/part-time/aziz/waycon-t1-delivery-20261108`.
Report: `supervisor-report.md`; commands/retry/cleanup: `operator-runbook.md`.
Final review: `waycon_vehicle_t1_final_review.zip` and
`waycon_vehicle_t1_final_review.zip.sha256`. `packaging-result.json` records the
archive SHA256; `_review/manifest.json` binds every source/evidence file.
Patch replay checks/applies against the reviewed baseline, compares all resulting
270 source files and executes focused regressions/contracts from that extraction.
Earlier failed/blocked attempts and historical model evidence are clearly labelled.
