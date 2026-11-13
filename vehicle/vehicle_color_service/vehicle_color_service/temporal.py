"""Confidence- and crop-quality-weighted temporal HSV aggregation."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

import cv2
import numpy as np

try:
    from . import config as cfg
    from .schemas import ColourObservation
except ImportError:  # Support direct execution from inside color_service/
    import config as cfg
    from schemas import ColourObservation

try:
    from .scalar_policy import (SUPPORTED_COLOURS, ColourEvidence, ColourDecision,
                                make_colour_observation, aggregate_colour_observations, decide_colour)
except ImportError:
    from scalar_policy import (SUPPORTED_COLOURS, ColourEvidence, ColourDecision,
                               make_colour_observation, aggregate_colour_observations, decide_colour)


@dataclass(frozen=True)
class CropQuality:
    size: float
    sharpness: float
    exposure: float
    clipping: float
    combined: float
    laplacian_variance: float
    extreme_dark_ratio: float
    extreme_bright_ratio: float


def compute_colour_crop_quality(
    crop_area: int,
    frame_area: int,
    saturation_fraction: float = cfg.COLOR_QUALITY_SATURATION_FRAC,
) -> float:
    """Return sqrt-normalized crop area in [0, 1], saturated at the cap."""
    if crop_area <= 0 or frame_area <= 0 or saturation_fraction <= 0:
        return 0.0
    area_fraction = min(1.0, crop_area / frame_area)
    return round(min(1.0, math.sqrt(area_fraction / saturation_fraction)), 4)


def compute_colour_crop_quality_details(
    crop: np.ndarray,
    frame_shape: tuple[int, ...],
    bbox: list[int] | tuple[int, int, int, int],
    *,
    mode: str | None = None,
) -> CropQuality:
    """Explainable bounded quality; exposure only penalizes digital clipping."""
    mode = mode or os.environ.get("COLOR_QUALITY_MODE", "enhanced")
    frame_h, frame_w = frame_shape[:2]
    crop_area = int(crop.shape[0] * crop.shape[1]) if isinstance(crop, np.ndarray) and crop.ndim >= 2 else 0
    size = compute_colour_crop_quality(crop_area, frame_h * frame_w)
    if crop_area <= 0 or crop.ndim != 3 or crop.shape[2] != 3:
        return CropQuality(size, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    sharpness = min(1.0, math.sqrt(max(0.0, laplacian_variance) / 300.0))
    dark_ratio = float(np.mean(gray <= 5))
    bright_ratio = float(np.mean(gray >= 250))
    pathological = max(dark_ratio, bright_ratio)
    exposure = 1.0 - min(1.0, max(0.0, pathological - 0.65) / 0.35)

    x1, y1, x2, y2 = bbox
    touches = sum((x1 <= 0, y1 <= 0, x2 >= frame_w, y2 >= frame_h))
    clipping = max(0.4, 1.0 - 0.2 * touches)
    # Area remains the base signal. The other components form a reliability
    # factor with a 0.55 floor, so defects discount rather than accidentally
    # boosting small-but-clean crops, and no one defect can zero the sample.
    reliability = 0.55 + 0.15 * sharpness + 0.20 * exposure + 0.10 * clipping
    combined = size if mode == "area" else size * reliability
    return CropQuality(
        round(size, 4), round(sharpness, 4), round(exposure, 4), round(clipping, 4),
        round(float(np.clip(combined, 0, 1)), 4), round(laplacian_variance, 3),
        round(dark_ratio, 4), round(bright_ratio, 4),
    )
