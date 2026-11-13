"""Real-model EOF and disposable commit-before-ACK acceptance; no raw readings."""
import argparse
import json
from pathlib import Path
import time
import requests
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from vehicle.common.console_stack import configure,console_device
    configure(load_dsn=False)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--every',type=int,default=1)
    a=p.parse_args()
    api='http://127.0.0.1:'+os.environ.get('WAYCON_WEB_PORT','5173')+'/api/t1/runs/'
    with a.video.open('rb') as stream:
        response=requests.post(api,data=dict(source_kind='mp4',sample_every=a.every,manufacturer_enabled='false'),
            files={'video':('real.mp4',stream,'video/mp4')},timeout=30)
    assert response.status_code==202
    rid=response.json()['run_id']
    print(json.dumps(dict(started=rid,profile='real models, full launcher, manufacturer off')),flush=True)
    signature=None
    until=time.monotonic()+1800
    while time.monotonic()<until:
        response=requests.get(api+rid+'/',timeout=15);response.raise_for_status();s=response.json()
        sample={k:s[k] for k in ('status','decoded_frames','analyzed_frames','sampling_skips','delivery','active_slot')}
        if sample!=signature:print(json.dumps(sample),flush=True);signature=sample
        if not s['active_slot']:break
        time.sleep(.5)
    else:raise RuntimeError('real_acceptance_timeout')
    root=Path(os.environ.get('T1_CONSOLE_RUN_ROOT',Path(__file__).resolve().parents[1]/'vehicle/media/t1-console'))/rid
    evidence=[json.loads(line) for line in (root/'analyzed-source.jsonl').read_text().splitlines()]
    indexes=[e['frame_index'] for e in evidence]
    progress=json.loads((root/'db1-progress.json').read_text())
    runtime=json.loads((root/'native-runtime.json').read_text())
    result=dict(run_id=rid,status=s['status'],decoded_frames=s['decoded_frames'],analyzed_frames=s['analyzed_frames'],
        sample_every=s['sample_every'],analyzed_source_indexes=indexes,end_reason=s['end_reason'],
        full_video_complete=s['full_video_complete'],active_slot_released=not s['active_slot'],cleanup=s['cleanup'],
        manufacturer_enabled=s['manufacturer_enabled'],delivery=s['delivery'],database=progress,native_runtime=runtime)
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    assert s['status']=='completed' and s['full_video_complete'] and s['end_reason']=='eof'
    assert indexes==list(range(0,s['decoded_frames'],a.every))
    assert not s['manufacturer_enabled'] and s['counters']['manufacturer_calls']==0
    assert s['delivery']['postgres_commit']=='committed' and s['delivery']['redis_ack']=='acknowledged'
    assert runtime['vehicle']['parameter_devices']==[console_device()]
    expected=os.environ.get('WAYCON_EXPECTED_GPU')
    if expected:assert expected.lower() in runtime['vehicle']['gpu_name'].lower()
    assert progress['evidence'] and all(e['exact_entry_pending'] and e['ack_reply']==1 and
        e['before_ack']['acceptances']==1 and e['before_ack']['row_receipts']==e['fact_count'] for e in progress['evidence'])
    assert any(e['before_ack']['rows']['fact_plate_read'] for e in progress['evidence'])
    assert all(s['cleanup'].values())
    print(json.dumps(dict(db1='PASS',run_id=rid,decoded=s['decoded_frames'],analyzed=s['analyzed_frames'])),flush=True)


if __name__=='__main__':main()
