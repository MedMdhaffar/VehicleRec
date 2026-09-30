import re
import uuid
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .service import run_color_analysis

@api_view(["GET"])
def health(request):
    return Response({"service": "vehicle-color", "status": "ready"})

@api_view(["POST"])
def analyze(request):
    upload = request.FILES.get("video")
    if upload is None:
        return Response({"error": "Attach a video in the 'video' field."}, status=400)
    job_id = uuid.uuid4().hex
    job_dir = Path(settings.MEDIA_ROOT) / "vehicle_color" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name or ".mp4").suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}: suffix = ".mp4"
    source = job_dir / f"source{suffix}"
    with source.open("wb") as destination:
        for chunk in upload.chunks(): destination.write(chunk)
    try:
        return Response(run_color_analysis(source, job_id=job_id))
    except Exception as exc:
        return Response({"error": f"Pipeline failed: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def artifact(request, job_id, filename):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or filename not in {"annotated.mp4", "results.jsonl"}: raise Http404
    path = Path(settings.MEDIA_ROOT) / "vehicle_color" / job_id / filename
    if not path.is_file(): raise Http404
    return FileResponse(path.open("rb"), content_type="video/mp4" if filename.endswith(".mp4") else "application/x-ndjson", as_attachment=False, filename=filename)
