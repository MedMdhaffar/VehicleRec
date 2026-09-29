# Sprint F5 — Frontend rework (single-page multi-camera dashboard)

The backend is already multi-camera (F3: `multistream.py` + `?cam=` endpoints + global `/start`,
all working — review data is being produced). The **frontend** is still the old per-camera
**tab** design and is broken against the new API. Rework it (not patch) into a **single page**
showing **all camera streams at once**, each with **ms/fps**, with **collapsible analytics** and
**one global Start/Stop**. Debug UI only (the team owns the production UI) — but it must work.

Scope: `live_feed/frontend/src/`. **No backend changes required** (API already supports everything;
one optional metrics tweak in §5).

---

## 0. Why the current UI breaks (root causes)
- `App.jsx`: camera **tabs** + `<CameraView key={activeCamera.id}>` → switching camera **unmounts**
  the view → `useEffect` cleanup **closes the WS + stops the `<img>` MJPEG**. Only one camera is
  ever live; navigating kills the others. **(the "everything stops on tab switch" bug)**
- `CameraView.handleStart` calls `/start?cam=` **per camera**, but backend `/start` is **global**.
- ch4 tile looks blank because ch4's **feed is dark** (`b≈0` in the spike) — not a UI bug; the
  stream is black. (Confirm with `/metrics?cam=4` or the spike's brightness.)
- Review/Calibrate are separate tabs inside the same unmounting tab model.

---

## 1. Target UX (single page)
```
┌ header ───────────────────────────────────────────────────────────┐
│ Live Feed     [ ● Start all ] [ Stop all ]   running · ws●         │
│                                   [ Review ] [ Calibrate ]         │
├───────────────────────────────────────────────────────────────────┤
│  ┌ ch4 ──────────────┐  ┌ ch1 ──────────────┐   ← grid, ALL mounted │
│  │ [annotated MJPEG] │  │ [annotated MJPEG] │     simultaneously     │
│  │ 17 fps · 22 ms    │  │ 16 fps · 19 ms    │   ← overlay on stream  │
│  │ ▸ Analytics       │  │ ▸ Analytics       │   ← collapsed card     │
│  └───────────────────┘  └───────────────────┘                       │
└───────────────────────────────────────────────────────────────────┘
  expanding ▸ Analytics → profiles table + line counts + metrics for that camera
  Review / Calibrate open as an OVERLAY (modal/drawer) — the live grid stays mounted underneath
```
The grid renders one tile per `CAMERAS` entry (config-driven → F4's `hik1` appears automatically).

---

## 2. Components — KILL / UPDATE / CREATE

| Action | File | Detail |
|---|---|---|
| **REWRITE** | `App.jsx` | Remove camera tabs + `activeCamera` unmount model. Render: header with **global Start/Stop** + Review/Calibrate buttons, then a **`<CameraGrid>`** that maps `CAMERAS` → one `<CameraTile cam=…>` each, **all mounted at once**. Review/Calibrate are **overlays** (state `overlay: null|'review'|'calibrate'` + a camera picker), rendered **on top** of the still-mounted grid (so streams never stop). |
| **REPLACE** | `CameraView.jsx` → `CameraTile.jsx` | Per-camera tile: owns its WS (`wsUrl(cam)`) + `<img src={streamUrl(cam)}>` (mounted for its whole lifetime). Overlays **fps + ms** (from WS `metrics`: `data.fps`, `data.e2e_ms`). Below the video, a **collapsible `<AnalyticsCard>`** (default collapsed). **Remove** per-tile Start/Stop. |
| **CREATE** | `AnalyticsCard.jsx` | `<details>`/disclosure that, when expanded, shows `MetricsStrip` + `TrafficPanel` + `ProfilesTable` for that camera. Collapsed by default. |
| **CREATE** | `Controls.jsx` (or in header) | **Global** Start → `POST camUrl(firstCam,'/start')` (one call starts all); Stop → `/stop`. Poll `/health` for running state + per-cam fps. |
| **UPDATE** | `ReviewView.jsx` | Already uses `camUrl(...?cam=)`. Add a **camera selector** (it currently takes one `camera` prop). Show the new fields: `vehicle_maker`, `plate_type`, `observed_category`, agree/disagree badges. Render as the Review **overlay**. |
| **UPDATE** | `CalibrationView.jsx` | Already `camUrl`-based. Add camera selector; render as the Calibrate **overlay**. |
| **KEEP** | `MetricsStrip/TrafficPanel/ProfilesTable` | Reused inside `AnalyticsCard` (they take props already). |
| **KEEP** | `config.js` | Already correct (`camUrl/wsUrl/streamUrl` + `?cam=`). `CAMERAS` from `VITE_CAMERAS`. |

---

## 3. Key behaviors to get right
- **All tiles mounted = all WS + streams live at once.** Never key a tile by `activeCamera`; key by
  `cam.id` and render the whole list. Opening Review/Calibrate must **overlay**, not replace, the grid.
- **One Start.** A single global Start triggers the backend's global `/start`. Per-tile pipeline
  status is derived from `/health` (`cameras[].fps`) + the WS connection, not per-tile control.
- **ms/fps overlay.** Read from the per-cam WS `metrics` message (`{type:'metrics', cam, data}`),
  show `data.fps` and `data.e2e_ms` on the tile. (Backend already pushes this ~`WS_METRICS_HZ`.)
- **A dark feed (ch4) is not a UI failure.** Show the stream as-is; optionally add a small "feed
  dark" hint if the tile is near-black, but don't treat it as an error.
- **MJPEG connection count.** Each mounted `<img>` holds one `/stream.mjpg?cam=` connection → the
  backend encoder for that cam runs while the tile is on screen (correct). Closing the overlay must
  not unmount tiles (so encoders keep running only for visible tiles).

---

## 4. Build order
1. `CameraTile.jsx` (from `CameraView`): strip Start/Stop, add fps/ms overlay + `AnalyticsCard`.
2. `AnalyticsCard.jsx` (collapsible wrapper around the existing panels).
3. `App.jsx` rewrite: header + global controls + `CameraGrid` (all tiles) + overlay host.
4. Wire Review/Calibrate as overlays with a camera selector.
5. `npm run build` (do **not** start the dev server — per project convention).

---

## 5. Optional backend tweak (only if needed)
- The WS already sends `metrics` with `fps` + `e2e_ms` per cam — enough for the overlay. If you want
  the overlay to update faster than `WS_METRICS_HZ` (1 Hz), raise `WS_METRICS_HZ` or have the tile
  poll `/metrics?cam=` at ~2 Hz. No structural API change.

---

## 6. Verification / acceptance
- [ ] One page shows **all** cameras streaming **simultaneously**; opening Review or Calibrate does
      **not** stop any stream (grid stays mounted under the overlay).
- [ ] **One Start** brings all cameras live; **Stop** stops all. No per-tile Start.
- [ ] Each tile overlays live **fps + ms**.
- [ ] Analytics card is collapsed by default; expanding shows that camera's profiles + line counts +
      metrics; collapsing keeps the page clean.
- [ ] Review overlay lists records per camera (selector), shows maker/plate_type/observed_category +
      agree/disagree, and Save-as-verified works (`/review/verify?cam=`).
- [ ] ch4 tile: if `/metrics?cam=4` shows a normal frame but the tile is black → real bug (fix); if
      the feed is dark (`b≈0`) → expected, not a UI issue.
- [ ] `npm run build` clean.

---

## 7. Notes
- Backend is unchanged and already correct (F3). This is frontend-only + one optional metrics knob.
- Config-driven grid → when **F4** adds the Hikvision (`hik1`) to `CAMERAS`/`VITE_CAMERAS`, it shows
  up in the grid with no frontend change.
- The review agent **pipeline** is fine (data is being produced); this sprint only fixes its **UI**
  integration into the single-page layout.
