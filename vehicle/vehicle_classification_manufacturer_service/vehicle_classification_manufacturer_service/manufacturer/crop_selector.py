"""Quality-first, visually diverse selection of manufacturer vehicle crops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import cv2
import numpy as np

from .crop_quality import (
    CropCandidate,
    CropQuality,
    CropQualityAnalyzer,
    CropQualityConfig,
    to_grayscale,
)

SelectionReason = Literal[
    "quality_and_diversity",
    "diversity_backfill",
    "redundant",
    "ineligible",
    "not_selected_limit",
]


@dataclass(frozen=True)
class CropSelectionEvaluation:
    candidate: CropCandidate
    quality: CropQuality
    selected: bool
    selection_reason: SelectionReason


@dataclass(frozen=True)
class CropSelectionResult:
    selected: tuple[CropCandidate, ...]
    evaluations: tuple[CropSelectionEvaluation, ...]

    @property
    def selected_evaluations(self) -> tuple[CropSelectionEvaluation, ...]:
        """Selected candidates with metadata, in the same public quality order."""

        indexed = [
            (input_order, evaluation)
            for input_order, evaluation in enumerate(self.evaluations)
            if evaluation.selected
        ]
        indexed.sort(key=_evaluation_priority_key)
        return tuple(evaluation for _input_order, evaluation in indexed)


@dataclass(frozen=True)
class BestCropSelectorConfig:
    quality: CropQualityConfig = CropQualityConfig()
    default_max_crops: int = 5
    default_min_crops: int = 2
    signature_width: int = 9
    signature_height: int = 8
    visual_similarity_threshold: float = 0.20

    def __post_init__(self) -> None:
        if (
            isinstance(self.default_max_crops, bool)
            or not isinstance(self.default_max_crops, int)
            or self.default_max_crops < 1
        ):
            raise ValueError("default_max_crops must be positive")
        if (
            isinstance(self.default_min_crops, bool)
            or not isinstance(self.default_min_crops, int)
            or not 1 <= self.default_min_crops <= self.default_max_crops
        ):
            raise ValueError("default_min_crops must be between 1 and default_max_crops")
        if self.signature_width < 2 or self.signature_height < 1:
            raise ValueError("signature dimensions are invalid")
        if not 0.0 <= self.visual_similarity_threshold <= 1.0:
            raise ValueError("visual_similarity_threshold must be within 0..1")


def visual_signature(
    image: np.ndarray,
    *,
    width: int = 9,
    height: int = 8,
) -> np.ndarray | None:
    """Return a small difference-hash signature, or None for unsupported input."""

    gray = to_grayscale(image)
    if gray is None:
        return None
    resized = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    return (resized[:, 1:] > resized[:, :-1]).reshape(-1)


def signature_distance(left: np.ndarray, right: np.ndarray) -> float:
    """Normalized Hamming distance in the inclusive range 0..1."""

    if left.shape != right.shape or left.size == 0:
        return 1.0
    return float(np.count_nonzero(left != right) / left.size)


class BestCropSelector:
    """Select a bounded quality-first set, backfilling only to a desired minimum."""

    def __init__(self, config: BestCropSelectorConfig | None = None) -> None:
        self.config = config or BestCropSelectorConfig()
        self.quality_analyzer = CropQualityAnalyzer(self.config.quality)

    def select(
        self,
        candidates: Sequence[CropCandidate],
        *,
        max_crops: int | None = None,
        min_crops: int | None = None,
    ) -> CropSelectionResult:
        limit = self.config.default_max_crops if max_crops is None else max_crops
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("max_crops must be positive")
        minimum = (
            min(self.config.default_min_crops, limit)
            if min_crops is None
            else min_crops
        )
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or not 1 <= minimum <= limit
        ):
            raise ValueError("min_crops must satisfy 1 <= min_crops <= max_crops")

        assessed = [
            (input_order, candidate, self.quality_analyzer.analyze(candidate))
            for input_order, candidate in enumerate(candidates)
        ]
        eligible = [item for item in assessed if item[2].eligible]
        eligible.sort(key=self._priority_key)

        signatures = {
            input_order: visual_signature(
                candidate.image,
                width=self.config.signature_width,
                height=self.config.signature_height,
            )
            for input_order, candidate, _quality in eligible
        }
        selected_orders: list[int] = []
        reasons: dict[int, SelectionReason] = {
            input_order: "ineligible"
            for input_order, _candidate, quality in assessed
            if not quality.eligible
        }
        redundant_orders: list[int] = []

        for input_order, _candidate, _quality in eligible:
            redundant = any(
                self._similar(signatures[input_order], signatures[selected_order])
                for selected_order in selected_orders
            )
            if redundant:
                reasons[input_order] = "redundant"
                redundant_orders.append(input_order)
            elif len(selected_orders) < limit:
                selected_orders.append(input_order)
                reasons[input_order] = "quality_and_diversity"
            else:
                reasons[input_order] = "not_selected_limit"

        # Diversity is primary. Redundant crops only fill the desired minimum, never
        # the absolute maximum unless min_crops explicitly equals max_crops.
        if len(selected_orders) < minimum:
            for input_order in redundant_orders:
                selected_orders.append(input_order)
                reasons[input_order] = "diversity_backfill"
                if len(selected_orders) == minimum:
                    break

        assessed_by_order = {item[0]: item for item in assessed}
        selected_orders.sort(
            key=lambda input_order: self._priority_key(assessed_by_order[input_order])
        )
        by_order = {input_order: candidate for input_order, candidate, _ in assessed}
        selected = tuple(by_order[input_order] for input_order in selected_orders)
        selected_set = set(selected_orders)
        evaluations = tuple(
            CropSelectionEvaluation(
                candidate=candidate,
                quality=quality,
                selected=input_order in selected_set,
                selection_reason=reasons[input_order],
            )
            for input_order, candidate, quality in assessed
        )
        return CropSelectionResult(selected=selected, evaluations=evaluations)

    @staticmethod
    def _priority_key(
        item: tuple[int, CropCandidate, CropQuality],
    ) -> tuple[float, int, int, int]:
        input_order, candidate, quality = item
        frame_unknown = 1 if candidate.frame_index is None else 0
        frame_index = candidate.frame_index if candidate.frame_index is not None else 0
        return (-quality.score, frame_unknown, frame_index, input_order)

    def _similar(
        self,
        left: np.ndarray | None,
        right: np.ndarray | None,
    ) -> bool:
        if left is None or right is None:
            return False
        return signature_distance(left, right) <= self.config.visual_similarity_threshold


def _evaluation_priority_key(
    item: tuple[int, CropSelectionEvaluation],
) -> tuple[float, int, int, int]:
    input_order, evaluation = item
    frame_index = evaluation.candidate.frame_index
    return (
        -evaluation.quality.score,
        1 if frame_index is None else 0,
        frame_index if frame_index is not None else 0,
        input_order,
    )
