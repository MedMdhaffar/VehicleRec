"""Bounded connection lifecycle; each reconnect is a new registered run scope."""
from datetime import datetime, timezone
import threading
import time
from uuid import UUID, uuid4

from ..db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
from ..frame_receipt import ClockPolicy, FrameReceiptClock
from ..vehicle_observation import RunEnd
from .rtsp import InputFault, InputStopped, RTSPBounds, RTSPInput


def run_connections(camera, session_factory, *, max_frames, max_seconds, reconnects,
                    bounds=RTSPBounds(), stop=None, source_factory=RTSPInput, backoff_seconds=.25):
    """Never carry tracker/clock/run state over a transport gap.

    Session.finish freezes work before settlement. A failed settlement blocks
    reconnect rather than losing work. Native retirement remains Engine-owned;
    the admission deadline never licenses freeing an executing crop.
    """
    if (type(max_frames) is not int or not 1 <= max_frames <= 8 or not 0 < max_seconds <= 300
            or type(reconnects) is not int or not 0 <= reconnects <= 3 or not 0 <= backoff_seconds <= 2):
        raise ValueError('invalid_rtsp_window')
    stop = stop if stop is not None else threading.Event()
    deadline = time.monotonic() + max_seconds
    org = UUID('12345678-1234-4234-8234-123456789012')
    initial = datetime.now(timezone.utc)
    registration = CameraRegistration(org, camera.camera_id, None, camera.timezone, initial, None)
    prefix = 'rtsp-' + uuid4().hex
    report = dict(frames=0, reconnects=0, connections=[], outages=[], time_basis='host_receipt',
                  source_pts_basis='decoder_pts_not_capture_time', frame_slots=1, status='PASS')
    for connection in range(reconnects + 1):
        if stop.is_set() or time.monotonic() >= deadline or report['frames'] >= max_frames:
            break
        context = RunContext(org, registration, prefix + '-' + str(connection), 'rtsp',
            datetime.now(timezone.utc), 't1-opt-in-rtsp-v3',
            ResolutionInfo(None, None, 'vehicle', ModelRegistration(None, 'local-vehicle-yolo26', 1, 'detect'),
                           None, 'owned disposable RTSP validation; no production registration', True), 'development')
        clock = FrameReceiptClock(context, utc_clock=lambda: datetime.now(timezone.utc),
                                  monotonic_clock=time.monotonic, policy=ClockPolicy())
        source = source_factory(camera, clock, deadline=deadline, bounds=bounds, stop=stop)
        session = None
        record = dict(run_id=context.run_id, connection=connection, frames=0, receipts=[],
                      incomplete=0, decoder_opens=0, decoder_cleaned=False)
        cause, reason = RunEnd.SERVICE_STOP, 'frame_budget'
        outage = False
        try:
            with source:
                record['source_metadata'] = source.metadata
                session = session_factory(context, clock, source.metadata['fps'], deadline, stop)
                while report['frames'] < max_frames:
                    if stop.is_set():
                        reason = 'controlled_stop'; break
                    if time.monotonic() >= deadline:
                        reason = 'time_budget'; break
                    packet = source.read()
                    if stop.is_set():
                        reason = 'controlled_stop'; break
                    if time.monotonic() >= deadline:
                        reason = 'time_budget'; break
                    # A delayed packet from a previous decoder must not enter a
                    # new run, even if frame index/PTS/raw tracker IDs coincide.
                    if packet.receipt is not clock.last_valid_receipt:
                        raise InputFault('stale_frame_receipt')
                    session.admit(packet)
                    report['frames'] += 1
                    record['frames'] += 1
                    record['receipts'].append(dict(frame_index=packet.receipt.frame_index,
                        utc=packet.receipt.utc.isoformat(), monotonic=packet.receipt.monotonic,
                        source_pts=packet.receipt.media_pts))
        except InputStopped:
            reason = 'controlled_stop'
        except InputFault as error:
            reason = str(error)
            if reason == 'rtsp_total_deadline':
                cause, reason = RunEnd.SERVICE_STOP, 'time_budget'
            else:
                cause, outage = RunEnd.CAMERA_OUTAGE, True
                report['outages'].append(dict(connection=connection, reason=reason,
                                             host_utc=datetime.now(timezone.utc).isoformat()))
        except BaseException:
            cause, reason = RunEnd.PROCESS_ERROR, 'engine_or_clock_failure'
            report['status'] = 'FAIL'
        finally:
            source.close()  # Admission from this decoder ends before sealing.
            record.update(decoder_cleaned=source.closed, decoder_opens=source.opens, stop_reason=reason,
                          decoded_frames=source.decoded,unadmitted_frames=source.decoded-record['frames'])
            if session is not None:
                try:
                    session.finish(cause, trigger=reason)
                    record.update(session.settle())
                except BaseException:
                    record['incomplete'] += 1
                    record['settlement_failure'] = True
                    report['status'] = 'FAIL'
                finally:
                    try:
                        record['cleanup'] = session.close()
                        record.update(getattr(session, 'record', {}))
                        if not record['cleanup'].get('complete', False):
                            report['status'] = 'FAIL'
                    except BaseException:
                        record['cleanup'] = dict(complete=False)
                        report['status'] = 'FAIL'
            record['closure'] = cause.value
            report['connections'].append(record)
        if report['status'] != 'PASS' or not outage or stop.is_set():
            break
        if connection < reconnects and time.monotonic() < deadline and report['frames'] < max_frames:
            stop.wait(min(backoff_seconds, max(0, deadline - time.monotonic())))
            if not stop.is_set() and time.monotonic() < deadline:
                report['reconnects'] += 1
    report['decoder_opens'] = sum(c['decoder_opens'] for c in report['connections'])
    report['incomplete'] = sum(c['incomplete'] for c in report['connections'])
    if report['frames'] == 0 and report['status'] == 'PASS':
        report['status'] = 'BLOCKED'
    return report
