"""Complete-plate PaddleOCR recognition with preserved raw output."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .errors import PluginError
from .utils import save_png


OCR_IDENTIFIER = "arabic_PP-OCRv5_mobile_rec"


def unwrap_paddle_result(result: Any) -> tuple[str, float | None, dict[str, Any]]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise PluginError("PaddleOCR result is not a dictionary-like JSON payload.")
    body = payload.get("res", payload)
    if not isinstance(body, dict) or "rec_text" not in body:
        raise PluginError("PaddleOCR result does not contain rec_text.")
    raw_text = body["rec_text"] if body["rec_text"] is not None else ""
    if not isinstance(raw_text, str):
        raise PluginError("PaddleOCR rec_text is not a string.")
    confidence = body.get("rec_score")
    return raw_text, None if confidence is None else float(confidence), payload


class ArabicRecognizer:
    def __init__(self, model_dir: Path, device: str = "cpu"):
        if not model_dir.is_dir():
            raise PluginError(f"PaddleOCR model directory is missing: {model_dir}")
        for name in ("inference.pdiparams", "inference.json", "inference.yml"):
            if not (model_dir / name).is_file():
                raise PluginError(f"PaddleOCR asset is missing: {model_dir / name}")
        try:
            from paddleocr import TextRecognition
        except Exception as exc:
            raise PluginError(f"PaddleOCR import failed: {type(exc).__name__}: {exc}") from exc
        started = time.perf_counter()
        try:
            self.model = TextRecognition(
                model_name=OCR_IDENTIFIER,
                model_dir=str(model_dir.resolve()),
                device=device,
            )
        except Exception as exc:
            raise PluginError(f"Could not load Arabic recognizer: {type(exc).__name__}: {exc}") from exc
        self.load_seconds = time.perf_counter() - started
        self.model_dir = model_dir.resolve()
        self.device = device

    def recognize(
        self, image_bgr: np.ndarray, submitted_path: Path | None = None
    ) -> dict[str, Any]:
        """Use lossless file input for diagnostics, otherwise pass BGR directly.

        Paddle's image reader accepts BGR arrays as well as paths. A private
        contiguous copy keeps the array path independent of caller storage.
        """
        if submitted_path is not None:
            save_png(submitted_path, image_bgr)
        model_input = str(submitted_path) if submitted_path is not None else image_bgr.copy(order="C")
        started = time.perf_counter()
        try:
            outputs = list(self.model.predict(input=model_input, batch_size=1))
            if len(outputs) != 1:
                raise PluginError(f"Expected one OCR result, got {len(outputs)}.")
            raw_text, confidence, payload = unwrap_paddle_result(outputs[0])
            status, failure = "completed", ""
        except Exception as exc:
            raw_text, confidence, payload = "", None, None
            status, failure = "failed", f"{type(exc).__name__}: {exc}"
        return {
            "inference_status": status,
            "failure_reason": failure,
            "raw_text": raw_text,
            "native_confidence": confidence,
            "native_payload": payload,
            "recognition_seconds": time.perf_counter() - started,
            "submitted_path": str(submitted_path.resolve()) if submitted_path is not None else None,
            "submitted_width": int(image_bgr.shape[1]),
            "submitted_height": int(image_bgr.shape[0]),
        }

