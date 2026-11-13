"""Opt-in synthetic MP4 coordination runner with owned Redis and optional PG16.

For real-model MP4 use the existing explicit-asset smoke CLI; see operator guide.
"""
import argparse,json,os,signal,sys,time,unittest
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['injected'],required=True)
    p.add_argument('--transport',choices=['plate-requests-v2'],required=True)
    p.add_argument('--redis-profile',choices=['ephemeral','aof-always'],required=True)
    p.add_argument('--redis-server',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--database',choices=['disabled','isolated-fixture'],required=True)
    p.add_argument('--org',type=UUID,required=True);p.add_argument('--identity-namespace',type=UUID,required=True)
    for name in ['run','camera','namespace']:p.add_argument('--'+name,required=True)
    p.add_argument('--timezone',required=True);p.add_argument('--frames',type=int,default=4)
    p.add_argument('--seconds',type=float,default=90);p.add_argument('--settlement-seconds',type=float,default=30)
    a=p.parse_args(argv);a.output.mkdir(parents=True,exist_ok=False)
    from vehicle.common.db1_rows.core import require,Blocked
    from vehicle.common.real_video.test_database import require_test_target
    from vehicle.common.real_video.injected import InjectedVideo
    from vehicle.common.real_video.persistent import PersistentRuntime
    from vehicle.common.real_video.runtime import PrivateRuntime
    from vehicle.common.real_video.bounded import drive_bounded
    from vehicle.common.redis_scalar.worker import load_request_codec
    from vehicle.common.redis_scalar.journal import Journal
    from vehicle.common.plate_requests.host import V2Publisher
    from contracts.db1.reference import canonical_json_bytes
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    report=dict(schema_version='waycon-bounded-report-v1',org_id=str(a.org),run_id=a.run,camera_id=a.camera,
        mode='injected',requested_device='cpu',actual_device='cpu (injected executors)',capabilities=['whole_ocr','marker','left_ocr','right_ocr'],
        manufacturer='unsupported',provider_calls=0,reference_model_calls=0,database=dict(enabled=a.database!='disabled',new=0,identical=0,conflicts=0,unknown_commits=0,ack_callbacks_completed=0,native_validated=False))
    rt=video=database=None
    def save():
        path=a.output/'report.json';tmp=path.with_suffix('.pending');tmp.write_bytes(canonical_json_bytes(report)+b'\n');tmp.replace(path)
    try:
        require(1<=a.frames<=8 and 0<a.seconds<=300 and 0<a.settlement_seconds<=120,'INVALID_CAPACITY','bounded run configuration required')
        require(a.redis_server.is_file() and os.access(a.redis_server,os.X_OK),'REDIS_BINARY','existing native Redis binary required')
        import re
        require(all(re.fullmatch('[A-Za-z0-9_-]{1,48}',v) for v in (a.namespace,a.run,a.camera)),'INVALID_IDENTITY','bounded simple run/camera/namespace required')
        if a.database!='disabled':require_test_target() # before capture, inference or Redis startup
        video=InjectedVideo(org=a.org,run=a.run,camera=a.camera,timezone_name=a.timezone,frames=a.frames,seconds=a.seconds)
        with ExitStack() as stack:
            if a.database!='disabled':
                from vehicle.common.real_video.test_database import OwnedDatabase
                database=stack.enter_context(OwnedDatabase(video));report['database_preflight']=database.settings
            rt=stack.enter_context(PersistentRuntime(a.redis_server,a.output,namespace=a.namespace,org=a.org,identity_namespace=a.identity_namespace,max_seconds=a.seconds)
                if a.redis_profile=='aof-always' else PrivateRuntime(a.redis_server,a.output,namespace=a.namespace,org=a.org,identity_namespace=a.identity_namespace,delivery_version='v2'))
            codec=load_request_codec(a.org,a.identity_namespace)
            journal=Journal(None,namespace=a.namespace,trusted_org=a.org,role='plate_requests',consumer='bounded-engine')
            publisher=V2Publisher(rt.client,journal,codec,video.engine)
            report.update(redis_profile=a.redis_profile,redis_settings=rt.settings,session=publisher.session,namespace=a.namespace,
                budgets=dict(frames=a.frames,seconds=a.seconds,settlement_seconds=a.settlement_seconds,native_api_calls=80,crop_bytes=32*1024*1024,scalar_bytes=8*1024*1024))
            def status(value):report['status']=value;save()
            report['status']=drive_bounded(video.source,video.engine,publisher,settlement_seconds=a.settlement_seconds,
                stop_requested=lambda:stopped,on_progress=status,runtime_check=rt.check if isinstance(rt,PersistentRuntime) else lambda:None)
            deadline=time.monotonic()+a.settlement_seconds
            while rt.client.xlen(journal.key('db1.delivery.v1'))<len(publisher.plans):
                require(time.monotonic()<deadline,'DELIVERY_WAITING','bounded final delivery wait');time.sleep(.025)
            rows=rt.client.xrange(journal.key('db1.delivery.v1'));report['parity']=[]
            require(len(rows)==len(publisher.plans),'DELIVERY_CONFLICT','one final per lifetime required')
            for _,fields in rows:
                m=codec.v1.decode(fields[b'json']);expected=publisher.plans[m.life.service_track_id].reference
                require(canonical_json_bytes(m.body['bundle'])==expected.payload.canonical,'PARITY_CONFLICT','complete captured evidence differs')
                report['parity'].append(dict(service_track_id=m.life.service_track_id,bundle_sha256=expected.digest,canonical_bytes_equal=True))
            if database is not None:
                from vehicle.common.db1_consumer import RedisDB1Consumer
                from vehicle.common.real_video.db_delivery import deliver_targets
                consumer=database.consumer(codec,video)
                db=RedisDB1Consumer(rt.client,journal,consumer,name='bounded-normal-role')
                def database_status(value):
                    report['database'].update(value)
                    report['status']['database']=dict(report['database'])
                    save()
                deliver_targets(db,rows,deadline=deadline,on_status=database_status,
                    runtime_check=rt.check if isinstance(rt,PersistentRuntime) else lambda:None)
                report['database'].update(native_validated=True,acceptance_receipts=database.receipts())
            from vehicle.common.real_video.status import run_status
            report['status']=run_status(video.source,video.engine,publisher,admitted_frames=report['status']['frames']['admitted'],database=report['database'])
            report['status']['outcome']='settled';report['outcome']='passed'
            if isinstance(rt,PersistentRuntime):
                while True:
                    try:rt.declare_complete();break
                    except Blocked as error:
                        if error.code!='RECOVERY_PENDING':raise
                        require(time.monotonic()<deadline,'METADATA_WAITING','bounded metadata drain wait');time.sleep(.025)
    except unittest.SkipTest:
        report.update(outcome='blocked',code='ISOLATED_POSTGRES_NOT_CONFIGURED')
    except Exception as error:report.update(outcome='failed',code=getattr(error,'code','HOST_FAILURE'),error_type=type(error).__name__)
    finally:
        if video is not None:video.close()
        if rt is not None:report['cleanup']=rt.cleanup
        if database is not None:report['database_cleanup']=database.cleanup
        save()
    print(json.dumps(dict(outcome=report['outcome'],code=report.get('code'),report=str(a.output/'report.json'))))
    return 0 if report['outcome']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
