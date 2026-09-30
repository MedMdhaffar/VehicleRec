import re
import uuid
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .service import run_classification

@api_view(["GET"])
def health(request):
    return Response({"service": "vehicle-classification-manufacturer", "status": "ready"})

@api_view(["POST"])
def analyze(request):
    upload = request.FILES.get("video")
    if upload is None: return Response({"error": "Attach a video in the 'video' field."}, status=400)
    job_id = uuid.uuid4().hex
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name or ".mp4").suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}: suffix = ".mp4"
    source = job_dir / f"source{suffix}"
    with source.open("wb") as destination:
        for chunk in upload.chunks(): destination.write(chunk)
    try:
        raw = request.data.get("max_frames")
        max_frames = int(raw) if raw not in (None, "") else None
        if max_frames is not None and max_frames < 1: raise ValueError("max_frames must be positive.")
        return Response(run_classification(source, job_id=job_id, max_frames=max_frames))
    except (ValueError, TypeError) as exc: return Response({"error": str(exc)}, status=400)
    except Exception as exc: return Response({"error": f"Pipeline failed: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def artifact(request, job_id, filename):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or filename not in {"annotated.mp4", "results.json"}: raise Http404
    path = Path(settings.MEDIA_ROOT) / "vehicle_classification" / job_id / filename
    if not path.is_file(): raise Http404
    return FileResponse(path.open("rb"), content_type="video/mp4" if filename.endswith(".mp4") else "application/json", as_attachment=False, filename=filename)
