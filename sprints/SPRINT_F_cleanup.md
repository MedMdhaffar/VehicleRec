# Sprint F — live_feed cleanup / refactor (pre-dockerization)

Small, low-risk code prerequisites so the Docker image (Sprint G) is built on a known-good,
container-safe tree. **Code changes only** — file/dir *exclusions* (training cruft, datasets,
frontend, `.pt` artifacts) are handled by `.dockerignore` in Sprint G, NOT deleted from the repo.

Evidence for every item is in `SERVICE_ANALYSIS_live_feed.md` §8 (verified this session).
Do these on the live app, verify, then proceed to Sprint G.

---

## 1. Remove the broken CLI — `live_feed/run.py`
**Why:** broken — `run.py:117` `emitted = tracker.process(...)` captures the 2-tuple
`(emitted, zone_events)` (`pipeline.py:95,304`); `run.py:120` `for p in emitted` then iterates
the tuple → `p.status` → `AttributeError` on the first frame. It also still carries the
pre-Sprint-E encode-in-loop + FPS-cap logic. The service entry is `app.py` (uvicorn).
**Action:** delete `live_feed/run.py`. (If a headless CLI is ever wanted, wrap `app`'s loop —
do not resurrect this file.)
**Check:** `grep -rn "live_feed.run\b" vehicle_services --include=*.py --include=*.sh` → only the
file's own docstring (already confirmed: nothing imports it).

---

## 2. Make the review-agent secrets path container-safe — `review_agent/config.py`
**Why:** line 8 `load_dotenv(Path(__file__).parent.parent.parent.parent / "orchestrator" / ".env")`
is a hardcoded **host** path that won't exist in the container (and points outside the image).
It won't crash (missing-file `load_dotenv` is a no-op), but it's fragile and misleading.
**Action:** make it optional + overridable, and rely on injected env in Docker:
```python
import os
from pathlib import Path
from dotenv import load_dotenv

# Optional .env for local dev; in Docker the key is injected via env_file (compose).
_default_env = Path(__file__).resolve().parents[3] / "orchestrator" / ".env"
_env_file = os.environ.get("REVIEW_ENV_FILE", str(_default_env))
if Path(_env_file).exists():
    load_dotenv(_env_file)
```
`client.py` already reads `os.environ["OPENROUTER_API_KEY"]`, so injected env works unchanged.
**Check:** `python -m live_feed.review_agent.smoke` still passes locally (loads the dev .env);
with the key exported and no .env file present, it must also pass.

---

## 3. Remove dead code (verified unused)
| File | Remove | Evidence |
|---|---|---|
| `live_feed/app.py` | `_FRAME_INTERVAL = 1.0 / cfg.FPS` (line ~46) | only definition; pacing was removed (Sprint E). `grep _FRAME_INTERVAL live_feed/app.py` → 1 hit |
| `live_feed/perf.py` | `inc_dropped()` method + the `_frames_dropped` field + its 3 references in `report()` / `report_dict()` / `final_report()` | `inc_dropped` is never called; the real drop metric is `drop_fps` (computed in `app.py` from the grabber). Drop the misleading always-0 `frames_dropped` from the snapshot. |
| `live_feed/config.py` + `live_feed/pipeline.py` | `VEHICLE_CONF` (config def + the name in pipeline's import list, line ~15) | imported but unused — `.track()` uses `TRACK_CONF`. `grep VEHICLE_CONF live_feed` → only def + import |

**Note:** the dev frontend's `MetricsStrip` may read `frames_dropped`; it's dev-only and tolerant
of missing keys, and `source_fps`/`drop_fps` now carry the real signal. Acceptable.

---

## 4. (OPTIONAL) De-duplicate the OCR engine in live_feed
**Why:** `plate_recognition/ocr.py:75` `_reader = PlateReader(...)` is an intentional **warm
singleton** for the standalone `plate_recognition` REST service (`app.py:46,141`). But live_feed
imports the `PlateReader` *class* (`pipeline.py:73`) which triggers that module-level load, then
builds its **own** `self._plate_reader` in `_ensure_loaded` → **two** OCR engines per process,
one unused. ×N camera containers, that's N extra OCR engines of VRAM/startup.
**Action (keeps warm behavior, does NOT delete — would break the REST service):**
- Add a lazy accessor in `ocr.py`:
  ```python
  _reader = None
  def get_reader() -> "PlateReader":
      global _reader
      if _reader is None:
          _reader = PlateReader(config.OCR_REC_MODEL)
      return _reader
  ```
  Point `plate_recognition/app.py` at `get_reader()` (warm it explicitly at REST startup if desired).
- In `live_feed/pipeline.py _ensure_loaded`, use the shared singleton:
  `from plate_recognition.ocr import get_reader; self._plate_reader = get_reader()`.
**Decision:** OPTIONAL — saves one OCR engine per container. Skip if you'd rather keep live_feed's
reader fully independent; flag in Sprint G's VRAM sizing either way.

---

## 5. Build order & verification
1. Apply 1–3 (and 4 if chosen). Compile-check each: `python -c "import ast; ast.parse(open(f).read())"`.
2. Import-check the package: `python -c "import live_feed.app, live_feed.pipeline, live_feed.perf"`.
3. Run the service locally on ch4, confirm: `/health` ok, `/metrics` returns (no `frames_dropped`
   key, `source_fps`/`drop_fps` present), tracking + line counting still work, review-agent smoke
   passes (or disables gracefully without the key). FPS unchanged.
4. `grep -rn "run.py\|_FRAME_INTERVAL\|inc_dropped\|VEHICLE_CONF" live_feed` → no stragglers.

## 6. Acceptance criteria
- [ ] `run.py` gone; nothing references it.
- [ ] `review_agent/config.py` loads env via `REVIEW_ENV_FILE`/optional path; smoke passes with injected env and no .env file.
- [ ] `_FRAME_INTERVAL`, `inc_dropped`/`_frames_dropped`, `VEHICLE_CONF` removed; app imports + runs; `/metrics` clean.
- [ ] (if chosen) live_feed reuses the OCR singleton; REST service still works.
- [ ] Live run on ch4 behaves exactly as before (FPS, tracking, counting, review agent).
