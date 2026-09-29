# Sprint C — ROI calibration + traffic analysis (inside live_feed)

**Phase:** C. Built as a **module inside `live_feed`** (not a separate service).
**Depends on:** the live_feed pipeline (LF-1/2/3) — vehicle detect + ByteTrack + per-vehicle `VehicleProfile`.
**Principle:** **pure computation — NO new models, NO new tracker.** All ROI logic is geometry/arithmetic on the boxes the existing pipeline already emits. Reuse the existing `track_id` only for short-term (line-crossing prev→cur); anchor long-term occupancy by **position/IoU** (immune to ID churn).

---

## 0. Context for the implementer (read first)

- The pipeline (`live_feed/pipeline.py`, `EnrichedTracker.process`) already, per frame: runs `model.track()` → for each vehicle has `track_id`, `bbox [x1,y1,x2,y2]`, `cls`, `conf`, and a `VehicleProfile` with motion state (`_stationary_frames`, `_last_centroid`) and a plate state machine.
- The serving layer (`live_feed/app.py`) runs the pipeline in a thread and exposes `/start /stop /health /metrics /stream.mjpg /ws`. `draw_live_frame` (in `common/annotate.py`) annotates the MJPEG.
- One process = one camera; channel = `cfg.LF_CHANNEL`; ROIs are **per camera**.
- Coordinates everywhere are **native frame pixels** (the camera's resolution, e.g. 1920×1080).
- Foot-point of a box = **bottom-center** `((x1+x2)/2, y2)` — the containment point for all ground zones (decided).

---

## 1. The four ROI functions (what each one DOES)

An ROI is a shape; its **function(s)** decide the rule applied to vehicles in/around it.

### Polygon functions (a polygon may carry SEVERAL)
| Function | Watches | Rule (pure geometry) | Output / event |
|----------|---------|----------------------|----------------|
| **`read_zone`** | vehicles whose **foot-point ∈ polygon** | **gate the plate cascade** — plate detect/OCR/type run ONLY for in-zone vehicles (see §4) | plate reads only for in-zone cars; far/side/background cars get **zero** plate work |
| **`parking`** | vehicles **occupying a spot** in the polygon (position-anchored, §5) | time how long a spot stays occupied | `parking_duration`; **over-stay** event if dwell > `OVERSTAY_SEC` |
| **`restricted`** | vehicles **occupying a spot** in the polygon | any sustained occupancy = illegal | **violation** event (after `RESTRICTED_MIN_SEC`) |
| **`occupancy`** *(optional, include)* | count of foot-points ∈ polygon | live count vs threshold | count; **overcrowding** event if count > `OCCUPANCY_MAX` |

`parking` vs `restricted`: `parking` is a *legal* area we **measure** (duration + over-stay); `restricted` is a *forbidden* area where **any** sustained stop is a **violation**.

### Line function (counting)
| Shape | Rule | Output |
|-------|------|--------|
| **counting line** (segment A→B) | a vehicle's foot-point path **crosses** the segment between consecutive frames | **+1** by **direction** (`left_to_right` / `right_to_left`, from the sign of `line_side`) and by **vehicle class** |

---

## 2. ROI config (`live_feed/rois/ch{N}.json`)

```json
{
  "camera": 4,
  "frame_size": [1920, 1080],
  "polygons": [
    { "id": "p1", "name": "entry lane", "points": [[x,y],...],
      "functions": ["read_zone","parking"] },
    { "id": "p2", "name": "no-stop", "points": [[x,y],...],
      "functions": ["restricted"] }
  ],
  "lines": [
    { "id": "l1", "name": "gate", "a": [x,y], "b": [x,y], "count": true }
  ]
}
```
- `functions` ∈ `{read_zone, parking, restricted, occupancy}`; multi-function allowed.
- Empty/missing file → no zones (see fallbacks). Coords are **native pixels**.

---

## 3. `live_feed/roi.py` — config model + compute primitives (no models)

- `RoiConfig.load(channel)` / `.save(channel, data)` → `live_feed/rois/ch{N}.json`; returns empty config if none.
- `RoiConfig` exposes parsed `polygons` (with `functions`) and `lines`.
- Geometry helpers (plain math / cv2):
  - `foot_point(bbox) -> (x,y)` = `((x1+x2)/2, y2)`.
  - `point_in_polygon(pt, poly_points) -> bool` (`cv2.pointPolygonTest` or ray-cast).
  - `iou(boxA, boxB) -> float`.
  - `line_side(pt, a, b) -> float` (signed; >0 one side, <0 the other).
  - `segment_crosses(p_prev, p_cur, a, b) -> bool` (does the path step cross the segment — `line_side` sign flip **and** within segment bounds).

---

## 4. THE PLATE-WORK CONDITION (read_zone gating) — detail

This is the new gate added to the **existing** plate state machine in `pipeline.py`. **Do not remove the existing conditions — ADD `in_read_zone` in front of them.**

Existing (keep):
```python
parked       = profile._stationary_frames >= STATIONARY_N
eff_max_ocr  = STATIONARY_MAX_OCR if parked else MAX_OCR        # 1 parked, 4 moving
resolved     = profile.plate.locked or profile.plate.ocr_attempts >= eff_max_ocr
cooldown_ok  = (frame_idx - profile._last_ocr_frame) >= OCR_COOLDOWN
```
**NEW gate:**
```python
read_zones = roi.polygons_with_function("read_zone")
in_read_zone = (len(read_zones) == 0)                          # FALLBACK: no read_zone defined → no gating
               or any(point_in_polygon(foot_point(bbox), z.points) for z in read_zones)

ocr_eligible = in_read_zone and (not resolved) and cooldown_ok
```
Then the existing `if ocr_eligible:` block (plate_detect → OCR → classify_type) is unchanged.

**Behaviour:**
- ≥1 `read_zone` defined → plate cascade runs **only** for vehicles whose foot-point is inside one. Cars outside (far, side-on, background, other lanes) get **zero** plate detect/OCR/type — the big waste-cut you asked for.
- **No** `read_zone` defined (uncalibrated camera) → `in_read_zone = True` → behaves exactly as today (don't break it).
- Net plate eligibility = **in read_zone AND (new/moving/unread) AND (every `OCR_COOLDOWN`th frame)** — the maximal reduction, stacking on the existing state machine.
- `in_read_zone` is one cheap point-in-polygon test per vehicle per frame.

---

## 5. Parking / restricted — position-anchored occupancy (no track, no model)

Per **parking** and **restricted** polygon, maintain a list of **occupancy slots**:
```
Slot = { bbox, occupied_since (ts), last_seen_frame, confirmed (bool),
         present_at_startup (bool), overstay_fired (bool), violation_fired (bool),
         plate, type }   # plate/type snapshotted from the matching vehicle if available
```

**Each frame, per such polygon:**
1. **Candidates** = vehicle boxes whose `foot_point ∈ polygon`.
2. **Match** each existing slot to its best candidate by **IoU ≥ `IOU_MATCH`**:
   - match → update `last_seen_frame`, refresh `bbox`; `occupied_since` **unchanged** (survives ID churn).
   - candidate matching **no** slot → create a new slot (`occupied_since = now`, `confirmed = False`).
3. **Confirm**: a slot becomes `confirmed` once it has been matched for `MIN_OCCUPY_FRAMES` consecutive frames (this filters cars merely *driving through* — they never sustain the overlap). Only **confirmed** slots count as "parked."
4. **Free**: a slot not matched for `FREE_FRAMES` consecutive frames → the spot emptied → **finalize** (emit `parking_ended` with `duration = last_seen − occupied_since`), remove slot.
5. **Events** (confirmed, non-startup slots only):
   - `parking`: when `now − occupied_since > OVERSTAY_SEC` and `not overstay_fired` → emit **over-stay** (once).
   - `restricted`: when `now − occupied_since > RESTRICTED_MIN_SEC` and `not violation_fired` → emit **violation** (once).

**Startup handling (decided — no alerts):** vehicles already occupying a zone when the service starts get slots with `present_at_startup = True`, `occupied_since = service_start`. Their duration is reported as a **lower bound** ("≥ since startup"), and they **never fire** over-stay/violation alerts. Only spots that become occupied *after* startup can alert.

Notes: this uses **no track_id** — occupancy identity = the *position* (IoU persistence). Moving cars never confirm. A car whose ByteTrack ID flips while parked keeps its slot (same position).

---

## 6. Line counting (bidirectional, per class)

Per `count` line, keep per-class counters `{left_to_right: {...}, right_to_left: {...}}` and a small per-`track_id` memory of the **previous foot-point** (reuse the existing `track_id` — short-term continuity only).

Each frame, for each tracked vehicle:
- `prev = last foot-point for this track_id` (skip if first sighting).
- `cur = foot_point(bbox)`.
- if `segment_crosses(prev, cur, line.a, line.b)`:
  - direction = sign of `line_side(cur, a, b)` vs `prev` → `left_to_right` or `right_to_left`.
  - increment `counts[direction][vehicle_type] += 1`; emit a `crossing` event.
  - de-dupe: one count per track per line (mark the line id on the profile so a track that lingers on the line isn't double-counted).
- update the stored prev foot-point.

---

## 7. Calibration (endpoints + frontend) — draw once per camera

**Endpoints (add to `app.py`):**
| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/snapshot` | latest **raw** frame as JPEG (for the draw canvas) |
| `GET` | `/rois` | current saved ROI config |
| `POST` | `/rois` | validate + save drawn config → `rois/ch{N}.json` |
| `POST` | `/reload-rois` | reload config into the running tracker (no restart) |

The tracker loads `RoiConfig.load(LF_CHANNEL)` on `/start` and on `/reload-rois`.

**Frontend — Calibration view (Vite):**
- Load `GET /snapshot` onto a `<canvas>`.
- Draw **polygons** (click points, close shape), assign **function(s)** via checkboxes + a name; draw **lines** (click A then B) + name.
- **Coordinate scaling (critical):** the canvas shows the frame **scaled**; convert click coords → **native pixels** via `native_size / canvas_size` before saving, and store `frame_size = native`. Otherwise zones are misaligned at runtime.
- Save → `POST /rois`; existing zones load from `GET /rois` for editing; a Reload button calls `/reload-rois`.

---

## 8. Overlay (live confirmation) — `draw_live_frame`

On the annotated MJPEG, draw: each polygon (translucent fill + name + its functions), each line (A→B with an arrow + name), live **counters** per line, and per **confirmed parking/restricted slot** its **dwell** (e.g. `3:21`, red if over-stay/violation). This is how the operator confirms calibration and sees the analytics live.

---

## 9. Config knobs (`live_feed/config.py`)

```python
ROI_DIR            = live_feed/rois
IOU_MATCH          = 0.5     # occupancy slot match
MIN_OCCUPY_FRAMES  = 15      # sustained overlap to confirm a parked occupancy (filters pass-through)
FREE_FRAMES        = 15      # frames a slot can be unmatched before it's freed/finalized
OVERSTAY_SEC       = 300     # parking over-stay alert threshold (5 min)
RESTRICTED_MIN_SEC = 5       # sustained stop in a restricted zone → violation
OCCUPANCY_MAX      = 10      # overcrowding threshold (occupancy function), per zone (overridable per polygon later)
```

---

## 10. Events / output

Push over `/ws` (and log to `livelog`) zone events alongside the existing profile events:
```
{ "type":"zone_event", "event":"overstay|violation|crossing|overcrowding|parking_ended",
  "zone": "p1"|"l1", "track_id"?: int, "vehicle_type": "...", "plate"?: "...",
  "duration_s"?: float, "direction"?: "left_to_right|right_to_left",
  "count"?: int, "present_at_startup"?: bool, "ts": "ISO8601" }
```

---

## 11. Build order (within this sprint)

1. **`roi.py`** — config load/save + geometry primitives (+ a tiny self-test of point-in-poly / IoU / crossing).
2. **Calibration** — `/snapshot`, `/rois` (GET/POST), `/reload-rois`; load ROIs into the tracker; Frontend draw view (with coord scaling); overlay of zones on MJPEG. *(Validate: draw, save, see zones aligned on the live video.)*
3. **Plate read_zone gating** (§4) — smallest, highest-value rule; verify plate work only fires inside the read_zone.
4. **Parking + restricted occupancy** (§5) — shared slot logic; over-stay/violation events; startup baseline (no alert); dwell overlay.
5. **Line counting** (§6) — per-class bidirectional counters + overlay + crossing events.

---

## 12. Acceptance criteria

- Calibration: with the camera running, draw ≥1 polygon (multi-function) + ≥1 line, save → `rois/ch{N}.json` in **native pixels**; `/reload-rois` → zones **overlay correctly aligned** on the MJPEG.
- **Plate gating**: with a `read_zone`, plate detect/OCR fires **only** for vehicles whose foot-point is inside; with **no** `read_zone`, behaviour is unchanged. (Confirm via `plate_calls_per_frame` and the events log — out-of-zone cars get no plate work.)
- **Parking/restricted**: a car that stops in a `parking`/`restricted` zone produces a **confirmed** slot after `MIN_OCCUPY_FRAMES`; dwell counts up; over-stay/violation fires once past threshold; a car **driving through** never confirms; **startup occupants never alert**; ID churn while parked does **not** reset dwell.
- **Counting**: a vehicle crossing a line increments the correct **direction + class**, counted **once** per track.
- No new models or trackers added; all ROI logic is geometry on existing detections.

---

## 13. Notes / out of scope

- ROIs are per-camera and assume a **fixed** camera (calibrate once).
- `occupancy` function is optional in this sprint — include the count + overcrowding event if cheap; otherwise stub for later.
- Per-spot (pre-drawn parking-slot) reporting is out of scope — occupancy is auto-discovered by position.
- The independent FPS fix (move display encode off the hot loop) is a separate concern (LF perf), not this sprint.
