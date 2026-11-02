# Idara investigation

Production code was not modified. Tests use Django's current model code and assets.

## Confirmed issue in the saved video

Input: `vehicle/media/license_plate/2610c8747a524dcc94108e0f4eba1ca2/source.mp4`.

The tracker assigns **track ID 1** to the initial NT plate and continues using ID 1 through both Idara scenes (frames 120 and 240). The video cuts between different cars whose plates occupy similar screen positions. Tracking uses geometry, not plate appearance or scene-cut detection.

The saved production result for ID 1 is the initial NT reading `214172 nt` from frame 0. There are no Idara recognition attempts in that job. Recognition is cached per track; subsequent attempts require substantial crop-quality/detail improvement, rather than a new scene or changed plate appearance. The Idara cars therefore inherit the existing track without an independent recognition attempt.

This is a tracking/recognition-scheduling identity problem in this video. Weak category matching and strict separator validation are real weaknesses, but the tests do not establish them as the cause of these two missed Idara cars.

## Tests and observations

| Test | Result |
|---|---|
| Leave-one-out matching of all 12 stored embeddings | 11/12 correct; all 3 Idara correct; NT reference 5 matched Tunis |
| Recomputed original reference features with self excluded | Same category outcomes as stored embeddings |
| Three Idara references at original, 160px, and 80px widths | 9/9 classified Idara; all produced accepted numbers |
| Detector on 10 video frames from 120 through 300 | Plate found in all 10 at default settings; confidence approximately 0.85–0.88 |
| Manually cropped plate from frame 120 | Idara; `17 - 138956`; accepted |
| Manually cropped plate from frame 240 | Idara; `17 - 148885`; accepted |
| Detector + ByteTrack replay, frames 0–320 | Every frame uses track ID 1, including both Idara scenes |
| Eight synthetic Idara parser cases | Normal/Unicode dash and Arabic digits accepted; absent separator, low confidence, and contradictory NT label rejected |
| Saved track 2 and 3 crops | NT predictions; these are separate scenes, not the two confirmed Idara scenes |

## Scope and limits

Reference tests reuse the small existing reference collection and are not independent accuracy validation. Downscaled references test resolution, not real motion blur or new plates. Manual crops deliberately bypass detector/tracker scheduling. The tracking replay tests every frame through 320; the existing production debug records establish that only the initial NT crop was recognized for ID 1 in the saved job. No full production inference rerun was required.

The original reference images are absent from Django's `assets/references/images/`. Diagnostic tests locate copies under `services/` and accept them only when their SHA-256 matches the manifest. `reference_sources.json` records all 12 verified sources. This does not add a runtime dependency to Django.

## Reproduce

Run from repository root in the existing runtime:

```bash
PYTHONDONTWRITEBYTECODE=1 .runtime/plates/venv/bin/python investigations/idara/run_tests.py
PYTHONDONTWRITEBYTECODE=1 .runtime/plates/venv/bin/python investigations/idara/test_video_detection.py
PYTHONDONTWRITEBYTECODE=1 .runtime/plates/venv/bin/python investigations/idara/test_tracking.py
```

The video detection script uses `frame_120.jpg` and `frame_240.jpg`, extracted from the saved input video and retained in this folder. All outputs stay in this folder.

- `results.json`: reference comparisons, 15 crop classifications/OCR results, and 8 parser cases.
- `video_detection_results.json`: 10 detector probes and 2 manual-crop classifications/number results.
- `tracking_results.json`: per-frame detections and track IDs for 321 frames.
- `run.log`, `detection.log`, `tracking.log`: captured execution logs.
- `frame_*.jpg`, `manual_plate_*.jpg`: inspected video frames and manual plate crops.
