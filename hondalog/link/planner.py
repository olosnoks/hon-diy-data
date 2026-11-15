"""Block-read planner: which (addr, length) requests to send.

A request costs the same fixed overhead however much it reads:

    5 bytes   our request, echoed back on the one-wire K-line
    3 bytes   reply header, length byte and checksum
    turnaround   ECU think time + USB latency + host scheduling (measured)
    gap          idle time before the next request

and then one byte time (10 bits at 9600 baud = 1.04 ms) per data byte. With
the defaults the overhead is worth about 12 data bytes, so reading across a
gap of unwanted bytes is cheaper than starting a second request unless the
gap is longer than that.

Channels are not all read equally often (see scheduler.py). A block is read
at the rate of its most demanding channel, so the planner minimises

    sum over blocks of  rate(block) * time(block)

which is the fraction of the wire the plan needs. Pulling a slow byte into a
fast block costs one byte time at the fast rate; leaving it out costs a whole
request at the slow rate. The dynamic programme below finds the exact optimum
over all ways of cutting the address-sorted channel list into blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..protocol import REPLY_OVERHEAD, REQUEST_BYTES, Framing


class ChannelLike(Protocol):
    name: str
    addr: int
    length: int


@dataclass(frozen=True)
class CostModel:
    """Time one read request takes on the wire."""

    byte_s: float = 10.0 / 9600
    echo_bytes: int = REQUEST_BYTES
    frame_bytes: int = REPLY_OVERHEAD
    turnaround_s: float = 0.002
    gap_s: float = 0.002

    @classmethod
    def from_framing(cls, framing: Framing, turnaround_s: float | None = None,
                     gap_s: float | None = None) -> "CostModel":
        return cls(byte_s=10.0 / framing.baud,
                   turnaround_s=framing.turnaround_s if turnaround_s is None else turnaround_s,
                   gap_s=framing.inter_request_delay_s if gap_s is None else gap_s)

    @property
    def overhead_s(self) -> float:
        """Fixed cost of a request, whatever its length."""
        return (self.echo_bytes + self.frame_bytes) * self.byte_s + self.turnaround_s + self.gap_s

    @property
    def overhead_bytes(self) -> float:
        """The fixed cost expressed in data bytes: the break-even gap."""
        return self.overhead_s / self.byte_s

    def request_s(self, length: int) -> float:
        return self.overhead_s + length * self.byte_s


@dataclass(frozen=True)
class Span:
    """A run of bytes that must arrive in one reply (one or more channels)."""

    addr: int
    length: int
    rate_hz: float = 1.0
    names: tuple[str, ...] = ()

    @property
    def end(self) -> int:
        return self.addr + self.length


@dataclass(frozen=True)
class Block:
    """One read request and what it is for."""

    addr: int
    length: int
    rate_hz: float = 1.0
    channels: tuple[str, ...] = ()

    @property
    def end(self) -> int:
        return self.addr + self.length


def spans_from_channels(channels: Iterable[ChannelLike],
                        rate_of: Callable[[ChannelLike], float] | None = None) -> list[Span]:
    """Channel byte ranges, overlapping ones merged, sorted by address.

    A multi-byte channel is one span, so it is never split across two
    requests: both bytes of RPM come from the same instant.
    """
    raw = sorted(((c.addr, c.addr + c.length, rate_of(c) if rate_of else 1.0, c.name)
                  for c in channels), key=lambda s: (s[0], s[1]))
    spans: list[Span] = []
    for lo, hi, rate, name in raw:
        if spans and lo < spans[-1].end:
            last = spans[-1]
            spans[-1] = Span(last.addr, max(last.end, hi) - last.addr,
                             max(last.rate_hz, rate), last.names + (name,))
        else:
            spans.append(Span(lo, hi - lo, rate, (name,)))
    return spans


def plan_blocks(spans: Sequence[Span], max_len: int = 16,
                cost: CostModel | None = None) -> list[Block]:
    """Cheapest set of reads that covers every span. Exact (dynamic programme).

    ``spans`` must be sorted and non-overlapping (``spans_from_channels``).
    A span longer than ``max_len`` is read in ``max_len`` pieces.
    """
    cost = cost or CostModel()
    if max_len < 1:
        raise ValueError("max_len must be at least 1")
    n = len(spans)
    inf = float("inf")
    best = [0.0] + [inf] * n          # best[k]: cheapest cover of spans[:k]
    cut = [0] * (n + 1)               # cut[k]: first span of the last block
    for k in range(1, n + 1):
        end = spans[k - 1].end
        rate = 0.0
        for i in range(k - 1, -1, -1):
            length = end - spans[i].addr
            if length > max_len and i < k - 1:
                break
            rate = max(rate, spans[i].rate_hz)
            pieces = -(-length // max_len)          # > 1 only for an oversize span
            c = best[i] + rate * (pieces * cost.overhead_s + length * cost.byte_s)
            if c < best[k]:
                best[k], cut[k] = c, i
    blocks: list[Block] = []
    k = n
    while k > 0:
        i = cut[k]
        addr, end = spans[i].addr, spans[k - 1].end
        rate = max(s.rate_hz for s in spans[i:k])
        names = tuple(name for s in spans[i:k] for name in s.names)
        parts = [(a, min(max_len, end - a)) for a in range(addr, end, max_len)]
        for a, length in reversed(parts):
            blocks.append(Block(a, length, rate, names))
        k = i
    blocks.reverse()
    return blocks


def bus_load(blocks: Sequence[Block], cost: CostModel) -> float:
    """Fraction of wire time the plan needs to hit every target. > 1 = it cannot."""
    return sum(b.rate_hz * cost.request_s(b.length) for b in blocks)


def predict_rates(blocks: Sequence[Block], cost: CostModel) -> list[float]:
    """Reads per second each block should get from the scheduler.

    The scheduler never idles. Blocks below the top rate are read at their
    target; the top-rate blocks share whatever wire time is left, which puts
    them above target on a quiet plan and below it on an overloaded one. If
    the slower blocks alone overload the wire, everything scales down together.
    """
    if not blocks:
        return []
    top = max(b.rate_hz for b in blocks)
    lower = sum(b.rate_hz * cost.request_s(b.length) for b in blocks if b.rate_hz < top)
    top_cycle = sum(cost.request_s(b.length) for b in blocks if b.rate_hz >= top)
    if lower >= 1.0 or top_cycle <= 0:
        load = bus_load(blocks, cost)
        return [b.rate_hz / load for b in blocks]
    top_hz = (1.0 - lower) / top_cycle
    return [top_hz if b.rate_hz >= top else b.rate_hz for b in blocks]
