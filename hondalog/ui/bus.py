"""ChannelBus: the one place live data goes on its way to the screen.

Sources (the ECU poller, the black-box reader, math channels, replay) publish
values from any thread. The UI never gets a signal per sample: it reads the
bus on its own frame timer. So a fast source cannot flood the event loop and
the poller never waits on the UI.

No Qt in here.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

SPARK_N = 120               # sparkline samples: last 60 s at 2 Hz
SPARK_DT = 0.5
HIST_N = 4096               # per-channel history ring (live replay lanes, map)
NAN = float("nan")


@dataclass
class ChannelSpec:
    """What the UI needs to know about one channel. Built by a ChannelSource."""
    id: str
    label: str = ""
    units: str = ""
    decimals: int = 0
    lo: float = 0.0             # display range (gauge, lane scaling)
    hi: float = 1.0
    kind: str = "value"         # "value" or "flag"
    source: str = "ECU"         # "ECU", "BB", "MATH", ...
    verified: bool = False
    warn_lo: float | None = None
    warn_hi: float | None = None
    alarm_lo: float | None = None
    alarm_hi: float | None = None
    signed: bool = False        # show an explicit + (fuel trims)
    mapped: bool = True         # False = a slot with nothing behind it yet

    def __post_init__(self):
        self.label = self.label or self.id
        if self.kind == "flag":
            self.lo, self.hi, self.decimals = 0.0, 1.0, 0
        if not self.hi > self.lo:
            self.hi = self.lo + 1.0

    @property
    def is_flag(self) -> bool:
        return self.kind == "flag"

    def format(self, value) -> str:
        """Display text: true minus sign, optional +, '---' for no data."""
        if value is None or value != value:
            return "---"
        if self.kind == "flag":
            return "ON" if value >= 0.5 else "OFF"
        if abs(value) < 0.5 * 10.0 ** -self.decimals:
            value = 0.0
        text = f"{abs(value):.{self.decimals}f}"
        if value < 0:
            return "−" + text
        return "+" + text if self.signed and value > 0 else text


class ChannelState:
    """Live state of one channel. Written under the bus lock, read without it
    (single attribute reads are atomic in CPython)."""

    __slots__ = ("spec", "value", "t", "gen", "vmin", "vmax", "changes", "timeout",
                 "spark", "spark_gen", "ht", "hv", "hn")

    def __init__(self, spec: ChannelSpec, hist_n: int = HIST_N):
        self.spec = spec
        self.value = NAN
        self.t = -math.inf          # bus time of the last valid update
        self.gen = 0                # bumps on every update
        self.vmin = NAN             # session min / max
        self.vmax = NAN
        self.changes = 0            # value changes since reset (flags, raw bytes)
        self.timeout = 1.0          # seconds without an update before it is stale
        self.spark = np.full(SPARK_N, NAN, dtype=np.float32)
        self.spark_gen = 0
        self.ht = np.zeros(hist_n, dtype=np.float64)
        self.hv = np.full(hist_n, NAN, dtype=np.float32)
        self.hn = 0                 # samples ever written to the ring

    def stale(self, now: float) -> bool:
        return not self.spec.mapped or now - self.t > self.timeout

    def _put(self, t: float, value: float) -> None:
        if value != self.value:
            if self.value == self.value:
                self.changes += 1
            self.value = value
        if not self.vmin <= value:      # also true while vmin is NaN
            self.vmin = value
        if not self.vmax >= value:
            self.vmax = value
        i = self.hn % len(self.hv)
        self.ht[i] = t
        self.hv[i] = value
        self.hn += 1
        self.t = t
        self.gen += 1


class ChannelBus:
    def __init__(self, hist_n: int = HIST_N):
        self._lock = threading.Lock()
        self._chans: dict[str, ChannelState] = {}
        self._hist_n = hist_n
        self.t0 = time.monotonic()
        self.version = 0            # bumps when the set of channels changes
        self.pub_gen = 0            # bumps on every publish
        self.last_wire = 0.0        # monotonic time the newest data left the wire
        self._spark_t = 0.0
        self._rates: dict[str, list] = {}   # source -> [last publish t, ema period]

    # -- time --

    def now(self) -> float:
        """Bus time: seconds since the bus was created (monotonic)."""
        return time.monotonic() - self.t0

    def bus_time(self, monotonic: float) -> float:
        return monotonic - self.t0

    # -- registry (any thread) --

    def register(self, spec: ChannelSpec) -> ChannelState:
        """Add a channel, or replace the spec of an existing one (keeps data)."""
        with self._lock:
            state = self._chans.get(spec.id)
            if state is not None:
                state.spec = spec
            else:
                state = ChannelState(spec, self._hist_n)
                chans = dict(self._chans)
                chans[spec.id] = state
                self._chans = chans         # copy-on-write: readers never see a half update
            self.version += 1
            return state

    def unregister_source(self, source: str) -> None:
        with self._lock:
            self._chans = {k: s for k, s in self._chans.items() if s.spec.source != source}
            self.version += 1

    def channel(self, channel_id: str) -> ChannelState | None:
        return self._chans.get(channel_id)

    def channels(self, source: str | None = None) -> list[ChannelState]:
        return [s for s in self._chans.values() if source is None or s.spec.source == source]

    def ids(self) -> list[str]:
        return list(self._chans)

    def set_timeout(self, source: str, seconds: float) -> None:
        for state in self.channels(source):
            state.timeout = seconds

    # -- publishing (any thread) --

    def publish(self, values, t: float | None = None, wire: float | None = None,
                source: str | None = None) -> None:
        """One sample per channel. values: id -> number (None / NaN are skipped).

        t is bus time (default: now). wire is the time.monotonic() at which the
        bytes arrived, for the latency overlay.
        """
        mono = time.monotonic()
        if t is None:
            t = mono - self.t0
        chans = self._chans
        with self._lock:
            for name, value in values.items():
                state = chans.get(name)
                if state is None or value is None or value != value:
                    continue
                state._put(t, float(value))
            self.pub_gen += 1
            self.last_wire = wire if wire is not None else mono
            if source is not None:
                self._note_rate(source, t)

    def publish_block(self, t, columns, wire: float | None = None,
                      source: str | None = None) -> None:
        """Many samples at once (a 1 kHz source sends blocks, not single rows).

        t: 1-D array of bus times, columns: id -> array of the same length.
        """
        t = np.asarray(t, dtype=np.float64)
        n = len(t)
        if n == 0:
            return
        chans = self._chans
        with self._lock:
            for name, col in columns.items():
                state = chans.get(name)
                if state is None:
                    continue
                col = np.asarray(col, dtype=np.float32)
                size = len(state.hv)
                tt, cc = (t[-size:], col[-size:]) if n > size else (t, col)
                idx = (state.hn + np.arange(len(tt))) % size
                state.ht[idx] = tt
                state.hv[idx] = cc
                state.hn += len(tt)
                last = float(col[-1])
                if last == last:
                    lo, hi = float(np.nanmin(col)), float(np.nanmax(col))
                    if not state.vmin <= lo:
                        state.vmin = lo
                    if not state.vmax >= hi:
                        state.vmax = hi
                    if last != state.value:
                        state.changes += 1
                        state.value = last
                    state.t = float(t[-1])
                    state.gen += 1
            self.pub_gen += 1
            self.last_wire = wire if wire is not None else time.monotonic()
            if source is not None:
                self._note_rate(source, float(t[-1]), n)

    def _note_rate(self, source: str, t: float, n: int = 1) -> None:
        rate = self._rates.get(source)
        if rate is None:
            self._rates[source] = [t, 0.0]
            return
        dt = (t - rate[0]) / n
        rate[0] = t
        if dt > 0:
            rate[1] = dt if rate[1] == 0 else 0.8 * rate[1] + 0.2 * dt

    def rate_hz(self, source: str) -> float:
        rate = self._rates.get(source)
        return 1.0 / rate[1] if rate and rate[1] > 0 else 0.0

    # -- UI side --

    def tick(self, now: float | None = None) -> bool:
        """Advance the 2 Hz sparkline buffers. Call once per UI frame.

        Returns True when a sparkline sample was taken.
        """
        if now is None:
            now = self.now()
        if now - self._spark_t < SPARK_DT:
            return False
        steps = min(3, int((now - self._spark_t) / SPARK_DT))
        self._spark_t = now if steps >= 3 else self._spark_t + steps * SPARK_DT
        for state in self._chans.values():
            spark = state.spark
            value = NAN if state.stale(now) else state.value
            for _ in range(steps):
                spark[:-1] = spark[1:]
                spark[-1] = value
            state.spark_gen += 1
        return True

    def history(self, channel_id: str, since: float = -math.inf):
        """(t, v) copies in time order, newest last. Not for paint paths."""
        state = self._chans.get(channel_id)
        if state is None:
            return np.empty(0), np.empty(0, dtype=np.float32)
        with self._lock:
            size, n = len(state.hv), state.hn
            if n <= size:
                t, v = state.ht[:n].copy(), state.hv[:n].copy()
            else:
                i = n % size
                t = np.concatenate((state.ht[i:], state.ht[:i]))
                v = np.concatenate((state.hv[i:], state.hv[:i]))
        if since > -math.inf and len(t):
            k = int(np.searchsorted(t, since))
            t, v = t[k:], v[k:]
        return t, v

    def reset_stats(self) -> None:
        """Forget session min / max and change counts (new drive)."""
        with self._lock:
            for state in self._chans.values():
                state.vmin = state.vmax = state.value
                state.changes = 0
