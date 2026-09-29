from __future__ import annotations

import logging
import re
import statistics
import threading

import numpy as np

import plate_recognition.config as config

log = logging.getLogger(__name__)

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_digits(text: str) -> str:
    """Convert Arabic-Indic digits while leaving Western digits unchanged."""
    return text.translate(_ARABIC_INDIC_DIGITS)


class OCRUnavailableError(RuntimeError):
    """The shared OCR engine could not be initialized."""


class PlateReader:
    """Thin wrapper around PaddleOCR (PP-OCRv6, transformers engine).
    read(crop) -> (digits_text, mean_confidence).
    """

    def __init__(self, rec_model: str = config.OCR_REC_MODEL):
        self.rec_model = rec_model
        try:
            from paddleocr import TextRecognition
            self.engine = TextRecognition(model_name=rec_model, engine="transformers")
            log.info("PaddleOCR TextRecognition (%s, transformers engine) loaded.", rec_model)
            return
        except Exception as exc:
            log.warning("transformers TextRecognition unavailable (%s); falling back to pipeline w/o oneDNN", exc)
        from paddleocr import PaddleOCR
        self.engine = PaddleOCR(
            text_recognition_model_name=rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )
        log.info("PaddleOCR pipeline (%s, oneDNN disabled) loaded.", rec_model)

    def _parse(self, out) -> tuple[str, str, float]:
        texts, scores = [], []
        for r in out or []:
            d = r if isinstance(r, dict) else getattr(r, "json", None) or {}
            if isinstance(d, dict) and "res" in d and isinstance(d["res"], dict):
                d = d["res"]
            if isinstance(d, dict) and "rec_texts" in d:
                texts += list(d.get("rec_texts", []))
                scores += list(d.get("rec_scores", []))
            elif isinstance(d, dict) and ("rec_text" in d or "text" in d):
                texts.append(d.get("rec_text", d.get("text", "")))
                scores.append(float(d.get("rec_score", d.get("score", 0.0)) or 0.0))
            elif isinstance(r, (list, tuple)):
                for line in r:
                    try:
                        texts.append(line[1][0])
                        scores.append(float(line[1][1]))
                    except Exception:
                        pass
        raw = " ".join(t.strip() for t in texts if t and t.strip())
        # Keep only alphanumeric characters (digits + latin letters)
        normalized = re.sub(r"[^0-9A-Za-z]", "", normalize_digits(raw))
        conf = round(statistics.mean(scores), 4) if scores else 0.0
        return raw, normalized, conf

    def read_with_raw(self, crop: np.ndarray) -> tuple[str, str, float]:
        if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
            return "", "", 0.0
        try:
            if hasattr(self.engine, "predict"):
                return self._parse(self.engine.predict(crop))
            return self._parse(self.engine.ocr(crop))
        except Exception as exc:
            log.warning("OCR error: %s", exc)
            return "", "", 0.0

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """Backward-compatible normalized OCR result used by live_feed."""
        _, normalized, confidence = self.read_with_raw(crop)
        return normalized, confidence


_reader: "PlateReader | None" = None
_reader_error: "Exception | None" = None
_reader_lock = threading.Lock()


def get_reader() -> PlateReader:
    global _reader, _reader_error
    if _reader is not None:
        return _reader
    if _reader_error is not None:
        raise OCRUnavailableError(str(_reader_error)) from _reader_error
    with _reader_lock:
        if _reader is None and _reader_error is None:
            try:
                _reader = PlateReader(config.OCR_REC_MODEL)
            except Exception as exc:
                _reader_error = exc
                log.error("OCR initialization failed: %s", exc)
        if _reader is None:
            assert _reader_error is not None
            raise OCRUnavailableError(str(_reader_error)) from _reader_error
    return _reader


def is_loaded() -> bool:
    return _reader is not None
