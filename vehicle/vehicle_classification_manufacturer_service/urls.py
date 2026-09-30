from django.urls import path

from .views import analyze, artifact, health


urlpatterns = [
    path("health/", health, name="classification-manufacturer-health"),
    path("analyze/", analyze, name="classification-manufacturer-analyze"),
    path("jobs/<str:job_id>/<str:filename>/", artifact, name="classification-manufacturer-artifact"),
]
