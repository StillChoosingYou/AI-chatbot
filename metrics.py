"""Live numbers about this running server: how long requests take and how many fail.

Kept in memory (the last few hundred requests per group), so they describe this server instance since it started.
A serverless host may start several instances; the Quality Scorecard says so next to the numbers.
"""
import threading
import time
from collections import deque

KEEP = 500


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


class Metrics:
    def __init__(self):
        self.started = time.time()
        self._lock = threading.Lock()
        self._times = {}                  # group -> deque of milliseconds
        self._errors = deque(maxlen=KEEP * 2)       # 1 for a 5xx, 0 for anything else

    def record(self, group, milliseconds, status):
        with self._lock:
            self._times.setdefault(group, deque(maxlen=KEEP)).append(milliseconds)
            self._errors.append(1 if status >= 500 else 0)

    def summary(self):
        with self._lock:
            groups = {name: list(values) for name, values in self._times.items()}
            errors = list(self._errors)
        out = {'uptime_seconds': int(time.time() - self.started), 'requests': len(errors),
               'errors': sum(errors), 'groups': {}}
        for name, values in groups.items():
            out['groups'][name] = {'count': len(values), 'p50': round(percentile(values, .5), 1),
                                   'p95': round(percentile(values, .95), 1), 'max': round(max(values), 1)}
        return out
