"""Exact budget resize and minimal patch-alignment padding from the successful run."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import cv2
import numpy as np
import torch

from .config import ResolutionPolicy
from .errors import PluginError


INTERPOLATIONS = {
    "nearest": cv2.INTER_NEAREST,
    "bilinear": cv2.INTER_LINEAR,
    "bicubic": cv2.INTER_CUBIC,
    "area": cv2.INTER_AREA,
    "lanczos": cv2.INTER_LANCZOS4,
}


@dataclass
class PreparedInput:
    resized_bgr: np.ndarray
    padded_bgr: np.ndarray
    pixel_values: torch.Tensor
    metadata: dict[str, Any]


def round_half_up(value: float) -> int:
    if not math.isfinite(value) or value < 0:
        raise PluginError(f"Cannot round invalid image dimension: {value}")
    return int(math.floor(value + 0.5))


def validate_policy(policy: ResolutionPolicy) -> None:
    if policy.target_area <= 0 or policy.max_long_edge <= 0 or policy.max_patch_count <= 0:
        raise PluginError("Resolution limits must all be positive.")
    if policy.interpolation not in INTERPOLATIONS:
        raise PluginError(f"Unknown resize interpolation: {policy.interpolation}")
    if len(policy.padding_rgb) != 3 or any(not 0 <= value <= 255 for value in policy.padding_rgb):
        raise PluginError("padding_rgb must contain three integers from 0 to 255.")


def aligned_dimension(dimension: int, patch_dimension: int) -> int:
    if dimension <= 0 or patch_dimension <= 0:
        raise PluginError("Image and patch dimensions must be positive.")
    return ((dimension + patch_dimension - 1) // patch_dimension) * patch_dimension


def dimensions_for_scale(height: int, width: int, scale: float) -> tuple[int, int]:
    return max(1, round_half_up(height * scale)), max(1, round_half_up(width * scale))


def aligned_patch_count(height: int, width: int, patch_size: tuple[int, int]) -> int:
    ph, pw = patch_size
    return (aligned_dimension(height, ph) // ph) * (aligned_dimension(width, pw) // pw)


def largest_scale_within_patch_budget(
    height: int,
    width: int,
    upper_scale: float,
    patch_size: tuple[int, int],
    max_patch_count: int,
) -> float:
    if aligned_patch_count(*dimensions_for_scale(height, width, upper_scale), patch_size) <= max_patch_count:
        return upper_scale
    low, high = 0.0, upper_scale
    for _ in range(80):
        midpoint = (low + high) / 2.0
        if aligned_patch_count(*dimensions_for_scale(height, width, midpoint), patch_size) <= max_patch_count:
            low = midpoint
        else:
            high = midpoint
    if low <= 0:
        raise PluginError("No positive resize scale satisfies the patch-count limit.")
    return low


def calculate_geometry(
    image_shape: Sequence[int], patch_size: tuple[int, int], policy: ResolutionPolicy
) -> dict[str, Any]:
    validate_policy(policy)
    height, width = int(image_shape[0]), int(image_shape[1])
    if height <= 0 or width <= 0:
        raise PluginError(f"Invalid image dimensions: {width}x{height}.")
    requested = math.sqrt(policy.target_area / float(height * width))
    edge_scale = min(requested, policy.max_long_edge / float(max(height, width)))
    scale = largest_scale_within_patch_budget(
        height, width, edge_scale, patch_size, policy.max_patch_count
    )
    resized_height, resized_width = dimensions_for_scale(height, width, scale)
    ph, pw = patch_size
    final_height = aligned_dimension(resized_height, ph)
    final_width = aligned_dimension(resized_width, pw)
    pad_height, pad_width = final_height - resized_height, final_width - resized_width
    top, left = pad_height // 2, pad_width // 2
    bottom, right = pad_height - top, pad_width - left
    grid = [final_height // ph, final_width // pw]
    patch_count = grid[0] * grid[1]
    if max(resized_height, resized_width) > policy.max_long_edge or patch_count > policy.max_patch_count:
        raise PluginError("Internal geometry calculation exceeded a configured limit.")
    return {
        "original_dimensions_hw": [height, width],
        "requested_scale": requested,
        "scale_after_long_edge_limit": edge_scale,
        "scale_used_before_integer_rounding": scale,
        "effective_scale_xy": [resized_width / width, resized_height / height],
        "resized_dimensions_hw": [resized_height, resized_width],
        "rounding_rule": "round-half-up; each dimension at least one pixel",
        "padding_ltrb": [left, top, right, bottom],
        "odd_padding_rule": "extra pixel goes to right or bottom",
        "final_dimensions_hw": [final_height, final_width],
        "patch_size_hw": [ph, pw],
        "patch_grid_hw": grid,
        "patch_count": patch_count,
        "long_edge_limit_applied": edge_scale < requested - 1e-12,
        "patch_count_limit_applied": scale < edge_scale - 1e-12,
        "pixels_cropped": 0,
    }


def resize_and_pad(
    crop_bgr: np.ndarray, geometry: dict[str, Any], policy: ResolutionPolicy
) -> tuple[np.ndarray, np.ndarray]:
    resized_height, resized_width = geometry["resized_dimensions_hw"]
    if (resized_height, resized_width) == crop_bgr.shape[:2]:
        resized = crop_bgr.copy()
    else:
        resized = cv2.resize(
            crop_bgr,
            (resized_width, resized_height),
            interpolation=INTERPOLATIONS[policy.interpolation],
        )
    left, top, right, bottom = geometry["padding_ltrb"]
    red, green, blue = policy.padding_rgb
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(blue, green, red)
    )
    if list(padded.shape[:2]) != geometry["final_dimensions_hw"]:
        raise PluginError("Padded image dimensions do not match calculated geometry.")
    return resized, padded


def normalized_tensor(padded_bgr: np.ndarray) -> torch.Tensor:
    rgb = cv2.cvtColor(padded_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    tensor = torch.from_numpy(rgb).permute(2, 0, 1) / 255.0
    mean = torch.tensor((0.485, 0.456, 0.406), dtype=torch.float32)[:, None, None]
    std = torch.tensor((0.229, 0.224, 0.225), dtype=torch.float32)[:, None, None]
    return ((tensor - mean) / std).unsqueeze(0)


def prepare_budget_input(
    crop_bgr: np.ndarray,
    patch_size: tuple[int, int],
    policy: ResolutionPolicy,
) -> PreparedInput:
    if crop_bgr.ndim != 3 or crop_bgr.shape[2] != 3 or crop_bgr.dtype != np.uint8:
        raise PluginError("Expected a non-empty uint8 BGR image.")
    geometry = calculate_geometry(crop_bgr.shape, patch_size, policy)
    resized, padded = resize_and_pad(crop_bgr, geometry, policy)
    tensor = normalized_tensor(padded)
    expected_h, expected_w = geometry["final_dimensions_hw"]
    if list(tensor.shape) != [1, 3, expected_h, expected_w]:
        raise PluginError(f"Unexpected DINO tensor shape: {list(tensor.shape)}")
    geometry.update(
        {
            "padding_rgb": list(policy.padding_rgb),
            "resize_interpolation": policy.interpolation,
            "tensor_shape": list(tensor.shape),
            "tensor_dtype": str(tensor.dtype),
        }
    )
    return PreparedInput(resized, padded, tensor, geometry)

