"""Fixed representation helpers for the DINOv2 plate experiment."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import torch
import torch.nn.functional as F

from plate_variants import VariantExperimentError


REPRESENTATIONS = ("r0_cls", "r1_mean_patch", "r2_global_regional")
MARKER_REPRESENTATION = "r3_marker_cls"
REGION_NAMES = ("left", "centre", "right")
REGION_WEIGHTS = {"whole": 0.25, "left": 0.25, "centre": 0.25, "right": 0.25}


def region_coordinates(width: int, height: int) -> dict[str, list[int]]:
    """Return overlapping xyxy regions using floor lower/ceil upper bounds."""
    if width <= 0 or height <= 0:
        raise VariantExperimentError("Region source dimensions must be positive.")
    raw = {
        "left": (0.0, 0.5 * width),
        "centre": (0.25 * width, 0.75 * width),
        "right": (0.5 * width, float(width)),
    }
    result: dict[str, list[int]] = {}
    for name, (lower, upper) in raw.items():
        x1 = max(0, min(width, math.floor(lower)))
        x2 = max(0, min(width, math.ceil(upper)))
        if x2 <= x1:
            raise VariantExperimentError(
                f"Region {name} is empty after rounding for {width}x{height}."
            )
        result[name] = [x1, 0, x2, height]
    return result


def patch_selection(
    preprocessing: dict[str, Any], patch_size: tuple[int, int]
) -> dict[str, Any]:
    """Select patches whose footprints intersect resized unpadded content."""
    patch_height, patch_width = patch_size
    grid_height, grid_width = (int(value) for value in preprocessing["patch_grid_hw"])
    resized_height, resized_width = (
        int(value) for value in preprocessing["resized_dimensions_hw"]
    )
    left, top, _right, _bottom = (
        int(value) for value in preprocessing["padding_ltrb"]
    )
    content_x1, content_y1 = left, top
    content_x2, content_y2 = left + resized_width, top + resized_height
    selected: list[bool] = []
    partial: list[bool] = []
    coverage: list[float] = []
    footprint_records: list[dict[str, Any]] = []
    patch_area = patch_height * patch_width
    for row in range(grid_height):
        for column in range(grid_width):
            x1, y1 = column * patch_width, row * patch_height
            x2, y2 = x1 + patch_width, y1 + patch_height
            intersection_width = max(0, min(x2, content_x2) - max(x1, content_x1))
            intersection_height = max(0, min(y2, content_y2) - max(y1, content_y1))
            intersection_area = intersection_width * intersection_height
            fraction = intersection_area / patch_area
            is_selected = intersection_area > 0
            is_partial = is_selected and intersection_area < patch_area
            selected.append(is_selected)
            partial.append(is_partial)
            coverage.append(float(fraction))
            footprint_records.append(
                {
                    "row": row,
                    "column": column,
                    "xyxy": [x1, y1, x2, y2],
                    "selected": is_selected,
                    "partial": is_partial,
                    "content_coverage_fraction": float(fraction),
                }
            )
    expected_count = grid_height * grid_width
    if len(selected) != expected_count or not any(selected):
        raise VariantExperimentError("Patch-selection mask has an invalid size or no content patches.")
    selected_fractions = [value for value, keep in zip(coverage, selected) if keep]
    return {
        "selection_rule": (
            "Exclude padding-only patches; include every patch with nonzero intersection "
            "with resized unpadded content using uniform pooling weight."
        ),
        "grid_hw": [grid_height, grid_width],
        "content_rectangle_xyxy_in_padded_input": [
            content_x1,
            content_y1,
            content_x2,
            content_y2,
        ],
        "selected_mask_flat": selected,
        "partial_mask_flat": partial,
        "valid_patch_count": int(sum(selected)),
        "partial_patch_count": int(sum(partial)),
        "padding_only_patch_count": int(expected_count - sum(selected)),
        "selected_content_coverage_fractions": selected_fractions,
        "coverage_summary": {
            "minimum": float(min(selected_fractions)),
            "maximum": float(max(selected_fractions)),
            "mean": float(np.mean(selected_fractions)),
        },
        "patches": footprint_records,
    }


def forward_whole_representations(
    pixel_values: torch.Tensor,
    model: Any,
    device: torch.device,
    preprocessing: dict[str, Any],
    patch_size: tuple[int, int],
) -> tuple[torch.Tensor, torch.Tensor, dict[str, Any]]:
    """Extract R0 CLS and R1 mean patch from one exact model forward pass."""
    selection = patch_selection(preprocessing, patch_size)
    model_tensor = pixel_values.to(device)
    with torch.no_grad():
        tokens = model(pixel_values=model_tensor).last_hidden_state[0].float()
    grid_height, grid_width = selection["grid_hw"]
    expected_patch_count = grid_height * grid_width
    expected_token_count = 1 + expected_patch_count
    if tokens.ndim != 2 or tokens.shape[0] != expected_token_count:
        raise VariantExperimentError(
            "DINOv2 token count does not match CLS plus the spatial grid: "
            f"got {list(tokens.shape)}, expected [{expected_token_count}, hidden]. "
            "No register/nonspatial-token fallback is permitted."
        )
    spatial_tokens = tokens[1:]
    mask = torch.tensor(
        selection["selected_mask_flat"], dtype=torch.bool, device=spatial_tokens.device
    )
    selected_tokens = spatial_tokens[mask]
    if selected_tokens.shape[0] != selection["valid_patch_count"]:
        raise VariantExperimentError("Selected patch-token count does not match the mask.")
    cls = F.normalize(tokens[0], p=2, dim=0).cpu()
    mean_patch = F.normalize(selected_tokens.mean(dim=0), p=2, dim=0).cpu()
    if not torch.isfinite(cls).all() or not torch.isfinite(mean_patch).all():
        raise VariantExperimentError("DINOv2 produced a non-finite representation.")
    metadata = {
        "forward_passes": 1,
        "token_layout": "one CLS token followed by row-major spatial patch tokens",
        "total_token_count": int(tokens.shape[0]),
        "spatial_patch_token_count": expected_patch_count,
        "register_or_other_nonspatial_token_count": 0,
        "r0": {
            "pooling": "last_hidden_state[0, 0], float32, L2-normalized",
            "dimension": int(cls.numel()),
            "l2_norm": float(torch.linalg.vector_norm(cls)),
        },
        "r1": {
            "pooling": (
                "uniform mean of selected float32 spatial patch tokens, then L2-normalized"
            ),
            "dimension": int(mean_patch.numel()),
            "l2_norm": float(torch.linalg.vector_norm(mean_patch)),
            "patch_selection": selection,
        },
    }
    return cls, mean_patch, metadata


def normalized_cls_from_tensor(
    pixel_values: torch.Tensor, model: Any, device: torch.device
) -> tuple[torch.Tensor, dict[str, Any]]:
    with torch.no_grad():
        tokens = model(pixel_values=pixel_values.to(device)).last_hidden_state
        cls = F.normalize(tokens[:, 0, :].float(), p=2, dim=1)[0].cpu()
    if not torch.isfinite(cls).all():
        raise VariantExperimentError("DINOv2 returned a non-finite CLS representation.")
    return cls, {
        "dimension": int(cls.numel()),
        "dtype": str(cls.dtype),
        "l2_norm": float(torch.linalg.vector_norm(cls)),
        "pooling": "last_hidden_state[:, 0, :], float32, L2-normalized",
    }


def cosine_matrix(embeddings: Sequence[torch.Tensor]) -> np.ndarray:
    stacked = F.normalize(torch.stack(list(embeddings)).float(), p=2, dim=1)
    return (stacked @ stacked.T).cpu().numpy()


def global_regional_matrix(
    components: dict[str, Sequence[torch.Tensor]],
) -> tuple[np.ndarray, dict[str, Any]]:
    if tuple(components) != tuple(REGION_WEIGHTS):
        raise VariantExperimentError(
            f"R2 components must be ordered {tuple(REGION_WEIGHTS)}; got {tuple(components)}."
        )
    count = len(components["whole"])
    if any(len(values) != count for values in components.values()):
        raise VariantExperimentError("R2 component counts do not match.")
    concatenated = []
    for index in range(count):
        parts = [
            math.sqrt(REGION_WEIGHTS[name])
            * F.normalize(components[name][index].float(), p=2, dim=0)
            for name in REGION_WEIGHTS
        ]
        vector = torch.cat(parts)
        concatenated.append(F.normalize(vector, p=2, dim=0))
    matrix = cosine_matrix(concatenated)
    direct = np.zeros_like(matrix)
    for name, weight in REGION_WEIGHTS.items():
        direct += weight * cosine_matrix(components[name])
    maximum_difference = float(np.max(np.abs(matrix - direct)))
    if maximum_difference > 1e-6:
        raise VariantExperimentError(
            f"R2 concatenation and fixed weighted score differ by {maximum_difference}."
        )
    return matrix, {
        "score": "0.25*cos(whole)+0.25*cos(left)+0.25*cos(centre)+0.25*cos(right)",
        "weights": REGION_WEIGHTS,
        "concatenation_equivalence": (
            "Concatenate each normalized 384-D component scaled by sqrt(0.25)=0.5; "
            "the resulting 1536-D vector has unit norm and its cosine equals the weighted score."
        ),
        "maximum_absolute_equivalence_difference": maximum_difference,
        "dimension_if_concatenated": int(concatenated[0].numel()),
    }

