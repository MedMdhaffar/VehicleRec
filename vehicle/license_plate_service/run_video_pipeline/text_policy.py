"""Pure scalar OCR comparison forms; native decoding stays in ocr.py."""
import re
from dataclasses import dataclass


def normalize_plate_text(text: str) -> str:
    """Canonical OCR comparison form while preserving the raw text separately."""
    return re.sub(r"[^0-9A-Za-z]", "", text or "").upper()


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


@dataclass(frozen=True)
class OCRResult:
    raw_text: str
    normalized_text: str
    confidence: float

