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

SUPPORTED_COLOURS = frozenset({
    "white", "black", "silver/gray", "red", "orange", "yellow",
    "green", "blue", "purple", "brown/beige",
})


@dataclass(frozen=True)
class ColourEvidence:
    weights: dict[str, float]
    total_weight: float
    winner: str
    winner_share: float
    runner_up: str
    runner_up_share: float
    winner_margin: float


@dataclass(frozen=True)
class ColourDecision:
    finalized: bool
    label: str
    confidence: float
    reason: str
    evidence: ColourEvidence


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


def make_colour_observation(
    label: str,
    confidence: float,
    quality: float,
    diagnostics: dict | None = None,
) -> ColourObservation:
    valid = (
        label in SUPPORTED_COLOURS
        and math.isfinite(confidence)
        and 0.0 < confidence <= 1.0
        and math.isfinite(quality)
        and 0.0 < quality <= 1.0
    )
    weight = confidence * quality if valid else 0.0
    return ColourObservation(
        label=label,
        confidence=round(float(confidence), 4) if math.isfinite(confidence) else 0.0,
        quality=round(float(quality), 4) if math.isfinite(quality) else 0.0,
        weight=round(float(weight), 6),
        valid=valid,
        diagnostics=diagnostics or {},
    )


def aggregate_colour_observations(samples: list[ColourObservation]) -> ColourEvidence:
    weights: dict[str, float] = {}
    for sample in samples:
        if sample.valid:
            weights[sample.label] = weights.get(sample.label, 0.0) + sample.weight

    total = sum(weights.values())
    if total <= 0:
        return ColourEvidence({}, 0.0, "unknown", 0.0, "unknown", 0.0, 0.0)

    ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    winner, winner_weight = ranked[0]
    runner_up, runner_weight = ranked[1] if len(ranked) > 1 else ("unknown", 0.0)
    winner_share = winner_weight / total
    runner_up_share = runner_weight / total
    return ColourEvidence(
        weights={label: round(weight, 6) for label, weight in weights.items()},
        total_weight=round(total, 6),
        winner=winner,
        winner_share=round(winner_share, 6),
        runner_up=runner_up,
        runner_up_share=round(runner_up_share, 6),
        winner_margin=round(winner_share - runner_up_share, 6),
    )


def decide_colour(
    samples: list[ColourObservation],
    *,
    finalizing: bool = False,
) -> ColourDecision:
    """Evaluate online locking or track-finalization rules for valid samples."""
    evidence = aggregate_colour_observations(samples)
    count = len(samples)

    if finalizing:
        if count <= 2:
            return ColourDecision(True, "unknown", evidence.winner_share, "short_unknown", evidence)
        if count <= 4:
            passed = (
                evidence.winner_share >= cfg.SHORT_TRACK_WINNER_SHARE
                and evidence.winner_margin >= cfg.SHORT_TRACK_WINNER_MARGIN
            )
            reason = "short_lock" if passed else "short_unknown"
            return ColourDecision(True, evidence.winner if passed else "unknown",
                                  evidence.winner_share, reason, evidence)
        passed = (
            evidence.winner_share >= cfg.FINAL_WINNER_SHARE
            and evidence.winner_margin >= cfg.FINAL_WINNER_MARGIN
        )
        reason = "final_lock" if passed else "final_unknown"
        return ColourDecision(True, evidence.winner if passed else "unknown",
                              evidence.winner_share, reason, evidence)

    if count >= cfg.MAX_VALID_COLOR_SAMPLES:
        passed = (
            evidence.winner_share >= cfg.FINAL_WINNER_SHARE
            and evidence.winner_margin >= cfg.FINAL_WINNER_MARGIN
        )
        reason = "max_lock" if passed else "max_unknown"
        return ColourDecision(True, evidence.winner if passed else "unknown",
                              evidence.winner_share, reason, evidence)

    if count >= cfg.MIN_VALID_COLOR_SAMPLES:
        passed = (
            evidence.winner_share >= cfg.EARLY_LOCK_WINNER_SHARE
            and evidence.winner_margin >= cfg.EARLY_LOCK_MARGIN
        )
        if passed:
            return ColourDecision(True, evidence.winner, evidence.winner_share,
                                  "early_lock", evidence)

    return ColourDecision(False, "unknown", evidence.winner_share, "pending", evidence)
