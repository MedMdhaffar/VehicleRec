"""Owned private Redis and metadata processes for a bounded local run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from ..redis_scalar.worker import connect


class PrivateRuntime:
    def __init__(self,binary,output,*,namespace,org,identity_namespace,delivery_version='v0'):
        self.binary=Path(binary).resolve()
        if not self.binary.is_file() or not os.access(self.binary,os.X_OK):raise ValueError('existing native Redis binary required')
        self.output=Path(output)
        self.namespace,self.org,self.identity_namespace=namespace,org,identity_namespace
        if delivery_version not in {'v0','v1','v2'}:raise ValueError('explicit delivery version required')
        self.roles=('colour','aggregator') if delivery_version=='v0' else ('colour','plate','aggregator_plate') if delivery_version=='v1' else ('colour','plate_requests','aggregator_plate')
        self.children=[]
        self.logs=[]
        self.client=None
        self.directory=None
        self.cleanup={}

    def _spawn(self,args,name):
        log=(self.output/(name+'.log')).open('wb')
        self.logs.append(log)
        child=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','COLOR_AGENT_ENABLED':'false',
                 'YOLO_OFFLINE':'true','YOLO_AUTOINSTALL':'false'})
        self.children.append((name,child))
        return child

    def __enter__(self):
        try:
            self.directory=tempfile.TemporaryDirectory(prefix='vehicle-video-')
            os.chmod(self.directory.name,0o700)
            sock=Path(self.directory.name)/'r.sock'
            server=self._spawn([str(self.binary),'--port','0','--bind','127.0.0.1','--unixsocket',str(sock),
                '--unixsocketperm','700','--save','','--appendonly','no','--maxmemory','64mb',
                '--maxmemory-policy','noeviction','--databases','1','--dir',self.directory.name,'--daemonize','no'],'redis')
            self.url='unix://'+str(sock)
            self.client=connect(self.url)
            deadline=time.monotonic()+10
            while True:
                try:
                    if self.client.ping():break
                except Exception:
                    if server.poll() is not None or time.monotonic()>=deadline:raise RuntimeError('private Redis startup failed')
                    time.sleep(.05)
            self.settings=dict(version=self.client.info('server')['redis_version'])
            for key in ['save','appendonly','port','maxmemory','maxmemory-policy','databases','unixsocketperm']:
                self.settings.update(self.client.config_get(key))
            self.settings.update(socket_mode=oct(sock.stat().st_mode & 0o777),directory_mode=oct(Path(self.directory.name).stat().st_mode & 0o777))
            for role in self.roles:
                self._spawn([sys.executable,'-m','vehicle.common.redis_scalar.worker','--role',role,
                    '--redis',self.url,'--namespace',self.namespace,'--org',str(self.org),
                    '--identity-namespace',str(self.identity_namespace),'--consumer','video-'+role],role)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        exited=[]
        for name,child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
            exited.append(dict(name=name,pid=child.pid,returncode=child.returncode))
        if self.client is not None:self.client.close()
        for log in self.logs:log.close()
        directory=self.directory.name if self.directory is not None else None
        if self.directory is not None:self.directory.cleanup()
        self.cleanup=dict(children=exited,live_pids=[c.pid for _,c in self.children if c.poll() is None],
                          temporary_directory_removed=directory is None or not Path(directory).exists())

    def __exit__(self,*_):self.close()
