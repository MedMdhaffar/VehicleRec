"""Evidence-limited local number policies. Never infer formats from category labels."""
from dataclasses import dataclass
from run_video_pipeline import config
from run_video_pipeline.ocr import digits_only
from run_video_pipeline.schemas import NumberRecognition

@dataclass(frozen=True)
class NumberPolicy:
    name: str
    public_category: str
    number_supported: bool
    input_preparation: str
    evidence: str


NUMBER_POLICIES = {
    "tunis": NumberPolicy("tunis_private", "private", True, "detect marker then OCR left/right digits; legacy whole-crop mode optional", "private parser and marker/digit checks"),
    "nt": NumberPolicy("nt_whole_plate", "temporary", True, "unchanged whole crop", "user-supplied application format; independent accuracy unvalidated"),
    "idara": NumberPolicy("idara_whole_plate", "administrative", True, "unchanged whole crop", "user-supplied application format; independent accuracy unvalidated"),
    "shorta": NumberPolicy("shorta_whole_plate", "police", True, "unchanged whole crop", "user-supplied application format; independent accuracy unvalidated"),
}
PUBLIC_CATEGORY = {category: policy.public_category for category, policy in NUMBER_POLICIES.items()}


def select_policy(category):
    return NUMBER_POLICIES.get(category)



def validate_tunis(result, crop_shape, *, config=config):
    sides_only = result.resolution_source == "marker_sides_ocr"
    checks = {
        "valid_private_fields": result.parser_status == "valid" and result.normalized_plate is not None,
        "reliable_tounes_marker": result.marker_reliable and result.marker_bbox is not None and result.marker_confidence >= config.PRIVATE_ROUTE_MIN_MARKER_CONF,
        "sufficient_ocr": result.ocr_confidence >= config.PRIVATE_ROUTE_MIN_OCR_CONF,
        "exact_digit_agreement": bool(result.normalized_plate) and digits_only(result.normalized_plate) == digits_only(result.normalized_ocr),
        "no_category_conflict": all(marker == "tunis" for marker in result.classification.observed_markers),
    }
    conflicting_regions = not sides_only and (
        result.left_ocr.isascii() and result.left_ocr.isdigit() and result.right_ocr.isascii() and result.right_ocr.isdigit()
        and result.left_ocr_confidence >= config.MARKER_REGION_OCR_CONF
        and result.right_ocr_confidence >= config.MARKER_REGION_OCR_CONF
        and 1 <= len(result.left_ocr) <= 3 and 1 <= len(result.right_ocr) <= 4
        and result.left_ocr + result.right_ocr != digits_only(result.normalized_ocr)
    )
    checks["region_readings_not_conflicting"] = not conflicting_regions
    if sides_only:
        del checks["exact_digit_agreement"]
        del checks["region_readings_not_conflicting"]
        checks["valid_side_fields"] = (
            result.left_ocr.isascii() and result.left_ocr.isdigit() and 1 <= len(result.left_ocr) <= 3
            and result.right_ocr.isascii() and result.right_ocr.isdigit() and 1 <= len(result.right_ocr) <= 4
            and result.series == result.left_ocr and result.registration_number == result.right_ocr
        )
        checks["sufficient_region_ocr"] = (
            result.left_ocr_confidence >= config.MARKER_REGION_OCR_CONF
            and result.right_ocr_confidence >= config.MARKER_REGION_OCR_CONF
        )
    if conflicting_regions: reason = "conflicting_readings"
    elif result.parser_status == "ambiguous": reason = "ambiguous_fields"
    elif not result.raw_ocr: reason = "insufficient_ocr_evidence"
    elif not checks["no_category_conflict"]: reason = "conflicting_category_evidence"
    elif not checks["valid_private_fields"]: reason = "invalid_format"
    elif not checks["reliable_tounes_marker"]: reason = "insufficient_marker_evidence"
    elif not checks["sufficient_ocr"]: reason = "insufficient_ocr_confidence"
    elif sides_only and not checks["valid_side_fields"]: reason = "invalid_format"
    elif sides_only and not checks["sufficient_region_ocr"]: reason = "insufficient_ocr_confidence"
    elif not sides_only and not checks["exact_digit_agreement"]: reason = "conflicting_readings"
    else: reason = None
    height, width = crop_shape[:2]
    regions = [] if sides_only else [{"role": "whole", "bbox": [0, 0, width, height], "coordinate_context": "crop_local_xyxy"}]
    if result.marker_bbox:
        regions.append({"role": "marker", "bbox": result.marker_bbox, "coordinate_context": "crop_local_xyxy"})
    if result.left_ocr or result.right_ocr or result.resolution_source in ("marker_left_right_ocr", "marker_sides_ocr"):
        regions.extend([{"role": "left_digits", "bbox": [0, 0, result.marker_bbox[0], height], "coordinate_context": "crop_local_xyxy"},
                        {"role": "right_digits", "bbox": [result.marker_bbox[2], 0, width, height], "coordinate_context": "crop_local_xyxy"}])
    return NumberRecognition(policy="tunis_private", outcome="recognized" if reason is None else "unresolved", reason=reason,
                             fields={"series": result.series, "registration_number": result.registration_number} if reason is None else {},
                             normalized_number=result.normalized_plate if reason is None else None, checks=checks, regions=regions)


def compact_result(result):
    """Accepted numbers only; raw OCR belongs exclusively in debug/review records."""
    return {"sample_id": result.sample_id, "predicted_category": result.predicted_category,
            "category_decision_source": result.category_decision_source, "recognition_policy": result.recognition_policy,
            "recognition_status": result.recognition_status, "unresolved_reason": result.unresolved_reason,
            "registration_fields": result.registration_fields, "normalized_number": result.normalized_number,
            "plate_text": result.normalized_number if result.status == "accepted" else "",
            "plate_category": result.plate_category, "status": result.status,
            "confidence": result.category_confidence, "ocr_confidence": result.text_confidence, "source": result.source,
            "attempt_timing": result.timing.model_dump(mode="json")}
