from __future__ import annotations

import logging
import argparse
import base64
import json
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    from . import config
    from .colour import estimate_colour_detailed
    from .device import resolve_device
    from .detection_schemas import Detection, DetectionColourReview
    from .schemas import ColourObservation, VehicleProfile
    from .temporal import compute_colour_crop_quality_details, decide_colour, make_colour_observation
except ImportError:  # Support: cd color_service && python detector.py ...
    import config
    from colour import estimate_colour_detailed
    from device import resolve_device
    from detection_schemas import Detection, DetectionColourReview
    from schemas import ColourObservation, VehicleProfile
    from temporal import compute_colour_crop_quality_details, decide_colour, make_colour_observation

CONF_THRESHOLD = config.CONF_THRESHOLD
MIN_BOX_AREA_FRAC = config.MIN_BOX_AREA_FRAC
PER_CLASS_CONF = config.PER_CLASS_CONF
TAXONOMY = config.TAXONOMY

log = logging.getLogger(__name__)


class VehicleDetector:
    def __init__(self, model_path: str, conf: float = CONF_THRESHOLD, device: str | None = None):
        self.model_path = model_path
        self.conf = conf
        self._model = None
        self.device = resolve_device(device)
        self._load_lock = threading.Lock()
        self._lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from ultralytics import YOLO
                self._model = YOLO(self.model_path)
                log.info("Loaded vehicle model on %s: %s", self.device, self.model_path)

    def predict(
        self,
        image: np.ndarray,
        include_person: bool = False,
        conf_override: Optional[float] = None,
    ) -> list[Detection]:
        if self._model is None:
            if not Path(self.model_path).exists():
                log.warning("Model weights not found at %s — returning []", self.model_path)
                return []
            self.load()

        effective_conf = conf_override if conf_override is not None else self.conf
        h, w = image.shape[:2]
        image_area = h * w

        with self._lock:
            results = self._model(
                image, conf=effective_conf, device=self.device, verbose=False
            )[0]

        detections: list[Detection] = []
        det_id = 0
        for box in results.boxes:
            cls_id = int(box.cls)
            conf_val = round(float(box.conf), 4)

            # Per-class confidence override (post-filter)
            if cls_id in PER_CLASS_CONF and conf_val < PER_CLASS_CONF[cls_id]:
                continue

            mapped_type = TAXONOMY.get(cls_id, "unknown")

            # Person is outside the canonical vehicle taxonomy. Preserve the
            # include_person compatibility switch by exposing it as unknown.
            if cls_id == 5 and not include_person:
                continue

            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]

            # Minimum box size gate
            box_area = max(0, x2 - x1) * max(0, y2 - y1)
            if image_area > 0 and (box_area / image_area) < MIN_BOX_AREA_FRAC:
                continue

            detections.append(Detection(
                id=det_id,
                bbox=[x1, y1, x2, y2],
                type=mapped_type,
                type_confidence=conf_val,
            ))
            det_id += 1

        return detections


class ColourPipeline:
    """Self-contained detection, tracking and temporal colour pipeline."""

    def __init__(self, model_path: str = config.VEHICLE_MODEL_PATH,
                 device: str | None = None, conf: float = config.TRACK_CONF):
        from ultralytics import YOLO
        self.device = resolve_device(device)
        self.conf = conf
        self.model = YOLO(model_path, task="detect")
        self.profiles: dict[int, VehicleProfile] = {}
        self.frame_index = 0
        self._agent = None

    def _agent_colour(self, crop: np.ndarray) -> tuple[str, float] | None:
        """Classify one uncertain crop through OpenRouter; initialize lazily."""
        if not config.COLOR_AGENT_ENABLED or not config.COLOR_AGENT_API_KEY:
            return None
        try:
            if self._agent is None:
                from langchain_openai import ChatOpenAI
                self._agent = ChatOpenAI(
                    model=config.COLOR_AGENT_MODEL,
                    api_key=config.COLOR_AGENT_API_KEY,
                    base_url=config.COLOR_AGENT_BASE_URL,
                    temperature=0,
                ).with_structured_output(DetectionColourReview)
            ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                return None
            data = base64.b64encode(encoded.tobytes()).decode("ascii")
            message = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Identify the dominant exterior paint colour of the vehicle. "
                        "Ignore windows, tyres, road, lights, shadows and reflections. "
                        "Use exactly one allowed label."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
                ],
            }]
            answer = self._agent.invoke(message)
            return answer.label, float(answer.confidence)
        except Exception as exc:
            log.warning("colour agent fallback failed: %s", exc)
            return None

    def _fallback_if_needed(self, profile: VehicleProfile) -> None:
        if (profile.colour != "unknown"
                and profile.colour_conf >= config.COLOR_AGENT_THRESHOLD):
            return
        crop = profile._best_crop
        if crop is None or not crop.size:
            return
        answer = self._agent_colour(crop)
        if answer is None:
            if config.COLOR_AGENT_ENABLED and not config.COLOR_AGENT_API_KEY:
                profile._colour_agent_error = "missing COLOR_AGENT_API_KEY/OPENROUTER_API_KEY"
            return
        label, confidence = answer
        if label in {
            "white", "black", "silver/gray", "red", "orange", "yellow",
            "green", "blue", "purple", "brown/beige",
        } and 0.0 <= confidence <= 1.0:
            profile.colour = label
            profile.colour_conf = round(confidence, 4)
            profile._colour_source = "agent_fallback"
            profile._colour_decision = "agent_fallback"

    @staticmethod
    def _apply(profile: VehicleProfile, decision) -> None:
        evidence = decision.evidence
        profile._colour_candidate = evidence.winner
        profile._colour_winner_share = evidence.winner_share
        profile._colour_runner_up = evidence.runner_up
        profile._colour_runner_up_share = evidence.runner_up_share
        profile._colour_margin = evidence.winner_margin
        profile._colour_decision = decision.reason
        if decision.finalized:
            profile.colour = decision.label
            profile.colour_conf = round(decision.confidence, 4)
            profile._colour_finalized = True

    def _observe(self, profile: VehicleProfile, crop: np.ndarray,
                 frame_shape: tuple[int, ...]) -> None:
        estimate = estimate_colour_detailed(
            crop,
            roi_mode=config.COLOR_ROI_MODE,
            normalization=config.COLOR_NORMALIZATION,
            neutral_mode=config.COLOR_NEUTRAL_MODE,
            feature_mode=config.COLOR_FEATURE_MODE,
        )
        quality = compute_colour_crop_quality_details(
            crop, frame_shape, profile.bbox, mode=config.COLOR_QUALITY_MODE
        )
        diagnostics = estimate.diagnostics()
        diagnostics["crop_quality"] = quality.__dict__
        observation = make_colour_observation(
            estimate.label, estimate.confidence, quality.combined, diagnostics
        )
        profile._colour_attempts += 1
        profile._last_colour_attempt = observation
        if observation.valid:
            profile._colour_samples.append(observation)
            self._apply(profile, decide_colour(profile._colour_samples))
        else:
            profile._unknown_colour_samples += 1

    def process_frame(self, frame: np.ndarray) -> list[VehicleProfile]:
        """Track vehicles in one BGR frame and update their colour profiles."""
        result = self.model.track(
            frame, persist=True, conf=self.conf, device=self.device,
            tracker=config.TRACKER_CONFIG, verbose=False,
        )[0]
        seen: set[int] = set()
        boxes = result.boxes
        if boxes is not None and boxes.id is not None:
            for box in boxes:
                if box.id is None:
                    continue
                track_id = int(box.id.item())
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                h, w = frame.shape[:2]
                x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
                y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                seen.add(track_id)
                profile = self.profiles.get(track_id)
                if profile is None:
                    profile = VehicleProfile(track_id=track_id, bbox=[x1, y1, x2, y2])
                    self.profiles[track_id] = profile
                profile.bbox = [x1, y1, x2, y2]
                profile.frames_seen += 1
                profile._last_seen_frame = self.frame_index
                cls_id = int(box.cls.item())
                profile.type = TAXONOMY.get(cls_id, "unknown")
                profile.type_conf = round(float(box.conf.item()), 4)
                crop = frame[y1:y2, x1:x2]
                crop_score = crop.shape[0] * crop.shape[1]
                if crop_score > profile._best_score:
                    profile._best_score = crop_score
                    profile._best_crop = crop.copy()
                if not profile._colour_finalized:
                    self._observe(profile, crop, frame.shape)
                    if profile._colour_finalized:
                        self._fallback_if_needed(profile)

        finalized: list[VehicleProfile] = []
        for track_id, profile in list(self.profiles.items()):
            if track_id not in seen and self.frame_index - profile._last_seen_frame > config.LOST_FRAMES:
                if not profile._colour_finalized:
                    self._apply(profile, decide_colour(profile._colour_samples, finalizing=True))
                    self._fallback_if_needed(profile)
                profile.status = "final"
                finalized.append(profile)
                del self.profiles[track_id]
        self.frame_index += 1
        return list(self.profiles.values()) + finalized

    def finalize_all(self) -> list[VehicleProfile]:
        """Finalize and remove all remaining tracks (for EOF/shutdown)."""
        finalized = []
        for profile in self.profiles.values():
            if not profile._colour_finalized:
                self._apply(profile, decide_colour(profile._colour_samples, finalizing=True))
                self._fallback_if_needed(profile)
            profile.status = "final"
            finalized.append(profile)
        self.profiles.clear()
        return finalized


def _public_profile(profile: VehicleProfile) -> dict:
    return {
        "track_id": profile.track_id,
        "bbox": profile.bbox,
        "type": profile.type,
        "type_confidence": profile.type_conf,
        "colour": profile.colour,
        "colour_confidence": profile.colour_conf,
        "colour_state": profile._colour_decision,
        "colour_source": profile._colour_source,
        "colour_agent_error": profile._colour_agent_error,
        "colour_samples": len(profile._colour_samples),
        "status": profile.status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone vehicle colour pipeline")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--video", type=Path)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", type=Path, help="optional annotated image/video")
    parser.add_argument("--jsonl", type=Path, help="optional per-frame JSONL results")
    args = parser.parse_args()

    pipeline = ColourPipeline(device=args.device)
    capture = cv2.VideoCapture(str(args.video or args.image))
    if not capture.isOpened():
        raise SystemExit(f"cannot open input: {args.video or args.image}")
    writer = None
    json_handle = args.jsonl.open("w", encoding="utf-8") if args.jsonl else None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            profiles = pipeline.process_frame(frame)
            # A still image has no temporal future. Expose its direct HSV result
            # instead of applying short-track rejection intended for video.
            if args.image:
                for profile in profiles:
                    observation = profile._last_colour_attempt
                    if observation and observation.valid:
                        profile.colour = observation.label
                        profile.colour_conf = observation.confidence
                        profile._colour_decision = "single_frame"
                        profile._colour_finalized = True
                    pipeline._fallback_if_needed(profile)
                    profile.status = "final"
            records = [_public_profile(p) for p in profiles]
            print(json.dumps({"frame": pipeline.frame_index - 1, "vehicles": records}))
            if json_handle:
                json_handle.write(json.dumps({"frame": pipeline.frame_index - 1, "vehicles": records}) + "\n")
            if args.output:
                for p in profiles:
                    x1, y1, x2, y2 = p.bbox
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 2)
                    cv2.putText(frame, f"{p.type} {p.colour} {p.colour_conf:.2f}",
                                (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.5, (0, 200, 255), 1, cv2.LINE_AA)
                if args.image:
                    cv2.imwrite(str(args.output), frame)
                else:
                    if writer is None:
                        fps = capture.get(cv2.CAP_PROP_FPS) or config.FPS
                        writer = cv2.VideoWriter(str(args.output), cv2.VideoWriter_fourcc(*"mp4v"),
                                                 fps, (frame.shape[1], frame.shape[0]))
                    writer.write(frame)
            if args.image:
                break
        if args.video:
            final_records = [_public_profile(p) for p in pipeline.finalize_all()]
            payload = {"frame": pipeline.frame_index, "event": "eof", "vehicles": final_records}
            print(json.dumps(payload))
            if json_handle:
                json_handle.write(json.dumps(payload) + "\n")
    finally:
        capture.release()
        if writer:
            writer.release()
        if json_handle:
            json_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
