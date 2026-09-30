# Standalone vehicle colour pipeline

This package detects and tracks vehicles in an image or video, estimates each
vehicle's colour, and prints per-frame JSON results. It can optionally write an
annotated image/video and JSON Lines output.

Run from the repository root with its Python environment:

```bash
mkdir -p vehicle_color_service/results
/home/intern/.venv/bin/python -m vehicle_color_service.detector \
  --video /absolute/path/to/video.mp4 \
  --device auto \
  --output vehicle_color_service/results/annotated.mp4 \
  --jsonl vehicle_color_service/results/results.jsonl
```

For a still image, replace `--video` with `--image` and use an image output
path:

```bash
mkdir -p vehicle_color_service/results
/home/intern/.venv/bin/python -m vehicle_color_service.detector \
  --image /absolute/path/to/image.jpg \
  --output vehicle_color_service/results/annotated.jpg
```

`--output` and `--jsonl` are optional. The packaged vehicle model and tracker
configuration are used by default. Set `VEHICLE_MODEL_PATH` or
`TRACKER_CONFIG` to override them.
