from django.urls import path

from .views import analyze, artifact, health


urlpatterns = [
    path("health/", health, name="vehicle-color-health"),
    path("analyze/", analyze, name="vehicle-color-analyze"),
    path("jobs/<str:job_id>/<str:filename>/", artifact, name="vehicle-color-artifact"),
]
