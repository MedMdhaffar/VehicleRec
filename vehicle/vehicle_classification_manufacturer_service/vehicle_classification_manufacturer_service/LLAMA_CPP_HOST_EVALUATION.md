# Isolated evaluation on the Linux GPU host

The approved JamePeng binding at `ec56af26d9c224e10983ef8b5f40ee34e941ec11`
passed the host CUDA smoke test in the supplied evidence archive. The direct
adapter is now implemented. The original source/build commands below are retained
for reproducibility; do not repeat a completed checkout/build just to run the
new adapter test. Use the final section for integration checks. No binding was
installed into `cuda-dev`; use the separate runtime documented in the migration
record. Do not use the old b12236 libraries or launch an inference server.

Run the numbered sections in order in Bash on the GPU host. A failed source
check or build is a blocker; do not substitute a newer revision or generic vision
handler. The code examples below target the inspected current-main interface;
the pinned-source audit and review must confirm that interface before installing.

## 1. Fetch the exact source and inspect it before installation

This uses a fresh directory outside the Git repository. It fails if that checkout
already exists rather than resetting or overwriting any work.

```bash
set -euo pipefail
EVAL_ROOT=/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2
REV=ec56af26d9c224e10983ef8b5f40ee34e941ec11
mkdir -p "$EVAL_ROOT/evidence"
git clone --no-checkout https://github.com/JamePeng/llama-cpp-python.git "$EVAL_ROOT/src"
git -C "$EVAL_ROOT/src" checkout --detach "$REV"
test "$(git -C "$EVAL_ROOT/src" rev-parse HEAD)" = "$REV"
git -C "$EVAL_ROOT/src" submodule update --init --recursive
git -C "$EVAL_ROOT/src" submodule status --recursive | tee "$EVAL_ROOT/evidence/submodules.txt"
git -C "$EVAL_ROOT/src/vendor/llama.cpp" rev-parse HEAD | tee "$EVAL_ROOT/evidence/native-revision.txt"
cat "$EVAL_ROOT/src/vendor/llama.cpp/LICENSE" | tee "$EVAL_ROOT/evidence/native-licence.txt"
git -C "$EVAL_ROOT/src" status --porcelain
python3 - "$EVAL_ROOT/src" <<'PY' | tee "$EVAL_ROOT/evidence/source-audit.txt"
import ast
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
licence = (root / "LICENSE.md").read_text()
if not licence.startswith("MIT License"):
    raise SystemExit("Unexpected binding licence: stop before installation")
print(licence)
requirements = {
    "llama_cpp/llama_multimodal.py": {
        "LFM25VLChatHandler": ("__init__", "__call__"),
        "MTMDChatHandler": ("__init__", "__call__", "_process_mtmd_prompt"),
        "MTMDBaseHandler": ("__init__", "_init_mtmd_context", "_is_image_chunk", "_free_mtmd_resources", "load_media", "_load_image", "_create_bitmap_from_bytes", "close"),
    },
    "llama_cpp/llama.py": {"Llama": ("__init__", "reset", "close", "create_chat_completion")},
}
for relative, classes in requirements.items():
    path = root / relative
    source = path.read_text()
    tree = ast.parse(source)
    print(relative, "sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    for name, methods in classes.items():
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
        print("CLASS", name, "BASES", [ast.unparse(base) for base in cls.bases])
        for method in methods:
            node = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
            print(ast.get_source_segment(source, node))
PY
```

Read `source-audit.txt`, `src/LICENSE.md`, `src/CMakeLists.txt`, and the native
submodule's licence before proceeding. The AST check prints source without
importing or installing the fork; it checks method presence, not semantic safety.
If a method moved into a different base class, inspect its actual owner and adapt
the example only after verifying its source; do not bypass the check blindly.

Confirm the LFM2.5-specific template, `mmproj_path` / `use_gpu` arguments,
`_process_mtmd_prompt` return/ownership contract used below, the `load_media`
override contract, native vision
evaluation, full hybrid-state reset, and explicit resource cleanup. Check that
`Llama.__init__` supports `n_seq_max`, `ctx_checkpoints`, `n_batch` and `n_ubatch`.
Record the complete native SHA, not just release-note abbreviation `18ab62a8e`.
Check native source for the `lfm2` hybrid architecture and the `lfm2` projector.
The high-level handler alone does not prove that these exact BF16 files load.

The inspected current-main `_load_image` re-encodes inputs as JPEG. The evaluation
handler below overrides `load_media` to return only the original, known PNG bytes,
bypassing that conversion and all remote URL loading. Verify this hook at the pin.

## 2. Build only in a separate environment

This proposes a complete, consistent Conda CUDA 13.2 toolkit plus GCC 14. It
avoids the previous mixture of pip CUDA headers and Conda nvcc. Package solving,
compiler compatibility and driver compatibility still need host verification.
CUDA architecture `80` matches the previous build log; change it only if the
actual evaluation GPU differs. No driver or global toolkit changes are needed.

```bash
conda create -y -p "$EVAL_ROOT/env" -c conda-forge -c nvidia \
  python=3.11 pip cmake ninja openssl gcc_linux-64=14 gxx_linux-64=14 cuda-toolkit=13.2
conda run --no-capture-output -p "$EVAL_ROOT/env" nvcc --version | tee "$EVAL_ROOT/evidence/nvcc.txt"
conda run --no-capture-output -p "$EVAL_ROOT/env" x86_64-conda-linux-gnu-c++ --version | tee "$EVAL_ROOT/evidence/compiler.txt"
conda run --no-capture-output -p "$EVAL_ROOT/env" cmake --version | tee "$EVAL_ROOT/evidence/cmake.txt"
nvidia-smi | tee "$EVAL_ROOT/evidence/driver.txt"
test "$(git -C "$EVAL_ROOT/src" rev-parse HEAD)" = "$REV"
test -z "$(git -C "$EVAL_ROOT/src" status --porcelain --untracked-files=no)"
conda run --no-capture-output -p "$EVAL_ROOT/env" env \
  LD_LIBRARY_PATH="$EVAL_ROOT/env/lib" \
  CMAKE_BUILD_PARALLEL_LEVEL=2 \
  CMAKE_ARGS="-DGGML_CUDA=ON -DLLAMA_BUILD=ON -DMTMD_BUILD=ON -DCMAKE_CUDA_ARCHITECTURES=80 -DCUDAToolkit_ROOT=$EVAL_ROOT/env -DCMAKE_CUDA_COMPILER=$EVAL_ROOT/env/bin/nvcc -DCMAKE_C_COMPILER=$EVAL_ROOT/env/bin/x86_64-conda-linux-gnu-cc -DCMAKE_CXX_COMPILER=$EVAL_ROOT/env/bin/x86_64-conda-linux-gnu-c++ -DCMAKE_CUDA_HOST_COMPILER=$EVAL_ROOT/env/bin/x86_64-conda-linux-gnu-c++" \
  python -m pip wheel --no-deps --no-cache-dir --wheel-dir "$EVAL_ROOT/wheels" "$EVAL_ROOT/src" \
  2>&1 | tee "$EVAL_ROOT/evidence/build.log"
sha256sum "$EVAL_ROOT"/wheels/*.whl | tee "$EVAL_ROOT/evidence/wheel-sha256.txt"
conda run --no-capture-output -p "$EVAL_ROOT/env" python -m pip install \
  "$EVAL_ROOT"/wheels/*.whl
conda run --no-capture-output -p "$EVAL_ROOT/env" python -m pip freeze > "$EVAL_ROOT/evidence/pip-freeze.txt"
conda list -p "$EVAL_ROOT/env" --explicit > "$EVAL_ROOT/evidence/conda-explicit.txt"
```

Do not set `LLAMA_CPP_LIB`, `LLAMA_CPP_LIB_PATH`, or other shared-library overrides
to the old native build. Review the build log for CUDA, MTMD, the selected
compiler/toolkit and packaged libraries. A wheel build is not the CUDA image test.

## 3. Direct Python image smoke test

In a second terminal, sample device memory while the smoke test runs; stop with
Ctrl-C after cleanup. The full table includes MIG-instance memory rows when permitted. The earlier
physical-device query returned Insufficient Permissions for every sample. This
monitor is optional; the new adapter test below also uses cudaMemGetInfo from
inside the inference process. Neither method isolates internal allocations.

```bash
nvidia-smi -lms 200 \
  > /home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/evidence/gpu-memory-mig.log
```

Return to the first terminal. The image is passed as its original PNG bytes in
an in-memory data URI. The `image_url` message field is a binding input format;
it makes no HTTP request. This evaluation uses the application's existing prompt
and parser, not its Transformers service. Run sequential requests only.

```bash
conda run --no-capture-output -p "$EVAL_ROOT/env" env \
  LD_LIBRARY_PATH="$EVAL_ROOT/env/lib" CUDA_VISIBLE_DEVICES=0 \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 EVAL_ROOT="$EVAL_ROOT" \
  python -u - <<'PY' 2>&1 | tee "$EVAL_ROOT/evidence/smoke.log"
import base64
import ctypes
import hashlib
import inspect
import json
import os
import sys
import time
from pathlib import Path

evaluation = Path(os.environ["EVAL_ROOT"])
package = Path("/home/intern/part-time/mohamed/VehicleRec/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service")
sys.path.insert(0, str(package))
from manufacturer.gguf_assets import resolve_gguf_assets
from manufacturer.prompts import PROMPT
from manufacturer.schemas import parse_result

if ctypes.CDLL("libcuda.so.1").cuInit(0) != 0:
    raise RuntimeError("CUDA driver initialization failed; CPU fallback forbidden")
import llama_cpp
from llama_cpp import Llama
from llama_cpp.llama_multimodal import LFM25VLChatHandler

if llama_cpp.__version__ != "0.4.6" or not llama_cpp.llama_supports_gpu_offload():
    raise RuntimeError("Expected pinned CUDA-capable binding")
for cls, relative in ((Llama, "llama_cpp/llama.py"), (LFM25VLChatHandler, "llama_cpp/llama_multimodal.py")):
    installed = Path(inspect.getfile(cls))
    expected = evaluation / "src" / relative
    if installed.read_bytes() != expected.read_bytes():
        raise RuntimeError(f"Installed source differs from audited checkout: {installed}")
    print("BINDING_SOURCE", installed, hashlib.sha256(installed.read_bytes()).hexdigest(), flush=True)
print("HANDLER_SIGNATURE", inspect.signature(LFM25VLChatHandler), flush=True)

# Evaluation-only instrumentation of the audited private ownership contract.
# Reject complete-prompt overflow before native prefill can shift visual input.
class CheckedHandler(LFM25VLChatHandler):
    def load_media(self, media_url, media_type):
        if media_type != "image" or media_url != data_uri:
            raise RuntimeError("Only the original in-memory PNG is allowed")
        return image

    def _process_mtmd_prompt(self, *args, **kwargs):
        result = super()._process_mtmd_prompt(*args, **kwargs)
        tokens, spans, chunks, bitmaps = result
        try:
            if len(tokens) + 128 > 4096:
                raise RuntimeError(f"Insufficient context: {len(tokens)} prompt positions + 128 output > 4096")
            images = sum(self._is_image_chunk(span[3]) for span in spans)
            if images < 1:
                raise RuntimeError("No native image chunk: refuse text-only inference")
            print("NATIVE_IMAGE_CHUNKS", images, "PROMPT_POSITIONS", len(tokens), flush=True)
        except BaseException:
            self._free_mtmd_resources(chunks, bitmaps)
            raise
        return result

def event(name, **values):
    print(json.dumps({"event": name, "unix_time": time.time(), "pid": os.getpid(), **values}), flush=True)

model, projector = resolve_gguf_assets()
for name, path in (("model", model), ("projector", projector)):
    with path.open("rb") as stream:
        event("asset", role=name, path=str(path), sha256=hashlib.file_digest(stream, "sha256").hexdigest())
image = Path("/home/intern/part-time/mohamed/vehicles/minivan.png").read_bytes()
if not image.startswith(b"\x89PNG\r\n\x1a\n"):
    raise RuntimeError("Expected original minivan.png")
data_uri = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
llm = None
handler = CheckedHandler(mmproj_path=str(projector), use_gpu=True, verbose=True)
event("before_load")
started = time.perf_counter()
try:
    llm = Llama(model_path=str(model), chat_handler=handler, n_gpu_layers=-1,
                n_ctx=4096, n_batch=512, n_ubatch=128, n_seq_max=1,
                ctx_checkpoints=0, seed=1, verbose=True)
    if llm.metadata.get("general.architecture") != "lfm2" or not llm.is_hybrid:
        raise RuntimeError("Expected the LFM2 hybrid model")
    handler._init_mtmd_context(llm)
    if not handler.is_support_vision:
        raise RuntimeError("Projector does not support vision")
    event("after_model_and_projector_load", seconds=time.perf_counter() - started)
    answers = []
    for index in range(10):
        llm.reset()
        event("inference_start", index=index, native_positions=llm.n_tokens)
        started = time.perf_counter()
        try:
            response = llm.create_chat_completion(messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": PROMPT},
            ]}], max_tokens=128, temperature=0, top_p=1, seed=1, stream=False)
            choice = response["choices"][0]
            event("inference_end", index=index, seconds=time.perf_counter() - started,
                  finish_reason=choice.get("finish_reason"), usage=response.get("usage"),
                  raw_output=str(choice["message"].get("content"))[:2048])
            if choice.get("finish_reason") != "stop":
                raise RuntimeError("Truncated/aborted response: not valid compatibility evidence")
            result = parse_result(choice["message"]["content"])
            event("parsed_result", index=index, result=result.to_dict())
            answers.append(result.to_dict())
        finally:
            llm.reset()
    event("repeated_image_complete", identical_outputs=all(answer == answers[0] for answer in answers))
finally:
    try:
        handler.close()
    finally:
        if llm is not None:
            # This example owns the handler; prevent Llama.close closing it again.
            llm.chat_handler = None
            llm.close()
    event("after_close")
PY
```

## 4. Interpret and preserve evidence

- The log must show actual language-layer CUDA offload, vision/projector CUDA
  allocation, nonzero native image chunks, successful IMAGE chunk evaluation and
  a generated response. A CUDA-enabled build and a plausible label alone do not
  demonstrate CUDA multimodal inference. No exact-asset success is assumed.
- Compare the printed asset hashes with `models/gguf-assets.sha256`. The source
  files must remain intact. No assets are downloaded or resized by this example.
- Correlate event timestamps with `gpu-memory-mig.log`: before load, after both loads,
  during each request and after close. Request 0 is the first-call measurement;
  requests 1-9 are warmed calls. Compare memory across warmed calls without
  claiming an internal allocation breakdown or long-run leak freedom.
- Repeated minivan output is preliminary reset/stability evidence, not proof of
  independent vehicle state or accuracy. After this compatibility gate passes,
  integration is already approved. It still requires A/B/A requests with different
  vehicles, fresh-instance comparisons, failure/cancellation cleanup tests,
  focused fake-backend tests, existing regression tests, and a short real video.
- Append the verified checkout/native SHA, licence audit, compiler/toolkit versions,
  wheel hash, test logs and actual measurements to `LLAMA_CPP_MIGRATION.md`.
  Do not replace the active backend based only on successfully building a wheel
  or this document's unexecuted commands. No additional dependency approval is
  required once the approved revision passes exact-model CUDA verification.

## Integrated adapter and MIG memory checks

The archive already passed the standalone compatibility gate. Run these new
checks against the integrated ManufacturerService without rebuilding the binding.
The script uses the CUDA runtime's cudaMemGetInfo inside the inference process,
which avoids the denied physical-device NVML query. Values describe the visible
MIG instance and can include other processes. The script now prints CPU process
RAM (current RSS and lifetime peak RSS) beside GPU memory at each stage. With
--memory-csv, it also samples both every 200 ms during loading and inference.
Sampled maxima are not exact allocation peaks; CUDA context overhead can remain
after model close, and CPU allocators can retain RAM.

```bash
set -euo pipefail
EVAL_ROOT=/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2
CUDA_VISIBLE_DEVICES=0 LD_LIBRARY_PATH="$EVAL_ROOT/env/lib" \
  "$EVAL_ROOT/env/bin/python" -u \
  /home/intern/part-time/mohamed/vlm-validation/test_llamacpp_direct.py \
  --repeat 10 \
  --memory-csv "$EVAL_ROOT/evidence/integrated-memory.csv" \
  2>&1 | tee "$EVAL_ROOT/evidence/integrated-images.log"
```

This uses minivan.png and sedan.png at original resolution, checks A/B/A and
fresh-instance normalized results, and reports initialization/inference timings,
request-state cleanup, CPU process RSS/peak RSS and CUDA-visible memory.
Terminal output and integrated-images.log contain the stage snapshots. The
combined integrated-memory.csv contains timestamp, event, phase, PID, CPU RSS,
CPU peak RSS, GPU used/free/total MiB, and CPU/GPU error columns. Unavailable
readings are blank in CSV with an error, never fabricated as zero. CPU RSS
includes the whole smoke-test process, not just model weights; peak RSS never
decreases within a process. It is not an accuracy benchmark.

To view samples live from a second terminal:

```bash
tail -f /home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/evidence/integrated-memory.csv
```
A failed cudaMemGetInfo measurement is reported explicitly; never interpret it
as zero usage. The original gpu-memory.csv remains unusable evidence.

For an actual detector/video test, create the separate application runtime.
It inherits the existing Django/YOLO/PyTorch dependencies without modifying their
cuda-dev installation. This additional binding is installed only in the overlay.
Do not recreate/overwrite an existing runtime environment blindly.

```bash
RUNTIME_ROOT=/home/intern/part-time/mohamed/.vehicle-llamacpp-runtime
/home/intern/miniforge3/envs/cuda-dev/bin/python -m venv --system-site-packages "$RUNTIME_ROOT"
"$RUNTIME_ROOT/bin/python" -m pip install "$EVAL_ROOT"/wheels/*.whl
cd /home/intern/part-time/mohamed/VehicleRec/vehicle
CUDA_VISIBLE_DEVICES=0 LD_LIBRARY_PATH="$EVAL_ROOT/env/lib" \
  "$RUNTIME_ROOT/bin/python" -u - <<'PY' 2>&1 | tee "$EVAL_ROOT/evidence/integrated-video.log"
import json
from pathlib import Path
from vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.service import VehicleAnalysisService

sources = sorted(Path('media/vehicle_classification').glob('*/source.mp4'),
                 key=lambda path: path.stat().st_mtime, reverse=True)
if not sources:
    raise FileNotFoundError('No saved source.mp4; supply a local video path')
output = Path('/tmp/vehicle-llamacpp-video-check')
output.mkdir(exist_ok=True)
pipeline = VehicleAnalysisService(enable_roi=False, enable_manufacturer=True)
try:
    result = pipeline.process_video(sources[0], output_path=output / 'annotated.mp4',
                                    json_output=output / 'results.json', max_frames=120)
finally:
    pipeline.close()
print(json.dumps({'source': str(sources[0]), 'output_directory': str(output),
                  'frames_processed': result['frames_processed'],
                  'vehicles': result['vehicles']}, indent=2))
if not result['vehicles']:
    raise RuntimeError('No vehicle track; inspect detection before treating this as a recognition test')
print('Inspect results.json recognition_evidence and annotated.mp4 overlays.')
PY
```

Review the JSON and play /tmp/vehicle-llamacpp-video-check/annotated.mp4. Confirm
manufacturer, vehicle_type, colour and recognition_status; retain raw failed
attempts rather than interpreting unknown values as successful recognition.
These integrated image/video commands have not been run by the agent.
Repackage evidence after running them:

```bash
tar -czf "$EVAL_ROOT/evidence-integrated.tar.gz" -C "$EVAL_ROOT" evidence
```
