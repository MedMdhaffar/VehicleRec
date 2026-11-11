from django.urls import path

from .views import analyze, artifact, jobs, crop, model_settings


urlpatterns = [
    path("settings/", model_settings, name="classification-manufacturer-settings"),
    path("jobs/", jobs, name="classification-manufacturer-jobs"),
    path("jobs/<str:job_id>/crops/<str:filename>/", crop, name="classification-manufacturer-crop"),
    path("analyze/", analyze, name="classification-manufacturer-analyze"),
    path("jobs/<str:job_id>/<str:filename>/", artifact, name="classification-manufacturer-artifact"),
]
