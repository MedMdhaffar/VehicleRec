"""Canonical local Plate scalar fusion/routing; no image or provider imports."""
from dataclasses import dataclass
from .text_policy import OCRResult, digits_only
from .private_plate_parser import parse_private_ocr, parse_private_plate

@dataclass(frozen=True, slots=True)
class LocalPlatePolicy:
    marker_reliable: float = .5
    region_confidence: float = .5
    route_ocr: float = .9
    route_marker: float = .5

DEFAULT_POLICY = LocalPlatePolicy()

@dataclass(frozen=True, slots=True)
class MarkerScalar:
    detected: bool
    confidence: float = 0.0
    bbox: tuple[int, int, int, int] | None = None
    center_x: float | None = None

def needs_region_ocr(ocr_result: OCRResult, marker: MarkerScalar, *, policy=DEFAULT_POLICY) -> bool:
    """Whether existing conservative fusion can use marker-split OCR evidence."""
    return (
        parse_private_ocr(ocr_result).status == "ambiguous"
        and marker.detected
        and marker.confidence >= policy.marker_reliable
        and marker.bbox is not None
    )


def assemble_plate_fields(
    bbox, detector_confidence,
    ocr_result: OCRResult,
    marker: MarkerScalar,
    left_result: OCRResult | None = None,
    right_result: OCRResult | None = None,
    *, policy=DEFAULT_POLICY,
) -> dict:
    """Apply the production parser/fusion policy to already-computed evidence."""
    parsed = parse_private_ocr(ocr_result)
    marker_reliable = marker.detected and marker.confidence >= policy.marker_reliable
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
            and left_result.confidence >= policy.region_confidence
            and right_result.confidence >= policy.region_confidence
        )
        if side_fields_valid and left_digits + right_digits == whole_digits:
            resolved = parse_private_plate(f"{left_digits}T{right_digits}")
            if resolved.status == "valid":
                parsed = resolved
                resolution_source = "marker_left_right_ocr"

    return dict(
        bbox=list(bbox),
        detector_confidence=(
            round(detector_confidence, 4)
            if detector_confidence is not None
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


def local_route(result, *, policy=DEFAULT_POLICY):
    """Local eligibility and diagnostic confidence; no fallback action."""
    parsed_digits = digits_only(result.normalized_plate or "")
    observed_digits = digits_only(result.normalized_ocr)
    compatible = bool(parsed_digits) and parsed_digits == observed_digits
    strong_private = (
        result.parser_status == "valid"
        and result.normalized_plate is not None
        and result.marker_reliable
        and result.marker_bbox is not None
        and result.marker_confidence >= policy.route_marker
        and result.ocr_confidence >= policy.route_ocr
        and compatible
    )
    if strong_private:
        confidence = min(result.ocr_confidence, result.marker_confidence)
        return (True, round(confidence, 4),
            "valid private structure, compatible digits, reliable تونس marker, and sufficient OCR confidence",
        )

    failed = []
    if result.parser_status != "valid" or result.normalized_plate is None: failed.append("private parser not valid")
    if not result.marker_reliable or result.marker_bbox is None: failed.append("تونس marker not reliable")
    if result.ocr_confidence < policy.route_ocr: failed.append("OCR confidence below private threshold")
    if not compatible: failed.append("OCR digits incompatible with parsed private identity")
    private_score = min(
        result.ocr_confidence,
        result.marker_confidence if result.marker_reliable else 0.0,
    )
    return (False, round(max(0.5, 1.0 - private_score), 4),
        "; ".join(failed) or "private evidence uncertain",
    )


def replace_cached_read(current_status, current_text, current_confidence,
                        candidate_status, candidate_text, candidate_confidence, gain):
    """Existing ordered recheck replacement policy, shared with TrackState."""
    if candidate_status != 'accepted':
        return False, 'recheck not accepted; kept initial result'
    if current_status != 'accepted':
        return True, 'accepted recheck replaced unresolved initial result'
    if candidate_text == current_text and candidate_confidence >= current_confidence - .02:
        return True, 'same identity confirmed on substantially better crop'
    if candidate_confidence >= current_confidence + gain:
        return True, 'different identity replaced by higher-confidence better-crop result'
    return False, 'recheck disagreed without sufficient confidence gain'

