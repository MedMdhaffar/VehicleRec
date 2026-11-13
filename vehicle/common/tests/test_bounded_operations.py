"""Operational host/status boundaries with injected native work and owned Redis."""
import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from uuid import uuid4
from vehicle.common.real_video.runtime import PrivateRuntime
from vehicle.common.real_video.persistent import PersistentRuntime
from vehicle.common.real_video.bounded import drive_bounded
from vehicle.common.real_video.injected import InjectedVideo
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.plate_requests.host import V2Publisher
from vehicle.common.tests.test_redis_scalar_integration import eventually
from vehicle.common.db1_rows.core import Blocked


class BoundedOperationsTests(unittest.TestCase):
    def setUp(self):
        self.binary=os.environ.get('VEHICLE_REDIS_SERVER')
        if not self.binary or not Path(self.binary).is_file():self.skipTest('explicit existing native Redis binary required')
        self.codec=load_request_codec(ORG,IDENTITY_NAMESPACE)
    def make_video(self,run,frames=3):
        v=InjectedVideo(org=ORG,run=run,camera='owned',timezone_name='Africa/Tunis',frames=frames,seconds=60)
        self.addCleanup(v.close);return v
    def publisher(self,rt,v,ns):
        return V2Publisher(rt.client,Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='engine'),self.codec,v.engine)
    def finish(self,rt,p):
        eventually(lambda:rt.client.xlen(p.journal.key('db1.delivery.v1'))==len(p.plans))
        if isinstance(rt,PersistentRuntime):
            def complete():
                try:rt.declare_complete();return True
                except Blocked as e:
                    if e.code!='RECOVERY_PENDING':raise
                    return False
            eventually(complete)

    def test_lost_execution_publication_reply_status_and_no_reinference(self):
        from redis.exceptions import TimeoutError
        with tempfile.TemporaryDirectory() as out:
            ns='lost'+uuid4().hex[:20];v=self.make_video(ns)
            with PrivateRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,delivery_version='v2') as rt:
                p=self.publisher(rt,v,ns);actual=rt.client
                class Lost:
                    failed=False
                    def eval(proxy,*args):
                        result=actual.eval(*args)
                        if not proxy.failed and args[2].endswith('engine.executions.v2'):
                            proxy.failed=True;raise TimeoutError('injected lost execution publication reply')
                        return result
                p.writer.client=Lost()
                status=drive_bounded(v.source,v.engine,p,settlement_seconds=15)
                self.finish(rt,p)
                self.assertEqual(status['frames'],dict(decoded=3,admitted=3,source_opens=1))
                self.assertEqual(status['recognition']['requests'],2)
                self.assertEqual(v.engine.audit.calls.get('whole_ocr'),1);self.assertEqual(v.engine.audit.calls.get('marker'),1)
                self.assertEqual(status['errors']['REDIS_RETRY'],1)
                self.assertEqual(rt.client.xlen(p.journal.key('engine.executions.v2')),2)
                self.assertEqual(status['recognition']['native_starts'],status['recognition']['known_retirements'])
                text=json.dumps(status,ensure_ascii=False)
                for forbidden in ['raw_text','plate_text','credentials','dsn','pixels','123 تونس']:self.assertNotIn(forbidden,text)
                self.assertEqual(status['database']['new'],0);self.assertFalse(status['database']['native_validated'])
            self.assertFalse(rt.cleanup['live_pids']);self.assertTrue(rt.cleanup['temporary_directory_removed'])

    def test_live_runner_survives_broker_restart_same_session(self):
        with tempfile.TemporaryDirectory() as out:
            ns='live'+uuid4().hex[:20];v=self.make_video(ns,1)
            with PersistentRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE) as rt:
                p=self.publisher(rt,v,ns);original=p.session;restarted=[]
                def restart():
                    if p.plans and p.plans[1].session.accepted and not restarted:
                        restarted.append(True);rt.stop_server(abrupt=True);rt.start_server()
                status=drive_bounded(v.source,v.engine,p,settlement_seconds=20,runtime_check=rt.check,after_turn=restart)
                self.finish(rt,p)
                self.assertEqual(restarted,[True]);self.assertEqual(p.session,original)
                self.assertEqual(v.engine.audit.calls.get('whole_ocr'),1);self.assertEqual(v.engine.audit.calls.get('marker'),1)
                self.assertEqual(status['lifetimes']['finalized'],2);self.assertEqual(status['resources']['retained_crops'],0)
            self.assertTrue(rt.cleanup['data_directory_removed'])

    def test_controlled_stop_and_eof_are_distinct_with_original_receipts(self):
        for mode in ('stop_before_frame','stop_after_frame','eof'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as out:
                ns='stop'+uuid4().hex[:20];v=self.make_video(ns,3)
                if mode=='eof':v.capture.count=1
                with PrivateRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,delivery_version='v2') as rt:
                    p=self.publisher(rt,v,ns)
                    stop=lambda:mode=='stop_before_frame' or (mode=='stop_after_frame' and v.source.decoded>=1)
                    status=drive_bounded(v.source,v.engine,p,settlement_seconds=15,stop_requested=stop)
                    self.finish(rt,p)
                    self.assertEqual(status['stop_reason'],'eof' if mode=='eof' else 'controlled_stop')
                    self.assertEqual(status['frames']['decoded'],0 if mode=='stop_before_frame' else 1)
                    if p.plans:
                        for plan in p.plans.values():
                            life=plan.original.owner.lifetime
                            self.assertEqual(life.first_receipt,life.last_receipt)
                            self.assertEqual(plan.transcript.candidates[0].receipt,life.first_receipt) if plan.transcript.candidates else None

    def test_namespace_is_not_reset_and_new_run_has_new_identity(self):
        with tempfile.TemporaryDirectory() as out:
            ns='occupied'+uuid4().hex[:20];v=self.make_video('first')
            with PrivateRuntime(self.binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,delivery_version='v2') as rt:
                j=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='engine')
                rt.client.xadd(j.key('engine.crops.v2'),{'json':b'explicit occupied fixture'})
                before=rt.client.xrange(j.key('engine.crops.v2'))
                with self.assertRaises(Blocked):V2Publisher(rt.client,j,self.codec,v.engine)
                self.assertEqual(rt.client.xrange(j.key('engine.crops.v2')),before)
                self.assertEqual(v.engine.audit.calls,{})
                v2=self.make_video('explicit-new-run')
                fresh=ns+'new';p=self.publisher(rt,v2,fresh)
                self.assertEqual(p.trusted_context.run_id,'explicit-new-run')
                self.assertEqual(v.source.opens,0);self.assertEqual(v2.source.opens,0)

    def test_cli_reports_ephemeral_complete_flow_and_early_db_block(self):
        for db in ('disabled','isolated-fixture'):
            if db=='isolated-fixture' and os.environ.get('DB1_P4_ADMIN_DSN'):continue
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'report';ns='cli'+uuid4().hex[:20]
                cmd=[sys.executable,'scripts/vehicle_bounded_run.py','--mode','injected','--transport','plate-requests-v2',
                    '--redis-profile','ephemeral','--database',db,'--redis-server',self.binary,'--output',str(out),
                    '--org',str(ORG),'--identity-namespace',str(IDENTITY_NAMESPACE),'--run',ns,'--camera','owned','--namespace',ns,'--timezone','Africa/Tunis','--frames','2']
                run=subprocess.run(cmd,capture_output=True,timeout=40)
                report=json.loads((out/'report.json').read_text())
                self.assertEqual(run.returncode,0 if db=='disabled' else 2,(run.stdout,run.stderr,report))
                self.assertEqual(report['outcome'],'passed' if db=='disabled' else 'blocked')
                if db=='disabled':self.assertEqual(len(report['parity']),2)
                else:self.assertNotIn('cleanup',report) # no Redis or capture was opened


class EngineBoundaryTests(unittest.TestCase):
    def test_returned_unknown_native_work_is_not_retired_or_recovered(self):
        from dataclasses import replace
        from vehicle.common.tests.test_plate_requests import session_fixture
        from vehicle.common.plate_requests.policy import reduce_event
        from vehicle.common.engine_plate import OutcomeUnknown
        codec=load_request_codec(ORG,IDENTITY_NAMESPACE);s,crop,*_=session_fixture(codec)
        contexts=[];original=s.adapter._executors['whole']
        def unknown(request,context):contexts.append(context);context.started(original.model);raise OutcomeUnknown()
        s.adapter._executors['whole']=replace(original,invoke=unknown)
        try:
            _,outputs=reduce_event(codec,codec.decode(s.raw),None);request=codec.decode(outputs[0][1])
            s.admission(request);s.allow_execution(request.body['request_id']);s.step()
            self.assertTrue(s.adapter._active.returned.wait(1))
            self.assertFalse(s.adapter._active.context.retirement.is_set())
            self.assertEqual(s.collect(),[]);self.assertFalse(crop.released)
            self.assertFalse(s.adapter.snapshot().admission_complete)
            self.assertEqual(s.adapter.snapshot().settlement,'open')
            s.adapter.stop();self.assertFalse(crop.released)
        finally:
            for context in contexts:context.retired() # test-owned fake has no actual native background use
            s.adapter.poll();s.adapter.stop();s.adapter.cleanup(discard=True)
