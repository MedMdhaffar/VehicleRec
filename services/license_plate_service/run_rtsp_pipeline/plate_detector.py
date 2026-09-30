from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

import run_video_pipeline.config as config

log = logging.getLogger(__name__)

BBox = tuple[int, int, int, int]
Detection = tuple[int, int, int, int, float]


@dataclass(frozen=True)
class PlateCrop:
    """An unmodified plate crop and the detection that produced it."""

    crop: np.ndarray
    bbox: BBox
    # ``None`` when the caller supplied an already-cropped plate and no
    # detector was run (for example the /read_plate_crop endpoint).
    confidence: float | None


def clamp_bbox(bbox: Sequence[float], image_width: int, image_height: int) -> BBox | None:
    """Clamp an ``xyxy`` box to image bounds, returning ``None`` if unusable."""
    if image_width <= 0 or image_height <= 0:
        return None
    try:
        if len(bbox) != 4:
            return None
        coords = [float(value) for value in bbox]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in coords):
        return None

    x1, y1, x2, y2 = (int(value) for value in coords)
    x1 = max(0, min(image_width, x1))
    y1 = max(0, min(image_height, y1))
    x2 = max(0, min(image_width, x2))
    y2 = max(0, min(image_height, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def crop_detections(
    image: np.ndarray,
    detections: Iterable[Sequence[float]],
    *,
    min_width: int = 1,
    min_height: int = 1,
    pad_fraction: float = 0.0,
) -> list[PlateCrop]:
    """Safely crop detections without resizing or otherwise changing pixels."""
    if image is None or not isinstance(image, np.ndarray) or image.ndim < 2 or image.size == 0:
        return []
    if min_width < 1 or min_height < 1:
        raise ValueError("minimum crop dimensions must be positive")
    if pad_fraction < 0:
        raise ValueError("pad_fraction must be non-negative")

    image_height, image_width = image.shape[:2]
    crops: list[PlateCrop] = []
    for detection in detections:
        try:
            if len(detection) != 5:
                continue
            raw_x1, raw_y1, raw_x2, raw_y2, raw_confidence = detection
            confidence = float(raw_confidence)
            raw_bbox = [float(raw_x1), float(raw_y1), float(raw_x2), float(raw_y2)]
            if not math.isfinite(confidence) or not all(math.isfinite(v) for v in raw_bbox):
                continue
            if raw_bbox[2] <= raw_bbox[0] or raw_bbox[3] <= raw_bbox[1]:
                continue

            pad_x = (raw_bbox[2] - raw_bbox[0]) * pad_fraction
            pad_y = (raw_bbox[3] - raw_bbox[1]) * pad_fraction
            bbox = clamp_bbox(
                (
                    raw_bbox[0] - pad_x,
                    raw_bbox[1] - pad_y,
                    raw_bbox[2] + pad_x,
                    raw_bbox[3] + pad_y,
                ),
                image_width,
                image_height,
            )
            if bbox is None:
                continue
            x1, y1, x2, y2 = bbox
            if x2 - x1 < min_width or y2 - y1 < min_height:
                continue
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            crops.append(PlateCrop(crop=crop.copy(), bbox=bbox, confidence=confidence))
        except (TypeError, ValueError, OverflowError):
            log.debug("Skipping invalid plate detection: %r", detection)
    return crops


def _bbox_iou(left: Sequence[float], right: Sequence[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def deduplicate_detections(
    detections: Iterable[Sequence[float]],
    *,
    iou_threshold: float = config.PLATE_DEDUP_IOU,
) -> list[Detection]:
    """Keep the strongest box among nearly identical plate detections."""
    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("IoU threshold must be between 0 and 1")
    valid: list[tuple[int, Detection]] = []
    for original_index, detection in enumerate(detections):
        try:
            if len(detection) != 5:
                continue
            x1, y1, x2, y2, confidence = (float(value) for value in detection)
            if (
                not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence))
                or x2 <= x1
                or y2 <= y1
            ):
                continue
            valid.append((original_index, (int(x1), int(y1), int(x2), int(y2), confidence)))
        except (TypeError, ValueError, OverflowError):
            continue

    kept: list[tuple[int, Detection]] = []
    for indexed_detection in sorted(valid, key=lambda item: item[1][4], reverse=True):
        _, detection = indexed_detection
        if all(_bbox_iou(detection, existing) < iou_threshold for _, existing in kept):
            kept.append(indexed_detection)
    return [detection for _, detection in sorted(kept, key=lambda item: item[0])]


class PlateDetector:
    def __init__(self, model_path: str = config.PLATE_DETECTOR_PATH):
        self.model_path = model_path
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        from ultralytics import YOLO
        self._model = YOLO(self.model_path)
        log.info("Loaded plate detector: %s", self.model_path)

    def predict(
        self,
        image: np.ndarray,
        conf: Optional[float] = None,
        imgsz: Optional[int] = None,
    ) -> list[Detection]:
        if self._model is None:
            if not Path(self.model_path).exists():
                log.warning("Plate detector weights not found at %s — returning []", self.model_path)
                return []
            self.load()

        effective_conf  = conf  if conf  is not None else config.PLATE_CONF
        effective_imgsz = imgsz if imgsz is not None else config.DETECT_IMGSZ

        with self._lock:
            results = self._model.predict(
                image, conf=effective_conf, imgsz=effective_imgsz, verbose=False
            )[0]

        out: list[Detection] = []
        for b in results.boxes:
            x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
            out.append((x1, y1, x2, y2, float(b.conf[0])))
        return out

    def detect_and_crop(
        self,
        image: np.ndarray,
        conf: Optional[float] = None,
        imgsz: Optional[int] = None,
        *,
        min_width: int = 1,
        min_height: int = 1,
        pad_fraction: float = 0.0,
    ) -> list[PlateCrop]:
        """Detect every plate and return safe, original-resolution crops."""
        if image is None or not isinstance(image, np.ndarray) or image.ndim < 2 or image.size == 0:
            return []
        detections = deduplicate_detections(
            self.predict(image, conf=conf, imgsz=imgsz),
            iou_threshold=config.PLATE_DEDUP_IOU,
        )
        return crop_detections(
            image,
            detections,
            min_width=min_width,
            min_height=min_height,
            pad_fraction=pad_fraction,
        )
