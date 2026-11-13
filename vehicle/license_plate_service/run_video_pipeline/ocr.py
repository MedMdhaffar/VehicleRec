from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from typing import Iterable
from pathlib import Path

import numpy as np

import run_video_pipeline.config as config

log = logging.getLogger(__name__)


from run_video_pipeline.text_policy import OCRResult, normalize_plate_text, digits_only


class PlateReader:
    """Thin wrapper around native PaddleOCR PP-OCRv6 text recognition.
    read(crop) -> (digits_text, mean_confidence).
    """

    def __init__(self, rec_model: str = config.OCR_REC_MODEL, *, model_dir: str | None = None, device: str | None = None):
        self.rec_model = rec_model
        from paddleocr import TextRecognition

        options = {}
        if model_dir is not None:
            local = Path(model_dir)
            if not all((local / name).is_file() for name in ('inference.pdiparams', 'inference.json', 'inference.yml')):
                raise FileNotFoundError('explicit OCR directory lacks required inference artifacts')
            options['model_dir'] = str(local.resolve())
        if device is not None:
            options['device'] = device
        self.engine = TextRecognition(model_name=rec_model, **options)
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
        raw = " ".join(t.strip() for t in texts if t and t.strip())
        normalized = normalize_plate_text(raw)
        conf = round(statistics.mean(scores), 4) if scores else 0.0
        return OCRResult(raw_text=raw, normalized_text=normalized, confidence=conf)

    def read_result(self, crop: np.ndarray) -> OCRResult:
        if crop is None or crop.size == 0:
            return OCRResult("", "", 0.0)
        try:
            if hasattr(self.engine, "predict"):
                return self._parse(self.engine.predict(crop))
            return self._parse(self.engine.ocr(crop))
        except Exception as exc:
            log.warning("OCR error: %s", exc)
            return OCRResult("", "", 0.0)

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
