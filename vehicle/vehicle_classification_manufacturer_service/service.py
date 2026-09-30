"""Adapter for the standalone vehicle classification/manufacturer pipeline."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from django.conf import settings

def run_classification(source: Path, *, job_id: str, max_frames: int | None = None) -> dict:
    app_root = Path(__file__).resolve().parent
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output, report = job_dir / "annotated.mp4", job_dir / "results.json"
    command = [sys.executable, "-m", "vehicle_classification_manufacturer_service.cli", "--video", str(source), "--device", "auto", "--output", str(output), "--json-output", str(report)]
    if max_frames is not None: command += ["--max-frames", str(max_frames)]
    completed = subprocess.run(command, cwd=app_root, capture_output=True, text=True, timeout=3600)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        raise RuntimeError(detail[0] if detail else "Classification pipeline failed")
    if not report.is_file():
        candidates = list(job_dir.glob("*.json"))
        if candidates:
            report = candidates[0]
        else:
            detail = (completed.stdout or completed.stderr).strip()
            raise RuntimeError(
                "Classification pipeline produced no report. "
                f"Output: {detail[-500:]}"
            )
    result = json.loads(report.read_text(encoding="utf-8"))
    result.update({"service": "vehicle-classification-manufacturer", "job_id": job_id, "output_video_url": f"/api/classification-manufacturer/jobs/{job_id}/annotated.mp4/", "report_url": f"/api/classification-manufacturer/jobs/{job_id}/results.json/"})
    return result
