"""Cheap private-local/exceptional-agent prototype orchestration."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from run_video_pipeline.agent import OpenRouterPlateAgent, normalize_agent_text
from run_video_pipeline.data_capture import ReviewStore
from run_video_pipeline.pipeline import analyze_plate_crop
from run_video_pipeline.plate_detector import PlateCrop
from run_video_pipeline.routing import route_plate
from run_video_pipeline.routing import RouteDecision
from run_video_pipeline.schemas import MarkerEvidence, PlateTiming, UnifiedPlateResult


class RuntimeCounters:
    def __init__(self):
        self._lock = threading.Lock()
        self.processed = self.local_predictions = self.agent_routes = self.agent_calls = self.unknown = 0

    def record(self, route: str, api_calls: int) -> None:
        with self._lock:
            self.processed += 1; self.agent_calls += api_calls
            if route == "private_local": self.local_predictions += 1
            elif route == "agent": self.agent_routes += 1
            else: self.unknown += 1

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "processed": self.processed, "local_predictions": self.local_predictions,
                "agent_routes": self.agent_routes, "unknown_routes": self.unknown,
                "agent_calls": self.agent_calls, "estimated_api_usage_count": self.agent_calls,
                "agent_route_percent": self.agent_routes / self.processed * 100 if self.processed else 0.0,
            }


class PrototypePlatePipeline:
    def __init__(self, reader, marker_localizer, agent: OpenRouterPlateAgent, store: ReviewStore, *, classifier_factory):
        self.classifier_factory = classifier_factory
        self.classifier = None
        self._recognition_lock = threading.Lock()
        self.reader = reader
        self.marker_localizer = marker_localizer
        self.agent = agent
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

    def _process_crop(
        self,
        detection: PlateCrop,
        *,
        original_image_ref: str | None = None,
        camera_id: str | None = None,
        track_id: str | None = None,
        sample_id: str | None = None,
        quality: dict | None = None,
    ) -> UnifiedPlateResult:
        total_started = time.perf_counter()
        local_timing: dict[str, float] = {}
        local = analyze_plate_crop(
            detection, self.reader, self.marker_localizer, timing=local_timing, classifier=self.classifier
        )
        route_started = time.perf_counter()
        classification_failed = local.classification is None or local.classification.status == "error"
        decision = (
            RouteDecision("unknown", 1.0, "category classification failed; attempt unresolved")
            if classification_failed else route_plate(detection.crop, local)
        )
        routing_ms = (time.perf_counter() - route_started) * 1000
        now = datetime.now(timezone.utc).isoformat()
        identifier = sample_id or str(uuid.uuid4())
        marker = MarkerEvidence(
            detected=local.marker_detected, reliable=local.marker_reliable,
            confidence=local.marker_confidence, bbox=local.marker_bbox,
        )
        api_calls = 0
        if decision.route == "private_local":
            result = UnifiedPlateResult(
                sample_id=identifier, timestamp=now, plate_text_raw=local.raw_ocr,
                plate_text_normalized=local.normalized_plate, plate_category="private",
                category_confidence=decision.confidence, text_confidence=local.ocr_confidence,
                route=decision.route, route_confidence=decision.confidence, route_reason=decision.reason,
                source="local_ocr", model_version=f"PP-OCRv6_medium_rec+marker:{local.resolution_source}",
                bbox=local.bbox, detector_confidence=local.detector_confidence,
                original_image_ref=original_image_ref, camera_id=camera_id, track_id=track_id,
                marker=marker, status="accepted",
                timing=PlateTiming(routing_ms=routing_ms, **local_timing),
                evidence={"local": local.model_dump(mode="json"), "quality": quality or {}},
            )
        elif decision.route == "agent":
            outcome = self.agent.analyze(detection.crop); api_calls = outcome.api_calls
            prediction = outcome.prediction
            accepted = outcome.status == "accepted" and prediction is not None
            result = UnifiedPlateResult(
                sample_id=identifier, timestamp=now,
                plate_text_raw=prediction.visible_text if prediction else "",
                plate_text_normalized=normalize_agent_text(prediction) if prediction else None,
                plate_category=prediction.category if accepted else "unknown",
                category_confidence=prediction.confidence if prediction else 0.0,
                text_confidence=prediction.confidence if prediction else 0.0,
                route="agent", route_confidence=decision.confidence, route_reason=decision.reason,
                source="agent" if prediction else "none", model_version=self.agent.model,
                bbox=local.bbox, detector_confidence=local.detector_confidence,
                original_image_ref=original_image_ref, camera_id=camera_id, track_id=track_id,
                marker=marker, status=outcome.status, warning=outcome.warning,
                timing=PlateTiming(
                    routing_ms=routing_ms, **local_timing,
                    image_preparation_ms=outcome.preparation_ms, api_ms=outcome.api_ms,
                    agent_total_ms=outcome.total_ms,
                ),
                evidence={"local": local.model_dump(mode="json"), "agent_evidence": prediction.evidence_summary if prediction else "", "quality": quality or {}},
            )
        else:
            result = UnifiedPlateResult(
                sample_id=identifier, timestamp=now, route="unknown",
                route_confidence=decision.confidence, route_reason=decision.reason,
                source="none", model_version="none", bbox=local.bbox,
                detector_confidence=local.detector_confidence, original_image_ref=original_image_ref,
                camera_id=camera_id, track_id=track_id, marker=marker, status="error" if classification_failed else "unknown",
                warning=local.classification.error if classification_failed and local.classification else "no usable route", timing=PlateTiming(routing_ms=routing_ms, **local_timing),
                evidence={"local": local.model_dump(mode="json"), "quality": quality or {}},
            )
        result.classification = local.classification
        result.timing.total_ms = (time.perf_counter() - total_started) * 1000
        stored = self.store.capture(result, detection.crop)
        self.counters.record(decision.route, api_calls)
        return stored
