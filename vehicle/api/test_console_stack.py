"""Readiness failure is explicit and cannot spawn a run; drain keeps admission."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from common.console_runs import RunStore, ActiveRunError
from common.console_stack import StackUnavailable


class ConsoleStackTests(SimpleTestCase):
    def test_missing_db1_returns_503_before_staging_or_spawning(self):
        with patch('api.console_views.require_delivery', side_effect=StackUnavailable('DB1 unavailable.')), \
                patch('api.console_views.RunStore') as store:
            response = self.client.post('/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                rtsp_uri='rtsp://private:secret@127.0.0.1:9/camera')), content_type='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': 'DB1 unavailable.'})
        store.return_value.start.assert_not_called()

    def test_console_only_is_explicit_in_readiness(self):
        with patch.dict(os.environ, {'T1_CONSOLE_DB1': 'console-only'}), \
                patch('common.console_stack._CACHE', None):
            response = self.client.get('/api/t1/stack/')
        self.assertFalse(response.json()['full_stack_ready'])
        self.assertEqual(response.json()['mode'], 'console-only')
        self.assertIn('Console-only', response.json()['error'])

    def test_rtsp_transport_is_explicit_and_validated(self):
        with patch('api.console_views.require_delivery'), patch('api.console_views.RunStore') as store:
            store.return_value.start.return_value = {'run_id': 'test-only'}
            response = self.client.post('/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                rtsp_uri='rtsp://127.0.0.1:9/owned', rtsp_transport='udp')), content_type='application/json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(store.return_value.start.call_args.kwargs['rtsp_transport'], 'udp')
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                RunStore(name).start(source_kind='rtsp', rtsp_uri='rtsp://127.0.0.1:9/owned', rtsp_transport='auto')

    def test_draining_prevents_another_start_inside_admission_lock(self):
        with tempfile.TemporaryDirectory() as name:
            drain = Path(name)/'draining'
            drain.touch()
            with patch.dict(os.environ, {'WAYCON_LAUNCHER_DRAIN': str(drain)}), \
                    patch('common.console_runs.subprocess.Popen') as spawn:
                store = RunStore(Path(name)/'runs')
                with self.assertRaises(ActiveRunError):
                    store.start(source_kind='mp4', upload=SimpleUploadedFile('test.mp4', b'fixture'))
                spawn.assert_not_called()
                self.assertEqual(store.list(), [])
