"""Explicit local demo configuration and read-only readiness checks.

Only the existing P4 fixture provisions databases. This module never starts or
reconfigures PostgreSQL or a shared Redis server.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import time

ROOT = Path(__file__).resolve().parents[2]
_CACHE = None


class StackUnavailable(RuntimeError):
    pass


def private_file(path):
    path = Path(path).expanduser()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise StackUnavailable('Private configuration must be an owned mode-0600 regular file.')
    return path


def configure(*, load_dsn=True):
    """Load a private configuration, with explicit environment taking precedence."""
    path = Path(os.environ.get('WAYCON_CONFIG', Path.home()/'.config/waycon/console.json'))
    if path.exists():
        settings = json.loads(private_file(path).read_text())
        names = {'admin_dsn_file': 'WAYCON_ADMIN_DSN_FILE', 'python': 'T1_CONSOLE_PYTHON',
                 'node': 'WAYCON_NODE', 'redis_server': 'T1_REDIS_SERVER',
                 'ocr_dir': 'T1_OCR_DIR', 'manufacturer_lock': 'T1_MANUFACTURER_LOCK',
                 'device': 'T1_CONSOLE_DEVICE', 'cuda_visible_devices': 'CUDA_VISIBLE_DEVICES',
                 'expected_gpu': 'WAYCON_EXPECTED_GPU', 'postgres_mode': 'WAYCON_POSTGRES_MODE',
                 'pg_bin': 'WAYCON_PG_BIN', 'pg_port': 'WAYCON_PG_PORT',
                 'api_port': 'WAYCON_API_PORT', 'web_port': 'WAYCON_WEB_PORT',
                 'vehicle_model': 'T1_VEHICLE_MODEL', 'vehicle_tracker': 'T1_VEHICLE_TRACKER',
                 'plate_model': 'T1_PLATE_MODEL', 'plate_tracker': 'T1_PLATE_TRACKER',
                 'marker_model': 'T1_MARKER_MODEL'}
        for key, variable in names.items():
            if key in settings:
                os.environ.setdefault(variable, str(settings[key]))
    if (load_dsn and os.environ.get('WAYCON_POSTGRES_MODE', 'owned') == 'existing' and os.environ.get('WAYCON_ADMIN_DSN_FILE') and
            ('DB1_P4_ADMIN_DSN' not in os.environ or os.environ.get('WAYCON_DSN_FROM_FILE') == '1')):
        os.environ['DB1_P4_ADMIN_DSN'] = private_file(os.environ['WAYCON_ADMIN_DSN_FILE']).read_text().strip()
        os.environ['WAYCON_DSN_FROM_FILE'] = '1'


def database_probe():
    """Verify only the authorized listener and provisioning rights, no DDL."""
    try:
        configure()
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        # Django's project package is also named vehicle. Keep this read-only
        # probe free of fixture imports, which use the repository namespace.
        # Actual provisioning still passes the canonical P4 fixture guard.
        existing = os.environ.get('WAYCON_POSTGRES_MODE', 'owned') == 'existing'
        expected_flag = 'DB1_P4_EXISTING_SERVER' if existing else 'DB1_P4_ISOLATED'
        other_flag = 'DB1_P4_ISOLATED' if existing else 'DB1_P4_EXISTING_SERVER'
        if os.environ.get(expected_flag) != '1' or os.environ.get(other_flag) == '1':
            raise StackUnavailable('Explicit local disposable fixture mode is required.')
        params = conninfo_to_dict(os.environ['DB1_P4_ADMIN_DSN'])
        if params.get('dbname') not in (('postgres', 'intern') if existing else ('postgres',)):
            raise StackUnavailable('DB1 fixture access must use an authorized maintenance database.')
        port = int(os.environ.get('WAYCON_PG_PORT', '5432'))
        if not 1 <= port <= 65535 or params.get('host') != '127.0.0.1' or params.get('port') != str(port):
            raise StackUnavailable('DB1 must use the configured local loopback test listener; remote databases are refused.')
        params.update(connect_timeout='5', options='-c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0')
        with psycopg.connect(**params, autocommit=True) as connection:
            if not 160000 <= connection.info.server_version < 170000:
                raise StackUnavailable('The DB1 fixture requires PostgreSQL 16.')
            with connection.cursor() as cursor:
                cursor.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
                if cursor.fetchone() != (True,):
                    raise StackUnavailable('DB1 disposable fixture provisioning access is missing.')
            return dict(status='ready', host='127.0.0.1', port=port, launcher_owned=not existing,
                        version=connection.info.server_version, target='disposable P4 fixtures')
    except StackUnavailable:
        raise
    except Exception:
        raise StackUnavailable('DB1 unavailable. Check the local PostgreSQL 16 test listener and private fixture access.') from None


def model_paths():
    vehicle = ROOT/'vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service'
    plate = ROOT/'vehicle/license_plate_service/run_video_pipeline'
    defaults = dict(vehicle_model=vehicle/'models/vehicle_yolo26.pt', vehicle_tracker=vehicle/'trackers/bytetrack_vehicles.yaml',
        plate_model=plate/'models/best.pt', plate_tracker=plate/'trackers/bytetrack_plates.yaml',
        marker_model=plate/'models/tounes_word_detector.pt',
        ocr_dir=ROOT/'models/PP-OCRv6_medium_rec')
    result={}
    for key,value in defaults.items():
        path=Path(os.environ.get('T1_'+key.upper(),value)).expanduser()
        result[key]=(path if path.is_absolute() else ROOT/path).resolve()
    return result


def console_device():
    import re
    value = os.environ.get('T1_CONSOLE_DEVICE', 'cuda:0')
    if not re.fullmatch(r'cuda:[0-9]+', value):
        raise StackUnavailable('T1_CONSOLE_DEVICE must select a local CUDA device, for example cuda:0. CPU fallback is disabled.')
    return value


def identity():
    run_root = Path(os.environ.get('T1_CONSOLE_RUN_ROOT', ROOT/'vehicle/media/t1-console')).resolve()
    return sha256((str(ROOT)+'\n'+str(run_root)).encode()).hexdigest()


def readiness(*, fresh=False):
    global _CACHE
    mode = os.environ.get('T1_CONSOLE_DB1', 'console-only')
    if not fresh and _CACHE and time.monotonic() - _CACHE[0] < 5:
        return _CACHE[1]
    result = dict(service='waycon-console', stack_id=identity(), mode=mode,
                  full_stack_ready=False, error=None)
    if mode == 'fixture':
        try:
            result['database'] = database_probe()
            result['full_stack_ready'] = True
        except StackUnavailable as error:
            result['error'] = str(error)
    else:
        result['error'] = 'Console-only mode: PostgreSQL delivery is not configured.'
    _CACHE = time.monotonic(), result
    return result


def require_delivery():
    if os.environ.get('T1_CONSOLE_DB1') == 'fixture':
        database_probe()
