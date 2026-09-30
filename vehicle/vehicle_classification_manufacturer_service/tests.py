import tempfile
from pathlib import Path
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

class ClassificationApiTests(TestCase):
    def test_health(self):
        response = self.client.get("/api/classification-manufacturer/health/")
        self.assertEqual(response.status_code, 200)

    def test_video_required(self):
        self.assertEqual(self.client.post("/api/classification-manufacturer/analyze/").status_code, 400)

    def test_upload_forwarded(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with patch("vehicle_classification_manufacturer_service.views.run_classification", return_value={"vehicles": []}) as run:
                response = self.client.post("/api/classification-manufacturer/analyze/", {"video": SimpleUploadedFile("clip.mp4", b"data")})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(Path(run.call_args.args[0]).is_file())
