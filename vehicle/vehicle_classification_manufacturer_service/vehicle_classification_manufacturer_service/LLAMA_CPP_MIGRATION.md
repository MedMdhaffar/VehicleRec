# Direct llama.cpp vehicle recognition

Status: the approved exact-model CUDA compatibility gate passed on the Linux host.
The active recognition implementation now uses the direct in-process adapter.
The agent session still cannot access CUDA (cuInit returns 100); deployment tests
of the new adapter and a real detector/video remain to be run on the GPU host.
No dependency was installed into or removed from the working cuda-dev environment.

## Pinned dependency and source audit

- MIT fork: https://github.com/JamePeng/llama-cpp-python
- Binding 0.4.6: `ec56af26d9c224e10983ef8b5f40ee34e941ec11`.
- Bundled llama.cpp: `18ab62a8ec5144ae5d2f65e78374ba69652d75dd`.
- Handler: `llama_cpp.llama_multimodal.LFM25VLChatHandler`, initialized with
  `mmproj_path`, `use_gpu=True`. The actual pinned source is now available in
  `.llamacpp-eval-ec56af2/src`; its licence, inherited methods, hybrid reset and
  native resource ownership were inspected before integrating it.
- Wheel: `llama_cpp_python-0.4.6-cp311-cp311-linux_x86_64.whl`.
  SHA-256: `8d535576f31136bf5debaa8a1b9042cfb2cadcce538579ed17ecddcdc7b43629`.
- Successful build reported GCC 14.4.0, nvcc 13.2.86, CMake 4.4.5, Python 3.11,
  and NVIDIA driver 595.99.02. Complete Conda/pip pins and build logs are in the
  supplied archive. The CUDA device was an A100-SXM4-80GB MIG 1g.10gb (9728 MiB).
- Runtime source hashes in manufacturer/llama_backend.py reject other bindings
  sharing the same version/import namespace. The matching native libraries must
  come from that wheel; never redirect it to the old b12236 shared libraries.
- This fork is separately maintained and uses private MTMD hooks for exact context
  accounting and lossless image delivery. Upgrades require source/ABI/lifecycle
  review and repeat exact-model CUDA tests; the version number alone is insufficient.

## Verified host evidence

Source: `/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/evidence.tar.gz`.
This archive was read directly without extracting over the checkout.

- Both asset hashes matched the original preparation manifest.
- Language model: BF16, LFM2 hybrid architecture; 17/17 layers offloaded to GPU.
- Vision encoder: `CLIP using CUDA0 backend`; vision support detected.
- Each minivan request produced 9 native image chunks (2290 visual embedding
  tokens), a 2653-position prompt and 21 output tokens. Full image+prompt+128-token
  allowance fit the 4096 context. Image slices were actually encoded and decoded.
- All 10 requests completed with finish_reason=stop; every request started at
  native position 0. All predicted Mazda/minivan/red, normalized to mazda/van/red.
  This is compatibility evidence, not ground-truth accuracy or A/B/A state proof.
- Model+projector initialization: 0.476354734 seconds (excludes earlier hashing and
  Python imports). First inference: 0.992870023 seconds. Requests 1–9:
  mean 0.734659333 seconds, range 0.728455777–0.741394872 seconds.
- after_close was recorded. No inference exception appeared in the smoke log.
- Every one of 1190 gpu-memory.csv samples was `Insufficient Permissions`.
  No before/after/peak memory or memory-stability claim can be made from that CSV.
  Native buffer logs are not a substitute for total process/device memory readings.

The MIG instance allowed CUDA computation while the physical-device NVML query
was denied. NVIDIA documents privileged aggregate queries versus per-MIG-handle
memory queries: https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html .
Use a MIG-aware query/monitor; do not change GPU permissions or infer a memory
number from this error. The full nvidia-smi MIG table was readable in driver.txt.

## Assets and configuration

Copied, not moved, into this package's models/ directory; source files in
vlm-validation/ remain intact. Filenames and BF16 quantization are unchanged.
Both binaries are Git-ignored. See models/gguf-assets.sha256:

- LFM2.5-VL-450M-BF16.gguf:
  `f780a432c7ed103ae35d3f20e76c35acd65777f6cc04fbe646f83efa42989b22`
- mmproj-LFM2.5-VL-450m-BF16.gguf:
  `899e337967b74c825afeb2f8b441fadd9b3078ec1ec680fc3b3cadbee2af231d`

Defaults resolve relative to the package. Override individual files with
MANUFACTURER_MODEL_PATH / MANUFACTURER_PROJECTOR_PATH. CUDA_VISIBLE_DEVICES must
expose one GPU/MIG instance; MANUFACTURER_DEVICE is cuda:0 in that namespace.
MANUFACTURER_CONTEXT_SIZE defaults to 4096. Existing temperature, top_p, token
limit, attempt/crop budgets and enabled controls are preserved. There is no
Transformers fallback, model server, HTTP client or per-crop model load.

The recognizer converts BGR to RGB once and sends a lossless in-memory PNG through
the real LFM2.5 MTMD handler. Its default JPEG loader is bypassed. Complete text
and visual-token budgets are checked before prefill; overflow is an explicit
ContextBudgetError, not silent truncation/context shifting. Model-output length
exhaustion, aborts, malformed JSON and native failures have distinct failures with
bounded raw diagnostics. Existing aliases, unknown semantics, attempt aggregation,
API results and annotations are preserved; no confidence is generated.

ManufacturerService serializes requests and close. The adapter disables native
checkpoint reuse, resets hybrid state before/after requests, clears media IDs and
buffers, and lets the pinned binding free request chunks/bitmaps. It explicitly
closes projector before model and retains partial model construction for cleanup.
VehicleAnalysisService also closes it if detector/pipeline startup fails. The
existing Django-process GPU lock remains; other workers/processes can each own
and allocate a separate model instance.

## Installation and deployment

The completed isolated build/source audit and direct binding image test are
reproducible using [LLAMA_CPP_HOST_EVALUATION.md](LLAMA_CPP_HOST_EVALUATION.md).
requirements-llamacpp.txt (in the parent Django app) records the immutable source
pin. Building from it needs GGML_CUDA=ON and MTMD_BUILD=ON with a consistent toolkit;
use the isolated recipe, not the old mixed pip-header/Conda-nvcc setup.

For deploying without altering cuda-dev, create a separate runtime overlay that
inherits its Django/YOLO/PyTorch packages and installs the tested binding locally:

```bash
EVAL_ROOT=/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2
RUNTIME_ROOT=/home/intern/part-time/mohamed/.vehicle-llamacpp-runtime
/home/intern/miniforge3/envs/cuda-dev/bin/python -m venv --system-site-packages "$RUNTIME_ROOT"
"$RUNTIME_ROOT/bin/python" -m pip install "$EVAL_ROOT"/wheels/*.whl
cd /home/intern/part-time/mohamed/VehicleRec/vehicle
CUDA_VISIBLE_DEVICES=0 LD_LIBRARY_PATH="$EVAL_ROOT/env/lib" \
  "$RUNTIME_ROOT/bin/python" manage.py runserver 8002
```

These deployment commands have not been run by the agent. The overlay avoids
installing the fork into cuda-dev; GPU compatibility of combined YOLO/PyTorch and
llama.cpp still needs a real video test. Inference works offline once all Python
and native dependencies plus both GGUF assets are installed. No downloads occur
in the recognizer. Do not use old HF-directory overrides as GGUF file paths.

## Integration verification and remaining host checks

54 focused tests passed after the backend change: the new fake-native adapter
suite, existing LocalRecognitionTests, GGUF asset tests and two upload API tests.
Tests cover lossless BGR/RGB conversion, image/prompt delivery, one initialization,
context budget rejection, strict output/truncation handling, independent unknown
fields, serial calls, reset/cache cleanup, startup/inference/cancellation cleanup,
job cleanup on detector failure, a synthetic video through the actual adapter into saved API results and overlays, and no inference
network or subprocess calls. The obsolete Transformers inference test was replaced
by the new adapter suite. Existing taxonomy/colour tests remain unchanged.

The unrelated stale health-route test is excluded; it expects a route removed
before this migration. No full-project test-suite success is claimed.

Still unexecuted on CUDA for the integrated adapter: A/B/A with different vehicle
images compared with fresh instances; repeated-request memory measurements using
MIG-accessible telemetry; cleanup/failure observations with real native resources;
and a short real YOLO/video run verifying saved results and overlays. The new
vlm-validation/test_llamacpp_direct.py exercises the integrated adapter with
A/B/A, fresh-instance checks, CPU process RSS/peak RSS and cudaMemGetInfo
snapshots. Its --memory-csv option saves combined CPU/GPU samples every 200 ms
with explicit error columns for unavailable readings. Copyable host commands
for both integrated images and the available saved video are in the final section
of LLAMA_CPP_HOST_EVALUATION.md and workspace LLAMA_CPP_GPU_TEST_COMMANDS.md. The archive
establishes the binding/asset compatibility gate, not these deployment checks.

Memory-monitor follow-up: five CPU-only tests passed for RSS/MiB conversion,
combined CSV/terminal fields, periodic sampling, unavailable CUDA readings and
sampler/file cleanup on failure. Live CPU/GPU telemetry during actual CUDA
inference remains a host check; previous application tests were not repeated.
