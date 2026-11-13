"""No-install inventory shared by --check and normal console startup."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import re

from .console_stack import ROOT, model_paths
from .console_postgres import postgres_bin

PACKAGES = ('Django','djangorestframework','django-cors-headers','python-dotenv',
    'torch','torchvision','numpy','opencv-python','ultralytics','lap','redis',
    'jsonschema','psycopg','paddleocr','paddlepaddle','paddlex','Pillow','PyYAML','requests','pydantic')


def executable(variable, name):
    value = os.environ.get(variable) or shutil.which(name)
    if value and '/' in value:
        path=Path(value).expanduser()
        value=str(path if path.is_absolute() else ROOT/path)
    found = shutil.which(value) if value else None
    return found if found and os.access(found,os.X_OK) else None


def inventory(mode):
    report = dict(python=dict(executable=sys.executable,version=sys.version.split()[0]),packages={},missing=[],warnings=[])
    missing=report['missing']
    if sys.version_info[:2]!=(3,11):missing.append('Python 3.11 is required; select it with T1_CONSOLE_PYTHON.')
    for package in PACKAGES:
        try:report['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:missing.append('Missing Python package: '+package)
    node=executable('WAYCON_NODE','node')
    if not node:missing.append('Missing Node executable; install Node 22.12+ or set WAYCON_NODE.')
    else:
        try:
            version=subprocess.check_output([node,'-p','process.versions.node'],text=True,timeout=10).strip()
            v=tuple(int(p) for p in version.split('.'))
            if not (v>=(22,12,0) or (20,19,0)<=v<(21,0,0)):
                missing.append('Node '+version+' is incompatible; use Node 22.12+ or 20.19+.')
            report['node']=dict(executable=node,version=version)
            os.environ['WAYCON_NODE']=node
            result=subprocess.run([node,'--input-type=module','-e',
                "await import('vite'); await import('@vitejs/plugin-react'); await import('react'); await import('react-dom');"],
                cwd=ROOT/'frontend',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            if result.returncode:missing.append('Frontend dependencies are missing or incompatible; run npm ci in frontend/.')
        except (OSError,ValueError,subprocess.SubprocessError):missing.append('Node/frontend runtime failed to execute; check WAYCON_NODE and frontend/node_modules.')
    redis=executable('T1_REDIS_SERVER','redis-server')
    if not redis:missing.append('Missing redis-server executable; install Redis or set T1_REDIS_SERVER.')
    else:
        report['redis_executable']=redis
        os.environ['T1_REDIS_SERVER']=redis
        try:
            output=subprocess.check_output([redis,'--version'],text=True,timeout=10)
            match=re.search(r'v=([0-9]+)\.([0-9]+)\.([0-9]+)',output)
            if not match or tuple(map(int,match.groups()))<(6,2,0):
                missing.append('Redis 6.2+ is required for the delivery stream commands: '+redis)
        except (OSError,subprocess.SubprocessError):missing.append('Cannot execute Redis server: '+redis)
    for key,path in model_paths().items():
        required=[path/name for name in ('inference.json','inference.pdiparams','inference.yml')] if key=='ocr_dir' else [path]
        for item in required:
            if not item.is_file():missing.append('Missing '+key+': '+str(item))
    if mode=='fixture':
        try:
            owned=os.environ.get('WAYCON_POSTGRES_MODE','owned')=='owned'
            port=int(os.environ.get('WAYCON_PG_PORT','0' if owned else '5432'))
            if not (0 if owned else 1)<=port<=65535:raise ValueError()
        except ValueError:missing.append('WAYCON_PG_PORT must be 0 (owned free-port selection) or a valid local port 1–65535.')
        if not (ROOT/'db1_contract.zip').is_file():missing.append('Missing approved DB1 schema archive: '+str(ROOT/'db1_contract.zip'))
        if os.environ.get('WAYCON_POSTGRES_MODE','owned')=='owned':
            binaries=postgres_bin()
            if binaries is None:missing.append('Missing PostgreSQL 16 server binaries: postgres, initdb, pg_ctl; install PostgreSQL 16 or set WAYCON_PG_BIN.')
            else:
                report['postgres_bin']=str(binaries)
                for name in ('postgres','initdb','pg_ctl'):
                    try:
                        output=subprocess.check_output([str(binaries/name),'--version'],text=True,timeout=10)
                        if ' 16.' not in output:missing.append(name+' must be PostgreSQL 16: '+str(binaries/name))
                    except (OSError,subprocess.SubprocessError):missing.append('Cannot execute PostgreSQL binary: '+str(binaries/name))
            if os.getuid()==0:missing.append('Owned PostgreSQL requires a normal WSL user; do not run the launcher with sudo.')
        elif os.environ.get('WAYCON_POSTGRES_MODE')!='existing':missing.append('WAYCON_POSTGRES_MODE must be owned or existing.')
        elif not (os.environ.get('DB1_P4_ADMIN_DSN') or os.environ.get('WAYCON_ADMIN_DSN_FILE')):
            missing.append('Existing PostgreSQL mode requires a private WAYCON_ADMIN_DSN_FILE or DB1_P4_ADMIN_DSN.')
        elif not os.environ.get('DB1_P4_ADMIN_DSN'):
            from .console_stack import private_file,StackUnavailable
            path=Path(os.environ['WAYCON_ADMIN_DSN_FILE']).expanduser()
            try:private_file(path)
            except FileNotFoundError:missing.append('Missing private PostgreSQL fixture DSN file: '+str(path))
            except (OSError,StackUnavailable):missing.append('PostgreSQL fixture DSN must be an owned mode-0600 regular file: '+str(path))
    # Manufacturer is optional while off. A relocated lock preserves hashes;
    # no model download or implicit replacement of the verified runtime occurs.
    lock=Path(os.environ.get('T1_MANUFACTURER_LOCK',ROOT/'vehicle/common/manufacturer/assets.json')).expanduser()
    absent=[]
    try:
        data=json.loads(lock.read_text())
        for item in data['artifacts'].values():
            path=Path(item['path']).expanduser()
            if not path.is_absolute():path=lock.parent/path
            if not path.is_file():absent.append(str(path))
        runtime=Path(data['runtime']).expanduser()
        if not runtime.is_absolute():runtime=lock.parent/runtime
        if not (runtime/'bin/python3.11').is_file():absent.append(str(runtime/'bin/python3.11'))
    except (OSError,ValueError,KeyError,TypeError):absent.append(str(lock))
    report['manufacturer']=dict(available=not absent,missing=absent,default_enabled=False,review_only=True)
    if absent:report['warnings'].append('Manufacturer unavailable while off; enabling it requires a local T1_MANUFACTURER_LOCK and its verified artifacts.')
    report['status']='missing' if missing else 'installed'
    return report
