"""Byte transports (real serial port, in-process simulator) and the Link that
runs request/reply exchanges over them.

Layers:

``Transport``   bytes in, bytes out, plus baud and break control (the last two
                are what the ISO 9141-2 5-baud init needs).
``Link``        Honda read requests with adaptive timeouts, echo checking,
                resync, retry with backoff, counters and frame timestamps.
                Refuses to send anything but read commands until unlocked.
"""

from __future__ import annotations

import errno
import os
import select
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import protocol
from .link.clock import REAL, Clock
from .link.timing import AdaptiveTiming, LinkStats
from .protocol import Framing

if TYPE_CHECKING:
    from .sim import EcuModel

FTDI_VID, FTDI_PID = 0x0403, 0x6001
UDEV_SYMLINK = "/dev/hondalog"
SIM_SYMLINK = "/tmp/hondalog-sim"
BUILTIN_SIM = "sim:"
BUILTIN_SIM_FLAKY = "sim:flaky"
BUILTIN_SIM_ISO = "sim:iso"

DEFAULT_USB_LATENCY_S = 0.016      # FTDI default latency_timer when we could not lower it
LOW_USB_LATENCY_S = 0.001


class TransportError(Exception):
    """The port itself failed (unplugged, permission denied, ...)."""


class CommandBlocked(Exception):
    """A frame that is not a read was stopped by the read-only guard."""


class Transport:
    name = "?"
    #: how long the adapter may sit on received bytes before handing them over
    usb_latency_s = 0.0
    baud = 9600

    def open(self) -> None: ...
    def close(self) -> None: ...
    def write(self, data: bytes) -> None: ...

    def read(self, size: int, timeout: float) -> bytes:
        """Up to ``size`` bytes; waits up to ``timeout`` for all of them."""
        return b""

    def read_some(self, size: int, timeout: float) -> bytes:
        """Up to ``size`` bytes, returning as soon as any have arrived."""
        return self.read(size, timeout)

    def flush_input(self) -> None: ...

    def set_baud(self, baud: int) -> None:
        raise TransportError(f"{self.name}: cannot change the baud rate")

    def set_break(self, on: bool) -> None:
        """Hold the line low (True) or release it (False)."""
        raise TransportError(f"{self.name}: cannot send a break")


class SerialTransport(Transport):
    """A tty: the KKL cable, or the pty that hondalog-sim creates."""

    def __init__(self, port: str, baud: int = 9600) -> None:
        self.name = port
        self.baud = baud
        self.usb_latency_s = DEFAULT_USB_LATENCY_S
        self._ser: Any = None
        self._fd = -1
        self._usb_id: tuple[int, int, str | None] | None = None

    def open(self) -> None:
        import serial
        port = self.name
        if not os.path.exists(port):
            port = self._find_again() or port
        try:
            self._ser = serial.Serial(port, self.baud, bytesize=8, parity="N",
                                      stopbits=1, timeout=0.2, write_timeout=1.0)
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportError(str(exc)) from exc
        try:
            self._fd = self._ser.fileno()
        except Exception:
            self._fd = -1
        self.usb_latency_s = self._lower_latency(port)
        if self._usb_id is None:
            self._usb_id = _usb_identity(port)

    def _lower_latency(self, port: str) -> float:
        """Cut the FTDI receive latency from 16 ms to 1 ms. Returns the result."""
        from .diag import ftdi
        try:  # ASYNC_LOW_LATENCY: ftdi_sio answers by setting latency_timer = 1
            self._ser.set_low_latency_mode(True)
        except Exception:
            pass
        ms = ftdi.read_latency_ms(port)
        if ms is None:          # a pty or a non-FTDI adapter: nothing is held back
            if ftdi.is_windows():
                # the registry could not be read: an FTDI cable is then most
                # likely on its 16 ms default, so do not claim 1 ms
                p = ftdi.find_comport(port)
                if p is not None and p.vid == ftdi.FTDI_VID:
                    return DEFAULT_USB_LATENCY_S
            return 0.0 if _is_pty(port) else LOW_USB_LATENCY_S
        if ms > 1 and ftdi.write_latency_ms(port, 1):
            ms = 1
        return ms / 1000.0

    def _find_again(self) -> str | None:
        """After a replug the cable may come back as another ttyUSBn."""
        if self._usb_id is None:
            return None
        try:
            from serial.tools import list_ports as lp
            for p in lp.comports():
                if (p.vid, p.pid, p.serial_number) == self._usb_id:
                    return str(p.device)
        except Exception:
            pass
        return None

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            self._fd = -1

    def write(self, data: bytes) -> None:
        import serial
        try:
            self._ser.write(data)
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(str(exc)) from exc

    def read(self, size: int, timeout: float) -> bytes:
        import serial
        try:
            self._ser.timeout = max(0.0, timeout)
            return bytes(self._ser.read(size))
        except (serial.SerialException, OSError, AttributeError, TypeError) as exc:
            raise TransportError(str(exc)) from exc

    def read_some(self, size: int, timeout: float) -> bytes:
        fd = self._fd
        if fd < 0:
            data = self.read(1, timeout)
            return data
        try:
            ready, _, _ = select.select([fd], [], [], max(0.0, timeout))
            if not ready:
                return b""
            data = os.read(fd, size)
        except InterruptedError:
            return b""
        except (OSError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno == errno.EAGAIN:
                return b""
            raise TransportError(str(exc)) from exc
        if not data:
            raise TransportError("the port reports data but returns none (cable unplugged?)")
        return data

    def flush_input(self) -> None:
        import serial
        try:
            self._ser.reset_input_buffer()
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(str(exc)) from exc

    def set_baud(self, baud: int) -> None:
        import serial
        try:
            self._ser.flush()
            self._ser.baudrate = baud
        except (serial.SerialException, OSError, AttributeError, ValueError) as exc:
            raise TransportError(f"cannot set {baud} baud: {exc}") from exc
        self.baud = baud

    def set_break(self, on: bool) -> None:
        import serial
        try:
            self._ser.break_condition = bool(on)
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(f"cannot set break: {exc}") from exc


def _is_pty(port: str) -> bool:
    try:
        return os.path.realpath(port).startswith("/dev/pts/")
    except OSError:
        return False


def _usb_identity(port: str) -> tuple[int, int, str | None] | None:
    try:
        from serial.tools import list_ports as lp
        real = os.path.realpath(port)
        for p in lp.comports():
            if (p.device == real or str(p.device).upper() == port.upper()) and p.vid is not None:
                return (p.vid, p.pid, p.serial_number)
    except Exception:
        pass
    return None


class LoopbackTransport(Transport):
    """The simulated ECU in the same process, with a model of the wire.

    ``byte_timing=False``: bytes come back at once (unit tests).
    ``byte_timing=True``: true 9600-baud timing. Every byte, echo included,
    becomes readable 10 bit times after the one before it, the ECU's reply
    starts ``model.turnaround_s`` after the last request byte, and the adapter
    hands bytes over on ``usb_latency_s`` ticks like an FTDI latency timer.

    With a ``VirtualClock`` waiting costs no real time (benchmarks, tests).
    Break and baud changes reach the model, so the ISO 9141-2 5-baud init works.
    """

    name = BUILTIN_SIM

    def __init__(self, model: "EcuModel | None" = None, clock: Clock = REAL,
                 byte_timing: bool = False, usb_latency_s: float | None = None) -> None:
        if model is None:
            from .sim import EcuModel
            model = EcuModel(clock=clock.now)
        self.model = model
        self.clock = clock
        self.byte_timing = byte_timing
        if usb_latency_s is None:
            usb_latency_s = LOW_USB_LATENCY_S if byte_timing else 0.0
        self.usb_latency_s = usb_latency_s
        self.baud = model.framing.baud
        self._rx: list[tuple[float, int]] = []      # (readable at, byte), time order
        self._wire_free = 0.0                       # when our last TX byte ends
        self._break_since: float | None = None
        self._is_open = True            # usable straight away; open() resets it

    # -- helpers --

    def _check(self) -> None:
        if self.model.unplugged(self.clock.now()):
            self._is_open = False
            raise TransportError("simulated cable unplugged")
        if not self._is_open:
            raise TransportError("port is not open")

    def _deliver(self, t: float) -> float:
        """When a byte that finished arriving at ``t`` becomes readable."""
        tick = self.usb_latency_s
        if tick <= 0:
            return t
        return (int(t / tick - 1e-9) + 1) * tick

    def _queue(self, start: float, data: bytes, byte_s: float) -> float:
        t = start
        for b in data:
            t += byte_s
            self._rx.append((self._deliver(t) if self.byte_timing else 0.0, b))
        return t

    # -- Transport --

    def open(self) -> None:
        if self.model.unplugged(self.clock.now()):
            raise TransportError("simulated cable unplugged: no such device")
        self._rx.clear()
        self._wire_free = 0.0
        self._break_since = None
        self._is_open = True
        self.baud = self.model.framing.baud

    def close(self) -> None:
        self._is_open = False

    def write(self, data: bytes) -> None:
        self._check()
        now = self.clock.now()
        byte_s = 10.0 / self.baud if self.byte_timing else 0.0
        start = max(now, self._wire_free)
        end = start + byte_s * len(data)
        self._wire_free = end
        model = self.model
        if model.cable_dead:
            return
        if model.echo:
            self._queue(start, model.echo_of(data), byte_s)
        for reply in model.on_bytes(bytes(data), self.baud, end):
            rb = 10.0 / reply.baud if self.byte_timing else 0.0
            t = end + (reply.delay_s if self.byte_timing else 0.0)
            for chunk in reply.chunks:
                t = self._queue(t + (chunk.gap_s if self.byte_timing else 0.0), chunk.data, rb)
        self._rx.sort(key=lambda item: item[0])

    def read(self, size: int, timeout: float) -> bytes:
        out = bytearray()
        deadline = self.clock.now() + max(0.0, timeout)
        while len(out) < size:
            remaining = deadline - self.clock.now()
            chunk = self.read_some(size - len(out), max(0.0, remaining))
            out += chunk
            if not chunk and remaining <= 0:
                break
        return bytes(out)

    def read_some(self, size: int, timeout: float) -> bytes:
        self._check()
        clock = self.clock
        now = clock.now()
        rx = self._rx
        if not rx or rx[0][0] > now:
            wake = rx[0][0] if rx else float("inf")
            if wake > now + timeout:
                # Nothing in time. Without byte timing still give the caller's
                # loop something to wait on, as the old loopback did.
                clock.sleep(timeout if self.byte_timing else min(timeout, 0.02))
                return b""
            clock.sleep(wake - now)
            now = max(clock.now(), wake)
        n = 0
        while n < len(rx) and n < size and rx[n][0] <= now:
            n += 1
        out = bytes(b for _, b in rx[:n])
        del rx[:n]
        return out

    def flush_input(self) -> None:
        self._check()
        now = self.clock.now()
        self._rx = [item for item in self._rx if item[0] > now]

    def set_baud(self, baud: int) -> None:
        self._check()
        self.baud = baud

    def set_break(self, on: bool) -> None:
        self._check()
        now = self.clock.now()
        if on and self._break_since is None:
            self._break_since = now
        elif not on and self._break_since is not None:
            start, self._break_since = self._break_since, None
            if self.model.cable_dead:
                return
            for reply in self.model.on_break(start, now):
                rb = 10.0 / reply.baud
                t = now + reply.delay_s
                for chunk in reply.chunks:
                    t += chunk.gap_s
                    for b in chunk.data:
                        t += rb
                        self._rx.append((self._deliver(t), b))
            self._rx.sort(key=lambda item: item[0])


@dataclass
class PortInfo:
    device: str
    label: str
    is_ftdi: bool = False


def list_ports() -> list[PortInfo]:
    """Candidate ports, best guess first."""
    ports: list[PortInfo] = []
    seen: set[str] = set()

    def add(device: str, label: str, is_ftdi: bool = False) -> None:
        if device not in seen:
            seen.add(device)
            ports.append(PortInfo(device, label, is_ftdi))

    if os.path.exists(UDEV_SYMLINK):
        add(UDEV_SYMLINK, f"{UDEV_SYMLINK}  (KKL cable, udev link)", True)
    try:
        from serial.tools import list_ports as lp
        found = sorted(lp.comports(), key=lambda p: p.device)
    except Exception:
        found = []
    for p in found:
        if p.vid == FTDI_VID and p.pid == FTDI_PID:
            add(p.device, f"{p.device}  (FTDI FT232R 0403:6001)", True)
        elif p.vid == FTDI_VID:
            add(p.device, f"{p.device}  (FTDI 0403:{p.pid:04x})", True)
    if os.path.exists(SIM_SYMLINK):
        add(SIM_SYMLINK, f"{SIM_SYMLINK}  (hondalog-sim pty)")
    for p in found:
        if p.vid is not None:
            add(p.device, f"{p.device}  ({p.vid:04x}:{p.pid:04x} {p.description})")
    add(BUILTIN_SIM, "sim:  (built-in simulator, true 9600 baud timing)")
    add(BUILTIN_SIM_FLAKY, "sim:flaky  (built-in simulator with line faults)")
    add(BUILTIN_SIM_ISO, "sim:iso  (built-in simulator that answers OBD2 only)")
    return ports


def default_port() -> str:
    """The port to use when none is named: the udev link if present, else the
    first FTDI adapter pyserial lists (COM21 on Windows), else, on Windows, the
    first USB COM port, else the udev link name."""
    if os.path.exists(UDEV_SYMLINK):
        return UDEV_SYMLINK
    try:
        from serial.tools import list_ports as lp
        found = sorted(lp.comports(), key=lambda p: p.device)
    except Exception:
        found = []
    for p in found:
        if p.vid == FTDI_VID:
            return str(p.device)
    if sys.platform == "win32":
        usb = [str(p.device) for p in found if p.vid is not None]
        if usb:
            return usb[0]
        return "COM1"
    return UDEV_SYMLINK


def make_transport(port: str, framing: Framing = protocol.DEFAULT) -> Transport:
    if port.startswith(BUILTIN_SIM):
        from .sim import EcuModel, Faults
        faults = Faults.flaky() if port == BUILTIN_SIM_FLAKY else None
        # sim:iso = an ECU that answers standard OBD2 (ISO 9141-2) but not Honda reads
        model = EcuModel(framing=framing, faults=faults, honda_protocol=port != BUILTIN_SIM_ISO)
        transport = LoopbackTransport(model, byte_timing=True)
        transport.name = port
        return transport
    return SerialTransport(port, framing.baud)


@dataclass(frozen=True, slots=True)
class ReadResult:
    """One successful block read."""

    addr: int
    data: bytes
    t_request: float     # clock time just before the request was written
    t_done: float        # clock time when the last reply byte was in hand
    latency_s: float     # estimated age of the data at t_done
    turnaround_s: float  # last request byte to first reply byte, as seen here
    attempts: int = 1


class Link:
    """Request/reply exchanges on a Transport. Use from one thread only."""

    BACKOFF_BASE_S = 0.005
    BACKOFF_MAX_S = 0.08
    RESYNC_MAX_S = 0.25

    def __init__(self, transport: Transport, framing: Framing = protocol.DEFAULT,
                 clock: Clock = REAL) -> None:
        self.transport = transport
        self.framing = framing
        self.clock = clock
        self.stats = LinkStats()
        self.timing = AdaptiveTiming(framing, transport.usb_latency_s)
        self.retries = max(0, int(framing.retries))
        self.echo_seen = False
        self._echo_mode: bool | None = None     # None = not known yet
        self._next_tx = 0.0
        self._last_tx_end = 0.0
        self._guard_until = 0.0
        self._requests: dict[tuple[int, int], bytes] = {}
        #: read-only guard: raw() sends only read requests and the init bytes
        self.read_only = True
        #: every non-read frame sent after an unlock, for the log header
        self.unlocked_frames: list[bytes] = []

    # -- housekeeping --

    def reset(self) -> None:
        """Start measuring again (after a reconnect or a protocol switch)."""
        self.timing = AdaptiveTiming(self.framing, self.transport.usb_latency_s)
        self._echo_mode = None
        self._next_tx = 0.0
        self._guard_until = 0.0

    def unlock(self, on: bool = True) -> None:
        """Expert unlock: let raw() send frames that are not reads."""
        self.read_only = not on

    def allowed(self, data: bytes) -> bool:
        """True if the read-only guard lets these bytes onto the wire."""
        f = self.framing
        if not data:
            return True
        if f.init_sequence and bytes(data) == f.init_sequence:
            return True
        return (len(data) == f.request_len and data[0] == f.cmd_read
                and data[1] == f.request_len and protocol.verify(data))

    def wake(self) -> None:
        """Send the wake-up sequence and throw away whatever comes back."""
        seq = self.framing.init_sequence
        if not seq:
            return
        self.stats.wakes += 1
        if self._guard_until:
            self._settle()
        self.transport.flush_input()
        self.transport.write(seq)
        self.clock.sleep(self.framing.init_delay_s)
        self.transport.read(256, 0.05)
        self._next_tx = self.clock.now() + self.timing.gap

    def _tx_time(self, nbytes: int) -> float:
        return nbytes * 10.0 / self.framing.baud

    def resync(self, quiet_until: float = 0.0) -> int:
        """Drain the line until it has been silent for ``idle_gap``.

        After junk or a damaged frame more bytes may still be on their way;
        sending into them would wreck the next exchange too. After a request
        that got no answer, pass ``quiet_until``: the line is then watched
        until that time for a reply that is merely late (see
        ``AdaptiveTiming.late_guard``), and the wait ends early once such a
        reply has come and gone. Returns the number of bytes discarded.
        """
        clock, read_some = self.clock, self.transport.read_some
        idle = self.timing.idle_gap
        end = max(clock.now() + self.RESYNC_MAX_S, quiet_until)
        dropped = 0
        while True:
            now = clock.now()
            if now >= end:
                break
            wait = idle if dropped or now + idle >= quiet_until else quiet_until - now
            chunk = read_some(256, wait)
            if chunk:
                dropped += len(chunk)
            elif dropped or clock.now() >= quiet_until:
                break
        self.stats.resyncs += 1
        self.stats.discarded_bytes += dropped
        self._next_tx = clock.now() + self.timing.gap
        return dropped

    def _settle(self) -> None:
        """Finish the late-reply watch left behind by an unanswered request."""
        until, self._guard_until = self._guard_until, 0.0
        self.resync(until)

    # -- reads --

    def read_mem(self, addr: int, length: int) -> bytes:
        """Read ``length`` RAM bytes at ``addr``. Raises ProtocolError."""
        return self.read_block(addr, length).data

    def read_block(self, addr: int, length: int, retries: int | None = None) -> ReadResult:
        """Read with timestamps. Retries with backoff, resyncing in between;
        raises the last ProtocolError if every attempt fails."""
        key = (addr, length)
        request = self._requests.get(key)
        if request is None:
            request = self._requests[key] = protocol.build_read_request(addr, length, self.framing)
        attempts = 1 + (self.retries if retries is None else retries)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._exchange(addr, length, request, attempt)
            except protocol.NoReply as exc:
                # Nothing came back. The caller hears about it now, but the
                # line is watched for a late reply before anything is sent
                # again, or that reply would pass for the next answer.
                error: protocol.ProtocolError = exc
                self._guard_until = self._last_tx_end + self.timing.late_guard
            except protocol.ProtocolError as exc:
                error = exc
                self.resync()
            if attempt >= attempts:
                raise error
            self.stats.retries += 1
            self.clock.sleep(min(self.BACKOFF_MAX_S, self.BACKOFF_BASE_S * 2 ** (attempt - 1)))

    def _exchange(self, addr: int, length: int, request: bytes, attempt: int) -> ReadResult:
        f, clock, tm, stats = self.framing, self.clock, self.timing, self.stats
        read_some = self.transport.read_some
        byte_s = tm.byte_s
        nreq = len(request) if self._echo_mode is not False else 0
        size = length + protocol.REPLY_OVERHEAD
        want = nreq + size
        want_len = (length + f.reply_len_extra) & 0xFF
        header = f.reply_header

        if self._guard_until:
            self._settle()
        wait = self._next_tx - clock.now()
        if wait > 0:
            clock.sleep(wait)
        t_req = clock.now()
        self.transport.write(request)
        stats.requests += 1

        tx_end = self._last_tx_end = t_req + len(request) * byte_s
        deadline = tx_end + byte_s + tm.response_timeout
        hard = t_req + (len(request) + size) * byte_s + tm.response_timeout + tm.inter_byte_timeout
        ibt = tm.inter_byte_timeout
        buf = bytearray()
        data: bytes | None = None
        started = False
        t_first = last_rx = max_gap = 0.0
        now = t_req
        while True:
            now = clock.now()
            remaining = deadline - now
            if remaining <= 0:
                break
            chunk = read_some(max(1, want - len(buf)), remaining)
            if not chunk:
                continue
            now = clock.now()
            buf += chunk
            n = len(buf)
            if started:
                if now - last_rx > max_gap:
                    max_gap = now - last_rx
            elif n > nreq or buf[:n] != request[:n]:
                started = True                  # past the echo, or not an echo at all
                t_first = now
            last_rx = now
            if started:
                deadline = min(hard, now + ibt)
            if n >= want:
                if buf[:nreq] == request[:nreq]:        # the lean, normal path
                    frame = buf[nreq:nreq + size]
                    if ((header < 0 or frame[0] == header) and frame[1] == want_len
                            and not sum(frame) & 0xFF):
                        data = bytes(frame[2:-1])
                        break
                rest, _ = protocol.strip_echo(buf, request)
                data = protocol.find_reply(rest, length, f)
                if data is not None:
                    break
            elif n >= size and buf[:min(n, nreq)] != request[:min(n, nreq)]:
                data = protocol.find_reply(protocol.strip_echo(buf, request)[0], length, f)
                if data is not None:
                    break

        echo_here = bytes(request) in buf
        if data is None:
            self._next_tx = now + tm.gap
            if self._echo_mode and buf and not echo_here:
                stats.echo_mismatches += 1
            try:
                data = protocol.parse_reply(bytes(buf), request, length, f)
            except protocol.NoReply:
                stats.timeouts += 1
                tm.on_failure(silent=True)
                raise
            except protocol.ShortReply:
                stats.timeouts += 1
                stats.short_replies += 1
                tm.on_failure(short=True)
                raise
            except protocol.BadChecksum:
                stats.checksum_failures += 1
                tm.on_failure()
                raise
            except protocol.ProtocolError:
                stats.bad_frames += 1
                tm.on_failure()
                raise

        if echo_here:
            self._echo_mode = self.echo_seen = True
        else:
            if self._echo_mode:
                stats.echo_mismatches += 1      # the frame survived, the echo did not
            else:
                self._echo_mode = False
            self.echo_seen = False
        on_wire = (len(request) + size) * byte_s
        extra = len(buf) - (size + (len(request) if echo_here else 0))
        if extra > 0:
            stats.junk_bytes += extra
        tm.on_success(now - t_req - on_wire, max_gap)
        stats.ok += 1
        self._next_tx = now + tm.gap
        return ReadResult(addr, data, t_req, now,
                          latency_s=size * byte_s + tm.usb_latency_s / 2,
                          turnaround_s=max(0.0, t_first - tx_end - byte_s),
                          attempts=attempt)

    # -- raw access (hex console, diagnostics) --

    def raw(self, data: bytes, quiet_s: float = 0.08, max_s: float = 0.6) -> tuple[bool, bytes]:
        """Send arbitrary bytes; collect what comes back until the line goes
        quiet. Returns (echo_seen, bytes_after_echo).

        Read-only by default: anything that is not a well-formed read request
        (or the init sequence) raises CommandBlocked until ``unlock()``.
        """
        data = bytes(data)
        if not self.allowed(data):
            if self.read_only:
                raise CommandBlocked(
                    f"blocked {protocol.fmt_hex(data)}: only read requests "
                    f"({self.framing.cmd_read:02X} {self.framing.request_len:02X} addr len cksum) "
                    "are sent unless the expert unlock is on")
            self.unlocked_frames.append(data)
        clock = self.clock
        if self._guard_until:
            self._settle()
        wait = self._next_tx - clock.now()
        if wait > 0:
            clock.sleep(wait)
        self.transport.flush_input()
        self.transport.write(data)
        start = clock.now()
        last_rx = start + self._tx_time(len(data))
        buf = bytearray()
        while True:
            now = clock.now()
            if now - start > max_s or (now - last_rx > quiet_s and now > last_rx):
                break
            chunk = self.transport.read_some(64, 0.02)
            if chunk:
                buf += chunk
                last_rx = clock.now()
        self._next_tx = clock.now() + self.timing.gap
        rest, echo_seen = protocol.strip_echo(bytes(buf), data)
        return echo_seen, rest


__all__ = [
    "BUILTIN_SIM", "BUILTIN_SIM_FLAKY", "CommandBlocked", "FTDI_PID", "FTDI_VID", "Link",
    "LoopbackTransport", "PortInfo", "ReadResult", "SIM_SYMLINK", "SerialTransport",
    "Transport", "TransportError", "UDEV_SYMLINK", "default_port", "list_ports", "make_transport",
]
