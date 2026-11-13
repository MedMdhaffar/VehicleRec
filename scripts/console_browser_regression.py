"""Serve built console against the actual API with pipe-controlled workers.

No camera, inference, external Redis or database. Run from repository root.
"""
from http import HTTPStatus
import json
import mimetypes
import os
from pathlib import Path
import shutil
from socketserver import ThreadingMixIn
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'vehicle'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vehicle.settings')
import django
django.setup()
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from common import console_runs as runs


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):pass


def main():
    settings.ALLOWED_HOSTS = ['127.0.0.1']
    application = get_wsgi_application()
    dist = ROOT/'frontend/dist'
    assert (dist/'index.html').is_file(), 'Build frontend first'
    real_popen = subprocess.Popen
    children, cameras, ready_threads = {}, [], []

    def launch(argv, **kwargs):
        kwargs.update(stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        kwargs['env'].pop('CONSOLE_FIXTURE_REDIS', None)
        child = real_popen([sys.executable, '-B', str(ROOT/'vehicle/api/console_worker_fixture.py'),
                            argv[-1]], **kwargs)
        directory = Path(argv[-1])
        config = json.loads((directory/'config.json').read_text())
        if config['source_kind'] == 'rtsp':cameras.append(Path(config['source']).parent)
        children[directory.name] = child
        number = len(children)

        def ready():
            assert json.loads(child.stdout.readline())['ready']
            if number == 2:
                child.stdin.write('terminal\n');child.stdin.flush()
                assert child.stdout.readline().strip() == 'terminal'

        thread = threading.Thread(target=ready)
        ready_threads.append(thread);thread.start()
        return child

    original_stop = runs.RunStore.stop

    def stop(store, run_id):
        result = original_stop(store, run_id)
        child = children.get(run_id)
        if child is not None and child.poll() is None:
            child.stdin.write('hard-exit\n' if len(children) == 3 else 'finish\n')
            child.stdin.flush()
        return result

    def serve(environ, start_response):
        if environ['PATH_INFO'].startswith('/api/'):
            return application(environ, start_response)
        path = (dist/environ['PATH_INFO'].lstrip('/')).resolve()
        if not path.is_relative_to(dist):
            start_response('404 Not Found', []);return [b'']
        if not path.is_file():path = dist/'index.html'
        data = path.read_bytes()
        start_response(f'{HTTPStatus.OK.value} OK', [('Content-Type', mimetypes.guess_type(path)[0] or 'text/html'),
                                                    ('Content-Length', str(len(data)))])
        return [data]

    with tempfile.TemporaryDirectory(prefix='console-browser-') as root:
        with patch.dict(os.environ, {'T1_CONSOLE_RUN_ROOT':root}), \
                patch.object(runs.subprocess, 'Popen', side_effect=launch), \
                patch.object(runs.RunStore, 'stop', stop):
            server = make_server('127.0.0.1', 0, serve, server_class=Server, handler_class=QuietHandler)
            thread = threading.Thread(target=server.serve_forever);thread.start()
            process = None
            try:
                node = os.environ['CONSOLE_TEST_NODE']
                process = real_popen([node, str(ROOT/'scripts/console_browser_regression.cjs')],
                    env={**os.environ, 'CONSOLE_TEST_URL':f'http://127.0.0.1:{server.server_port}'})
                assert process.wait(timeout=90) == 0, 'Browser regression failed'
                assert len(children) == 3
                assert len(cameras) == 2 and cameras[0] != cameras[1]
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=5)
                server.shutdown();server.server_close();thread.join(timeout=5)
                for child in children.values():
                    if child.poll() is None:child.kill()
                    child.wait(timeout=5)
                    child.stdin.close();child.stdout.close()
                for thread in ready_threads:thread.join(timeout=5)
                for camera in cameras:
                    if camera.exists():shutil.rmtree(camera)
    print('Browser fixture resources removed; no production services used.')


if __name__ == '__main__':main()
