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


def _scalar_policy():
    from .scalar_policy import LocalPlatePolicy
    return LocalPlatePolicy(config.MARKER_RELIABLE_CONF, config.MARKER_REGION_OCR_CONF,
        config.PRIVATE_ROUTE_MIN_OCR_CONF, config.PRIVATE_ROUTE_MIN_MARKER_CONF)


def needs_region_ocr(ocr_result, marker):
    from .scalar_policy import needs_region_ocr as scalar_needs_region
    return scalar_needs_region(ocr_result, marker, policy=_scalar_policy())


def assemble_plate_detection(detection, ocr_result, marker, left_result=None, right_result=None):
    from .scalar_policy import assemble_plate_fields
    return PlateDetection(**assemble_plate_fields(detection.bbox, detection.confidence,
        ocr_result, marker, left_result, right_result, policy=_scalar_policy()))


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
