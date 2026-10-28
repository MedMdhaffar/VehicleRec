"""Fixed policies and helpers for the manual-marker Arabic OCR experiment."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np


EXPECTED_MARKINGS = {
    "4": "ن ت",
    "5": "ن ت",
    "6": "شرطة",
    "7": "شرطة",
    "8": "تونس",
    "9": "تونس",
    "10": "تونس",
}

CATEGORY_BY_NORMALIZED_MARKING = {
    "نت": "nt",
    "شرطة": "shorta",
    "تونس": "tunis",
}


class ArabicOCRExperimentError(RuntimeError):
    """Raised for invalid experiment inputs or recognizer output."""


def normalize_arabic_marker(text: str) -> str:
    """Apply the label-independent normalization policy fixed for this experiment."""
    normalized = unicodedata.normalize("NFKC", text)
    output: list[str] = []
    for character in normalized:
        if character == "\u0640":  # Arabic tatweel
            continue
        if character.isspace():
            continue
        if unicodedata.category(character) == "Mn":  # Arabic diacritics/marks
            continue
        output.append(character)
    return "".join(output)


def category_from_normalized_marker(text: str) -> str:
    """Map exact normalized category words only; never repair or fuzzy-match."""
    return CATEGORY_BY_NORMALIZED_MARKING.get(text, "unknown")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_marker_crops(
    records: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    """Extract xyxy markers using exclusive x2/y2 coordinates."""
    selected: list[dict[str, Any]] = []
    crops: list[np.ndarray] = []
    for record in records:
        marker = record.get("marker_bbox")
        if marker is None:
            continue
        image_id = str(record["id"])
        if image_id not in EXPECTED_MARKINGS:
            raise ArabicOCRExperimentError(f"Unexpected marker-eligible image: {image_id}")
        if len(marker) != 4 or any(not isinstance(value, int) for value in marker):
            raise ArabicOCRExperimentError(f"Image {image_id}: invalid marker bbox {marker!r}")
        x1, y1, x2, y2 = marker
        height, width = record["image"].shape[:2]
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ArabicOCRExperimentError(f"Image {image_id}: out-of-bounds marker bbox {marker!r}")
        crop = record["image"][y1:y2, x1:x2].copy()
        if crop.shape[:2] != (y2 - y1, x2 - x1):
            raise ArabicOCRExperimentError(f"Image {image_id}: crop dimensions do not match bbox")
        selected.append(record)
        crops.append(crop)
    ids = [str(record["id"]) for record in selected]
    if ids != ["4", "5", "6", "7", "8", "9", "10"]:
        raise ArabicOCRExperimentError(f"Expected marker IDs 4–10 in order, got {ids}")
    if selected[1]["marker_bbox"] != [89, 6, 120, 27]:
        raise ArabicOCRExperimentError("Image 5 does not use corrected bbox (89, 6, 120, 27)")
    return selected, crops


def unwrap_paddle_result(result: Any) -> tuple[str, float | None, dict[str, Any]]:
    """Preserve PaddleOCR raw text while accepting its documented Result wrapper."""
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise ArabicOCRExperimentError("PaddleOCR result is not a dictionary-like JSON payload")
    body = payload.get("res", payload)
    if not isinstance(body, dict) or "rec_text" not in body:
        raise ArabicOCRExperimentError("PaddleOCR result does not contain rec_text")
    raw_text = body["rec_text"]
    if raw_text is None:
        raw_text = ""
    if not isinstance(raw_text, str):
        raise ArabicOCRExperimentError("PaddleOCR rec_text is not a string")
    confidence = body.get("rec_score")
    if confidence is not None:
        confidence = float(confidence)
    return raw_text, confidence, payload

