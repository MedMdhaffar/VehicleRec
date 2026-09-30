from django.urls import path

from .views import analyze, artifact, health, rtsp_preview, rtsp_session, stop_rtsp_session


urlpatterns = [
    path("health/", health, name="license-plate-health"),
    path("analyze/", analyze, name="license-plate-analyze"),
    path("rtsp/", rtsp_session, name="license-plate-rtsp"),
    path("rtsp/stop/", stop_rtsp_session, name="license-plate-rtsp-stop"),
    path("rtsp/preview/", rtsp_preview, name="license-plate-rtsp-preview"),
    path("jobs/<str:job_id>/<str:filename>/", artifact, name="license-plate-artifact"),
]
