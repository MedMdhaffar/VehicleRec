"""Dependency-light HSV vehicle-colour estimation with explainable diagnostics."""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from common.config import (
    COLOUR_CONF_FLOOR, COLOUR_REGION_FRAC, COLOUR_SAT_THRESH,
    COLOUR_TOP_TRIM, COLOUR_VAL_BLACK, COLOUR_VAL_WHITE,
)

_HUE_BINS: list[tuple[str, list[tuple[int, int]]]] = [
    ("red", [(0, 10), (170, 180)]), ("orange", [(10, 25)]),
    ("yellow", [(25, 35)]), ("green", [(35, 85)]),
    ("blue", [(85, 130)]), ("purple", [(130, 170)]),
]
_BROWN_VAL_THRESH = 100

SUPPORTED_FEATURE_MODES = frozenset({"hsv", "hsv_gray", "hsv_lab"})

# Auxiliary fusion thresholds use OpenCV's 0..255 grayscale/Lab encoding.  They
# are deliberately few and live separately from the unchanged HSV thresholds.
_GRAY_BLACK_MAX = 85.0
_GRAY_WHITE_MIN = 175.0
_LAB_BLACK_MAX = 90.0
_LAB_WHITE_MIN = 170.0
_LAB_NEUTRAL_CHROMA_MAX = 18.0
_LAB_NEUTRAL_LOW_SAT_MIN = 0.35
_LAB_NEUTRAL_ROI_RATIO = 0.60

# name, x0, y0, x1, y1. Borders, roof and wheel/road strip are intentionally omitted.
PAINT_ROI_SPECS = (
    ("upper_body", 0.18, 0.18, 0.82, 0.52),
    ("left_body", 0.10, 0.34, 0.48, 0.78),
    ("center_body", 0.25, 0.30, 0.75, 0.82),
    ("right_body", 0.52, 0.34, 0.90, 0.78),
    ("lower_middle", 0.18, 0.52, 0.82, 0.88),
)


@dataclass(frozen=True)
class HsvStatistics:
    median_h: float
    median_s: float
    median_v: float
    mean_s: float
    mean_v: float
    v_p10: float
    v_p25: float
    v_p50: float
    v_p75: float
    v_p90: float
    low_saturation_ratio: float
    dark_ratio: float
    bright_ratio: float


@dataclass(frozen=True)
class GrayscaleStatistics:
    mean_intensity: float
    median_intensity: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    dark_pixel_ratio: float
    bright_pixel_ratio: float


@dataclass(frozen=True)
class LabStatistics:
    median_l: float
    median_a: float
    median_b: float
    mean_l: float
    mean_a: float
    mean_b: float
    l_p10: float
    l_p50: float
    l_p90: float


@dataclass(frozen=True)
class RoiEvidence:
    name: str
    coordinates: tuple[int, int, int, int]
    accepted: bool
    rejection_reason: str
    area: int
    very_dark_ratio: float
    very_bright_ratio: float
    edge_density: float
    colour_variance: float
    roi_quality: float
    label: str
    confidence: float
    weight: float
    hsv_statistics: HsvStatistics | None
    grayscale_statistics: GrayscaleStatistics | None = None
    lab_statistics: LabStatistics | None = None
    hsv_label: str = "unknown"
    hsv_confidence: float = 0.0
    fusion_reason: str = ""


@dataclass(frozen=True)
class ColourEstimate:
    label: str
    confidence: float
    roi_mode: str
    normalization: str
    neutral_mode: str
    rois: tuple[RoiEvidence, ...]
    # True when the adaptive-neutral consensus below produced this label instead
    # of the weighted ROI vote. Such a result is "black" by rule, not by hue, so
    # a very dark blue/navy car lands here with a perfect score — downstream
    # consumers need to tell the two apart.
    neutral_override: bool = False
    feature_mode: str = "hsv"
    fusion_reason: str = ""

    def diagnostics(self) -> dict:
        return asdict(self)


def normalize_illumination(image: np.ndarray, method: str = "none") -> np.ndarray:
    """Return a shape/dtype-preserving, hue-conservative normalization."""
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("colour input must be an HxWx3 NumPy array")
    if image.dtype != np.uint8:
        raise ValueError("colour input must use uint8 pixels")
    if image.size == 0 or method == "none":
        return image
    if method == "gray_world":
        values = image.astype(np.float32)
        means = values.reshape(-1, 3).mean(axis=0)
        target = float(means.mean())
        scales = np.divide(target, means, out=np.ones(3, np.float32), where=means > 1.0)
        scales = np.clip(scales, 0.75, 1.25)
        return np.clip(values * scales, 0, 255).astype(np.uint8)
    if method == "gamma":
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        value = hsv[:, :, 2]
        median = float(np.median(value))
        if median <= 1.0:
            return image.copy()
        gamma = float(np.clip(math.log(0.5) / math.log(median / 255.0), 0.75, 1.35))
        table = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        hsv[:, :, 2] = cv2.LUT(value, table)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    raise ValueError(f"unsupported colour normalization: {method}")


def _statistics(hsv: np.ndarray) -> HsvStatistics:
    pixels = hsv.reshape(-1, 3).astype(np.float32)
    h, s, v = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    pct = np.percentile(v, [10, 25, 50, 75, 90])
    return HsvStatistics(
        median_h=round(float(np.median(h)), 3), median_s=round(float(np.median(s)), 3),
        median_v=round(float(np.median(v)), 3), mean_s=round(float(np.mean(s)), 3),
        mean_v=round(float(np.mean(v)), 3), v_p10=round(float(pct[0]), 3),
        v_p25=round(float(pct[1]), 3), v_p50=round(float(pct[2]), 3),
        v_p75=round(float(pct[3]), 3), v_p90=round(float(pct[4]), 3),
        low_saturation_ratio=round(float(np.mean(s < COLOUR_SAT_THRESH)), 4),
        dark_ratio=round(float(np.mean(v < 45)), 4),
        bright_ratio=round(float(np.mean(v > 235)), 4),
    )


def _grayscale_statistics(region: np.ndarray) -> GrayscaleStatistics:
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)
    pct = np.percentile(gray, [10, 25, 50, 75, 90])
    return GrayscaleStatistics(
        mean_intensity=round(float(np.mean(gray)), 3),
        median_intensity=round(float(np.median(gray)), 3),
        p10=round(float(pct[0]), 3), p25=round(float(pct[1]), 3),
        p50=round(float(pct[2]), 3), p75=round(float(pct[3]), 3),
        p90=round(float(pct[4]), 3),
        dark_pixel_ratio=round(float(np.mean(gray < 45)), 4),
        bright_pixel_ratio=round(float(np.mean(gray > 235)), 4),
    )


def _lab_statistics(region: np.ndarray) -> LabStatistics:
    lab = cv2.cvtColor(region, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    l_channel = lab[:, 0]
    l_pct = np.percentile(l_channel, [10, 50, 90])
    medians = np.median(lab, axis=0)
    means = np.mean(lab, axis=0)
    return LabStatistics(
        median_l=round(float(medians[0]), 3),
        median_a=round(float(medians[1]), 3),
        median_b=round(float(medians[2]), 3),
        mean_l=round(float(means[0]), 3),
        mean_a=round(float(means[1]), 3),
        mean_b=round(float(means[2]), 3),
        l_p10=round(float(l_pct[0]), 3),
        l_p50=round(float(l_pct[1]), 3),
        l_p90=round(float(l_pct[2]), 3),
    )


def _resolve_feature_mode(mode: str | None) -> str:
    resolved = (mode or os.environ.get("COLOR_FEATURE_MODE", "hsv")).strip().lower()
    if resolved not in SUPPORTED_FEATURE_MODES:
        raise ValueError(
            f"unsupported colour feature mode {resolved!r}; "
            f"expected one of {sorted(SUPPORTED_FEATURE_MODES)}"
        )
    return resolved


def _neutral_label(luminance: float, *, black_max: float, white_min: float) -> str:
    if luminance < black_max:
        return "black"
    if luminance >= white_min:
        return "white"
    return "silver/gray"


def fuse_roi_colour(
    hsv_label: str,
    hsv_confidence: float,
    hsv_statistics: HsvStatistics,
    *,
    grayscale_statistics: GrayscaleStatistics | None = None,
    lab_statistics: LabStatistics | None = None,
    feature_mode: str = "hsv",
) -> tuple[str, float, str]:
    """Fuse one ROI without changing the HSV-only path.

    Luminance only refines already-neutral HSV evidence.  Lab additionally uses
    the sign of the b axis for the red/purple boundary.  Cross-ROI Lab neutral
    consensus is handled separately by :func:`fuse_colour_evidence`.
    """
    mode = _resolve_feature_mode(feature_mode)
    if mode == "hsv":
        return hsv_label, hsv_confidence, ""

    if hsv_label in {"black", "silver/gray", "white"} \
            and hsv_statistics.low_saturation_ratio >= 0.50:
        if mode == "hsv_gray" and grayscale_statistics is not None:
            label = _neutral_label(
                grayscale_statistics.median_intensity,
                black_max=_GRAY_BLACK_MAX,
                white_min=_GRAY_WHITE_MIN,
            )
            return label, hsv_confidence, "grayscale_neutral_luminance"
        if mode == "hsv_lab" and lab_statistics is not None:
            label = _neutral_label(
                lab_statistics.median_l,
                black_max=_LAB_BLACK_MAX,
                white_min=_LAB_WHITE_MIN,
            )
            return label, hsv_confidence, "lab_neutral_luminance"

    if mode == "hsv_lab" and lab_statistics is not None:
        # OpenCV Lab b is centred at 128: red paint is strongly positive while
        # magenta/purple is strongly negative.  Restrict this to the adjacent
        # HSV red/purple classes and require substantial a-axis chroma.
        if hsv_label == "purple" and lab_statistics.median_a >= 150 \
                and lab_statistics.median_b >= 145:
            return "red", hsv_confidence, "lab_red_purple_chroma"
        if hsv_label == "red" and lab_statistics.median_a >= 150 \
                and lab_statistics.median_b <= 105:
            return "purple", hsv_confidence, "lab_red_purple_chroma"

    return hsv_label, hsv_confidence, ""


def fuse_colour_evidence(
    rois: list[RoiEvidence] | tuple[RoiEvidence, ...],
    hsv_label: str,
    hsv_confidence: float,
    *,
    feature_mode: str,
) -> tuple[str, float, str]:
    """Apply one cross-ROI auxiliary rule after the normal weighted HSV vote."""
    mode = _resolve_feature_mode(feature_mode)
    if mode != "hsv_lab":
        return hsv_label, hsv_confidence, ""
    accepted = [
        roi for roi in rois
        if roi.accepted and roi.hsv_statistics is not None and roi.lab_statistics is not None
    ]
    if len(accepted) < 3:
        return hsv_label, hsv_confidence, ""
    neutral_like = [
        roi for roi in accepted
        if roi.hsv_statistics.low_saturation_ratio >= _LAB_NEUTRAL_LOW_SAT_MIN
        and math.hypot(
            roi.lab_statistics.median_a - 128.0,
            roi.lab_statistics.median_b - 128.0,
        ) <= _LAB_NEUTRAL_CHROMA_MAX
    ]
    required = max(3, math.ceil(len(accepted) * _LAB_NEUTRAL_ROI_RATIO))
    if len(neutral_like) < required:
        return hsv_label, hsv_confidence, ""

    median_l = float(np.median([roi.lab_statistics.median_l for roi in neutral_like]))
    maximum_l = max(roi.lab_statistics.median_l for roi in neutral_like)
    label = (
        "white" if maximum_l >= _LAB_WHITE_MIN
        else _neutral_label(median_l, black_max=_LAB_BLACK_MAX, white_min=_LAB_WHITE_MIN)
    )
    return label, round(len(neutral_like) / len(accepted), 4), "lab_neutral_roi_consensus"


def _classify_hsv(region: np.ndarray, neutral_mode: str) -> tuple[str, float, HsvStatistics]:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3).astype(np.int32)
    hue, sat, val = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    stats = _statistics(hsv)
    ach_mask = sat < COLOUR_SAT_THRESH
    bins: dict[str, int] = {}
    ach_val = val[ach_mask]
    if len(ach_val):
        # Calibrated from Stage-1 MP4 ROI diagnostics: black ROI median-V p90
        # was 61, while silver/gray median-V p10 was 94. Their gap midpoint is
        # 77.5, hence 78. White showed no evidence supporting a boundary change.
        black_limit = 78 if neutral_mode == "adaptive" else COLOUR_VAL_BLACK
        white_limit = COLOUR_VAL_WHITE
        bins["white"] = int(np.sum(ach_val >= white_limit))
        bins["black"] = int(np.sum(ach_val < black_limit))
        bins["silver/gray"] = len(ach_val) - bins["white"] - bins["black"]
    chr_mask = ~ach_mask
    chr_hue, chr_val = hue[chr_mask], val[chr_mask]
    for colour_name, ranges in _HUE_BINS:
        mask = np.zeros(len(chr_hue), dtype=bool)
        for lo, hi in ranges:
            mask |= (chr_hue >= lo) & (chr_hue < hi)
        count = int(np.sum(mask))
        if not count:
            continue
        if colour_name == "orange":
            dark = int(np.sum(mask & (chr_val < _BROWN_VAL_THRESH)))
            bins["brown/beige"] = bins.get("brown/beige", 0) + dark
            bins["orange"] = count - dark
        else:
            bins[colour_name] = count
    if not bins or len(pixels) == 0:
        return "unknown", 0.0, stats
    dominant = max(bins, key=bins.get)
    confidence = round(bins[dominant] / len(pixels), 4)
    return (dominant if confidence >= COLOUR_CONF_FLOOR else "unknown"), confidence, stats


def _roi_metrics(region: np.ndarray) -> tuple[float, float, float, float, float]:
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    dark = float(np.mean(gray < 8))
    bright = float(np.mean(gray > 247))
    edge_density = float(np.mean(cv2.Canny(gray, 60, 150) > 0))
    variance = float(np.mean(np.var(region.astype(np.float32), axis=(0, 1))))
    exposure = 1.0 - min(0.65, max(dark, bright))
    texture = 1.0 - min(0.45, edge_density)
    variance_quality = 1.0 if variance <= 5000 else max(0.55, 1.0 - (variance - 5000) / 15000)
    quality = float(np.clip(0.45 * exposure + 0.35 * texture + 0.20 * variance_quality, 0, 1))
    return dark, bright, edge_density, variance, quality


def extract_paint_rois(crop: np.ndarray) -> list[RoiEvidence]:
    """Generate bounded paint candidates, with a guaranteed center fallback."""
    if not isinstance(crop, np.ndarray) or crop.ndim != 3 or crop.shape[2] != 3 or crop.size == 0:
        return []
    h, w = crop.shape[:2]
    candidates: list[RoiEvidence] = []
    for name, rx0, ry0, rx1, ry1 in PAINT_ROI_SPECS:
        x0, y0 = max(0, min(w - 1, int(w * rx0))), max(0, min(h - 1, int(h * ry0)))
        x1, y1 = max(x0 + 1, min(w, math.ceil(w * rx1))), max(y0 + 1, min(h, math.ceil(h * ry1)))
        region = crop[y0:y1, x0:x1]
        area = int(region.shape[0] * region.shape[1])
        if region.size:
            dark, bright, edges, variance, quality = _roi_metrics(region)
        else:
            dark = bright = edges = variance = quality = 0.0
        reason = ""
        if min(region.shape[:2], default=0) < 3 or area < 16:
            reason = "too_small"
        elif bright > 0.985:
            reason = "nearly_all_clipped_bright"
        elif edges > 0.75:
            reason = "extreme_edge_density"
        candidates.append(RoiEvidence(name, (x0, y0, x1, y1), not reason, reason, area,
            round(dark, 4), round(bright, 4), round(edges, 4), round(variance, 3),
            round(quality, 4), "unknown", 0.0, 0.0, None))
    if candidates and not any(item.accepted for item in candidates):
        index = next((i for i, item in enumerate(candidates) if item.name == "center_body"), 0)
        old = candidates[index]
        candidates[index] = RoiEvidence(old.name, old.coordinates, True, "fallback", old.area,
            old.very_dark_ratio, old.very_bright_ratio, old.edge_density, old.colour_variance,
            max(0.1, old.roi_quality), old.label, old.confidence, old.weight, old.hsv_statistics)
    return candidates


def estimate_colour_detailed(crop_bgr: np.ndarray, *, roi_mode: str | None = None,
    normalization: str | None = None, neutral_mode: str | None = None,
    feature_mode: str | None = None) -> ColourEstimate:
    roi_mode = roi_mode or os.environ.get("COLOR_ROI_MODE", "multi")
    normalization = normalization or os.environ.get("COLOR_NORMALIZATION", "none")
    neutral_mode = neutral_mode or os.environ.get("COLOR_NEUTRAL_MODE", "adaptive")
    feature_mode = _resolve_feature_mode(feature_mode)
    if not isinstance(crop_bgr, np.ndarray) or crop_bgr.ndim != 3 or crop_bgr.shape[-1] != 3 \
            or crop_bgr.dtype != np.uint8 or crop_bgr.size == 0:
        return ColourEstimate(
            "unknown", 0.0, roi_mode, normalization, neutral_mode, (),
            feature_mode=feature_mode,
        )
    normalized = normalize_illumination(crop_bgr, normalization)
    h, w = normalized.shape[:2]
    if roi_mode == "legacy":
        trim = int(h * COLOUR_TOP_TRIM)
        avail_h = h - trim
        y0 = trim + int(avail_h * (1 - COLOUR_REGION_FRAC) / 2)
        y1 = trim + int(avail_h * (1 + COLOUR_REGION_FRAC) / 2)
        x0 = int(w * (1 - COLOUR_REGION_FRAC) / 2)
        x1 = w - x0
        region = normalized[y0:y1, x0:x1]
        if region.size == 0:
            return ColourEstimate(
                "unknown", 0.0, roi_mode, normalization, neutral_mode, (),
                feature_mode=feature_mode,
            )
        hsv_label, hsv_confidence, stats = _classify_hsv(region, neutral_mode)
        gray_stats = _grayscale_statistics(region) if feature_mode != "hsv" else None
        lab_stats = _lab_statistics(region) if feature_mode == "hsv_lab" else None
        label, confidence, fusion_reason = fuse_roi_colour(
            hsv_label, hsv_confidence, stats,
            grayscale_statistics=gray_stats, lab_statistics=lab_stats,
            feature_mode=feature_mode,
        )
        roi = RoiEvidence("legacy_center", (x0, y0, x1, y1), True, "", region.shape[0] * region.shape[1],
            0.0, 0.0, 0.0, 0.0, 1.0, label, confidence, confidence, stats,
            gray_stats, lab_stats, hsv_label, hsv_confidence, fusion_reason)
        return ColourEstimate(
            label, confidence, roi_mode, normalization, neutral_mode, (roi,),
            feature_mode=feature_mode, fusion_reason=fusion_reason,
        )
    if roi_mode != "multi":
        raise ValueError(f"unsupported colour ROI mode: {roi_mode}")
    raw_rois = extract_paint_rois(normalized)
    results: list[RoiEvidence] = []
    weights: dict[str, float] = {}
    for roi in raw_rois:
        label, confidence, stats, weight = "unknown", 0.0, None, 0.0
        hsv_label, hsv_confidence = "unknown", 0.0
        gray_stats = None
        lab_stats = None
        fusion_reason = ""
        if roi.accepted:
            x0, y0, x1, y1 = roi.coordinates
            region = normalized[y0:y1, x0:x1]
            hsv_label, hsv_confidence, stats = _classify_hsv(region, neutral_mode)
            gray_stats = _grayscale_statistics(region) if feature_mode != "hsv" else None
            lab_stats = _lab_statistics(region) if feature_mode == "hsv_lab" else None
            label, confidence, fusion_reason = fuse_roi_colour(
                hsv_label, hsv_confidence, stats,
                grayscale_statistics=gray_stats, lab_statistics=lab_stats,
                feature_mode=feature_mode,
            )
            if label != "unknown":
                weight = confidence * roi.roi_quality
                weights[label] = weights.get(label, 0.0) + weight
        results.append(RoiEvidence(roi.name, roi.coordinates, roi.accepted, roi.rejection_reason, roi.area,
            roi.very_dark_ratio, roi.very_bright_ratio, roi.edge_density, roi.colour_variance,
            roi.roi_quality, label, confidence, round(weight, 6), stats,
            gray_stats, lab_stats, hsv_label, hsv_confidence, fusion_reason))
    if neutral_mode == "adaptive":
        accepted_stats = [roi.hsv_statistics for roi in results if roi.accepted and roi.hsv_statistics]
        neutral_dark = [
            stats for stats in accepted_stats
            if stats.median_s < COLOUR_SAT_THRESH and stats.median_v < 105
        ]
        # Stage-2 video calibration: visually black tracks 5/7 had four such
        # ROIs, while the dark-blue reference track 1 had none.
        if len(neutral_dark) >= 3:
            confidence = round(len(neutral_dark) / len(accepted_stats), 4)
            return ColourEstimate("black", confidence, roi_mode, normalization,
                                  neutral_mode, tuple(results), neutral_override=True,
                                  feature_mode=feature_mode)
    total = sum(weights.values())
    if total <= 0:
        return ColourEstimate(
            "unknown", 0.0, roi_mode, normalization, neutral_mode, tuple(results),
            feature_mode=feature_mode,
        )
    label, winner_weight = max(weights.items(), key=lambda item: item[1])
    confidence = round(winner_weight / total, 4)
    label, confidence, fusion_reason = fuse_colour_evidence(
        results, label, confidence, feature_mode=feature_mode,
    )
    return ColourEstimate(
        label, confidence, roi_mode, normalization, neutral_mode, tuple(results),
        feature_mode=feature_mode, fusion_reason=fusion_reason,
    )


def estimate_colour(crop_bgr: np.ndarray) -> tuple[str, float]:
    """Backward-compatible public `(label, confidence)` entry point."""
    result = estimate_colour_detailed(crop_bgr)
    return result.label, result.confidence
