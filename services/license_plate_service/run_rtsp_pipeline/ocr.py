from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from pathlib import Path
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np

import run_video_pipeline.config as config

log = logging.getLogger(__name__)


def normalize_digits(text: str) -> str:
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in (text or ""))


def normalize_plate_text(text: str) -> str:
    """Preserve category words, digits and uncertain letters; normalize separators only."""
    return re.sub(r"\s+", " ", normalize_digits(unicodedata.normalize("NFKC", text or ""))).strip().upper()


def digits_only(text: str) -> str:
    return "".join(c for c in normalize_digits(text) if c in "0123456789")


@dataclass(frozen=True)
class OCRResult:
    raw_text: str
    normalized_text: str
    confidence: float
    native_confidence: float | None = None


class PlateReader:
    """Thin wrapper around native PaddleOCR PP-OCRv6 text recognition.
    read(crop) -> (digits_text, mean_confidence).
    """

    def __init__(self, rec_model: str = config.OCR_REC_MODEL):
        self.rec_model = rec_model
        configured_dir = os.environ.get("REGISTRATION_OCR_MODEL_DIR", "")
        model_dir = Path(configured_dir).expanduser()
        if not configured_dir or not model_dir.is_absolute():
            raise RuntimeError("REGISTRATION_OCR_MODEL_DIR must be an absolute path to installed local PP-OCRv6_medium_rec assets")
        required = ("inference.json", "inference.pdiparams", "inference.yml")
        if not all((model_dir / name).is_file() for name in required):
            raise RuntimeError("Set REGISTRATION_OCR_MODEL_DIR to the installed local PP-OCRv6_medium_rec assets; runtime downloads are forbidden")
        import yaml
        identity = yaml.safe_load((model_dir / "inference.yml").read_text(encoding="utf-8"))
        if identity.get("Global", {}).get("model_name") != rec_model:
            raise RuntimeError(f"Registration OCR assets do not identify {rec_model}")
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import TextRecognition
        self.engine = TextRecognition(model_name=rec_model, model_dir=str(model_dir.resolve()),
                                      device=os.environ.get("PLATE_OCR_DEVICE", "cpu"))
        log.info("Native PaddleOCR TextRecognition (%s) loaded.", rec_model)

    def _parse(self, out) -> OCRResult:
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
        raw = " ".join(t for t in texts if t)
        normalized = normalize_plate_text(raw)
        native_confidence = statistics.mean(scores) if scores else 0.0
        conf = round(native_confidence, 4)
        return OCRResult(raw_text=raw, normalized_text=normalized, confidence=conf, native_confidence=native_confidence)

    def read_result(self, crop: np.ndarray) -> OCRResult:
        if crop is None or crop.size == 0:
            return OCRResult("", "", 0.0)
        try:
            if hasattr(self.engine, "predict"):
                return self._parse(self.engine.predict(crop))
            return self._parse(self.engine.ocr(crop))
        except Exception as exc:
            raise RuntimeError(f"Registration OCR failed: {exc}") from exc

    def read_batch(self, crops: Iterable[np.ndarray], batch_size: int = 16) -> list[OCRResult]:
        """Recognize tight plate crops in batches, preserving input order."""
        crop_list = list(crops)
        if not crop_list:
            return []
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        results: list[OCRResult] = []
        for start in range(0, len(crop_list), batch_size):
            batch = crop_list[start:start + batch_size]
            if not hasattr(self.engine, "predict"):
                results.extend(self.read_result(crop) for crop in batch)
                continue
            try:
                outputs = list(self.engine.predict(batch, batch_size=len(batch)))
                if len(outputs) != len(batch):
                    raise RuntimeError(
                        f"OCR returned {len(outputs)} results for a batch of {len(batch)}"
                    )
                results.extend(self._parse([output]) for output in outputs)
            except Exception as exc:
                log.warning("Batched OCR failed (%s); retrying crops individually", exc)
                results.extend(self.read_result(crop) for crop in batch)
        return results

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """Backward-compatible normalized text/confidence API."""
        result = self.read_result(crop)
        return result.normalized_text, result.confidence


_reader: "PlateReader | None" = None


def get_reader() -> PlateReader:
    global _reader
    if _reader is None:
        _reader = PlateReader(config.OCR_REC_MODEL)
    return _reader
