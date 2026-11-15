"""Standard OBD2 over the K-line: ISO 9141-2, with an ISO 14230 (KWP2000)
fast-init fallback.

This is the link that every 1996+ US-market car must answer, so it is the
fallback if the Honda RAM-read protocol stays silent on pin 7, and the way to
read and clear trouble codes either way.

``Iso9141Link``   wake-up (5-baud or fast init), message framing, timing.
``Obd2Session``   the services: Mode 01 live PIDs (only the supported ones),
                  Mode 02 freeze frame, Mode 03 stored and Mode 07 pending
                  DTCs, Mode 04 clear (needs an explicit confirmation).

5-baud init, as done here on an FT232 (which cannot run at 5 baud):

    idle >= 300 ms (W5)
    address 0x33 at 5 bit/s by switching break on and off: 200 ms per bit,
        start bit low, 8 data bits LSB first, stop bit high        (2.0 s)
    ECU: 0x55 sync at 10400 baud after 60-300 ms (W1)
    ECU: key byte 1, key byte 2, each within 20 ms (W2, W3)
    us:  inverted key byte 2, 25-50 ms later (W4)
    ECU: inverted address 0xCC, 25-50 ms later
    then requests  68 6A F1 <mode> <pid..> <sum>
    and replies    48 6B <ecu> <mode+0x40> <data..> <sum>

Timing rules kept: at least 55 ms between the end of a reply and the next
request (P3min), and something sent at least every 5 s (P3max) or the ECU
drops the session; ``request`` re-runs the init if the session has gone stale
and ``keepalive`` exists for callers that sit idle.

Sharing the line with the Honda protocol
----------------------------------------
Both use pin 7 at different speeds (9600 and 10400 baud), so only one can
talk at a time. Going Honda -> ISO needs the line idle for 300 ms and the
2.5 s init. Going ISO -> Honda needs no power cycle here: stop sending, set
9600 baud, and the ECU lets go of the ISO session after at most 5 s of
silence. ``Poller.obd2`` does both switches. Whether the real P2P behaves
this way (or answers Honda reads straight away, or wants the key cycled) is
not verified; see the README.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .link.clock import REAL, Clock
from .transport import Transport, TransportError

ISO_BAUD = 10400
INIT_ADDRESS = 0x33
BIT_S = 0.2                    # one bit at 5 baud
W5_S = 0.3                     # bus idle before an init
P3_MIN_S = 0.055               # end of reply -> next request
P3_MAX_S = 5.0                 # session timeout
KEEPALIVE_S = 4.0
HEADER_9141 = b"\x68\x6a\xf1"
KWP_START = bytes.fromhex("c133f18166")

CLEAR_CONFIRM = "CLEAR"
CLEAR_WARNING = (
    "Clearing trouble codes (OBD2 Mode 04) also erases the freeze frame and resets "
    "every readiness monitor to 'not ready'. The car will not pass an emissions "
    "inspection until the monitors have run again, which takes several days of "
    "normal driving. The ECU may also forget its fuel trims and idle learning. "
    "Do this with the key ON and the engine OFF, and write the codes down first.")


class Iso9141Error(Exception):
    """Base class for ISO 9141-2 / KWP2000 problems."""


class InitError(Iso9141Error):
    """The wake-up handshake did not complete."""


class NoResponse(Iso9141Error):
    """The ECU did not answer a request (it may not support it)."""


class NegativeResponse(Iso9141Error):
    """KWP2000 negative response (7F ...)."""


class ConfirmationRequired(Iso9141Error):
    """A destructive service was called without its confirmation."""


@dataclass
class InitResult:
    method: str                          # "5-baud" or "fast"
    protocol: str                        # "ISO 9141-2" or "ISO 14230-4 (KWP2000)"
    key_bytes: tuple[int, int]
    w1_s: float = 0.0                    # measured: end of address -> sync byte
    garbage: int = 0                     # stray bytes skipped before the sync
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{self.protocol} via {self.method} init, key bytes "
                f"{self.key_bytes[0]:02X} {self.key_bytes[1]:02X}")


def checksum(data: bytes) -> int:
    """ISO 9141-2 / KWP2000 checksum: plain sum modulo 256."""
    return sum(data) & 0xFF


# Mode 01 PID -> number of data bytes (for splitting replies that carry no length)
_PID_LEN = {0x00: 4, 0x01: 4, 0x02: 2, 0x03: 2, 0x04: 1, 0x05: 1, 0x06: 1, 0x07: 1, 0x08: 1,
            0x09: 1, 0x0A: 1, 0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0E: 1, 0x0F: 1, 0x10: 2, 0x11: 1,
            0x12: 1, 0x13: 1, 0x14: 2, 0x15: 2, 0x16: 2, 0x17: 2, 0x18: 2, 0x19: 2, 0x1A: 2,
            0x1B: 2, 0x1C: 1, 0x1D: 1, 0x1E: 1, 0x1F: 2, 0x20: 4, 0x21: 2, 0x40: 4}


def _likely_len(buf: bytes, i: int) -> int | None:
    """Total length an ISO 9141-2 message starting at ``i`` most likely has."""
    if i + 4 > len(buf):
        return None
    mode = buf[i + 3]
    if mode in (0x43, 0x47):
        return 11
    if mode == 0x44:
        return 5
    if mode == 0x41 and i + 5 <= len(buf) and buf[i + 4] in _PID_LEN:
        return 3 + 2 + _PID_LEN[buf[i + 4]] + 1
    if mode == 0x42 and i + 5 <= len(buf) and buf[i + 4] in _PID_LEN:
        return 3 + 3 + _PID_LEN[buf[i + 4]] + 1
    return None


def split_9141(buf: bytes) -> list[bytes]:
    """Cut a run of ISO 9141-2 replies (48 6B xx ... sum) into payloads.

    The messages carry no length, so the cuts are found by checksum, trying
    the length the mode and PID imply first. Returns the payloads (mode byte
    onward) of every message that checks out; damaged bytes are skipped.
    """
    def walk(i: int) -> list[bytes] | None:
        if i == len(buf):
            return []
        if buf[i] != 0x48 or i + 5 > len(buf) or buf[i + 1] != 0x6B:
            return None
        likely = _likely_len(buf, i)
        lengths = ([likely] if likely else []) + [n for n in range(5, 12) if n != likely]
        for n in lengths:
            if i + n <= len(buf) and checksum(buf[i:i + n - 1]) == buf[i + n - 1]:
                rest = walk(i + n)
                if rest is not None:
                    return [bytes(buf[i + 3:i + n - 1])] + rest
        return None

    whole = walk(0)
    if whole is not None:
        return whole
    out: list[bytes] = []                   # salvage what checks out
    i = 0
    while i + 5 <= len(buf):
        if buf[i] == 0x48 and buf[i + 1] == 0x6B:
            likely = _likely_len(buf, i)
            lengths = ([likely] if likely else []) + [n for n in range(5, 12) if n != likely]
            for n in lengths:
                if i + n <= len(buf) and checksum(buf[i:i + n - 1]) == buf[i + n - 1]:
                    out.append(bytes(buf[i + 3:i + n - 1]))
                    i += n - 1
                    break
        i += 1
    return out


def split_kwp(buf: bytes) -> list[bytes]:
    """Cut a run of KWP2000 replies (fmt tgt src data sum) into payloads."""
    out: list[bytes] = []
    i = 0
    while i + 4 <= len(buf):
        fmt = buf[i]
        n = fmt & 0x3F
        head = 3 if fmt & 0x80 else 1
        if n == 0:
            if i + head >= len(buf):
                break
            n = buf[i + head]
            head += 1
        end = i + head + n
        if fmt & 0x80 and n and end < len(buf) and checksum(buf[i:end]) == buf[end]:
            out.append(bytes(buf[i + head:end]))
            i = end + 1
        else:
            i += 1
    return out


class Iso9141Link:
    """One tester on the K-line speaking ISO 9141-2 (or KWP2000 after a fast
    init). Use from one thread only; nothing else may use the port meanwhile."""

    def __init__(self, transport: Transport, clock: Clock = REAL, honda_baud: int = 9600,
                 trace: Callable[[str], None] | None = None) -> None:
        self.transport = transport
        self.clock = clock
        self.honda_baud = honda_baud
        self.trace = trace
        self.init: InitResult | None = None
        self.kwp = False
        self.requests = 0
        self.reinits = 0
        self._last_activity = float("-inf")
        self._next_tx = 0.0

    # -- small helpers --

    def _note(self, text: str) -> None:
        if self.trace is not None:
            self.trace(text)

    def _sleep_until(self, t: float) -> None:
        self.clock.sleep(t - self.clock.now())

    def _read_byte(self, timeout: float) -> int | None:
        data = self.transport.read_some(1, max(0.0, timeout))
        return data[0] if data else None

    @property
    def connected(self) -> bool:
        return self.init is not None and self.clock.now() - self._last_activity < P3_MAX_S

    # -- wake-up --

    def connect(self, allow_fast: bool = True) -> InitResult:
        """5-baud init; if that fails and ``allow_fast``, try the fast init."""
        try:
            return self.slow_init()
        except InitError as slow:
            if not allow_fast:
                raise
            self._note(f"5-baud init failed ({slow}); trying fast init")
            try:
                return self.fast_init()
            except InitError as fast:
                raise InitError(f"5-baud init: {slow}; fast init: {fast}") from fast

    def slow_init(self, address: int = INIT_ADDRESS) -> InitResult:
        """ISO 9141-2 5-baud init by break toggling. Takes about 2.6 s."""
        t, clock = self.transport, self.clock
        self.init = None
        t.set_break(False)
        t.set_baud(ISO_BAUD)
        clock.sleep(W5_S + 0.05)                     # bus idle
        t.flush_input()
        bits = [0] + [(address >> i) & 1 for i in range(8)] + [1]
        self._note(f"5-baud init, address {address:02X}: " + "".join(map(str, bits)))
        t0 = clock.now()
        low = False
        try:
            for k, bit in enumerate(bits):
                if (bit == 0) != low:
                    self._sleep_until(t0 + k * BIT_S)
                    low = bit == 0
                    t.set_break(low)
        finally:
            if low:
                t.set_break(False)                   # never leave the line held low
        t_end = t0 + len(bits) * BIT_S
        # The breaks come back as framing garbage. Drop it late in the stop bit.
        self._sleep_until(t_end - 0.03)
        t.flush_input()

        garbage = 0
        deadline = t_end + 0.3 + 0.2                 # W1 max + margin
        while True:
            b = self._read_byte(deadline - clock.now())
            if b is None:
                raise InitError("no sync byte (0x55) after the address: the ECU did not "
                                "answer the 5-baud init")
            if b == 0x55:
                break
            garbage += 1
            if garbage > 16:
                raise InitError("only garbage after the address, no 0x55 sync "
                                "(wrong baud rate, or a cable that cannot do 10400)")
        w1 = clock.now() - t_end
        kb1 = self._read_byte(0.02 + 0.1)
        kb2 = self._read_byte(0.02 + 0.1) if kb1 is not None else None
        if kb1 is None or kb2 is None:
            raise InitError("sync byte seen but the key bytes did not follow")
        clock.sleep(0.03)                            # W4: 25-50 ms
        t.write(bytes([~kb2 & 0xFF]))
        want = ~address & 0xFF
        deadline = clock.now() + 0.05 + 0.15
        seen = bytearray()
        while want not in seen[-1:]:
            b = self._read_byte(deadline - clock.now())
            if b is None:
                raise InitError(f"key bytes {kb1:02X} {kb2:02X} seen, but the ECU did not "
                                f"confirm with {want:02X} (got {seen.hex(' ') or 'nothing'})")
            seen.append(b)
        self.kwp = kb2 == 0x8F
        result = InitResult("5-baud", "ISO 14230-4 (KWP2000)" if self.kwp else "ISO 9141-2",
                            (kb1, kb2), w1_s=w1, garbage=garbage)
        if (kb1, kb2) not in ((0x08, 0x08), (0x94, 0x94)) and not self.kwp:
            result.notes.append(f"unusual key bytes {kb1:02X} {kb2:02X}; treated as ISO 9141-2")
        return self._established(result)

    def fast_init(self) -> InitResult:
        """ISO 14230 fast init: 25 ms low, 25 ms high, StartCommunication."""
        t, clock = self.transport, self.clock
        self.init = None
        t.set_break(False)
        t.set_baud(ISO_BAUD)
        clock.sleep(W5_S + 0.05)
        t.flush_input()
        self._note("fast init: 25 ms low, 25 ms high, C1 33 F1 81 66")
        t0 = clock.now()
        t.set_break(True)
        try:
            self._sleep_until(t0 + 0.025)
        finally:
            t.set_break(False)
        self._sleep_until(t0 + 0.050)
        t.flush_input()
        t.write(KWP_START)
        buf = self._collect(len(KWP_START), first_timeout=0.3)
        if buf.startswith(KWP_START):
            buf = buf[len(KWP_START):]
        replies = [p for p in split_kwp(buf) if p[:1] == b"\xc1"]
        if not replies:
            raise InitError("no StartCommunication response (this ECU is probably "
                            "ISO 9141-2 only, which needs the 5-baud init)")
        kb = replies[0][1:3].ljust(2, b"\x00")
        self.kwp = True
        return self._established(InitResult("fast", "ISO 14230-4 (KWP2000)", (kb[0], kb[1])))

    def _established(self, result: InitResult) -> InitResult:
        now = self.clock.now()
        self.init = result
        self._last_activity = now
        self._next_tx = now + P3_MIN_S + 2 * self.transport.usb_latency_s
        self._note(f"session up: {result}")
        return result

    def close(self) -> None:
        """Leave the ISO session and put the port back to the Honda baud rate.

        There is no "stop" message in ISO 9141-2: the ECU ends the session by
        itself after 5 s without a request.
        """
        self.init = None
        try:
            self.transport.set_break(False)
        except TransportError:
            pass
        self.transport.set_baud(self.honda_baud)
        self.transport.flush_input()

    # -- messages --

    def _frame(self, payload: bytes) -> bytes:
        if self.kwp:
            body = bytes([0xC0 | len(payload), 0x33, 0xF1]) + payload
        else:
            body = HEADER_9141 + payload
        return body + bytes([checksum(body)])

    def _collect(self, echo_len: int, first_timeout: float) -> bytes:
        """Read until the ECU has been quiet for longer than P2max."""
        clock, read_some = self.clock, self.transport.read_some
        quiet = 0.06 + 2 * self.transport.usb_latency_s
        buf = bytearray()
        deadline = clock.now() + echo_len * 10.0 / ISO_BAUD + first_timeout
        while True:
            now = clock.now()
            if now >= deadline:
                break
            chunk = read_some(64, deadline - now)
            if chunk:
                buf += chunk
                if len(buf) > echo_len:
                    deadline = clock.now() + quiet
        return bytes(buf)

    def request(self, payload: bytes, retries: int = 1) -> list[bytes]:
        """Send one service request; return the reply payloads (mode byte first).

        Raises NoResponse if nothing valid comes back. Re-runs the init first
        if the session has been idle for too long to still be alive.
        """
        if self.init is None:
            raise Iso9141Error("no ISO session: call connect() first")
        clock = self.clock
        attempt = 0
        while True:
            attempt += 1
            if clock.now() - self._last_activity > P3_MAX_S - 0.5:
                self._note("session idle too long, re-initialising")
                self.reinits += 1
                self.connect(allow_fast=self.init is None or self.init.method == "fast")
            wait = self._next_tx - clock.now()
            if wait > 0:
                clock.sleep(wait)
            frame = self._frame(bytes(payload))
            self.transport.flush_input()
            self.transport.write(frame)
            self.requests += 1
            buf = self._collect(len(frame), first_timeout=0.25)
            now = clock.now()
            self._next_tx = now       # the quiet wait in _collect already covers P3min
            if buf.startswith(frame):
                buf = buf[len(frame):]
            replies = split_kwp(buf) if self.kwp else split_9141(buf)
            if replies and replies[0][:1] == b"\x7f":
                self._last_activity = now
                raise NegativeResponse(f"negative response {replies[0].hex(' ')} "
                                       f"to {bytes(payload).hex(' ')}")
            good = [r for r in replies if r and r[0] == (payload[0] + 0x40) & 0xFF]
            if good:
                self._last_activity = now
                return good
            if attempt > retries:
                raise NoResponse(f"no reply to {bytes(payload).hex(' ')}"
                                 + (f" (got {buf.hex(' ')})" if buf else ""))

    def keepalive(self) -> bool:
        """Keep the session open: sends Mode 01 PID 00 if it has been idle for
        4 s. Returns True if a message was sent."""
        if self.init is None or self.clock.now() - self._last_activity < KEEPALIVE_S:
            return False
        self.request(b"\x01\x00")
        return True


# ---- services ---------------------------------------------------------------

@dataclass(frozen=True)
class PidValue:
    pid: int
    name: str
    value: float | None
    units: str
    raw: bytes
    text: str = ""

    def __str__(self) -> str:
        if self.text:
            return f"{self.name}: {self.text}"
        if self.value is None:
            return f"{self.name}: {self.raw.hex(' ')}"
        return f"{self.name}: {self.value:g} {self.units}".rstrip()


_FUEL_STATUS = {0: "not reported", 1: "open loop (not yet warm)", 2: "closed loop",
                4: "open loop (load or decel fuel cut)", 8: "open loop (system fault)",
                16: "closed loop, but a fault in an O2 sensor"}
_OBD_STD = {1: "OBD-II (CARB)", 2: "OBD (EPA)", 3: "OBD and OBD-II", 4: "OBD-I",
            5: "not OBD compliant", 6: "EOBD", 7: "EOBD and OBD-II"}


def _trim(a: int) -> float:
    return round((a - 128) * 100 / 128, 1)


# pid -> (name, units, decoder(bytes) -> value or text)
_PIDS: dict[int, tuple[str, str, Callable[[bytes], float | str]]] = {
    0x03: ("Fuel system status", "", lambda d: _FUEL_STATUS.get(d[0], f"0x{d[0]:02X}")),
    0x04: ("Calculated load", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x05: ("Coolant temperature", "°C", lambda d: d[0] - 40),
    0x06: ("Short term fuel trim B1", "%", lambda d: _trim(d[0])),
    0x07: ("Long term fuel trim B1", "%", lambda d: _trim(d[0])),
    0x08: ("Short term fuel trim B2", "%", lambda d: _trim(d[0])),
    0x09: ("Long term fuel trim B2", "%", lambda d: _trim(d[0])),
    0x0A: ("Fuel pressure", "kPa", lambda d: d[0] * 3),
    0x0B: ("Intake manifold pressure", "kPa", lambda d: d[0]),
    0x0C: ("Engine speed", "rpm", lambda d: (d[0] * 256 + d[1]) / 4),
    0x0D: ("Vehicle speed", "km/h", lambda d: d[0]),
    0x0E: ("Ignition advance", "° BTDC", lambda d: d[0] / 2 - 64),
    0x0F: ("Intake air temperature", "°C", lambda d: d[0] - 40),
    0x10: ("Mass air flow", "g/s", lambda d: (d[0] * 256 + d[1]) / 100),
    0x11: ("Throttle position", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x13: ("O2 sensors present", "", lambda d: " ".join(
        f"B{1 + i // 4}S{1 + i % 4}" for i in range(8) if d[0] >> i & 1) or "none"),
    0x1C: ("OBD standard", "", lambda d: _OBD_STD.get(d[0], f"0x{d[0]:02X}")),
    0x1F: ("Run time since start", "s", lambda d: d[0] * 256 + d[1]),
    0x21: ("Distance with MIL on", "km", lambda d: d[0] * 256 + d[1]),
}
for _i in range(8):
    _PIDS[0x14 + _i] = (f"O2 sensor B{1 + _i // 4}S{1 + _i % 4}", "V",
                        lambda d: round(d[0] / 200, 3))

_MONITORS_B = ((0, "misfire"), (1, "fuel system"), (2, "comprehensive components"))
_MONITORS_CD = ((0, "catalyst"), (1, "heated catalyst"), (2, "evaporative system"),
                (3, "secondary air"), (4, "A/C refrigerant"), (5, "O2 sensor"),
                (6, "O2 sensor heater"), (7, "EGR"))


@dataclass
class Readiness:
    mil_on: bool
    dtc_count: int
    monitors: dict[str, bool]            # supported monitors -> complete?
    raw: bytes = b""

    @property
    def ready(self) -> bool:
        return all(self.monitors.values())

    @property
    def incomplete(self) -> list[str]:
        return [name for name, done in self.monitors.items() if not done]

    def __str__(self) -> str:
        state = "all monitors ready" if self.ready else "not ready: " + ", ".join(self.incomplete)
        return f"MIL {'ON' if self.mil_on else 'off'}, {self.dtc_count} stored DTC(s), {state}"


def decode_readiness(data: bytes) -> Readiness:
    """Mode 01 PID 01: MIL, DTC count and monitor readiness."""
    a, b, c, d = data[:4]
    monitors: dict[str, bool] = {}
    for bit, name in _MONITORS_B:
        if b >> bit & 1:
            monitors[name] = not b >> (bit + 4) & 1
    for bit, name in _MONITORS_CD:
        if c >> bit & 1:
            monitors[name] = not d >> bit & 1
    return Readiness(bool(a & 0x80), a & 0x7F, monitors, bytes(data[:4]))


def decode_dtc(hi: int, lo: int) -> str:
    """Two DTC bytes -> 'P0135'."""
    return f"{'PCBU'[hi >> 6]}{(hi >> 4) & 3}{hi & 0xF:X}{lo >> 4:X}{lo & 0xF:X}"


def decode_pid(pid: int, data: bytes) -> PidValue:
    entry = _PIDS.get(pid)
    if entry is None:
        return PidValue(pid, f"PID {pid:02X}", None, "", bytes(data))
    name, units, fn = entry
    try:
        value = fn(data)
    except IndexError:
        return PidValue(pid, name, None, units, bytes(data), text="short reply")
    if isinstance(value, str):
        return PidValue(pid, name, None, units, bytes(data), text=value)
    return PidValue(pid, name, float(value), units, bytes(data))


@dataclass
class Dtc:
    code: str
    description: str
    status: str = "stored"               # "stored" or "pending"

    def __str__(self) -> str:
        return f"{self.code}  {self.description}" + ("  (pending)" if self.status == "pending" else "")


@dataclass
class FreezeFrame:
    dtc: Dtc | None
    values: dict[int, PidValue] = field(default_factory=dict)


@dataclass
class Obd2Snapshot:
    """Everything worth saving from one ISO session (log header, report)."""

    init: str = ""
    readiness: Readiness | None = None
    stored: list[Dtc] = field(default_factory=list)
    pending: list[Dtc] = field(default_factory=list)
    freeze_frame: FreezeFrame | None = None
    supported_pids: list[int] = field(default_factory=list)
    values: dict[int, PidValue] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = ["OBD2 snapshot", f"  link: {self.init or 'unknown'}"]
        if self.readiness is not None:
            lines.append(f"  status: {self.readiness}")
            for name, done in self.readiness.monitors.items():
                lines.append(f"    {name:<26} {'ready' if done else 'NOT READY'}")
        for title, codes in (("stored codes", self.stored), ("pending codes", self.pending)):
            lines.append(f"  {title}: " + ("none" if not codes else ""))
            lines.extend(f"    {dtc.code}  {dtc.description}" for dtc in codes)
        if self.freeze_frame is not None and self.freeze_frame.dtc is not None:
            lines.append(f"  freeze frame for {self.freeze_frame.dtc.code}:")
            lines.extend(f"    {v}" for v in self.freeze_frame.values.values())
        if self.values:
            lines.append("  live values (supported PIDs: "
                         + " ".join(f"{p:02X}" for p in self.supported_pids) + "):")
            lines.extend(f"    {v}" for v in self.values.values())
        lines.extend(f"  ! {e}" for e in self.errors)
        return "\n".join(lines) + "\n"


class Obd2Session:
    """OBD2 services on an initialised ``Iso9141Link``."""

    CLEAR_CONFIRM = CLEAR_CONFIRM
    CLEAR_WARNING = CLEAR_WARNING

    def __init__(self, link: Iso9141Link) -> None:
        self.link = link
        self._supported: dict[int, list[int]] = {}

    # -- Mode 01 / 02 --

    def supported_pids(self, mode: int = 1) -> list[int]:
        """PIDs the ECU says it supports (walks the 00, 20, 40.. bitmaps)."""
        if mode in self._supported:
            return list(self._supported[mode])
        found: list[int] = []
        base = 0
        while base <= 0xC0:
            try:
                data = self._pid_data(mode, base)
            except NoResponse:
                break
            bits = int.from_bytes(data[:4].ljust(4, b"\x00"), "big")
            found.extend(base + n for n in range(1, 33) if bits >> (32 - n) & 1)
            if not bits & 1:
                break
            base += 0x20
        self._supported[mode] = found
        return list(found)

    def _pid_data(self, mode: int, pid: int, frame: int = 0) -> bytes:
        request = bytes([mode, pid]) + (bytes([frame]) if mode == 2 else b"")
        skip = len(request)
        for reply in self.link.request(request):
            if reply[1:skip] == request[1:]:
                return reply[skip:]
        raise NoResponse(f"reply to mode {mode:02X} PID {pid:02X} did not match the request")

    def read_pid(self, pid: int, mode: int = 1, frame: int = 0) -> PidValue:
        """One PID, decoded. Raises NoResponse if the ECU does not answer it."""
        return decode_pid(pid, self._pid_data(mode, pid, frame))

    def read_pids(self, pids: list[int] | None = None) -> dict[int, PidValue]:
        """Every supported live PID (or the given ones). Skips the bitmaps,
        the monitor status and the freeze-frame DTC, which have their own calls."""
        if pids is None:
            pids = [p for p in self.supported_pids() if p not in (0x01, 0x02) and p % 0x20]
        out: dict[int, PidValue] = {}
        for pid in pids:
            try:
                out[pid] = self.read_pid(pid)
            except NoResponse:
                continue
        return out

    def readiness(self) -> Readiness:
        """MIL state, number of stored codes, readiness monitors (PID 01)."""
        return decode_readiness(self._pid_data(1, 0x01))

    def read_freeze_frame(self, frame: int = 0) -> FreezeFrame | None:
        """Mode 02: the snapshot stored with the first emissions DTC, or None."""
        try:
            data = self._pid_data(2, 0x02, frame)
        except NoResponse:
            return None
        if len(data) < 2 or data[:2] == b"\x00\x00":
            return None
        out = FreezeFrame(self._dtc(data[0], data[1], "stored"))
        for pid in self.supported_pids(mode=2):
            if pid in (0x01, 0x02) or pid % 0x20 == 0:
                continue
            try:
                out.values[pid] = self.read_pid(pid, mode=2, frame=frame)
            except NoResponse:
                continue
        return out

    # -- Mode 03 / 07 / 04 --

    @staticmethod
    def _dtc(hi: int, lo: int, status: str) -> Dtc:
        from .diag.dtc import describe
        code = decode_dtc(hi, lo)
        return Dtc(code, describe(code), status)

    def _dtcs(self, mode: int, status: str) -> list[Dtc]:
        try:
            replies = self.link.request(bytes([mode]))
        except NoResponse:
            return []               # many ECUs stay silent when there is nothing to report
        out: list[Dtc] = []
        for reply in replies:
            body = reply[1:]
            for i in range(0, len(body) - 1, 2):
                if body[i] or body[i + 1]:
                    dtc = self._dtc(body[i], body[i + 1], status)
                    if dtc.code not in [d.code for d in out]:
                        out.append(dtc)
        return out

    def read_dtcs(self) -> list[Dtc]:
        """Mode 03: stored (confirmed) trouble codes."""
        return self._dtcs(0x03, "stored")

    def read_pending_dtcs(self) -> list[Dtc]:
        """Mode 07: codes seen once, not yet confirmed."""
        return self._dtcs(0x07, "pending")

    def clear_dtcs(self, confirm: str = "", allow_engine_running: bool = False) -> bool:
        """Mode 04: erase codes, freeze frame and readiness. Destructive.

        Does nothing unless ``confirm == Obd2Session.CLEAR_CONFIRM`` ("CLEAR").
        Show ``CLEAR_WARNING`` to the user before passing it. Refuses while
        the engine is turning unless ``allow_engine_running``. Returns True
        when the ECU acknowledged.
        """
        if confirm != CLEAR_CONFIRM:
            raise ConfirmationRequired(
                f"clear_dtcs needs confirm={CLEAR_CONFIRM!r}. " + CLEAR_WARNING)
        if not allow_engine_running:
            try:
                rpm = self.read_pid(0x0C).value or 0.0
            except NoResponse:
                rpm = 0.0
            if rpm > 0:
                raise Iso9141Error(f"engine is running ({rpm:.0f} rpm): clear codes with the "
                                   "key ON and the engine OFF")
        try:
            self.link.request(b"\x04", retries=0)
        except NoResponse:
            return False
        return True

    # -- everything at once --

    def snapshot(self, live: bool = True) -> Obd2Snapshot:
        """Readiness, codes, freeze frame and (optionally) every live PID."""
        snap = Obd2Snapshot(init=str(self.link.init) if self.link.init else "")
        steps: list[tuple[str, Callable[[], None]]] = [
            ("supported PIDs", lambda: setattr(snap, "supported_pids", self.supported_pids())),
            ("readiness", lambda: setattr(snap, "readiness", self.readiness())),
            ("stored codes", lambda: setattr(snap, "stored", self.read_dtcs())),
            ("pending codes", lambda: setattr(snap, "pending", self.read_pending_dtcs())),
            ("freeze frame", lambda: setattr(snap, "freeze_frame", self.read_freeze_frame())),
        ]
        if live:
            steps.append(("live values", lambda: setattr(snap, "values", self.read_pids())))
        for name, step in steps:
            try:
                step()
            except Iso9141Error as exc:
                snap.errors.append(f"{name}: {exc}")
        return snap
