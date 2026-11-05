"""OCR-first NT detection followed by three-category R2 cosine matching."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import PluginConfig
from .dinov3 import DinoV3Encoder
from .errors import PluginError
from .ocr import ArabicRecognizer
from .parser import parse_nt_evidence
from .preprocessing import calculate_geometry, resize_and_pad
from .reference_index import ReferenceIndex, compatibility_record
from .representations import extract_r2
from .utils import load_bgr, sha256_file


class PlateCategoryClassifier:
    """Classify complete, tightly framed plate crops against a frozen gallery."""

    def __init__(self, config: PluginConfig, load_ocr: bool = True):
        self.config = config
        started = time.perf_counter()
        self.encoder = DinoV3Encoder(config.dinov3_repo, config.dinov3_checkpoint, config.device)
        self.visual_load_seconds = time.perf_counter() - started
        expected = compatibility_record(config, self.encoder)
        self.index = ReferenceIndex.load(config.reference_index, expected)
        self.ocr = ArabicRecognizer(config.paddle_model_dir, config.ocr_device) if load_ocr else None

    def classify(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        *,
        exclude_reference_ids: tuple[str, ...] = (),
        exclude_groups: dict[str, Any] | None = None,
        expected_category: str | None = None,
    ) -> dict[str, Any]:
        path = Path(image_path).expanduser().resolve()
        output = Path(output_dir).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        image = load_bgr(path)
        return self._classify_image(
            image,
            input_metadata={
                "path": str(path), "sha256": sha256_file(path),
                "width": int(image.shape[1]), "height": int(image.shape[0]),
            },
            submitted_path=output / "submitted_ocr" / f"{path.stem}_budget_unpadded.png",
            exclude_reference_ids=exclude_reference_ids,
            exclude_groups=exclude_groups,
            expected_category=expected_category,
        )

    def classify_bgr(
        self,
        image: np.ndarray,
        output_dir: str | Path | None = None,
        *,
        exclude_reference_ids: tuple[str, ...] = (),
        exclude_groups: dict[str, Any] | None = None,
        expected_category: str | None = None,
    ) -> dict[str, Any]:
        """Classify uint8 H×W×3 BGR pixels without changing the caller's array.

        By default no files are written. If output_dir is supplied, save the
        unpadded OCR submission there. Use a separate directory per call when
        retaining diagnostics. No source path or file hash is inferred.
        """
        if not isinstance(image, np.ndarray):
            raise PluginError("Expected a NumPy array containing uint8 BGR pixels.")
        if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
            raise PluginError(f"Expected a nonempty BGR array shaped (H, W, 3), got {image.shape}.")
        if image.dtype != np.uint8:
            raise PluginError(f"Expected uint8 BGR pixels, got dtype={image.dtype}.")
        # Isolate model/preprocessing code from caller-owned storage, including
        # read-only arrays and non-contiguous frame slices.
        owned_image = image.copy(order="C")
        submitted_path = None
        if output_dir is not None:
            submitted_path = (
                Path(output_dir).expanduser().resolve()
                / "submitted_ocr" / "array_budget_unpadded.png"
            )
        return self._classify_image(
            owned_image,
            input_metadata={
                "kind": "bgr_array", "dtype": "uint8", "color_order": "BGR",
                "width": int(image.shape[1]), "height": int(image.shape[0]),
            },
            submitted_path=submitted_path,
            exclude_reference_ids=exclude_reference_ids,
            exclude_groups=exclude_groups,
            expected_category=expected_category,
        )

    def _classify_image(
        self,
        image: np.ndarray,
        *,
        input_metadata: dict[str, Any],
        submitted_path: Path | None,
        exclude_reference_ids: tuple[str, ...],
        exclude_groups: dict[str, Any] | None,
        expected_category: str | None,
    ) -> dict[str, Any]:
        """Check NT OCR first; otherwise match only the three visual categories."""
        total_started = time.perf_counter()
        geometry = calculate_geometry(image.shape, (16, 16), self.config.policy)
        resized, _padded = resize_and_pad(image, geometry, self.config.policy)
        if self.ocr is None:
            ocr = {
                "inference_status": "disabled",
                "failure_reason": "OCR model was not loaded.",
                "raw_text": "",
                "native_confidence": None,
                "native_payload": None,
                "recognition_seconds": 0.0,
                "submitted_path": None,
                "submitted_width": int(resized.shape[1]),
                "submitted_height": int(resized.shape[0]),
            }
            parser = None
        else:
            ocr = self.ocr.recognize(resized, submitted_path)
            parser = parse_nt_evidence(ocr["raw_text"]) if ocr["inference_status"] == "completed" else None

        # Reuse the parser's normalized, boundary-checked marker detection.
        # Conflicting markers remain recorded for downstream number validation.
        nt_present = parser is not None and parser["matches"]["nt"]
        visual_seconds = 0.0
        feature_metadata = None
        if nt_present:
            visual = {
                "visual_category": None,
                "nearest_reference_id": None,
                "nearest_reference_category": None,
                "nearest_similarity": None,
                "predicted_category_margin": None,
                "tie_flag": False,
                "eligible_reference_count": 0,
            }
            final_category = "nt"
            decision_source = "ocr_nt_evidence"
        else:
            # Failed/disabled OCR retains the visual fallback, but can never
            # assign NT: ReferenceIndex matches only the three visual categories.
            visual_started = time.perf_counter()
            embedding, feature_metadata = extract_r2(image, self.encoder, self.config.policy)
            visual = self.index.match(
                embedding,
                exclude_ids=exclude_reference_ids,
                exclude_groups=exclude_groups,
                tie_tolerance=self.config.tie_tolerance,
                expected_category=expected_category,
            )
            visual_seconds = time.perf_counter() - visual_started
            final_category = visual["visual_category"]
            decision_source = "visual_r2"
        visual_category = visual["visual_category"]
        conflict = parser is not None and parser["decision"] == "conflicting"
        ocr_failure = ocr["inference_status"] == "failed"
        result = {
            "schema_version": 1,
            "input": input_metadata,
            "visual": {
                **visual,
                "representation": "r2_global_regional",
                "feature_dimension": 1536,
                "feature_metadata": feature_metadata,
                "seconds": visual_seconds,
            },
            "ocr": {
                **ocr,
                "normalized_text": "" if parser is None else parser["normalized_text"],
                "parser_boundary_text": "" if parser is None else parser["boundary_text"],
                "nt_evidence": "unavailable" if parser is None else parser["decision"],
                "parser_reason": "inference_unavailable" if parser is None else parser["reason"],
                "conflicting_categories": [] if parser is None else parser["conflicting_categories"],
                "external_budget_geometry": geometry,
            },
            "decision": {
                "visual_category": visual_category,
                "final_category": final_category,
                "source": decision_source,
                "override_applied": False,
                "conflict_flag": conflict,
                "ocr_failure_flag": ocr_failure,
                "tie_flag": visual["tie_flag"],
                "uncertainty_threshold_applied": False,
            },
            "timing": {
                "visual_feature_and_match_seconds": visual_seconds,
                "ocr_recognition_seconds": ocr["recognition_seconds"],
                "total_classification_seconds": time.perf_counter() - total_started,
            },
            "index": {
                "path": str(self.index.path),
                "compatibility_fingerprint": self.index.compatibility_fingerprint,
            },
        }
        if expected_category is not None:
            result["evaluation"] = {
                "expected_category": expected_category,
                "visual_correct": None if nt_present else visual_category == expected_category,
                "final_correct": final_category == expected_category,
            }
        return result


def build_reference_index(config: PluginConfig, output_path: Path | None = None) -> ReferenceIndex:
    encoder = DinoV3Encoder(config.dinov3_repo, config.dinov3_checkpoint, config.device)
    return ReferenceIndex.build(config, encoder, output_path)

