"""Link counters and the adaptive timeouts.

Three timers are measured and adapted while the link runs:

``response_timeout``
    How long to wait, after the request has left the wire, for the first
    reply byte. Tracks the measured per-request overhead (ECU think time +
    USB latency + host scheduling) with a smoothed mean and deviation, the
    way TCP tracks its retransmit timer (RFC 6298). Doubles on a timeout.
    Never above ``Framing.reply_timeout_s``, never below
    ``Framing.min_reply_timeout_s``.

``inter_byte_timeout``
    Once a reply has started, how long a silence means "this frame is over".
    Tracks the largest gap seen between received chunks. Doubles when a reply
    is cut short. Never above ``Framing.inter_byte_timeout_s``.

``gap``
    Idle time between the end of one frame and the next request. Starts at
    ``Framing.inter_request_delay_s`` (its floor). It grows only when errors
    come in clusters (more than about one exchange in five failing), doubling
    up to ``Framing.max_inter_request_delay_s``, and shrinks back once the
    line is clean again. A single bad frame on a noisy cable does not slow
    the link down.

``late_guard``
    After a request that got no answer at all, how long the line must be
    watched before the next request goes out. A reply that is merely late
    would otherwise land in the next exchange, and since Honda replies carry
    no address it could be taken for the answer to the wrong question. This
    is ``Framing.reply_timeout_s``, the longest a reply is ever waited for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..protocol import Framing

_ERR_CLUSTER = 0.2        # smoothed failure share above which the gap grows
_ERR_CLEAN = 0.05         # ... and below which it shrinks again


@dataclass
class LinkStats:
    """Counters since connect. Plain ints: reading them from another thread is fine."""

    requests: int = 0            # exchanges started (retries included)
    ok: int = 0                  # exchanges that returned a valid frame
    timeouts: int = 0            # no reply, or a reply that stopped short
    short_replies: int = 0       # the "stopped short" part of timeouts
    checksum_failures: int = 0   # full-length reply, wrong checksum
    bad_frames: int = 0          # bytes arrived, none of them a reply
    echo_mismatches: int = 0     # our own bytes came back damaged or not at all
    junk_bytes: int = 0          # extra bytes skipped around an otherwise good frame
    resyncs: int = 0             # times the line was drained back to idle
    discarded_bytes: int = 0     # bytes thrown away while draining
    retries: int = 0             # second and later attempts
    wakes: int = 0               # init sequences sent
    reconnects: int = 0          # port reopened after it was lost

    @property
    def errors(self) -> int:
        return self.timeouts + self.checksum_failures + self.bad_frames

    def as_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["errors"] = self.errors
        return out


class AdaptiveTiming:
    def __init__(self, framing: Framing, usb_latency_s: float = 0.001) -> None:
        self.byte_s = 10.0 / framing.baud           # 8N1: 10 bits per byte
        self.adaptive = framing.adaptive_timing
        self.usb_latency_s = max(0.0, usb_latency_s)
        self.timeout_ceiling = framing.reply_timeout_s
        self.timeout_floor = min(framing.min_reply_timeout_s, framing.reply_timeout_s)
        self.ibt_ceiling = max(framing.inter_byte_timeout_s, 2 * self.byte_s)
        self.gap_floor = max(0.0, framing.inter_request_delay_s)
        self.gap_ceiling = max(self.gap_floor, framing.max_inter_request_delay_s)
        self.srtt: float | None = None
        self.rttvar = 0.0
        self.peak_chunk_gap = 0.0
        self.response_timeout = self.timeout_ceiling
        self.inter_byte_timeout = self.ibt_ceiling
        self.gap = self.gap_floor
        self.error_share = 0.0

    def reset(self) -> None:
        """Forget what was measured (after a reconnect or a protocol switch)."""
        self.srtt = None
        self.rttvar = 0.0
        self.peak_chunk_gap = 0.0
        self.response_timeout = self.timeout_ceiling
        self.inter_byte_timeout = self.ibt_ceiling
        self.gap = self.gap_floor
        self.error_share = 0.0

    # -- measurements --

    def on_success(self, overhead_s: float, chunk_gap_s: float) -> None:
        """``overhead_s``: exchange time minus the pure byte time on the wire.
        ``chunk_gap_s``: longest silence inside the reply."""
        r = max(0.0, overhead_s)
        if self.srtt is None:
            self.srtt, self.rttvar = r, r / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - r)
            self.srtt = 0.875 * self.srtt + 0.125 * r
        self.peak_chunk_gap = max(chunk_gap_s, 0.95 * self.peak_chunk_gap)
        self.error_share *= 0.9
        if not self.adaptive:
            return
        want = 2 * self.srtt + 4 * self.rttvar + 2 * self.byte_s + 2 * self.usb_latency_s
        self.response_timeout = min(self.timeout_ceiling, max(self.timeout_floor, want))
        ibt = 4 * self.peak_chunk_gap + 2 * self.byte_s + 2 * self.usb_latency_s
        self.inter_byte_timeout = min(self.ibt_ceiling, max(self.ibt_floor, ibt))
        if self.error_share < _ERR_CLEAN and self.gap > self.gap_floor:
            self.gap = max(self.gap_floor, self.gap * 0.9)

    def on_failure(self, short: bool = False, silent: bool = False) -> None:
        """A request failed. ``short``: the reply started, then stopped.
        ``silent``: nothing but (maybe) the echo came back."""
        self.error_share = 0.9 * self.error_share + 0.1
        if not self.adaptive:
            return
        if silent:
            self.response_timeout = min(self.timeout_ceiling, self.response_timeout * 2)
        if short:
            self.inter_byte_timeout = min(self.ibt_ceiling, self.inter_byte_timeout * 2)
        if self.error_share > _ERR_CLUSTER:
            self.gap = min(self.gap_ceiling, max(self.gap * 2, 0.002))

    # -- derived --

    @property
    def ibt_floor(self) -> float:
        return min(self.ibt_ceiling, 4 * self.byte_s + 2 * self.usb_latency_s)

    @property
    def idle_gap(self) -> float:
        """Silence that counts as "the line is idle" when resynchronising."""
        return 4 * self.byte_s + 2 * self.usb_latency_s

    @property
    def late_guard(self) -> float:
        """How long after an unanswered request a late reply is still expected."""
        return self.timeout_ceiling

    @property
    def overhead_s(self) -> float:
        """Measured per-request overhead, or 0 before the first good exchange."""
        return self.srtt or 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "overhead_ms": 1000 * self.overhead_s,
            "overhead_dev_ms": 1000 * self.rttvar,
            "response_timeout_ms": 1000 * self.response_timeout,
            "inter_byte_timeout_ms": 1000 * self.inter_byte_timeout,
            "gap_ms": 1000 * self.gap,
        }
