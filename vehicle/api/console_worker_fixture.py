"""Controlled regression child: no decoder, GPU, camera or PostgreSQL."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def run():
    with os.fdopen(int(os.environ.pop('T1_CONSOLE_LAUNCH_FD')), 'rb') as gate:
        if gate.read(1) != b'1':return 1
    directory = Path(sys.argv[1])
    from vehicle.common.console_runs import atomic_json, timestamp
    from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
    config = json.loads((directory/'config.json').read_text())
    owned = None
    if os.environ.get('CONSOLE_FIXTURE_REDIS'):
        owned = OwnedRedis(os.environ['CONSOLE_FIXTURE_REDIS']).__enter__()
    print(json.dumps({'ready':True, 'redis_directory':str(owned.path) if owned else None}), flush=True)
    status = json.loads((directory/'status.json').read_text())
    for command in sys.stdin:
        command = command.strip()
        if command == 'hard-exit':os._exit(23)
        if command == 'terminal':
            status.update(status='completed', phase='Controlled work completed', ended_at=timestamp())
            atomic_json(directory/'status.json', status)
            print('terminal', flush=True)
        if command == 'finish':
            if owned:owned.__exit__(None, None, None)
            if config['source_kind'] == 'rtsp':
                import shutil
                shutil.rmtree(Path(config['source']).parent)
            status.update(status='stopped' if (directory/'stop').exists() else 'completed',
                          phase='Controlled worker finished', ended_at=timestamp(), cleanup=dict(
                              decoder_closed=True, models_closed=True, redis_removed=True,
                              private_camera_removed=True, native_children_exited=True))
            atomic_json(directory/'status.json', status)
            return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(run())
