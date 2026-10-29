"""Shared integration contracts; run in fresh processes for each pipeline root."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

REPOSITORY = Path(__file__).resolve().parents[3]
ROOT = (REPOSITORY / 'vehicle/license_plate_service' if os.environ.get('PLATE_PIPELINE_FLAVOR') == 'django'
        else REPOSITORY / 'services/license_plate_service')
sys.path.insert(0, str(ROOT))
from run_video_pipeline.data_capture import ReviewStore
from run_video_pipeline.marker_localizer import MarkerLocalization
from run_video_pipeline.ocr import OCRResult
from run_video_pipeline.pipeline import analyze_plate_crop
from run_video_pipeline.plate_detector import PlateCrop
from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
from run_video_pipeline.run_video_prototype import CountingReader, TrackState, VideoPrototypeRunner


def category_result(ocr_failed=False):
    return {
        'decision': {'final_category': 'nt', 'visual_category': 'tunis', 'source': 'ocr_nt_evidence',
                     'conflict_flag': False, 'ocr_failure_flag': ocr_failed},
        'visual': {'nearest_reference_id': '5', 'nearest_similarity': 0.72, 'predicted_category_margin': 0.03},
        'ocr': {'nt_evidence': 'present', 'native_payload': {'large': 'not persisted'}},
        'timing': {'visual_feature_and_match_seconds': 0.01, 'ocr_recognition_seconds': 0.02},
    }


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events = []
        self.image = np.random.default_rng(3).integers(0, 256, (30, 120, 3), dtype=np.uint8)
        self.plate = PlateCrop(self.image, (101, 202, 221, 232), 0.95)
        self.classifier = Mock()
        self.classifier.classify_bgr.side_effect = lambda crop: self.events.append(('category', crop)) or category_result()
        self.factory = Mock(return_value=self.classifier)
        self.reader = Mock()
        self.reader.read_result.side_effect = lambda crop: self.events.append(('registration', crop)) or OCRResult('123T4567', '123T4567', 0.99)
        self.marker = Mock()
        self.marker.locate.return_value = MarkerLocalization(True, 0.99, (40, 1, 50, 29), 45)
        self.agent = Mock()
        self.store = ReviewStore(Path(self.tmp.name) / 'captures')
        self.pipeline = self.make_pipeline()

    def make_pipeline(self, pipeline_class=PrototypePlatePipeline):
        return pipeline_class(self.reader, self.marker, self.agent, self.store, classifier_factory=self.factory)

    def make_runner(self, pipeline=None, runner_class=VideoPrototypeRunner):
        reader = CountingReader(self.reader)
        pipeline = pipeline or self.make_pipeline()
        pipeline.reader = reader
        return runner_class(pipeline, Mock(), Mock(), reader)

    def test_order_pixels_bbox_and_distinct_debug_evidence(self):
        before = self.image.copy()
        self.pipeline.initialize_classifier()
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual([event[0] for event in self.events], ['category', 'registration'])
        self.assertIs(self.events[0][1], self.image)
        self.assertIs(self.events[1][1], self.image)
        np.testing.assert_array_equal(self.image, before)
        self.assertEqual(result.bbox, list(self.plate.bbox))
        self.assertEqual(result.plate_category, 'private')
        self.assertEqual(result.classification.final_category, 'nt')
        self.assertEqual(result.text_confidence, 0.99)
        self.agent.analyze.assert_not_called()
        debug = json.loads(self.store.debug_path.read_text().splitlines()[0])
        self.assertEqual(debug['classification']['nearest_similarity'], 0.72)
        self.assertEqual(debug['classification']['category_margin'], 0.03)
        self.assertEqual(debug['classification']['nt_evidence'], 'present')
        self.assertEqual(debug['classification']['visual_ms'], 10.0)
        compact = json.loads(self.store.predictions_path.read_text().splitlines()[0])
        self.assertNotIn('classification', compact)
        self.assertNotIn('classification', result.model_dump())
        self.assertNotIn('native_payload', self.store.debug_path.read_text())

    def test_classification_error_skips_registration_and_agent_and_is_persisted(self):
        self.classifier.classify_bgr.side_effect = ValueError('fixture classifier failure')
        self.pipeline.initialize_classifier()
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual((result.status, result.route, result.source), ('error', 'unknown', 'none'))
        self.assertIn('fixture classifier failure', result.classification.error)
        self.reader.read_result.assert_not_called()
        self.marker.locate.assert_not_called()
        self.agent.analyze.assert_not_called()
        debug = json.loads(self.store.debug_path.read_text().splitlines()[0])
        self.assertEqual(debug['classification']['status'], 'error')
        self.assertGreaterEqual(debug['classification']['classification_ms'], 0)

    def test_arabic_ocr_failure_does_not_fail_successful_visual_classification(self):
        result = category_result(True)
        result['decision'].update(final_category='tunis', source='visual_r2')
        result['ocr']['nt_evidence'] = 'unavailable'
        self.classifier.classify_bgr.side_effect = None
        self.classifier.classify_bgr.return_value = result
        self.pipeline.initialize_classifier()
        recognized = self.pipeline.process_crop(self.plate)
        self.assertEqual(recognized.status, 'accepted')
        self.assertTrue(recognized.classification.arabic_ocr_failed)
        self.assertEqual(recognized.classification.final_category, 'tunis')
        self.reader.read_result.assert_called_once()

    def test_worker_initializes_once_reuses_and_rechecks_better_crop(self):
        threads = []
        def build():
            threads.append(threading.get_ident())
            return self.classifier
        self.factory.side_effect = build
        runner = self.make_runner()
        self.addCleanup(runner.finish, 'test')
        state = TrackState(7, 0, 0)
        state.consider(self.plate, 0)
        runner.tracks[7] = state
        runner._submit(state, 'test', 'strong_quality')
        state.pending.result(timeout=5)
        runner._drain('test', 1)
        better = PlateCrop(np.tile(self.image, (2, 2, 1)), (101, 202, 341, 262), 0.95)
        state.consider(better, 2)
        self.assertTrue(state.recheck_is_warranted(1.5, 1.25))
        runner._drain('test', 3)
        state.pending.result(timeout=5)
        runner.finish('test')
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.classifier.classify_bgr.call_count, 2)
        self.assertEqual(len(state.attempts), 2)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertIsNone(runner.pipeline.classifier)
        self.assertFalse(any(t.name.startswith('plate-recognition') for t in threading.enumerate()))

    def test_failed_attempt_does_not_crash_runner_or_block_next_track(self):
        self.classifier.classify_bgr.side_effect = [RuntimeError('failed crop'), category_result()]
        runner = self.make_runner()
        for track_id in [1, 2]:
            state = TrackState(track_id, 0, 0)
            state.consider(self.plate, 0)
            runner.tracks[track_id] = state
            runner._submit(state, 'test', 'strong_quality')
        runner.finish('test')
        self.assertEqual(runner.tracks[1].final_result.status, 'error')
        self.assertEqual(runner.tracks[2].final_result.status, 'accepted')
        self.assertEqual(self.reader.read_result.call_count, 1)
        self.agent.analyze.assert_not_called()

    def test_initialization_failure_stops_before_frames_and_shuts_down_worker(self):
        self.factory.side_effect = RuntimeError('index incompatible')
        detector = Mock()
        with self.assertRaisesRegex(RuntimeError, 'classifier initialization failed.*index incompatible'):
            VideoPrototypeRunner(self.pipeline, detector, Mock(), CountingReader(self.reader))
        detector.predict.assert_not_called()
        self.assertFalse(any(t.name.startswith('plate-recognition') for t in threading.enumerate()))

    def test_rtsp_uses_active_video_recognition_and_injected_classifier(self):
        rtsp = importlib.import_module('run_rtsp_pipeline.run_rtsp_prototype')
        prototype = importlib.import_module('run_rtsp_pipeline.prototype_pipeline')
        self.assertIs(prototype.analyze_plate_crop, analyze_plate_crop)
        runner = self.make_runner(self.make_pipeline(prototype.PrototypePlatePipeline), rtsp.VideoPrototypeRunner)
        try:
            future = runner.executor.submit(runner.pipeline.process_crop, self.plate)
            result = future.result(timeout=5)
            self.assertEqual(result.classification.final_category, 'nt')
            self.factory.assert_called_once()
        finally:
            runner.finish('test')

    def test_direct_concurrent_calls_are_serialized(self):
        self.pipeline.initialize_classifier()
        active, peak = 0, 0
        state_lock = threading.Lock()
        def classify(crop):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return category_result()
        self.classifier.classify_bgr.side_effect = classify
        with ThreadPoolExecutor(max_workers=2) as callers:
            futures = [callers.submit(self.pipeline.process_crop, self.plate) for _ in range(2)]
            for future in futures:
                self.assertEqual(future.result(timeout=5).status, 'accepted')
        self.assertEqual(peak, 1)

    def test_reconnected_runner_has_a_new_worker_owned_classifier(self):
        pipeline = self.make_pipeline()
        runner = self.make_runner(pipeline)
        runner.finish('first-session')
        self.assertIsNone(pipeline.classifier)
        next_classifier = Mock()
        next_classifier.classify_bgr.return_value = category_result()
        self.factory.return_value = next_classifier
        runner = self.make_runner(pipeline)
        try:
            self.assertIs(pipeline.classifier, next_classifier)
            self.assertEqual(self.factory.call_count, 2)
        finally:
            runner.finish('second-session')

    def test_unreadable_video_closes_initialized_worker(self):
        runner = self.make_runner()
        with patch('run_video_pipeline.run_video_prototype.cv2.VideoCapture') as capture:
            capture.return_value.isOpened.return_value = False
            with self.assertRaises(SystemExit):
                runner.run(Path('unreadable.mp4'), Path('unused.mp4'))
        self.assertIsNone(runner.pipeline.classifier)
        capture.return_value.release.assert_called_once()
        self.assertTrue(runner._finished)

    def test_rtsp_initialization_failure_closes_camera_before_reads(self):
        rtsp = importlib.import_module('run_rtsp_pipeline.run_rtsp_prototype')
        reader = Mock(connection_generation=1, source_fps=25)
        reader.ensure_connected.return_value = True
        args = SimpleNamespace(url='rtsp://example/stream', read_timeout=5, reconnect_delay=1,
                               max_reconnects=0, camera_id='test', agent_enabled=False,
                               capture_root=Path(self.tmp.name)/'rtsp', tracker_config=rtsp.DEFAULT_TRACKER)
        with patch.object(rtsp, 'RTSPFrameReader', return_value=reader), patch.object(
            rtsp, 'get_reader', return_value=self.reader
        ), patch.object(rtsp, 'create_classifier', side_effect=RuntimeError('missing asset')
        ), patch.object(rtsp, '_build_tracker', return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError, 'classifier initialization failed.*missing asset'):
                rtsp.run_rtsp(args, threading.Event())
        reader.close.assert_called_once()
        reader.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
