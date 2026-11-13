"""Bounded same-live-session host, including reconnect without native replay."""
import time
from collections import Counter
from ..db1_rows.core import require
from ..vehicle_observation import RunEnd
from ..frame_receipt import ClockEpochInvalid
from .status import run_status


def drive_bounded(source,engine,publisher,*,settlement_seconds=30,stop_requested=lambda:False,on_progress=lambda s:None,
                  runtime_check=lambda:None,after_turn=lambda:None):
    require(0<settlement_seconds<=120,'INVALID_WINDOW','bounded settlement window required')
    start=time.monotonic();errors=Counter();admitted=0;reason='frame_limit'
    from redis.exceptions import ConnectionError,TimeoutError
    def offer():
        deadline=time.monotonic()+settlement_seconds
        while engine.pending:
            runtime_check()
            for sid,closed in tuple(engine.pending.items()):
                try:
                    if publisher.publish(closed):engine.acknowledge(sid)
                except (ConnectionError,TimeoutError):
                    errors['REDIS_RETRY']+=1
                    try:publisher.reconnect()
                    except (ConnectionError,TimeoutError):pass
            after_turn()
            if engine.pending:
                require(time.monotonic()<deadline,'SETTLEMENT_WAITING','deadline reached; original evidence remains unresolved')
                time.sleep(.025)
    try:
        with source:
            for _ in range(engine.limits.max_frames):
                runtime_check()
                if stop_requested():reason='controlled_stop';break
                if time.monotonic()-start>=engine.limits.max_seconds:reason='time_limit';break
                packet=source.read()
                if packet is None:reason='eof';break
                engine.process(packet);admitted+=1;del packet
                offer()
        engine.finish(RunEnd.END_OF_INPUT if reason=='eof' else RunEnd.SERVICE_STOP,trigger=reason)
        offer()
        result=run_status(source,engine,publisher,admitted_frames=admitted,errors=errors)
        result.update(outcome='settled',stop_reason=reason,elapsed_seconds=time.monotonic()-start)
        on_progress(result)
        return result
    except BaseException as error:
        errors[getattr(error,'code','HOST_FAILURE')]+=1
        engine.finish(RunEnd.CLOCK_DISCONTINUITY if isinstance(error,ClockEpochInvalid) else RunEnd.PROCESS_ERROR,trigger='host_failure')
        result=run_status(source,engine,publisher,admitted_frames=admitted,errors=errors)
        result.update(outcome='unresolved',stop_reason='host_failure',elapsed_seconds=time.monotonic()-start)
        on_progress(result)
        raise
    finally:source.close();engine.close()
