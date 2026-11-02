"""Local classifier factory and compact, typed category evidence."""
from __future__ import annotations

import time

from run_video_pipeline.schemas import CategoryClassification


def create_classifier():
    """Called once on each recognition worker, before processing any frames."""
    from .license_plate_plugin import PlateCategoryClassifier, load_default_config

    import os
    from dataclasses import replace
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    configuration = load_default_config()
    return PlateCategoryClassifier(replace(configuration, device=os.environ.get("PLATE_CLASSIFIER_DEVICE", configuration.device)))


def classify_crop(classifier, crop) -> CategoryClassification:
    started = time.perf_counter()
    try:
        if classifier is None:
            raise RuntimeError("Category classifier was not initialized")
        result = classifier.classify_bgr(crop)
        decision, visual, ocr = result["decision"], result["visual"], result["ocr"]
        evidence = CategoryClassification(
            status="completed", final_category=decision["final_category"],
            visual_category=decision["visual_category"], decision_source=decision["source"],
            nearest_reference_id=visual["nearest_reference_id"],
            nearest_similarity=visual["nearest_similarity"],
            category_margin=visual["predicted_category_margin"],
            nt_evidence=ocr["nt_evidence"], conflict=decision["conflict_flag"],
            observed_markers=ocr.get("conflicting_categories", []), category_only_text=ocr.get("raw_text", ""),
            arabic_ocr_failed=decision["ocr_failure_flag"],
            classification_ms=(time.perf_counter() - started) * 1000,
            visual_ms=result["timing"]["visual_feature_and_match_seconds"] * 1000,
            arabic_ocr_ms=result["timing"]["ocr_recognition_seconds"] * 1000,
        )
        if isinstance(classifier, EvidenceClassifier):
            classifier.last_evidence = evidence
        return evidence
    except Exception as exc:
        return CategoryClassification(
            status="error", error=f"{type(exc).__name__}: {exc}",
            classification_ms=(time.perf_counter() - started) * 1000,
        )


class EvidenceClassifier:
    """Call-local proxy; holds evidence if downstream registration raises."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.last_evidence = None

    def classify_bgr(self, crop):
        if self.delegate is None:
            raise RuntimeError("Category classifier was not initialized")
        return self.delegate.classify_bgr(crop)
