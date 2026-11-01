"""Application-format contracts; mocked OCR is not an accuracy evaluation."""
import csv
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_classifier_runtime as runtime

from run_video_pipeline import config
from run_video_pipeline.ocr import OCRResult
from run_video_pipeline.schemas import CategoryClassification
from run_video_pipeline.whole_plate_numbers import recognize_whole_number
from run_video_pipeline.policies import compact_result
from run_video_pipeline.data_capture import HumanReview


class ExtractionTests(unittest.TestCase):
    def read(self, category, text, score=.99, markers=()):
        return recognize_whole_number(category, OCRResult(text, text, score), CategoryClassification(
            status='completed', final_category=category, observed_markers=list(markers)))

    def test_formats_unicode_zeros_and_optional_labels(self):
        cases = [
            ('idara', '٠٩ — ٠٠١٢', '09 - 0012', {'first_group': '09', 'second_group': '0012'}),
            ('idara', '1234-5', '1234 - 5', {'first_group': '1234', 'second_group': '5'}),
            ('nt', '٠٠١٢ ن ت', '0012 nt', {'registration_number': '0012'}),
            ('nt', 'نت 0012', '0012 nt', {'registration_number': '0012'}),
            ('nt', '0012 NT', '0012 nt', {'registration_number': '0012'}),
            ('nt', '0012', '0012 nt', {'registration_number': '0012'}),
            ('shorta', 'شرطة ٠٠١٢', 'shorta 0012', {'registration_number': '0012'}),
            ('shorta', '0012 SHORTA', 'shorta 0012', {'registration_number': '0012'}),
            ('shorta', '0012', 'shorta 0012', {'registration_number': '0012'}),
        ]
        for category, text, display, fields in cases:
            with self.subTest(text=text):
                result = self.read(category, text)
                self.assertEqual(result.outcome, 'recognized')
                self.assertEqual(result.normalized_number, display)
                self.assertEqual(result.fields, fields)
        for dash in '-‐‑‒–—−﹘﹣－':
            self.assertEqual(self.read('idara', f' 01 \t{dash}  002 ').normalized_number, '01 - 002')

    def test_rejections_do_not_expose_accepted_fields_or_number(self):
        cases = [('idara', '17138957', 'missing_idara_separator'),
                 ('idara', '17 138957', 'missing_idara_separator'),
                 ('idara', '1-2-3', 'ambiguous_digit_groups'),
                 ('nt', '12 34', 'ambiguous_digit_groups'),
                 ('shorta', '12-34', 'ambiguous_digit_groups'),
                 ('nt', '12NT34', 'ambiguous_digit_groups'),
                 ('nt', '12O34', 'unexpected_ocr_text'),
                 ('shorta', '???123', 'unexpected_ocr_text'),
                 ('nt', 'ABC 123', 'unexpected_ocr_text'),
                 ('shorta', 'شرطة', 'missing_registration_digits'),
                 ('nt', '', 'empty_ocr'),
                 ('nt', '123 شرطة', 'contradictory_category_marker'),
                 ('shorta', '123 NT', 'contradictory_category_marker'),
                 ('idara', '01-002 تونس', 'contradictory_category_marker')]
        for category, text, reason in cases:
            with self.subTest(category=category, text=text):
                result = self.read(category, text)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.outcome, 'unresolved')
                self.assertIsNone(result.normalized_number)
                self.assertEqual(result.fields, {})
        self.assertEqual(self.read('nt', '123', markers=['shorta']).reason, 'contradictory_category_marker')

    def test_threshold_boundary_and_unrounded_score(self):
        with patch.object(config, 'WHOLE_PLATE_MIN_OCR_CONF', .9):
            result = self.read('nt', '0012', .899999)
            self.assertEqual(result.reason, 'insufficient_ocr_confidence')
            self.assertEqual(result.checks['extracted_fields'], {'registration_number': '0012'})
            self.assertEqual(result.checks['ocr_confidence'], .899999)
            self.assertEqual(self.read('nt', '0012', .9).outcome, 'recognized')
        with patch.object(config, 'WHOLE_PLATE_MIN_OCR_CONF', 1.1):
            with self.assertRaises(ValueError):
                self.read('nt', '123')

    def test_reader_retains_native_score_without_changing_tunis_legacy_score(self):
        from run_video_pipeline.ocr import PlateReader
        reader = object.__new__(PlateReader)
        result = reader._parse([{'rec_text': '0012', 'rec_score': .899999}])
        self.assertEqual(result.confidence, .9)
        self.assertEqual(result.native_confidence, .899999)


class IntegrationTests(unittest.TestCase):
    setUp = runtime.RuntimeTests.setUp
    make_pipeline = runtime.RuntimeTests.make_pipeline
    make_runner = runtime.RuntimeTests.make_runner

    def configure(self, category, text, score=.987654321):
        payload = runtime.category_result()
        payload['decision']['final_category'] = category
        self.classifier.classify_bgr.side_effect = None
        self.classifier.classify_bgr.return_value = payload
        self.reader.read_result.side_effect = None
        self.reader.read_result.return_value = OCRResult(text, text, score)

    def test_persistence_export_compact_and_rtsp_share_formats(self):
        for category, text, display in [('nt', '0012', '0012 nt'), ('idara', '01-002', '01 - 002'),
                                         ('shorta', '0012', 'shorta 0012')]:
            self.configure(category, text)
            for module in ('run_video_pipeline.prototype_pipeline', 'run_rtsp_pipeline.prototype_pipeline'):
                pipeline = self.make_pipeline(importlib.import_module(module).PrototypePlatePipeline)
                pipeline.initialize_classifier()
                result = pipeline.process_crop(self.plate)
                self.assertEqual(compact_result(result)['plate_text'], display)
                self.assertEqual(result.text_confidence, .987654321)
                self.assertEqual(result.category_confidence, .987654321)
                self.assertEqual(result.timing.marker_ms, 0)
                debug = json.loads(self.store.debug_path.read_text().splitlines()[-1])
                self.assertEqual(debug['plate_text_raw'], text)
                self.assertEqual(debug['evidence']['number_policy']['checks']['extracted_fields'], result.registration_fields)
                self.store.add_review(HumanReview(sample_id=result.sample_id, verified_text=display,
                                                  verified_category=result.plate_category, human_verified=True))
                pipeline.release_classifier()
        output = Path(self.tmp.name) / 'export.csv'
        self.store.export_verified(output)
        rows = list(csv.DictReader(output.open()))
        self.assertEqual(len(rows), 6)
        self.assertEqual({row['normalized_number'] for row in rows}, {'0012 nt', '01 - 002', 'shorta 0012'})
        self.marker.locate.assert_not_called()

    def test_errors_empty_and_low_confidence_keep_policy_and_hide_display(self):
        for category in ('nt', 'idara', 'shorta'):
            self.configure(category, '01-002' if category == 'idara' else '0012')
            pipeline = self.make_pipeline()
            pipeline.initialize_classifier()
            self.reader.read_result.side_effect = RuntimeError('native OCR failed')
            result = pipeline.process_crop(self.plate)
            self.assertEqual(result.recognition_policy, f'{category}_whole_plate')
            self.assertEqual(result.recognition_status, 'error')
            self.assertEqual(result.unresolved_reason, 'ocr_failure')
            self.assertEqual(compact_result(result)['plate_text'], '')
            self.reader.read_result.side_effect = None
            self.reader.read_result.return_value = OCRResult('', '', .99)
            self.assertEqual(pipeline.process_crop(self.plate).unresolved_reason, 'empty_ocr')
            self.reader.read_result.return_value = OCRResult('01-002' if category == 'idara' else '0012', '', .8)
            result = pipeline.process_crop(self.plate)
            self.assertEqual(result.unresolved_reason, 'insufficient_ocr_confidence')
            self.assertEqual(compact_result(result)['plate_text'], '')

    def test_identity_includes_category_and_string_fields(self):
        from run_video_pipeline.run_video_prototype import RecognitionAttempt, TrackState
        self.configure('nt', '0012')
        pipeline = self.make_pipeline()
        pipeline.initialize_classifier()
        first = pipeline.process_crop(self.plate)
        state = TrackState(1, 0, 0)
        def attempt(result):
            return RecognitionAttempt(result=result, crop_frame=0, crop_score=1, crop_area=1,
                                      crop_sharpness=1, low_level_ocr_calls=1,
                                      trigger_reason='test')
        state.record_attempt(attempt(first), .02)
        other = first.model_copy(update={'sample_id': 'other', 'predicted_category': 'shorta',
                                         'registration_fields': {'registration_number': '0012'}})
        state.record_attempt(attempt(other), .02)
        self.assertTrue(state.identity_conflict)
        self.assertEqual(state.final_result.sample_id, first.sample_id)
        state = TrackState(2, 0, 0)
        state.record_attempt(attempt(first), .02)
        changed_digits = first.model_copy(update={'sample_id': 'digits',
                                                  'registration_fields': {'registration_number': '012'}})
        state.record_attempt(attempt(changed_digits), .02)
        self.assertTrue(state.identity_conflict)

    def test_native_score_controls_new_policies_but_not_tunis(self):
        self.configure('nt', '0012')
        pipeline = self.make_pipeline()
        pipeline.initialize_classifier()
        self.reader.read_result.return_value = OCRResult('0012', '0012', .9, .899999)
        result = pipeline.process_crop(self.plate)
        self.assertEqual(result.text_confidence, .899999)
        self.assertEqual(result.unresolved_reason, 'insufficient_ocr_confidence')
        self.configure('tunis', '123T4567')
        self.reader.read_result.return_value = OCRResult('123T4567', '123T4567', .9, .899999)
        result = pipeline.process_crop(self.plate)
        self.assertEqual(result.text_confidence, .9)
        self.assertEqual(result.normalized_number, '123 تونس 4567')

    def test_annotation_and_direct_rtsp_dispatch_use_same_display(self):
        from run_video_pipeline.run_video_prototype import RecognitionAttempt, TrackState, annotate_frame
        from run_video_pipeline.schemas import CategoryClassification
        rtsp = importlib.import_module('run_rtsp_pipeline.pipeline')
        for category, text, expected in [('idara', '09-0012', '09 - 0012'),
                                          ('nt', '0012', '0012 nt'), ('shorta', '0012', 'shorta 0012')]:
            self.configure(category, text)
            pipeline = self.make_pipeline()
            pipeline.initialize_classifier()
            result = pipeline.process_crop(self.plate)
            state = TrackState(1, 0, 0)
            state.record_attempt(RecognitionAttempt(result=result, low_level_ocr_calls=1), .02)
            with patch('cv2.putText') as draw:
                annotate_frame(self.image.copy(), [(1, self.plate.bbox)], {1: state})
            self.assertIn(expected, draw.call_args.args[1])
            with patch.object(rtsp, 'classify_crop', return_value=CategoryClassification(
                status='completed', final_category=category
            )):
                direct = rtsp.analyze_plate_crop(self.plate, self.reader, self.marker, classifier=self.classifier)
            self.assertEqual(direct.number.normalized_number, expected)
        self.marker.locate.assert_not_called()
