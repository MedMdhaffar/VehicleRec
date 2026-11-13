# Full WAYCON launcher verification — 2026-11-13

The launcher starts the real local console stack without starting an input.
Recorded MP4 and privately entered RTSP use the existing T1 Engine, run-owned
Redis, Aggregator and DB1 consumer. All analytical writes are disposable fixture
writes. No production writes, shared catalogue changes, server reconfiguration,
commits or pushes were performed.

## Launch and present

On Analytic:

```bash
cd /home/intern/part-time/aziz/pipeline
bash scripts/run_vehicle_console.sh
```

Browser URL: **http://127.0.0.1:5173/**.

In Windows PowerShell, using the existing SSH target:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5173:127.0.0.1:5173 intern@Analytic
```

Open the same browser URL on Windows. Only port 5173 needs forwarding; the
development frontend uses its existing same-origin API proxy to Django on 8002.
Choose a local MP4 in the browser, or enter a fresh camera URI and explicit
transport. Manufacturer defaults off and remains review only when enabled.

The command stays in the foreground. Rerunning it reuses the existing launcher.
Ctrl-C or `bash scripts/run_vehicle_console.sh --stop` requests an owned shutdown.
New Starts are blocked while draining. Stop is requested for active work and its
slot must release before owned web processes are terminated. Failure to confirm
release leaves services running with an explanation. Unowned processes are not
terminated. No PID file is used as authority to signal an arbitrary process.

## Resource and delivery boundaries

Read the frontend README, console worker/run store, race/proxy reports, Phase 5,
RTSP milestone, P4 fixture/runbook and DB1 alignment before implementing this.
No separately named launcher review was found in the workspace. The console
continues its working v2 progressive Plate request protocol; historical Phase 5
v3 recognition evidence is not presented as evidence for these new runs.

The existing private configuration at `~/.config/waycon/console.json` references
an existing owned mode-0600 DSN file. Its authorized target is PostgreSQL
**16.14 at 127.0.0.1:5433**, with the authorized `intern` maintenance fallback.
Startup checks version and provisioning rights, creates a fresh P4 fixture,
verifies its registrations and real restricted-login writes/receipts, then
removes that fixture. Maintenance connections perform provisioning/catalogue
operations only. Schema, test registrations, facts and receipts belong to fresh
randomly named fixture databases; application connections are NOSUPERUSER,
NOBYPASSRLS, non-owner logins with forced tenant RLS.

Each real delivery uses the existing `OwnedDeliveryDatabase` fixture and
`RedisDB1Consumer`/`NaturalConsumer` in a separate DB1 process. There is one
disposable database per track delivery, matching the existing fixture host's
scope. PostgreSQL commit and Redis DB1 ACK are independent public fields. After
confirmed commit, a separate restricted connection verifies exact facts,
acceptance bytes/digests and row receipts while the exact physical Redis entry
is pending. The existing consumer then performs XACK. Fixture databases/roles
are removed afterwards; the retained evidence is not a persistent demo database.

Uploaded MP4 fixture media metadata binds the actual uploaded SHA-256, byte
count and dimensions. This is explicitly fixture metadata, with no object-store
upload/existence claim. RTSP uses `source_kind=stream` and
`source_media_key=NULL`, never the fixture's unrelated template MP4 row.
Manufacturer produces no `fact_vehicle_make` rows. No production registrations
or manufacturer catalogue mappings were added.

Redis uses the existing `OwnedRedis`: private Unix socket, TCP port 0,
appendonly/appendfsync-always, and an owned temporary directory/process. Startup
briefly probes and removes one owned instance. Each run starts/removes its own
instance automatically. System Redis on 6379/6380 is not used or stopped.

Startup checks a real CUDA tensor operation on **NVIDIA A100-SXM4-80GB MIG
1g.10gb, cuda:0**, fingerprints the actual vehicle/Plate/marker/OCR artifacts,
and verifies the manufacturer lock's model/projector/runtime hashes without
manufacturer inference. Native run evidence records actual imported OpenCV,
CUDA parameter placement and original detector calls. Plate/OCR retain their
existing explicit CPU bindings. No models or dependencies were installed.

Full mode fails startup when DB1 is unavailable. Explicit `--console-only`
works without a DSN and visibly reports that the full stack is not ready.
There is no automatic downgrade. Start also performs a fresh DB1 check before
staging or spawning. In the normal private-file configuration, the CUDA worker
does not inherit the administrator DSN; only the DB1 host reads that file.

Canonical wire envelopes and DB1 progress stay private under their run root.
Failed delivery reports the last confirmed commit/ACK boundary and fails the
run. It does not automatically replay or reinfer. URI input is body-only,
cleared on Start, staged separately per run, removed at cleanup and excluded
from argv, URLs, status, browser storage and service logs.

## Actual execution evidence

Evidence root: [`vehicle/media/launcher-validation-20261113`](../../vehicle/media/launcher-validation-20261113/).

| Check | Result and evidence |
|---|---|
| Fresh full startup | PASS: loopback ports 8002/5173, full DB1 readiness, unchanged run count and zero active input. [Evidence](../../vehicle/media/launcher-validation-20261113/fresh-startup.json). |
| Rerun while up | PASS: same launcher owner, no new processes or run. [Evidence](../../vehicle/media/launcher-validation-20261113/rerun.json). |
| Missing DB1 access | PASS: exit 1, clear DB1/full-stack error, no web services or input started. [Evidence](../../vehicle/media/launcher-validation-20261113/missing-db1-startup.json). |
| Explicit console-only | PASS: started without a usable DSN; browser visibly showed full-stack-not-ready and zero active input. [Evidence](../../vehicle/media/launcher-validation-20261113/console-only.json). |
| Browser MP4 | Initial PASS: run `e035f6a6-7da7-4112-9ea6-01223f7a3134`, four frames, manufacturer off, loaded preview and selected Plate/vehicle crops, two settled tracks, clean completion, two confirmed commits and two ACKs. [Browser](../../vehicle/media/launcher-validation-20261113/browser-mp4.json), [before-ACK proof](../../vehicle/media/launcher-validation-20261113/mp4-commit-before-ack.json). Final replay is recorded below. |
| RTSP complete | PASS: run `90e9bc7d-ff6b-4d35-b045-83939a58a274`, four frames, two tracks and a private Plate read. Five facts, five row receipts and two acceptances across two disposable databases were verified before their respective real XACK replies. Stream source registrations had no source media link. [Evidence](../../vehicle/media/launcher-validation-20261113/rtsp-complete.json). |
| RTSP preview and browser Stop | PASS: separate run `3254d132-1a3d-471a-9cab-8cd3bfdfd507`, one frame, live loaded preview, Stop, slot release and all cleanup true. No settled DB1 deliveries were claimed for this stopped run. [Browser](../../vehicle/media/launcher-validation-20261113/loopback-stop/browser-rtsp.json), [owned source/privacy cleanup](../../vehicle/media/launcher-validation-20261113/loopback-stop/owned-rtsp.json). |
| Stop during active Start, then owned shutdown | PASS: browser-started run `ac6d1a47-8b99-4c3f-9199-15486c27783c` stopped; its worker exited and its slot released before Django/Vite stopped. Both web ports closed. [Evidence](../../vehicle/media/launcher-validation-20261113/owned-shutdown.json). |
| Shared infrastructure preservation | PASS: identical PostgreSQL postmaster start time, maintenance relation catalogue hash, empty fixture database/role inventory, and shared PostgreSQL/Redis listening socket inodes before/after shutdown. [Before](../../vehicle/media/launcher-validation-20261113/shared-before-shutdown.json), [after](../../vehicle/media/launcher-validation-20261113/shared-after-shutdown.json). |
| Real camera | NOT TESTED: no camera access was supplied. Loopback success is reported separately. |

The known MP4 remains at
`vehicle/license_plate_service/run_video_pipeline/test_video.mp4`, 2,381,732
bytes, SHA-256
`36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2`.
All browser requests used port 5173; direct port-8002 browser requests were
blocked during acceptance to simulate the single Windows SSH forward.

The owned VLC source used full-resolution H.264 replay of that clip's first
four frames, repeated inside one long media session, with explicit UDP. Earlier
attempts remain labelled failures: VLC rejects TCP, a ten-second media session
ended during model initialization, and one resized stationary replay yielded
no tracks and correctly reported no deliveries. The successful completion
run's paired Stop attempt outlasted its 240-second source; the independent
successful Stop used a 600-second owned source. The entire older paired test
is not claimed to pass. No detector/OCR threshold or recognition policy was
changed to obtain these results. The existing synthetic Input-only probe also
passed 11 frames, a forced outage and reconnect with owned cleanup.

## Regression checks

All commands used the installed Python 3.11 and Node 22 runtimes. Native DB
commands received private DSN configuration through the environment and
`DB1_P4_EXISTING_SERVER=1`; Redis-labelled tests received the existing binary
through `VEHICLE_REDIS_SERVER`. Skipped setup is not counted as a pass.

| Command/gate | Observed result |
|---|---|
| `python vehicle/manage.py test api.test_console_stack api.test_console api.test_console_lifecycle api.test_console_crops api.test_console_resources --noinput` | 21 passed, including the original deterministic Start/Stop publication barrier and owned hard-exit resource probe. [Log](../../vehicle/media/launcher-validation-20261113/django-final-tests.log). |
| `python -m unittest discover -s tests/db1_integration` | 38 passed, zero skips. [Log](../../vehicle/media/launcher-validation-20261113/native-db1-tests-with-redis.log). |
| Shared console projection/crops, RTSP Input, natural consumer and console DB1 tests | 38 passed, zero skips. [Log](../../vehicle/media/launcher-validation-20261113/shared-tests-native.log). |
| Final `python -m unittest -v vehicle.common.tests.test_console_db1` | Two passed with real PostgreSQL and Redis: physical source metadata/before-ACK visibility; failed independent check preserves confirmed commit and leaves the exact entry pending. [Log](../../vehicle/media/launcher-validation-20261113/console-db1-final-tests.log). The success case overlaps the shared gate; the failure case adds coverage. |
| `node --test frontend/tests/*.mjs` | Five passed. |
| `npm run lint`, `npm run build` | Passed. |
| `python vehicle/manage.py check`, `bash -n scripts/run_vehicle_console.sh`, `git diff --check` | Passed. |

An initial database discovery omitted the Redis variable and skipped eight
methods; the corrected 38-method run above executed all of them. An initial
shared-test harness ran multiprocessing tests from stdin and failed; the real
module invocation above passed. These failed/partial attempts remain in the
evidence directory and are not included as successful verification.

The current supervisor archive has SHA-256
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`,
matching the existing P4 PostgreSQL report; its mtime remains 2026-10-08.
The launcher does not edit it, contracts, catalogues, recognition policies or
the standalone `services/` copies. Existing unrelated working-tree changes and
historical demo runs were preserved.

## Final replay and handoff

The final full-mode MP4 replay **passed**: run
`ef1b127d-69b6-4c94-9b7d-fba9e0335567`, four frames, manufacturer off,
two settled tracks, five facts/row receipts and two acceptances committed before
two real ACKs. Separate connections also verified that fixture media metadata
matched the actual browser upload's SHA-256, byte count and dimensions.
Its [browser result](../../vehicle/media/launcher-validation-20261113/browser-mp4-final.json)
and [screenshot](../../vehicle/media/launcher-validation-20261113/mp4-final.png)
confirm loaded preview/crop images, clean completion, worker exit and released
admission. [Credential-boundary evidence](../../vehicle/media/launcher-validation-20261113/worker-credential-boundary.json)
confirms that the actual CUDA worker did not inherit the administrator DSN.

The [final handoff check](../../vehicle/media/launcher-validation-20261113/handoff.json)
confirms full-stack readiness, zero active runs, no remaining fixture
databases/roles and reuse of the same launcher owner. The launcher is left
running in full mode with the successful MP4 preview/results retained. No camera
or active video run is left open.
