import tempfile
import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch, Mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from .video import prepare_browser_video


class BrowserVideoTests(SimpleTestCase):
    @skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
    def test_output_is_h264_with_browser_pixel_format_and_faststart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "annotated.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=blue:s=64x48:r=5", "-t", "1", "-c:v", "mpeg4", str(path)],
                check=True, capture_output=True,
            )
            prepare_browser_video(path)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,pix_fmt,width,height,nb_frames", "-of", "json", str(path)],
                check=True, capture_output=True, text=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "h264")
            self.assertEqual(stream["pix_fmt"], "yuv420p")
            self.assertEqual((stream["width"], stream["height"]), (64, 48))
            self.assertEqual(stream["nb_frames"], "5")
            data = path.read_bytes()
            self.assertLess(data.index(b"moov"), data.index(b"mdat"))

    def test_failed_encoding_preserves_original_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "annotated.mp4"
            path.write_bytes(b"original video")
            with patch("license_plate_service.video.shutil.which", return_value="ffmpeg"), patch(
                "license_plate_service.video.subprocess.run",
                return_value=Mock(returncode=1, stderr="encoder failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                    prepare_browser_video(path)
            self.assertEqual(path.read_bytes(), b"original video")
            self.assertEqual(list(Path(directory).iterdir()), [path])


class VideoProgressTests(SimpleTestCase):
    def test_heartbeat_logs_during_blocking_work_and_stops_on_error(self):
        import threading
        from .progress import video_request_progress
        heartbeat_seen = threading.Event()
        def capture(message, *args):
            if 'still active' in message:
                heartbeat_seen.set()
        with patch('license_plate_service.progress.log.info', side_effect=capture) as logged:
            with self.assertRaisesRegex(RuntimeError, 'inference failed'):
                with video_request_progress('test-heartbeat', interval=.01):
                    self.assertTrue(heartbeat_seen.wait(2))
                    raise RuntimeError('inference failed')
            self.assertIn('request reached Django', logged.call_args_list[0].args[0])
            self.assertIn('request finished', logged.call_args_list[-1].args[0])
        self.assertFalse(any(t.name == 'video-progress-test-heartbeat' for t in threading.enumerate()))


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


class ClassifierServiceTests(SimpleTestCase):
    def setUp(self):
        from .service import _ensure_pipeline_import_path
        _ensure_pipeline_import_path()

    def test_video_adapter_injects_factory_without_eager_crop_classification(self):
        from .service import run_video_analysis
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root), patch(
            'run_video_pipeline.classification.create_classifier'
        ) as factory, patch('run_video_pipeline.ocr.PlateReader'), patch(
            'run_video_pipeline.prototype_pipeline.PrototypePlatePipeline'
        ) as pipeline, patch('run_video_pipeline.run_video_prototype.VideoPrototypeRunner') as runner, patch(
            'run_video_pipeline.run_video_prototype._build_tracker'
        ), patch('license_plate_service.service.prepare_browser_video'), patch('cv2.VideoCapture') as capture:
            capture.return_value.isOpened.return_value = True
            capture.return_value.get.return_value = 25
            runner.return_value.run.return_value = {}
            run_video_analysis(Path(media_root)/'source.mp4', job_id='test')
            self.assertIs(pipeline.call_args.kwargs['classifier_factory'], factory)
            self.assertTrue(runner.call_args.args[1].model_path.endswith('yolo-v9-t-640-license-plates-end2end.onnx'))
            factory.assert_not_called()
            capture.return_value.release.assert_called_once()

    def test_rtsp_startup_failure_reaches_session_error_state(self):
        import threading
        from types import SimpleNamespace
        from .service import RTSPManager
        manager = RTSPManager()
        args = SimpleNamespace(record_output=None)
        with patch('run_rtsp_pipeline.run_rtsp_prototype.run_rtsp', side_effect=RuntimeError(
            'Plate category classifier initialization failed: index incompatible'
        )):
            manager._run(args, threading.Event(), 'test')
        state = manager.snapshot()
        self.assertFalse(state['running'])
        self.assertEqual(state['status'], 'error')
        self.assertIn('index incompatible', state['error'])


class RemovedRemoteOptionTests(SimpleTestCase):
    def test_video_rejects_legacy_option_even_when_false(self):
        response = self.client.post('/api/license-plate/analyze/', {'agent_enabled': 'false'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'removed_option')

    def test_camera_rejects_legacy_option(self):
        response = self.client.post('/api/license-plate/rtsp/', {'agent_enabled': False}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('local only', response.json()['error'])


class WholePlateApiTests(SimpleTestCase):
    def test_upload_preserves_formatted_results_and_hides_rejected_numbers(self):
        """Real policy/storage/compact projection; inference and video adapter mocked."""
        import numpy as np
        from .service import _ensure_pipeline_import_path
        _ensure_pipeline_import_path()
        from run_video_pipeline.data_capture import ReviewStore
        from run_video_pipeline.ocr import OCRResult
        from run_video_pipeline.plate_detector import PlateCrop
        from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
        from run_video_pipeline.policies import compact_result
        from run_video_pipeline.schemas import CategoryClassification
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            reader = Mock()
            marker = Mock()
            pipeline = PrototypePlatePipeline(reader, marker, ReviewStore(Path(directory)/'captures'),
                                               classifier_factory=lambda: Mock())
            pipeline.initialize_classifier()
            for category, text, expected in [('nt', '0012', '0012 nt'),
                                               ('idara', '09-0012', '09 - 0012'),
                                               ('shorta', '0012', 'shorta 0012'),
                                               ('idara', '090012', '')]:
                reader.read_result.return_value = OCRResult(text, text, .99)
                with patch('run_video_pipeline.pipeline.classify_crop', return_value=CategoryClassification(
                    status='completed', final_category=category
                )):
                    result = pipeline.process_crop(PlateCrop(np.zeros((20, 120, 3), dtype=np.uint8), (0, 0, 120, 20), .99))
                compact = compact_result(result)
                with patch('license_plate_service.views.run_video_analysis', return_value={'tracks': {'1': compact}}):
                    response = self.client.post('/api/license-plate/analyze/', {
                        'video': SimpleUploadedFile('clip.mp4', b'mocked-video'), 'max_frames': '1',
                    })
                self.assertEqual(response.status_code, 200)
                row = response.json()['tracks']['1']
                self.assertEqual(row['plate_text'], expected)
                self.assertEqual(row['normalized_number'], expected or None)
                if not expected:
                    self.assertEqual(row['registration_fields'], {})
                    self.assertEqual(row['unresolved_reason'], 'missing_idara_separator')
            marker.locate.assert_not_called()


class DetectorStartupApiTests(SimpleTestCase):
    def test_detector_missing_assets_and_provider_failures_are_reported(self):
        for failure in (FileNotFoundError('Local t-640 weights missing'), RuntimeError('ONNX provider fallback rejected')):
            with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
                'license_plate_service.views.run_video_analysis', side_effect=failure
            ):
                response = self.client.post('/api/license-plate/analyze/', {
                    'video': SimpleUploadedFile('clip.mp4', b'mocked video'), 'max_frames': '1',
                })
            self.assertEqual(response.status_code, 500)
            self.assertIn(str(failure), response.json()['error'])
