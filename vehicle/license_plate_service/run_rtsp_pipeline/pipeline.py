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
from run_video_pipeline.schemas import PlateDetection, NumberRecognition
from run_video_pipeline.classification import classify_crop

log = logging.getLogger(__name__)


class RegistrationOCRError(RuntimeError):
    pass


class MarkerLocalizationError(RuntimeError):
    pass


class OCRReader(Protocol):
    def read_result(self, crop: np.ndarray) -> OCRResult: ...


class MarkerLocalizer(Protocol):
    def locate(self, crop: np.ndarray, *, inference_conf: float) -> MarkerLocalization: ...


def _read_safely(reader: OCRReader, crop: np.ndarray, bbox) -> OCRResult:
    try:
        return reader.read_result(crop)
    except Exception as exc:
        log.warning("OCR failed for plate bbox %s: %s", bbox, exc)
        raise RegistrationOCRError(f"Registration OCR failed for bbox {bbox}: {exc}") from exc


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
        raise MarkerLocalizationError(f"Marker localization failed for bbox {bbox}: {exc}") from exc


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


def _analyze_plate_crop(
    detection: PlateCrop,
    reader: OCRReader,
    marker_localizer: MarkerLocalizer | None = None,
    *,
    timing: dict[str, float] | None = None,
    classifier=None,
) -> PlateDetection:
    """Recognize and parse one safe crop while preserving partial results."""
    classification = classify_crop(classifier, detection.crop)
    if timing is not None:
        timing["classification_ms"] = classification.classification_ms
    if classification.status == "error":
        return PlateDetection(
            classification=classification, bbox=list(detection.bbox),
            detector_confidence=detection.confidence, raw_ocr="", normalized_ocr="",
            ocr_confidence=0.0, parser_status="invalid",
            parser_reason="category classification failed before registration OCR",
        )
    from run_video_pipeline.policies import select_policy
    policy = select_policy(classification.final_category)
    if policy is None or not policy.number_supported:
        reason = "number_policy_evidence_missing" if policy else "category_uncertain"
        return PlateDetection(
            classification=classification, bbox=list(detection.bbox), detector_confidence=detection.confidence,
            raw_ocr="", normalized_ocr="", ocr_confidence=0.0, parser_status="invalid",
            parser_reason=reason, number=NumberRecognition(
                policy=policy.name if policy else "unselected", outcome="unresolved", reason=reason,
                checks={"category_prediction_is_not_number_evidence": True,
                        "required": "independent complete crops with field/region labels and validated layout specifications"},
            ),
        )
    total_started = time.perf_counter()
    started = time.perf_counter()
    ocr_result = _read_safely(reader, detection.crop, detection.bbox)
    ocr_seconds = time.perf_counter() - started
    if classification.final_category in ('nt', 'idara', 'shorta'):
        from run_video_pipeline.whole_plate_numbers import recognize_whole_number
        # Preserve the native score for new policies; Tunis retains its existing rounded reader score.
        if ocr_result.native_confidence is not None:
            ocr_result = OCRResult(ocr_result.raw_text, ocr_result.normalized_text, ocr_result.native_confidence)
        started = time.perf_counter()
        number = recognize_whole_number(classification.final_category, ocr_result, classification)
        parser_seconds = time.perf_counter() - started
        if timing is not None:
            timing.update(ocr_ms=ocr_seconds * 1000, parser_ms=parser_seconds * 1000,
                          local_total_ms=(time.perf_counter() - total_started) * 1000)
        return PlateDetection(
            classification=classification, number=number, bbox=list(detection.bbox),
            detector_confidence=detection.confidence, raw_ocr=ocr_result.raw_text,
            normalized_ocr=ocr_result.normalized_text, ocr_confidence=ocr_result.confidence,
            parser_status='valid' if number.outcome == 'recognized' else 'invalid',
            parser_reason=number.reason, normalized_plate=number.normalized_number,
            registration_number=number.fields.get('registration_number'),
        )
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
    result.classification = classification
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
    classifier=None,
) -> list[PlateDetection]:
    """Detect, safely crop, recognize, and parse every plate in an image."""
    detections = detector.detect_and_crop(image, conf=conf)
    return [
        analyze_plate_crop(detection, reader, marker_localizer, classifier=classifier)
        for detection in detections
    ]


def analyze_plate_crop(detection, reader, marker_localizer=None, *, timing=None, classifier=None):
    """Classify once and dispatch; errors never fall back to a different policy."""
    from run_video_pipeline.policies import validate_tunis
    from run_video_pipeline.classification import EvidenceClassifier
    classifier = EvidenceClassifier(classifier)
    readings = []
    region_records = []
    original_reader = reader
    class RecordingReader:
        def read_result(self, crop):
            height, width = detection.crop.shape[:2]
            index = len(region_records)
            bbox = [0, 0, crop.shape[1], height] if index < 2 else [width - crop.shape[1], 0, width, height]
            region_records.append({"role": ["whole", "left_digits", "right_digits"][min(index, 2)],
                                   "bbox": bbox, "coordinate_context": "crop_local_xyxy"})
            result = original_reader.read_result(crop)
            if not isinstance(result, OCRResult):
                raise RegistrationOCRError("Registration reader returned an invalid result")
            if not np.isfinite(result.confidence) or not 0.0 <= result.confidence <= 1.0:
                raise RegistrationOCRError(f"Invalid registration OCR confidence for raw reading {result.raw_text!r}: {result.confidence}")
            readings.append(result)
            return result
    reader = RecordingReader()
    try:
        result = _analyze_plate_crop(detection, reader, marker_localizer, timing=timing, classifier=classifier)
        if result.number is None:
            if result.classification is None or result.classification.status == "error":
                result.number = NumberRecognition(policy="unselected", outcome="error", reason="classification_failure")
            else:
                result.number = validate_tunis(result, detection.crop.shape)
        return result
    except Exception as exc:
        # Preserve category evidence if OCR raises: the recording proxy is filled
        # by classify_bgr, so we do not classify a second time.
        category = getattr(classifier, "last_evidence", None)
        reason = "ocr_failure" if isinstance(exc, RegistrationOCRError) else (
            "marker_localization_failure" if isinstance(exc, MarkerLocalizationError) else "processing_failure")
        return PlateDetection(
            classification=category, bbox=list(detection.bbox), detector_confidence=detection.confidence,
            raw_ocr=readings[0].raw_text if readings else "",
            normalized_ocr=readings[0].normalized_text if readings else "",
            ocr_confidence=readings[0].confidence if readings else 0.0, parser_status="invalid",
            parser_reason=str(exc), number=NumberRecognition(policy=(f"{category.final_category}_whole_plate" if category and category.final_category in ("nt", "idara", "shorta") else "tunis_private"), outcome="error", reason=reason, regions=region_records,
                                                            checks={"error": str(exc), "partial_ocr": [{"raw": r.raw_text, "normalized": r.normalized_text, "confidence": r.confidence} for r in readings]}),
        )
