"""Separate hard-exit probe against explicitly owned, private native Redis."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.test import Client, SimpleTestCase
from common import console_runs as runs


class ConsoleResourceTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {'T1_CONSOLE_RUN_ROOT':self.tmp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(runs._CHILDREN.clear)

    def test_hard_exit_leaves_credentials_and_owned_redis_cleanup_unknown(self):
        import redis
        executable = Path(os.environ.get('T1_REDIS_SERVER',
            runs.ROOT.parent.parent/'redis-native-validation/redis-8.2.11/src/redis-server'))
        self.assertTrue(executable.is_file(), 'Explicit native Redis fixture is required')
        real_popen = subprocess.Popen
        spawned = []

        def launch(argv, **kwargs):
            kwargs.update(stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            kwargs['env'] = {**kwargs['env'], 'CONSOLE_FIXTURE_REDIS':str(executable)}
            child = real_popen([sys.executable, '-B', str(Path(__file__).with_name('console_worker_fixture.py')),
                                argv[-1]], **kwargs)
            spawned.append(child)
            return child

        redis_dir = camera_dir = client = None
        try:
            with patch.object(runs.subprocess, 'Popen', side_effect=launch):
                response = Client().post('/api/t1/runs/', json.dumps(dict(source_kind='rtsp',
                    rtsp_uri='rtsp://fixture-user:fixture-secret@127.0.0.1:9/hard-exit')),
                    content_type='application/json')
            self.assertEqual(response.status_code, 202)
            child = spawned[0]
            manifest = json.loads(child.stdout.readline())
            self.assertTrue(manifest['ready'])
            redis_dir = Path(manifest['redis_directory'])
            directory = Path(self.tmp.name)/response.json()['run_id']
            camera = Path(json.loads((directory/'config.json').read_text())['source'])
            camera_dir = camera.parent
            client = redis.Redis(unix_socket_path=str(redis_dir/'redis.sock'), socket_timeout=2)
            self.assertTrue(client.ping())
            child.stdin.write('hard-exit\n');child.stdin.flush()
            self.assertEqual(child.wait(timeout=5), 23)
            status = runs.RunStore().status(directory.name)
            self.assertEqual(status['status'], 'interrupted')
            self.assertIsNone(status['cleanup'])
            self.assertFalse(status['active_slot'])
            self.assertTrue(camera.exists())
            self.assertTrue(client.ping())
            self.assertNotIn('fixture-secret', json.dumps(status))
            print('Hard-exit evidence: credential file retained; owned Redis still responds; cleanup unknown')
        finally:
            for child in spawned:
                if child.poll() is None:
                    child.kill();child.wait(timeout=5)
                child.stdin.close();child.stdout.close()
            # Fixture-only recovery through this exact private Unix socket.
            # No PID signals, broad directory scan or wildcard deletion.
            if client is not None:
                client.shutdown(nosave=True)
                client.close()
            for path in (redis_dir, camera_dir):
                if path is not None:shutil.rmtree(path)
        self.assertFalse(redis_dir.exists())
        self.assertFalse(camera_dir.exists())
