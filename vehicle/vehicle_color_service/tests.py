import tempfile
from pathlib import Path
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

class VehicleColorApiTests(TestCase):
    def test_health(self):
        response = self.client.get("/api/color/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_video_is_required(self):
        self.assertEqual(self.client.post("/api/color/analyze/").status_code, 400)

    def test_upload_is_forwarded_to_pipeline(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with patch("vehicle_color_service.views.run_color_analysis", return_value={"vehicles": []}) as run:
                response = self.client.post("/api/color/analyze/", {"video": SimpleUploadedFile("clip.mp4", b"data")})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(Path(run.call_args.args[0]).is_file())
