"""Owned AOF/always Redis restart tests; Engine/native evidence is injected."""
import json,os,signal,tempfile,time,unittest
from pathlib import Path
from uuid import uuid4
from hashlib import sha256
from contracts.db1.reference import canonical_json_bytes
from vehicle.common.real_video.persistent import PersistentRuntime
from vehicle.common.plate_requests.host import V2Publisher
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.tests import test_mp4_colour_plate as injected
from vehicle.common.tests.test_redis_scalar_integration import eventually


def freeze(children):
    for child in children:
        child.send_signal(signal.SIGSTOP)
        pid,status=os.waitpid(child.pid,os.WUNTRACED)
        assert pid==child.pid and os.WIFSTOPPED(status)


def snapshot(client,prefix):
    result={}
    for key in sorted(client.scan_iter(match=prefix+'*',count=64)):
        kind=client.type(key)
        if kind==b'stream':
            groups=[]
            for g in client.xinfo_groups(key):
                pending=client.xpending_range(key,g['name'],'-','+',256)
                groups.append((g['name'],g['pending'],g['last-delivered-id'],
                    [(v['message_id'],v['consumer']) for v in pending]))
            result[key]=(kind,client.xrange(key),groups)
        elif kind==b'hash':result[key]=(kind,client.hgetall(key))
        elif kind==b'string':result[key]=(kind,client.get(key))
        else:raise AssertionError('unexpected owned key type')
    return result


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.binary=os.environ.get('VEHICLE_REDIS_SERVER')
        if not self.binary or not Path(self.binary).is_file():self.skipTest('explicit existing native Redis binary required')
        self.codec=load_request_codec(ORG,IDENTITY_NAMESPACE)

    def owner(self):
        o=injected.CombinedVideoTests('runTest');o.setUpClass();o.setUp();self.addCleanup(o.doCleanups)
        return o,o.engine()

    def test_stop_and_sigkill_exact_state_at_all_handoff_boundaries(self):
        audits=[]
        for abrupt in (False,True):
            for phase in ('before_prepare','prepared','published','acknowledged','final_delivery'):
                with self.subTest(abrupt=abrupt,phase=phase),tempfile.TemporaryDirectory() as out:
                    owner,engine=self.owner();ns='persist'+uuid4().hex[:20]
                    rt=PersistentRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,workers=False,max_seconds=90)
                    with rt:
                        workers=[rt.start_worker(role) for role in ('colour','aggregator_plate')]
                        marker=Path(out)/'phase';release=Path(out)/'release'
                        extra=[] if phase=='final_delivery' else ['--pause-at',phase,'--pause-kind','crop' if phase=='before_prepare' else 'sealed',
                            '--phase-file',str(marker),'--resume-file',str(release)]
                        workers.append(rt.start_worker('plate_requests',extra=extra))
                        journal=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='live-engine')
                        p=V2Publisher(rt.client,journal,self.codec,engine);closed=owner.one_frame(engine)
                        def boundary():
                            p.publish(closed);rt.check()
                            return rt.client.xlen(journal.key('db1.delivery.v1'))==1 if phase=='final_delivery' else marker.exists()
                        eventually(boundary,seconds=20)
                        if phase in ('prepared','published'):self.assertFalse(p.plans[1].session.adapter.confirmed)
                        freeze(workers)
                        before=snapshot(rt.client,journal.prefix)
                        native_before=dict(engine.audit.calls);session=p.session
                        identity=rt.identity;rt.stop_server(abrupt=abrupt);rt.start_server()
                        self.assertEqual(identity,rt.identity)
                        self.assertEqual(before,snapshot(rt.client,journal.prefix))
                        self.assertEqual(p.reconnect(),session)
                        release.write_text('resume')
                        for child in workers:child.send_signal(signal.SIGCONT)
                        eventually(lambda:p.publish(closed),seconds=20)
                        eventually(lambda:rt.client.xlen(journal.key('db1.delivery.v1'))==1,seconds=15)
                        plan=p.plans[1]
                        raw=rt.client.xrange(journal.key('db1.delivery.v1'))[0][1][b'json']
                        delivery=self.codec.v1.decode(raw)
                        self.assertEqual(canonical_json_bytes(delivery.body['bundle']),plan.reference.payload.canonical)
                        calls=dict(engine.audit.calls)
                        self.assertTrue(p.publish(closed));self.assertEqual(calls,engine.audit.calls)
                        self.assertEqual(calls.get('whole_ocr'),1);self.assertEqual(calls.get('marker'),1)
                        engine.acknowledge(1);engine.close()
                        self.assertEqual(engine.plate.buffers,[])
                        rt.declare_complete()
                        audit=dict(phase=phase,abrupt=abrupt,session=session,exact_stream_group_journal_replay=True,
                            before_calls=native_before,final_calls=calls,bundle_sha256=plan.reference.digest,
                            settings=rt.settings,disk=rt.check(),events=rt.events)
                    self.assertTrue(rt.cleanup['data_directory_removed']);self.assertFalse(rt.cleanup['live_pids'])
                    audit['cleanup']=rt.cleanup;audits.append(audit)
        print('PERSISTENCE_BOUNDARIES '+json.dumps(audits,sort_keys=True),flush=True)

    def test_metadata_replacement_fence_and_live_engine_resume(self):
        from vehicle.common.redis_scalar.journal import Journal
        from redis.exceptions import ResponseError
        owner,engine=self.owner()
        with tempfile.TemporaryDirectory() as out:
            ns='replace'+uuid4().hex[:20]
            with PersistentRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,workers=False) as rt:
                others=[rt.start_worker(role) for role in ('colour','aggregator_plate')]
                marker=Path(out)/'phase'
                dead=rt.start_worker('plate_requests',extra=['--pause-at','prepared','--pause-kind','sealed','--phase-file',str(marker)])
                journal=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='engine')
                p=V2Publisher(rt.client,journal,self.codec,engine);closed=owner.one_frame(engine)
                def prepared():p.publish(closed);return marker.exists()
                eventually(prepared,seconds=20)
                state=journal.prefix+'plate_requests:journal';pending=rt.client.hget(state,'pending')
                lease=journal.prefix+'plate_requests:lease';old_token=rt.client.get(lease)
                freeze(others);dead.kill();dead.wait(timeout=3)
                rt.stop_server(abrupt=True);rt.start_server()
                self.assertEqual(rt.client.hget(state,'pending'),pending)
                self.assertEqual(rt.client.get(lease),old_token)
                # Exact-token test-clock advance only after confirmed process death.
                rt.client.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('PEXPIRE',KEYS[1],1) end return 0",1,lease,old_token)
                eventually(lambda:rt.client.get(lease) is None)
                replacement=rt.start_worker('plate_requests')
                for child in others:child.send_signal(signal.SIGCONT)
                eventually(lambda:p.publish(closed),seconds=20)
                stale=Journal(rt.client,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='dead-owner',lease_ms=60000);stale.token=old_token
                with self.assertRaises(ResponseError):stale.renew()
                self.assertNotEqual(rt.client.get(lease),old_token)
                self.assertIsNone(rt.client.hget(state,'pending'))
                engine.acknowledge(1);engine.close()
                eventually(lambda:rt.client.xlen(journal.key('db1.delivery.v1'))==1)
                rt.declare_complete();events=rt.events
            self.assertTrue(rt.cleanup['data_directory_removed'])
            print('PERSISTENCE_REPLACEMENT '+json.dumps(dict(repaired=True,stale_owner_rejected=True,dead_owner_clock_advance=True,events=events,cleanup=rt.cleanup)),flush=True)

    def test_new_engine_cannot_claim_persisted_namespace_or_old_handles(self):
        from vehicle.common.db1_rows.core import Blocked
        from vehicle.common.tests.test_plate_requests import session_fixture,complete_loop
        from dataclasses import replace
        owner,engine=self.owner()
        with tempfile.TemporaryDirectory() as out:
            ns='ownership'+uuid4().hex[:20]
            with PersistentRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,workers=False) as rt:
                j=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='engine')
                p=V2Publisher(rt.client,j,self.codec,engine)
                rt.stop_server(abrupt=True);rt.start_server()
                with self.assertRaises(Blocked):V2Publisher(rt.client,j,self.codec,engine)
                self.assertEqual(p.reconnect(),p.session)
                old,crop,*_=session_fixture(self.codec)
                new,new_crop,*_=session_fixture(self.codec)
                try:
                    state,_,_=complete_loop(self.codec,old)
                    request=next(iter(state['requests'].values()))
                    rejection=self.codec.decode(new.admission(self.codec.decode(self.codec.make('request',request))))
                    self.assertEqual(rejection.body['reason'],'session_unavailable')
                    self.assertEqual(new.adapter.records(),())
                finally:
                    for s in (old,new):s.adapter.stop();s.adapter.cleanup(discard=True)
                engine.close();rt.declare_complete()
            self.assertTrue(rt.cleanup['data_directory_removed'])
