"""Deterministic routing from local plate evidence to local or agent handling."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import run_video_pipeline.config as config
from run_video_pipeline.ocr import digits_only
from run_video_pipeline.schemas import PlateDetection, PlateRoute


@dataclass(frozen=True)
class RouteDecision:
    route: PlateRoute
    confidence: float
    reason: str


def route_plate(crop: np.ndarray | None, result: PlateDetection | None) -> RouteDecision:
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0 or result is None:
        return RouteDecision("unknown", 1.0, "missing usable crop or local evidence")

    parsed_digits = digits_only(result.normalized_plate or "")
    observed_digits = digits_only(result.normalized_ocr)
    compatible = bool(parsed_digits) and parsed_digits == observed_digits
    strong_private = (
        result.parser_status == "valid"
        and result.normalized_plate is not None
        and result.marker_reliable
        and result.marker_bbox is not None
        and result.marker_confidence >= config.PRIVATE_ROUTE_MIN_MARKER_CONF
        and result.ocr_confidence >= config.PRIVATE_ROUTE_MIN_OCR_CONF
        and compatible
    )
    if strong_private:
        confidence = min(result.ocr_confidence, result.marker_confidence)
        return RouteDecision(
            "private_local", round(confidence, 4),
            "valid private structure, compatible digits, reliable تونس marker, and sufficient OCR confidence",
        )

    failed = []
    if result.parser_status != "valid" or result.normalized_plate is None: failed.append("private parser not valid")
    if not result.marker_reliable or result.marker_bbox is None: failed.append("تونس marker not reliable")
    if result.ocr_confidence < config.PRIVATE_ROUTE_MIN_OCR_CONF: failed.append("OCR confidence below private threshold")
    if not compatible: failed.append("OCR digits incompatible with parsed private identity")
    private_score = min(
        result.ocr_confidence,
        result.marker_confidence if result.marker_reliable else 0.0,
    )
    return RouteDecision(
        "agent", round(max(0.5, 1.0 - private_score), 4),
        "; ".join(failed) or "private evidence uncertain",
    )
