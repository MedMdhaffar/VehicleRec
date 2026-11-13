"""Deterministic lifecycle races, with controlled children and no native models."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase

from common import console_runs as runs


class ConsoleLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment = patch.dict(os.environ, {'T1_CONSOLE_RUN_ROOT': self.tmp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        runs._CHILDREN.clear()
        self.addCleanup(runs._CHILDREN.clear)
        runs._LAUNCHING.clear()
        self.addCleanup(runs._LAUNCHING.clear)

    @staticmethod
    def start():
        return Client().post('/api/t1/runs/', {
            'source_kind': 'mp4', 'video': SimpleUploadedFile('controlled.mp4', b'fixture'),
            'manufacturer_enabled': 'false',
        })

    def test_poll_stop_and_competing_start_at_spawn_publication_boundary(self):
        real_popen = subprocess.Popen
        spawned = []
        boundary = threading.Barrier(2)
        publish = threading.Event()

        def launch(argv, **kwargs):
            # A real child remains alive on a pipe; no decoding or inference.
            child = real_popen([sys.executable, '-B', '-c',
                'import sys, pathlib; sys.stdin.buffer.read(); '
                'p=pathlib.Path(sys.argv[1]); '
                '(p/"stop-observed").write_text(str((p/"stop").exists()))', argv[-1]],
                stdin=subprocess.PIPE)
            spawned.append(child)
            if len(spawned) == 1:
                boundary.wait(timeout=5)
                if not publish.wait(timeout=5):
                    raise RuntimeError('test publication timeout')
            return child

        with patch.object(runs.subprocess, 'Popen', side_effect=launch):
            with ThreadPoolExecutor(max_workers=2) as requests:
                first = requests.submit(self.start)
                try:
                    boundary.wait(timeout=5)
                    directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
                    detail = Client().get(f'/api/t1/runs/{directory.name}/').json()
                    listing = Client().get('/api/t1/runs/').json()['runs']
                    stopped = Client().post(f'/api/t1/runs/{directory.name}/stop/').json()
                    stop_recorded = (directory/'stop').exists()
                    competitor = requests.submit(self.start)
                    second_response = competitor.result(timeout=3)
                finally:
                    publish.set()
                first_response = first.result(timeout=5)
        try:
            print('Boundary evidence:', dict(detail=detail['status'], listing=listing[0]['status'],
                  stop=stopped['status'], stop_recorded=stop_recorded,
                  competing_start=second_response.status_code, children=len(spawned)))
            self.assertEqual(first_response.status_code, 202)
            self.assertNotEqual(detail['status'], 'interrupted')
            self.assertNotEqual(listing[0]['status'], 'interrupted')
            self.assertEqual(stopped['status'], 'stopping')
            self.assertTrue(stop_recorded)
            self.assertEqual(second_response.status_code, 409)
            self.assertEqual(len(spawned), 1)
        finally:
            for child in spawned:
                child.stdin.close()
                child.wait(timeout=5)
        self.assertEqual((directory/'stop-observed').read_text(), 'True')

    def test_terminal_status_does_not_release_slot_until_child_exit(self):
        real_popen = subprocess.Popen
        child = real_popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.buffer.read()'],
                           stdin=subprocess.PIPE)
        try:
            with patch.object(runs.subprocess, 'Popen', return_value=child):
                response = self.start()
                self.assertEqual(response.status_code, 202)
                directory = Path(self.tmp.name)/response.json()['run_id']
                status = response.json()
                status['status'] = 'completed'
                runs.atomic_json(directory/'status.json', status)
                self.assertTrue(runs.RunStore().status(directory.name)['active_slot'])
                self.assertEqual(self.start().status_code, 409)
                child.stdin.close()
                child.wait(timeout=5)
                status = runs.RunStore().status(directory.name)
                self.assertEqual(status['status'], 'completed')
                self.assertFalse(status['active_slot'])
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
            if not child.stdin.closed:child.stdin.close()

    def test_live_owner_survives_registry_loss_then_dead_worker_is_interrupted(self):
        real_popen = subprocess.Popen
        spawned = []

        def launch(argv, **kwargs):
            # Keep the expected script/run identity in argv for restart inspection.
            child = real_popen([sys.executable, '-B', '-c', 'import sys;sys.stdin.buffer.read()',
                                argv[-2], argv[-1]], stdin=subprocess.PIPE)
            spawned.append(child)
            return child

        try:
            with patch.object(runs.subprocess, 'Popen', side_effect=launch):
                response = self.start()
                run_id = response.json()['run_id']
                runs._CHILDREN.clear()
                status = runs.RunStore().status(run_id)
                self.assertEqual(status['status'], 'starting')
                self.assertTrue(status['active_slot'])
                self.assertEqual(self.start().status_code, 409)
                spawned[0].stdin.close();spawned[0].wait(timeout=5)
                status = runs.RunStore().status(run_id)
                self.assertEqual(status['status'], 'interrupted')
                self.assertFalse(status['active_slot'])
                self.assertIsNone(status['cleanup'])
        finally:
            for child in spawned:
                if child.poll() is None:child.kill();child.wait(timeout=5)
                if not child.stdin.closed:child.stdin.close()

    def test_real_worker_honors_stop_before_ownership_publication_without_models(self):
        real_popen = subprocess.Popen
        boundary = threading.Barrier(2)
        publish = threading.Event()
        spawned = []

        def launch(argv, **kwargs):
            child = real_popen(argv, **kwargs)
            spawned.append(child)
            boundary.wait(timeout=5)
            if not publish.wait(timeout=5):raise RuntimeError('test publication timeout')
            return child

        try:
            with patch.object(runs.subprocess, 'Popen', side_effect=launch):
                with ThreadPoolExecutor(max_workers=1) as requests:
                    pending = requests.submit(Client().post, '/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                        rtsp_uri='rtsp://fixture-user:fixture-secret@127.0.0.1:9/stream')),
                        content_type='application/json')
                    try:
                        boundary.wait(timeout=5)
                        directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
                        camera = Path(json.loads((directory/'config.json').read_text())['source'])
                        self.assertEqual(runs.RunStore().stop(directory.name)['status'], 'stopping')
                    finally:publish.set()
                    self.assertEqual(pending.result(timeout=5).status_code, 202)
            self.assertEqual(spawned[0].wait(timeout=5), 0)
            status = runs.RunStore().status(directory.name)
            self.assertEqual(status['status'], 'stopped')
            self.assertFalse(status['active_slot'])
            self.assertEqual(status['frames'], 0)
            self.assertTrue(all(status['cleanup'].values()))
            self.assertFalse(camera.parent.exists())
        finally:
            for child in spawned:
                if child.poll() is None:child.kill();child.wait(timeout=5)

    def test_spawn_failure_releases_slot_only_after_private_cleanup(self):
        cleaning = threading.Barrier(2)
        finish = threading.Event()
        real_remove = runs.shutil.rmtree

        def remove(path, *args, **kwargs):
            if Path(path).name.startswith('waycon-camera-'):
                cleaning.wait(timeout=5)
                if not finish.wait(timeout=5):raise RuntimeError('test cleanup timeout')
            return real_remove(path, *args, **kwargs)

        def rtsp_start():
            return Client().post('/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                rtsp_uri='rtsp://fixture-user:fixture-secret@127.0.0.1:9/stream')),
                content_type='application/json')

        with patch.object(runs.subprocess, 'Popen', side_effect=OSError('fixture')), \
                patch.object(runs.shutil, 'rmtree', side_effect=remove):
            with ThreadPoolExecutor(max_workers=1) as requests:
                launch = requests.submit(rtsp_start)
                try:
                    cleaning.wait(timeout=5)
                    directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
                    camera = Path(json.loads((directory/'config.json').read_text())['source'])
                    self.assertTrue(camera.exists())
                    self.assertTrue(runs.RunStore().status(directory.name)['active_slot'])
                    self.assertEqual(self.start().status_code, 409)
                    self.assertTrue(runs.RunStore().stop(directory.name)['stop_requested'])
                finally:finish.set()
                self.assertEqual(launch.result(timeout=5).status_code, 400)
        status = runs.RunStore().status(directory.name)
        self.assertEqual(status['status'], 'failed')
        self.assertFalse(status['active_slot'])
        self.assertFalse(camera.parent.exists())

    def test_ownership_write_failure_reaps_gated_child_and_cleans_credentials(self):
        real_popen, real_json = subprocess.Popen, runs.atomic_json
        spawned = []
        cleaning = threading.Barrier(2)
        finish = threading.Event()

        def launch(argv, **kwargs):
            child = real_popen([sys.executable, '-B', '-c',
                'import os,pathlib,sys; '
                'f=int(os.environ["T1_CONSOLE_LAUNCH_FD"]); '
                'ok=os.read(f,1); '
                'pathlib.Path(sys.argv[1],"entered-native-work").write_text("unexpected") '
                'if ok == b"1" else None', argv[-1]], **kwargs)
            spawned.append(child)
            real_wait = child.wait

            def wait(**options):
                cleaning.wait(timeout=5)
                if not finish.wait(timeout=5):raise RuntimeError('test wait timeout')
                return real_wait(**options)

            child.wait = wait
            return child

        def save(path, value):
            if path.name == 'owner.json':raise OSError('controlled ownership write failure')
            return real_json(path, value)

        with patch.object(runs.subprocess, 'Popen', side_effect=launch), \
                patch.object(runs, 'atomic_json', side_effect=save):
            with ThreadPoolExecutor(max_workers=1) as requests:
                pending = requests.submit(Client().post, '/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                    rtsp_uri='rtsp://fixture-user:fixture-secret@127.0.0.1:9/stream')),
                    content_type='application/json')
                try:
                    cleaning.wait(timeout=5)
                    directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
                    self.assertIn(str(directory), runs._CHILDREN)
                    self.assertTrue(runs.RunStore().status(directory.name)['active_slot'])
                    self.assertEqual(self.start().status_code, 409)
                    self.assertTrue(runs.RunStore().stop(directory.name)['stop_requested'])
                finally:finish.set()
                response = pending.result(timeout=5)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
        camera = Path(json.loads((directory/'config.json').read_text())['source'])
        self.assertFalse(camera.parent.exists())
        self.assertFalse((directory/'entered-native-work').exists())
        self.assertNotIn(str(directory), runs._CHILDREN)
        status = runs.RunStore().status(directory.name)
        self.assertEqual(status['status'], 'failed')
        self.assertFalse(status['active_slot'])
        self.assertTrue(all(status['cleanup'].values()))

    def test_second_server_process_is_rejected_even_with_inherited_registry(self):
        runs.RunStore()  # Acquire process lifetime lock, before fork.
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                from uuid import uuid4
                detail = f'/api/t1/runs/{uuid4()}/'
                result = [Client().get('/api/t1/runs/').status_code,
                          self.start().status_code, Client().get(detail).status_code,
                          Client().post(detail+'stop/').status_code,
                          Client().get(detail+'preview/').status_code]
                os.write(write_fd, json.dumps(result).encode())
            finally:os._exit(0)
        os.close(write_fd)
        try:
            self.assertEqual(json.loads(os.read(read_fd, 128)), [503]*5)
        finally:
            os.close(read_fd)
            os.waitpid(pid, 0)

    def test_independent_server_process_is_rejected(self):
        runs.RunStore()
        result = subprocess.run([sys.executable, '-B', '-c',
            'from common.console_runs import RunStore,ServerProcessError\n'
            'try:RunStore()\n'
            'except ServerProcessError:raise SystemExit(69)\n'],
            env={**os.environ, 'PYTHONPATH':str(runs.ROOT)}, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 69, result.stderr.decode())

    def test_incomplete_failed_launch_cleanup_keeps_reservation(self):
        real_remove = runs.shutil.rmtree
        retained = []

        def remove(path, *args, **kwargs):
            if Path(path).name.startswith('waycon-camera-'):
                retained.append(path)
                raise OSError('controlled credential cleanup failure')
            return real_remove(path, *args, **kwargs)

        try:
            with patch.object(runs.subprocess, 'Popen', side_effect=OSError('controlled spawn failure')), \
                    patch.object(runs.shutil, 'rmtree', side_effect=remove):
                response = Client().post('/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                    rtsp_uri='rtsp://fixture-user:fixture-secret@127.0.0.1:9/stream')),
                    content_type='application/json')
            self.assertEqual(response.status_code, 400)
            directory = next(p for p in Path(self.tmp.name).iterdir() if p.is_dir())
            status = runs.RunStore().status(directory.name)
            self.assertTrue(status['active_slot'])
            self.assertFalse(status['cleanup']['private_camera_removed'])
            self.assertEqual(self.start().status_code, 409)
            runs._LAUNCHING.clear()  # Restart cannot turn failed cleanup into free admission.
            recovered = runs.RunStore().status(directory.name)
            self.assertTrue(recovered['active_slot'])
            self.assertEqual(recovered['status'], 'failed')
            self.assertFalse(recovered['cleanup']['private_camera_removed'])
            self.assertEqual(self.start().status_code, 409)
        finally:
            for path in retained:real_remove(path)

    def test_restart_with_unpublished_ownership_remains_reserved_for_inspection(self):
        store = runs.RunStore()
        from uuid import uuid4
        directory = store.root/str(uuid4())
        directory.mkdir()
        runs.atomic_json(directory/'status.json', runs.initial_status(directory.name, 'mp4', False, 4))
        runs.atomic_json(directory/'lifecycle.json', {'phase':'launching'})
        status = store.status(directory.name)
        self.assertEqual(status['error_code'], 'ownership_uncertain')
        self.assertTrue(status['active_slot'])
        self.assertIsNone(status['cleanup'])
        self.assertEqual(self.start().status_code, 409)

