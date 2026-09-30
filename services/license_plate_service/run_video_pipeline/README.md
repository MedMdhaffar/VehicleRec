# Standalone license-plate video pipeline

This package tracks license plates in a prerecorded video, recognizes the best
crop for each track, writes an annotated video, and produces JSON metrics and a
per-track summary.

Run from the repository's `license_plate_service` directory so the
`run_video_pipeline` package is on Python's import path:

```bash
cd /home/intern/pipeline/license_plate_service
/home/intern/.venv/bin/python -m run_video_pipeline.run_video_prototype \
  /absolute/path/to/video.mp4 \
  --output run_video_pipeline/test_output/annotated.mp4 \
  --metrics-output run_video_pipeline/test_output/metrics.json
```

The annotated video and metrics paths are generated automatically when the
corresponding options are omitted. Use `--max-frames N` for a short test run.

Recognition is local by default. To allow the optional OpenRouter fallback,
configure `OPENROUTER_ENABLED=true` and `OPENROUTER_API_KEY`, then add
`--agent-enabled` to the command.

The packaged plate detector, marker detector, and ByteTrack configuration are
used by default. They can be overridden with `PLATE_DETECTOR_PATH`,
`WORD_DETECTOR_PATH`, and `--tracker-config`, respectively.
