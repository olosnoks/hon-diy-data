"""Time source for the link.

Everything on the link side asks a ``Clock`` for the time and to wait, never
``time`` directly. With ``VirtualClock`` the simulator, the link and the
poller run on simulated time: a 60 second benchmark at exact 9600 baud byte
timing finishes in well under a second and gives the same numbers every run.
"""

from __future__ import annotations

import time


class Clock:
    """The real monotonic clock."""

    virtual = False

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VirtualClock(Clock):
    """Simulated time. ``sleep`` returns at once and moves the clock."""

    virtual = True

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.t += seconds

    def advance_to(self, t: float) -> None:
        if t > self.t:
            self.t = t


REAL = Clock()
