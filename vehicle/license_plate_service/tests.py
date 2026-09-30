import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings


class LicensePlateApiTests(TestCase):
    def test_health(self):
        response = self.client.get("/api/license-plate/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_video_is_required(self):
        response = self.client.post("/api/license-plate/analyze/")
        self.assertEqual(response.status_code, 400)

    def test_video_pipeline_receives_saved_upload(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with patch("license_plate_service.views.run_video_analysis") as run:
                run.return_value = {"frames": 2, "tracks": {}}
                response = self.client.post(
                    "/api/license-plate/analyze/",
                    {"video": SimpleUploadedFile("clip.mp4", b"video-bytes"), "max_frames": "2"},
                )
                self.assertEqual(response.status_code, 200)
                source = run.call_args.args[0]
                self.assertTrue(Path(source).is_file())
                self.assertEqual(run.call_args.kwargs["max_frames"], 2)

    def test_rtsp_url_is_validated(self):
        response = self.client.post(
            "/api/license-plate/rtsp/", {"url": "https://example.com/video"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_artifact_path_is_restricted(self):
        response = self.client.get("/api/license-plate/jobs/not-a-job/annotated.mp4/")
        self.assertEqual(response.status_code, 404)
