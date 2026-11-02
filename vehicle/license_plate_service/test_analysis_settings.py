import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from .analysis_settings import parse_settings, pipeline_config
from .service import _ensure_pipeline_import_path, run_video_analysis


class AnalysisSettingsTests(SimpleTestCase):
    def setUp(self):
        _ensure_pipeline_import_path()

    def test_defaults_are_the_active_config_and_runner_values(self):
        from run_video_pipeline import config
        from run_video_pipeline.run_video_prototype import RUNNER_DEFAULTS
        response = self.client.get('/api/license-plate/settings/')
        self.assertEqual(response.status_code, 200)
        defaults = {field['key']: field['default'] for field in response.json()['fields']}
        self.assertEqual(defaults['PLATE_CONF'], config.PLATE_CONF)
        self.assertEqual(defaults['WHOLE_PLATE_MIN_OCR_CONF'], config.WHOLE_PLATE_MIN_OCR_CONF)
        self.assertEqual(defaults['retry_improvement'], RUNNER_DEFAULTS['retry_improvement'])
        self.assertEqual(parse_settings(), defaults)

    def test_rejects_bad_values_and_fixed_model_settings_before_inference(self):
        invalid = [[], 'bad json', {'PLATE_CONF': 0}, {'PLATE_CONF': True},
                   {'PLATE_CONF': '0.4'}, {'PLATE_CONF': float('nan')},
                   {'min_candidates': 1.5}, {'retry_improvement': 1},
                   {'track_low_thresh': .9, 'track_high_thresh': .3},
                   {'DETECT_IMGSZ': 1280}, {'device': 'cpu'}]
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
            'license_plate_service.views.run_video_analysis'
        ) as analyze:
            for payload in invalid:
                with self.subTest(payload=payload):
                    response = self.client.post('/api/license-plate/analyze/', {
                        'video': SimpleUploadedFile('clip.mp4', b'video'),
                        'analysis_settings': payload if isinstance(payload, str) else json.dumps(payload),
                    })
                    self.assertEqual(response.status_code, 400)
            analyze.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_upload_passes_and_persists_effective_settings(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
            'license_plate_service.views.run_video_analysis', return_value={'tracks': {}}
        ) as analyze:
            response = self.client.post('/api/license-plate/analyze/', {
                'video': SimpleUploadedFile('clip.mp4', b'video'),
                'analysis_settings': json.dumps({'PLATE_CONF': .7, 'track_buffer': 30}),
            })
            self.assertEqual(response.status_code, 200)
            settings = analyze.call_args.kwargs['analysis_settings']
            self.assertEqual(settings['PLATE_CONF'], .7)
            self.assertEqual(settings['track_buffer'], 30)
            summary = next(Path(directory).glob('license_plate/*/summary.json'))
            self.assertEqual(json.loads(summary.read_text())['analysis_settings'], settings)
            reopened = self.client.get(f'/api/license-plate/jobs/{summary.parent.name}/debug/')
            self.assertEqual(reopened.json()['analysis_settings'], settings)

    def test_service_forwards_detection_tracking_runner_and_worker_settings(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch(
            'run_video_pipeline.prototype_pipeline.PrototypePlatePipeline'
        ) as pipeline, patch('run_video_pipeline.run_video_prototype.VideoPrototypeRunner') as runner, patch(
            'run_video_pipeline.run_video_prototype._build_tracker'
        ) as tracker, patch('license_plate_service.service.prepare_browser_video'), patch('cv2.VideoCapture') as capture:
            capture.return_value.isOpened.return_value = True
            capture.return_value.get.return_value = 25
            runner.return_value.run.return_value = {}
            metrics = run_video_analysis(Path(directory) / 'clip.mp4', job_id='test', analysis_settings={
                'PLATE_CONF': .6, 'PLATE_DEDUP_IOU': .4, 'MARKER_INFERENCE_CONF': .2,
                'track_buffer': 12, 'retry_improvement': 2, 'association_padding': .1,
            })
            self.assertEqual(runner.return_value.run.call_args.kwargs['conf'], .6)
            self.assertEqual(runner.call_args.kwargs['dedup_iou'], .4)
            self.assertEqual(runner.call_args.kwargs['retry_improvement'], 2)
            self.assertEqual(runner.call_args.kwargs['association_padding'], .1)
            self.assertEqual(tracker.call_args.kwargs['overrides']['track_buffer'], 12)
            self.assertEqual(pipeline.call_args.kwargs['config'].MARKER_INFERENCE_CONF, .2)
            self.assertEqual(metrics['analysis_settings']['PLATE_CONF'], .6)

    def test_whole_number_thresholds_are_isolated_across_recognition_workers(self):
        import numpy as np
        from run_video_pipeline import config
        from run_video_pipeline.data_capture import ReviewStore
        from run_video_pipeline.ocr import OCRResult
        from run_video_pipeline.plate_detector import PlateCrop
        from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
        from run_video_pipeline.schemas import CategoryClassification
        original = config.WHOLE_PLATE_MIN_OCR_CONF
        with tempfile.TemporaryDirectory() as directory:
            pipelines = []
            for index, threshold in enumerate((.9, .99)):
                reader = Mock()
                reader.read_result.return_value = OCRResult('17-138956', '17-138956', .95)
                pipeline = PrototypePlatePipeline(reader, Mock(), ReviewStore(Path(directory) / str(index)),
                    classifier_factory=Mock(), config=pipeline_config(parse_settings({'WHOLE_PLATE_MIN_OCR_CONF': threshold})))
                pipeline.classifier = Mock()
                pipelines.append(pipeline)
            crop = PlateCrop(np.zeros((20, 120, 3), dtype=np.uint8), (0, 0, 120, 20), .99)
            with patch('run_video_pipeline.pipeline.classify_crop', return_value=CategoryClassification(status='completed', final_category='idara')):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda pipeline: pipeline.process_crop(crop), pipelines))
            self.assertEqual([result.number.outcome for result in results], ['recognized', 'unresolved'])
            self.assertEqual(results[1].number.reason, 'insufficient_ocr_confidence')
        self.assertEqual(config.WHOLE_PLATE_MIN_OCR_CONF, original)

    def test_private_threshold_and_marker_detection_use_run_values(self):
        import numpy as np
        from run_video_pipeline.pipeline import analyze_plate_crop
        from run_video_pipeline.marker_localizer import MarkerLocalization
        from run_video_pipeline.ocr import OCRResult
        from run_video_pipeline.plate_detector import PlateCrop
        from run_video_pipeline.schemas import CategoryClassification
        crop = PlateCrop(np.zeros((30, 120, 3), dtype=np.uint8), (0, 0, 120, 30), .99)
        reader = Mock()
        reader.read_result.return_value = OCRResult('17T1389', '17T1389', .95)
        marker = Mock()
        marker.locate.return_value = MarkerLocalization(True, .99, (30, 0, 50, 25), 40)
        with patch('run_video_pipeline.pipeline.classify_crop', return_value=CategoryClassification(status='completed', final_category='tunis')):
            permissive = analyze_plate_crop(crop, reader, marker, classifier=Mock(), config=pipeline_config(parse_settings({'PRIVATE_ROUTE_MIN_OCR_CONF': .9})))
            strict = analyze_plate_crop(crop, reader, marker, classifier=Mock(), config=pipeline_config(parse_settings({'PRIVATE_ROUTE_MIN_OCR_CONF': .99, 'MARKER_INFERENCE_CONF': .2})))
        self.assertEqual(permissive.number.outcome, 'recognized')
        self.assertEqual(strict.number.reason, 'insufficient_ocr_confidence')
        self.assertEqual(marker.locate.call_args.kwargs['inference_conf'], .2)

    def test_tracker_overrides_do_not_change_yaml_defaults(self):
        from run_video_pipeline.run_video_prototype import _build_tracker, DEFAULT_TRACKER
        before = DEFAULT_TRACKER.read_bytes()
        with patch('ultralytics.trackers.byte_tracker.BYTETracker') as tracker:
            _build_tracker(DEFAULT_TRACKER, 25, overrides={'track_buffer': 10, 'match_thresh': .6})
        self.assertEqual(tracker.call_args.args[0].track_buffer, 10)
        self.assertEqual(tracker.call_args.args[0].match_thresh, .6)
        self.assertEqual(DEFAULT_TRACKER.read_bytes(), before)
