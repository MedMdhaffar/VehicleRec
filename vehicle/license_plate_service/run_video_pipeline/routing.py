"""Deterministic routing from local plate evidence to local or agent handling."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import run_video_pipeline.config as config
from run_video_pipeline.ocr import digits_only
from run_video_pipeline.schemas import PlateDetection, PlateRoute


@dataclass(frozen=True)
class RouteDecision:
    route: PlateRoute
    confidence: float
    reason: str


def route_plate(crop: np.ndarray | None, result: PlateDetection | None) -> RouteDecision:
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0 or result is None:
        return RouteDecision("unknown", 1.0, "missing usable crop or local evidence")

    from .scalar_policy import local_route, LocalPlatePolicy
    local, confidence, reason = local_route(result, policy=LocalPlatePolicy(
        config.MARKER_RELIABLE_CONF, config.MARKER_REGION_OCR_CONF,
        config.PRIVATE_ROUTE_MIN_OCR_CONF, config.PRIVATE_ROUTE_MIN_MARKER_CONF))
    return RouteDecision('private_local' if local else 'agent', confidence, reason)
