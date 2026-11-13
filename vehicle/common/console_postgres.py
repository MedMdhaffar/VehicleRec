"""Native PostgreSQL 16 owned by one launcher, never a system cluster."""
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time


def postgres_bin():
    configured = os.environ.get('WAYCON_PG_BIN')
    candidates = [Path(configured).expanduser()] if configured else [
        Path(shutil.which('postgres') or '/nonexistent').parent,
        Path('/usr/lib/postgresql/16/bin'), Path('/usr/local/pgsql/bin')]
    return next((p for p in candidates if all(os.access(p/name,os.X_OK) for name in ('postgres','initdb','pg_ctl'))),None)


class OwnedPostgres:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.process = self.temp = self.log = None
        self.cleaned = False
        self.retained = False
        self.previous = {k:os.environ.get(k) for k in ('DB1_P4_ADMIN_DSN','DB1_P4_ISOLATED',
            'DB1_P4_EXISTING_SERVER','WAYCON_PG_PORT','WAYCON_DSN_FROM_FILE')}

    def __enter__(self):
        from psycopg.conninfo import make_conninfo
        import psycopg
        binaries = postgres_bin()
        if binaries is None:raise RuntimeError('Missing PostgreSQL 16 server binaries: postgres, initdb, pg_ctl; set WAYCON_PG_BIN.')
        try:
            self.temp = Path(tempfile.mkdtemp(prefix='waycon-postgres-'))
            private = self.temp
            password = secrets.token_urlsafe(32)
            pwfile = private/'password'
            pwfile.write_text(password+'\n');pwfile.chmod(0o600)
            self.log = (self.directory/'postgres.log').open('ab')
            result = subprocess.run([str(binaries/'initdb'),'-D',str(private/'data'),'-U','waycon_launcher',
                '--pwfile',str(pwfile),'--auth-local=reject','--auth-host=scram-sha-256','--no-locale','--encoding=UTF8'],
                stdout=self.log,stderr=self.log,timeout=60)
            pwfile.unlink()
            if result.returncode:raise RuntimeError('Owned PostgreSQL initdb failed; inspect the private postgres.log.')
            port = int(os.environ.get('WAYCON_PG_PORT','0'))
            with socket.socket() as probe:
                try:probe.bind(('127.0.0.1',port))
                except OSError:
                    raise RuntimeError('Configured local PostgreSQL port is occupied or unavailable; use WAYCON_PG_PORT=0 for a free port. Existing services were left untouched.') from None
                port=probe.getsockname()[1]
            self.process = subprocess.Popen([str(binaries/'postgres'),'-D',str(private/'data'),
                '-h','127.0.0.1','-p',str(port),'-k',str(private),
                '-c','log_statement=none','-c','log_min_error_statement=panic',
                '-c','log_parameter_max_length=0','-c','log_parameter_max_length_on_error=0'],
                stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,start_new_session=True)
            dsn = make_conninfo(host='127.0.0.1',port=port,dbname='postgres',user='waycon_launcher',password=password,connect_timeout=2)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if self.process.poll() is not None:raise RuntimeError('Owned PostgreSQL exited; inspect the private postgres.log.')
                try:
                    with psycopg.connect(dsn):break
                except psycopg.OperationalError:time.sleep(.1)
            else:raise RuntimeError('Owned PostgreSQL readiness timed out.')
            os.environ.update(DB1_P4_ADMIN_DSN=dsn,DB1_P4_ISOLATED='1',WAYCON_PG_PORT=str(port))
            os.environ.pop('DB1_P4_EXISTING_SERVER',None)
            os.environ.pop('WAYCON_DSN_FROM_FILE',None)
            return self
        except BaseException:
            self.close();raise

    def close(self):
        if self.retained:
            # A web/run drain could not be confirmed. Keep the database and its
            # private directory too; never tear it out from under admitted work.
            if self.log is not None:self.log.close();self.log=None
            return
        if self.process is not None and self.process.poll() is None:
            # Exact Popen handle only; fast PostgreSQL shutdown after web/run drain.
            self.process.send_signal(signal.SIGINT)
            try:self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Owned PostgreSQL did not stop; private cluster retained for inspection.') from None
        if self.log is not None:self.log.close();self.log=None
        if self.temp is not None:shutil.rmtree(self.temp);self.temp=None
        for key,value in self.previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        self.cleaned=True

    def __exit__(self,*_):self.close()

    def retain_for_inspection(self):
        from .console_runs import atomic_json
        self.retained=True
        atomic_json(self.directory/'postgres-retained.json',dict(pid=self.process.pid,
            directory=str(self.temp),reason='backend admission or active run could not be confirmed; retained with web services'))
