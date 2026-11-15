"""Priority scheduler: which block to read next, and how well it is doing.

Every channel has a rate class:

    fast    engine speed and load: RPM, MAP, TPS, injector, ignition advance
    medium  mixture and road speed: O2, fuel trims, VSS
    slow    everything that drifts: temperatures, battery, flags

A channel's class comes from its ``rate`` field in the address map
(``rate = "fast"``); without one, from its name (``DEFAULT_CLASS``); without
that, flags and unknown channels are slow. The targets in reads per second
are ``rate_fast_hz`` / ``rate_medium_hz`` / ``rate_slow_hz`` in the map's
``[protocol]`` table (20 / 5 / 1 by default).

The scheduler is "most overdue first": it reads the block whose time since
its last read, measured in its own periods, is largest. It never waits: when
no block is due yet, the spare wire time goes to the fast class, which then
runs above target. When the targets add up to more than the wire can carry,
the slower classes still get close to their (cheap) targets and the fast
class takes what is left; nothing starves, because a block's urgency keeps
growing until it is read. ``report`` gives the achieved rate per channel and
the shortfall against target.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from ..protocol import Framing
from .planner import Block, ChannelLike

FAST, MEDIUM, SLOW = "fast", "medium", "slow"
RATE_CLASSES = (FAST, MEDIUM, SLOW)

DEFAULT_CLASS: dict[str, str] = {
    "rpm": FAST, "map": FAST, "tps": FAST, "inj": FAST, "ign": FAST,
    "o2": MEDIUM, "stft": MEDIUM, "ltft": MEDIUM, "vss": MEDIUM,
    "ect": SLOW, "iat": SLOW, "batt": SLOW, "baro": SLOW,
}

_WINDOW_S = 5.0            # achieved rates are averaged over this long
_TOLERANCE = 0.05          # within 5% of target counts as met


def rate_class_of(channel: ChannelLike) -> str:
    explicit = str(getattr(channel, "rate", "") or "").strip().lower()
    if explicit:
        if explicit not in RATE_CLASSES:
            raise ValueError(f"{channel.name}: rate must be one of {RATE_CLASSES}, not {explicit!r}")
        return explicit
    return DEFAULT_CLASS.get(channel.name, SLOW)


def target_hz(rate_class: str, framing: Framing) -> float:
    return {FAST: framing.rate_fast_hz, MEDIUM: framing.rate_medium_hz,
            SLOW: framing.rate_slow_hz}[rate_class]


@dataclass(frozen=True)
class ChannelRate:
    name: str
    rate_class: str
    target_hz: float
    achieved_hz: float
    worst_gap_s: float          # longest time between two reads in the window

    @property
    def shortfall_hz(self) -> float:
        """Reads per second missing against target; 0 when within 5% of it."""
        return 0.0 if self.ok else self.target_hz - self.achieved_hz

    @property
    def ok(self) -> bool:
        return self.achieved_hz >= self.target_hz * (1 - _TOLERANCE)


class Scheduler:
    def __init__(self, blocks: Sequence[Block], channel_class: dict[str, str] | None = None,
                 channel_target: dict[str, float] | None = None) -> None:
        self.blocks = list(blocks)
        self.channel_class = channel_class or {}
        self.channel_target = channel_target or {}
        self._rate = [max(b.rate_hz, 1e-6) for b in self.blocks]
        self._last = [float("-inf")] * len(self.blocks)
        self._stamps: list[deque[float]] = [deque(maxlen=1024) for _ in self.blocks]
        self.reads = [0] * len(self.blocks)
        self._lead = 0.0
        self._low_last = False
        self._prev_done = float("-inf")
        self.failures = [0] * len(self.blocks)
        top = max(self._rate, default=0.0)
        #: blocks at the highest rate: one pass over these is one "cycle"
        self.top = frozenset(i for i, r in enumerate(self._rate) if r >= top)

    def next(self, now: float) -> int:
        """Index of the block to read now.

        1. A never-read block, fastest class first.
        2. A slower-class block that is due, but never two in a row: the
           slower classes are cheap and keep their targets, and the fast
           class never waits for more than one of them.
        3. The most overdue block, if anything is due.
        4. Otherwise the spare wire time goes to the fastest class.
        """
        best, best_u = 0, float("-inf")
        top, top_u = -1, float("-inf")
        low, low_u = -1, float("-inf")
        last, rate, top_set = self._last, self._rate, self.top
        lead = self._lead
        for i in range(len(rate)):
            u = (now - last[i] + lead) * rate[i]
            if u > best_u or (u == best_u and rate[i] > rate[best]):
                best, best_u = i, u
            if i in top_set:
                if u > top_u:
                    top, top_u = i, u
            elif u > low_u:
                low, low_u = i, u
        if best_u == float("inf"):
            pick = best
        elif low_u >= 1.0 and not self._low_last:
            pick = low
        elif best_u >= 1.0 or top < 0:
            pick = best if (best in top_set or not self._low_last or top < 0) else top
        else:
            pick = top
        self._low_last = pick not in top_set
        return pick

    def done(self, index: int, t_done: float) -> None:
        # A block picked now is finished one request later, so "due" looks
        # ahead by the typical request time. Without this every period comes
        # out one request too long.
        step = t_done - self._prev_done
        if 0.0 < step < 0.2:
            self._lead = step if self._lead == 0.0 else 0.9 * self._lead + 0.1 * step
        self._prev_done = t_done
        self._last[index] = t_done
        self._stamps[index].append(t_done)
        self.reads[index] += 1

    def failed(self, index: int, now: float) -> None:
        """Push a failed block back one period so it cannot hog the wire."""
        self._last[index] = now
        self.failures[index] += 1

    def restart(self) -> None:
        """Forget read times (after the link was down)."""
        self._last = [float("-inf")] * len(self.blocks)
        for stamps in self._stamps:
            stamps.clear()

    def block_hz(self, index: int, now: float) -> tuple[float, float]:
        """(reads per second, worst gap) for one block over the last window."""
        stamps = [t for t in self._stamps[index] if t >= now - _WINDOW_S]
        if len(stamps) < 2:
            return 0.0, (now - stamps[0] if stamps else float("inf"))
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        worst = max(max(gaps), now - stamps[-1])
        span = stamps[-1] - stamps[0]
        return ((len(stamps) - 1) / span if span > 0 else 0.0), worst

    def report(self, now: float) -> dict[str, ChannelRate]:
        """Achieved rate and shortfall for every channel, by channel name."""
        out: dict[str, ChannelRate] = {}
        for i, block in enumerate(self.blocks):
            hz, worst = self.block_hz(i, now)
            for name in block.channels:
                out[name] = ChannelRate(name, self.channel_class.get(name, SLOW),
                                        self.channel_target.get(name, block.rate_hz), hz, worst)
        return out

    def shortfalls(self, now: float) -> dict[str, ChannelRate]:
        return {n: r for n, r in self.report(now).items() if not r.ok}
