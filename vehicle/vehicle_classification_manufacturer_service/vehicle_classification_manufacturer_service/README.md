# Standalone vehicle pipeline

This package extracts the repository's YOLO, ByteTrack, temporal vehicle-type,
track-lifecycle, ROI, performance, and annotation paths into a normal CLI. It does
not start a server and does not use the removed image-analysis subsystems.

Run from the repository root with its Python environment:

```bash
/home/intern/.venv/bin/python -m vehicle_pipeline \
  --video /absolute/path/to/video.mp4 \
  --device auto \
  --output debug/vehicle_output.mp4 \
  --json-output debug/vehicle_results.json
```

Add `--roi-config /absolute/path/to/roi.json` to enable the production ROI rules,
and `--events-output debug/events.jsonl` to stream ROI events to disk.

Python usage:

```python
from vehicle_pipeline import VehicleAnalysisService

service = VehicleAnalysisService(device="auto", enable_roi=False)
result = service.process_video("video.mp4", output_path="vehicle_output.mp4")
```

For frame-by-frame use, call `process_frame(frame, frame_index)` in increasing
frame order, then call `finish()` once at end of input.
