"""Exact review-baseline patch, replay, extracted tests and sanitized evidence ZIP."""
import argparse,difflib,hashlib,json,os,subprocess,sys,tempfile,time,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
EXPECTED='e2b58a1168248a38aa2af199e327555c6600a124eeeed67dc7c10e559f5542b2'
NEW=['vehicle/common/plate_requests/completion.py','vehicle/common/plate_requests/ownership.py',
 'vehicle/common/real_video/persistent.py','vehicle/common/real_video/bounded.py','vehicle/common/real_video/status.py',
 'vehicle/common/real_video/injected.py','vehicle/common/real_video/test_database.py',
 'vehicle/common/tests/test_review_handshake.py','vehicle/common/tests/test_review_handshake_redis.py',
 'vehicle/common/tests/test_redis_persistence.py','vehicle/common/tests/test_bounded_operations.py',
 'scripts/vehicle_bounded_run.py','scripts/vehicle_restart_validate.py','scripts/package_vehicle_restart_review.py',
 'docs/architecture/vehicle_operator_guide.md','docs/architecture/vehicle_restart_results.md']

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
  assert len(manifest['source_files'])==246
  baseline={f['path']:z.read(f['path']) for f in manifest['source_files']}
  for f in manifest['source_files']:assert sha(baseline[f['path']])==f['sha256']
 starting=json.loads((a.evidence/'starting-state.json').read_bytes())
 assert starting['differences']==[] and starting['archive_sha256']==EXPECTED and starting['source_files']==246
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
 with tempfile.TemporaryDirectory(prefix='waycon-restart-replay-') as td:
  snapshot=Path(td)
  for n,b in baseline.items():
   target=snapshot/safe(n);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
  patch_path=snapshot/'scoped.patch';patch_path.write_bytes(patch)
  env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','VEHICLE_REDIS_SERVER':str(a.redis_server.resolve())}
  def run(name,command):
   t=time.monotonic();r=subprocess.run(command,cwd=snapshot,env=env,capture_output=True)
   evidence[name+'.log']=r.stdout+r.stderr
   results.append(dict(name=name,command=command,returncode=r.returncode,seconds=time.monotonic()-t))
   if r.returncode:raise RuntimeError(name+' failed: '+(r.stdout+r.stderr).decode()[-6000:])
   if name.startswith('extracted-') and b'skipped=' in r.stderr:raise RuntimeError(name+' skipped')
  run('patch-check',['git','apply','--check',str(patch_path)])
  run('patch-apply',['git','apply',str(patch_path)])
  for n,b in files.items():assert (snapshot/n).read_bytes()==b,n
  run('extracted-pure',[sys.executable,'-m','unittest',*['vehicle.common.tests.'+v for v in [
   'test_review_handshake','test_plate_requests','test_db1_consumer','test_db1_adapter','test_engine_plate','test_mp4_publication_order']]])
  run('extracted-native',[sys.executable,'-m','unittest',*['vehicle.common.tests.'+v for v in [
   'test_review_handshake_redis','test_redis_persistence','test_bounded_operations','test_plate_requests_redis']]])
  run('extracted-contracts',[sys.executable,'contracts/validate_examples.py'])
  run('extracted-p1',[sys.executable,'contracts/db1/validate_fixtures.py'])
  for n,b in files.items():assert (snapshot/n).read_bytes()==b,n
 assert not snapshot.exists()
 evidence['replay.json']=encoded(dict(baseline_sha256=EXPECTED,baseline_source_fingerprint=fingerprint(baseline),
  final_source_fingerprint=fingerprint(files),patch_sha256=sha(patch),source_files_compared=len(files),
  commands=results,temporary_extraction_removed=True,seconds=time.monotonic()-begin))
 allow=['starting-state.json','starting-git-status.txt','review-red.log','review-green.log','gates12.log',
  'ack-cursor-red.log','ack-cursor-red-fixed-test.log','terminal-content-final.log','persistence-initial.log',
  'persistence-and-compat.log','operations-initial.log','postgres-gate.log','commands-additional.json','cleanup.json',
  'runner-final.log','runner-final/report.json','runner-pg-final.log','runner-pg-final/report.json',
  'accepted-checks.log']
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
 evidence['README.md']=b'''# WAYCON completion, commit/ACK and owned Redis restart review\n\nKnown baseline: vehicle_plate_requests_db1_review.zip, SHA256\ne2b58a1168248a38aa2af199e327555c6600a124eeeed67dc7c10e559f5542b2.\nAll 246 manifested baseline sources matched the starting working tree; no\npre-existing post-review repairs existed. Git HEAD is not this baseline.\nExtract those 246 sources, then git apply --check and git apply scoped.patch.\nAll root sources are the complete final 262-file inspection/test snapshot.\nUnchanged files retain their reviewed origins; manifest.json identifies changes.\nNo unrelated working-tree changes or supplemental dependency were added.\n\nSee docs/architecture/vehicle_restart_results.md and vehicle_operator_guide.md.\nNative PostgreSQL is BLOCKED without an explicitly authorized disposable target.\nNine connection-free guards and class skips do not prove native PG behavior.\nReal inference was not rerun. Process-kill tests do not establish power-loss\nguarantees or Engine process-crash recovery. No production-complete claim.\nEvidence includes intentional pre-fix failures, explicitly distinct from passes.\nNo credentials, model weights, media, pixels, database or Redis data are included.\n'''
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
