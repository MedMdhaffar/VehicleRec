# Local license-plate RTSP pipeline

Use the persistent runtime provisioned by `scripts/setup_local_plate_runtime.sh`.
From the repository root:

```bash
export REGISTRATION_OCR_MODEL_DIR=/absolute/path/to/PP-OCRv6_medium_rec
bash scripts/run_local_plate.sh rtsp --url 'rtsp://camera-host/stream' --max-frames 60
```

Recognition is fully local: detection, tracking, selected complete crop,
classification, number policy and validation. No API key or external recognition
service is used. `tunis` retains the conservative private-plate checks. The established
Idara, NT and Shorta whole-plate number policies remain enabled. Raw OCR is not displayed as a recognized number.

Set `PLATE_MAX_PENDING` to bound running plus queued recognition tasks (default 4).
Saturated tracks defer without blocking frame processing; finalization drains
eligible work. RTSP stale deferred tracks are recorded as unresolved after the
existing retention interval. Model weights and registration OCR must be installed
locally before startup. See `docs/license_plate_local_completion.md` for exact
policy coverage, offline setup, evidence and test limitations.

## Plate detector (t-640 ONNX)

The active plate detector is `yolo-v9-t-640-license-plate-end2end` via
`open-image-models==0.6.0`. The separate Tunis marker YOLO is unchanged.
Run `bash scripts/run_local_plate.sh preflight` from repository root to verify
local weights, providers, and a real blank-frame inference without Python socket access.
Weights default to `~/.cache/open-image-models/yolo-v9-t-640-license-plate-end2end/yolo-v9-t-640-license-plates-end2end.onnx`;
`PLATE_DETECTOR_PATH` may select another copy with the same pinned SHA-256.
Missing assets fail; only explicit setup downloads them.

`PLATE_CONF` defaults to 0.25 (initial compatibility setting, also the library
default; not calibrated here). The graph embeds score floor 0.001 and NMS IoU
0.45; application dedup remains `PLATE_DEDUP_IOU=0.8`. No new crop padding.
`DETECT_IMGSZ`/`--imgsz` may only be 640; arbitrary Ultralytics spatial sizes
are rejected. The library letterboxes BGR input and reverses coordinates once.

Default `PLATE_DETECTOR_PROVIDERS=CPUExecutionProvider`. To explicitly request
GPU use `CUDAExecutionProvider`; unavailable providers or session fallback fail
unless `PLATE_DETECTOR_ALLOW_CPU_FALLBACK=1`. Actual and requested providers are
reported in detector diagnostics. Default intra/inter-op thread counts are 2/1
via `PLATE_DETECTOR_INTRA_OP_THREADS`/`PLATE_DETECTOR_INTER_OP_THREADS`.
A Conda environment name is not evidence of GPU execution. See the new
`docs/detector_t640_integration.md` audit for setup, verification and rollback.

Number-policy coverage is documented in `docs/whole_plate_number_policies.md`;
its enabled Idara/NT/Shorta formats remain unchanged. Detector replacement does not change those policies.
