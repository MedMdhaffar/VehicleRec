from __future__ import annotations
from typing import Optional
from pydantic import BaseModel
from common.schemas import BoundingBox, Confidence


class PlateDetection(BaseModel):
    bbox: BoundingBox     # [x1, y1, x2, y2]
    number: str           # OCR digits
    digits_conf: Confidence
    type: str             # "tounes" | "other"
    type_conf: Confidence
    raw_number: str = "" # unmodified OCR text, before digit/character normalization
    warning: Optional[str] = None


class PlateRecognitionResponse(BaseModel):
    request_id: str
    timestamp: str
    image_size: list[int]   # [w, h]
    plate_count: int
    plates: list[PlateDetection]
    annotated_image: Optional[str] = None
    annotated_image_b64: Optional[str] = None
