"""Deterministic console API acceptance with real MP4/RTSP decoders.

Use only the isolated, explicitly configured empty-inference fixture server.
Real model/DB1 acceptance is a separate full-launcher run. No URI or pixels are
written to the report; only source indexes, timestamps and bounded counters.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import requests


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',default='http://127.0.0.1:8003/api/t1/runs/')
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    os.umask(0o077)
    a.output.mkdir(parents=True,exist_ok=True)
    report=dict(mp4='FAIL',rtsp_loopback='FAIL',inference='explicit server-side fixture',runs=[])
    child=None
    def get(rid):
        response=requests.get(a.api+rid+'/',timeout=10);response.raise_for_status();return response.json()
    def start(every,video=None,uri=None,seconds=None):
        data=dict(source_kind='mp4' if video else 'rtsp',sample_every=every,manufacturer_enabled='false')
        if seconds is not None:data['max_seconds']=seconds
        if video:
            with video.open('rb') as stream:response=requests.post(a.api,data=data,files={'video':('fixture.mp4',stream,'video/mp4')},timeout=20)
        else:
            data.update(rtsp_uri=uri,rtsp_transport='udp')
            response=requests.post(a.api,json=data,timeout=20)
        assert response.status_code==202
        result=response.json();assert result['sample_every']==every
        assert 'max_frames' not in result
        return result['run_id']
    def wait(rid,predicate,seconds=90):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            status=get(rid)
            if predicate(status):return status
            time.sleep(.03)
        raise RuntimeError('acceptance_wait_timeout')
    def finished(rid):
        status=wait(rid,lambda s:not s['active_slot'])
        assert status['cleanup'] and all(status['cleanup'].values())
        path=a.run_root/rid/'analyzed-source.jsonl'
        evidence=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert len(evidence)==status['analyzed_frames']
        assert all(e['connection_id']==rid for e in evidence)
        item=dict(run_id=rid,source_kind=status['source_kind'],sample_every=status['sample_every'],
            status=status['status'],end_reason=status['end_reason'],full_video_complete=status['full_video_complete'],
            decoded_frames=status['decoded_frames'],analyzed_frames=status['analyzed_frames'],
            sampling_skips=status['sampling_skips'],overload_drops=status['overload_drops'],
            analyzed_source_indexes=[e['frame_index'] for e in evidence],receipts=evidence,
            active_slot_released=True,cleanup=status['cleanup'],
            tracks=[dict(track_id=r['track_id'],raw_track_id=r['raw_track_id'],tracker_epoch=r['tracker_epoch'],run_id=r['run_id']) for r in status['rows']])
        report['runs'].append(item)
        return status,item
    try:
        with tempfile.TemporaryDirectory(prefix='waycon-sampling-source-') as name:
            private=Path(name)
            def video(path,frames):
                subprocess.run(['/usr/bin/ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=320x180:r=21',
                    '-frames:v',str(frames),'-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p',str(path)],
                    check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            clip=private/'21.mp4';video(clip,21)
            for every in (1,5):
                rid=start(every,video=clip);status,item=finished(rid)
                assert item['analyzed_source_indexes']==list(range(0,21,every))
                assert status['decoded_frames']==21 and status['sampling_skips']==21-len(item['receipts'])
                assert status['status']=='completed' and status['end_reason']=='eof' and status['full_video_complete']
            rid=start(1,video=clip)
            wait(rid,lambda s:s['analyzed_frames']>=3)
            requests.post(a.api+rid+'/stop/',timeout=10).raise_for_status()
            status,_=finished(rid)
            assert status['status']=='stopped' and status['end_reason']=='controlled_stop'
            assert not status['full_video_complete'] and status['decoded_frames']<21
            rid=start(5,video=clip);status,_=finished(rid);assert status['full_video_complete']
            rid=start(1,video=clip,seconds=.01);status,_=finished(rid)
            assert status['status']=='partial' and status['end_reason']=='time_limit' and not status['full_video_complete']
            long_clip=private/'101.mp4';video(long_clip,101)
            rid=start(1,video=long_clip);status,item=finished(rid)
            assert item['analyzed_source_indexes']==list(range(101)) and status['full_video_complete']
            report['mp4']='PASS'
            source=private/'source.mp4'
            subprocess.run(['/usr/bin/ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=20',
                '-t','180','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-g','10','-threads','1','-pix_fmt','yuv420p',str(source)],
                check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def serve():
                nonlocal child
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                uri='rtsp://127.0.0.1:'+str(port)+'/owned'
                config=private/'vlc.conf'
                config.write_text('sout=#rtp{sdp='+uri+'}\nsout-keep=1\nrtsp-host=127.0.0.1\n')
                child=subprocess.Popen(['/usr/bin/cvlc','--config',str(config),'--intf','dummy','--no-audio','--no-media-library',str(source)],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env={**os.environ,'XDG_CONFIG_HOME':str(private/'config'),'XDG_CACHE_HOME':str(private/'cache')})
                until=time.monotonic()+10
                while time.monotonic()<until:
                    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
                        f=line.split();address,value=f[1].split(':')
                        if int(value,16)==port and f[3]=='0A':
                            assert address=='0100007F';return uri
                    time.sleep(.02)
                raise RuntimeError('owned_source_start_failed')
            uri=serve()
            for every in (1,5):
                rid=start(every,uri=uri);wait(rid,lambda s:s['analyzed_frames']>=6)
                requests.post(a.api+rid+'/stop/',timeout=10).raise_for_status()
                status,item=finished(rid)
                indexes=item['analyzed_source_indexes']
                assert indexes[0]==0 and all(i%every==0 for i in indexes)
                assert indexes==sorted(set(indexes)) and status['status']=='stopped'
                assert all(t['track_id']==1 and t['tracker_epoch']==0 for t in item['tracks'])
                if every==1:assert status['sampling_skips']==0 and status['overload_drops']>0
                else:assert status['sampling_skips']>0
                assert not Path(json.loads((a.run_root/rid/'config.json').read_text())['source']).exists()
            rid=start(5,uri=uri);wait(rid,lambda s:s['analyzed_frames']>=3)
            child.terminate();child.wait(timeout=5)
            status,outage=finished(rid)
            assert status['status']=='failed' and status['end_reason']=='camera_outage'
            uri=serve()
            rid=start(5,uri=uri);wait(rid,lambda s:s['analyzed_frames']>=3)
            requests.post(a.api+rid+'/stop/',timeout=10).raise_for_status()
            status,reconnected=finished(rid)
            assert reconnected['analyzed_source_indexes'][0]==0 and rid!=outage['run_id']
            assert all(t['track_id']==1 and t['tracker_epoch']==0 and t['raw_track_id']==7 for t in reconnected['tracks'])
            report['rtsp_loopback']='PASS'
            report['reconnect']='fresh private Start after observed outage; no old sampling/lifetime state'
            report['live_camera']='NOT TESTED: no camera access'
    except Exception as error:
        import traceback
        report['failure']=dict(type=type(error).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno) for f in traceback.extract_tb(error.__traceback__)])
    finally:
        if child is not None and child.poll() is None:child.terminate();child.wait(timeout=5)
        report['owned_source_retired']=child is None or child.poll() is not None
        (a.output/'api-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='runs'}))
    return 0 if report['mp4']==report['rtsp_loopback']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
