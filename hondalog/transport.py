"""Byte transports (real serial port, in-process simulator) and the Link that
runs request/reply exchanges over them."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import protocol
from .protocol import Framing

FTDI_VID, FTDI_PID = 0x0403, 0x6001
UDEV_SYMLINK = "/dev/hondalog"
SIM_SYMLINK = "/tmp/hondalog-sim"
BUILTIN_SIM = "sim:"


class TransportError(Exception):
    """The port itself failed (unplugged, permission denied, ...)."""


class Transport:
    name = "?"

    def open(self) -> None: ...
    def close(self) -> None: ...
    def write(self, data: bytes) -> None: ...
    def read(self, size: int, timeout: float) -> bytes: ...
    def flush_input(self) -> None: ...


class SerialTransport(Transport):
    """A tty: the KKL cable, or the pty that hondalog-sim creates."""

    def __init__(self, port: str, baud: int = 9600):
        self.name = port
        self.baud = baud
        self._ser = None

    def open(self) -> None:
        import serial
        try:
            self._ser = serial.Serial(self.name, self.baud, bytesize=8, parity="N",
                                      stopbits=1, timeout=0.2, write_timeout=1.0)
        except (serial.SerialException, OSError) as exc:
            raise TransportError(str(exc)) from exc
        try:  # FTDI: cut the 16 ms receive latency. Not supported on a pty.
            self._ser.set_low_latency_mode(True)
        except Exception:
            pass

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def write(self, data: bytes) -> None:
        import serial
        try:
            self._ser.write(data)
            self._ser.flush()
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(str(exc)) from exc

    def read(self, size: int, timeout: float) -> bytes:
        import serial
        try:
            self._ser.timeout = max(0.0, timeout)
            return self._ser.read(size)
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(str(exc)) from exc

    def flush_input(self) -> None:
        import serial
        try:
            self._ser.reset_input_buffer()
        except (serial.SerialException, OSError, AttributeError) as exc:
            raise TransportError(str(exc)) from exc


class LoopbackTransport(Transport):
    """The simulated ECU in the same process. No pty, no real timing."""

    name = BUILTIN_SIM

    def __init__(self, model=None):
        if model is None:
            from .sim import EcuModel
            model = EcuModel()
        self.model = model
        self._rx = bytearray()
        self._req = bytearray()

    def open(self) -> None:
        self._rx.clear()
        self._req.clear()

    def write(self, data: bytes) -> None:
        if self.model.echo:
            self._rx += data
        self._req += data
        self._rx += self.model.feed(self._req)

    def read(self, size: int, timeout: float) -> bytes:
        if not self._rx:
            time.sleep(min(timeout, 0.02))
        out = bytes(self._rx[:size])
        del self._rx[:size]
        if out:
            time.sleep(len(out) * 0.0005)   # keep the poll rate believable
        return out

    def flush_input(self) -> None:
        self._rx.clear()


@dataclass
class PortInfo:
    device: str
    label: str
    is_ftdi: bool = False


def list_ports() -> list[PortInfo]:
    """Candidate ports, best guess first."""
    ports: list[PortInfo] = []
    seen: set[str] = set()

    def add(device, label, is_ftdi=False):
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
    if os.path.exists(SIM_SYMLINK):
        add(SIM_SYMLINK, f"{SIM_SYMLINK}  (hondalog-sim pty)")
    for p in found:
        if p.vid is not None:
            add(p.device, f"{p.device}  ({p.vid:04x}:{p.pid:04x} {p.description})")
    add(BUILTIN_SIM, "sim:  (built-in simulator, no pty)")
    return ports


def make_transport(port: str, framing: Framing = protocol.DEFAULT) -> Transport:
    if port == BUILTIN_SIM:
        return LoopbackTransport()
    return SerialTransport(port, framing.baud)


class Link:
    """Request/reply exchanges on a Transport. Use from one thread only."""

    def __init__(self, transport: Transport, framing: Framing = protocol.DEFAULT):
        self.transport = transport
        self.framing = framing
        self.echo_seen = False

    def wake(self) -> None:
        """Send the wake-up sequence and throw away whatever comes back."""
        seq = self.framing.init_sequence
        if not seq:
            return
        self.transport.flush_input()
        self.transport.write(seq)
        time.sleep(self.framing.init_delay_s)
        self.transport.read(256, 0.05)

    def _tx_time(self, nbytes: int) -> float:
        return nbytes * 10.0 / self.framing.baud

    def read_mem(self, addr: int, length: int) -> bytes:
        """Read ``length`` RAM bytes at ``addr``. Raises ProtocolError."""
        f = self.framing
        request = protocol.build_read_request(addr, length, f)
        want = len(request) + protocol.reply_size(length, f)
        self.transport.flush_input()
        self.transport.write(request)
        deadline = time.monotonic() + self._tx_time(want) + f.reply_timeout_s
        buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Short slices: a cable that does not echo delivers fewer bytes than
            # ``want`` and must not cost a full timeout on every request.
            buf += self.transport.read(max(1, want - len(buf)), min(remaining, 0.02))
            if len(buf) >= protocol.reply_size(length, f):
                rest, _ = protocol.strip_echo(buf, request)
                if protocol.find_reply(rest, length, f) is not None:
                    break
        self.echo_seen = protocol.strip_echo(buf, request)[1]
        return protocol.parse_reply(bytes(buf), request, length, f)

    def raw(self, data: bytes, quiet_s: float = 0.08, max_s: float = 0.6):
        """Send arbitrary bytes; collect what comes back until the line goes
        quiet. Returns (echo_seen, bytes_after_echo)."""
        self.transport.flush_input()
        self.transport.write(data)
        start = time.monotonic()
        last_rx = start + self._tx_time(len(data))
        buf = bytearray()
        while True:
            now = time.monotonic()
            if now - start > max_s or (now - last_rx > quiet_s and now > last_rx):
                break
            chunk = self.transport.read(64, 0.02)
            if chunk:
                buf += chunk
                last_rx = time.monotonic()
        rest, echo_seen = protocol.strip_echo(bytes(buf), data)
        return echo_seen, rest
