from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class CategoryClassification(BaseModel):
    """Category evidence only; similarities are not calibrated probabilities."""

    status: Literal["completed", "error"]
    final_category: Optional[str] = None
    visual_category: Optional[str] = None
    decision_source: Optional[str] = None
    nearest_reference_id: Optional[str] = None
    nearest_similarity: Optional[float] = None
    category_margin: Optional[float] = None
    nt_evidence: Optional[str] = None
    conflict: bool = False
    arabic_ocr_failed: bool = False
    error: Optional[str] = None
    observed_markers: list[str] = Field(default_factory=list)
    category_only_text: str = ""
    classification_ms: float = 0.0
    visual_ms: float = 0.0
    arabic_ocr_ms: float = 0.0


class NumberRecognition(BaseModel):
    policy: str
    outcome: Literal["recognized", "unresolved", "error"]
    reason: Optional[str] = None
    fields: dict[str, str] = Field(default_factory=dict)
    normalized_number: Optional[str] = None
    checks: dict[str, Any] = Field(default_factory=dict)
    regions: list[dict[str, Any]] = Field(default_factory=list)


class PlateDetection(BaseModel):
    """Unified result for one detected (or directly supplied) plate crop."""

    classification: Optional[CategoryClassification] = Field(default=None, exclude=True)
    number: Optional[NumberRecognition] = Field(default=None, exclude=True)
    bbox: list[int]  # [x1, y1, x2, y2]
    detector_confidence: Optional[float]
    raw_ocr: str
    normalized_ocr: str
    ocr_confidence: float
    parser_status: Literal["valid", "ambiguous", "invalid"]
    series: Optional[str] = None
    registration_number: Optional[str] = None
    normalized_plate: Optional[str] = None
    parser_reason: Optional[str] = None
    marker_detected: bool = False
    marker_reliable: bool = False
    marker_confidence: float = 0.0
    marker_bbox: Optional[list[int]] = None  # crop-local [x1, y1, x2, y2]
    marker_center_x: Optional[float] = None  # crop-local x coordinate
    left_ocr: str = ""
    left_ocr_confidence: float = 0.0
    right_ocr: str = ""
    right_ocr_confidence: float = 0.0
    resolution_source: Literal["whole_ocr", "marker_left_right_ocr"] = "whole_ocr"


class PlateRecognitionResponse(BaseModel):
    request_id: str
    timestamp: str
    image_size: list[int]   # [w, h]
    plate_count: int
    plates: list[PlateDetection]
    annotated_image: Optional[str] = None
    annotated_image_b64: Optional[str] = None


PlateCategory = Literal["private", "temporary", "diplomatic", "military", "foreign", "other", "unknown", "administrative", "police"]
PlateRoute = Literal["private_local", "local", "unknown"]


class MarkerEvidence(BaseModel):
    detected: bool = False
    reliable: bool = False
    confidence: float = 0.0
    bbox: Optional[list[int]] = None


class PlateTiming(BaseModel):
    classification_ms: float = 0.0
    routing_ms: float = 0.0
    marker_ms: float = 0.0
    ocr_ms: float = 0.0
    parser_ms: float = 0.0
    local_total_ms: float = 0.0
    queue_wait_ms: float = 0.0
    total_ms: float = 0.0


class UnifiedPlateResult(BaseModel):
    classification: Optional[CategoryClassification] = Field(default=None, exclude=True)
    number: Optional[NumberRecognition] = Field(default=None, exclude=True)
    sample_id: str
    timestamp: str
    plate_text_raw: str = ""
    plate_text_normalized: Optional[str] = None
    plate_category: PlateCategory = "unknown"
    category_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    text_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    route: PlateRoute
    route_confidence: float = Field(ge=0.0, le=1.0)
    route_reason: str
    source: Literal["local_ocr", "none"]
    model_version: str
    bbox: list[int]
    detector_confidence: Optional[float] = None
    crop_path: Optional[str] = None
    original_image_ref: Optional[str] = None
    camera_id: Optional[str] = None
    track_id: Optional[str] = None
    marker: MarkerEvidence = Field(default_factory=MarkerEvidence)
    status: Literal["accepted", "unresolved", "error"]
    predicted_category: Optional[str] = None
    category_decision_source: Optional[str] = None
    recognition_policy: str = "unselected"
    recognition_status: Literal["recognized", "unresolved", "error"] = "unresolved"
    unresolved_reason: Optional[str] = None
    registration_fields: dict[str, str] = Field(default_factory=dict)
    normalized_number: Optional[str] = None
    warning: Optional[str] = None
    timing: PlateTiming = Field(default_factory=PlateTiming)
    evidence: dict[str, Any] = Field(default_factory=dict)


class PrototypeRecognitionResponse(BaseModel):
    request_id: str
    timestamp: str
    image_size: list[int]
    plate_count: int
    plates: list[UnifiedPlateResult]
    runtime_counters: dict[str, float | int]
