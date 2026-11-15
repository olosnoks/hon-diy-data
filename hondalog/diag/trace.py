"""A Transport wrapper that records every byte sent and received.

The first-contact report shows the whole conversation with the ECU, so a
failure on the real car can be read off the report without running anything
again.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..link.clock import REAL, Clock
from ..protocol import fmt_hex
from ..transport import Transport


@dataclass(frozen=True)
class TraceEvent:
    t: float                 # clock time
    kind: str                # "TX", "RX", "BREAK", "BAUD", "FLUSH", "NOTE"
    data: bytes = b""
    text: str = ""


class TracingTransport(Transport):
    """Pass-through to ``inner`` that logs TX, RX, break and baud changes."""

    def __init__(self, inner: Transport, clock: Clock = REAL, limit: int = 200_000) -> None:
        self.inner = inner
        self.clock = clock
        self.events: list[TraceEvent] = []
        self.limit = limit
        self._sync()

    def _sync(self) -> None:
        """Copy the attributes the Link reads from the wrapped transport."""
        self.name = self.inner.name
        self.usb_latency_s = self.inner.usb_latency_s
        self.baud = self.inner.baud

    def _log(self, kind: str, data: bytes = b"", text: str = "") -> None:
        if len(self.events) < self.limit:
            self.events.append(TraceEvent(self.clock.now(), kind, bytes(data), text))

    def note(self, text: str) -> None:
        self._log("NOTE", text=text)

    def open(self) -> None:
        self.inner.open()
        self._sync()

    def close(self) -> None:
        self.inner.close()

    def write(self, data: bytes) -> None:
        self._log("TX", data)
        self.inner.write(data)

    def read(self, size: int, timeout: float) -> bytes:
        data = self.inner.read(size, timeout)
        if data:
            self._log("RX", data)
        return data

    def read_some(self, size: int, timeout: float) -> bytes:
        data = self.inner.read_some(size, timeout)
        if data:
            self._log("RX", data)
        return data

    def flush_input(self) -> None:
        self.inner.flush_input()

    def set_baud(self, baud: int) -> None:
        self._log("BAUD", text=str(baud))
        self.inner.set_baud(baud)
        self._sync()

    def set_break(self, on: bool) -> None:
        self._log("BREAK", text="on (line low)" if on else "off (line high)")
        self.inner.set_break(on)

    # -- output --

    def bytes_sent(self) -> int:
        return sum(len(e.data) for e in self.events if e.kind == "TX")

    def bytes_received(self) -> int:
        return sum(len(e.data) for e in self.events if e.kind == "RX")

    def dump(self, t0: float | None = None, merge_s: float = 0.003) -> str:
        """One line per event, times in ms. RX chunks closer than ``merge_s``
        are joined, so the echo and the reply each read as one line."""
        if not self.events:
            return "(nothing on the wire)\n"
        base = self.events[0].t if t0 is None else t0
        merged: list[TraceEvent] = []
        for e in self.events:
            last = merged[-1] if merged else None
            if (last is not None and e.kind == "RX" and last.kind == "RX"
                    and e.t - last.t <= merge_s):
                merged[-1] = TraceEvent(e.t, "RX", last.data + e.data)
                continue
            merged.append(e)
        lines = []
        for e in merged:
            body = fmt_hex(e.data) if e.data else e.text
            lines.append(f"{1000 * (e.t - base):10.1f}  {e.kind:<5} {body}")
        return "\n".join(lines) + "\n"
