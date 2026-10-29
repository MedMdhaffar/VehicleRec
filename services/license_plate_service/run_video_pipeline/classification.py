"""Local classifier factory and compact, typed category evidence."""
from __future__ import annotations

import time

from run_video_pipeline.schemas import CategoryClassification


def create_classifier():
    """Called once on each recognition worker, before processing any frames."""
    from license_plate_plugin import PlateCategoryClassifier, load_default_config

    return PlateCategoryClassifier(load_default_config())


def classify_crop(classifier, crop) -> CategoryClassification:
    started = time.perf_counter()
    try:
        if classifier is None:
            raise RuntimeError("Category classifier was not initialized")
        result = classifier.classify_bgr(crop)
        decision, visual, ocr = result["decision"], result["visual"], result["ocr"]
        return CategoryClassification(
            status="completed", final_category=decision["final_category"],
            visual_category=decision["visual_category"], decision_source=decision["source"],
            nearest_reference_id=visual["nearest_reference_id"],
            nearest_similarity=visual["nearest_similarity"],
            category_margin=visual["predicted_category_margin"],
            nt_evidence=ocr["nt_evidence"], conflict=decision["conflict_flag"],
            arabic_ocr_failed=decision["ocr_failure_flag"],
            classification_ms=(time.perf_counter() - started) * 1000,
            visual_ms=result["timing"]["visual_feature_and_match_seconds"] * 1000,
            arabic_ocr_ms=result["timing"]["ocr_recognition_seconds"] * 1000,
        )
    except Exception as exc:
        return CategoryClassification(
            status="error", error=f"{type(exc).__name__}: {exc}",
            classification_ms=(time.perf_counter() - started) * 1000,
        )
