"""In-process MTMD adapter for the audited JamePeng binding (no server)."""
import ctypes
import hashlib
from pathlib import Path

BINDING_REVISION = "ec56af26d9c224e10983ef8b5f40ee34e941ec11"
BINDING_SOURCES = {
    "llama.py": "eb3b7c7a7c3fd1ae3c2177ae8558d4285807536a8629ad1991a8aa142af2c805",
    "llama_multimodal.py": "7cb3d4cabfdef8d7031bcea14a1b310c5c712b60f6a58ca44ecd0a3adadf6401",
}


class InferenceError(RuntimeError):
    def __init__(self, message, raw_output=""):
        super().__init__(message)
        self.raw_output = str(raw_output)[:2048]


class ContextBudgetError(InferenceError):
    pass


class TruncatedOutputError(InferenceError):
    pass


def _require_cuda(device):
    if device != "cuda:0":
        raise RuntimeError("Recognition requires cuda:0; select the GPU with CUDA_VISIBLE_DEVICES")
    try:
        driver = ctypes.CDLL("libcuda.so.1")
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuInit.restype = ctypes.c_int
        status = driver.cuInit(0)
        count = ctypes.c_int()
        driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        driver.cuDeviceGetCount.restype = ctypes.c_int
        if status or driver.cuDeviceGetCount(ctypes.byref(count)) or count.value != 1:
            raise RuntimeError(f"CUDA recognition requires one visible GPU (cuInit={status}, devices={count.value})")
    except OSError as exc:
        raise RuntimeError("CUDA driver unavailable; CPU inference is not enabled") from exc


def _load_binding():
    try:
        import llama_cpp
        from llama_cpp.llama_multimodal import LFM25VLChatHandler
    except ImportError as exc:
        raise RuntimeError(f"Install the CUDA JamePeng llama-cpp-python wheel at {BINDING_REVISION}; see LLAMA_CPP_MIGRATION.md") from exc
    directory = Path(llama_cpp.__file__).resolve().parent
    if llama_cpp.__version__ != "0.4.6" or any(
        hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest
        for name, digest in BINDING_SOURCES.items()
    ):
        raise RuntimeError(f"Unverified llama-cpp-python build; expected JamePeng revision {BINDING_REVISION}")
    if not llama_cpp.llama_supports_gpu_offload():
        raise RuntimeError("CUDA offload is unavailable; check GPU access and the CUDA-enabled binding")
    return llama_cpp.Llama, LFM25VLChatHandler


class LlamaBackend:
    """Job-owned native resources. ManufacturerService serializes every call."""
    def __init__(self, config, model_path, projector_path):
        self.model = self.handler = None
        _require_cuda(config.device)
        model_type, handler_type = _load_binding()

        class VehicleHandler(handler_type):
            image_bytes = None
            image_uri = None
            output_tokens = config.max_new_tokens

            def load_media(self, media_url, media_type):
                # Bypass the binding's JPEG recompression and remote URL loader.
                if media_type != "image" or self.image_bytes is None or media_url != self.image_uri:
                    raise InferenceError("Expected the current in-memory vehicle image")
                return self.image_bytes

            def _process_mtmd_prompt(self, *args, **kwargs):
                result = super()._process_mtmd_prompt(*args, **kwargs)
                tokens, spans, chunks, bitmaps = result
                try:
                    budget = min(config.context_size, kwargs["llama"].n_ctx_seq())
                    required = max(len(tokens), self._mtmd_cpp.mtmd_helper_get_n_tokens(chunks))
                    if required + self.output_tokens > budget:
                        raise ContextBudgetError(f"Insufficient context: {required} prompt tokens + {self.output_tokens} output > {budget}")
                    if not any(self._is_image_chunk(span[3]) for span in spans):
                        raise InferenceError("No image reached the native vision encoder")
                except BaseException:
                    self._free_mtmd_resources(chunks, bitmaps)
                    raise
                return result

        try:
            self.handler = VehicleHandler(mmproj_path=str(projector_path), use_gpu=True, verbose=True)
            # Retain partially initialized model resources for explicit cleanup.
            self.model = model_type.__new__(model_type)
            self.model.__init__(
                model_path=str(model_path), n_gpu_layers="all", n_ctx=config.context_size,
                n_batch=512, n_ubatch=128, n_seq_max=1, ctx_checkpoints=0,
                seed=1, verbose=True,
            )
            self.model.chat_handler = self.handler
            if self.model.metadata.get("general.architecture") != "lfm2" or not self.model.is_hybrid:
                raise RuntimeError("Expected the LFM2 hybrid architecture")
            self.handler._init_mtmd_context(self.model)
            if not self.handler.is_support_vision:
                raise RuntimeError("The matching projector does not support vision")
        except BaseException:
            self.close()
            raise

    def generate(self, image_bytes, prompt, *, temperature, top_p, max_tokens):
        import base64
        if self.model is None:
            raise InferenceError("Recognizer is closed")
        try:
            self.model.reset()
            self.handler._media_cache_ids.clear()
            self.handler._media_cache_keys.clear()
            self.handler.image_bytes = image_bytes
            self.handler.image_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
            self.handler.output_tokens = max_tokens
            return self.model.create_chat_completion(messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": self.handler.image_uri}},
                {"type": "text", "text": prompt},
            ]}], max_tokens=max_tokens, temperature=temperature, top_p=top_p, seed=1, stream=False)
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(f"Native VLM inference failed: {exc}") from exc
        finally:
            self.handler.image_bytes = self.handler.image_uri = None
            self.handler._media_cache_ids.clear()
            self.handler._media_cache_keys.clear()
            self.model.reset()

    def close(self):
        handler, model = self.handler, self.model
        self.handler = self.model = None
        try:
            if handler is not None:
                handler.image_bytes = handler.image_uri = None
                handler.close()
        finally:
            if model is not None:
                model.chat_handler = None
                model.close()
