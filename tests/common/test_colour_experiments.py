from __future__ import annotations

import numpy as np
import pytest

from common.colour import (
    GrayscaleStatistics,
    HsvStatistics,
    estimate_colour_detailed,
    extract_paint_rois,
    fuse_roi_colour,
    normalize_illumination,
)


def test_paint_roi_coordinates_stay_inside_crop_and_labels_are_supported():
    crop = np.full((40, 80, 3), (20, 20, 20), dtype=np.uint8)
    rois = extract_paint_rois(crop)
    assert rois
    for roi in rois:
        x0, y0, x1, y1 = roi.coordinates
        assert 0 <= x0 < x1 <= 80
        assert 0 <= y0 < y1 <= 40
    result = estimate_colour_detailed(crop, roi_mode="multi", neutral_mode="adaptive")
    assert result.label == "black"


def test_tiny_rois_do_not_crash_and_have_a_fallback_when_rejected():
    tiny = np.zeros((2, 2, 3), dtype=np.uint8)
    rois = extract_paint_rois(tiny)
    assert rois
    assert any(roi.accepted and roi.rejection_reason == "fallback" for roi in rois)
    assert estimate_colour_detailed(tiny, roi_mode="multi").label in {
        "black", "silver/gray", "white", "unknown"
    }


def test_candidate_rejection_rejects_clipped_bright_region():
    bright = np.full((30, 30, 3), 255, dtype=np.uint8)
    rois = extract_paint_rois(bright)
    assert any(not roi.accepted and roi.rejection_reason == "nearly_all_clipped_bright" for roi in rois)
    assert any(roi.accepted for roi in rois)  # deterministic center fallback


@pytest.mark.parametrize("value, expected", [(30, "black"), (125, "silver/gray"), (220, "white")])
def test_calibrated_neutral_boundaries(value, expected):
    crop = np.full((30, 30, 3), value, dtype=np.uint8)
    assert estimate_colour_detailed(crop, roi_mode="legacy", neutral_mode="adaptive").label == expected


def test_neutral_change_does_not_remap_chromatic_red():
    red = np.full((30, 30, 3), (0, 0, 255), dtype=np.uint8)
    assert estimate_colour_detailed(red, roi_mode="legacy", neutral_mode="adaptive").label == "red"


def test_dark_saturated_blue_is_not_remapped_by_neutral_consensus():
    blue = np.full((50, 80, 3), (70, 20, 10), dtype=np.uint8)
    assert estimate_colour_detailed(blue, roi_mode="multi", neutral_mode="adaptive").label == "blue"


def test_illumination_modes_preserve_shape_dtype_and_none_identity():
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    assert normalize_illumination(image, "none") is image
    for method in ("gray_world", "gamma"):
        result = normalize_illumination(image, method)
        assert result.shape == image.shape
        assert result.dtype == image.dtype


@pytest.mark.parametrize("bad", [np.zeros((3, 3), dtype=np.uint8), np.zeros((3, 3, 3), dtype=np.float32)])
def test_illumination_invalid_inputs_are_safe(bad):
    with pytest.raises(ValueError):
        normalize_illumination(bad, "none")


# ── Adaptive-neutral override flag ────────────────────────────────────────────
# The override returns "black" by rule rather than by hue, so a very dark navy
# car lands there with a perfect score. The flag lets consumers tell the two
# apart; it never changes the label or the confidence.

def test_neutral_override_flag_set_when_consensus_fires():
    dark = np.full((120, 120, 3), 40, dtype=np.uint8)
    result = estimate_colour_detailed(dark, roi_mode="multi", neutral_mode="adaptive")
    assert result.label == "black"
    assert result.neutral_override is True


@pytest.mark.parametrize("channel,expected", [(0, "blue"), (2, "red")])
def test_neutral_override_flag_clear_for_chromatic_colours(channel, expected):
    crop = np.zeros((120, 120, 3), dtype=np.uint8)
    crop[:, :, channel] = 200
    result = estimate_colour_detailed(crop, roi_mode="multi", neutral_mode="adaptive")
    assert result.label == expected
    assert result.neutral_override is False


def test_legacy_neutral_mode_never_sets_the_override_flag():
    """Only the adaptive consensus path can set it."""
    dark = np.full((120, 120, 3), 40, dtype=np.uint8)
    result = estimate_colour_detailed(dark, roi_mode="multi", neutral_mode="legacy")
    assert result.neutral_override is False


def test_neutral_override_flag_defaults_false_and_is_exposed():
    crop = np.full((120, 120, 3), 200, dtype=np.uint8)
    result = estimate_colour_detailed(crop, roi_mode="multi", neutral_mode="adaptive")
    assert result.neutral_override is False
    assert result.diagnostics()["neutral_override"] is False


def _hsv_stats(*, median_s=20, median_v=125, low_saturation_ratio=0.9):
    return HsvStatistics(
        median_h=0, median_s=median_s, median_v=median_v,
        mean_s=median_s, mean_v=median_v,
        v_p10=median_v, v_p25=median_v, v_p50=median_v,
        v_p75=median_v, v_p90=median_v,
        low_saturation_ratio=low_saturation_ratio,
        dark_ratio=0, bright_ratio=0,
    )


def _gray_stats(value):
    return GrayscaleStatistics(
        mean_intensity=value, median_intensity=value,
        p10=value, p25=value, p50=value, p75=value, p90=value,
        dark_pixel_ratio=float(value < 45), bright_pixel_ratio=float(value > 235),
    )


def test_dark_low_saturation_grayscale_favours_black_over_silver():
    label, _confidence, reason = fuse_roi_colour(
        "silver/gray", 0.8, _hsv_stats(),
        grayscale_statistics=_gray_stats(55), feature_mode="hsv_gray",
    )
    assert label == "black"
    assert reason == "grayscale_neutral_luminance"


def test_bright_low_saturation_grayscale_favours_white():
    label, _confidence, _reason = fuse_roi_colour(
        "silver/gray", 0.8, _hsv_stats(),
        grayscale_statistics=_gray_stats(220), feature_mode="hsv_gray",
    )
    assert label == "white"


def test_middle_luminance_remains_silver_gray():
    label, _confidence, _reason = fuse_roi_colour(
        "silver/gray", 0.8, _hsv_stats(),
        grayscale_statistics=_gray_stats(125), feature_mode="hsv_gray",
    )
    assert label == "silver/gray"


def test_lab_chroma_can_correct_hsv_purple_toward_red():
    # This red-magenta sample falls just inside HSV's purple bin, while positive
    # Lab b supplies independent evidence that it is on the red side.
    red_magenta = np.full((80, 120, 3), (100, 0, 255), dtype=np.uint8)
    hsv = estimate_colour_detailed(red_magenta, roi_mode="multi", feature_mode="hsv")
    fused = estimate_colour_detailed(red_magenta, roi_mode="multi", feature_mode="hsv_lab")
    assert hsv.label == "purple"
    assert fused.label == "red"
    assert any(roi.fusion_reason == "lab_red_purple_chroma" for roi in fused.rois)


def test_clean_blue_does_not_become_neutral_in_lab_mode():
    blue = np.full((80, 120, 3), (210, 70, 30), dtype=np.uint8)
    result = estimate_colour_detailed(blue, roi_mode="multi", feature_mode="hsv_lab")
    assert result.label == "blue"
    assert result.fusion_reason == ""


@pytest.mark.parametrize("mode", ["hsv", "hsv_gray", "hsv_lab"])
def test_feature_modes_are_deterministic(mode):
    crop = np.full((80, 120, 3), (110, 105, 100), dtype=np.uint8)
    assert estimate_colour_detailed(crop, feature_mode=mode) == \
        estimate_colour_detailed(crop, feature_mode=mode)


def test_hsv_feature_mode_is_backward_compatible_and_has_no_auxiliary_cost():
    crop = np.full((80, 120, 3), (200, 80, 40), dtype=np.uint8)
    default = estimate_colour_detailed(crop, roi_mode="multi")
    explicit = estimate_colour_detailed(crop, roi_mode="multi", feature_mode="hsv")
    assert default == explicit
    assert all(roi.grayscale_statistics is None for roi in explicit.rois)
    assert all(roi.lab_statistics is None for roi in explicit.rois)


def test_auxiliary_statistics_are_exposed_for_every_accepted_roi():
    crop = np.full((80, 120, 3), (130, 120, 110), dtype=np.uint8)
    gray = estimate_colour_detailed(crop, feature_mode="hsv_gray")
    lab = estimate_colour_detailed(crop, feature_mode="hsv_lab")
    assert all(roi.grayscale_statistics is not None for roi in gray.rois if roi.accepted)
    assert all(roi.grayscale_statistics is not None for roi in lab.rois if roi.accepted)
    assert all(roi.lab_statistics is not None for roi in lab.rois if roi.accepted)
