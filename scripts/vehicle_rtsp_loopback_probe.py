"""Owned VLC RTSP replay: Input/receipt/outage/reconnect, no model or DB calls."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    source_args=p.add_mutually_exclusive_group(required=True)
    source_args.add_argument('--video',type=Path,help='Audited MP4 only')
    source_args.add_argument('--synthetic',action='store_true',help='Generated color bars; no source checkout required')
    p.add_argument('--ffmpeg',type=Path,default=Path('/usr/bin/ffmpeg'))
    p.add_argument('--rtsp-transport',required=True,choices=('tcp','udp'))
    p.add_argument('--vlc',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    report=dict(status='FAIL',frames=0,reconnects=0,connections=[],model_calls=0,db1_records=0)
    report['limits']=dict(seconds=60,first_connection_frames=32,reconnected_frames=2,
        open_seconds=12,read_seconds=3,decoded_frame_slots=1,frame_bytes=1920*1080*3)
    child=source=None
    try:
        if a.video:
            lock=json.loads((ROOT/'vehicle/common/mohamed_lpr_assets.json').read_text())
            if not a.video.is_file():raise RuntimeError('audited_clip_unavailable')
            if sha256(a.video.read_bytes()).hexdigest()!=lock['clip_sha256']:
                raise RuntimeError('wrong_audited_clip')
        from vehicle.common.real_video.rtsp import private_camera,RTSPBounds,RTSPInput,InputFault
        from vehicle.common.db1_context import CameraRegistration,ModelRegistration,ResolutionInfo,RunContext
        from vehicle.common.frame_receipt import FrameReceiptClock,ClockPolicy
        with tempfile.TemporaryDirectory(prefix='waycon-owned-rtsp-',dir=str(a.output.parent)) as name:
            private=Path(name);os.chmod(private,0o700)
            video=a.video
            if a.synthetic:
                video=private/'generated.mp4'
                subprocess.run([str(a.ffmpeg),'-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10',
                    '-t','8','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-g','10','-threads','1',
                    '-pix_fmt','yuv420p','-an',str(video)],check=True,timeout=15,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            endpoint='rtsp://127.0.0.1:'+str(port)+'/audited'
            vlc_config=private/'vlc.conf'
            vlc_config.write_text('sout=#rtp{sdp='+endpoint+'}\nsout-keep=1\nrtsp-host=127.0.0.1\n')
            vlc_config.chmod(0o600)
            config=private/'camera.json'
            config.write_text(json.dumps(dict(endpoint=endpoint,camera_id='owned-loopback',timezone='Africa/Tunis')))
            config.chmod(0o600)
            camera=private_camera(config)
            org=UUID('12345678-1234-4234-8234-123456789012')
            registration=CameraRegistration(org,camera.camera_id,None,camera.timezone,datetime.now(timezone.utc),None)
            deadline=time.monotonic()+60
            for epoch in range(2):
                env=dict(os.environ,XDG_CONFIG_HOME=str(private/'config'),XDG_CACHE_HOME=str(private/'cache'))
                args=[str(a.vlc),'--config',str(vlc_config),'--intf','dummy','--no-audio','--loop',
                      '--no-media-library',str(video)]
                child=subprocess.Popen(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    ready=False
                    opening_deadline=min(deadline,time.monotonic()+5)
                    while time.monotonic()<opening_deadline and child.poll() is None:
                        for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
                            fields=row.split();address,value=fields[1].split(':')
                            if int(value,16)==port and fields[3]=='0A':
                                if address!='0100007F':raise RuntimeError('owned_server_not_loopback_only')
                                sockets=set()
                                for fd in Path('/proc/'+str(child.pid)+'/fd').iterdir():
                                    try:sockets.add(os.readlink(fd))
                                    except FileNotFoundError:pass
                                if 'socket:['+fields[9]+']' not in sockets:
                                    raise RuntimeError('loopback_listener_not_owned')
                                ready=True
                        if ready:break
                        time.sleep(.02)
                    if not ready:raise RuntimeError('owned_vlc_server_unavailable')
                    context=RunContext(org,registration,'rtsp-loopback-'+uuid4().hex,'rtsp',datetime.now(timezone.utc),
                        'input-only-owned-probe',ResolutionInfo(None,None,'vehicle',
                            ModelRegistration(None,'no-model-execution',1,'detect'),None,'owned input-only probe',True),'development')
                    clock=FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),
                                            monotonic_clock=time.monotonic,policy=ClockPolicy())
                    source=RTSPInput(camera,clock,deadline=deadline,
                                     bounds=RTSPBounds(open_seconds=12,read_seconds=3,transport=a.rtsp_transport))
                    record=dict(run_id=context.run_id,receipts=[],outage=False)
                    with source:
                        record['source_metadata']=source.metadata
                        for index in range(32 if epoch==0 else 2):
                            try:packet=source.read()
                            except InputFault as error:
                                record.update(outage=True,reason=str(error));break
                            record['receipts'].append(dict(frame_index=packet.receipt.frame_index,
                                utc=packet.receipt.utc.isoformat(),monotonic=packet.receipt.monotonic,
                                source_pts=packet.receipt.media_pts))
                            if epoch==0 and index==0:
                                child.terminate();child.wait(timeout=3)  # ONLY the owned source is interrupted.
                        if epoch==0 and not record['outage']:
                            raise RuntimeError('outage_not_observed_within_frame_budget')
                    record.update(frames=source.decoded,decoder_cleaned=source.closed,
                                  decoder_retired=not source.process.is_alive())
                    report['connections'].append(record)
                    report['frames']+=source.decoded
                    if epoch==1:report['reconnects']+=1
                finally:
                    if source:source.close()
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=3)
            if len(report['connections'])!=2 or report['connections'][1]['frames']!=2:
                raise RuntimeError('owned_reconnect_failed')
            report.update(status='PASS',source_kind='generated_color_bars' if a.synthetic else 'audited_mp4',
                          clip_sha256=sha256(video.read_bytes()).hexdigest(),vlc_sha256=sha256(a.vlc.read_bytes()).hexdigest(),
                          transport=a.rtsp_transport,
                          source_time='decoder_pts_not_capture_time',time_basis='host_receipt',
                          source_processes_retired=True,private_configuration_removed_after_exit=True)
    except Exception as error:
        report.update(status='FAIL',failure_type=type(error).__name__,reason='bounded_owned_loopback_failure')
        if isinstance(error,RuntimeError) and __import__('re').fullmatch('[a-z0-9_]{1,96}',str(error)):
            report['reason']=str(error)
    finally:
        if source:source.close()
        if child is not None and child.poll() is None:
            child.terminate();child.wait(timeout=3)
        (a.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('status','reason','frames','reconnects','model_calls','db1_records') if k in report}))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
