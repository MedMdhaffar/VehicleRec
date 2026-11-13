"""Explicit browser acceptance using an owned, loopback-only VLC replay.

Never invoked by launcher startup. Replays the known MP4's first four frames so
the plate stays in view; this is a loopback test, not a real camera.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import shutil
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0,str(ROOT))
    from vehicle.common.console_stack import configure
    configure(load_dsn=False)
    os.umask(0o077)
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    child = None
    report = dict(status='FAIL', source='owned loopback replay', real_camera='not tested')
    try:
        with tempfile.TemporaryDirectory(prefix='waycon-browser-rtsp-') as name:
            private = Path(name)
            short, clip = private/'short.mp4', private/'loopback.mp4'
            video = ROOT/'vehicle/license_plate_service/run_video_pipeline/test_video.mp4'
            # Stay in one VLC media lifetime while native models initialize and
            # both browser runs execute. A short looping file closes RTP at EOF.
            subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-i', str(video), '-vf', 'setpts=5*PTS',
                            '-r', '5', '-frames:v', '4', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                            '-tune', 'zerolatency', '-g', '4', '-keyint_min', '4', '-sc_threshold', '0',
                            '-x264-params', 'repeat-headers=1', '-threads', '1', '-pix_fmt', 'yuv420p', '-an', str(short)],
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-stream_loop', '-1', '-i', str(short),
                            '-t', '600', '-map', '0:v:0', '-c', 'copy', str(clip)],
                           check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            endpoint = 'rtsp://127.0.0.1:'+str(port)+'/owned'
            config = private/'vlc.conf'
            config.write_text('sout=#rtp{sdp='+endpoint+'}\nsout-keep=1\nrtsp-host=127.0.0.1\n')
            uri_file = private/'uri'
            uri_file.write_text(endpoint)
            child = subprocess.Popen(['/usr/bin/cvlc', '--config', str(config), '--intf', 'dummy',
                '--no-audio', '--loop', '--no-media-library', str(clip)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, 'XDG_CONFIG_HOME':str(private/'config'), 'XDG_CACHE_HOME':str(private/'cache')})
            deadline = time.monotonic()+10
            ready = False
            while time.monotonic() < deadline and child.poll() is None:
                for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
                    fields = row.split()
                    address, value = fields[1].split(':')
                    if int(value, 16) == port and fields[3] == '0A':
                        assert address == '0100007F'
                        sockets = set()
                        for fd in Path('/proc/'+str(child.pid)+'/fd').iterdir():
                            try:
                                sockets.add(os.readlink(fd))
                            except FileNotFoundError:
                                pass
                        assert 'socket:['+fields[9]+']' in sockets
                        ready = True
                if ready:
                    break
                time.sleep(.05)
            assert ready
            node = os.environ.get('WAYCON_NODE') or shutil.which('node')
            if not node:raise RuntimeError('Missing Node for loopback browser acceptance')
            playwright=os.environ.get('CONSOLE_TEST_PLAYWRIGHT')
            if not playwright:
                playwright=subprocess.check_output([node,'-p',"require.resolve('playwright', {paths: [process.cwd()]})"],
                    cwd=ROOT/'frontend',text=True,stderr=subprocess.DEVNULL,timeout=10).strip()
            env = {**os.environ, 'CONSOLE_TEST_RTSP_FILE':str(uri_file), 'CONSOLE_TEST_OUTPUT':str(output),
                   'CONSOLE_TEST_PLAYWRIGHT':playwright}
            with (output/'browser-rtsp.log').open('wb') as log:
                test = subprocess.run([node, str(ROOT/'scripts/console_rtsp_acceptance.cjs')], env=env, cwd=ROOT,
                                      stdout=log, stderr=log, timeout=650)
            assert test.returncode == 0
            # Decoder/native stderr is suppressed; inspect both public service
            # logs and browser URL/storage evidence without printing the URI.
            logs = [ROOT/'vehicle/media/waycon-launcher/django.log', ROOT/'vehicle/media/waycon-launcher/vite.log',
                    output/'browser-rtsp.log', output/'browser-rtsp.json']
            assert not any(endpoint in path.read_text(errors='replace') for path in logs)
            report.update(status='PASS', listener_loopback_only=True, listener_owned=True,
                          source='1920x1080 replay of first four known MP4 frames', uri_absent_from_logs_and_status=True)
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            report['owned_source_retired'] = child.poll() is not None
        report['private_configuration_removed'] = 'private' in locals() and not private.exists()
        (output/'owned-rtsp.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
