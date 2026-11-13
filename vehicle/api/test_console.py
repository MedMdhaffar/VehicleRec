"""API and private Start tests. No video, GPU, database delivery or camera."""
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
from uuid import uuid4
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from common.console_runs import RunStore, atomic_json, _CHILDREN


class ConsoleAPITests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.environment = patch.dict(os.environ,{'T1_CONSOLE_RUN_ROOT':self.tmp.name})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.spawn = patch('common.console_runs.subprocess.Popen')
        self.process = self.spawn.start();self.addCleanup(self.spawn.stop)
        self.process.return_value.poll.return_value = None
        self.process.return_value.pid = os.getpid()
        _CHILDREN.clear();self.addCleanup(_CHILDREN.clear)
        self.cameras = [];self.addCleanup(self.clean_cameras)

    def clean_cameras(self):
        for p in self.cameras:shutil.rmtree(p,ignore_errors=True)

    def rtsp(self, uri):
        return self.client.post('/api/t1/runs/',json.dumps(dict(source_kind='rtsp',rtsp_uri=uri,
            manufacturer_enabled=True,sample_every=5,max_seconds=30)),content_type='application/json')

    def test_sampling_validation_has_no_frame_cap_or_default_timeout(self):
        for invalid in (True, False, 0, -1, 1.5, '1.5', '1.0', '', 'bad'):
            response=self.client.post('/api/t1/runs/',json.dumps(dict(source_kind='rtsp',
                rtsp_uri='rtsp://secret@127.0.0.1:9/test',sample_every=invalid)),content_type='application/json')
            self.assertEqual(response.status_code,400)
        self.process.assert_not_called()
        response=self.client.post('/api/t1/runs/',dict(source_kind='mp4',sample_every='999999',
            video=SimpleUploadedFile('fixture.mp4',b'test-only')))
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['sample_every'],999999)
        self.assertIsNone(response.json()['max_seconds'])
        self.assertNotIn('max_frames',response.json())
        config=json.loads((Path(self.tmp.name)/response.json()['run_id']/'config.json').read_text())
        self.assertEqual(config['sample_every'],999999)
        self.assertNotIn('max_frames',config)

    def test_input_specific_defaults_and_historical_records(self):
        for kind,expected in [('mp4',1),('rtsp',5)]:
            self.process.return_value.poll.return_value=None
            if kind=='mp4':
                response=self.client.post('/api/t1/runs/',dict(source_kind=kind,video=SimpleUploadedFile('fixture.mp4',b'fixture')))
            else:
                response=self.client.post('/api/t1/runs/',json.dumps(dict(source_kind=kind,rtsp_uri='rtsp://127.0.0.1:9/private')),content_type='application/json')
                config=json.loads((Path(self.tmp.name)/response.json()['run_id']/'config.json').read_text())
                self.cameras.append(Path(config['source']).parent)
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.json()['sample_every'],expected)
            directory=Path(self.tmp.name)/response.json()['run_id']
            status=response.json();status.pop('sample_every');status.update(max_frames=4,status='completed')
            atomic_json(directory/'status.json',status)
            self.process.return_value.poll.return_value=0
            historical=self.client.get(f'/api/t1/runs/{directory.name}/').json()
            self.assertEqual(historical['max_frames'],4)
            self.assertFalse(historical['active_slot'])

    def test_each_start_gets_a_new_private_camera_without_uri_in_public_state_or_argv(self):
        for index in (1,2):
            self.process.return_value.poll.return_value = None
            uri = f'rtsp://private-user:private-secret@127.0.0.1:9/camera-{index}'
            response = self.rtsp(uri)
            self.assertEqual(response.status_code,202)
            run_id = response.json()['run_id'];directory = Path(self.tmp.name)/run_id
            config = json.loads((directory/'config.json').read_text())
            camera = Path(config['source']);self.cameras.append(camera.parent)
            self.assertEqual(camera.stat().st_mode & 0o777,0o600)
            self.assertEqual(camera.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(json.loads(camera.read_text())['endpoint'],uri)
            self.assertNotIn(uri,(directory/'config.json').read_text())
            self.assertNotIn('private-secret',repr(self.process.call_args))
            for path in (f'/api/t1/runs/{run_id}/','/api/t1/runs/'):
                self.assertNotIn(b'private-secret',self.client.get(path).content)
                self.assertNotIn(uri.encode(),self.client.get(path).content)
            stopped = self.client.post(f'/api/t1/runs/{run_id}/stop/')
            self.assertEqual(stopped.json()['status'],'stopping')
            self.assertTrue((directory/'stop').exists())
            status = response.json();status['status']='stopped'
            atomic_json(directory/'status.json',status)
            self.process.return_value.poll.return_value = 0
            self.assertFalse(RunStore().status(run_id)['active_slot'])
        self.assertNotEqual(self.cameras[0],self.cameras[1])

    def test_real_multipart_contract_and_single_active_run(self):
        response = self.client.post('/api/t1/runs/',dict(source_kind='mp4',
            video=SimpleUploadedFile('client-file.mp4',b'test-only',content_type='video/mp4'),
            manufacturer_enabled='false',sample_every='1',max_seconds='30'))
        self.assertEqual(response.status_code,202)
        value = response.json();self.assertFalse(value['manufacturer_enabled'])
        self.assertEqual(value['delivery']['postgres_commit'],'not_configured')
        self.assertEqual(value['delivery']['redis_ack'],'not_attempted')
        self.assertEqual(self.rtsp('rtsp://127.0.0.1:9/stream').status_code,409)
        run_id=value['run_id']
        self.assertEqual(self.client.get(f'/api/t1/runs/{run_id}/preview/').status_code,404)
        preview=Path(self.tmp.name)/run_id/'preview.jpg';preview.write_bytes(b'backend-preview-test')
        response=self.client.get(f'/api/t1/runs/{run_id}/preview/')
        self.assertEqual(response['Cache-Control'],'no-store')
        self.assertEqual(b''.join(response.streaming_content),b'backend-preview-test')

    def test_validation_errors_and_unknown_runs_do_not_echo_sensitive_input(self):
        response=self.rtsp('invalid://private-secret')
        self.assertEqual(response.status_code,400)
        self.assertNotIn(b'private-secret',response.content)

    def test_worker_loss_is_visible_and_never_restarts_inference(self):
        response=self.client.post('/api/t1/runs/',dict(source_kind='mp4',
            video=SimpleUploadedFile('fixture.mp4',b'test-only'),manufacturer_enabled='false'))
        self.assertEqual(response.status_code,202)
        run_id=response.json()['run_id']
        _CHILDREN.clear()  # Simulate a new web process after the worker has died.
        status=self.client.get(f'/api/t1/runs/{run_id}/').json()
        self.assertEqual(status['status'],'interrupted')
        self.assertEqual(self.process.call_count,1)
        self.assertEqual(self.client.post(f'/api/t1/runs/{run_id}/stop/').json()['status'],'interrupted')
        self.assertEqual(self.client.get(f'/api/t1/runs/{uuid4()}/').status_code,404)
        response=self.client.post('/api/t1/runs/',json.dumps(dict(source_kind='rtsp',
            rtsp_uri='rtsp://private-secret@127.0.0.1:9/stream',max_frames=999)),content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertNotIn(b'private-secret',response.content)
