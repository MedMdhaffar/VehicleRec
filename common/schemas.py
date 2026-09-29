from __future__ import annotations
from typing import Annotated, Optional
from pydantic import AfterValidator, BaseModel, Field


def _validate_bbox(value: list[int]) -> list[int]:
    x1, y1, x2, y2 = value
    if x2 < x1 or y2 < y1:
        raise ValueError("bbox must use [x1, y1, x2, y2] with x2 >= x1 and y2 >= y1")
    return value


BoundingBox = Annotated[
    list[int],
    Field(min_length=4, max_length=4),
    AfterValidator(_validate_bbox),
]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class LabelConfidence(BaseModel):
    label: str
    confidence: Confidence


class BoxPrediction(BaseModel):
    bbox: BoundingBox
    confidence: Confidence


class Detection(BaseModel):
    id: int
    bbox: BoundingBox           # [x1, y1, x2, y2]
    type: str
    type_confidence: Confidence
    colour: Optional[str] = None
    colour_confidence: Optional[Confidence] = None


class ClassifyResponse(BaseModel):
    request_id: str
    timestamp: str
    image_size: list[int]       # [w, h]
    vehicle_count: int
    detections: list[Detection]
    annotated_image: Optional[str] = None
    annotated_image_b64: Optional[str] = None


class ErrorResponse(BaseModel):
    request_id: str
    error: str
    detail: str
