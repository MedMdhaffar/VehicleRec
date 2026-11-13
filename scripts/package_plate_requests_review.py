"""Package exact scoped source/evidence against the reviewed publication baseline.

No model/media capture or inference. Replay uses a private temporary extraction.
"""
import argparse,difflib,hashlib,json,os,subprocess,sys,tempfile,time,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
EXPECTED='449bcda14d9630431bfacc8d8fbccf0c22209ed8287362d0052bb3c8f90f934b'
SUPPLEMENTAL=['db1_contract.zip','tests/db1_integration/README.md','tests/db1_integration/acceptance_proposal.sql',
              'tests/db1_integration/test_fixture_safety.py','tests/db1_integration/test_postgres.py']
NEW=['contracts/vehicle_plate_requests_v2.schema.json','docs/architecture/vehicle_plate_requests_v2.md',
     'docs/architecture/vehicle_plate_requests_results.md','vehicle/common/db1_delivery.py','vehicle/common/db1_consumer.py',
     'vehicle/common/tests/test_plate_requests.py','vehicle/common/tests/test_plate_requests_redis.py',
     'vehicle/common/tests/test_db1_consumer.py','tests/db1_integration/test_natural_consumer.py',
     'scripts/vehicle_plate_requests_integration.py','scripts/vehicle_plate_requests_validate.py',
     'scripts/package_plate_requests_review.py']
NEW += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'vehicle/common/plate_requests').glob('*.py'))]


def sha(raw):return hashlib.sha256(raw).hexdigest()
def safe(path):
    p=PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or '\\' in path:raise ValueError('unsafe archive path')
    return path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--redis-server',type=Path,required=True)
    a=p.parse_args();a.evidence=a.evidence.resolve()
    assert sha(a.baseline.read_bytes())==EXPECTED
    evidence={};start=time.monotonic()
    with zipfile.ZipFile(a.baseline) as z:
        old_manifest=json.loads(z.read('_review/manifest.json'))
        baseline={f['path']:z.read(f['path']) for f in old_manifest['source_files']}
        for f in old_manifest['source_files']:assert sha(baseline[f['path']])==f['sha256']
    files={name:(ROOT/name).read_bytes() for name in sorted(set(baseline)|set(SUPPLEMENTAL)|set(NEW))}
    source=[];patch=[]
    for name,raw in files.items():
        safe(name);old=baseline.get(name)
        origin='reviewed-publication-baseline' if old is not None else 'pre-existing-working-tree-dependency' if name in SUPPLEMENTAL else 'this-milestone'
        changed=old!=raw if old is not None else name not in SUPPLEMENTAL
        source.append(dict(path=name,bytes=len(raw),sha256=sha(raw),origin=origin,
            baseline_sha256=sha(old) if old is not None else None,changed=changed))
        if not changed:continue
        patch.append(f'diff --git a/{name} b/{name}\n')
        if old is None:patch.append('new file mode 100644\n')
        patch.extend(difflib.unified_diff((old or b'').decode().splitlines(True),raw.decode().splitlines(True),
            fromfile='a/'+name if old is not None else '/dev/null',tofile='b/'+name))
    patch=''.join(patch).encode()
    # Supplemental fixtures are unchanged pre-existing dependencies, not newly
    # authored supervisor SQL or platform-approved receipt schema.
    with tempfile.TemporaryDirectory(prefix='plate-v2-review-') as temp:
        snapshot=Path(temp)
        for name,raw in baseline.items():
            path=snapshot/safe(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        for name in SUPPLEMENTAL:
            path=snapshot/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(files[name])
        patch_path=snapshot/'milestone.patch';patch_path.write_bytes(patch)
        for command in (['git','apply','--check',str(patch_path)],['git','apply',str(patch_path)]):
            run=subprocess.run(command,cwd=snapshot,capture_output=True)
            if run.returncode:raise RuntimeError(run.stderr.decode())
        for name,raw in files.items():assert (snapshot/name).read_bytes()==raw,name
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','VEHICLE_REDIS_SERVER':str(a.redis_server.resolve())}
        jobs=[('extracted-pure',[sys.executable,'-m','unittest','vehicle.common.tests.test_plate_requests',
                'vehicle.common.tests.test_db1_consumer','vehicle.common.tests.test_db1_rows','vehicle.common.tests.test_db1_adapter',
                'vehicle.common.tests.test_engine_plate','vehicle.common.tests.test_mp4_publication_order']),
              ('extracted-native',[sys.executable,'scripts/vehicle_plate_requests_integration.py'])]
        results=[]
        for name,cmd in jobs:
            begin=time.monotonic();run=subprocess.run(cmd,cwd=snapshot,env=env,capture_output=True)
            evidence[name+'.log']=run.stdout+run.stderr
            results.append(dict(command=cmd,returncode=run.returncode,seconds=time.monotonic()-begin))
            if run.returncode:raise RuntimeError(name+' failed: '+(run.stdout+run.stderr).decode()[-4000:])
            if b'skipped=' in run.stderr:raise RuntimeError('extracted native/pure check skipped')
        for name,raw in files.items():assert (snapshot/name).read_bytes()==raw,name
    assert not snapshot.exists()
    evidence['replay.json']=(json.dumps(dict(baseline_sha256=EXPECTED,source_files_compared=len(files),patch_sha256=sha(patch),
        commands=results,temporary_extraction_removed=True,seconds=time.monotonic()-start),indent=2)+'\n').encode()
    # Explicit evidence allowlist; runtime settings dirs, raw payloads and media
    # cannot enter the package through recursive artifact collection.
    names=['starting-git-status.txt','starting-head.txt','baseline-verification.json','commands-additional.json',
        'compat-initial.log','request-initial.log','request-compat-fixed.log','native-initial.log','native-loop-fixed.log',
        'db-consumer-initial.log','native-recovery-initial.log','request-expanded.log','new-suite.log',
        'evidence-capacity-red.log','evidence-capacity-green.log','new-final.log','final-domain-db.log','postgres-gate.log',
        'native-regions.log','seal-conflict-red.log','seal-conflict-green.log','cleanup.json','real-01.log','real-01/preflight.json','real-01/report.json','real-01/associations.json',
        'real-01/colour.log','real-01/plate_requests.log','real-01/aggregator_plate.log','real-01/redis.log']
    names += ['full-01/'+name for name in ['commands.json','result.json','contracts.log','db1-contracts.log','pure.log','native-redis.log','postgres-gate.log']]
    for name in names:
        path=a.evidence/name
        if not path.is_file():raise FileNotFoundError('required sanitized validation evidence missing: '+name)
        evidence['evidence/'+safe(name)]=path.read_bytes()
    evidence['scoped.patch']=patch
    evidence['README.md']=b'''# Plate-owned requests v2 and replaceable DB1 consumer\n\nGit HEAD is not the baseline. Verify the supplied publication-order archive SHA256,\nextract only its 223 source files from its manifest, and apply scoped.patch with\ngit apply --check then git apply. The five pre-existing supplemental dependencies\nlisted in manifest.json must also be copied from this package before tests. They\nare unchanged working-tree dependencies, not authored platform SQL or approved\nreceipt deployment. The root source files are the complete final review snapshot.\n\nSee docs/architecture/vehicle_plate_requests_results.md. PostgreSQL native\nvalidation is BLOCKED without a configured disposable target; skips are not passes.\nNo Engine process-crash inference recovery or Redis restart durability is claimed.\nNo models, media, crops, credentials, environments, caches or DB data are included.\n'''
    with zipfile.ZipFile(ROOT/'db1_contract.zip') as z:
        evidence['supervisor-sql-hashes.json']=json.dumps({n:sha(z.read(n)) for n in z.namelist() if n.endswith('.sql')},indent=2).encode()+b'\n'
    manifest=dict(baseline_archive=a.baseline.name,baseline_sha256=EXPECTED,source_files=source,
        scoped_paths=[f['path'] for f in source if f['changed']],pre_existing_dependencies=SUPPLEMENTAL,
        evidence=[dict(path='_review/'+name,bytes=len(raw),sha256=sha(raw)) for name,raw in sorted(evidence.items())])
    evidence['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=a.output.with_suffix('.pending.zip')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,raw in sorted({**files,**{'_review/'+k:v for k,v in evidence.items()}}.items()):z.writestr(safe(name),raw)
    with zipfile.ZipFile(temporary) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(set(z.namelist()))
        for name,raw in files.items():assert z.read(name)==raw
        for entry in manifest['evidence']:assert sha(z.read(entry['path']))==entry['sha256']
        for name in z.namelist():
            safe(name)
            assert not set(PurePosixPath(name).parts)&{'__pycache__','.git','.venv','ultralytics-settings'}
            assert Path(name).suffix.lower() not in {'.pt','.pdiparams','.mp4','.png','.jpg','.sqlite3','.db','.env','.pyc'}
    temporary.replace(a.output)
    checksum=sha(a.output.read_bytes());a.output.with_suffix('.zip.sha256').write_text(checksum+'  '+a.output.name+'\n')
    (a.evidence/'packaging-result.json').write_bytes(evidence['replay.json'])
    print(json.dumps(dict(archive=str(a.output),sha256=checksum,source_files=len(files),scoped_files=len(manifest['scoped_paths']),replay=True,cleanup=True)),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
