"""Real Redis only: isolated native child server, private socket/dir; no shared keys.

No substitutes/mocks for Redis semantics. The class skips if no native server is
available; scripts/vehicle_redis_integration.py treats that skip as a blocker.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.redis_scalar.codec import bounded_json, vehicle_key
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE,fixture
from vehicle.common.redis_scalar.worker import Worker,load_codec,connect,DEFAULT_LEASE_MS
from vehicle.common.redis_scalar.journal import Journal,PREPARE,PUBLISH,FINISH,ACK_DUPLICATE
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.tests.test_colour_aggregator import fixture as memory_fixture
from vehicle.common.colour_metadata import ColourMetadata
from vehicle.common.aggregator import Aggregator,Branch

ROOT=Path(__file__).resolve().parents[3]


def eventually(predicate, seconds=10):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        result=predicate()
        if result:return result
        time.sleep(.02)
    raise AssertionError('bounded integration wait expired')


class RedisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server=os.environ.get('VEHICLE_REDIS_SERVER') or shutil.which('redis-server')
        if not server or not Path(server).is_file() or not os.access(server,os.X_OK):
            raise unittest.SkipTest('BLOCKED: native redis-server unavailable; no real Redis validation executed')
        cls.directory=tempfile.TemporaryDirectory(prefix='vehicle-redis-')
        os.chmod(cls.directory.name,0o700)
        cls.socket=str(Path(cls.directory.name)/'r.sock')
        cls.url='unix://'+cls.socket
        cls.log=open(Path(cls.directory.name)/'redis.log','wb')
        cls.server=subprocess.Popen([server,'--port','0','--bind','127.0.0.1','--unixsocket',cls.socket,
            '--unixsocketperm','700','--save','','--appendonly','no','--maxmemory','64mb',
            '--maxmemory-policy','noeviction','--databases','1','--dir',cls.directory.name,
            '--daemonize','no'],stdout=cls.log,stderr=subprocess.STDOUT)
        cls.client=connect(cls.url)
        try:
            def ready():
                try:return cls.client.ping()
                except Exception:
                    if cls.server.poll() is not None:raise RuntimeError('native Redis failed to start')
                    return False
            eventually(ready)
            version=cls.client.info('server')['redis_version']
            if tuple(int(p) for p in version.split('.')[:2])<(6,2):
                raise RuntimeError('Redis >=6.2 required for XAUTOCLAIM')
            cls.codec=load_codec(ORG,IDENTITY_NAMESPACE)
            settings={k:v for pattern in ('appendonly','save','maxmemory-policy','maxmemory','port')
                      for k,v in cls.client.config_get(pattern).items()}
            print('REAL_REDIS_SETTINGS '+json.dumps(dict(version=version,**settings)),flush=True)
        except Exception:
            cls.server.terminate();cls.server.wait(timeout=3)
            cls.client.close();cls.log.close();cls.directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.terminate()
        cls.server.wait(timeout=3)
        cls.log.close()
        cls.directory.cleanup()

    def setUp(self):
        self.ns='t'+uuid4().hex
        self.children=[]
        self.handles=[]
        self.sequence=0

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
        for handle in self.handles:handle.close()
        # No FLUSH, shared server, shared key deletion, trimming or config changes.

    def journal(self,role='aggregator',**kw):
        kw.setdefault('lease_ms',DEFAULT_LEASE_MS[role])
        return Journal(self.client,namespace=self.ns,trusted_org=ORG,role=role,
                       consumer='test-'+uuid4().hex[:8],**kw)

    def worker(self,role='aggregator',**kw):
        w=Worker(self.client,codec=self.codec,namespace=self.ns,role=role,consumer='direct-'+uuid4().hex[:8],**kw)
        w.setup()
        return w

    def start(self,role,phase=None):
        # Normal tests use production defaults. For a confirmed dead child only,
        # accelerate its remaining lease instead of sleeping 60s after SIGKILL.
        # Compare the exact dead owner's token atomically; never expire a live
        # competitor's/new owner's lease. This is private test-clock control.
        for previous in self.children:
            if previous.poll() is None:continue
            args=previous.args
            if args[args.index('--namespace')+1]!=self.ns or args[args.index('--role')+1]!=role:continue
            owner=args[args.index('--consumer')+1]
            key=self.journal(role).lease;held=self.client.get(key)
            if held and held.startswith((owner+'-').encode()):
                self.client.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('PEXPIRE',KEYS[1],1) end return 0",1,key,held)
                eventually(lambda:self.client.get(key)!=held)
        self.sequence+=1
        marker=Path(self.directory.name)/f'{self.ns}-{self.sequence}.phase'
        log=open(Path(self.directory.name)/f'{self.ns}-{self.sequence}.log','wb')
        self.handles.append(log)
        cmd=[sys.executable,'-m','vehicle.common.redis_scalar.worker','--redis',self.url,'--namespace',self.ns,
             '--org',str(ORG),'--identity-namespace',str(IDENTITY_NAMESPACE),'--consumer',f'{role}-{self.sequence}',
             '--role',role]
        if phase:cmd+=['--pause-at',phase,'--phase-file',str(marker)]
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        self.children.append(child)
        return child,marker

    def send(self,stream,raw):
        return self.client.xadd(self.journal().key(stream),{'json':raw})

    def count(self,stream):return self.client.xlen(self.journal().key(stream))

    def wait_delivery(self,count=1):eventually(lambda:self.count('db1.delivery')==count)

    def state(self,role,life):return self.journal(role).read_state(vehicle_key(life))

    def assert_exact_delivery(self):
        raw=self.client.xrange(self.journal().key('db1.delivery'))[0][1][b'json']
        message=self.codec.decode(raw)
        owner,measurements,classes=memory_fixture()
        expected=Aggregator(identity_policy=self.codec.identity_policy,validation=self.codec.validation).join(
            owner,classes,(Branch('colour','enabled',ColourMetadata().finalize(owner.lifetime,measurements)),
                          Branch('plate','disabled'),Branch('manufacturer','unsupported'))).delivery
        self.assertEqual(canonical_json_bytes(message.body['bundle']),expected.payload.canonical)
        self.assertEqual(message.body['bundle_sha256'],expected.digest)

    def test_separate_processes_exact_bytes_both_input_orders(self):
        for order in ('closed-first','colour-first'):
            with self.subTest(order=order):
                self.ns='t'+uuid4().hex
                self.start('colour');self.start('aggregator')
                closed,batch=fixture(self.codec)
                if order=='closed-first':
                    self.send('engine.lifecycle',closed)
                    eventually(lambda:self.state('aggregator',self.codec.decode(closed).life))
                    self.send('engine.evidence.colour',batch)
                else:
                    self.send('engine.evidence.colour',batch)
                    eventually(lambda:self.count('results.colour')==1)
                    eventually(lambda:self.state('aggregator',self.codec.decode(closed).life))
                    self.send('engine.lifecycle',closed)
                self.wait_delivery();self.assert_exact_delivery()

    def test_identical_duplicate_changed_content_and_malformed_cross_org(self):
        self.start('colour');self.start('aggregator')
        closed,batch=fixture(self.codec)
        self.send('engine.lifecycle',closed);self.send('engine.evidence.colour',batch)
        self.wait_delivery()
        self.send('engine.evidence.colour',batch)
        changed=bounded_json(batch)
        changed['body']['measurements'][0]['quality']=.7
        changed['body_sha256']=sha256(canonical_json_bytes(changed['body'])).hexdigest()
        self.send('engine.evidence.colour',canonical_json_bytes(changed))
        self.send('engine.evidence.colour',b'{"duplicate":1,"duplicate":2}')
        wrong=bounded_json(batch);wrong['org_id']=str(uuid4())
        self.send('engine.evidence.colour',canonical_json_bytes(wrong))
        eventually(lambda:self.count('diagnostics.colour')==3)
        codes=[bounded_json(r[1][b'json'])['code'] for r in self.client.xrange(self.journal().key('diagnostics.colour'))]
        self.assertEqual(set(codes),{'PAYLOAD_CONFLICT','INVALID_MESSAGE','ORG_MISMATCH'})
        eventually(lambda:self.client.xpending(self.journal().key('engine.evidence.colour'),'colour-v1')['pending']==0)
        self.assertEqual(self.count('results.colour'),1);self.assertEqual(self.count('db1.delivery'),1)

    def test_restart_waiting_and_other_ready_vehicle_progresses(self):
        self.start('colour');aggregator,_=self.start('aggregator')
        closed,batch=fixture(self.codec,plate='enabled')
        life=self.codec.decode(closed).life
        self.send('engine.lifecycle',closed);self.send('engine.evidence.colour',batch)
        eventually(lambda:(self.state('aggregator',life) or {}).get('colour'))
        self.assertEqual(self.state('aggregator',life)['status'],'waiting')
        self.assertEqual(self.count('db1.delivery'),0)
        aggregator.kill();aggregator.wait(timeout=3)
        self.start('aggregator')
        ready,batch=fixture(self.codec,track=2)
        self.send('engine.lifecycle',ready);self.send('engine.evidence.colour',batch)
        self.wait_delivery()
        self.assertEqual(self.state('aggregator',life)['status'],'waiting')
        self.assertEqual(self.state('aggregator',self.codec.decode(ready).life)['status'],'ready')

    def test_short_processing_lease_fails_closed_and_normal_owner_recovers(self):
        from unittest.mock import patch
        from redis.exceptions import ResponseError
        closed,batch=fixture(self.codec)
        self.send('engine.evidence.colour',batch)
        first=self.worker('colour',lease_ms=300)
        def delayed(*args,**kwargs):
            result=reduce_message(*args,**kwargs)
            time.sleep(.35) # deliberate lease expiry, not an operating timeout
            return result
        with patch('vehicle.common.redis_scalar.worker.reduce_message',side_effect=delayed):
            with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):first.step()
        self.assertEqual(self.client.hlen(first.journal.state),0)
        self.assertEqual(self.count('results.colour'),0)
        self.assertEqual(self.client.xpending(first.journal.key('engine.evidence.colour'),'colour-v1')['pending'],1)
        second=self.worker('colour') # existing normal 2000ms profile
        self.assertTrue(second.step())
        self.assertEqual(self.count('results.colour'),1)
        self.assertEqual(self.client.xpending(first.journal.key('engine.evidence.colour'),'colour-v1')['pending'],0)
        before=self.client.hgetall(first.journal.state)
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):first.journal.renew()
        self.assertEqual(self.client.hgetall(first.journal.state),before)
        second.journal.release()

    def test_competing_consumers_and_stale_fence_rejected(self):
        first=self.journal('colour',lease_ms=300);second=self.journal('colour',lease_ms=300)
        self.assertTrue(first.acquire());self.assertFalse(second.acquire())
        time.sleep(.35)
        self.assertTrue(second.acquire())
        from redis.exceptions import ResponseError
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):first.renew()
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):
            first.handoff(source='engine.evidence.colour',entry_id=b'1-0',marker='test',digest='a'*64)
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):
            first.call(PUBLISH,[first.lease,first.state,first.key('results.colour')],first.token,b'{}','1-0',b'{}',10)
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):
            first.call(FINISH,[first.lease,first.state,first.key('engine.evidence.colour')],first.token,b'{}','colour-v1','1-0')
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):
            first.call(PREPARE,[first.lease,first.state],first.token,0,'stale','a'*64,'',b'',b'{}',256,128)
        with self.assertRaisesRegex(ResponseError,'STALE_OWNER'):
            first.call(ACK_DUPLICATE,[first.lease,first.state,first.key('engine.evidence.colour')],first.token,'stale','a'*64,'colour-v1','1-0')
        self.assertEqual(self.client.hlen(first.state),0)
        self.assertEqual(self.count('results.colour'),0)
        second.release()
        self.start('colour');self.start('colour');self.start('aggregator');self.start('aggregator')
        closed,batch=fixture(self.codec)
        self.send('engine.lifecycle',closed);self.send('engine.evidence.colour',batch)
        self.wait_delivery();self.assertEqual(self.count('results.colour'),1)

    def test_both_aggregator_streams_continuously_ready_are_fair(self):
        worker=self.worker('aggregator')
        for track in range(1,7):
            closed,batch=fixture(self.codec,track=track)
            _,out=reduce_message(self.codec,'colour',self.codec.decode(batch),None)
            self.send('engine.lifecycle',closed);self.send('results.colour',out[0][1])
        pending={}
        for source in worker.sources:
            rows=self.client.xreadgroup(worker.journal.group,'previous-reader',
                {worker.journal.key(source):'>'},count=2)[0][1]
            pending[source]={row[0] for row in rows}
        served=[]
        original=worker.process
        def record(source,entry_id,fields):
            served.append((source,entry_id))
            return original(source,entry_id,fields)
        worker.process=record
        # Both sources still have new entries after all eight turns; each also
        # started with two pending entries, so test all four scheduling lanes.
        for _ in range(8):self.assertTrue(worker.step())
        self.assertTrue(all(served[i][0]!=served[i+1][0] for i in range(7)),served)
        for source in worker.sources:
            ids=[entry for stream,entry in served if stream==source]
            self.assertEqual(len(ids),4,served)
            self.assertEqual(sum(entry in pending[source] for entry in ids),2,served)
        for _ in range(4):self.assertTrue(worker.step())
        self.assertEqual(self.count('db1.delivery'),6)
        worker.journal.release()

    def test_changed_batch_identity_conflicts_and_original_retry_still_works(self):
        self.start('colour');self.start('aggregator')
        closed,batch=fixture(self.codec)
        message=self.codec.decode(batch)
        self.send('engine.evidence.colour',batch)
        eventually(lambda:self.count('results.colour')==1)
        changed=bounded_json(batch);changed['event_id']=str(uuid4())
        self.send('engine.evidence.colour',canonical_json_bytes(changed))
        self.send('engine.evidence.colour',batch)
        eventually(lambda:self.count('diagnostics.colour')==1)
        eventually(lambda:self.client.xpending(self.journal().key('engine.evidence.colour'),'colour-v1')['pending']==0)
        self.assertEqual(self.state('colour',message.life)['batch_id'],message.event_id)
        terminal=self.client.xrange(self.journal().key('results.colour'))[0][1][b'json']
        self.assertEqual(self.codec.decode(terminal).body['batch_id'],message.event_id)
        diagnostic=self.client.xrange(self.journal().key('diagnostics.colour'))[0][1][b'json']
        self.assertEqual(bounded_json(diagnostic)['code'],'PAYLOAD_CONFLICT')
        self.assertEqual(self.count('results.colour'),1)
        self.send('engine.lifecycle',closed)
        self.wait_delivery();self.assert_exact_delivery()

    def test_invalid_closure_cannot_poison_later_valid_join(self):
        for order in ('closed-first','terminal-first'):
            with self.subTest(order=order):
                self.ns='t'+uuid4().hex
                self.start('colour');self.start('aggregator')
                closed,batch=fixture(self.codec)
                life=self.codec.decode(closed).life
                if order=='terminal-first':
                    self.send('engine.evidence.colour',batch)
                    eventually(lambda:(self.state('aggregator',life) or {}).get('colour'))
                invalid=bounded_json(closed)
                invalid['body']['classes'].append(dict(invalid['body']['classes'][0]))
                invalid['body_sha256']=sha256(canonical_json_bytes(invalid['body'])).hexdigest()
                self.send('engine.lifecycle',canonical_json_bytes(invalid))
                eventually(lambda:self.count('diagnostics.aggregator')==1)
                retained=self.state('aggregator',life)
                self.assertNotIn('closed',retained or {})
                if order=='closed-first':self.assertIsNone(retained)
                self.send('engine.lifecycle',closed)  # Same event ID as invalid input.
                if order=='closed-first':self.send('engine.evidence.colour',batch)
                self.wait_delivery();self.assert_exact_delivery()
                self.assertEqual(self.state('aggregator',life)['status'],'ready')

    def test_child_kill_at_handoff_publication_and_ack_boundaries(self):
        for role in ('colour','aggregator'):
            for phase in ('prepared','published','before_ack','acknowledged'):
                with self.subTest(role=role,phase=phase):
                    self.ns='t'+uuid4().hex
                    closed,batch=fixture(self.codec)
                    if role=='colour':
                        self.start('aggregator');self.send('engine.lifecycle',closed)
                    else:
                        direct=self.worker('aggregator')
                        self.send('engine.lifecycle',closed);direct.step();direct.journal.release()
                    child,marker=self.start(role,phase)
                    if role=='colour':self.send('engine.evidence.colour',batch)
                    else:
                        _,out=reduce_message(self.codec,'colour',self.codec.decode(batch),None)
                        self.send('results.colour',out[0][1])
                    eventually(marker.exists)
                    child.kill();child.wait(timeout=3)
                    self.start(role)
                    self.wait_delivery();self.assert_exact_delivery()
                    self.assertEqual(self.count('results.colour'),1)
                    eventually(lambda:not self.client.hexists(self.journal(role).state,'pending'))

    def test_partial_lua_write_is_repaired_not_rolled_back(self):
        worker=self.worker('colour')
        _,batch=fixture(self.codec);self.send('engine.evidence.colour',batch)
        original=worker.journal.call
        def failing(script,keys,*args):
            if script==PUBLISH:
                script=script.replace("return 'published'","return redis.error_reply('INJECTED_AFTER_WRITE')")
            return original(script,keys,*args)
        worker.journal.call=failing
        from redis.exceptions import ResponseError
        with self.assertRaisesRegex(ResponseError,'INJECTED_AFTER_WRITE'):worker.step()
        self.assertEqual(self.count('results.colour'),1)
        self.assertTrue(self.client.hexists(worker.journal.state,'pending'))
        self.assertEqual(self.client.xpending(worker.journal.key('engine.evidence.colour'),'colour-v1')['pending'],1)
        worker.journal.call=original
        worker.step()
        self.assertEqual(self.count('results.colour'),1)
        self.assertFalse(self.client.hexists(worker.journal.state,'pending'))

    def test_lost_command_replies_reconcile_without_extra_publication(self):
        from redis.exceptions import TimeoutError
        for target in (PREPARE,PUBLISH,FINISH):
            with self.subTest(target=target[:20]):
                self.ns='t'+uuid4().hex
                worker=self.worker('colour')
                _,batch=fixture(self.codec);self.send('engine.evidence.colour',batch)
                original=worker.journal.call
                lost=False
                def drop_reply(script,keys,*args):
                    nonlocal lost
                    reply=original(script,keys,*args)
                    if script==target and not lost:
                        lost=True
                        raise TimeoutError('injected lost reply')
                    return reply
                worker.journal.call=drop_reply
                with self.assertRaises(TimeoutError):worker.step()
                worker.journal.call=original
                worker.journal.release()
                replacement=self.worker('colour')
                replacement.step()
                self.assertEqual(self.count('results.colour'),1)
                self.assertEqual(self.client.xpending(worker.journal.key('engine.evidence.colour'),'colour-v1')['pending'],0)
                replacement.journal.release()

    def test_capacity_preserves_input_and_pending_output_intent(self):
        from redis.exceptions import ResponseError
        worker=self.worker('colour',max_vehicles=1)
        _,batch=fixture(self.codec);self.send('engine.evidence.colour',batch);worker.step()
        _,other=fixture(self.codec,track=2);self.send('engine.evidence.colour',other)
        with self.assertRaisesRegex(ResponseError,'CAPACITY_EXCEEDED'):worker.step()
        self.assertEqual(self.client.xpending(worker.journal.key('engine.evidence.colour'),'colour-v1')['pending'],1)
        self.assertEqual(self.count('engine.evidence.colour'),2)
        self.assertEqual(self.count('results.colour'),1)
        self.ns='t'+uuid4().hex
        limited=self.worker('colour',max_stream=1)
        self.client.xadd(limited.journal.key('results.colour'),{'json':b'{}'},id='0-1')
        self.send('engine.evidence.colour',batch)
        with self.assertRaisesRegex(ResponseError,'CAPACITY_EXCEEDED'):limited.step()
        self.assertTrue(self.client.hexists(limited.journal.state,'pending'))
        self.assertEqual(self.client.xpending(limited.journal.key('engine.evidence.colour'),'colour-v1')['pending'],1)


if __name__=='__main__':unittest.main()
