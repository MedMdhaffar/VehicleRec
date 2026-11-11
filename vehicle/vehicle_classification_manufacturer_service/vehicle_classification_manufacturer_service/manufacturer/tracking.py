"""Synchronous bounded crop collection and independent evidence consensus."""
from dataclasses import dataclass, field
import time
import logging
from pathlib import Path
import cv2
from .config import MODEL_ID
from .crop_quality import CropCandidate
from .crop_selector import BestCropSelector, visual_signature, signature_distance

log = logging.getLogger(__name__)

@dataclass
class TrackRecognition:
    status: str = "disabled"
    manufacturer: str = "unknown"
    body_type: str = "unknown"
    color: str = "unknown"
    attempts: list = field(default_factory=list)
    candidate: object = None
    quality: float = -1.0
    last_frame: int = -100000
    used_signatures: list = field(default_factory=list)
    last_quality: float = -1.0
    failure_reason: str = ""
    key: str = ""
    crops: dict = field(default_factory=dict)
    best_saved_quality: float = -1.0

    def to_dict(self):
        successes = [a for a in self.attempts if "result" in a]
        return {"manufacturer": self.manufacturer, "vlm_vehicle_type": self.body_type, "color": self.color,
                "recognition_status": self.status,
                "decision_source": "local_lfm" if successes else "none",
                "recognition_attempts": len(self.attempts), "supporting_crops": len(successes),
                "manufacturer_supporting_crops": sum(a["result"]["manufacturer"] == self.manufacturer for a in successes) if self.manufacturer != "unknown" else 0,
                "body_type_supporting_crops": sum(a["result"]["vehicle_type"] == self.body_type for a in successes) if self.body_type != "unknown" else 0,
                "color_supporting_crops": sum(a["result"]["color"] == self.color for a in successes) if self.color != "unknown" else 0,
                "recognition_seconds": sum(a["seconds"] for a in self.attempts),
                "recognition_model": MODEL_ID if self.status != "disabled" else None,
                "selected_crops": self.crops, "recognition_evidence": self.attempts, "recognition_failure": self.failure_reason}

class TrackRecognizer:
    def __init__(self, recognizer, config):
        self.recognizer, self.config = recognizer, config
        self.selector = BestCropSelector()
        self.output_dir = None

    def observe(self, state, frame, bbox, frame_index, frames_seen):
        if len(state.attempts) >= self.config.max_attempts or frame_index - state.last_frame < self.config.crop_gap:
            return
        state.last_frame = frame_index
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return
        crop = frame[y1:y2, x1:x2]
        scale = min(1.0, self.config.max_crop_side / max(crop.shape[:2])) if self.config.max_crop_side else 1.0
        if min(crop.shape[:2]) < 32:
            return
        if crop.nbytes > self.config.max_pending_crop_mb * 1024 * 1024:
            state.failure_reason = "crop_memory_budget_exceeded"
            return
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale)))) if scale < 1 else crop.copy()
        candidate = CropCandidate(crop, frame_index, bbox=tuple(bbox), frame_size=(width, height))
        selection = self.selector.select([candidate], max_crops=1, min_crops=1)
        if not selection.selected:
            return
        score = selection.selected_evaluations[0].quality.score
        if "early" not in state.crops:
            self.save_crop(state, "early", crop)
        if score > state.best_saved_quality:
            self.save_crop(state, "best", crop)
            state.best_saved_quality = score
        signature = visual_signature(crop)
        if any(signature_distance(signature, previous) <= 0.20 for previous in state.used_signatures):
            return
        if score > state.quality:
            state.candidate, state.quality = candidate, score
        initial = not state.attempts and frames_seen >= self.config.initial_frames
        better = bool(state.attempts) and state.quality > state.last_quality + 0.05
        retry = bool(state.attempts) and state.attempts[-1].get("error") == "invalid_model_json"
        if initial or better or retry:
            self.attempt(state)

    def save_crop(self, state, label, image):
        if self.output_dir is None:
            return None
        directory = Path(self.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{state.key}_{label}.jpg"
        if not cv2.imwrite(str(directory / filename), image):
            raise RuntimeError("Could not save vehicle crop")
        state.crops[label] = filename
        return filename

    def attempt(self, state):
        if state.candidate is None or len(state.attempts) >= self.config.max_attempts:
            return
        candidate = state.candidate
        state.candidate = None
        state.last_quality = state.quality
        state.quality = -1.0
        started = time.perf_counter()
        record = {"frame_index": candidate.frame_index, "quality_score": state.last_quality}
        log.info("%s: VLM attempt %d/%d frame=%s crop=%dx%d quality=%.3f", state.key, len(state.attempts) + 1, self.config.max_attempts, candidate.frame_index, candidate.image.shape[1], candidate.image.shape[0], state.last_quality)
        try:
            record["crop"] = self.save_crop(state, f"attempt_{len(state.attempts) + 1}", candidate.image)
            retry_invalid = bool(state.attempts) and state.attempts[-1].get("error") == "invalid_model_json"
            if retry_invalid:
                record["retry_reason"] = "invalid_model_json"
                result = self.recognizer.predict(candidate.image, retry_invalid=True)
            else:
                result = self.recognizer.predict(candidate.image)
            record["result"] = result.to_dict()
            state.used_signatures.append(visual_signature(candidate.image))
        except Exception as exc:
            record["error"] = str(exc) if isinstance(exc, ValueError) and str(exc) in {"invalid_model_json", "invalid_crop"} else type(exc).__name__
            if hasattr(exc, "raw_output"):
                record["raw_output"] = exc.raw_output
            log.warning("%s: recognition attempt failed (%s); raw_output=%r", state.key, record["error"], record.get("raw_output", "unavailable"))
        record["seconds"] = time.perf_counter() - started
        state.attempts.append(record)
        self.aggregate(state)
        log.info("%s: attempt finished in %.2fs; prediction=%s selected=%s/%s/%s status=%s", state.key, record["seconds"], record.get("result", record.get("error")), state.manufacturer, state.body_type, state.color, state.status)

    @staticmethod
    def aggregate(state):
        valid = [a["result"] for a in state.attempts if "result" in a]
        selected = valid[-1] if valid else {}
        for key, attr in (("manufacturer", "manufacturer"), ("vehicle_type", "body_type"), ("color", "color")):
            setattr(state, attr, selected.get(key, "unknown"))
        state.status = "recognized" if state.manufacturer != "unknown" or state.body_type != "unknown" or state.color != "unknown" else ("unknown" if valid else "error")

    def finish(self, state):
        self.attempt(state)
        state.candidate = None
        if not state.attempts:
            state.status = "unknown"
            state.failure_reason = state.failure_reason or "no_usable_crop"
        elif state.status == "unknown":
            state.failure_reason = "no_known_vlm_labels"
