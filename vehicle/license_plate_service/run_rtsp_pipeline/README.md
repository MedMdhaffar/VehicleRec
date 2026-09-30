# Standalone license-plate RTSP pipeline

This package runs the track-based license-plate recognizer against one RTSP
camera. It reconnects after interrupted reads and can optionally record the
annotated stream and write a JSON session summary.

Run from the repository's `license_plate_service` directory so the sibling
pipeline packages are on Python's import path:

```bash
cd /home/intern/pipeline/license_plate_service
/home/intern/.venv/bin/python -m run_rtsp_pipeline.run_rtsp_prototype \
  --url 'rtsp://user:password@camera-host:554/stream' \
  --camera-id entrance-1 \
  --record-output run_rtsp_pipeline/test_output/recording.mp4 \
  --summary-output run_rtsp_pipeline/test_output/summary.json
```

The URL can instead be supplied through `RTSP_URL`, and the camera ID through
`RTSP_CAMERA_ID`. Stop an unlimited run with `Ctrl+C`; use `--max-frames N` or
`--max-seconds N` for a bounded test. Recording and summary files are optional,
and the final summary is always printed to standard output.

Recognition is local by default. To allow the optional OpenRouter fallback,
configure `OPENROUTER_ENABLED=true` and `OPENROUTER_API_KEY`, then add
`--agent-enabled` to the command.

The packaged detector models and tracker configuration are used by default.
Use `PLATE_DETECTOR_PATH`, `WORD_DETECTOR_PATH`, or `--tracker-config` to
override them.
