# WAYCON WSL handoff

Prepared on Analytic, 2026-11-13. **RTX 5080 validation is pending the supervisor's laptop test.**

## Short handoff checklist

1. **Publish, then pull `waycon-wsl-launcher`.** It is a proposed handoff branch,
   not published by this task. Local `main` and cached `origin/main` point to
   `a8950aa9f974bf8e46178e2973b3a2d632a86fce`. The full console, launcher,
   shared Engine/DB1 code and configurable sampling remain uncommitted locally.
   Git HTTPS cannot authenticate and the connected GitHub API returns 404 for
   this repository; current remote branches/content could not be verified.
   Do **not** assume a pull of `main` contains these features. After the reviewed
   publication commands below succeed:

   ```bash
   git clone --branch waycon-wsl-launcher https://github.com/MedMdhaffar/VehicleRec.git
   cd VehicleRec
   ```

2. **Check before installing:** `bash scripts/run_vehicle_console.sh --check`.
   It prints every missing installed prerequisite and writes private
   `vehicle/media/waycon-launcher/prerequisites.json`. Nothing is installed or
   downloaded automatically. Requirements are Linux Python **3.11**, Node
   **22.12+** (or 20.19+), frontend dependencies, CUDA-enabled PyTorch/torchvision,
   native **Redis 6.2+**, **PostgreSQL 16 server binaries** (`postgres`, `initdb`,
   `pg_ctl`) and OpenCV's Linux system libraries. Use an existing compatible
   environment when available. If missing, provision these prerequisites using
   the laptop's Linux package manager, then run only the needed project steps:

   ```bash
   python3.11 -m venv .venv
   .venv/bin/python -m pip install -r vehicle/requirements-console.txt
   (cd frontend && npm ci)
   ```

   The console requirements omit TensorRT export/training and remote provider
   stacks. They retain the currently inspected CUDA 12.8 PyTorch build. Startup
   executes the actual vehicle checkpoint on the selected GPU and checks
   parameter placement/native forward calls; a CUDA-visible GPU alone is not
   sufficient. If the installed build cannot run on the GPU, startup fails
   explicitly with no CPU fallback. See [PyTorch's official selector](https://pytorch.org/get-started/locally/)
   and [Vite's Node requirements](https://vite.dev/guide/). Native PostgreSQL
   binaries may be supplied through `WAYCON_PG_BIN`; nothing uses or stops a
   system cluster installed by a package manager. Run the launcher as your
   normal WSL user, without sudo.

3. **Supply the approved files that Git does not contain.** Obtain these through
   the existing project asset owner; do not substitute downloaded models:

   | File | Location in checkout |
   | --- | --- |
   | Supplied DB1 schema-only archive | `db1_contract.zip` |
   | PP-OCRv6 medium recognition export: `inference.json`, `inference.pdiparams`, `inference.yml` | `models/PP-OCRv6_medium_rec/` |

   The vehicle `vehicle_yolo26.pt`, Plate `best.pt`, marker
   `tounes_word_detector.pt`, both tracker YAML files, and known Tunisian
   `vehicle/license_plate_service/run_video_pipeline/test_video.mp4` are already
   tracked in the local base commit. Their current GitHub availability remains
   subject to the publication check above. Asset hashes are recorded in
   `docs/t1/vehicle_console_assets.sha256`; after supplying the missing files:

   ```bash
   sha256sum -c docs/t1/vehicle_console_assets.sha256
   install -d -m 700 ~/.config/waycon
   install -m 600 docs/t1/vehicle_console.wsl.example.json ~/.config/waycon/console.json
   ```

   This example requires a local `RTX 5080` and selects an owned PostgreSQL
   cluster; it contains no credentials. Do not copy Analytic's private config,
   DSN, virtual environments or run data. Manufacturer stays **off**. Optional
   manufacturer enablement additionally requires its verified local GGUF,
   projector, compatible llama.cpp runtime and a host-specific verified
   `T1_MANUFACTURER_LOCK`; missing files are reported without blocking the
   manufacturer-off demo. The supplied portable manifest retains the approved
   hashes and uses relative paths; it is not proof of a rebuilt RTX runtime.

4. **Check, launch and stop with the same script:**

   ```bash
   bash scripts/run_vehicle_console.sh --check
   bash scripts/run_vehicle_console.sh
   # In a second terminal in this checkout, or use Ctrl-C:
   bash scripts/run_vehicle_console.sh --stop
   ```

   Open **http://127.0.0.1:5173/** in Windows. No SSH tunnel is needed locally.
   One launcher serves both browser-uploaded MP4 and a newly entered private
   RTSP URI. Startup opens neither source. The launcher owns Django, Vite and a
   fresh temporary PostgreSQL 16 cluster on a free loopback port. Redis starts
   automatically for each run on a private Unix socket. Before ready, startup
   creates the disposable DB1 fixture, checks schema, synthetic registrations,
   normal application role and forced RLS, writes a synthetic delivery and
   verifies committed receipts from a separate connection, then removes that
   fixture. Each real run uses the same verified fixture/consumer path.
   Owned shutdown requests Stop, waits for the active slot, stops only its
   services and removes its temporary PostgreSQL cluster. If drain cannot be
   confirmed, services stay running with an explanation. It never adopts or
   stops system PostgreSQL/Redis or connects to Analytic by default.

5. **Laptop acceptance (pending):** choose the known MP4 through the console,
   N=1 (or explicitly select N=5), manufacturer off, timeout blank. Confirm
   preview/results, EOF and released slot. Private `native-runtime.json` must
   name the RTX 5080 and show the selected CUDA parameter device and actual
   forward calls. `db1-progress.json` must show separate-connection committed
   facts and receipts while `exact_entry_pending=true`, then `ack_reply=1` and
   pending/lag zero. The UI shows commit and ACK separately. The existing
   acceptance helper uploads through the console API and asserts these facts:

   ```bash
   .venv/bin/python scripts/console_sampling_db1_acceptance.py \
     --video vehicle/license_plate_service/run_video_pipeline/test_video.mp4 \
     --output vehicle/media/laptop-mp4-evidence.json
   ```

   Keep that output private; no camera URI, footage or raw plate readings are
   needed in the handoff report. RTSP **loopback** acceptance is separate and
   optionally uses `scripts/console_owned_rtsp_check.py` with FFmpeg, VLC and
   Playwright/Chromium already installed; those browser/source tools are not
   launcher dependencies. **Real-camera testing remains unperformed.**

## Publication review and exact commands

No commit, push or staging was performed. The unpublished required groups are:

* Full console UI, same-origin proxy, API routes/admission/Stop guards and worker.
* Shared Input/Engine/tracker/Redis/Aggregator/DB1 consumer and contract code,
  synthetic disposable PostgreSQL fixtures, necessary runtime service changes.
* Nth-frame sampling, EOF/partial states, history/counters and acceptance tests.
* Portable prerequisite checks, local PostgreSQL owner, runtime path/GPU
  configuration, focused dependency file and this handoff.

`docs/t1/vehicle_console_publish.paths` is an explicit 375-file source/test/docs
allowlist for all required local work, including previously untracked runtime
dependencies. It excludes private configuration, DSNs, uploads, media/run logs,
audit data, generated crops, virtual environments, node_modules and external
model/schema assets. The current base already tracks a virtual environment,
SQLite database, bytecode and old generated RTSP outputs; the index-only removal
below excludes them from the handoff tree while preserving local files.
Against local HEAD, that allowlist contains **277 untracked files, 21 modified
tracked files, and 77 unchanged tracked files**. These are local Git facts, not
an authenticated statement about GitHub's present contents.

Run these only after reviewing/authorizing publication. If `git fetch` shows
new upstream changes, reconcile them before committing; do not overwrite them.
The branch is based on the locally tested `main`; no force push is involved.

```bash
git fetch origin
git switch -c waycon-wsl-launcher main
git add --pathspec-from-file=docs/t1/vehicle_console_publish.paths
git rm -r --cached --ignore-unmatch -- vehicle/.venv vehicle/db.sqlite3 \
  services/license_plate_service/run_rtsp_pipeline/test_output \
  vehicle/license_plate_service/run_rtsp_pipeline/test_output \
  ':(glob)**/__pycache__/**' ':(glob)**/*.pyc'
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
# Review the staged source and removals before these publication steps:
git commit -m "Add portable full-stack WAYCON console"
git push -u origin waycon-wsl-launcher
git ls-remote --heads origin waycon-wsl-launcher
```

The supervisor should clone only after the final command confirms that branch.
No one-time setup script was added: environment/package installation and copying
the approved assets are the only one-time work; the existing launcher manages
runtime services.

## Analytic verification and limits

The existing private configuration now explicitly selects Analytic's Python,
Node, Redis, OCR, original manufacturer manifest, CUDA device and existing
loopback PostgreSQL 5433. None of those absolute paths is a launcher fallback.
The original full console was left idle/running with history intact.

* Independent `--check`: PASS, actual A100 MIG checkpoint CUDA inference,
  run-owned Redis 8.2.11 probe cleanup, PostgreSQL 16.14 disposable schema and
  registrations, separate restricted connection sees committed facts/receipts.
* Independent Django/Vite on **8004/5174**: PASS startup with no input and owned
  shutdown; those listeners stopped while original **8002/5173** and shared
  PostgreSQL **5433** remained ready. No original stack Stop was requested.
* Portable configuration/ownership and sampling tests: 11 passed; manufacturer
  regressions: 13 passed. API/admission,
  Stop race, crop and owned-resource regressions: 23 passed. Disposable DB1
  regressions: 11 passed. Frontend: 7 passed, lint and build passed.
* Native launcher-owned PostgreSQL execution on Analytic: **BLOCKED** because
  native `postgres`, `initdb`, `pg_ctl` are not installed there. The owned mode
  reports exactly that before starting services; it does not reuse Analytic's
  shared listener. Lifecycle/target rejection are covered by injected tests.
* Relocation check: PASS from another working directory and a temporary checkout
  path containing spaces, without host private configuration. Missing resources
  were resolved in that checkout, with no Analytic-specific resource paths and
  no services started.
* Previous MP4/RTSP loopback/commit-before-ACK evidence remains in private local
  history. No new real-camera or RTX 5080 execution is claimed. Final laptop
  MP4 acceptance and native owned-PostgreSQL lifecycle remain to be run there.

Validation logs are private under `vehicle/media/wsl-handoff-validation/` and
are excluded from publication. Nothing was installed on Analytic for this task.
