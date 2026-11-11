import json
import logging
import re
import uuid
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .service import run_classification
from .vehicle_classification_manufacturer_service.manufacturer.config import MODEL_ID, MODEL_PARAMETERS, RecognitionConfig, parse_model_settings

log = logging.getLogger(__name__)

@api_view(["GET"])
def model_settings(request):
    return Response({"model": MODEL_ID, "fields": MODEL_PARAMETERS,
                     "defaults": RecognitionConfig.from_env().model_settings()})

@api_view(["POST"])
def analyze(request):
    upload = request.FILES.get("video")
    if upload is None: return Response({"error": "Attach a video in the 'video' field."}, status=400)
    job_id = uuid.uuid4().hex
    log.info("Vehicle job %s: receiving upload (%d bytes)", job_id, upload.size)
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name or ".mp4").suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}: suffix = ".mp4"
    source = job_dir / f"source{suffix}"
    with source.open("wb") as destination:
        for chunk in upload.chunks(): destination.write(chunk)
    try:
        log.info("Vehicle job %s: upload saved; validating analysis options", job_id)
        raw = request.data.get("max_frames")
        max_frames = int(raw) if raw not in (None, "") else None
        if max_frames is not None and max_frames < 1: raise ValueError("max_frames must be positive.")
        enabled = request.data.get("enable_manufacturer")
        if enabled is not None:
            if str(enabled).lower() not in {"true", "false", "1", "0"}:
                raise ValueError("enable_manufacturer must be true or false")
            enabled = str(enabled).lower() in {"true", "1"}
        options = parse_model_settings(request.data.get("model_settings"))
        return Response(run_classification(source, job_id=job_id, max_frames=max_frames, enable_manufacturer=enabled, model_settings=options))
    except (ValueError, TypeError) as exc: return Response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("Vehicle job %s failed", job_id)
        return Response({"error": f"Pipeline failed: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def artifact(request, job_id, filename):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or filename not in {"annotated.mp4", "results.json"}: raise Http404
    path = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id / filename
    if not path.is_file(): raise Http404
    return FileResponse(path.open("rb"), content_type="video/mp4" if filename.endswith(".mp4") else "application/json", as_attachment=False, filename=filename)


@api_view(["GET"])
def jobs(request):
    root = Path(settings.MEDIA_ROOT) / "vehicle_classification"
    reports = sorted(root.glob("*/results.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    rows = []
    for path in reports[:50]:
        if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(result, dict) or result.get("job_id") != path.parent.name or "report_url" not in result:
            continue
        rows.append({"job_id": result["job_id"], "tracks_detected": result.get("tracks_detected", 0),
                     "report_url": result["report_url"]})
    return Response(rows)


@api_view(["GET"])
def crop(request, job_id, filename):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or not re.fullmatch(r"track_[0-9]+_[0-9]+_(early|best|attempt_[1-5])\.jpg", filename):
        raise Http404
    path = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id / "crops" / filename
    if not path.is_file():
        raise Http404
    return FileResponse(path.open("rb"), content_type="image/jpeg")
