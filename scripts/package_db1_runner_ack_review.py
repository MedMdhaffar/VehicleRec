"""Database ACK follow-up: exact patch, baseline reproduction, replay and validation ZIP."""
import argparse,difflib,hashlib,json,os,subprocess,sys,tempfile,time,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
EXPECTED='c7304fe406bc88b7377bf3a0dfb9b80cba447b62679990a8d9acc752545f00a6'
NEW=['vehicle/common/real_video/db_delivery.py','vehicle/common/tests/test_db1_runner_ack.py',
 'docs/architecture/vehicle_db1_runner_ack_results.md','scripts/package_db1_runner_ack_review.py']

def sha(b):return hashlib.sha256(b).hexdigest()
def encoded(v):return (json.dumps(v,indent=2,sort_keys=True)+'\n').encode()
def safe(n):
 p=PurePosixPath(n)
 assert n and not p.is_absolute() and '..' not in p.parts and '\\' not in n and str(p)==n,n
 return n

def fingerprint(files):return sha(encoded({n:sha(b) for n,b in sorted(files.items())}))

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['baseline','evidence','output','redis-server']:p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();a.evidence=a.evidence.resolve();assert not a.output.exists(),'refuse replacing review archive'
 assert sha(a.baseline.read_bytes())==EXPECTED
 with zipfile.ZipFile(a.baseline) as z:
  assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
  for n in z.namelist():safe(n)
  manifest_bytes=z.read('_review/manifest.json');manifest=json.loads(manifest_bytes)
  assert len(manifest['source_files'])==262
  baseline={f['path']:z.read(f['path']) for f in manifest['source_files']}
  for f in manifest['source_files']:assert sha(baseline[f['path']])==f['sha256']
 starting=json.loads((a.evidence/'starting-state.json').read_bytes())
 assert starting['differences']==[] and starting['sha256']==EXPECTED and starting['source_files']==262
 files={n:(ROOT/n).read_bytes() for n in sorted(set(baseline)|set(NEW))}
 patch=[];sources=[]
 for n,b in files.items():
  safe(n);old=baseline.get(n);changed=old!=b
  sources.append(dict(path=n,bytes=len(b),sha256=sha(b),baseline_sha256=sha(old) if old is not None else None,
   changed=changed,origin='known-reviewed-baseline' if old is not None else 'new-task-work'))
  if not changed:continue
  patch.append(f'diff --git a/{n} b/{n}\n')
  if old is None:patch.append('new file mode 100644\n')
  patch.extend(difflib.unified_diff((old or b'').decode().splitlines(True),b.decode().splitlines(True),
   fromfile='a/'+n if old is not None else '/dev/null',tofile='b/'+n))
 patch=''.join(patch).encode();evidence={};results=[];begin=time.monotonic()
 with tempfile.TemporaryDirectory(prefix='waycon-db-ack-replay-') as td:
  snapshot=Path(td)
  for n,b in baseline.items():
   target=snapshot/safe(n);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
  patch_path=snapshot/'scoped.patch';patch_path.write_bytes(patch)
  env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','VEHICLE_REDIS_SERVER':str(a.redis_server.resolve())}
  def run(name,command,expected=0):
   t=time.monotonic();r=subprocess.run(command,cwd=snapshot,env=env,capture_output=True)
   evidence[name+'.log']=r.stdout+r.stderr
   results.append(dict(name=name,command=command,returncode=r.returncode,expected_returncode=expected,seconds=time.monotonic()-t))
   if r.returncode!=expected:raise RuntimeError(name+' failed: '+(r.stdout+r.stderr).decode()[-6000:])
   if name.startswith('extracted-') and b'skipped=' in r.stderr:raise RuntimeError(name+' skipped')
  # The standalone regression has no imports from the new implementation and
  # proves the old runner's failure against the exact unpatched baseline.
  prefix=files['vehicle/common/tests/test_db1_runner_ack.py'].split(
   b'\n\nfrom vehicle.common.tests import test_redis_scalar_integration',1)[0]
  reproducer=prefix+b'\n\nif __name__=="__main__":unittest.main()\n'
  evidence['baseline-reproducer.py']=reproducer
  reproduced=snapshot/'baseline_reproducer.py';reproduced.write_bytes(reproducer)
  run('baseline-red',[sys.executable,str(reproduced)],expected=1)
  assert b'DB_WAITING' in evidence['baseline-red.log'] and b'Ran 1 test' in evidence['baseline-red.log']
  reproduced.unlink()
  run('patch-check',['git','apply','--check',str(patch_path)])
  run('patch-apply',['git','apply',str(patch_path)])
  for n,b in files.items():assert (snapshot/n).read_bytes()==b,n
  run('extracted-pure',[sys.executable,'-m','unittest',*['vehicle.common.tests.'+v for v in [
   'test_review_handshake','test_plate_requests','test_db1_consumer','test_db1_adapter','test_engine_plate','test_mp4_publication_order']]])
  run('extracted-native',[sys.executable,'-m','unittest',*['vehicle.common.tests.'+v for v in [
   'test_db1_runner_ack','test_review_handshake_redis','test_redis_persistence','test_bounded_operations','test_plate_requests_redis']]])
  run('extracted-contracts',[sys.executable,'contracts/validate_examples.py'])
  run('extracted-p1',[sys.executable,'contracts/db1/validate_fixtures.py'])
  for n,b in files.items():assert (snapshot/n).read_bytes()==b,n
 assert not snapshot.exists()
 evidence['replay.json']=encoded(dict(baseline_sha256=EXPECTED,baseline_source_fingerprint=fingerprint(baseline),
  final_source_fingerprint=fingerprint(files),patch_sha256=sha(patch),source_files_compared=len(files),
  commands=results,temporary_extraction_removed=True,seconds=time.monotonic()-begin))
 allow=['starting-state.json','starting-git-status.txt','red.log','green-initial.log','expanded-initial.log',
  'commands.json','cleanup.json','accepted-checks.log','startup-window-failure.log','current-ack.log']
 allow += ['accepted-checks/'+n for n in ['commands.json','result.json','test-methods.json','gates12.log','contracts.log',
  'p1.log','pure.log','native.log','postgres-gate.log']]
 for n in allow:
  path=a.evidence/safe(n);assert path.is_file(),n
  evidence['evidence/'+n]=path.read_bytes()
 # No recursive evidence collection: broker directories, raw scalar payloads,
 # crops, weights, media, credentials and database files are never candidates.
 evidence['scoped.patch']=patch
 with zipfile.ZipFile(ROOT/'db1_contract.zip') as z:
  evidence['supervisor-sql-hashes.json']=encoded({n:sha(z.read(n)) for n in z.namelist() if n.endswith('.sql')})
 evidence['README.md']=b'''# DB1 runner completion after an ambiguous Redis ACK

Baseline: vehicle_restart_commit_ack_review.zip, SHA256
c7304fe406bc88b7377bf3a0dfb9b80cba447b62679990a8d9acc752545f00a6.
All 262 manifested baseline sources matched the initial workspace. Git HEAD is
not the baseline. No later pre-existing repair was present. Preserve unrelated
working-tree changes. Extract baseline sources and apply scoped.patch with
 git apply --check scoped.patch
 git apply scoped.patch
All root source files form the complete final 266-file dependency snapshot.

baseline-reproducer.py can be copied into an unpatched baseline root and run with
the existing Python and VEHICLE_REDIS_SERVER set to the installed native binary.
Its expected failure is DB_WAITING after both commits and both Redis XACKs. The
packager executed it before patching. Intentional red logs are not passing tests.

See docs/architecture/vehicle_db1_runner_ack_results.md. Native PostgreSQL remains
BLOCKED without an authorized disposable target. These ACK ambiguity tests use
injected database outcomes and real owned Redis; they do not prove native PG.
No real model inference was repeated. Reviewed Plate completion, tenant and AOF
fixes, canonical DB1 policy/adapter, supervisor SQL and historical fixtures remain
unchanged. No production/Engine-crash recovery or power-loss guarantee is claimed.
No credentials, media, models, crops, Redis or database data are included.
'''
 final_manifest=dict(baseline_archive=a.baseline.name,baseline_sha256=EXPECTED,baseline_manifest_sha256=sha(manifest_bytes),
  baseline_source_fingerprint=fingerprint(baseline),final_source_fingerprint=fingerprint(files),source_files=sources,
  scoped_paths=[f['path'] for f in sources if f['changed']],pre_existing_repair_changes=[],new_task_paths=[f['path'] for f in sources if f['changed']],
  evidence=[dict(path='_review/'+n,bytes=len(b),sha256=sha(b)) for n,b in sorted(evidence.items())])
 evidence['manifest.json']=encoded(final_manifest)
 a.output.parent.mkdir(parents=True,exist_ok=True);temporary=a.output.with_suffix('.pending.zip')
 with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for n,b in sorted({**files,**{'_review/'+n:b for n,b in evidence.items()}}.items()):z.writestr(safe(n),b)
 with zipfile.ZipFile(temporary) as z:
  assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
  for f in [*sources,*final_manifest['evidence']]:assert sha(z.read(f['path']))==f['sha256'],f['path']
  for n in z.namelist():
   safe(n);assert not set(PurePosixPath(n).parts)&{'__pycache__','.git','.venv','appendonlydir','ultralytics-settings'}
   assert Path(n).suffix.lower() not in {'.pt','.pdiparams','.mp4','.png','.jpg','.sqlite3','.db','.env','.pyc','.aof','.rdb'}
 for n,b in files.items():assert (ROOT/n).read_bytes()==b,'workspace changed during packaging: '+n
 temporary.replace(a.output);checksum=sha(a.output.read_bytes())
 a.output.with_suffix('.zip.sha256').write_text(checksum+'  '+a.output.name+'\n')
 (a.evidence/'packaging-result.json').write_bytes(evidence['replay.json'])
 print(json.dumps(dict(archive=str(a.output),sha256=checksum,source_files=len(files),scoped_files=len(final_manifest['scoped_paths']),
  final_source_fingerprint=final_manifest['final_source_fingerprint'],replay=True,CRC=True,safe_unique_paths=True,temporary_extraction_removed=True)),flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
