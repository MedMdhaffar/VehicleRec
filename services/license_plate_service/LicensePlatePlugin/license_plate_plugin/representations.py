"""Exact R2 whole/left/centre/right feature extraction."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

from .config import ResolutionPolicy
from .dinov3 import DinoV3Encoder, PATCH_SIZE
from .errors import PluginError
from .preprocessing import prepare_budget_input


VIEW_ORDER = ("whole", "left", "centre", "right")
VIEW_WEIGHTS = {name: 0.25 for name in VIEW_ORDER}


def region_coordinates(width: int, height: int) -> dict[str, list[int]]:
    if width <= 0 or height <= 0:
        raise PluginError("Region source dimensions must be positive.")
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
            raise PluginError(f"Region {name} is empty for image {width}x{height}.")
        result[name] = [x1, 0, x2, height]
    return result


def extract_r2(
    image_bgr: np.ndarray,
    encoder: DinoV3Encoder,
    policy: ResolutionPolicy,
) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = image_bgr.shape[:2]
    coordinates = region_coordinates(width, height)
    crops = {"whole": image_bgr}
    for name, (x1, y1, x2, y2) in coordinates.items():
        crops[name] = image_bgr[y1:y2, x1:x2].copy()
    components: list[torch.Tensor] = []
    views: dict[str, Any] = {}
    total_started = time.perf_counter()
    for name in VIEW_ORDER:
        prepared = prepare_budget_input(crops[name], PATCH_SIZE, policy)
        encoder.synchronize()
        started = time.perf_counter()
        embedding, embedding_metadata = encoder.cls_embedding(prepared.pixel_values)
        encoder.synchronize()
        elapsed = time.perf_counter() - started
        components.append(math.sqrt(VIEW_WEIGHTS[name]) * F.normalize(embedding.float(), p=2, dim=0))
        views[name] = {
            "source_xyxy": [0, 0, width, height] if name == "whole" else coordinates[name],
            "preprocessing": prepared.metadata,
            "embedding": embedding_metadata,
            "forward_seconds": elapsed,
        }
    combined = F.normalize(torch.cat(components), p=2, dim=0).cpu().numpy().astype(np.float32)
    if combined.shape != (1536,) or not np.isfinite(combined).all():
        raise PluginError(f"Invalid R2 vector: shape={combined.shape}")
    return combined, {
        "view_order": list(VIEW_ORDER),
        "weights": VIEW_WEIGHTS,
        "dimension": 1536,
        "l2_norm": float(np.linalg.norm(combined)),
        "views": views,
        "total_feature_seconds": time.perf_counter() - total_started,
    }


def r2_score(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != (1536,) or second.shape != (1536,):
        raise PluginError("R2 similarity requires two 1536-dimensional vectors.")
    return float(np.dot(first.astype(np.float32), second.astype(np.float32)))

