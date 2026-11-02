from django.urls import path

from . import debug_views
from .views import analysis_options, analyze, artifact, rtsp_preview, rtsp_session, stop_rtsp_session


urlpatterns = [
    path("settings/", analysis_options, name="license-plate-settings"),
    path("jobs/", debug_views.jobs, name="license-plate-jobs"),
    path("jobs/<str:job_id>/debug/", debug_views.job_detail, name="license-plate-debug"),
    path("jobs/<str:job_id>/debug/crops/<str:filename>/", debug_views.crop, name="license-plate-crop"),
    path("analyze/", analyze, name="license-plate-analyze"),
    path("rtsp/", rtsp_session, name="license-plate-rtsp"),
    path("rtsp/stop/", stop_rtsp_session, name="license-plate-rtsp-stop"),
    path("rtsp/preview/", rtsp_preview, name="license-plate-rtsp-preview"),
    path("jobs/<str:job_id>/<str:filename>/", artifact, name="license-plate-artifact"),
]
