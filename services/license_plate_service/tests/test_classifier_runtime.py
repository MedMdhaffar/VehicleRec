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
        'decision': {'final_category': 'tunis', 'visual_category': 'tunis', 'source': 'visual_r2',
                     'conflict_flag': False, 'ocr_failure_flag': ocr_failed},
        'visual': {'nearest_reference_id': '5', 'nearest_similarity': 0.72, 'predicted_category_margin': 0.03},
        'ocr': {'nt_evidence': 'absent', 'native_payload': {'large': 'not persisted'}},
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
        self.store = ReviewStore(Path(self.tmp.name) / 'captures')
        self.pipeline = self.make_pipeline()

    def make_pipeline(self, pipeline_class=PrototypePlatePipeline):
        return pipeline_class(self.reader, self.marker, self.store, classifier_factory=self.factory)

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
        self.assertEqual(result.classification.final_category, 'tunis')
        self.assertEqual(result.text_confidence, 0.99)
        debug = json.loads(self.store.debug_path.read_text().splitlines()[0])
        self.assertEqual(debug['classification']['nearest_similarity'], 0.72)
        self.assertEqual(debug['classification']['category_margin'], 0.03)
        self.assertEqual(debug['classification']['nt_evidence'], 'absent')
        self.assertEqual(debug['classification']['visual_ms'], 10.0)
        compact = json.loads(self.store.predictions_path.read_text().splitlines()[0])
        self.assertNotIn('classification', compact)
        self.assertNotIn('classification', result.model_dump())
        self.assertNotIn('native_payload', self.store.debug_path.read_text())

    def test_classification_error_skips_registration_and_is_persisted(self):
        self.classifier.classify_bgr.side_effect = ValueError('fixture classifier failure')
        self.pipeline.initialize_classifier()
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual((result.status, result.route, result.source), ('error', 'unknown', 'none'))
        self.assertIn('fixture classifier failure', result.classification.error)
        self.reader.read_result.assert_not_called()
        self.marker.locate.assert_not_called()
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
            self.assertEqual(result.classification.final_category, 'tunis')
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
            rtsp, 'CountingReader', return_value=CountingReader(self.reader)
        ), patch.object(rtsp, 'create_classifier', side_effect=RuntimeError('missing asset')
        ), patch.object(rtsp, '_build_tracker', return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError, 'classifier initialization failed.*missing asset'):
                rtsp.run_rtsp(args, threading.Event())
        reader.close.assert_called_once()
        reader.read.assert_not_called()


class LocalCompletionTests(unittest.TestCase):
    make_pipeline = RuntimeTests.make_pipeline
    make_runner = RuntimeTests.make_runner
    # Inherit all lifecycle contracts against the final local pipeline as well.
    def setUp(self):
        RuntimeTests.setUp(self)
        denial = patch('socket.socket.connect', side_effect=AssertionError('recognition network denied'))
        self.network = denial.start()
        self.addCleanup(denial.stop)

    def test_nonprivate_dispatch_uses_one_whole_crop_read_without_marker(self):
        for category, text, expected in [('nt', '00123', '00123 nt'),
                                         ('idara', '09-0012', '09 - 0012'),
                                         ('shorta', '00123', 'shorta 00123')]:
            payload = category_result()
            payload['decision']['final_category'] = category
            self.classifier.classify_bgr.side_effect = None
            self.classifier.classify_bgr.return_value = payload
            self.reader.read_result.side_effect = None
            self.reader.read_result.return_value = OCRResult(text, text, .99)
            self.reader.reset_mock()
            pipeline = self.make_pipeline()
            pipeline.initialize_classifier()
            result = pipeline.process_crop(self.plate)
            self.assertEqual(result.status, 'accepted')
            self.assertEqual(result.normalized_number, expected)
            self.reader.read_result.assert_called_once_with(self.plate.crop)
            self.marker.locate.assert_not_called()
        self.network.assert_not_called()

    def test_arabic_digits_leading_zeros_and_no_letter_guessing(self):
        from run_video_pipeline.private_plate_parser import parse_private_plate
        from run_video_pipeline.ocr import normalize_plate_text
        result = parse_private_plate('٠١ تونس ٠٠٢٣')
        self.assertEqual(result.normalized_plate, '01 تونس 0023')
        self.assertEqual(result.registration_number, '0023')
        self.assertEqual(normalize_plate_text('O٠١'), 'O01')
        self.assertEqual(parse_private_plate('O01').status, 'invalid')
        self.assertEqual(parse_private_plate('123456').status, 'ambiguous')

    def test_ambiguous_invalid_and_low_confidence_are_unresolved(self):
        self.pipeline.initialize_classifier()
        for text, score, reason in [('1T12345', .99, 'invalid_format'), ('', 0, 'insufficient_ocr_evidence'),
                                    ('123T4567', .89, 'insufficient_ocr_confidence')]:
            self.reader.read_result.side_effect = None
            self.reader.read_result.return_value = OCRResult(text, text, score)
            result = self.pipeline.process_crop(self.plate)
            self.assertEqual(result.unresolved_reason, reason)
            self.assertIsNone(result.normalized_number)

    def test_marker_region_resolution_requires_exact_agreement(self):
        self.pipeline.initialize_classifier()
        for right, accepted in [('3456', True), ('9999', False)]:
            self.reader.read_result.side_effect = [OCRResult('123456', '123456', .99),
                                                  OCRResult('12', '12', .99), OCRResult(right, right, .99)]
            result = self.pipeline.process_crop(self.plate)
            self.assertEqual(result.status == 'accepted', accepted)
            if accepted:
                self.assertEqual(result.registration_fields, {'series': '12', 'registration_number': '3456'})
            else:
                self.assertEqual(result.unresolved_reason, 'conflicting_readings')

    def test_number_ocr_failure_preserves_classification_and_errors(self):
        self.pipeline.initialize_classifier()
        self.reader.read_result.side_effect = RuntimeError('OCR broke')
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.unresolved_reason, 'ocr_failure')
        self.assertEqual(result.predicted_category, 'tunis')
        self.assertIsNone(result.normalized_number)

    def test_bounded_saturation_defers_and_final_drain_keeps_every_track(self):
        gate = threading.Event()
        original = self.classifier.classify_bgr.side_effect
        def slow(crop):
            gate.wait(5)
            return original(crop)
        self.classifier.classify_bgr.side_effect = slow
        reader = CountingReader(self.reader)
        pipeline = PrototypePlatePipeline(reader, self.marker, self.store, classifier_factory=self.factory)
        runner = VideoPrototypeRunner(pipeline, Mock(), Mock(), reader, max_pending=1)
        for track_id in range(4):
            state = TrackState(track_id, 0, 0)
            state.consider(self.plate, 0)
            runner.tracks[track_id] = state
            runner._submit(state, 'test', 'strong_quality')
        self.assertEqual(sum(x.pending is not None for x in runner.tracks.values()), 1)
        self.assertEqual(sum(x.deferred_reason is not None for x in runner.tracks.values()), 3)
        gate.set()
        runner.finish('test')
        self.assertEqual(self.classifier.classify_bgr.call_count, 4)
        self.assertTrue(all(x.final_result.status == 'accepted' for x in runner.tracks.values()))
        self.assertTrue(all(x.pending is None and x.deferred_reason is None for x in runner.tracks.values()))
        self.assertEqual(runner.deferred_events, 3)
        self.assertTrue(all(x.attempts[0].queue_wait_ms >= 0 for x in runner.tracks.values()))

    def test_identity_conflict_never_replaces_accepted_category(self):
        from run_video_pipeline.run_video_prototype import RecognitionAttempt
        self.pipeline.initialize_classifier()
        current = self.pipeline.process_crop(self.plate)
        state = TrackState(1, 0, 0)
        state.record_attempt(RecognitionAttempt(current, 1), .02)
        conflict = current.model_copy(update={'sample_id': 'conflict', 'predicted_category': 'nt',
                                               'plate_category': 'temporary', 'text_confidence': 1.0})
        state.record_attempt(RecognitionAttempt(conflict, 1), .02)
        self.assertEqual(state.final_result.sample_id, current.sample_id)
        self.assertTrue(state.identity_conflict)
        self.assertIn('conflict', state.identity_conflict_samples)

    def test_unresolved_raw_ocr_is_never_public_plate_text(self):
        from run_video_pipeline.policies import compact_result
        self.pipeline.initialize_classifier()
        self.reader.read_result.side_effect = None
        self.reader.read_result.return_value = OCRResult('uncertain123', 'uncertain123', .99)
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(compact_result(result)['plate_text'], '')
        self.assertEqual(result.plate_text_raw, 'uncertain123')
        self.assertIsNone(result.plate_text_normalized)

    def test_stale_deferred_rtsp_work_is_explicitly_recorded_not_silently_lost(self):
        rtsp = importlib.import_module('run_rtsp_pipeline.run_rtsp_prototype')
        gate = threading.Event()
        self.classifier.classify_bgr.side_effect = lambda crop: gate.wait(5) and category_result()
        reader = CountingReader(self.reader)
        pipeline = PrototypePlatePipeline(reader, self.marker, self.store, classifier_factory=self.factory)
        runner = VideoPrototypeRunner(pipeline, Mock(), Mock(), reader, max_pending=1)
        for track_id in [1, 2]:
            state = TrackState(track_id, 0, 0)
            state.consider(self.plate, 0)
            runner.tracks[track_id] = state
            runner._submit(state, 'source', 'strong_quality')
        totals = rtsp.LiveTotals()
        rtsp._retire_stale_tracks(runner, totals, session_id=1, frame_index=500)
        self.assertNotIn(2, runner.tracks)
        self.assertEqual(totals.unresolved_track_count, 1)
        stored = [json.loads(x) for x in self.store.predictions_path.read_text().splitlines()]
        self.assertEqual(stored[0]['unresolved_reason'], 'queue_capacity_retention_limit')
        gate.set()
        runner.finish('source')
        self.assertEqual(self.classifier.classify_bgr.call_count, 1)

    def test_registration_initialization_failure_releases_classifier(self):
        reader = CountingReader(factory=Mock(side_effect=RuntimeError('local OCR assets missing')))
        pipeline = PrototypePlatePipeline(reader, self.marker, self.store, classifier_factory=self.factory)
        with self.assertRaisesRegex(RuntimeError, 'local OCR assets missing'):
            VideoPrototypeRunner(pipeline, Mock(), Mock(), reader)
        self.assertIsNone(pipeline.classifier)

    def test_absent_marker_cannot_be_replaced_by_category_prediction(self):
        self.pipeline.initialize_classifier()
        self.marker.locate.return_value = MarkerLocalization(False)
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(result.status, 'unresolved')
        self.assertEqual(result.unresolved_reason, 'insufficient_marker_evidence')


    def test_conflicting_category_marker_is_not_accepted(self):
        payload = category_result()
        payload['ocr']['conflicting_categories'] = ['shorta']
        payload['decision']['conflict_flag'] = True
        self.classifier.classify_bgr.side_effect = None
        self.classifier.classify_bgr.return_value = payload
        self.pipeline.initialize_classifier()
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(result.status, 'unresolved')
        self.assertEqual(result.unresolved_reason, 'conflicting_category_evidence')

    def test_shutdown_drains_deferred_work_after_frame_failure(self):
        reader = CountingReader(self.reader)
        pipeline = PrototypePlatePipeline(reader, self.marker, self.store, classifier_factory=self.factory)
        runner = VideoPrototypeRunner(pipeline, Mock(), Mock(), reader, max_pending=1)
        for track_id in range(3):
            state = TrackState(track_id, 0, 0)
            state.consider(self.plate, 0)
            runner.tracks[track_id] = state
            runner._submit(state, 'source', 'strong_quality')
        runner.close()
        self.assertTrue(all(state.final_result is not None for state in runner.tracks.values()))
        self.assertIsNone(pipeline.classifier)

    def test_many_tracks_remain_bounded_and_all_finalize(self):
        reader = CountingReader(self.reader)
        pipeline = PrototypePlatePipeline(reader, self.marker, self.store, classifier_factory=self.factory)
        runner = VideoPrototypeRunner(pipeline, Mock(), Mock(), reader, max_pending=2)
        for track_id in range(100):
            state = TrackState(track_id, 0, 0)
            state.consider(self.plate, 0)
            runner.tracks[track_id] = state
            runner._submit(state, 'source', 'strong_quality')
            self.assertLessEqual(sum(t.pending is not None for t in runner.tracks.values()), 2)
        runner.finish('source')
        self.assertEqual(self.classifier.classify_bgr.call_count, 100)
        self.assertTrue(all(t.final_result.status == 'accepted' for t in runner.tracks.values()))

    def test_review_export_preserves_public_category_and_category_evidence(self):
        import csv
        runner = self.make_runner()
        state = TrackState(1, 0, 0)
        state.consider(self.plate, 0)
        runner.tracks[1] = state
        runner._submit(state, 'test', 'strong_quality')
        runner.finish('test')
        result = state.final_result
        self.store.review_prediction(result.sample_id, 'accepted_as_correct')
        target = Path(self.tmp.name)/'verified.csv'
        self.assertEqual(self.store.export_verified(target), 1)
        with target.open() as handle:
            row = list(csv.DictReader(handle))[0]
        self.assertEqual(row['predicted_category'], 'private')
        self.assertEqual(row['plugin_category'], 'tunis')
        self.assertEqual(row['recognition_policy'], 'tunis_private')
        self.assertEqual(row['normalized_number'], '123 تونس 4567')
        self.assertEqual(row['identity_conflict'], 'False')

    def test_legacy_cli_flags_are_rejected_and_clients_are_absent(self):
        import contextlib
        import io
        import importlib.util
        from run_video_pipeline.run_video_prototype import main
        rtsp = importlib.import_module('run_rtsp_pipeline.run_rtsp_prototype')
        for command, argv in [(main, ['video', 'unused.mp4', '--agent-enabled']),
                              (rtsp._parse_args, ['rtsp', '--url', 'rtsp://example/stream', '--agent-enabled'])]:
            with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as raised:
                    command()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn('unrecognized arguments', error.getvalue())
        self.assertIsNone(importlib.util.find_spec('run_video_pipeline.agent'))
        self.assertIsNone(importlib.util.find_spec('run_rtsp_pipeline.agent'))

    def test_offline_reader_rejects_missing_or_relative_model_dir(self):
        from run_video_pipeline.ocr import PlateReader
        for value in ['', 'relative/models', '/does-not-exist']:
            with patch.dict(os.environ, {'REGISTRATION_OCR_MODEL_DIR': value}):
                with self.assertRaisesRegex(RuntimeError, 'REGISTRATION_OCR_MODEL_DIR'):
                    PlateReader()

    def test_marker_runtime_failure_is_an_error_with_partial_ocr_preserved(self):
        self.pipeline.initialize_classifier()
        self.marker.locate.side_effect = RuntimeError('marker backend failed')
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.unresolved_reason, 'marker_localization_failure')
        self.assertEqual(result.plate_text_raw, '123T4567')
        self.assertEqual(result.number.regions[0]['bbox'], [0, 0, 120, 30])

    def test_invalid_ocr_confidence_is_recorded_as_error_not_a_frame_exception(self):
        self.pipeline.initialize_classifier()
        self.reader.read_result.side_effect = None
        self.reader.read_result.return_value = OCRResult('123T4567', '123T4567', float('nan'))
        result = self.pipeline.process_crop(self.plate)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.unresolved_reason, 'ocr_failure')
        self.assertIn('123T4567', result.number.checks['error'])

    def test_unresolved_recheck_retains_accepted_number_and_exposes_category_conflict(self):
        from run_video_pipeline.run_video_prototype import RecognitionAttempt
        self.pipeline.initialize_classifier()
        first = self.pipeline.process_crop(self.plate)
        state = TrackState(1, 0, 0)
        state.record_attempt(RecognitionAttempt(first, 1), .02)
        next_result = first.model_copy(update={'sample_id': 'different-category', 'predicted_category': 'nt',
                                               'status': 'unresolved', 'normalized_number': None, 'registration_fields': {}})
        state.record_attempt(RecognitionAttempt(next_result, 0), .02)
        self.assertEqual(state.final_result.sample_id, first.sample_id)
        self.assertTrue(state.category_conflict)
        self.assertFalse(state.identity_conflict)


if __name__ == '__main__':
    unittest.main()
