# Local manufacturer audit — 2026-11-13

The original target is run `37e9b02b-e16c-47c8-b479-157cd3161541`:
completed 2026-11-13 02:33:37 UTC, eight frames, **15 tracks and eight Ford
predictions**. It was the latest matching completed manufacturer-enabled run
at inspection, before the explicitly separate diagnostic rerun below.

The console stages uploads as `input.mp4` and did not retain their original
filenames. Consequently, `tunis-from-5s.mp4` is the user-supplied clip identity;
the independently verifiable file identity is SHA-256
`14369b11d34544d981d0f125b2b330608af6014e3a053ed07ee2bfe944d2c27d`.
An earlier matching-upload run had 13 tracks, so it is not the requested run.

| Stored result | Original | Separate diagnostic rerun |
|---|---:|---:|
| Ford | 8 | 8 |
| Volkswagen | 2 | 2 |
| Mercedes | 1 | 1 |
| Unknown | 2 | 2 |
| Unavailable / execution failed | 2 | 2 |
| Total tracks | 15 | 15 |

These are output counts, **not accuracy measurements**. Human labels are pending.

## Retention and the one diagnostic rerun

The original journal retained source-frame/crop hashes, bounding boxes, crop
shapes, track identities, request IDs, runtime provenance and bounded completed
raw answers. It did **not** archive the exact PNG transport/native VLM input.
The 14 available historical crop PNGs were recovered previously from the MP4;
this audit independently verified their decoded BGR hashes, source-frame hashes
and bounding-box slices. They are labeled **reconstructions**, including for
failed attempts. They cannot establish whether the original native vision path
consumed the image. Track 13 had no eligible manufacturer crop or call.

Exactly one rerun was started through the existing console API via port 5173:
`7f051be8-3556-49e8-b9aa-7c0c74afe960`, started 03:18:07 UTC and completed
03:19:13 UTC. It used identical uploaded bytes, manufacturer recognition on,
`max_frames=8`, `max_seconds=180`, and unchanged model/runtime settings. It
completed eight frames and 15 tracks. **A rerun cannot prove what the original
model saw**, even where its hashes and outputs match.

Private, process-local instrumentation copied the submitted PNG and the exact
PNG passed into `LlamaBackend.generate`, recorded the image returned by
`load_media`, confirmed native image chunks, and captured the unfiltered native
response. A pipe carried diagnostics to the parent so the existing worker file
size limit was unchanged. The original implementations received unchanged
arguments and their return values were passed through. The model, prompt,
decoder settings, acceptance rules and native limits were not edited.

Twelve rerun calls reached generation and the native image path. Their submitted
PNG, generation input and loaded media bytes matched exactly. Two other calls
(tracks 14 and 15) failed during CUDA initialization before generation:
`cuInit=100, devices=0`. Their submitted PNGs are retained separately, but their
VLM-input cells correctly say **Unavailable**. The original two failures retain
only a generic failed outcome; the rerun error does not establish their original
cause. No extra retry was performed. One track had no eligible call in either run.

The rerun reports all five cleanup guarantees true and no active slot. The
temporary diagnostic backend was replaced by the normal documented backend
after the worker exited; Vite was reused throughout. Both the direct API on
8002 and proxied run list on 5173 subsequently returned HTTP 200.

## Image, result and track checks

- Each run has 14 distinct crop hashes and 14 distinct request IDs. No crop hash
  is reused across tracks. The rerun used 14 distinct native process IDs.
- Each SQLite vehicle key equals its canonical vehicle-binding hash; each
  request ID equals its full binding hash; each result carries that same binding.
- Run ID, service track, raw track, epoch, frame, crop size, crop hash and frame
  hash agree between the rerun capture, journal and status response.
- Both MP4s were decoded locally to verify every attempted source-frame hash and
  bounding-box crop hash. All corresponding original/rerun crops match.
- The real manufacturer policy was replayed read-only against all 14 journal
  results in each run, with lifetime endpoints reconstructed from retained
  Colour receipts. Every status and normalized brand agrees with the API rows.
- Every corresponding completed raw answer is byte-identical between these two
  runs. Six answers are also identical across tracks 3, 6, 8, 9, 10 and 11.
  Those tracks have different images and, in the rerun, separate native
  processes with their own verified inputs and captured responses. Repetition
  alone is not evidence of stale-response reuse.

## Where Ford appears

| Hypothesis | Evidence / disposition |
|---|---|
| A Ford example in the prompt | Absent. The sole brand example is Toyota. The captured rerun prompts contain no Ford. Current model artifacts, prompt and runtime source provenance cryptographically match every original binding. |
| A parser fallback inserts Ford | Absent. The parser reads the raw `manufacturer` field, normalizes it, and checks the vocabulary. Unknown/unsupported brands become Unknown; invalid JSON is rejected. No Ford default exists. Real-parser replay matches every stored row. |
| The UI inserts Ford | Absent in the inspected renderer. It displays `make` for accepted results and separate Unknown/Unavailable states. Ford is already present before projection/UI. |
| A stale or crossed response | No mismatch found. The rerun records separate native processes, current PNGs loaded into vision, and raw responses matching their own journal bindings. The adapter resets model/cache state around each call. Original native input/response-stream capture is unavailable, so historical native consumption cannot be established directly. |
| The VLM itself emits Ford | Verified: eight original completed raw answers contain Ford before parsing. Eight rerun raw native responses do too. This identifies the first observed source of the value; it does not establish brand correctness or its underlying cause. |

The repeated brand concentration could reflect recognition limitations or input
ambiguity. Neither is established as an accuracy finding without human labels.
No model, parser, prompt, UI or acceptance change is justified by this audit.
The demonstrated evidence gap is original native-input retention; the smallest
action taken was the isolated local diagnostic capture, without a production
behavior change.

## Local contact sheets and labeling

New audit images and full-answer exports remain in the ignored private audit
directory, outside Django's media root. Existing source journals and historical
run crops were preserved in their original run folders:

`vehicle/private-audits/manufacturer-20261113/`

Open these files locally on Analytic, or transfer them privately to your own
machine. They are self-contained HTML files with embedded images and no network
requests. Do not publish them or serve this directory as public media.

| File | Purpose |
|---|---|
| `original-reconstruction-blind.html` | Prediction-free original crop reconstructions for first-pass labeling |
| `diagnostic-rerun-blind.html` | Prediction-free exact rerun VLM inputs; unavailable inputs remain explicit |
| `original-reconstruction-annotated.html` | Original crop reconstructions, retained raw answers and normalized results |
| `diagnostic-rerun-annotated.html` | Exact rerun inputs, captured raw answers and normalized results |

Each sheet has exactly one row per track, zero-based source frame and width ×
height. Annotated sheets include Unknown and Unavailable. PNG exports with the
same basenames are also available. The full per-track audits include hashes,
binding checks, native process IDs and failure diagnostics.

Blind HTML and PNG copies contain **no model answers or predicted brands**.
Human brand fields are blank. Unavailable inputs and crops with a short side
below 96 pixels start as **Unjudgeable**, explicitly a conservative provisional
review aid, not a change to the pipeline's existing 48-pixel admission rule.
Thirteen rows are provisionally Unjudgeable in each sheet; the two larger crops
remain Unreviewed, not presumed identifiable. A human should override the size
screen when appropriate and mark obscured or ambiguous crops Unjudgeable after
inspection. Ambiguity has not been invented from dimensions alone.

The blind sheet's button exports human labels locally. Matching blank label
templates are provided. Browser test labels were deleted and are not evidence.
No brand accuracy will be reported until actual human labels are supplied.

## Validation and preservation

All audit assertions passed, four local HTML sheets passed Chromium checks
(15 rows each, 14 reconstructed / 12 actual VLM images loaded, no external
requests, prediction-free blind DOM, functional local label download), and all
13 manufacturer regressions passed. Private logs and structured results are in
the audit directory. All 14 pre-audit hashes of manufacturer source/assets,
frontend renderer, console lifecycle and worker files remain unchanged.

Commands executed from the repository root:

```bash
# Temporary diagnostic backend, only for the separately labeled rerun
T1_CONSOLE_PYTHON=/home/intern/part-time/aziz/pipeline/vehicle/private-audits/manufacturer-20261113/worker_capture.py PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py runserver 127.0.0.1:8002 --noreload
# One HTTP POST through http://127.0.0.1:5173/api/t1/runs/
# multipart: source_kind=mp4, manufacturer_enabled=true,
# max_frames=8, max_seconds=180; video=the original run's input.mp4 bytes.
# HTTP 202; run ID recorded above. Do not repeat for this audit.

# Restored normal backend after the diagnostic worker exited
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py runserver 127.0.0.1:8002 --noreload

PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -B vehicle/private-audits/manufacturer-20261113/build_audit.py > vehicle/private-audits/manufacturer-20261113/build-audit.log
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -B -m unittest vehicle.common.tests.test_manufacturer > vehicle/private-audits/manufacturer-20261113/manufacturer-tests.log 2>&1
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node vehicle/private-audits/manufacturer-20261113/check_sheets.cjs
```

Changes for this audit are this report and private diagnostic scripts/artifacts
only. Existing work and the Start/Stop lifecycle fix were preserved. No DB1
contract/catalogue changes, production writes, commits or pushes were made.
