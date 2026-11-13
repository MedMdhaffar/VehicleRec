"""Execute ordered review/restart/compatibility checks with actual command evidence."""
import argparse,json,os,re,subprocess,sys,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--redis-server',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','VEHICLE_REDIS_SERVER':str(a.redis_server.resolve())}
    pure=['test_foundations','test_db1_rows','test_db1_adapter','test_db1_outbox','test_coordinator_cleanup','test_colour_aggregator',
          'test_plate_metadata','test_engine_plate','test_plate_local_executors','test_real_video','test_mp4_colour_plate','test_mp4_publication_order',
          'test_redis_scalar','test_redis_plate','test_redis_leases','test_plate_requests','test_db1_consumer','test_review_handshake']
    native=['test_db1_runner_ack','test_review_handshake_redis','test_redis_persistence','test_bounded_operations','test_plate_requests_redis',
            'test_mp4_colour_plate_redis_integration','test_engine_plate_redis_integration','test_redis_plate_integration','test_redis_scalar_integration']
    def names(values):return ['vehicle.common.tests.'+v for v in values]
    jobs=[('gates12',[sys.executable,'-m','unittest',*names(['test_review_handshake','test_review_handshake_redis'])]),
          ('contracts',[sys.executable,'contracts/validate_examples.py']),('p1',[sys.executable,'contracts/db1/validate_fixtures.py']),
          ('pure',[sys.executable,'-m','unittest',*names(pure)]),('native',[sys.executable,'-m','unittest',*names(native)]),
          ('postgres-gate',[sys.executable,'-m','unittest','discover','-s','tests/db1_integration'])]
    all_ids=[]
    def ids(suite):
        for value in suite:
            if isinstance(value,unittest.TestSuite):yield from ids(value)
            else:yield value.id()
    for modules in (pure,native):
        for name in names(modules):all_ids.extend(ids(unittest.defaultTestLoader.loadTestsFromName(name)))
    assert len(all_ids)==len(set(all_ids)),'overlap inside main pure/native suites'
    (a.output/'test-methods.json').write_text(json.dumps(dict(unique_methods=len(all_ids),ids=all_ids,
        note='gates12 focused checks overlap the main suites; ten persistence scenarios are subcases of one method'),indent=2)+'\n')
    results=[]
    for name,command in jobs:
        begin=time.monotonic()
        with (a.output/(name+'.log')).open('wb') as f:run=subprocess.run(command,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
        log=(a.output/(name+'.log')).read_text(errors='replace');match=re.search(r'Ran (\d+) tests? in ([\d.]+)s',log)
        result=dict(name=name,command=command,returncode=run.returncode,seconds=time.monotonic()-begin,
            unittest_methods=int(match[1]) if match else None,skips='skipped=' in log)
        results.append(result);(a.output/'commands.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(result),flush=True)
        if name=='gates12' and run.returncode:return 1
    pg=bool(os.environ.get('DB1_P4_ADMIN_DSN')) and (os.environ.get('DB1_P4_ISOLATED')=='1' or os.environ.get('DB1_P4_EXISTING_SERVER')=='1')
    summary=dict(commands=results,unique_main_methods=len(all_ids),postgres='inspect actual native log' if pg else 'BLOCKED: DB1_P4_ADMIN_DSN and exactly one explicit isolation/provisioning mode are absent')
    (a.output/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    return 0 if all(r['returncode']==0 and (not r['skips'] or r['name']=='postgres-gate') for r in results) else 1
if __name__=='__main__':raise SystemExit(main())
