"""Fixed colour, shape, and common evaluation helpers for plate categories."""

from __future__ import annotations

import math
from typing import Any, Sequence

import cv2
import numpy as np

from plate_variants import TIE_TOLERANCE, VariantExperimentError, same_group


HSV_BINS = (18, 8, 8)
HSV_RANGES = ((0, 180), (0, 256), (0, 256))
RED_HUE_INTERVALS = ((0, 10), (170, 179))
RED_MIN_SATURATION = 100
RED_MIN_VALUE = 50
MIN_GROUP_FRACTION = 0.005
SHAPE_CANVAS_SIZE = 256
SHAPE_CONTENT_LONG_EDGE = 224
ADAPTIVE_BLOCK_SIZE = 31
ADAPTIVE_C = 5
CANNY_THRESHOLDS = (50, 150)


def l1_histogram(hsv: np.ndarray, mask: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    if hsv.ndim != 3 or hsv.shape[2] != 3:
        raise VariantExperimentError("HSV histogram input must be HxWx3.")
    if mask is not None:
        if mask.shape != hsv.shape[:2]:
            raise VariantExperimentError("Histogram mask shape does not match the image.")
        pixel_count = int(np.count_nonzero(mask))
    else:
        pixel_count = int(hsv.shape[0] * hsv.shape[1])
    histogram = cv2.calcHist(
        [hsv], [0, 1, 2], mask, list(HSV_BINS), [0, 180, 0, 256, 0, 256]
    ).astype(np.float64).ravel()
    total = float(histogram.sum())
    if total > 0:
        histogram /= total
    return histogram, pixel_count


def hellinger_distance(first: np.ndarray, second: np.ndarray) -> float:
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    if first.shape != second.shape or np.any(first < 0) or np.any(second < 0):
        raise VariantExperimentError("Hellinger inputs must be matching nonnegative vectors.")
    first_sum, second_sum = float(first.sum()), float(second.sum())
    if first_sum <= 0 or second_sum <= 0:
        raise VariantExperimentError("Hellinger distance requires nonempty normalized histograms.")
    p, q = first / first_sum, second / second_sum
    coefficient = float(np.sum(np.sqrt(p * q)))
    return float(math.sqrt(max(0.0, 1.0 - min(1.0, coefficient))))


def colour_descriptor(image_bgr: np.ndarray) -> dict[str, Any]:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    threshold, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright_mask = np.where(gray > threshold, 255, 0).astype(np.uint8)
    dark_mask = np.where(gray <= threshold, 255, 0).astype(np.uint8)
    full_histogram, total_pixels = l1_histogram(hsv)
    bright_histogram, bright_pixels = l1_histogram(hsv, bright_mask)
    dark_histogram, dark_pixels = l1_histogram(hsv, dark_mask)
    minimum_pixels = max(1, int(math.ceil(total_pixels * MIN_GROUP_FRACTION)))
    hue, saturation, value = cv2.split(hsv)
    red_mask = (
        (((hue >= RED_HUE_INTERVALS[0][0]) & (hue <= RED_HUE_INTERVALS[0][1]))
         | ((hue >= RED_HUE_INTERVALS[1][0]) & (hue <= RED_HUE_INTERVALS[1][1])))
        & (saturation >= RED_MIN_SATURATION)
        & (value >= RED_MIN_VALUE)
    )
    red_count = int(np.count_nonzero(red_mask))
    width = image_bgr.shape[1]
    edges = (0, width // 3, (2 * width) // 3, width)
    red_regions = {}
    for name, x1, x2 in zip(("left", "centre", "right"), edges[:-1], edges[1:]):
        region_red = int(np.count_nonzero(red_mask[:, x1:x2]))
        region_pixels = int(image_bgr.shape[0] * (x2 - x1))
        red_regions[name] = {
            "x_range": [x1, x2],
            "red_pixel_count": region_red,
            "fraction_of_all_red": None if red_count == 0 else region_red / red_count,
            "regional_red_proportion": 0.0 if region_pixels == 0 else region_red / region_pixels,
        }
    return {
        "c0_histogram": full_histogram,
        "c1_bright_histogram": bright_histogram,
        "c1_dark_histogram": dark_histogram,
        "bright_mask": bright_mask,
        "dark_mask": dark_mask,
        "red_mask": (red_mask.astype(np.uint8) * 255),
        "measurements": {
            "otsu_threshold": float(threshold),
            "total_pixels": total_pixels,
            "bright_pixels": bright_pixels,
            "dark_pixels": dark_pixels,
            "bright_proportion": bright_pixels / total_pixels,
            "dark_proportion": dark_pixels / total_pixels,
            "minimum_group_pixels": minimum_pixels,
            "bright_group_valid": bright_pixels >= minimum_pixels,
            "dark_group_valid": dark_pixels >= minimum_pixels,
            "saturated_red_pixels": red_count,
            "saturated_red_fraction": red_count / total_pixels,
            "median_luminance_gray_0_255": float(np.median(gray)),
            "median_saturation_opencv_0_255": float(np.median(saturation)),
            "red_spatial_distribution": red_regions,
        },
    }


def grouped_hellinger(first: dict[str, Any], second: dict[str, Any]) -> float:
    distances = []
    for group in ("bright", "dark"):
        first_valid = bool(first["measurements"][f"{group}_group_valid"])
        second_valid = bool(second["measurements"][f"{group}_group_valid"])
        if first_valid and second_valid:
            distance = hellinger_distance(
                first[f"c1_{group}_histogram"], second[f"c1_{group}_histogram"]
            )
        elif not first_valid and not second_valid:
            distance = 0.0
        else:
            distance = 1.0
        distances.append(distance)
    return float(np.mean(distances))


def colour_matrices(descriptors: Sequence[dict[str, Any]]) -> dict[str, np.ndarray]:
    count = len(descriptors)
    c0 = np.zeros((count, count), dtype=np.float64)
    c1 = np.zeros((count, count), dtype=np.float64)
    for first in range(count):
        for second in range(first + 1, count):
            c0[first, second] = c0[second, first] = hellinger_distance(
                descriptors[first]["c0_histogram"], descriptors[second]["c0_histogram"]
            )
            c1[first, second] = c1[second, first] = grouped_hellinger(
                descriptors[first], descriptors[second]
            )
    return {"c0_hsv_joint": c0, "c1_bright_dark_hsv": c1}


def normalize_shape_views(marker_bgr: np.ndarray) -> dict[str, Any]:
    gray = cv2.cvtColor(marker_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    scale = SHAPE_CONTENT_LONG_EDGE / max(height, width)
    resized_width = max(1, int(math.floor(width * scale + 0.5)))
    resized_height = max(1, int(math.floor(height * scale + 0.5)))
    resized = cv2.resize(gray, (resized_width, resized_height), interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        resized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        ADAPTIVE_BLOCK_SIZE,
        ADAPTIVE_C,
    )

    def foreground_white(binary: np.ndarray) -> tuple[np.ndarray, bool]:
        border = np.concatenate((binary[0], binary[-1], binary[:, 0], binary[:, -1]))
        inverted = float(np.mean(border)) > 127.5
        return (cv2.bitwise_not(binary) if inverted else binary), inverted

    otsu, otsu_inverted = foreground_white(otsu)
    adaptive, adaptive_inverted = foreground_white(adaptive)
    edges = cv2.Canny(otsu, CANNY_THRESHOLDS[0], CANNY_THRESHOLDS[1])
    top = (SHAPE_CANVAS_SIZE - resized_height) // 2
    left = (SHAPE_CANVAS_SIZE - resized_width) // 2
    bottom, right = top + resized_height, left + resized_width

    def canvas(content: np.ndarray) -> np.ndarray:
        output = np.zeros((SHAPE_CANVAS_SIZE, SHAPE_CANVAS_SIZE), dtype=np.uint8)
        output[top:bottom, left:right] = content
        return output

    valid_mask = np.zeros((SHAPE_CANVAS_SIZE, SHAPE_CANVAS_SIZE), dtype=np.uint8)
    valid_mask[top:bottom, left:right] = 255
    return {
        "resized_gray": resized,
        "gray_canvas": canvas(resized),
        "otsu_canvas": canvas(otsu),
        "adaptive_canvas": canvas(adaptive),
        "edge_canvas": canvas(edges),
        "valid_mask": valid_mask,
        "metadata": {
            "source_dimensions_wh": [width, height],
            "scale": scale,
            "resized_dimensions_wh": [resized_width, resized_height],
            "canvas_size": SHAPE_CANVAS_SIZE,
            "content_rectangle_xyxy": [left, top, right, bottom],
            "adaptive": {
                "method": "Gaussian",
                "block_size": ADAPTIVE_BLOCK_SIZE,
                "C": ADAPTIVE_C,
                "applied_after_proportional_resize": True,
            },
            "otsu_polarity_inverted": otsu_inverted,
            "adaptive_polarity_inverted": adaptive_inverted,
            "polarity_rule": "Invert when the resized binary border majority is white; foreground is white on black.",
            "component_filtering": "none; Arabic dots are retained",
            "canny_thresholds": list(CANNY_THRESHOLDS),
        },
    }


def _directional_correlation(
    reference: np.ndarray, query: np.ndarray, reference_mask: np.ndarray
) -> tuple[float | None, str | None]:
    selected = reference_mask.astype(bool)
    first = reference[selected].astype(np.float64)
    second = query[selected].astype(np.float64)
    if first.size < 2:
        return None, "reference scoring mask contains fewer than two pixels"
    first_std, second_std = float(first.std()), float(second.std())
    if first_std <= 1e-12 or second_std <= 1e-12:
        return None, "constant/zero-variance template values under the scoring mask"
    score = float(np.mean((first - first.mean()) * (second - second.mean())) / (first_std * second_std))
    return max(-1.0, min(1.0, score)), None


def symmetric_template_correlation(
    first: dict[str, Any], second: dict[str, Any]
) -> tuple[float | None, str | None]:
    forward, forward_error = _directional_correlation(
        first["adaptive_canvas"], second["adaptive_canvas"], first["valid_mask"]
    )
    reverse, reverse_error = _directional_correlation(
        second["adaptive_canvas"], first["adaptive_canvas"], second["valid_mask"]
    )
    if forward is None or reverse is None:
        return None, "; ".join(error for error in (forward_error, reverse_error) if error)
    return (forward + reverse) / 2.0, None


def symmetric_chamfer_distance(
    first: dict[str, Any], second: dict[str, Any]
) -> tuple[float | None, str | None]:
    first_edges = first["edge_canvas"] > 0
    second_edges = second["edge_canvas"] > 0
    if not np.any(first_edges) or not np.any(second_edges):
        return None, "one or both edge maps are empty"
    distance_to_second = cv2.distanceTransform((~second_edges).astype(np.uint8), cv2.DIST_L2, 3)
    distance_to_first = cv2.distanceTransform((~first_edges).astype(np.uint8), cv2.DIST_L2, 3)
    normalization = math.hypot(SHAPE_CANVAS_SIZE, SHAPE_CANVAS_SIZE)
    distance = (
        float(distance_to_second[first_edges].mean())
        + float(distance_to_first[second_edges].mean())
    ) / (2.0 * normalization)
    return distance, None


def shape_matrices(views: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    count = len(views)
    result = {}
    for method, scorer, diagonal in (
        ("s0_template_correlation", symmetric_template_correlation, 1.0),
        ("s1_symmetric_chamfer", symmetric_chamfer_distance, 0.0),
    ):
        matrix = np.full((count, count), np.nan, dtype=np.float64)
        np.fill_diagonal(matrix, diagonal)
        failures: dict[str, str] = {}
        for first in range(count):
            for second in range(first + 1, count):
                score, error = scorer(views[first], views[second])
                if score is None:
                    failures[f"{first}:{second}"] = error or "undefined score"
                else:
                    matrix[first, second] = matrix[second, first] = score
        result[method] = {"matrix": matrix, "pair_failures": failures}
    return result


def evaluate_leave_one_out(
    records: Sequence[dict[str, Any]],
    matrix: np.ndarray | None,
    higher_is_better: bool,
    unavailable_reason: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for query_index, query in enumerate(records):
        eligible = [
            index
            for index, reference in enumerate(records)
            if index != query_index and not same_group(query, reference)
        ]
        base = {
            "query_id": str(query["id"]),
            "query_category": query["category"],
            "eligible_reference_count": len(eligible),
        }
        if matrix is None:
            rows.append({**base, "valid": False, "failure_reason": unavailable_reason, "nearest_reference_id": None, "predicted_category": None, "nearest_raw_score": None, "best_correct_category_raw_score": None, "best_incorrect_category_raw_score": None, "correct_category_margin": None, "correct": None, "tie_flag": False})
            continue
        valid_eligible = [index for index in eligible if np.isfinite(matrix[query_index, index])]
        if not valid_eligible:
            rows.append({**base, "valid": False, "failure_reason": "no valid eligible pair scores", "nearest_reference_id": None, "predicted_category": None, "nearest_raw_score": None, "best_correct_category_raw_score": None, "best_incorrect_category_raw_score": None, "correct_category_margin": None, "correct": None, "tie_flag": False})
            continue
        scores = [float(matrix[query_index, index]) for index in valid_eligible]
        winning_score = (max if higher_is_better else min)(scores)
        tied = [index for index in valid_eligible if abs(float(matrix[query_index, index]) - winning_score) <= TIE_TOLERANCE]
        winner = min(tied, key=lambda index: int(records[index]["id"]))
        correct_scores = [(float(matrix[query_index, index]), index) for index in valid_eligible if records[index]["category"] == query["category"]]
        incorrect_scores = [(float(matrix[query_index, index]), index) for index in valid_eligible if records[index]["category"] != query["category"]]
        chooser = max if higher_is_better else min
        best_correct = chooser(correct_scores, default=None)
        best_incorrect = chooser(incorrect_scores, default=None)
        margin = None
        if best_correct is not None and best_incorrect is not None:
            margin = best_correct[0] - best_incorrect[0] if higher_is_better else best_incorrect[0] - best_correct[0]
        predicted = records[winner]["category"]
        rows.append({**base, "valid": True, "failure_reason": None, "nearest_reference_id": str(records[winner]["id"]), "predicted_category": predicted, "nearest_raw_score": winning_score, "best_correct_category_raw_score": None if best_correct is None else best_correct[0], "best_incorrect_category_raw_score": None if best_incorrect is None else best_incorrect[0], "correct_category_margin": margin, "correct": predicted == query["category"], "tie_flag": len(tied) > 1})
    return rows

