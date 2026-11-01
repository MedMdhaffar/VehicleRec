"""Serialized, worker-owned local category and number recognition."""
import threading
import time
import uuid
from datetime import datetime, timezone
from run_video_pipeline.data_capture import ReviewStore
from run_video_pipeline.pipeline import analyze_plate_crop
from run_video_pipeline.plate_detector import PlateCrop
from run_video_pipeline.routing import route_plate
from run_video_pipeline.policies import PUBLIC_CATEGORY
from run_video_pipeline.schemas import MarkerEvidence, PlateTiming, UnifiedPlateResult


class RuntimeCounters:
    def __init__(self):
        self.processed = 0

    def record(self):
        self.processed += 1

    def snapshot(self):
        return {"processed": self.processed}


class PrototypePlatePipeline:
    def __init__(self, reader, marker_localizer, store: ReviewStore, *, classifier_factory):
        self.classifier_factory = classifier_factory
        self.classifier = None
        self._recognition_lock = threading.Lock()
        self.reader = reader
        self.marker_localizer = marker_localizer
        self.store = store
        self.counters = RuntimeCounters()

    def initialize_classifier(self):
        """Initialize on the runner's single worker; reconnects create a new owner."""
        with self._recognition_lock:
            if self.classifier is not None:
                raise RuntimeError("Category classifier already belongs to an active recognition worker")
            try:
                self.classifier = self.classifier_factory()
            except Exception as exc:
                raise RuntimeError(f"Plate category classifier initialization failed: {exc}") from exc

    def release_classifier(self):
        with self._recognition_lock:
            self.classifier = None

    def process_crop(self, detection: PlateCrop, **kwargs) -> UnifiedPlateResult:
        # Also serialize direct callers; no shared global classifier exists.
        with self._recognition_lock:
            return self._process_crop(detection, **kwargs)

    def _process_crop(self, detection, *, original_image_ref=None, camera_id=None, track_id=None, sample_id=None, quality=None):
        started = time.perf_counter()
        timing = {}
        local = analyze_plate_crop(detection, self.reader, self.marker_localizer, timing=timing, classifier=self.classifier)
        category = local.classification
        number = local.number
        decision = route_plate(detection.crop, local)
        recognized = number.outcome == "recognized"
        result = UnifiedPlateResult(
            classification=category, number=number,
            sample_id=sample_id or str(uuid.uuid4()), timestamp=datetime.now(timezone.utc).isoformat(),
            plate_text_raw=local.raw_ocr, plate_text_normalized=number.normalized_number,
            plate_category=PUBLIC_CATEGORY.get(category.final_category, "unknown") if category else "unknown",
            # Retained compatibility field is an evidence score, not category probability.
            category_confidence=(min(local.ocr_confidence, local.marker_confidence) if number.policy == "tunis_private" else local.ocr_confidence) if recognized else 0.0,
            text_confidence=local.ocr_confidence, route=decision.route, route_confidence=decision.confidence,
            route_reason=decision.reason, source="local_ocr" if local.raw_ocr else "none", model_version="PP-OCRv6_medium_rec",
            bbox=local.bbox, detector_confidence=local.detector_confidence,
            original_image_ref=original_image_ref, camera_id=camera_id, track_id=track_id,
            marker=MarkerEvidence(detected=local.marker_detected, reliable=local.marker_reliable,
                                  confidence=local.marker_confidence, bbox=local.marker_bbox),
            status="accepted" if recognized else number.outcome,
            predicted_category=category.final_category if category else None,
            category_decision_source=category.decision_source if category else None,
            recognition_policy=number.policy, recognition_status=number.outcome,
            unresolved_reason=number.reason, registration_fields=number.fields, normalized_number=number.normalized_number,
            warning=local.parser_reason if number.outcome == "error" else number.reason,
            timing=PlateTiming(**timing, queue_wait_ms=(quality or {}).get("queue_wait_ms", 0.0)),
            evidence={"local": local.model_dump(mode="json"), "number_policy": number.model_dump(mode="json"), "quality": quality or {}},
        )
        result.timing.total_ms = (time.perf_counter() - started) * 1000
        self.counters.record()
        return self.store.capture(result, detection.crop)
