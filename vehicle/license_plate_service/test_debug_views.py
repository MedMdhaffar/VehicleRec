import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings


class DebugViewsTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.job_id = 'a' * 32
        self.job = Path(self.directory.name) / 'license_plate' / self.job_id
        (self.job / 'captures' / 'crops').mkdir(parents=True)
        self.sample_id = '11111111-1111-1111-1111-111111111111'
        prediction = {'sample_id': self.sample_id, 'track_id': '1', 'predicted_category': 'idara',
                      'normalized_number': '17 - 138956', 'recognition_status': 'recognized'}
        (self.job / 'captures' / 'predictions.jsonl').write_text(json.dumps(prediction) + '\n')
        debug = {'record_type': 'prediction_debug', 'sample_id': self.sample_id,
                 'classification': {'final_category': 'idara'}}
        event = {'record_type': 'track_decision', 'sample_id': self.sample_id}
        (self.job / 'captures' / 'prediction_debug.jsonl').write_text(
            json.dumps(debug) + '\n' + json.dumps(event) + '\n' + '{"incomplete":')
        (self.job / 'captures' / 'crops' / f'{self.sample_id}.jpg').write_bytes(b'crop')

    def test_legacy_job_loads_evidence_and_ignores_events_and_partial_lines(self):
        response = self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['has_metrics'])
        track = data['tracks']['1']
        self.assertEqual(track['attempts'][0]['debug']['classification']['final_category'], 'idara')
        crop = self.client.get(track['attempts'][0]['crop_url'])
        self.assertEqual(crop.status_code, 200)
        self.assertEqual(b''.join(crop.streaming_content), b'crop')

    def test_summary_preserves_selected_result_and_adds_all_attempts(self):
        summary = {'input_name': 'idara.mp4', 'frames': 100, 'tracks': {'1': {
            'sample_id': 'selected', 'recognition_status': 'unresolved', 'attempt_log': []}}}
        (self.job / 'summary.json').write_text(json.dumps(summary))
        response = self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/')
        self.assertTrue(response.json()['has_metrics'])
        self.assertEqual(response.json()['tracks']['1']['sample_id'], 'selected')
        self.assertEqual(len(response.json()['tracks']['1']['attempts']), 1)
        jobs = self.client.get('/api/license-plate/jobs/').json()
        self.assertEqual(jobs[0]['name'], 'idara.mp4')

    def test_legacy_track_uses_latest_attempt_for_its_display(self):
        path = self.job / 'captures' / 'predictions.jsonl'
        with path.open('a') as handle:
            handle.write(json.dumps({'track_id': '1', 'sample_id': 'second', 'normalized_number': '17 - 148885'}) + '\n')
        track = self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/').json()['tracks']['1']
        self.assertEqual(track['normalized_number'], '17 - 148885')
        self.assertEqual(len(track['attempts']), 2)

    def test_completed_run_with_no_detections_is_still_listed(self):
        (self.job / 'captures' / 'predictions.jsonl').unlink()
        (self.job / 'summary.json').write_text(json.dumps({'tracks': {}, 'frames': 5}))
        jobs = self.client.get('/api/license-plate/jobs/').json()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['frames'], 5)

    def test_invalid_job_and_unapproved_artifact_are_rejected(self):
        self.assertEqual(self.client.get('/api/license-plate/jobs/not-a-job/debug/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/crops/summary.json/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/crops/{"b" * 36}.jpg/').status_code, 404)

    def test_crop_symlink_outside_job_is_rejected(self):
        outside = Path(self.directory.name) / 'secret.jpg'
        outside.write_bytes(b'secret')
        crop = self.job / 'captures' / 'crops' / f'{self.sample_id}.jpg'
        crop.unlink()
        crop.symlink_to(outside)
        self.assertEqual(self.client.get(f'/api/license-plate/jobs/{self.job_id}/debug/crops/{self.sample_id}.jpg/').status_code, 404)

    @patch('license_plate_service.views.run_video_analysis')
    def test_upload_persists_metrics_for_reopening_run(self, analyze):
        analyze.return_value = {'tracks': {}, 'frames': 4}
        upload = SimpleUploadedFile('test.mp4', b'video', content_type='video/mp4')
        response = self.client.post('/api/license-plate/analyze/', {'video': upload})
        self.assertEqual(response.status_code, 200)
        summaries = list((Path(self.directory.name) / 'license_plate').glob('*/summary.json'))
        self.assertEqual(len(summaries), 1)
        self.assertEqual(json.loads(summaries[0].read_text())['input_name'], 'test.mp4')
