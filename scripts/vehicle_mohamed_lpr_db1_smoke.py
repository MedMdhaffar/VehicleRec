"""Owned test-only Phase 5 host: CUDA producer and separate PG16 DB1 consumer."""
import argparse
import base64
import csv
from dataclasses import asdict
from hashlib import sha256
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def stage_redis(directory):
    """Only the exact tested local client, RECORD-verified; never download."""
    dist=importlib.metadata.distribution('redis')
    if dist.version!='7.4.0':raise RuntimeError('missing_verified_local_redis_7_4_0')
    stage=directory/'python';stage.mkdir()
    manifest={}
    record=dist.read_text('RECORD')
    for relative,checksum,size in csv.reader(io.StringIO(record)):
        path=Path(relative)
        assert path.parts[0] in {'redis','redis-7.4.0.dist-info'} and '..' not in path.parts
        if path.suffix=='.pyc':continue
        data=Path(dist.locate_file(relative)).read_bytes()
        if checksum:
            method,expected=checksum.split('=',1)
            assert method=='sha256' and base64.urlsafe_b64encode(sha256(data).digest()).rstrip(b'=').decode()==expected
            assert len(data)==int(size)
        else:assert relative=='redis-7.4.0.dist-info/RECORD'
        target=stage/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        manifest[relative]=sha256(data).hexdigest()
    return stage,dict(version=dist.version,record_sha256=sha256(record.encode()).hexdigest(),files=manifest)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--admin-dsn-file',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--cuda-python',required=True,type=Path)
    p.add_argument('--source-root',required=True,type=Path)
    p.add_argument('--ocr-cache',required=True,type=Path)
    p.add_argument('--video',required=True,type=Path)
    p.add_argument('--redis-server',required=True,type=Path)
    a=p.parse_args(argv)
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    report=dict(status='BLOCKED',db1_records=0,model_comparison_calls=0)
    started=time.monotonic();child=None;client=None;db=None
    runtime=tempfile.TemporaryDirectory(prefix='mohamed-phase5-',dir=str(a.output.parent))
    private=Path(runtime.name);os.chmod(private,0o700)
    try:
        # Private configuration is read only by the DB host, not the CUDA child.
        import psycopg
        from vehicle.common.real_video.test_database import fixture_module
        env=dict(os.environ,DB1_P4_EXISTING_SERVER='1',DB1_P4_ADMIN_DSN=a.admin_dsn_file.read_text().strip())
        env.pop('DB1_P4_ISOLATED',None)
        params=fixture_module().provisioning_params(env)
        if params.get('host')!='127.0.0.1' or params.get('port')!='5433':raise RuntimeError('wrong_authorized_pg_target')
        with psycopg.connect(**params,autocommit=True) as c:
            if not 160000<=c.info.server_version<170000:raise RuntimeError('postgresql_16_required')
            with c.cursor() as cur:
                cur.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
                if cur.fetchone()!=(True,):raise RuntimeError('pg_fixture_requires_provisioning_superuser')
            report['postgres_version']=c.info.server_version
        stage,manifest=stage_redis(private)
        (a.output/'temporary-redis-package.json').write_text(json.dumps(manifest,indent=2)+'\n')
        from uuid import uuid4
        from vehicle.common.redis_scalar.synthetic import IDENTITY_NAMESPACE
        run_id='mohamed-phase5-'+uuid4().hex
        output=a.output/run_id
        command=[str(a.cuda_python),str(ROOT/'scripts/vehicle_mohamed_lpr_db1_child.py'),
            '--control-dir',str(private),'--identity-namespace',str(IDENTITY_NAMESPACE),
            '--enable-mohamed-lpr-v3','--source-root',str(a.source_root),'--ocr-cache',str(a.ocr_cache),
            '--video',str(a.video),'--output',str(output),'--redis-server',str(a.redis_server),
            '--tunis-mode','legacy_whole','--max-frames','4','--max-crops','8','--max-seconds','240']
        child_env=dict(os.environ,PYTHONPATH=str(stage),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PIP_NO_INDEX='1')
        for key in ('DB1_P4_ADMIN_DSN','DB1_P4_EXISTING_SERVER','DB1_P4_ISOLATED'):
            child_env.pop(key,None)
        (a.output/'cuda-command.json').write_text(json.dumps(dict(argv=command,environment={k:child_env[k] for k in
            ('PYTHONPATH','PYTHONNOUSERSITE','PYTHONDONTWRITEBYTECODE','PIP_NO_INDEX')}),indent=2)+'\n')
        log=(a.output/'cuda-private.log').open('w');os.chmod(log.name,0o600)
        child=subprocess.Popen(command,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+270
        while not (private/'ready.json').exists():
            if child.poll() is not None:raise RuntimeError('cuda_child_blocked_or_failed_before_delivery')
            if time.monotonic()>=deadline:raise RuntimeError('cuda_admission_deadline')
            time.sleep(.025)
        control=json.loads((private/'ready.json').read_text())
        import redis
        from vehicle.common.mohamed_lpr_stream_codec import Scope
        from vehicle.common.mohamed_lpr_delivery import TunisDeliveryCodec,OCRBinding
        from vehicle.common.observation_evidence import RecognitionIdentity
        from vehicle.common.redis_scalar.worker import load_codec
        from vehicle.common.mohamed_lpr_delivery_stream import DeliveryJournal,SOURCE
        from vehicle.common.mohamed_lpr_test_database import OwnedDeliveryDatabase
        from vehicle.common.db1_consumer import RedisDB1Consumer
        scope=Scope(**control['scope'])
        assert scope.run_id==run_id
        codec=TunisDeliveryCodec(base=load_codec(scope.org_id,control['identity_namespace']),scope=scope,
            bindings=tuple(OCRBinding(b['model_ref'],RecognitionIdentity(**b['identity'])) for b in control['bindings']))
        client=redis.Redis(unix_socket_path=control['redis_socket'],socket_timeout=2)
        journal=DeliveryJournal(client,namespace=control['namespace'],codec=codec,consumer='db1-owned-host')
        rows=client.xrange(journal.key('db1.delivery.v1'))
        assert len(rows)==1
        raw=rows[0][1][b'json'];message=codec.decode(raw)
        source_result=codec.source.read(message.body['plate']['sealed_json'].encode())
        media=dict(control['source_media'],bytes=a.video.stat().st_size,ts=message.life.context.started_at,
            bucket='owned-phase5-fixture',object_key='clips/'+control['source_media']['sha256']+'.mp4')
        # Explicit fixture-only source registration; no object upload/existence claim.
        os.environ.update(DB1_P4_EXISTING_SERVER='1',DB1_P4_ADMIN_DSN=env['DB1_P4_ADMIN_DSN'])
        os.environ.pop('DB1_P4_ISOLATED',None)
        with OwnedDeliveryDatabase(codec,raw,source_media=media) as db:
            natural=db.consumer();entry=natural.prepare(raw)
            reader=RedisDB1Consumer(client,journal,natural,name='phase5-db1')
            boundary=[]
            def hook(phase):
                if phase=='committed':
                    state=db.inspect(entry)
                    assert state['rows']['fact_vehicle']==state['rows']['fact_plate_read']==1
                    assert state['acceptances']==1 and state['row_receipts']==3
                    assert client.xpending(journal.key('db1.delivery.v1'),reader.group)['pending']==1
                    boundary.append(state)
            natural.hook=hook
            result=reader.step()
            assert result.disposition=='new' and len(boundary)==1 and reader.last_delivery.ack_reply==1
            pending=client.xpending(journal.key('db1.delivery.v1'),reader.group)['pending']
            assert pending==0
            projection_pending=client.xpending(journal.key(SOURCE),journal.group)['pending']
            assert projection_pending==0
            report.update(status='PASS',run_id=run_id,namespace=control['namespace'],db1_records=result.fact_count,
                db1_publications=len(rows),
                before_ack=boundary[0],db1_pending=pending,projection_pending=projection_pending,
                acceptance_digest=result.acceptance_sha256,natural_delivery_sha256=sha256(raw).hexdigest(),
                source_decision_sha256=codec.source.read(message.body['plate']['sealed_json'].encode())['body']['decision_sha256'],
                source_result_event_id=source_result['event_id'],source_frame_id=source_result['body']['crop']['frame_id'],
                source_crop_id=source_result['body']['crop']['crop_id'],
                db1_ack_reply=reader.last_delivery.ack_reply,fixture_settings=db.settings)
        report['database_cleanup']=db.cleanup
        (private/'done.json').write_text(json.dumps(report))
        code=child.wait(timeout=30);log.close()
        native=json.loads((output/'summary.json').read_text())
        assert code==0 and native['status']=='passed' and native['owned_redis_cleaned']
        report.update(cuda_exit=code,cuda_summary=str(output/'summary.json'),model_calls=native['model_calls'],
            vehicle_plate_calls=native['vehicle_plate_calls'],pending=native['pending'],crop_cleanup=native['crop_cleanup'],
            unretired_operations=native['unretired_operations'],parity=native['parity'],frames=native['frames'],
            decoder_opens=native['decoder_opens'],owned_redis_cleaned=native['owned_redis_cleaned'])
    except Exception as error:
        report.update(status='FAIL' if child is not None else 'BLOCKED',failure_type=type(error).__name__,
                      reason='owned_phase5_validation_failed_details_suppressed')
        if isinstance(error,RuntimeError) and __import__('re').fullmatch('[a-z0-9_]{1,96}',str(error)):
            report['reason']=str(error)
        if isinstance(error,ModuleNotFoundError):report['reason']='missing_python_module_'+str(error.name)
        if 'psycopg' in locals() and isinstance(error,psycopg.OperationalError):
            report['reason']='authorized_pg16_connection_unavailable'
        if child is not None and (output/'summary.json').is_file():
            failure=json.loads((output/'summary.json').read_text())
            report['cuda_failure_code']=failure.get('failure_code')
            if failure.get('status')=='blocked':report['status']='BLOCKED'
        if child is not None and child.poll() is None:
            (private/'done.json').write_text(json.dumps(dict(status='FAIL')))
            try:child.wait(timeout=130)
            except subprocess.TimeoutExpired:
                report['native_process_retained']=True
    finally:
        if 'log' in locals() and not log.closed:log.close()
        if client is not None:client.close()
        if child is None or child.poll() is not None:
            runtime.cleanup();report['temporary_package_and_control_removed']=not private.exists()
        else:
            # Do not destroy an active Engine's crop/package environment.
            runtime._finalizer.detach()
        report['elapsed_seconds']=time.monotonic()-started
        (a.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('status','db1_records','frames','model_calls','db1_pending','unretired_operations') if k in report}))
    return 0 if report['status']=='PASS' else 2 if report['status']=='BLOCKED' else 1


if __name__=='__main__':raise SystemExit(main())
