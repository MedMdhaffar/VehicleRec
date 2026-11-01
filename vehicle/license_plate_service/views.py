import logging
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .service import rtsp_manager, run_video_analysis
from .progress import video_request_progress


log = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    return Response({"service": "license-plate", "status": "ready"})


@api_view(["POST"])
def analyze(request):
    job_id = uuid.uuid4().hex
    with video_request_progress(job_id):
        return _analyze(request, job_id)


def _analyze(request, job_id):
    if "agent_enabled" in request.data:
        return Response({"error": "agent_enabled was removed; license-plate recognition is local only", "code": "removed_option"}, status=400)
    upload = request.FILES.get("video")
    if upload is None:
        return Response({"error": "Attach a video in the 'video' field."}, status=400)
    log.info("Video job %s: receiving upload (%d bytes)", job_id, upload.size)
    job_dir = Path(settings.MEDIA_ROOT) / "license_plate" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name).suffix.lower() if upload.name else ".mp4"
    suffix = suffix if suffix in {".mp4", ".avi", ".mov", ".mkv", ".webm"} else ".mp4"
    source = job_dir / f"source{suffix}"
    with source.open("wb") as destination:
        for chunk in upload.chunks():
            destination.write(chunk)
    try:
        raw_max_frames = request.data.get("max_frames")
        max_frames = int(raw_max_frames) if raw_max_frames not in (None, "") else None
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be positive.")
        result = run_video_analysis(
            source, job_id=job_id, max_frames=max_frames,
        )
        return Response(result)
    except (ValueError, TypeError) as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        log.exception("Video job %s failed", job_id)
        return Response({"error": f"Pipeline failed: {exc}"}, status=500)


@api_view(["GET", "POST"])
def rtsp_session(request):
    if request.method == "GET":
        return Response(rtsp_manager.snapshot())
    try:
        return Response(rtsp_manager.start(request.data), status=status.HTTP_202_ACCEPTED)
    except (ValueError, TypeError) as exc:
        return Response({"error": str(exc)}, status=400)
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=409)
    except Exception as exc:
        return Response({"error": f"Pipeline is unavailable: {exc}"}, status=500)


@api_view(["POST"])
def stop_rtsp_session(request):
    return Response(rtsp_manager.stop())


@api_view(["GET"])
def rtsp_preview(request):
    frame = rtsp_manager.preview()
    if frame is None:
        return Response({"error": "No preview frame is available yet."}, status=404)
    return HttpResponse(frame, content_type="image/jpeg")


@api_view(["GET"])
def artifact(request, job_id, filename):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or filename not in {"annotated.mp4", "recording.mp4"}:
        raise Http404
    path = Path(settings.MEDIA_ROOT) / "license_plate" / job_id / filename
    if not path.is_file():
        raise Http404
    return FileResponse(path.open("rb"), content_type="video/mp4", as_attachment=False, filename=filename)
