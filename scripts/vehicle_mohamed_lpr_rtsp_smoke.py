"""Opt-in one-camera CUDA/Redis smoke with disposable PostgreSQL delivery only."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--enable-rtsp-v3',required=True,action='store_true')
    p.add_argument('--rtsp-uri-file',type=Path,help='Optional private URI-only file; otherwise use a hidden terminal prompt')
    p.add_argument('--camera-id',help='Optional explicit camera identity; otherwise a fresh opaque identity per Start')
    p.add_argument('--camera-timezone',default='Africa/Tunis')
    p.add_argument('--rtsp-transport',choices=('tcp','udp'),default='tcp',help='Explicit transport; no fallback')
    for key in ('admin-dsn-file','cuda-python','source-root','ocr-cache','redis-server','output'):
        p.add_argument('--'+key,required=True,type=Path)
    p.add_argument('--tunis-mode',required=True,choices=('legacy_whole','marker_first'))
    p.add_argument('--max-frames',required=True,type=int,choices=range(1,9))
    p.add_argument('--max-crops',required=True,type=int,choices=range(1,9))
    p.add_argument('--reconnects',required=True,type=int,choices=range(4))
    for key in ('max-seconds','open-seconds','read-seconds'):
        p.add_argument('--'+key,required=True,type=float)
    a=p.parse_args(argv)
    if not 0<a.max_seconds<=300 or not 0<a.open_seconds<=15 or not 0<a.read_seconds<=15:
        p.error('invalid bounded RTSP window')
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    report=dict(status='BLOCKED',frames=0,reconnects=0,db1_records=0,decoder_opens=0)
    runtime=child=log=None
    db1_checks=[]
    old_signals={}
    previous_env={k:os.environ.get(k) for k in ('DB1_P4_ADMIN_DSN','DB1_P4_EXISTING_SERVER','DB1_P4_ISOLATED')}
    try:
        from vehicle.common.real_video.rtsp import uri_file,prompted_uri,stage_camera
        runtime=tempfile.TemporaryDirectory(prefix='waycon-rtsp-private-',dir=str(a.output.parent))
        private=Path(runtime.name);os.chmod(private,0o700)
        camera=stage_camera(uri_file(a.rtsp_uri_file) if a.rtsp_uri_file else prompted_uri(),private,
                            camera_id=a.camera_id,timezone=a.camera_timezone)
        import psycopg
        from vehicle.common.real_video.test_database import fixture_module
        env=dict(os.environ,DB1_P4_EXISTING_SERVER='1',DB1_P4_ADMIN_DSN=a.admin_dsn_file.read_text().strip())
        env.pop('DB1_P4_ISOLATED',None)
        params=fixture_module().provisioning_params(env)
        if params.get('host')!='127.0.0.1' or params.get('port')!='5433':
            raise RuntimeError('wrong_authorized_pg_target')
        with psycopg.connect(**params,autocommit=True) as conn:
            if not 160000<=conn.info.server_version<170000:raise RuntimeError('postgresql_16_required')
            with conn.cursor() as cur:
                cur.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
                if cur.fetchone()!=(True,):raise RuntimeError('pg_fixture_provisioning_privilege_required')
        os.environ.update(DB1_P4_EXISTING_SERVER='1',DB1_P4_ADMIN_DSN=env['DB1_P4_ADMIN_DSN'])
        os.environ.pop('DB1_P4_ISOLATED',None)
        from scripts.vehicle_mohamed_lpr_db1_smoke import stage_redis
        stage,manifest=stage_redis(private)
        (a.output/'temporary-redis-package.json').write_text(json.dumps(manifest,indent=2)+'\n')
        command=[str(a.cuda_python),str(ROOT/'scripts/vehicle_mohamed_lpr_rtsp_child.py'),
                 '--control-dir',str(private),'--camera-config',str(camera.path),'--output',str(a.output/'native')]
        for key in ('source_root','ocr_cache','redis_server','tunis_mode','max_frames',
                    'max_crops','max_seconds','open_seconds','read_seconds','reconnects','rtsp_transport'):
            command+=['--'+key.replace('_','-'),str(getattr(a,key))]
        child_env=dict(os.environ,PYTHONPATH=str(stage),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PIP_NO_INDEX='1')
        for key in previous_env:child_env.pop(key,None)
        (a.output/'command.json').write_text(json.dumps(dict(argv=command,
            environment={k:child_env[k] for k in ('PYTHONPATH','PYTHONNOUSERSITE','PYTHONDONTWRITEBYTECODE','PIP_NO_INDEX')}),indent=2)+'\n')
        log=(a.output/'cuda-private.log').open('w');os.chmod(log.name,0o600)
        child=subprocess.Popen(command,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT)
        def stop(*_):
            if child.poll() is None:child.send_signal(signal.SIGTERM)
        for signum in (signal.SIGINT,signal.SIGTERM):
            old_signals[signum]=signal.signal(signum,stop)
        from vehicle.common.real_video.rtsp_delivery import consume_owned
        deadline=time.monotonic()+a.max_seconds+180  # Initialization + retirement/cleanup allowance.
        seen=set()
        while child.poll() is None:
            for ready in private.glob('ready-*.json'):
                if ready.name in seen:continue
                seen.add(ready.name)
                try:
                    result=consume_owned(json.loads(ready.read_text()))
                except BaseException:
                    result=dict(status='FAIL',reason='owned_pg_validation_failed_no_ack_claim',
                                db1_records=None,commit_or_ack_outcome='unconfirmed')
                db1_checks.append(result)
                done=ready.with_name(ready.name.replace('ready-','done-',1))
                tmp=done.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.replace(done)
            if time.monotonic()>=deadline:
                stop();report['reason']='rtsp_host_deadline'
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:report['native_process_retained']=True
                break
            time.sleep(.025)
        summary=a.output/'native/summary.json'
        if summary.exists():report.update(json.loads(summary.read_text()))
        report['host_db1_checks']=db1_checks
        if any(r['status']!='PASS' for r in db1_checks):
            report.update(status='FAIL',db1_records=None,commit_or_ack_outcome='unconfirmed')
        if child.poll() is None:
            report.update(status='FAIL',native_process_retained=True)
        elif child.returncode not in (0,2):report['status']='FAIL'
    except BaseException as error:
        report.update(status='FAIL' if child else 'BLOCKED',failure_type=type(error).__name__,
                      reason='rtsp_preflight_failed_details_suppressed')
        from vehicle.common.real_video.rtsp import InputFault
        if isinstance(error,InputFault):report['reason']=str(error)
        if 'psycopg' in locals() and isinstance(error,psycopg.OperationalError):
            report['reason']='authorized_pg16_unavailable'
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:report['native_process_retained']=True
    finally:
        for signum,handler in old_signals.items():signal.signal(signum,handler)
        for key,value in previous_env.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        if log:log.close()
        if runtime:
            if child is None or child.poll() is not None:
                runtime.cleanup();report['temporary_package_and_control_removed']=True
            else:
                runtime._finalizer.detach()
        report['ingest_status']='PASS' if report['frames']>0 else 'NOT_RUN'
        report['private_plate_delivery_status']=(
            'UNCONFIRMED' if report['db1_records'] is None else
            'COMMIT_AND_ACK_CONFIRMED' if report['db1_records']>0 else
            'NOT_OBSERVED' if report['frames']>0 else 'NOT_RUN')
        (a.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('status','reason','frames','reconnects','db1_records','decoder_opens',
                                              'private_plate_delivery_status') if k in report}))
    return 0 if report['status']=='PASS' else 2 if report['status']=='BLOCKED' else 1


if __name__=='__main__':
    raise SystemExit(main())
