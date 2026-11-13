"""Freeze the scoped T1 handoff, replay its patch, and verify every archived byte."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = '4f399efd37bb7d62e1cb51946e8af2f5b8121724c9dafab90fddda161d277a48'
NEW = ['tests/db1_integration/test_delivery_gate.py',
       'docs/t1/vehicle_t1_supervisor_handoff.md',
       'docs/t1/vehicle_t1_delivery_runbook.md', 'scripts/package_vehicle_t1_delivery.py']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def safe(name):
    path = PurePosixPath(name)
    assert name and not path.is_absolute() and '..' not in path.parts and '\\' not in name and str(path) == name
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('baseline', 'evidence', 'output', 'redis-server'):
        parser.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), 'refuse replacing an existing review ZIP'
    assert sha(args.baseline.read_bytes()) == EXPECTED
    with ZipFile(args.baseline) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        manifest_bytes = archive.read('_review/manifest.json')
        manifest = json.loads(manifest_bytes)
        assert len(manifest['source_files']) == 266
        baseline = {safe(f['path']): archive.read(f['path']) for f in manifest['source_files']}
        for record in manifest['source_files']:
            assert sha(baseline[record['path']]) == record['sha256']
    starting = json.loads((args.evidence / 'starting-state.json').read_bytes())
    assert starting['differences'] == [] and starting['sha256'] == EXPECTED and starting['source_files'] == 266
    files = {name: (ROOT / name).read_bytes() for name in sorted(set(baseline) | set(NEW))}
    # This delivery adds acceptance evidence only. All reviewed code, SQL and fixtures stay exact.
    assert all(files[name] == data for name, data in baseline.items())
    patch = []
    for name, data in files.items():
        old = baseline.get(name)
        if old == data:
            continue
        patch += [f'diff --git a/{name} b/{name}\n']
        if old is None:
            patch += ['new file mode 100644\n']
        patch += difflib.unified_diff((old or b'').decode().splitlines(True), data.decode().splitlines(True),
                                     fromfile='a/' + name if old is not None else '/dev/null', tofile='b/' + name)
    patch = ''.join(patch).encode()
    evidence = {'scoped.patch': patch}
    # An explicit allowlist is reviewed by the operator. No broker/data directory is collected.
    allow = json.loads((args.evidence / 'package-inputs.json').read_bytes())
    for name in allow:
        safe(name)
        path = args.evidence / name
        assert path.is_file() and not path.is_symlink()
        evidence['evidence/' + name] = path.read_bytes()
    replay = []
    with tempfile.TemporaryDirectory(prefix='waycon-t1-review-replay-') as temporary:
        snapshot = Path(temporary)
        for name, data in baseline.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        patch_path = snapshot / 'scoped.patch'
        patch_path.write_bytes(patch)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', VEHICLE_REDIS_SERVER=str(args.redis_server.resolve()))
        def run(name, command, allow_skips=False):
            start = time.monotonic()
            result = subprocess.run(command, cwd=snapshot, env=env, capture_output=True)
            log = result.stdout + result.stderr
            evidence['replay/' + name + '.log'] = log
            record = dict(name=name, command=command, returncode=result.returncode,
                          seconds=time.monotonic() - start, skips=b'skipped=' in log)
            replay.append(record)
            assert result.returncode == 0 and (allow_skips or not record['skips']), name
        run('patch-check', ['git', 'apply', '--check', str(patch_path)])
        run('patch-apply', ['git', 'apply', str(patch_path)])
        assert all((snapshot / name).read_bytes() == data for name, data in files.items())
        run('consumer-and-ack', [sys.executable, '-m', 'unittest',
            'vehicle.common.tests.test_db1_consumer', 'vehicle.common.tests.test_db1_runner_ack'])
        run('contracts', [sys.executable, 'contracts/validate_examples.py'])
        run('db1-contract', [sys.executable, 'contracts/db1/validate_fixtures.py'])
        run('fixture-guards', [sys.executable, '-m', 'unittest', 'discover',
            '-s', 'tests/db1_integration', '-p', 'test_fixture_safety.py'])
        # Import/AST validation of new native coverage is distinct from real execution evidence.
        run('new-native-import', [sys.executable, '-c',
            "import sys;sys.path.insert(0,'tests/db1_integration');import test_delivery_gate"])
        assert all((snapshot / name).read_bytes() == data for name, data in files.items())
    assert not snapshot.exists()
    evidence['replay.json'] = encoded(dict(commands=replay, compared_files=len(files),
        patch_sha256=sha(patch), temporary_extraction_removed=True,
        native_execution='See current native-authorized evidence; import is not a native pass'))
    with ZipFile(ROOT / 'db1_contract.zip') as archive:
        evidence['preservation.json'] = encoded(dict(all_266_baseline_sources_unchanged=True,
            supervisor_archive_sha256=sha(files['db1_contract.zip']),
            supervisor_sql={name: sha(archive.read(name)) for name in archive.namelist() if name.endswith('.sql')},
            historical_fixtures={name: sha(data) for name, data in baseline.items()
                                 if name.startswith('contracts/') and ('fixtures/' in name or 'examples/' in name)}))
    evidence['README.md'] = b'''# WAYCON Vehicle T1 delivery review

Read docs/t1/vehicle_t1_supervisor_handoff.md for the current verdict and limitations.
Read docs/t1/vehicle_t1_delivery_runbook.md for redacted commands and retry/cleanup.
All 266 reviewed baseline files are unchanged. Four additions form this handoff.
The scoped patch replays against vehicle_db1_runner_ack_followup_review.zip,
SHA256 4f399efd37bb7d62e1cb51946e8af2f5b8121724c9dafab90fddda161d277a48.
Extract only its manifested source files, then git apply --check and git apply
_review/scoped.patch. Compare against _review/manifest.json.
Historical model evidence belongs to its original source manifest. Current tests
use injected recognition; current native PostgreSQL outcomes are stated explicitly.
Early missing-credential attempts remain as history, never counted as passes.
No credentials, model/media assets, pixels, Redis data or database data are included.
'''
    source_records = [dict(path=name, bytes=len(data), sha256=sha(data),
        baseline_sha256=sha(baseline[name]) if name in baseline else None,
        changed=name not in baseline) for name, data in files.items()]
    source_hashes = {name: sha(data) for name, data in files.items()}
    final_manifest = dict(baseline_archive=args.baseline.name, baseline_sha256=EXPECTED,
        baseline_manifest_sha256=sha(manifest_bytes), source_files=source_records,
        baseline_source_fingerprint=sha(encoded({n: sha(b) for n, b in sorted(baseline.items())})),
        final_source_fingerprint=sha(encoded(source_hashes)), scoped_paths=NEW,
        pre_existing_differences=[], evidence=[dict(path='_review/' + name, bytes=len(data), sha256=sha(data))
                                             for name, data in sorted(evidence.items())])
    evidence['manifest.json'] = encoded(final_manifest)
    contents = {**files, **{'_review/' + name: data for name, data in evidence.items()}}
    for name in contents:
        safe(name)
        assert not set(PurePosixPath(name).parts) & {'.git', '.venv', '__pycache__', 'appendonlydir'}
        assert Path(name).suffix.lower() not in {'.env', '.pyc', '.mp4', '.pt', '.pdiparams', '.aof', '.rdb', '.sqlite3', '.db'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_suffix('.pending.zip')
    with ZipFile(pending, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(contents.items()):
            archive.writestr(name, data)
    with ZipFile(pending) as archive:
        assert archive.testzip() is None and len(archive.namelist()) == len(contents)
        for name, data in contents.items():
            assert archive.read(name) == data
    assert all((ROOT / name).read_bytes() == data for name, data in files.items())
    pending.replace(args.output)
    checksum = sha(args.output.read_bytes())
    args.output.with_suffix('.zip.sha256').write_text(checksum + '  ' + args.output.name + '\n')
    result = dict(archive=str(args.output), sha256=checksum, source_files=len(files), scoped_paths=NEW,
                  replay=True, CRC=True, all_content_hashes_verified=True,
                  final_source_fingerprint=final_manifest['final_source_fingerprint'])
    (args.evidence / 'packaging-result.json').write_bytes(encoded(result))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
