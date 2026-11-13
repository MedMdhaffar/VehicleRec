"""Console sampling, with no admission limit on a video's frame count."""
import math
import re


def sampling_interval(value, kind):
    if value is None:
        return 5 if kind == 'rtsp' else 1
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    if type(value) is not int or value < 1:
        raise ValueError('sampling_interval_must_be_a_positive_integer')
    return value


def timeout_seconds(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):raise ValueError('invalid_timeout')
    value = float(value)
    if not math.isfinite(value) or value <= 0:raise ValueError('invalid_timeout')
    return value


def advance_tracker(tracker, gap, empty, pixels, *, update=None):
    """Age ByteTrack in source-frame units, including sampled/dropped frames.

    After all tracks have expired, fast-forward the empty remainder. Work is
    bounded by the tracker's lost-track horizon even for enormous N values.
    No model is invoked and no nonexistent observation is manufactured.
    """
    missing = max(0, gap-1)
    if not missing:return
    horizon = getattr(tracker,'max_frames_lost',getattr(tracker,'max_time_lost',None))
    if horizon is None:raise RuntimeError('unsupported_tracker_timebase')
    steps = min(missing, horizon+2)
    update = update or tracker.update
    for _ in range(steps):update(empty, pixels)
    tracker.frame_id += missing-steps
