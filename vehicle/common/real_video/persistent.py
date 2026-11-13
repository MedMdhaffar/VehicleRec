"""Opt-in owned AOF restart-validation profile. Ephemeral PrivateRuntime is unchanged.

A restart reuses this object's directory and live clients/Engine ledger. It is not
an Engine recovery system or a power-loss guarantee. Failed runs retain data.
"""
import json,os,resource,shutil,subprocess,tempfile,time
from pathlib import Path
from uuid import uuid4
from ..db1_rows.core import require
from ..redis_scalar.worker import connect
from .runtime import PrivateRuntime


class PersistentRuntime(PrivateRuntime):
    def __init__(self,binary,output,*,namespace,org,identity_namespace,workers=True,max_seconds=180,max_restarts=8):
        super().__init__(binary,output,namespace=namespace,org=org,identity_namespace=identity_namespace,delivery_version='v2')
        require(type(max_restarts) is int and 0<=max_restarts<=8 and 0<max_seconds<=300,
                'INVALID_CAPACITY','bounded owned restart profile required')
        self.workers=workers;self.max_seconds=max_seconds;self.max_restarts=max_restarts
        self.restarts=0;self.server=None;self.data_path=None;self.identity=None;self.marker=None
        self.started=None;self.completed=False;self.events=[];self.max_disk_bytes=96*1024*1024;self.max_file_bytes=8*1024*1024
        self.closed=False

    def check(self):
        require(time.monotonic()-self.started<=self.max_seconds,'RUNTIME_BUDGET','persistent run time budget exceeded')
        stat=self.data_path.stat()
        require((stat.st_dev,stat.st_ino)==self.identity and (self.data_path/'owner.json').read_bytes()==self.marker,
                'DIRECTORY_IDENTITY','owned directory identity changed')
        files=[p for p in self.data_path.rglob('*') if p.is_file()]
        size=sum(p.stat().st_size for p in files)
        require(len(files)<=16 and size<=self.max_disk_bytes and all(p.stat().st_size<=self.max_file_bytes for p in files),
                'DISK_CAPACITY','owned AOF directory budget exceeded')
        return dict(files=len(files),bytes=size,max_bytes=self.max_disk_bytes)

    def __enter__(self):
        self.started=time.monotonic();self.output.mkdir(parents=True,exist_ok=True)
        self.data_path=Path(tempfile.mkdtemp(prefix='waycon-aof-'));os.chmod(self.data_path,0o700)
        s=self.data_path.stat();self.identity=(s.st_dev,s.st_ino)
        self.marker=json.dumps(dict(owner=str(uuid4()),namespace=self.namespace,org_id=str(self.org),profile='aof-always-v1'),sort_keys=True).encode()
        (self.data_path/'owner.json').write_bytes(self.marker)
        self.url='unix://'+str(self.data_path/'r.sock');self.client=connect(self.url)
        try:
            self.start_server()
            if self.workers:
                for role in self.roles:self.start_worker(role)
            return self
        except BaseException:
            self.close();raise

    def start_worker(self,role,*,extra=()):
        require(role in self.roles,'INVALID_WORKER','configured metadata role required')
        import sys
        return self._spawn([sys.executable,'-m','vehicle.common.redis_scalar.worker','--role',role,'--redis',self.url,
            '--namespace',self.namespace,'--org',str(self.org),'--identity-namespace',str(self.identity_namespace),
            '--consumer','persistent-'+role+'-'+str(len(self.children)),'--run-seconds',str(self.max_seconds),*extra],role+'-'+str(len(self.children)))

    def start_server(self):
        require(self.server is None or self.server.poll() is not None,'SERVER_ACTIVE','owned Redis is still active')
        self.check()
        require(self.restarts<=self.max_restarts,'RESTART_CAPACITY','restart budget exhausted')
        args=[str(self.binary),'--port','0','--bind','127.0.0.1','--unixsocket',str(self.data_path/'r.sock'),
            '--unixsocketperm','700','--save','','--appendonly','yes','--appendfsync','always',
            '--no-appendfsync-on-rewrite','no','--auto-aof-rewrite-percentage','0','--aof-use-rdb-preamble','no',
            '--aof-load-truncated','no','--maxmemory','64mb','--maxmemory-policy','noeviction','--databases','1',
            '--dir',str(self.data_path),'--daemonize','no']
        log=(self.output/f'redis-{self.restarts}.log').open('wb');self.logs.append(log)
        file_limit=self.max_file_bytes
        def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(file_limit,file_limit))
        self.server=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,preexec_fn=limits)
        self.children.append(('redis-'+str(self.restarts),self.server))
        self.client.connection_pool.disconnect()
        deadline=time.monotonic()+10
        while True:
            try:
                if self.client.ping():break
            except Exception:
                if self.server.poll() is not None or time.monotonic()>=deadline:raise RuntimeError('OWNED_AOF_START_FAILED') from None
                time.sleep(.025)
        settings={}
        for key in ['appendonly','appendfsync','save','maxmemory','maxmemory-policy','databases','port',
                    'auto-aof-rewrite-percentage','aof-use-rdb-preamble','aof-load-truncated','no-appendfsync-on-rewrite','dir']:
            settings.update(self.client.config_get(key))
        require(settings['appendonly']=='yes' and settings['appendfsync']=='always' and settings['maxmemory-policy']=='noeviction'
                and settings['save']=='' and int(settings['port'])==0 and settings['dir']==str(self.data_path),
                'PERSISTENCE_CONFIG','effective owned AOF settings differ')
        persistence=self.client.info('persistence')
        require(persistence['aof_enabled']==1 and persistence['aof_last_write_status']=='ok','AOF_UNHEALTHY','AOF is not healthy')
        settings.update(version=self.client.info('server')['redis_version'],directory_mode=oct(self.data_path.stat().st_mode&0o777),
            socket_mode=oct((self.data_path/'r.sock').stat().st_mode&0o777))
        self.settings=settings;self.events.append(dict(event='started',generation=self.restarts,pid=self.server.pid,settings=settings,disk=self.check()))
        self.restarts+=1

    def stop_server(self,*,abrupt=False):
        require(self.server is not None,'SERVER_UNAVAILABLE','owned Redis was not started')
        if self.server.poll() is None:
            self.server.kill() if abrupt else self.server.terminate()
            self.server.wait(timeout=5)
        self.events.append(dict(event='SIGKILL' if abrupt else 'stop',pid=self.server.pid,returncode=self.server.returncode,disk=self.check()))
        self.client.connection_pool.disconnect()

    def declare_complete(self):
        """Caller has validated native settlement and all expected final deliveries.

        Refuse outstanding journal intents or group PEL entries. This declaration
        is for disposable validated test data, never permission to discard a live
        Engine's unknown native outcomes or an uncommitted production delivery.
        """
        self.check()
        prefix=f'{self.namespace}:{{vehicle-v1.{self.org}}}:'
        keys=list(self.client.scan_iter(match=prefix+'*',count=64))
        require(len(keys)<=64,'CAPACITY_EXCEEDED','owned namespace key budget')
        for key in keys:
            kind=self.client.type(key)
            if kind==b'hash' and key.endswith(b':journal'):
                require(self.client.hget(key,'pending') is None,'RECOVERY_PENDING','unfinished metadata intent')
            if kind==b'stream':
                require(self.client.xlen(key)<=256,'CAPACITY_EXCEEDED','stream count budget')
                for group in self.client.xinfo_groups(key):
                    require(group['pending']==0 and not group.get('lag'),'RECOVERY_PENDING','unacknowledged/unconsumed handoff')
        self.completed=True

    def close(self):
        if self.closed:return
        exited=[]
        for name,child in reversed(self.children):
            if child.poll() is None:
                # A test may deliberately SIGSTOP a metadata child.
                import signal
                child.send_signal(signal.SIGCONT);child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
            exited.append(dict(name=name,pid=child.pid,returncode=child.returncode))
        if self.client is not None:self.client.close()
        for log in self.logs:log.close()
        removed=False
        if self.completed and self.data_path is not None:
            s=self.data_path.stat()
            require((s.st_dev,s.st_ino)==self.identity,'DIRECTORY_IDENTITY','refuse foreign cleanup')
            shutil.rmtree(self.data_path);removed=True
        self.cleanup=dict(children=exited,live_pids=[p.pid for _,p in self.children if p.poll() is None],
            data_directory_removed=removed,data_retained=bool(self.data_path) and not removed,
            retained_path=str(self.data_path) if self.data_path and not removed else None)
        self.closed=True
