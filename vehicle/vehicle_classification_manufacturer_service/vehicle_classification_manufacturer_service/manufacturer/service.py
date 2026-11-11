"""Offline llama.cpp VLM inference, loaded once per video analysis job."""
import io
import logging
import threading
import time
import numpy as np
from .config import MODEL_ID, RecognitionConfig
from .gguf_assets import resolve_gguf_assets
from .llama_backend import LlamaBackend, InferenceError, TruncatedOutputError
from .schemas import ModelOutputError, parse_result
from .prompts import PROMPT

log = logging.getLogger(__name__)


class ManufacturerService:
    def __init__(self, config=None):
        self.config = config or RecognitionConfig.from_env()
        self._lock = threading.RLock()
        self._backend = None
        self.prompt = PROMPT
        started = time.perf_counter()
        model, projector = resolve_gguf_assets(self.config.model_path, self.config.projector_path)
        log.info("Loading %s with in-process llama.cpp on %s", MODEL_ID, self.config.device)
        self._backend = LlamaBackend(self.config, model, projector)
        log.info("Local VLM model and projector ready in %.2fs", time.perf_counter() - started)

    def predict(self, crop, *, retry_invalid=False):
        with self._lock:
            if self._backend is None:
                raise InferenceError("Recognizer is closed")
            if not isinstance(crop, np.ndarray) or crop.dtype != np.uint8 or crop.ndim != 3 or crop.shape[2] != 3 or min(crop.shape[:2]) < 1:
                raise ValueError("invalid_crop")
            from PIL import Image
            # OpenCV BGR -> RGB once; PNG transport preserves these pixels.
            with Image.fromarray(crop[:, :, ::-1]) as image, io.BytesIO() as buffer:
                if self.config.max_crop_side:
                    image.thumbnail((self.config.max_crop_side, self.config.max_crop_side))
                image.save(buffer, format="PNG")
                image_bytes = buffer.getvalue()
            prompt = self.prompt
            if retry_invalid:
                prompt += (
                    ' Formatting correction: output exactly one JSON object. '
                    'All three values must be strings. Do not nest objects, repeat the object, '
                    'use markdown, or append explanations. Required format: '
                    '{"manufacturer":"unknown","vehicle_type":"unknown","color":"unknown"}.'
                )
            response = self._backend.generate(image_bytes, prompt, temperature=self.config.temperature,
                top_p=self.config.top_p, max_tokens=self.config.max_new_tokens)
            try:
                choice = response["choices"][0]
                text = choice["message"]["content"]
                finish = choice["finish_reason"]
                if not isinstance(text, str):
                    raise TypeError()
            except (KeyError, TypeError, IndexError):
                raise ModelOutputError(str(response)[:2048]) from None
            if finish == "length":
                raise TruncatedOutputError("Output token limit exhausted", text)
            if finish != "stop":
                raise InferenceError(f"Generation did not complete: {finish}", text)
            return parse_result(text)

    def close(self):
        with self._lock:
            backend, self._backend = self._backend, None
            if backend is not None:
                backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
