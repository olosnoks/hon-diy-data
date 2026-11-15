"""Read-only link probes: how fast does the ECU answer, how long a block does
it accept, and which addresses answer at all.

Each takes a ``Link`` and sends nothing but read requests. From the app run
them on the poller thread: ``poller.submit(lambda link: probe_timing(link))``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..protocol import ProtocolError
from ..transport import Link

BLOCK_LENGTHS = (1, 2, 4, 8, 16, 32, 48, 64)
GAP_CANDIDATES_S = (0.005, 0.002, 0.001)       # never tried below 1 ms


@dataclass
class TimingProbe:
    reads: int = 0
    failed: int = 0
    turnaround_median_ms: float = 0.0     # last request byte -> first reply byte
    turnaround_p95_ms: float = 0.0
    turnaround_max_ms: float = 0.0
    overhead_ms: float = 0.0              # whole exchange minus its byte time
    usb_latency_ms: float = 0.0
    gap_errors: dict[float, int] = field(default_factory=dict)   # gap (s) -> failed reads
    #: values for the address map's [protocol] table
    recommended: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reads > 0 and self.failed <= self.reads // 20

    def summary(self) -> str:
        if not self.reads:
            return "no read succeeded"
        return (f"ECU turnaround median {self.turnaround_median_ms:.1f} ms, "
                f"p95 {self.turnaround_p95_ms:.1f} ms, max {self.turnaround_max_ms:.1f} ms "
                f"over {self.reads} reads ({self.failed} failed)")


def probe_timing(link: Link, reads: int = 200, addr: int = 0x00, length: int = 2,
                 tune_gap: bool = True, apply: bool = False) -> TimingProbe:
    """Measure the ECU's turnaround and suggest timeouts.

    The suggestion keeps a wide margin: the reply timeout is four times the
    slowest turnaround seen plus 20 ms, never under 50 ms. With ``tune_gap``
    it also tries inter-request gaps of 5, 2 and 1 ms (30 reads each) and
    recommends the shortest that produced no error. With ``apply`` the
    recommended reply timeout and gap become this link's limits right away
    (the adaptive timing keeps working inside them).
    """
    out = TimingProbe(usb_latency_ms=1000 * link.transport.usb_latency_s)
    turnarounds: list[float] = []
    overheads: list[float] = []
    byte_s = link.timing.byte_s
    for _ in range(reads):
        try:
            r = link.read_block(addr, length, retries=0)
        except ProtocolError:
            out.failed += 1
            continue
        turnarounds.append(r.turnaround_s)
        overheads.append(r.t_done - r.t_request - (5 + length + 3) * byte_s)
    out.reads = len(turnarounds)
    if not turnarounds:
        out.notes.append("no read succeeded; nothing measured")
        return out
    turnarounds.sort()
    out.turnaround_median_ms = 1000 * statistics.median(turnarounds)
    out.turnaround_p95_ms = 1000 * turnarounds[min(len(turnarounds) - 1, int(0.95 * len(turnarounds)))]
    out.turnaround_max_ms = 1000 * turnarounds[-1]
    out.overhead_ms = 1000 * statistics.median(overheads)
    out.recommended["reply_timeout_s"] = round(min(0.5, max(0.05, 4 * turnarounds[-1] + 0.02)), 3)
    out.recommended["turnaround_s"] = round(statistics.median(overheads), 4)
    if out.usb_latency_ms > 1.5:
        out.notes.append(f"USB latency timer is {out.usb_latency_ms:.0f} ms, not 1 ms: every "
                         "request is slower than it needs to be (see the udev rule)")

    if tune_gap:
        saved = link.timing.gap, link.timing.gap_floor
        try:
            for gap in GAP_CANDIDATES_S:
                link.timing.gap = link.timing.gap_floor = gap
                errors = 0
                for _ in range(30):
                    try:
                        link.read_block(addr, length, retries=0)
                    except ProtocolError:
                        errors += 1
                out.gap_errors[gap] = errors
        finally:
            link.timing.gap, link.timing.gap_floor = saved
        clean = [g for g, e in out.gap_errors.items() if e == 0]
        if clean:
            out.recommended["inter_request_delay_s"] = min(clean)
        else:
            out.recommended["inter_request_delay_s"] = max(GAP_CANDIDATES_S)
            out.notes.append("errors at every gap tried; the line is noisy, not too fast")
    if apply and out.ok:
        tm = link.timing
        tm.timeout_ceiling = out.recommended["reply_timeout_s"]
        tm.timeout_floor = min(tm.timeout_floor, tm.timeout_ceiling)
        tm.response_timeout = min(tm.response_timeout, tm.timeout_ceiling)
        if "inter_request_delay_s" in out.recommended:
            tm.gap_floor = tm.gap = out.recommended["inter_request_delay_s"]
            tm.gap_ceiling = max(tm.gap_ceiling, tm.gap_floor)
    return out


@dataclass
class BlockLengthProbe:
    results: dict[int, bool] = field(default_factory=dict)     # length -> answered
    max_read_len: int = 0

    def summary(self) -> str:
        if not self.max_read_len:
            return "no block length answered"
        tried = ", ".join(f"{n}{'' if ok else ' (no)'}" for n, ok in self.results.items())
        return f"largest block the ECU answered: {self.max_read_len} bytes (tried {tried})"


def probe_block_length(link: Link, addr: int = 0x00,
                       lengths: tuple[int, ...] = BLOCK_LENGTHS) -> BlockLengthProbe:
    """Find the longest read the ECU answers with a valid checksum."""
    out = BlockLengthProbe()
    for length in lengths:
        if addr + length > 0x100:
            break
        ok = False
        for _ in range(2):
            try:
                ok = len(link.read_block(addr, length, retries=0).data) == length
            except ProtocolError:
                continue
            if ok:
                break
        out.results[length] = ok
        if ok:
            out.max_read_len = max(out.max_read_len, length)
    return out


@dataclass
class Sweep:
    data: dict[int, int] = field(default_factory=dict)         # address -> byte
    unanswered: list[tuple[int, int]] = field(default_factory=list)   # (addr, length)

    @property
    def answered(self) -> int:
        return len(self.data)

    def ascii_runs(self, min_len: int = 4) -> list[tuple[int, str]]:
        """Runs of printable bytes: where an ECU id or part number might be."""
        runs: list[tuple[int, str]] = []
        start, text = 0, ""
        for addr in range(0x101):
            b = self.data.get(addr, -1)
            if 0x20 <= b < 0x7F:
                if not text:
                    start = addr
                text += chr(b)
            else:
                if len(text) >= min_len:
                    runs.append((start, text))
                text = ""
        return runs

    def hexdump(self) -> str:
        lines = ["      " + " ".join(f"{i:02X}" for i in range(16))]
        for row in range(0, 0x100, 16):
            cells = [f"{self.data[a]:02X}" if a in self.data else "--" for a in range(row, row + 16)]
            text = "".join(chr(self.data[a]) if 0x20 <= self.data.get(a, 0) < 0x7F else "."
                           for a in range(row, row + 16))
            lines.append(f"{row:02X}:   {' '.join(cells)}   {text}")
        return "\n".join(lines)


def sweep(link: Link, start: int = 0x00, end: int = 0xFF, chunk: int = 16) -> Sweep:
    """Read every address once. Chunks that fail are retried byte by byte, so
    one dead address does not hide its neighbours."""
    out = Sweep()
    for addr in range(start, end + 1, chunk):
        length = min(chunk, end + 1 - addr)
        try:
            data = link.read_block(addr, length).data
        except ProtocolError:
            out.unanswered.append((addr, length))
            continue
        out.data.update((addr + i, b) for i, b in enumerate(data))
    if out.data and out.unanswered:
        failed, out.unanswered = out.unanswered, []
        for addr, length in failed:
            for a in range(addr, addr + length):
                try:
                    out.data[a] = link.read_block(a, 1, retries=0).data[0]
                except ProtocolError:
                    if out.unanswered and sum(out.unanswered[-1]) == a:
                        out.unanswered[-1] = (out.unanswered[-1][0], out.unanswered[-1][1] + 1)
                    else:
                        out.unanswered.append((a, 1))
    return out
