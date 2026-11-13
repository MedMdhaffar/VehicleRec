"""Real spawned stalled decoders; synthetic pixels only, never a camera."""
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import json
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import tempfile
import threading
import time
import unittest

import numpy as np

from vehicle.common.real_video.rtsp import private_camera, InputFault, InputStopped, RTSPBounds, RTSPInput, stage_camera, uri_file, prompted_uri
from vehicle.common.real_video.rtsp_host import run_connections
from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_real_video import Detector
from vehicle.common.real_video.engine import Engine
from vehicle.common.observation_evidence import RecognitionIdentity
from types import SimpleNamespace


def synthetic_decoder(path, fingerprint, generation, channel, memory_name, bounds, *, start_pts=0.0):
    """Exercise the actual process/shared-slot boundary, including a hung C-call equivalent."""
    memory = SharedMemory(name=memory_name)
    try:
        if Path(path).stem == 'openstall':
            Path(path).with_suffix('.entered').touch()
            time.sleep(30)
        channel.send((generation, 'opened', dict(fps=25.0)))
        while True:
            command, sequence = channel.recv()
            if command == 'stop':
                return
            if Path(path).stem == 'stall' or sequence > 0:
                Path(path).with_suffix('.reading').touch()
                time.sleep(30)
            np.ndarray((16, 16, 3), dtype=np.uint8, buffer=memory.buf)[:] = 1
            tag = 'old-generation' if Path(path).stem == 'stale' else generation
            channel.send((tag, 'frame', (sequence, (16, 16, 3), start_pts)))
    finally:
        memory.close()


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        owner, _, _ = fixture()
        c = owner.lifetime.context
        self.context = replace(c, source_kind='rtsp')
        self.clock = FrameReceiptClock(self.context, utc_clock=lambda: datetime.now(timezone.utc),
                                       monotonic_clock=time.monotonic, policy=ClockPolicy())

    def camera(self, name='camera'):
        path = Path(self.temp.name) / (name + '.json')
        path.write_text(json.dumps(dict(endpoint='rtsp://unit:secret@127.0.0.1:1/camera',
                                       camera_id=self.context.camera.camera_id, timezone='Africa/Tunis')))
        path.chmod(0o600)
        return private_camera(path)

    def source(self, name='camera', *, bounds=None, **kw):
        source = RTSPInput(self.camera(name), self.clock, deadline=time.monotonic()+30,
                           decoder_target=synthetic_decoder,
                           bounds=bounds or RTSPBounds(open_seconds=8, read_seconds=.25, max_frame_bytes=4096), **kw)
        self.addCleanup(source.close)
        return source

    def test_private_file_permissions_and_sanitized_errors(self):
        camera = self.camera()
        self.assertNotIn('secret', repr(camera))
        camera.path.chmod(0o644)
        with self.assertRaisesRegex(InputFault, '^private_camera_configuration_unavailable_or_invalid$'):
            private_camera(camera.path)

    def test_dynamic_start_uri_snapshot_and_fresh_camera_identity(self):
        first=Path(self.temp.name)/'start-one';second=Path(self.temp.name)/'start-two'
        first.mkdir(mode=0o700);second.mkdir(mode=0o700)
        a=stage_camera('rtsp://unit:secret@127.0.0.1:1/one',first)
        b=stage_camera('rtsp://unit:secret@127.0.0.1:1/two',second)
        self.assertNotEqual(a.camera_id,b.camera_id)
        self.assertNotIn('secret',repr(a))
        self.assertEqual(a.path.stat().st_mode & 0o777,0o600)
        supplied=Path(self.temp.name)/'uri.txt'
        supplied.write_text('rtsp://unit:secret@127.0.0.1:1/one\n');supplied.chmod(0o600)
        self.assertEqual(uri_file(supplied),a.endpoint)
        self.assertTrue(supplied.exists())

    def test_hidden_prompt_never_falls_back_to_echo(self):
        from unittest.mock import patch
        import getpass
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value='secret-uri') as prompt:
            self.assertEqual(prompted_uri(),'secret-uri')
            prompt.assert_called_once_with('RTSP URI (hidden): ')
        with patch('sys.stdin.isatty',return_value=False):
            with self.assertRaisesRegex(InputFault,'rtsp_uri_prompt_requires_terminal'):
                prompted_uri()
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=getpass.GetPassWarning):
            with self.assertRaisesRegex(InputFault,'rtsp_hidden_prompt_unavailable'):
                prompted_uri()

    def test_configuration_change_cannot_switch_camera_during_reconnect(self):
        source=self.source()
        from vehicle.common.real_video.rtsp import decoder
        source.target=decoder
        body=json.loads(source.camera.path.read_text());body['endpoint']='rtsp://127.0.0.1:1/changed'
        source.camera.path.write_text(json.dumps(body))
        with self.assertRaisesRegex(InputFault,'private_configuration_changed'):
            source.__enter__()
        self.assertTrue(source.closed)

    def test_opt_in_cli_missing_camera_blocks_before_process_or_database(self):
        from scripts.vehicle_mohamed_lpr_rtsp_smoke import main
        from unittest.mock import patch
        directory=Path(self.temp.name)
        args=['--enable-rtsp-v3','--rtsp-uri-file',str(directory/'absent-private.txt'),
              '--admin-dsn-file',str(directory/'must-not-read'), '--output',str(directory/'cli-output'),
              '--cuda-python','/unused','--source-root','/unused','--ocr-cache','/unused',
              '--redis-server','/unused','--tunis-mode','legacy_whole','--max-frames','4',
              '--max-crops','4','--max-seconds','90','--open-seconds','5','--read-seconds','3','--reconnects','1']
        with patch('subprocess.Popen',side_effect=AssertionError('unexpected process before camera authorization')):
            self.assertEqual(main(args),2)
        report=json.loads((directory/'cli-output/summary.json').read_text())
        self.assertEqual((report['status'],report['frames'],report['db1_records']),('BLOCKED',0,0))

    def test_frame_receipt_host_time_and_source_pts_remain_separate(self):
        source = self.source()
        with source:
            packet = source.read()
            self.assertIs(packet.receipt, self.clock.last_valid_receipt)
            self.assertEqual(packet.receipt.media_pts, 0.0)
            self.assertGreater(packet.receipt.utc.year, 2020)
            self.assertEqual(packet.pixels.shape, (16, 16, 3))
            self.assertEqual(source.decoded, 1)
            name = source.memory.name
        self.assertFalse(source.process.is_alive())
        with self.assertRaises(FileNotFoundError):
            SharedMemory(name=name)

    def test_stalled_read_has_deadline_and_releases_process_and_slot(self):
        source = self.source('stall')
        with source:
            started = time.monotonic()
            with self.assertRaisesRegex(InputFault, 'rtsp_read_or_open_timeout'):
                source.read()
            self.assertLess(time.monotonic()-started, 1.5)
        self.assertTrue(source.closed)
        self.assertFalse(source.process.is_alive())

    def test_stop_interrupts_stalled_read_without_waiting_for_read_timeout(self):
        source = self.source('stall',bounds=RTSPBounds(open_seconds=8,read_seconds=5,max_frame_bytes=4096))
        source.__enter__()
        result = []
        def reader():
            try:
                source.read()
            except InputStopped:
                result.append('stopped')
        thread = threading.Thread(target=reader)
        thread.start()
        deadline=time.monotonic()+2
        while not source.camera.path.with_suffix('.reading').exists() and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertTrue(source.camera.path.with_suffix('.reading').exists())
        started = time.monotonic()
        source.stop()
        thread.join(1)
        self.assertEqual(result, ['stopped'])
        self.assertLess(time.monotonic()-started, 1)
        self.assertFalse(source.process.is_alive())

    def test_stalled_open_is_interruptible_and_cannot_reopen(self):
        source = self.source('openstall')
        def stopper():
            deadline=time.monotonic()+7
            while not source.camera.path.with_suffix('.entered').exists() and time.monotonic()<deadline:
                time.sleep(.005)
            source.stop_event.set()
        timer=threading.Thread(target=stopper);timer.start()
        with self.assertRaises(InputStopped):
            source.__enter__()
        timer.join()
        self.assertTrue(source.camera.path.with_suffix('.entered').exists())
        self.assertTrue(source.closed)
        with self.assertRaises(InputFault):
            source.__enter__()

    def test_late_old_decoder_frame_is_not_receipted(self):
        source = self.source('stale')
        with source:
            with self.assertRaisesRegex(InputFault, 'stale_decoder_message'):
                source.read()
        self.assertIsNone(self.clock.last_valid_receipt)

    def test_total_deadline_beats_read_timeout(self):
        source = self.source('stall')
        with source:
            source.deadline = time.monotonic()+.05
            with self.assertRaisesRegex(InputFault, 'rtsp_total_deadline'):
                source.read()

    def test_reconnect_new_runs_clocks_trackers_pts_reset_and_outage_closure(self):
        sessions = []
        class Session:
            def __init__(self, context, clock):
                self.clock = clock
                self.detector = Detector([])
                self.engine = Engine(context, clock, self.detector,
                    algorithm=RecognitionIdentity('unit-local-hsv',1,'classify'), quality_algorithm='unit-quality',
                    estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
                    quality=lambda c,s,b:SimpleNamespace(combined=.9))
                self.record = {}
                sessions.append(self)
            def admit(self, packet):self.engine.process(packet)
            def finish(self, cause, *, trigger):self.engine.finish(cause,trigger=trigger)
            def settle(self):
                self.record['incomplete']=len(self.engine.pending)
                return self.record
            def close(self):self.engine.close();return dict(complete=True)
        def factory(c, clock, fps, deadline, stop):return Session(c,clock)
        opens=[]
        def source_factory(*a,**k):
            start_pts=100.0 if not opens else 0.0
            opens.append(start_pts)
            return RTSPInput(*a,**k,decoder_target=partial(synthetic_decoder,start_pts=start_pts))
        result = run_connections(self.camera(), factory, max_frames=3, max_seconds=20, reconnects=1,
            bounds=RTSPBounds(open_seconds=8,read_seconds=.15,max_frame_bytes=4096), backoff_seconds=0,
            source_factory=source_factory)
        self.assertEqual((result['frames'],result['reconnects'],len(result['outages']),result['incomplete']), (2,1,2,2))
        lives = [next(iter(s.engine.pending.values())).owner.lifetime for s in sessions]
        self.assertNotEqual(lives[0].context.run_id,lives[1].context.run_id)
        self.assertIs(lives[0].context.camera,lives[1].context.camera)
        self.assertEqual([v.raw_track_id for v in lives], [7,7])
        self.assertEqual([v.closure.value for v in lives], ['camera_outage']*2)
        self.assertEqual([v.first_receipt.media_pts for v in lives], [100.0,0.0])
        self.assertTrue(all(c['decoder_cleaned'] and c['cleanup']['complete'] for c in result['connections']))
        print('RTSP_RECONNECT_EVIDENCE '+json.dumps(result),flush=True)

    def test_delayed_packet_from_old_clock_rejected_before_new_engine(self):
        from vehicle.common.real_video.input import DecodedFrame
        old = self.clock.capture(0, 99.0)
        class StaleSource:
            opens=1;decoded=0;closed=False;metadata={'fps':25}
            def __enter__(self):return self
            def __exit__(self,*_):self.close()
            def close(self):self.closed=True
            def read(self):return DecodedFrame(old,np.zeros((16,16,3),dtype=np.uint8))
        class Session:
            record={}
            def admit(self,p):raise AssertionError('stale frame admitted')
            def finish(self,cause,*,trigger):self.cause=cause
            def settle(self):return dict(incomplete=0)
            def close(self):return dict(complete=True)
        result=run_connections(self.camera(),lambda *a:Session(),max_frames=1,max_seconds=2,reconnects=0,
                               source_factory=lambda *a,**k:StaleSource())
        self.assertEqual(result['frames'],0)
        self.assertEqual(result['outages'][0]['reason'],'stale_frame_receipt')

    def test_reconnect_attempt_budget_and_no_engine_on_open_failure(self):
        class FailedOpen:
            opens=1;decoded=0;closed=False
            def __enter__(self):raise InputFault('rtsp_open_failed')
            def __exit__(self,*_):self.close()
            def close(self):self.closed=True
        result=run_connections(self.camera(),lambda *a:self.fail('Engine created without an open decoder'),
            max_frames=1,max_seconds=3,reconnects=3,backoff_seconds=0,
            source_factory=lambda *a,**k:FailedOpen())
        self.assertEqual((result['decoder_opens'],result['reconnects'],len(result['outages'])),(4,3,4))
        self.assertEqual(result['status'],'BLOCKED')

    def test_total_run_deadline_is_service_stop_and_not_camera_outage(self):
        class DeadlineSource:
            opens=1;decoded=0;closed=False;metadata={'fps':25}
            def __enter__(self):return self
            def __exit__(self,*_):self.close()
            def close(self):self.closed=True
            def read(self):raise InputFault('rtsp_total_deadline')
        class Session:
            record={}
            def finish(self,cause,*,trigger):self.cause=cause
            def settle(self):return dict(incomplete=0)
            def close(self):return dict(complete=True)
        result=run_connections(self.camera(),lambda *a:Session(),max_frames=1,max_seconds=2,reconnects=1,
                               source_factory=lambda *a,**k:DeadlineSource())
        self.assertEqual(result['outages'],[])
        self.assertEqual(result['reconnects'],0)
        self.assertEqual(result['connections'][0]['closure'],'service_stop')
        self.assertEqual(result['connections'][0]['stop_reason'],'time_budget')

    def test_unknown_retirement_prevents_reconnect_and_reports_incomplete(self):
        class Source:
            opens=1;decoded=0;closed=False;metadata={'fps':25}
            def __enter__(self):return self
            def __exit__(self,*_):self.close()
            def close(self):self.closed=True
            def read(self):raise InputFault('rtsp_disconnected')
        class Session:
            record={}
            def finish(self,cause,*,trigger):pass
            def settle(self):return dict(incomplete=1)
            def close(self):return dict(complete=False,unretired_operations=1,retained_crops=1)
        result=run_connections(self.camera(),lambda *a:Session(),max_frames=1,max_seconds=2,reconnects=3,
                               source_factory=lambda *a,**k:Source())
        self.assertEqual((result['status'],result['reconnects'],result['incomplete']),('FAIL',0,1))

    def test_stop_between_decode_and_admission_never_starts_engine_work(self):
        stop=threading.Event()
        from vehicle.common.real_video.input import DecodedFrame
        class Source:
            opens=1;decoded=0;closed=False;metadata={'fps':25}
            def __init__(self,clock):self.clock=clock
            def __enter__(self):return self
            def __exit__(self,*_):self.close()
            def close(self):self.closed=True
            def read(self):
                receipt=self.clock.capture(0,0.0);self.decoded+=1;stop.set()
                return DecodedFrame(receipt,np.zeros((16,16,3),dtype=np.uint8))
        class Session:
            record={}
            def admit(self,p):raise AssertionError('Engine ran after Stop')
            def finish(self,cause,*,trigger):pass
            def settle(self):return dict(incomplete=0)
            def close(self):return dict(complete=True)
        result=run_connections(self.camera(),lambda *a:Session(),max_frames=1,max_seconds=2,reconnects=1,stop=stop,
            source_factory=lambda camera,clock,**k:Source(clock))
        self.assertEqual(result['frames'],0)
        self.assertEqual(result['connections'][0]['unadmitted_frames'],1)
        self.assertEqual(result['connections'][0]['stop_reason'],'controlled_stop')


if __name__ == '__main__':
    unittest.main()
