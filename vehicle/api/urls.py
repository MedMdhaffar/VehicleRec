from django.urls import path

from .views import health
from . import console_views


urlpatterns = [
    path("t1/stack/", console_views.stack_status, name="t1-stack"),
    path("health/", health, name="health"),
    path("t1/runs/", console_views.runs, name="t1-runs"),
    path("t1/runs/<uuid:run_id>/", console_views.run_detail, name="t1-run"),
    path("t1/runs/<uuid:run_id>/stop/", console_views.run_stop, name="t1-stop"),
    path("t1/runs/<uuid:run_id>/preview/", console_views.run_preview, name="t1-preview"),
    path("t1/runs/<uuid:run_id>/tracks/<int:track_id>/crops/<str:kind>/", console_views.run_crop, name="t1-crop"),
]
