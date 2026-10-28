"""Manifest, manual geometry, P0-P4 preparation, and evaluation helpers."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np


VARIANTS = ("p0", "p1", "p2", "p3", "p4")  # Historical manual-geometry names.
CONTROLLED_VARIANTS = ("p0", "grayscale", "clahe")
RECTIFICATION_STATUSES = {"needed", "unnecessary", "unavailable"}
TIE_TOLERANCE = 1e-12


class VariantExperimentError(RuntimeError):
    """A controlled-experiment validation or geometry failure."""


@dataclass
class PreparedVariant:
    name: str
    image_bgr: np.ndarray
    metadata: dict[str, Any]


def prepare_controlled_variants(image_bgr: np.ndarray) -> dict[str, PreparedVariant]:
    """Prepare whole-crop colour, replicated grayscale, and fixed Lab-CLAHE variants."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or image_bgr.dtype != np.uint8:
        raise VariantExperimentError("Controlled variants require a uint8 three-channel BGR image.")
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    grayscale = cv2.merge([gray, gray, gray])
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    luminance, a_channel, b_channel = cv2.split(lab)
    clahe_operator = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced_luminance = clahe_operator.apply(luminance)
    clahe = cv2.cvtColor(
        cv2.merge([enhanced_luminance, a_channel, b_channel]),
        cv2.COLOR_LAB2BGR,
    )
    dimensions = [width, height]
    return {
        "p0": PreparedVariant(
            "p0",
            image_bgr.copy(),
            {
                "description": "unchanged complete colour plate crop",
                "dimensions_wh": dimensions,
                "pixels_cropped": 0,
            },
        ),
        "grayscale": PreparedVariant(
            "grayscale",
            grayscale,
            {
                "description": "whole P0 converted to grayscale and replicated into three channels",
                "conversion": "OpenCV BGR2GRAY then merge(gray, gray, gray)",
                "dimensions_wh": dimensions,
                "pixels_cropped": 0,
                "channels_identical": bool(
                    np.array_equal(grayscale[:, :, 0], grayscale[:, :, 1])
                    and np.array_equal(grayscale[:, :, 1], grayscale[:, :, 2])
                ),
            },
        ),
        "clahe": PreparedVariant(
            "clahe",
            clahe,
            {
                "description": "whole P0 with CLAHE applied only to OpenCV Lab L channel",
                "conversion": "BGR->Lab; CLAHE L only; retain a,b; Lab->BGR",
                "clahe": {"clipLimit": 2.0, "tileGridSize": [4, 4]},
                "dimensions_wh": dimensions,
                "pixels_cropped": 0,
                "colour_note": (
                    "Colour retention is approximate because BGR/Lab conversion and uint8 "
                    "clipping/rounding are not perfectly reversible."
                ),
            },
        ),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VariantExperimentError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VariantExperimentError(f"Expected a JSON object in {path}.")
    return value


def validate_manifest(manifest_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate exact paths, numeric order, dimensions, boxes, and duplicates."""
    manifest = load_json(manifest_path)
    raw = manifest.get("images")
    if not isinstance(raw, list) or not raw:
        raise VariantExperimentError("Manifest 'images' must be a non-empty list.")
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    ids = [str(item.get("id")) for item in raw if isinstance(item, dict)]
    expected_ids = [str(index) for index in range(1, len(raw) + 1)]
    if ids != expected_ids:
        errors.append(f"Manifest IDs must be in numerical order {expected_ids}; got {ids}.")
    hashes: dict[str, list[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            errors.append("Every manifest image entry must be an object.")
            continue
        record = dict(item)
        image_id = str(record.get("id"))
        path = Path(str(record.get("path", "")))
        expected_filename = str(record.get("filename", ""))
        category = str(record.get("category", ""))
        if path.name != expected_filename:
            errors.append(
                f"Image {image_id}: path filename {path.name!r} does not equal "
                f"required filename {expected_filename!r}."
            )
        if path.parent.name != category:
            errors.append(
                f"Image {image_id}: manifest category {category!r} does not match "
                f"the immediate parent directory {path.parent.name!r}."
            )
        record["path"] = path
        record["available"] = False
        if not path.is_file():
            same_stem = sorted(
                str(candidate)
                for candidate in path.parent.glob(f"{path.stem}.*")
                if candidate.is_file()
            )
            alternative = (
                f" A same-stem file exists but was not substituted: {same_stem}."
                if same_stem
                else ""
            )
            errors.append(f"Image {image_id}: required file is missing: {path}.{alternative}")
            records.append(record)
            continue
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            errors.append(f"Image {image_id}: OpenCV could not decode {path}.")
            records.append(record)
            continue
        height, width = image.shape[:2]
        expected = (int(record.get("expected_width", -1)), int(record.get("expected_height", -1)))
        if (width, height) != expected:
            errors.append(
                f"Image {image_id}: decoded {width}x{height}, expected {expected[0]}x{expected[1]}."
            )
        marker = record.get("marker_bbox")
        if marker is not None:
            try:
                x1, y1, x2, y2 = (float(value) for value in marker)
                if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"Image {image_id}: marker_bbox is invalid or out of bounds: {marker!r}.")
        digest = file_sha256(path)
        hashes.setdefault(digest, []).append(image_id)
        record.update(
            {
                "available": True,
                "image": image,
                "decoded_dimensions_wh": [width, height],
                "sha256": digest,
            }
        )
        records.append(record)
    duplicates = [values for values in hashes.values() if len(values) > 1]
    if duplicates:
        errors.extend(f"Exact duplicate files detected for IDs {group}." for group in duplicates)
    report = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": json_sha256(manifest_path),
        "valid": not errors,
        "errors": errors,
        "exact_duplicate_id_groups": duplicates,
        "images": [
            {
                key: (str(value) if isinstance(value, Path) else value)
                for key, value in record.items()
                if key != "image"
            }
            for record in records
        ],
        "physical_independence": (
            "Unverified where physical_plate_id and group_id are null; registration text is metadata "
            "and is not used to infer plate identity."
        ),
    }
    return records, report


def rounded_bbox(bbox: Sequence[float], width: int, height: int) -> list[int]:
    """Convert float xyxy to an enclosing integer, x2/y2-exclusive crop."""
    if len(bbox) != 4:
        raise VariantExperimentError("tight_plate_bbox must contain four values.")
    values = [float(value) for value in bbox]
    if not all(math.isfinite(value) for value in values):
        raise VariantExperimentError("tight_plate_bbox contains a non-finite value.")
    x1, y1, x2, y2 = values
    result = [math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)]
    if not (0 <= result[0] < result[2] <= width and 0 <= result[1] < result[3] <= height):
        raise VariantExperimentError(
            f"Rounded tight_plate_bbox {result} is invalid for {width}x{height}."
        )
    return result


def validate_corners(corners: Sequence[Sequence[float]], width: int, height: int) -> np.ndarray:
    try:
        points = np.asarray(corners, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise VariantExperimentError("plate_corners must be numeric.") from exc
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise VariantExperimentError("plate_corners must be four finite xy points.")
    if np.any(points[:, 0] < 0) or np.any(points[:, 0] > width) or np.any(points[:, 1] < 0) or np.any(points[:, 1] > height):
        raise VariantExperimentError("plate_corners fall outside the original image.")
    if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
        raise VariantExperimentError("plate_corners are not convex in TL,TR,BR,BL order.")
    area = abs(float(cv2.contourArea(points)))
    if area <= 1e-6:
        raise VariantExperimentError("plate_corners have zero area.")
    cross = []
    for index in range(4):
        a, b, c = points[index], points[(index + 1) % 4], points[(index + 2) % 4]
        first_edge = b - a
        second_edge = c - b
        cross.append(float(first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0]))
    if not (all(value > 0 for value in cross) or all(value < 0 for value in cross)):
        raise VariantExperimentError("plate_corners have inconsistent winding/order.")
    return points


def validate_annotations(
    annotation_path: Path, records: Sequence[dict[str, Any]], require_complete: bool = True
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    document = load_json(annotation_path)
    raw = document.get("images", {})
    if not isinstance(raw, dict):
        raise VariantExperimentError("Annotation 'images' must be an object keyed by image ID.")
    errors: list[str] = []
    annotations: dict[str, dict[str, Any]] = {}
    for record in records:
        image_id = str(record["id"])
        if not record.get("available"):
            continue
        value = raw.get(image_id)
        if value is None:
            if require_complete:
                errors.append(f"Image {image_id}: manual geometry annotation is missing.")
            continue
        if not isinstance(value, dict):
            errors.append(f"Image {image_id}: annotation must be an object.")
            continue
        try:
            width, height = record["decoded_dimensions_wh"]
            bbox = rounded_bbox(value.get("tight_plate_bbox", []), width, height)
            marker = record.get("marker_bbox")
            if marker is not None and np.allclose(np.asarray(bbox), np.asarray(marker), atol=0):
                raise VariantExperimentError(
                    "tight_plate_bbox exactly equals marker_bbox; marker boxes are not plate boundaries."
                )
            corners = validate_corners(value.get("plate_corners", []), width, height)
            status = value.get("rectification_status")
            if status not in RECTIFICATION_STATUSES:
                raise VariantExperimentError(
                    f"rectification_status must be one of {sorted(RECTIFICATION_STATUSES)}."
                )
            if status == "unavailable" and require_complete:
                raise VariantExperimentError("rectification_status is unavailable.")
            # All four corners must fall inside the enclosing P1 crop.
            x1, y1, x2, y2 = bbox
            if np.any(corners[:, 0] < x1) or np.any(corners[:, 0] > x2) or np.any(corners[:, 1] < y1) or np.any(corners[:, 1] > y2):
                raise VariantExperimentError("plate_corners must fall within tight_plate_bbox.")
            annotations[image_id] = {
                **value,
                "tight_plate_bbox_rounded_xyxy": bbox,
                "plate_corners": corners.tolist(),
            }
        except VariantExperimentError as exc:
            errors.append(f"Image {image_id}: {exc}")
    report = {
        "annotation_path": str(annotation_path),
        "annotation_sha256": json_sha256(annotation_path),
        "complete": not errors and len(annotations) == sum(bool(r.get("available")) for r in records),
        "errors": errors,
        "annotated_count": len(annotations),
    }
    return annotations, report


def prepare_variants(image_bgr: np.ndarray, annotation: dict[str, Any]) -> dict[str, PreparedVariant]:
    """Prepare P0-P4 without using marker metadata."""
    height, width = image_bgr.shape[:2]
    bbox = rounded_bbox(annotation["tight_plate_bbox"], width, height)
    corners = validate_corners(annotation["plate_corners"], width, height)
    x1, y1, x2, y2 = bbox
    p1 = image_bgr[y1:y2, x1:x2].copy()
    local = corners - np.array([x1, y1], dtype=np.float32)
    status = annotation["rectification_status"]
    transform_record: dict[str, Any]
    if status == "unnecessary":
        p2 = p1.copy()
        transform_record = {"applied": False, "reason": "manual status: unnecessary"}
    elif status == "needed":
        top = np.linalg.norm(local[1] - local[0])
        bottom = np.linalg.norm(local[2] - local[3])
        left = np.linalg.norm(local[3] - local[0])
        right = np.linalg.norm(local[2] - local[1])
        out_width = max(2, int(math.floor(max(top, bottom) + 0.5)))
        out_height = max(2, int(math.floor(max(left, right) + 0.5)))
        destination = np.asarray(
            [[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(local, destination)
        if not np.isfinite(matrix).all() or abs(float(np.linalg.det(matrix))) <= 1e-12:
            raise VariantExperimentError("Perspective transform is singular or non-finite.")
        p2 = cv2.warpPerspective(p1, matrix, (out_width, out_height), flags=cv2.INTER_CUBIC)
        transform_record = {
            "applied": True,
            "source_corners_p1_local_xy": local.tolist(),
            "opposing_edge_lengths": {"top": float(top), "bottom": float(bottom), "left": float(left), "right": float(right)},
            "output_rule": "round-half-up(max(opposing edge lengths)); minimum 2 pixels; no imposed aspect ratio",
            "output_dimensions_wh": [out_width, out_height],
            "matrix": matrix.tolist(),
            "interpolation": "OpenCV INTER_CUBIC",
        }
    else:
        raise VariantExperimentError("Cannot create P2 when rectification_status is unavailable.")
    gray = cv2.cvtColor(p2, cv2.COLOR_BGR2GRAY)
    p3 = cv2.merge([gray, gray, gray])
    lab = cv2.cvtColor(p2, cv2.COLOR_BGR2LAB)
    luminance, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    p4 = cv2.cvtColor(cv2.merge([clahe.apply(luminance), a_channel, b_channel]), cv2.COLOR_LAB2BGR)
    common = {
        "bbox_coordinate_convention": "xyxy, x2/y2 exclusive after floor(x1/y1), ceil(x2/y2)",
        "tight_plate_bbox_original_xyxy": list(annotation["tight_plate_bbox"]),
        "tight_plate_bbox_rounded_xyxy": bbox,
        "corners_original_xy_tl_tr_br_bl": corners.tolist(),
        "marker_bbox_used_for_geometry": False,
    }
    return {
        "p0": PreparedVariant("p0", image_bgr.copy(), {"description": "unchanged colour source plate", "dimensions_wh": [width, height]}),
        "p1": PreparedVariant("p1", p1, {**common, "description": "manual tight complete colour crop", "dimensions_wh": [p1.shape[1], p1.shape[0]]}),
        "p2": PreparedVariant("p2", p2, {**common, "description": "P1 with manual perspective status applied", "rectification_status": status, "perspective_transform": transform_record, "dimensions_wh": [p2.shape[1], p2.shape[0]]}),
        "p3": PreparedVariant("p3", p3, {"description": "P2 grayscale replicated identically into three channels", "conversion": "OpenCV BGR2GRAY then merge(gray,gray,gray)", "dimensions_wh": [p3.shape[1], p3.shape[0]]}),
        "p4": PreparedVariant("p4", p4, {"description": "P2 with CLAHE on Lab L channel", "conversion": "BGR->Lab; CLAHE L only; retain a,b; Lab->BGR", "clahe": {"clipLimit": 2.0, "tileGridSize": [4, 4]}, "colour_note": "Colour retention is approximate due to colour-space conversion and uint8 clipping/rounding.", "dimensions_wh": [p4.shape[1], p4.shape[0]]}),
    }


def same_group(first: dict[str, Any], second: dict[str, Any]) -> bool:
    for key in ("physical_plate_id", "group_id"):
        if first.get(key) is not None and first.get(key) == second.get(key):
            return True
    return False


def leave_one_out_predictions(
    condition: str,
    matrix: np.ndarray,
    records: Sequence[dict[str, Any]],
    tie_tolerance: float = TIE_TOLERANCE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query_index, query in enumerate(records):
        eligible = [index for index, ref in enumerate(records) if index != query_index and not same_group(query, ref)]
        if not eligible:
            raise VariantExperimentError(f"Query {query['id']} has no eligible references.")
        best_score = max(float(matrix[query_index, index]) for index in eligible)
        tied = [index for index in eligible if abs(float(matrix[query_index, index]) - best_score) <= tie_tolerance]
        winner = min(tied, key=lambda index: int(records[index]["id"]))
        category_max: dict[str, tuple[float, int]] = {}
        for index in eligible:
            category = records[index]["category"]
            score = float(matrix[query_index, index])
            if category not in category_max or score > category_max[category][0] + tie_tolerance or (
                abs(score - category_max[category][0]) <= tie_tolerance and int(records[index]["id"]) < int(records[category_max[category][1]]["id"])
            ):
                category_max[category] = (score, index)
        same = [(float(matrix[query_index, index]), index) for index in eligible if records[index]["category"] == query["category"]]
        different = [(float(matrix[query_index, index]), index) for index in eligible if records[index]["category"] != query["category"]]
        best_same = max(same, default=None)
        best_diff = max(different, default=None)
        winning_category = records[winner]["category"]
        other_category_scores = [value[0] for key, value in category_max.items() if key != winning_category]
        rows.append(
            {
                "condition": condition,
                "query_id": str(query["id"]),
                "query_category": query["category"],
                "nearest_reference_id": str(records[winner]["id"]),
                "predicted_category": winning_category,
                "nearest_similarity": best_score,
                "best_same_category_reference_id": None if best_same is None else str(records[best_same[1]]["id"]),
                "best_same_category_similarity": None if best_same is None else best_same[0],
                "best_different_category_reference_id": None if best_diff is None else str(records[best_diff[1]]["id"]),
                "best_different_category_similarity": None if best_diff is None else best_diff[0],
                "correct_category_margin": None if best_same is None or best_diff is None else best_same[0] - best_diff[0],
                "predicted_category_margin": (
                    None if not other_category_scores else best_score - max(other_category_scores)
                ),
                "correct": winning_category == query["category"],
                "tie_flag": len(tied) > 1 or sum(abs(value[0] - best_score) <= tie_tolerance for value in category_max.values()) > 1,
                "eligible_reference_count": len(eligible),
            }
        )
    return rows


def classification_metrics(rows: Sequence[dict[str, Any]], categories: Sequence[str]) -> dict[str, Any]:
    labels = list(categories)
    index = {label: number for number, label in enumerate(labels)}
    confusion = np.zeros((len(labels), len(labels)), dtype=int)
    for row in rows:
        confusion[index[row["query_category"]], index[row["predicted_category"]]] += 1
    recalls, f1_values = [], []
    per_category: dict[str, Any] = {}
    for number, label in enumerate(labels):
        tp = int(confusion[number, number])
        support = int(confusion[number].sum())
        predicted = int(confusion[:, number].sum())
        recall = tp / support if support else 0.0
        precision = tp / predicted if predicted else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        recalls.append(recall)
        f1_values.append(f1)
        per_category[label] = {"correct": tp, "total": support, "recall": recall, "precision": precision, "f1": f1}
    correct = sum(int(row["correct"]) for row in rows)
    return {
        "correct": correct,
        "total": len(rows),
        "accuracy": correct / len(rows),
        "balanced_accuracy": float(np.mean(recalls)),
        "macro_f1": float(np.mean(f1_values)),
        "categories": labels,
        "confusion_matrix": confusion.tolist(),
        "per_category": per_category,
    }
