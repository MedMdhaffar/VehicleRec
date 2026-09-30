"""Adapter between Django and the standalone vehicle-colour detector."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from django.conf import settings

def run_color_analysis(source: Path, *, job_id: str, device: str = "auto") -> dict:
    app_root = Path(__file__).resolve().parent
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_color" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output, jsonl = job_dir / "annotated.mp4", job_dir / "results.jsonl"
    command = [sys.executable, "-m", "vehicle_color_service.detector", "--video", str(source), "--device", device, "--output", str(output), "--jsonl", str(jsonl)]
    completed = subprocess.run(command, cwd=app_root, capture_output=True, text=True, timeout=3600)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        raise RuntimeError(detail[0] if detail else "Vehicle colour pipeline failed")
    frames = []
    for line in completed.stdout.splitlines():
        try:
            payload = json.loads(line)
            if isinstance(payload, dict) and "vehicles" in payload: frames.append(payload)
        except json.JSONDecodeError: pass
    vehicles = {}
    for frame in frames:
        for vehicle in frame.get("vehicles", []): vehicles[str(vehicle["track_id"])] = vehicle
    return {"service": "vehicle-color", "job_id": job_id, "frames": len(frames), "vehicles": list(vehicles.values()), "output_video_url": f"/api/color/jobs/{job_id}/annotated.mp4/", "results_url": f"/api/color/jobs/{job_id}/results.jsonl/"}
