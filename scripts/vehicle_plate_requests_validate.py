"""Reproducible affected checks; no installs, models, or implicit DB connections."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--redis-server',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','VEHICLE_REDIS_SERVER':str(a.redis_server.resolve())}
    pure=['test_foundations','test_db1_rows','test_db1_adapter','test_db1_outbox','test_coordinator_cleanup',
        'test_colour_aggregator','test_plate_metadata','test_engine_plate','test_plate_local_executors',
        'test_real_video','test_mp4_colour_plate','test_mp4_publication_order','test_redis_scalar','test_redis_plate','test_redis_leases',
        'test_plate_requests','test_db1_consumer']
    native=['test_plate_requests_redis','test_mp4_colour_plate_redis_integration','test_engine_plate_redis_integration',
            'test_redis_plate_integration','test_redis_scalar_integration']
    jobs=[('contracts',[sys.executable,'contracts/validate_examples.py']),
          ('db1-contracts',[sys.executable,'contracts/db1/validate_fixtures.py']),
          ('pure',[sys.executable,'-m','unittest',*[f'vehicle.common.tests.{n}' for n in pure]]),
          ('native-redis',[sys.executable,'-m','unittest',*[f'vehicle.common.tests.{n}' for n in native]]),
          ('postgres-gate',[sys.executable,'-m','unittest','discover','-s','tests/db1_integration'])]
    evidence=[]
    for name,command in jobs:
        start=time.monotonic()
        with (a.output/(name+'.log')).open('wb') as f:
            run=subprocess.run(command,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
        log=(a.output/(name+'.log')).read_text(errors='replace')
        evidence.append(dict(name=name,command=command,returncode=run.returncode,seconds=time.monotonic()-start,
            skipped='skipped=' in log or 'skipped ' in log))
        (a.output/'commands.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence[-1]),flush=True)
    # PG fixture discovery is explicitly not counted as native validation.
    blocked_pg=not (os.environ.get('DB1_P4_ADMIN_DSN') and (os.environ.get('DB1_P4_ISOLATED')=='1' or os.environ.get('DB1_P4_EXISTING_SERVER')=='1'))
    result=dict(checks=evidence,native_postgres='BLOCKED: disposable target not configured' if blocked_pg else 'inspect postgres-gate log',
        native_redis='passed' if next(v for v in evidence if v['name']=='native-redis')['returncode']==0 and not next(v for v in evidence if v['name']=='native-redis')['skipped'] else 'not passed')
    (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if all(v['returncode']==0 for v in evidence) and result['native_redis']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
