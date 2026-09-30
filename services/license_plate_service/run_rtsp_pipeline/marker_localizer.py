"""Localization of the central تونس marker in a tight plate crop."""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

import run_video_pipeline.config as config
from run_video_pipeline.plate_detector import BBox, clamp_bbox

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarkerLocalization:
    detected: bool
    confidence: float = 0.0
    bbox: BBox | None = None
    center_x: float | None = None


class TounesMarkerLocalizer:
    """YOLO تونس word localizer using its required direct 512-square stretch."""

    def __init__(self, model_path: str = config.WORD_DETECTOR_PATH):
        self.model_path = str(model_path)
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"Tounes marker model not found: {self.model_path}")
        from ultralytics import YOLO

        self._model = YOLO(self.model_path, task="detect")
        log.info("Loaded تونس marker localizer: %s", self.model_path)

    @staticmethod
    def _empty() -> MarkerLocalization:
        return MarkerLocalization(detected=False)

    @staticmethod
    def _prepare(crop: np.ndarray) -> np.ndarray | None:
        if crop is None or not isinstance(crop, np.ndarray) or crop.ndim < 2 or crop.size == 0:
            return None
        # This is deliberately a stretch, not YOLO letterboxing. The detector's
        # Roboflow training preprocessing resized every plate directly to 512².
        return cv2.resize(crop, (config.WORD_IMGSZ, config.WORD_IMGSZ))

    @staticmethod
    def _decode(result, crop: np.ndarray) -> MarkerLocalization:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return TounesMarkerLocalizer._empty()

        confidences = boxes.conf.tolist()
        best_index = max(range(len(confidences)), key=confidences.__getitem__)
        confidence = float(confidences[best_index])
        if not math.isfinite(confidence):
            return TounesMarkerLocalizer._empty()

        sx1, sy1, sx2, sy2 = boxes.xyxy[best_index].tolist()
        height, width = crop.shape[:2]
        bbox = clamp_bbox(
            (
                sx1 * width / config.WORD_IMGSZ,
                sy1 * height / config.WORD_IMGSZ,
                sx2 * width / config.WORD_IMGSZ,
                sy2 * height / config.WORD_IMGSZ,
            ),
            width,
            height,
        )
        if bbox is None:
            return TounesMarkerLocalizer._empty()
        return MarkerLocalization(
            detected=True,
            confidence=confidence,
            bbox=bbox,
            center_x=(bbox[0] + bbox[2]) / 2.0,
        )

    def locate_batch(
        self,
        crops: Iterable[np.ndarray],
        *,
        inference_conf: float = 0.05,
        batch_size: int = 32,
    ) -> list[MarkerLocalization]:
        crop_list = list(crops)
        if not crop_list:
            return []
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self._model is None:
            self.load()

        output: list[MarkerLocalization] = []
        for start in range(0, len(crop_list), batch_size):
            batch = crop_list[start:start + batch_size]
            prepared = [self._prepare(crop) for crop in batch]
            valid_indices = [i for i, image in enumerate(prepared) if image is not None]
            batch_output = [self._empty() for _ in batch]
            if valid_indices:
                images = [prepared[i] for i in valid_indices]
                with self._lock:
                    results = self._model.predict(
                        images,
                        conf=inference_conf,
                        imgsz=config.WORD_IMGSZ,
                        verbose=False,
                    )
                for index, result in zip(valid_indices, results):
                    batch_output[index] = self._decode(result, batch[index])
            output.extend(batch_output)
        return output

    def locate(
        self,
        crop: np.ndarray,
        *,
        inference_conf: float = 0.05,
    ) -> MarkerLocalization:
        return self.locate_batch([crop], inference_conf=inference_conf, batch_size=1)[0]


def split_around_marker(
    crop: np.ndarray,
    marker: MarkerLocalization,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return untouched left/right digit regions outside a valid marker box."""
    if not marker.detected or marker.bbox is None or crop is None or crop.size == 0:
        return None
    x1, _, x2, _ = marker.bbox
    left = crop[:, :x1]
    right = crop[:, x2:]
    if left.size == 0 or right.size == 0 or left.shape[1] < 1 or right.shape[1] < 1:
        return None
    return left.copy(), right.copy()
