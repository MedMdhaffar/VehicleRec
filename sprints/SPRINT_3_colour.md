# Sprint 3 — Colour

**Phase:** A (vehicle_classification)
**Depends on:** Sprint 2
**Goal:** Add per-vehicle **colour** estimation using the local HSV pipeline and integrate it into the existing response envelope and annotated image.

---

## Why this sprint

Colour is the last piece that makes `vehicle_classification` feature-complete (detect + type + colour). The production method is fully local: five paint-oriented ROIs feed HSV analysis, crop-quality weighting, and temporal aggregation. The envelope already has nullable `colour`/`colour_confidence` fields (Sprint 0), so this sprint fills them without a schema change.

---

## Scope

**In scope**
- `common/colour.py` — crop → dominant colour + confidence, mapped to a fixed palette.
- Integration into `/classify` and `/classify_crop` (colour added per detection).
- Colour appended to the annotated label.
- Config: palette, paint ROIs, quality settings, and temporal thresholds.

**Out of scope**
- Learned or external colour classifiers.
- Multi-tone / metallic / two-tone vehicle handling beyond a single dominant colour.

---

## Approach — local HSV

1. **Sample the body, not the noise.** Analyze five bounded, paint-oriented ROIs that avoid crop borders, roof glare, wheels, and the road strip.
2. **Convert to HSV.** Work in HSV — it separates chroma (hue) from brightness, which is what makes "is this red or just a dark thing" tractable.
3. **Achromatic vs chromatic split** (this is the part naive hue-binning gets wrong):
   - **Low saturation** → achromatic → decide **white / gray-silver / black** by **value** (brightness) thresholds.
   - **Sufficient saturation** → chromatic → bin by **hue** into red / orange / yellow / green / blue / purple, refining brown/beige from low-value oranges.
4. **Combine ROI evidence** into one frame observation, rejecting or reducing the influence of poor paint samples.
5. **Weight crop quality** using size, sharpness, exposure, and clipping diagnostics.
6. **Aggregate temporally** across the track until early lock, sample-budget finalization, or track loss.

**Palette (fixed, configurable):** `white, silver/gray, black, red, orange, yellow, green, blue, brown/beige` (+ `unknown` when confidence is below a floor).

---

## Deliverables

```
common/colour.py          # estimate_colour(crop) -> (colour:str, confidence:float)
common/config.py          # palette, hue bins, S/V thresholds, and confidence floor
live_feed/colour_temporal.py # quality-weighted temporal aggregation
live_feed/pipeline.py      # per-track observations and final colour
vehicle_classification/
  app.py                  # colour wired into both endpoints + annotation
```

---

## Tasks

1. **`common/colour.py`**
   - `estimate_colour(crop_bgr) -> (colour, confidence)`.
   - Five paint-oriented ROIs; HSV conversion; achromatic/chromatic split; ROI evidence and confidence; `unknown` below the confidence floor.
   - Pure local function with no learned model state or network dependency.

2. **Config** (`common/config.py`)
   - `COLOUR_PALETTE`, hue-bin edges, saturation/value thresholds for the achromatic split, and `COLOUR_CONF_FLOOR`.

3. **Integrate into the service**
   - In `/classify` and `/classify_crop`, after detection: for each `Detection`, crop its bbox from the frame, call `estimate_colour`, and set `detection.colour` + `detection.colour_confidence`.
   - Append colour to the annotated label (`van  white  0.91`).

4. **Smoke validation**
   - Run sample images; confirm obvious cases (white van, black car, red car) are right and confidence is sensible; confirm achromatic vehicles aren't mislabelled as a random hue.

---

## Acceptance criteria

- `POST /classify` → each detection now carries `colour` + `colour_confidence`; annotated image shows type **and** colour per vehicle.
- White / black / silver vehicles are classified as achromatic (not as a random hue) — the common failure mode is handled.
- Clear chromatic cases (red/blue/etc.) are correct on daytime samples.
- `colour.py` remains a pure local crop-analysis function with no service coupling or learned colour model.
- Documented limitations (below) are written into the module docstring.

---

## Notes / limitations (HSV baseline)

- **Night / low light / glare / strong colour-cast lighting** can degrade accuracy.
- Single **dominant** colour only — two-tone vehicles report the majority colour.
- Metallic/pearl finishes can read as silver/gray.
- Crop-quality weighting and temporal aggregation reduce the effect of weak individual frames.

---

## End of Phase A

After this sprint, `vehicle_classification` is feature-complete: **detect → type → colour**, stateless REST (`/classify`, `/classify_crop`), returning `{JSON + annotated image}`, backed by the streetdet-trained yolo26. Next: **Phase B — plate_recognition** (Sprints 4–5), reusing `common.detector`.
