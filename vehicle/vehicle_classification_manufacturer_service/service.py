"""Connect Django requests directly to the vehicle analysis pipeline."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from django.conf import settings

from .vehicle_classification_manufacturer_service.manufacturer.config import recognition_enabled
from .vehicle_classification_manufacturer_service.service import VehicleAnalysisService


log = logging.getLogger(__name__)
_pipeline_lock = threading.Lock()


def run_classification(source: Path, *, job_id: str, max_frames: int | None = None, enable_manufacturer: bool | None = None, model_settings: dict | None = None) -> dict:
    started = time.monotonic()
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output, report = job_dir / "annotated.mp4", job_dir / "results.json"
    enabled = recognition_enabled() if enable_manufacturer is None else enable_manufacturer
    log.info("Vehicle job %s: waiting for the analysis pipeline", job_id)
    # Each job owns its models and tracker. Bound concurrent GPU allocations
    # within this Django process and release them before the next job starts.
    with _pipeline_lock:
        log.info("Vehicle job %s: starting analysis; VLM recognition=%s", job_id, enabled)
        pipeline = VehicleAnalysisService(
            device="auto", enable_manufacturer=enabled,
            model_settings=model_settings,
        )
        try:
            result = pipeline.process_video(source, output_path=output, max_frames=max_frames)
        finally:
            pipeline.close()
    if output.is_file():
        from license_plate_service.video import prepare_browser_video
        log.info("Vehicle job %s: encoding annotated video for browser playback", job_id)
        prepare_browser_video(output)
        log.info("Vehicle job %s: browser encoding complete", job_id)
    for vehicle in result.get("vehicles", []):
        base = f"/api/classification-manufacturer/jobs/{job_id}/crops/"
        vehicle["selected_crops"] = {label: base + filename + "/" for label, filename in vehicle.get("selected_crops", {}).items()}
        for attempt in vehicle.get("recognition_evidence", []):
            if attempt.get("crop"):
                attempt["crop_url"] = base + attempt["crop"] + "/"
    result.update({"service": "vehicle-classification-manufacturer", "job_id": job_id, "output_video_url": f"/api/classification-manufacturer/jobs/{job_id}/annotated.mp4/", "report_url": f"/api/classification-manufacturer/jobs/{job_id}/results.json/"})
    report.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    log.info("Vehicle job %s: complete in %.1fs; tracks=%s frames=%s", job_id, time.monotonic() - started, result.get("tracks_detected", 0), result.get("frames_processed", 0))
    return result
