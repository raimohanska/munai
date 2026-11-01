"""Daily capture pause in the service's local (Home Assistant) timezone."""
import time


def capture_paused(options, now=None):
    """Start is inclusive, end exclusive; matching times disable the pause."""
    now = time.localtime() if now is None else now
    minute = now.tm_hour * 60 + now.tm_min
    start, end = (int(options[key][:2]) * 60 + int(options[key][3:])
                  for key in ('capture_pause_start', 'capture_pause_end'))
    if start > end:
        return minute >= start or minute < end
    return start <= minute < end
