"""Native Redis phase-synchronized completion and partition regressions."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from uuid import uuid4
import json,os,subprocess,sys,unittest
from contracts.db1.reference import canonical_json_bytes
from vehicle.common.tests import test_plate_requests_redis as native
from vehicle.common.tests.test_plate_requests import session_fixture,complete_loop
from vehicle.common.tests.test_redis_scalar_integration import eventually
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.codec import vehicle_key
from vehicle.common.redis_scalar.journal import Journal,FINISH
from vehicle.common.plate_requests.worker import RequestWorker
from vehicle.common.plate_requests.host import V2Publisher,DeliveryPlan
from vehicle.common.plate_requests.codec import STREAMS
from vehicle.common.db1_rows.core import Blocked


class CompletionRedisTests(unittest.TestCase):
    setUpClass=classmethod(native.RequestRedisTests.setUpClass.__func__)
    tearDownClass=classmethod(native.RequestRedisTests.tearDownClass.__func__)
    setUp=native.RequestRedisTests.setUp
    tearDown=native.RequestRedisTests.tearDown
    journal=native.RequestRedisTests.journal
    start=native.RequestRedisTests.start

    def seed(self):
        codec=load_request_codec(ORG,IDENTITY_NAMESPACE)
        s,crop,*_=session_fixture(codec);state,_,terminal=complete_loop(codec,s)
        self.addCleanup(lambda:(s.adapter.stop(),s.adapter.cleanup(discard=True)))
        state=deepcopy(state);state['status']='sealing';state.pop('sealed');state.pop('terminal_sha256')
        w=RequestWorker(self.client,codec=codec,namespace=self.ns,consumer='seed');w.setup();j=w.journal
        self.assertTrue(j.acquire())
        entry=self.client.xadd(j.key(STREAMS['crop']),{'json':s.raw})
        self.client.xreadgroup(j.group,j.consumer,{j.key(STREAMS['crop']):'>'},count=1)
        j.handoff(source=STREAMS['crop'],entry_id=entry,marker='fixture-sealing',digest=sha256(s.raw).hexdigest(),
            vehicle=vehicle_key(s.adapter.lifetime),state=state)
        j.release()
        sid=self.client.xadd(j.key(STREAMS['sealed']),{'json':s.sealed})
        c=SimpleNamespace(owner=SimpleNamespace(service_track_id=1,lifetime=s.adapter.lifetime))
        p=object.__new__(V2Publisher);p.attempts=0;p.confirmed=set();p.codec=codec;p.client=self.client;p.journal=j
        p.engine=SimpleNamespace(audit=SimpleNamespace(calls={}))
        p.writer=SimpleNamespace(confirmed={'sealed-1'},entries={'sealed-1':(sid.decode(),STREAMS['sealed'],s.sealed)})
        plan=DeliveryPlan(c,s,b'',b'');p.prepare=lambda _:plan;p.pump=lambda:None
        return w,p,c,s,crop,sid,terminal

    def paused_child(self,phase):
        self.sequence+=1
        marker=Path(self.directory.name)/(self.ns+phase+'.phase')
        log=open(Path(self.directory.name)/(self.ns+phase+'.log'),'wb');self.handles.append(log)
        cmd=[sys.executable,'-m','vehicle.common.redis_scalar.worker','--role','plate_requests','--redis',self.url,
            '--namespace',self.ns,'--org',str(ORG),'--identity-namespace',str(IDENTITY_NAMESPACE),'--consumer','phase-'+phase,
            '--pause-at',phase,'--pause-kind','sealed','--phase-file',str(marker)]
        child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        self.children.append(child);eventually(marker.exists,seconds=15)
        return child

    def test_paused_boundaries_death_and_replacement_repair(self):
        for phase in ('before_prepare','prepared','published','before_ack'):
            self.ns='completion'+uuid4().hex[:20]
            w,p,c,s,crop,sid,terminal=self.seed()
            child=self.paused_child(phase)
            with patch('vehicle.common.plate_requests.host.reference'):
                self.assertFalse(p.publish(c))
            self.assertFalse(crop.released);self.assertFalse(s.adapter.confirmed)
            self.assertEqual(self.client.xpending(w.journal.key(STREAMS['sealed']),w.journal.group)['pending'],1)
            child.kill();child.wait(timeout=3)
            # Existing reviewed helper expires only the confirmed dead owner's
            # exact lease token. Live-owner default remains 60 seconds.
            replacement,_=self.start('plate_requests')
            def done():
                with patch('vehicle.common.plate_requests.host.reference'):return p.publish(c)
            eventually(done,seconds=15)
            self.assertTrue(crop.released);self.assertTrue(s.adapter.confirmed)
            self.assertEqual(self.client.xpending(w.journal.key(STREAMS['sealed']),w.journal.group)['pending'],0)
            rows=self.client.xrange(w.journal.key('results.plate'))
            self.assertEqual([f[b'json'] for _,f in rows],[terminal])
            replacement.terminate();replacement.wait(timeout=3)

    def finish(self,w):
        self.assertTrue(w.step())
        return w.journal

    def test_lost_finish_reply_and_cleanup_retry_after_real_finish(self):
        w,p,c,s,crop,sid,terminal=self.seed();actual=self.client
        class Lost:
            failed=False
            def __getattr__(proxy,k):return getattr(actual,k)
            def eval(proxy,*args):
                answer=actual.eval(*args)
                if args[0]==FINISH and not proxy.failed:
                    proxy.failed=True
                    from redis.exceptions import TimeoutError
                    raise TimeoutError('injected lost FINISH reply')
                return answer
        w.journal.client=Lost()
        from redis.exceptions import TimeoutError
        with self.assertRaises(TimeoutError):w.step()
        self.assertIsNone(actual.hget(w.journal.state,'pending'))
        crop.fail_release=1
        with patch('vehicle.common.plate_requests.host.reference'):
            with self.assertRaises(RuntimeError):p.publish(c)
            self.assertTrue(s.adapter.confirmed);self.assertFalse(crop.released)
            self.assertTrue(p.publish(c))
        self.assertEqual(crop.release_calls,2);w.journal.release()

    def test_wrong_digest_full_session_lifetime_and_changed_terminal(self):
        for defect in ('digest','session','lifetime','terminal','terminal_bytes','unread_input'):
            self.ns='invalid'+uuid4().hex[:20]
            w,p,c,s,crop,sid,terminal=self.seed();j=self.finish(w)
            sealed=p.codec.decode(s.sealed)
            if defect=='unread_input':
                newer=self.client.xadd(j.key(STREAMS['sealed']),{'json':s.sealed})
                p.writer.entries['sealed-1']=(newer.decode(),STREAMS['sealed'],s.sealed)
                with patch('vehicle.common.plate_requests.host.reference'):self.assertFalse(p.publish(c))
                self.assertFalse(crop.released)
                w.step()
                with patch('vehicle.common.plate_requests.host.reference'):self.assertTrue(p.publish(c))
                j.release();continue
            if defect=='digest':self.client.hset(j.state,'seen:'+sealed.event_id,'0'*64)
            elif defect in ('session','lifetime'):
                state=j.read_state(vehicle_key(s.adapter.lifetime))
                if defect=='session':state['sealed']['session']=str(uuid4())
                else:state['sealed']['life']['lifetime_sequence']+=1
                self.client.hset(j.state,'vehicle:'+vehicle_key(s.adapter.lifetime),canonical_json_bytes(state))
            elif defect=='terminal_bytes':
                changed=json.loads(terminal);changed['body']['provenance_sha256']='0'*64
                changed['body_sha256']=sha256(canonical_json_bytes(changed['body'])).hexdigest()
                original=self.client.xrange(j.key('results.plate'))[0][0]
                self.client.xadd(j.key('results.plate'),{'json':canonical_json_bytes(changed)})
                self.client.xdel(j.key('results.plate'),original)
            else:self.client.xadd(j.key('results.plate'),{'json':terminal})
            with self.assertRaises(Blocked):p.publish(c)
            self.assertFalse(crop.released);self.assertFalse(s.adapter.confirmed);j.release()

    def test_foreign_org_zero_calls_then_correct_binding_full_flow(self):
        codec=load_request_codec(ORG,IDENTITY_NAMESPACE);client=Mock();engine=Mock()
        with self.assertRaises(Blocked):V2Publisher(client,Journal(None,namespace=self.ns,trusted_org=uuid4(),role='plate_requests',consumer='foreign'),codec,engine)
        self.assertEqual(client.mock_calls,[]);self.assertEqual(engine.mock_calls,[])
        w,p,c,s,crop,*_=self.seed();self.finish(w)
        with patch('vehicle.common.plate_requests.host.reference'):self.assertTrue(p.publish(c))
        self.assertTrue(crop.released);w.journal.release()
