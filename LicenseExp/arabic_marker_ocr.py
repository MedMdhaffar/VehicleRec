"""Fixed policies and helpers for the manual-marker Arabic OCR experiment."""

from __future__ import annotations

import hashlib
import re
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

FILTER_CONDITIONS = ("original", "otsu", "canny", "otsu_canny")
FILTER_PARAMETERS = {
    "original": {"operation": "unchanged BGR crop"},
    "otsu": {
        "operation": "BGR to grayscale, then OpenCV Otsu binary threshold",
        "threshold_type": "THRESH_BINARY | THRESH_OTSU",
    },
    "canny": {
        "operation": "BGR to grayscale, then OpenCV Canny",
        "threshold1": 100,
        "threshold2": 200,
        "aperture_size": 3,
        "l2_gradient": False,
    },
    "otsu_canny": {
        "operation": "OpenCV Canny applied to the Otsu binary image",
        "threshold1": 100,
        "threshold2": 200,
        "aperture_size": 3,
        "l2_gradient": False,
    },
    "shared": {
        "preserve_dimensions": True,
        "single_channel_conversion": "cv2.COLOR_GRAY2BGR; three identical channels",
        "extra_resize_blur_sharpen_morphology_inversion": False,
    },
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


def parse_nt_evidence(text: str) -> dict[str, Any]:
    """Detect explicit NT text without matching it inside a longer Arabic word."""
    boundary_text = unicodedata.normalize("NFKC", text)
    boundary_text = "".join(
        character
        for character in boundary_text
        if character != "\u0640" and unicodedata.category(character) != "Mn"
    )
    # Arabic base letters are the only forbidden external boundary. Digits,
    # punctuation, separators, and string boundaries are permitted.
    arabic_letter = r"[\u0621-\u063A\u0641-\u064A\u066E-\u066F\u0671-\u06D3\u06FA-\u06FC]"
    patterns = {
        "nt": rf"(?<!{arabic_letter})ن\s*ت(?!{arabic_letter})",
        "shorta": rf"(?<!{arabic_letter})شرطة(?!{arabic_letter})",
        "tunis": rf"(?<!{arabic_letter})تونس(?!{arabic_letter})",
    }
    matches = {
        name: bool(re.search(pattern, boundary_text))
        for name, pattern in patterns.items()
    }
    conflicting_categories = [name for name in ("shorta", "tunis") if matches[name]]
    if conflicting_categories:
        decision = "conflicting"
        reason = "explicit_non_nt_category_evidence"
    elif matches["nt"]:
        decision = "present"
        reason = "explicit_nt_with_valid_arabic_letter_boundaries"
    else:
        decision = "absent"
        reason = "no_unambiguous_nt_match"
    return {
        "decision": decision,
        "reason": reason,
        "boundary_text": boundary_text,
        "normalized_text": normalize_arabic_marker(text),
        "matches": matches,
        "conflicting_categories": conflicting_categories,
    }


def fixed_filter_variants(image_bgr: np.ndarray) -> dict[str, np.ndarray]:
    """Create the four fixed, unchanged-size OCR inputs without tuning."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or image_bgr.dtype != np.uint8:
        raise ArabicOCRExperimentError("OCR filter input must be uint8 BGR with three channels")
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, otsu_gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    canny_gray = cv2.Canny(gray, 100, 200, apertureSize=3, L2gradient=False)
    otsu_canny_gray = cv2.Canny(otsu_gray, 100, 200, apertureSize=3, L2gradient=False)
    variants = {
        "original": image_bgr.copy(),
        "otsu": cv2.cvtColor(otsu_gray, cv2.COLOR_GRAY2BGR),
        "canny": cv2.cvtColor(canny_gray, cv2.COLOR_GRAY2BGR),
        "otsu_canny": cv2.cvtColor(otsu_canny_gray, cv2.COLOR_GRAY2BGR),
    }
    for condition, variant in variants.items():
        if variant.shape != image_bgr.shape or variant.dtype != np.uint8:
            raise ArabicOCRExperimentError(
                f"{condition}: filter changed dimensions, channels, or dtype"
            )
    return variants


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

