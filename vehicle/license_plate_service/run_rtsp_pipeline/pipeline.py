"""Shared detector -> crop -> OCR -> private-plate parser orchestration."""
from __future__ import annotations

import logging
import time
from typing import Protocol

import numpy as np

import run_video_pipeline.config as config
from run_video_pipeline.marker_localizer import (
    MarkerLocalization,
    split_around_marker,
)
from run_video_pipeline.ocr import OCRResult, digits_only
from run_video_pipeline.plate_detector import PlateCrop, PlateDetector
from run_video_pipeline.private_plate_parser import parse_private_ocr, parse_private_plate
from run_video_pipeline.schemas import PlateDetection

log = logging.getLogger(__name__)


class OCRReader(Protocol):
    def read_result(self, crop: np.ndarray) -> OCRResult: ...


class MarkerLocalizer(Protocol):
    def locate(self, crop: np.ndarray, *, inference_conf: float) -> MarkerLocalization: ...


def _read_safely(reader: OCRReader, crop: np.ndarray, bbox) -> OCRResult:
    try:
        return reader.read_result(crop)
    except Exception as exc:
        log.warning("OCR failed for plate bbox %s: %s", bbox, exc)
        return OCRResult(raw_text="", normalized_text="", confidence=0.0)


def _locate_safely(
    localizer: MarkerLocalizer | None,
    crop: np.ndarray,
    bbox,
) -> MarkerLocalization:
    if localizer is None:
        return MarkerLocalization(detected=False)
    try:
        return localizer.locate(crop, inference_conf=config.MARKER_INFERENCE_CONF)
    except Exception as exc:
        log.warning("Marker localization failed for plate bbox %s: %s", bbox, exc)
        return MarkerLocalization(detected=False)


def needs_region_ocr(ocr_result: OCRResult, marker: MarkerLocalization) -> bool:
    """Whether existing conservative fusion can use marker-split OCR evidence."""
    return (
        parse_private_ocr(ocr_result).status == "ambiguous"
        and marker.detected
        and marker.confidence >= config.MARKER_RELIABLE_CONF
        and marker.bbox is not None
    )


def assemble_plate_detection(
    detection: PlateCrop,
    ocr_result: OCRResult,
    marker: MarkerLocalization,
    left_result: OCRResult | None = None,
    right_result: OCRResult | None = None,
) -> PlateDetection:
    """Apply the production parser/fusion policy to already-computed evidence."""
    parsed = parse_private_ocr(ocr_result)
    marker_reliable = marker.detected and marker.confidence >= config.MARKER_RELIABLE_CONF
    left_result = left_result or OCRResult("", "", 0.0)
    right_result = right_result or OCRResult("", "", 0.0)
    resolution_source = "whole_ocr"

    if parsed.status == "ambiguous" and marker_reliable:
        left_digits = digits_only(left_result.normalized_text)
        right_digits = digits_only(right_result.normalized_text)
        whole_digits = digits_only(ocr_result.normalized_text)
        side_fields_valid = (
            left_result.normalized_text.isascii()
            and left_result.normalized_text.isdigit()
            and right_result.normalized_text.isascii()
            and right_result.normalized_text.isdigit()
            and 1 <= len(left_digits) <= 3
            and 1 <= len(right_digits) <= 4
            and left_result.confidence >= config.MARKER_REGION_OCR_CONF
            and right_result.confidence >= config.MARKER_REGION_OCR_CONF
        )
        if side_fields_valid and left_digits + right_digits == whole_digits:
            resolved = parse_private_plate(f"{left_digits}T{right_digits}")
            if resolved.status == "valid":
                parsed = resolved
                resolution_source = "marker_left_right_ocr"

    return PlateDetection(
        bbox=list(detection.bbox),
        detector_confidence=(
            round(detection.confidence, 4)
            if detection.confidence is not None
            else None
        ),
        raw_ocr=ocr_result.raw_text,
        normalized_ocr=ocr_result.normalized_text,
        ocr_confidence=round(ocr_result.confidence, 4),
        parser_status=parsed.status,
        series=parsed.series,
        registration_number=parsed.registration_number,
        normalized_plate=parsed.normalized_plate,
        parser_reason=parsed.reason,
        marker_detected=marker.detected,
        marker_reliable=marker_reliable,
        marker_confidence=round(marker.confidence, 4),
        marker_bbox=list(marker.bbox) if marker.bbox is not None else None,
        marker_center_x=round(marker.center_x, 2) if marker.center_x is not None else None,
        left_ocr=left_result.normalized_text,
        left_ocr_confidence=round(left_result.confidence, 4),
        right_ocr=right_result.normalized_text,
        right_ocr_confidence=round(right_result.confidence, 4),
        resolution_source=resolution_source,
    )


def analyze_plate_crop(
    detection: PlateCrop,
    reader: OCRReader,
    marker_localizer: MarkerLocalizer | None = None,
    *,
    timing: dict[str, float] | None = None,
) -> PlateDetection:
    """Recognize and parse one safe crop while preserving partial results."""
    total_started = time.perf_counter()
    started = time.perf_counter()
    ocr_result = _read_safely(reader, detection.crop, detection.bbox)
    ocr_seconds = time.perf_counter() - started
    started = time.perf_counter()
    marker = _locate_safely(marker_localizer, detection.crop, detection.bbox)
    marker_seconds = time.perf_counter() - started
    left_result = OCRResult("", "", 0.0)
    right_result = OCRResult("", "", 0.0)

    # Explicit OCR markers and uniquely structured whole-crop OCR retain their
    # existing parser result. Marker evidence resolves only ambiguous digits.
    if needs_region_ocr(ocr_result, marker):
        regions = split_around_marker(detection.crop, marker)
        if regions is not None:
            started = time.perf_counter()
            left_result = _read_safely(reader, regions[0], detection.bbox)
            right_result = _read_safely(reader, regions[1], detection.bbox)
            ocr_seconds += time.perf_counter() - started
    started = time.perf_counter()
    result = assemble_plate_detection(detection, ocr_result, marker, left_result, right_result)
    parser_seconds = time.perf_counter() - started
    if timing is not None:
        timing.update({
            "ocr_ms": ocr_seconds * 1000,
            "marker_ms": marker_seconds * 1000,
            "parser_ms": parser_seconds * 1000,
            "local_total_ms": (time.perf_counter() - total_started) * 1000,
        })
    return result


def analyze_image(
    image: np.ndarray,
    detector: PlateDetector,
    reader: OCRReader,
    marker_localizer: MarkerLocalizer | None = None,
    *,
    conf: float | None = None,
) -> list[PlateDetection]:
    """Detect, safely crop, recognize, and parse every plate in an image."""
    detections = detector.detect_and_crop(image, conf=conf)
    return [
        analyze_plate_crop(detection, reader, marker_localizer)
        for detection in detections
    ]
