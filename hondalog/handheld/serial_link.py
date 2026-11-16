"""The laptop's end of the handheld's serial link (firmware/handheld/SERIAL.md).

``CydPoller``
    The live source: a thread that owns the port and looks like
    ``hondalog.poller.Poller`` to everything downstream. Every READ frame is
    the ECU's raw bytes; they are decoded with the *laptop's* address map and
    handed out as ``BlockRead`` (``on_block``), a ``Sample`` per TICK
    (``on_sample``). ``LiveSession.connect("cyd:...")`` makes one, so the app
    and ``hondalog-log record --port cyd:`` record a CYD drive the same way
    as a drive on the KKL cable.

``CydClient``
    Request and answer, for the command line (``hondalog-handheld sync``,
    ``push-map``, ``set-time``): list and fetch files with resume, push a
    file, set the clock. Two-way firmware only.

Port names: ``cyd:`` (find it: ``/dev/hondalog-cyd``, a CH340 ``1a86:7523``,
or ``hondalog-cydsim``'s pty), ``cyd:/dev/ttyUSB0``, ``cyd:/dev/ttyUSB0@460800``,
``cyd:sim`` and ``cyd:sim:p1`` (the built-in simulator, both variants).

One rule keeps the tx-only variant safe: **nothing is written to the port
until a HELLO has said the handheld listens.** On a GPS-on-P1 board the
laptop's TX pin is wired to the GPS's line.
"""

from __future__ import annotations

import collections
import os
import struct
import threading
import time
import zlib
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..addressmap import AddressMap
from ..blackbox.reader import ClockSync
from ..link.clock import REAL
from ..link.scheduler import ChannelRate, rate_class_of, target_hz
from ..link.timing import LinkStats
from ..poller import CONNECTING, HONDA, NO_REPLY, OBD2, OK, PORT_LOST, STOPPED, BlockRead, Sample
from . import format as F
from . import serial_proto as P

PREFIX = "cyd:"
UDEV_SYMLINK = "/dev/hondalog-cyd"
CH340_ID = (0x1A86, 0x7523)
SILENT_S = 1.5                  # no frame for this long: the stream has stopped
HUNT_S = 3.0                    # no good frame for this long after opening: try the next baud


class CydError(Exception):
    """The handheld did not answer, refused, or the port failed."""


class CydBusy(CydError):
    """The file is still being written by the handheld."""


# ---- ports ------------------------------------------------------------------

class SerialPort:
    """The CH340 behind the CYD's USB-C socket (or ``hondalog-cydsim``'s pty)."""

    def __init__(self, name: str, baud: int = P.BAUDS[0]):
        self.name = name
        self.baud = baud
        self._ser: Any = None

    def open(self) -> None:
        import serial
        try:
            ser = serial.Serial()
            ser.port = self.name
            ser.baudrate = self.baud
            ser.timeout = 0.05
            ser.write_timeout = 1.0
            # DTR and RTS drive the board's auto-reset circuit (EN and GPIO0, the
            # button). Linux raises both when the port opens, which is harmless;
            # RTS on with DTR off is a reset. So RTS is released first, then DTR
            # (dsrdtr=True keeps pyserial from setting DTR during open()).
            ser.dsrdtr = True
            ser.rts = False
            ser.open()
            try:
                ser.dtr = False
            except OSError:             # a pty (hondalog-cydsim) has no modem lines
                pass
            self._ser = ser
        except (serial.SerialException, OSError, ValueError) as exc:
            self._ser = None
            raise CydError(str(exc)) from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def set_baud(self, baud: int) -> None:
        self.baud = baud
        if self._ser is not None:
            try:
                self._ser.baudrate = baud
                self._ser.reset_input_buffer()
            except Exception as exc:
                raise CydError(str(exc)) from exc

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except Exception as exc:
            raise CydError(str(exc)) from exc

    def read(self, timeout: float) -> bytes:
        try:
            self._ser.timeout = timeout
            data = self._ser.read(1)
            waiting = self._ser.in_waiting
            return data + (self._ser.read(waiting) if waiting else b"")
        except Exception as exc:
            raise CydError(str(exc)) from exc


def is_cyd_port(name: str) -> bool:
    return name.startswith(PREFIX)


def is_sim_port(name: str) -> bool:
    return name.startswith(PREFIX + "sim")


def list_ports() -> list[tuple[str, str]]:
    """(port name, label) for the connection menu: handhelds found, best guess first."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(device: str, label: str) -> None:
        real = os.path.realpath(device)
        if real not in seen:
            seen.add(real)
            out.append((PREFIX + device, label))

    if os.path.exists(UDEV_SYMLINK):
        add(UDEV_SYMLINK, f"CYD (serial)  {UDEV_SYMLINK}  (handheld, udev link)")
    try:
        from serial.tools import list_ports as lp
        for p in sorted(lp.comports(), key=lambda p: p.device):
            if (p.vid, p.pid) == CH340_ID:
                add(p.device, f"CYD (serial)  {p.device}  (CH340 {p.vid:04x}:{p.pid:04x})")
    except Exception:
        pass
    from .cydsim import CYDSIM_SYMLINK
    if os.path.exists(CYDSIM_SYMLINK):
        add(CYDSIM_SYMLINK, f"CYD (serial)  {CYDSIM_SYMLINK}  (hondalog-cydsim pty)")
    return out


def find_device() -> str | None:
    """The device ``cyd:`` stands for, or None."""
    found = list_ports()
    return found[0][0][len(PREFIX):] if found else None


def make_port(name: str) -> Any:
    """A port object for a ``cyd:`` name (or a bare device path)."""
    spec = name[len(PREFIX):] if name.startswith(PREFIX) else name
    if spec == "sim" or spec.startswith("sim:"):
        from .cydsim import SimPort, sim_from_port
        return SimPort(sim_from_port(PREFIX + spec))
    device, _, baud = spec.partition("@")
    if not device:
        device = find_device() or ""
        if not device:
            raise CydError(f"no handheld found: no {UDEV_SYMLINK}, no CH340 (1a86:7523) serial port. "
                           "Plug the CYD's USB-C port in, or name the port: cyd:/dev/ttyUSB0")
    port = SerialPort(device, int(baud) if baud else P.BAUDS[0])
    port.fixed_baud = bool(baud)        # type: ignore[attr-defined]
    return port


class _Opener:
    """Opening, and finding the baud rate: if nothing readable arrives for
    HUNT_S the next of ``serial_proto.BAUDS`` is tried, round and round."""

    def __init__(self, port: Any):
        self.port = port
        first = getattr(port, "baud", P.BAUDS[0])
        self.bauds = [first] if getattr(port, "fixed_baud", False) else [first] + [b for b in P.BAUDS if b != first]
        self._i = 0
        self.t_change = 0.0
        self.locked = False             # a good frame arrived at this speed

    def open(self) -> None:
        self.port.open()
        self.t_change = time.monotonic()

    def good(self) -> None:
        self.locked = True

    def hunt(self, now: float) -> bool:
        """Call while nothing good has arrived. True if the baud was changed."""
        if self.locked or len(self.bauds) < 2 or now - self.t_change < HUNT_S:
            return False
        self._i = (self._i + 1) % len(self.bauds)
        self.port.set_baud(self.bauds[self._i])
        self.t_change = now
        return True


# ---- events -----------------------------------------------------------------

@dataclass
class CydEvent:
    """An EVENT frame: a marker, an alarm, a stall trigger, a trouble code ..."""
    t: float                    # session seconds (the poller's axis)
    t_dev_us: int
    code: int
    src: int
    aux: int
    value: float | int

    @property
    def name(self) -> str:
        return F.EVENT_NAMES.get(self.code, f"event_{self.code}")

    @property
    def is_stall(self) -> bool:
        return self.code == F.EV_STALL

    @property
    def text(self) -> str:
        if self.code == F.EV_STALL:
            return f"stall: {F.STALL_MESSAGES.get(self.src, 'trigger')}"
        if self.code == F.EV_MARKER:
            return f"MARK {self.value} (handheld button)"
        if self.code == F.EV_DTC:
            return f"trouble code {F.dtc_text(int(self.value))}"
        if self.code == F.EV_DTC_NONE:
            return "no stored trouble codes"
        return self.name.replace("_", " ")


def parse_event(p: bytes) -> tuple[int, int, int, int, float | int] | None:
    if len(p) < F.REC_EVENT.size:
        return None
    t_us, code, src, aux, value = F.REC_EVENT.unpack(p[:F.REC_EVENT.size])
    if code in F.FLOAT_EVENTS:
        value = struct.unpack("<f", p[12:16])[0]
    return t_us, code, src, aux, value


def parse_gps(p: bytes) -> dict[str, Any] | None:
    if len(p) < F.REC_GPS.size:
        return None
    t_us, lat, lon, utc, speed, course, fix, sats, hdop = F.REC_GPS.unpack(p[:F.REC_GPS.size])
    has = bool(fix)
    return {"t_us": t_us, "gps_lat": lat / 1e7 if has else None, "gps_lon": lon / 1e7 if has else None,
            "gps_speed": speed * 0.036 if has else None, "gps_heading": course / 100 if has else None,
            "gps_sats": sats, "gps_fix": 1 if has else 0, "gps_hdop": hdop / 100 if hdop else None,
            "gps_utc": float(utc) if utc else None}


class _OneWayClock:
    """Device time -> laptop time when nothing can be asked: the smallest
    ``arrival - device stamp`` of the recent frames is the one that waited
    least on the way, so that is the offset (it is late by the wire time of
    one frame, a millisecond or two)."""

    def __init__(self, window: int = 64):
        self._recent: collections.deque[float] = collections.deque(maxlen=window)
        self.offset: float | None = None

    def add(self, host_s: float, dev_us: int) -> None:
        self._recent.append(host_s - dev_us / 1e6)
        self.offset = min(self._recent)

    def reset(self) -> None:
        self._recent.clear()
        self.offset = None


class _ReadOnlyLink:
    """What ``LiveSession.log_meta`` and the UI ask a poller's link: this one
    cannot send anything to the ECU at all."""

    read_only = True
    unlocked_frames: tuple = ()

    def unlock(self, on: bool = True) -> None:
        pass


# ---- the live source ---------------------------------------------------------

class CydPoller(threading.Thread):
    """The handheld's stream as a channel source; see the module text.

    Has what the UI and ``LiveSession`` read off a ``Poller``: ``samples``,
    ``on_sample``, ``on_block``, ``state``, ``hz``, ``reads``, ``errors``,
    ``last_error``, ``ready``, ``t0``, ``ecu_id``, ``rates``, ``stop()``.
    Extra: ``hello``, ``device`` (the handheld's own counters), ``lost``
    (frames missing by sequence number), ``map_mismatch`` / ``warning``,
    ``on_gps``, ``on_event``, ``on_log``, and the commands ``mark()``,
    ``ack_alarm()``, ``request_dtc()``, ``drive()`` (two-way firmware only).
    """

    source = "cyd"

    def __init__(self, port: Any, amap: AddressMap, on_sample: Callable[[Sample], None] | None = None,
                 max_queue: int = 2000, t0: float | None = None):
        super().__init__(name="hondalog-cyd", daemon=True)
        self.port_name = port if isinstance(port, str) else getattr(port, "name", "cyd:")
        self._port_arg = port
        self.port: Any = None
        self.amap = amap
        self.clock = REAL
        self.link = _ReadOnlyLink()
        self.transport = None
        self.on_sample = on_sample
        self.on_block: Callable[[BlockRead], None] | None = None
        self.on_gps: Callable[[float, dict], None] | None = None
        self.on_event: Callable[[CydEvent], None] | None = None
        self.on_log: Callable[[str], None] | None = None
        self.samples: collections.deque[Sample] = collections.deque(maxlen=max_queue)
        self.log_lines: collections.deque[str] = collections.deque(maxlen=200)
        self.ready = threading.Event()          # set once the handheld said hello (or did not, for 3 s)
        self.t0 = time.monotonic() if t0 is None else t0
        self.state = CONNECTING
        self.mode = HONDA
        self.reads = 0                          # READ frames
        self.errors = 0                         # damaged frames, lost frames, port errors
        self.sweeps = 0
        self.hz = 0.0
        self.last_error = ""
        self.ecu_id: bytes | None = None
        self.rates: dict[str, ChannelRate] = {}
        self.hello: P.Hello | None = None
        self.device: dict[str, int] = {}        # the last STATS frame
        self.lost = 0                           # frames missing by sequence number
        self.frames = 0
        self.bytes_in = 0
        self.bytes_per_s = 0.0
        self.map_mismatch = False
        self.warning = ""
        self.clock_sync = ClockSync()
        self._one_way = _OneWayClock()
        self._class = {c.name: rate_class_of(c) for c in amap.channels}
        self._target = {n: target_hz(k, amap.framing) for n, k in self._class.items()}
        self._decoders: dict[tuple[int, int], list[tuple[str, Any, int, int]]] = {}
        self._block_times: dict[tuple[int, int], collections.deque[float]] = {}
        self._values: dict[str, float | None] = {n: None for n in amap.names}
        self._stamps: dict[str, float] = {}
        self._fresh: set[str] = set()
        self._stop_evt = threading.Event()
        self._wlock = threading.Lock()
        self._deframer = P.Deframer()
        self._seq_out = 0
        self._seq_in: int | None = None
        self._last_rx = 0.0
        self._last_good = 0.0
        self._last_down_emit = 0.0
        self._next_report = 0.0
        self._next_beat = 0.0
        self._rate_mark = (0.0, 0)
        self._ecu_up: bool | None = None
        self._opener: _Opener | None = None
        self._t_open = 0.0
        self._last_t = 0.0

    # -- what a Poller has --

    @property
    def blocks(self) -> list[tuple[int, int]]:
        """The reads the handheld makes, as seen so far: (addr, length)."""
        return sorted(self._decoders)

    def predicted_rates(self) -> dict[str, float]:
        return {}

    @property
    def bus_load(self) -> float:
        return 0.0

    def channel_rates(self) -> dict[str, ChannelRate]:
        return dict(self.rates)

    def class_rates(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for rate in self.rates.values():
            target, worst = out.get(rate.rate_class, (rate.target_hz, float("inf")))
            out[rate.rate_class] = (target, min(worst, rate.achieved_hz))
        return out

    def shortfalls(self) -> dict[str, ChannelRate]:
        return {n: r for n, r in self.rates.items() if not r.ok}

    @property
    def stats(self) -> LinkStats:
        """The handheld's K-line counters (from its STATS frames)."""
        d = self.device
        return LinkStats(requests=d.get("reads", 0) + d.get("errors", 0), ok=d.get("reads", 0),
                         timeouts=d.get("timeouts", 0), checksum_failures=d.get("checksum_failures", 0),
                         retries=d.get("retries", 0), reconnects=d.get("reconnects", 0))

    def link_report(self) -> dict[str, float]:
        """For the drive's header: the K-line as the handheld counted it, and this link."""
        d = self.device
        out: dict[str, float] = {k: float(d.get(k, 0)) for k in ("reads", "errors", "timeouts", "checksum_failures",
                                                              "retries", "reconnects")}
        out.update(sample_hz=self.hz, cyd_frames=float(self.frames), cyd_lost=float(self.lost),
                   cyd_damaged=float(self._deframer.bad), cyd_dropped=float(d.get("tx_dropped", 0)),
                   cyd_bytes_per_s=round(self.bytes_per_s, 1))
        return out

    def cyd_meta(self) -> dict[str, Any]:
        """Facts for the drive's ``meta``: which handheld, which firmware, which map."""
        h = self.hello
        meta: dict[str, Any] = {"port": self.port_name, "frames": self.frames, "lost": self.lost,
                                "damaged": self._deframer.bad}
        if h is not None:
            meta.update(device=h.device, firmware=h.fw, variant=h.variant_name,
                        link="two-way" if h.two_way else "tx-only", boot=h.boot_no, map_hash=h.map_hash,
                        builtin_map=h.builtin_map, baud=getattr(self.port, "baud", h.baud),
                        clock="exchange" if self.clock_sync.valid else "one-way")
        meta["map_mismatch"] = self.map_mismatch
        if self.map_mismatch:
            meta["uncovered_channels"] = self.uncovered()
        return meta

    def uncovered(self) -> list[str]:
        """Channels of the laptop's map that no read of the handheld contains."""
        blocks = self.blocks
        return [c.name for c in self.amap.channels
                if not any(a <= c.addr and c.addr + c.length <= a + n for a, n in blocks)]

    def link_text(self) -> str:
        """One line for the connection menu and the status bar."""
        h = self.hello
        if h is None:
            return ""
        d = self.device
        parts = [f"CYD {h.device} fw {h.fw}", "two-way" if h.two_way else "TX-only (GPS on P1)",
                 f"{self.hz:.0f} Hz", f"{self.bytes_per_s / 1000:.1f} kB/s at {getattr(self.port, 'baud', h.baud)} baud",
                 f"lost {self.lost} damaged {self._deframer.bad} dropped {d.get('tx_dropped', 0)}"]
        if d:
            flags = d.get("flags", 0)
            parts.append(("SD rec #%d" % d.get("drive_no", 0)) if flags & P.ST_RECORDING
                         else "SD ok" if flags & P.ST_SD_OK else "no SD")
            parts.append(f"K-line {d.get('timeouts', 0)} t/o {d.get('checksum_failures', 0)} cks")
            if d.get("time_source"):            # older firmware does not say: nothing is shown rather than a guess
                parts.append("TIME " + P.time_text(d["time_source"], d.get("time_err_us", 0)))
        parts.append("MAP MISMATCH #" + h.map_hash if self.map_mismatch else "map #" + h.map_hash)
        return " · ".join(parts)

    @property
    def ecu_id_text(self) -> str:
        if self.ecu_id is None:
            return "(no reply)"
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in self.ecu_id)
        return f"{self.ecu_id.hex(' ').upper()}  \"{text}\""

    def _refuse(self, what: str) -> Future:
        fut: Future = Future()
        fut.set_exception(RuntimeError(f"{what} is not possible over the handheld: the handheld owns the K-line. "
                                       "Use the KKL cable on the laptop for that"))
        return fut

    def submit(self, fn: Any) -> Future:
        return self._refuse("a raw read")

    def obd2(self, fn: Any) -> Future:
        return self._refuse("an OBD2 session")

    def burst(self, names: list[str], seconds: float) -> Future:
        return self._refuse("a burst read")

    def set_expert_unlock(self, on: bool) -> None:
        """Nothing to unlock: this link cannot reach the ECU."""

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout)

    # -- commands (two-way firmware) --

    @property
    def two_way(self) -> bool:
        return self.hello is not None and self.hello.two_way

    def send(self, ftype: int, payload: bytes = b"") -> None:
        """One frame to the handheld. Refused until a hello has said it listens."""
        if not self.two_way:
            raise CydError("the handheld is tx-only (GPS on P1)" if self.hello is not None
                           else "no hello from the handheld yet")
        with self._wlock:
            if self.state in (PORT_LOST, STOPPED) or self.port is None:
                raise CydError(self.last_error or "not connected")
            self.port.write(P.frame_encode(ftype, payload, self._seq_out))
            self._seq_out = (self._seq_out + 1) & 0xFF

    def mark(self, number: int = 0) -> None:
        self.send(P.SC_MARK)

    def ack_alarm(self) -> None:
        self.send(P.SC_ACK_ALARM)

    def request_dtc(self) -> None:
        """Ask the handheld to read stored trouble codes (its own ISO 9141-2
        Mode 03 read; about 8 s without live data). They arrive as events."""
        self.send(P.SC_DTC)

    def drive(self, on: bool) -> None:
        """Stop (and hold) or start the handheld's own drive file."""
        self.send(P.SC_DRIVE, bytes([1 if on else 0]))

    # -- thread body --

    def run(self) -> None:
        try:
            self._open()
            while not self._stop_evt.is_set():
                if self.state == PORT_LOST:
                    self._emit_down(time.monotonic())
                    self._stop_evt.wait(0.5)
                    self._open()
                    continue
                try:
                    data = self.port.read(0.05)
                    now = time.monotonic()
                    if data:
                        self._last_rx = now
                        self.bytes_in += len(data)
                        before = self._deframer.bad
                        for ftype, seq, payload in self._deframer.feed(data):
                            self._frame(ftype, seq, payload, now)
                        self.errors += self._deframer.bad - before
                    self._housekeeping(now)
                except CydError as exc:
                    self._port_lost(exc)
        finally:
            self.ready.set()
            self.state = STOPPED
            if self.port is not None:
                self.port.close()

    def _open(self) -> None:
        self._t_open = time.monotonic()
        try:
            if self.port is None:
                self.port = make_port(self._port_arg) if isinstance(self._port_arg, str) else self._port_arg
            self._opener = _Opener(self.port)
            self._opener.open()
        except (CydError, ValueError) as exc:
            if isinstance(self._port_arg, str):
                self.port = None            # look for it again: it may come back as another ttyUSB
            self._port_lost(exc)
            self.ready.set()
            return
        self.state = CONNECTING
        self.hello = None
        self._seq_in = None
        self._deframer = P.Deframer()
        self._one_way.reset()
        self.clock_sync = ClockSync()
        self._last_rx = self._last_good = self._t_open
        self._ecu_up = None

    def _port_lost(self, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"port: {exc}"
        self.state = PORT_LOST
        self.hz = 0.0
        if self.port is not None:
            self.port.close()

    def _housekeeping(self, now: float) -> None:
        opener = self._opener
        if self.two_way and now >= self._next_beat:
            # the heartbeat that shows "PC" on the handheld, and the clock exchange
            self._next_beat = now + (0.2 if self.clock_sync.count < 5 else 1.0)
            self.send(P.SC_TIME_REQ, struct.pack("<q", int(now * 1e6)))
        if opener is not None and not opener.locked:
            if opener.hunt(now):
                self._deframer = P.Deframer()
                self.last_error = f"nothing readable: trying {self.port.baud} baud"
            if now - self._t_open > HUNT_S and not self.ready.is_set():
                self.state = NO_REPLY
                if not self.last_error:
                    self.last_error = "no data from the handheld (is the link on? HANDHELD.CFG serial = 1)"
                self.ready.set()
        if now - self._last_good > SILENT_S and self.state in (OK, CONNECTING) and self.frames:
            self.state = NO_REPLY
            self.hz = 0.0
            self.last_error = f"no data from the handheld for {SILENT_S:.1f} s"
            self._unknown()
        if self.state == NO_REPLY and now - self._last_good > SILENT_S and now - self._last_down_emit >= 0.25:
            self._emit_down(now)            # keeps the time axis moving, like the poller's probes
        if now >= self._next_report:
            self._next_report = now + 0.5
            self._report(now)

    def _report(self, now: float) -> None:
        t_prev, bytes_prev = self._rate_mark
        if t_prev and now > t_prev:
            inst = (self.bytes_in - bytes_prev) / (now - t_prev)
            self.bytes_per_s = inst if self.bytes_per_s == 0 else 0.7 * self.bytes_per_s + 0.3 * inst
        self._rate_mark = (now, self.bytes_in)
        rates: dict[str, ChannelRate] = {}
        t = now - self.t0
        for key, decode in self._decoders.items():
            times = self._block_times[key]
            while times and t - times[0] > 4.0:
                times.popleft()
            span = (times[-1] - times[0]) if len(times) > 1 else 0.0
            hz = (len(times) - 1) / span if span > 0 else 0.0
            gap = max((b - a for a, b in zip(times, list(times)[1:])), default=0.0)
            for name, _, _, _ in decode:
                rates[name] = ChannelRate(name, self._class[name], self._target[name], hz, gap)
        self.rates = rates

    # -- time --

    def _host(self, dev_us: int, now: float) -> float:
        """Laptop monotonic seconds of a device timestamp."""
        if self.clock_sync.valid:
            host = self.clock_sync.to_host_us(dev_us) / 1e6
        elif self._one_way.offset is not None:
            host = dev_us / 1e6 + self._one_way.offset
        else:
            host = now
        return min(host, now)

    def _session(self, dev_us: int, now: float) -> float:
        t = self._host(dev_us, now) - self.t0
        return t

    # -- frames --

    def _frame(self, ftype: int, seq: int, p: bytes, now: float) -> None:
        self.frames += 1
        self._last_good = now
        if self._opener is not None:
            self._opener.good()
        if self._seq_in is not None:
            missing = (seq - self._seq_in - 1) & 0xFF
            if missing:
                self.lost += missing
                self.errors += missing
        self._seq_in = seq
        if ftype == P.SF_READ and len(p) >= P.READ.size:
            self._read(p, now)
        elif ftype == P.SF_TICK and len(p) >= P.TICK.size:
            t_us, lat_us, hz_c, _n = P.TICK.unpack(p[:P.TICK.size])
            self._one_way.add(now, t_us)
            self.hz = hz_c / 100.0
            self._tick(t_us, lat_us / 1e6, now)
        elif ftype == P.SF_LINK and len(p) >= P.LINK.size:
            t_us, state, up, _backend, iso = P.LINK.unpack(p[:P.LINK.size])
            self._one_way.add(now, t_us)
            self.mode = OBD2 if iso else HONDA
            self._link(bool(up), state, now)
        elif ftype == P.SF_HELLO:
            hello = P.parse_hello(p)
            if hello is not None:
                self._hello(hello, now)
        elif ftype == P.SF_STATS:
            stats = P.parse_stats(p)
            if stats is not None:
                self._one_way.add(now, stats["t_us"])
                self.device = stats
        elif ftype == P.SF_GPS:
            fix = parse_gps(p)
            if fix is not None and self.on_gps is not None:
                self._call(self.on_gps, self._session(fix["t_us"], now), fix)
        elif ftype == P.SF_EVENT:
            ev = parse_event(p)
            if ev is not None and self.on_event is not None:
                self._call(self.on_event, CydEvent(self._session(ev[0], now), *ev))
        elif ftype == P.SF_ISO and len(p) >= P.ISO.size:
            self._iso(p, now)
        elif ftype == P.SF_TIME_RSP and len(p) >= P.TIME_RSP.size:
            host_tx, dev_rx, dev_tx = P.TIME_RSP.unpack(p[:P.TIME_RSP.size])
            self.clock_sync.add(host_tx, dev_rx, dev_tx, int(now * 1e6))
        elif ftype == P.SF_LOG:
            text = p.decode("utf-8", "replace").rstrip()
            self.log_lines.append(text)
            if self.on_log is not None:
                self._call(self.on_log, text)

    def _call(self, fn: Callable, *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:            # a listener's problem must not stop the stream
            self.last_error = f"{getattr(fn, '__name__', 'callback')}: {exc!r}"

    def _hello(self, hello: P.Hello, now: float) -> None:
        first = self.hello is None
        restarted = (not first and self.hello is not None
                     and (hello.boot_no != self.hello.boot_no or hello.t_us < self.hello.t_us))
        if restarted:                       # the handheld restarted: its clock starts over
            self._one_way.reset()
            self.clock_sync = ClockSync()
        self.hello = hello
        self._one_way.add(now, hello.t_us)
        if hello.ecu_id:
            self.ecu_id = hello.ecu_id
        if first or restarted:
            self.map_mismatch = hello.map_hash != self.amap.version_hash
            self.warning = ""
            if self.map_mismatch:
                self.warning = (f"the handheld runs address map #{hello.map_hash}, yours is "
                                f"#{self.amap.version_hash}: its raw reads are decoded with yours. "
                                "Channels its plan does not read stay empty (hondalog-handheld push-map)")
            if hello.rev != P.REV:
                self.warning = (self.warning + " " if self.warning else "") + \
                    f"the handheld speaks link revision {hello.rev}, this program {P.REV}"
            self._next_beat = now
        if self.state == CONNECTING and hello.link_state not in (P.LINK_OK, P.LINK_ISO_ONLY):
            self._link(False, hello.link_state, now)
        self.ready.set()

    def _decoder(self, addr: int, length: int) -> list[tuple[str, Any, int, int]]:
        key = (addr, length)
        decode = self._decoders.get(key)
        if decode is None:
            decode = self._decoders[key] = [(c.name, c, c.addr - addr, c.length) for c in self.amap.channels
                                            if addr <= c.addr and c.addr + c.length <= addr + length]
            self._block_times[key] = collections.deque(maxlen=256)
        return decode

    def _read(self, p: bytes, now: float) -> None:
        t_us, addr, length, lat_us = P.READ.unpack(p[:P.READ.size])
        data = p[P.READ.size:P.READ.size + length]
        if len(data) != length:
            self.errors += 1
            return
        latency = lat_us / 1e6
        if not self.clock_sync.valid:
            self._one_way.add(now, t_us + lat_us)
        mono = self._host(t_us + lat_us, now)       # when the reply's last byte was in, on this clock
        t = max(mono - self.t0, self._last_t)       # never backwards
        self._last_t = t
        self.reads += 1
        if self._ecu_up is not True:
            self._link(True, P.LINK_OK, now)
        decode = self._decoder(addr, length)
        values, stamps = self._values, self._stamps
        for name, channel, offset, n in decode:
            values[name] = channel.convert(data[offset:offset + n])
            stamps[name] = t - latency
            self._fresh.add(name)
        self._block_times[(addr, length)].append(t)
        if self.on_block is not None:
            try:
                self.on_block(BlockRead(t, t + self.t0, addr, data, latency,
                                        {name: values[name] for name, _, _, _ in decode}))
            except Exception as exc:
                self.last_error = f"on_block: {exc!r}"

    def _iso(self, p: bytes, now: float) -> None:
        from ..obd2 import decode_pid
        from .importer import PID_CHANNEL
        t_us, mode, pid, length, _flags = P.ISO.unpack(p[:P.ISO.size])
        data = p[P.ISO.size:P.ISO.size + length]
        name = PID_CHANNEL.get(pid)
        self.reads += 1
        self.mode = OBD2
        if mode != 0x41 or name not in self._values:
            return
        if self._ecu_up is not True:
            self._link(True, P.LINK_ISO_ONLY, now)
        self._values[name] = decode_pid(pid, data).value
        self._stamps[name] = self._session(t_us, now)
        self._fresh.add(name)

    def _tick(self, t_us: int, latency: float, now: float) -> None:
        if self._ecu_up is not True:
            return
        mono = self._host(t_us, now)
        t = max(mono - self.t0, self._last_t)
        sample = Sample(t, datetime.fromtimestamp(time.time() - (now - mono)), True, dict(self._values),
                        dict(self._stamps), latency, mono, frozenset(self._fresh))
        self._fresh.clear()
        self.sweeps += 1
        self._deliver(sample)

    def _unknown(self) -> None:
        for name in self._values:
            self._values[name] = None
        self._fresh.clear()

    def _link(self, up: bool, state: int, now: float) -> None:
        if up:
            if self._ecu_up is not True:
                self._ecu_up = True
                self.last_error = ""
            self.state = OK
            return
        self._ecu_up = False
        self.hz = 0.0
        self.state = NO_REPLY if state != P.LINK_CONNECTING or self.state != CONNECTING else CONNECTING
        self.last_error = f"handheld: {P.LINK_NAMES.get(state, 'ECU link down')}"
        self._unknown()
        if self.state == NO_REPLY:
            self._emit_down(now)

    def _emit_down(self, now: float) -> None:
        self._last_down_emit = now
        t = max(now - self.t0, self._last_t)
        self._last_t = t
        self._deliver(Sample(t, datetime.now(), False, {}, {}, 0.0, now))

    def _deliver(self, sample: Sample) -> None:
        self.samples.append(sample)
        if self.on_sample is not None:
            try:
                self.on_sample(sample)
            except Exception as exc:
                self.last_error = f"on_sample: {exc!r}"


# ---- request and answer ------------------------------------------------------

class UsedLength:
    """Follows a drive or capture file as it arrives and says where its last
    good block ends, once ``GARBAGE`` bytes past it have shown no other.

    A drive that power cut short has no END block and keeps the length it was
    preallocated with (16 MB, FORMAT.md): what follows the last block is
    whatever the card held. Every block's CRC is seeded with the file's nonce,
    so old data never passes for a block. A clean END stops it at once."""

    GARBAGE = 256 * 1024

    def __init__(self) -> None:
        self.buf = bytearray()
        self.nonce: int | None = None
        self.pos = 0
        self.used = 0
        self.off = False                    # not a drive file: never cut

    def __call__(self, data: bytes) -> int | None:
        if self.off:
            return None
        self.buf += data
        buf = self.buf
        if self.nonce is None:
            if len(buf) < F.FILE_HDR.size:
                return None
            fields = F.FILE_HDR.unpack_from(buf)
            if fields[0] != F.FILE_MAGIC:
                self.off = True
                return None
            self.nonce = fields[9]
            self.pos = self.used = fields[2]    # header length
        while self.pos + F.BLOCK_HDR.size <= len(buf):
            p = self.pos
            magic, kind, _, _, length, _, _ = F.BLOCK_HDR.unpack_from(buf, p)
            if magic == F.BLOCK_MAGIC and length <= F.MAX_PAYLOAD:
                end = p + F.BLOCK_HDR.size + length + 4
                if end > len(buf):
                    break                   # the rest of it is on the way
                crc = struct.unpack_from("<I", buf, end - 4)[0]
                if crc == F.block_crc(self.nonce, bytes(buf[p:end - 4])):
                    self.pos = self.used = end
                    if kind == F.B_END:
                        return end
                    continue
            nxt = buf.find(F.BLOCK_MAGIC_BYTES, p + 1)
            self.pos = nxt if nxt >= 0 else max(p + 1, len(buf) - 3)
        return self.used if len(buf) - self.used >= self.GARBAGE else None


@dataclass
class FileEntry:
    kind: int
    file_no: int
    size: int
    open: bool = False

    @property
    def name(self) -> str:
        return {P.F_DRIVE: f"DRIVES/D{self.file_no:06d}.HHD", P.F_CAPTURE: f"CAPTURES/C{self.file_no:06d}.HHD",
                P.F_MAP: "HANDHELD.MAP", P.F_CFG: "HANDHELD.CFG"}.get(self.kind, f"kind{self.kind}/{self.file_no}")


class CydClient:
    """One command at a time on a port this object owns (the app must not be
    connected to the handheld meanwhile). The live stream keeps arriving and
    is skipped over.

        with CydClient("cyd:") as cyd:
            for entry in cyd.list_files(P.F_DRIVE): ...
    """

    def __init__(self, port: Any = PREFIX, timeout: float = 1.5, retries: int = 4):
        self.port_name = port if isinstance(port, str) else getattr(port, "name", "cyd:")
        self.port = make_port(port) if isinstance(port, str) else port
        self.timeout = timeout
        self.retries = retries
        self.hello: P.Hello | None = None
        self.stats: dict[str, int] = {}
        self.frames = 0
        self._deframer = P.Deframer()
        self._seq = 0
        self._pending: collections.deque[tuple[int, int, bytes]] = collections.deque()
        self._opener: _Opener | None = None

    def __enter__(self) -> "CydClient":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def damaged(self) -> int:
        return self._deframer.bad

    def open(self, wait_s: float = 12.0) -> P.Hello:
        """Open the port and wait for the handheld's hello (it says one every
        2 s; the baud rates are tried in turn)."""
        self._opener = _Opener(self.port)
        self._opener.open()
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            for ftype, _seq, payload in self._frames(0.1):
                if ftype == P.SF_HELLO:
                    hello = P.parse_hello(payload)
                    if hello is not None:
                        self.hello = hello
                        return hello
            if self._opener.hunt(time.monotonic()):
                self._deframer = P.Deframer()
        self.close()
        raise CydError(f"no hello from a handheld on {self.port_name} (is it running, and is the link on? "
                       "HANDHELD.CFG: serial = 1)")

    def close(self) -> None:
        self.port.close()

    def _frames(self, timeout: float) -> list[tuple[int, int, bytes]]:
        out = list(self._pending)
        self._pending.clear()
        if not out:
            out = self._deframer.feed(self.port.read(timeout))
        for ftype, _seq, payload in out:
            self.frames += 1
            if self._opener is not None:
                self._opener.good()
            if ftype == P.SF_STATS:
                self.stats = P.parse_stats(payload) or self.stats
        return out

    def need_two_way(self) -> None:
        if self.hello is None:
            raise CydError("not open")
        if not self.hello.two_way:
            raise CydError(f"this handheld is tx-only ({self.hello.variant_name}: the GPS has the port's receive "
                           "pin), so it cannot take commands. Move the SD card to the laptop instead: "
                           "hondalog-handheld import /media/sd")

    def request(self, ftype: int, payload: bytes, accept: Callable[[int, int, bytes, int], Any],
                timeout: float | None = None, retries: int | None = None) -> Any:
        """Send a command; feed every frame that arrives to
        ``accept(type, seq, payload, sent_seq)`` until it returns something
        other than None. Sent again on a timeout."""
        self.need_two_way()
        timeout = self.timeout if timeout is None else timeout
        for _attempt in range(1 + (self.retries if retries is None else retries)):
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFF
            self.port.write(P.frame_encode(ftype, payload, seq))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                frames = self._frames(min(0.05, max(0.001, end - time.monotonic())))
                for i, (rtype, rseq, rpayload) in enumerate(frames):
                    result = accept(rtype, rseq, rpayload, seq)
                    if result is not None:
                        self._pending.extend(frames[i + 1:])
                        return result
        raise CydError("the handheld did not answer")

    def command(self, ftype: int, payload: bytes = b"", retries: int | None = None,
                timeout: float | None = None) -> tuple[int, int]:
        """A command answered by an ACK: (status, value)."""
        def accept(rtype: int, _rseq: int, p: bytes, seq: int) -> tuple[int, int] | None:
            if rtype != P.SF_ACK or len(p) < P.ACK.size:
                return None
            cmd, cseq, status, value = P.ACK.unpack(p[:P.ACK.size])
            return (status, value) if cmd == ftype and cseq == seq else None
        return self.request(ftype, payload, accept, timeout, retries)

    # -- engine --

    def mark(self) -> None:
        self.command(P.SC_MARK, retries=0)      # not sent twice: a second marker would be a lie

    def ack_alarm(self) -> None:
        self.command(P.SC_ACK_ALARM)

    def drive(self, on: bool) -> None:
        self.command(P.SC_DRIVE, bytes([1 if on else 0]))

    def request_dtc(self) -> None:
        self.command(P.SC_DTC, retries=0)

    def set_time(self, unix_s: float | None = None) -> bool:
        """Set the handheld's clock from this computer's. False: it keeps its
        own, because its GPS has already set it."""
        status, _ = self.command(P.SC_TIME_SET, struct.pack("<q", int((time.time() if unix_s is None else unix_s) * 1e6)))
        if status not in (P.OK, P.IGNORED):
            raise CydError(f"set-time: {P.status_text(status)}")
        return status == P.OK

    # -- files --

    def list_files(self, kind: int) -> list[FileEntry]:
        out: list[FileEntry] = []
        after = 0
        while True:
            page: list[FileEntry] = []

            def accept(rtype: int, _rseq: int, p: bytes, seq: int) -> Any:
                if rtype == P.SF_ACK and len(p) >= P.ACK.size:
                    cmd, cseq, status, _ = P.ACK.unpack(p[:P.ACK.size])
                    if cmd == P.SC_LIST_FILES and cseq == seq:
                        raise CydError(f"list files: {P.status_text(status)}")
                    return None
                if rtype != P.SF_FILE_INFO or len(p) < P.FILE_INFO.size:
                    return None
                fkind, flags, _, no, size = P.FILE_INFO.unpack(p[:P.FILE_INFO.size])
                if fkind != kind:
                    return None
                if no == P.NO_FILE:
                    return bool(flags & P.FI_MORE), size
                if no > after and all(e.file_no != no for e in page):
                    page.append(FileEntry(fkind, no, size, bool(flags & P.FI_OPEN)))
                return None

            more, count = self.request(P.SC_LIST_FILES, P.LIST.pack(kind, after), accept)
            if len(page) != count:          # a FILE_INFO frame was lost on the way: ask for the page again
                page.clear()
                more, count = self.request(P.SC_LIST_FILES, P.LIST.pack(kind, after), accept)
                if len(page) != count:
                    raise CydError("the file list arrived incomplete twice")
            out += page
            if not more or not page:
                return out
            after = page[-1].file_no

    def read_chunk(self, kind: int, file_no: int, offset: int, length: int = P.CHUNK) -> tuple[bytes, bool]:
        """(bytes, end of file). Each chunk carries its own CRC32; a damaged
        one is asked for again."""
        def accept(rtype: int, _rseq: int, p: bytes, seq: int) -> Any:
            if rtype == P.SF_ACK and len(p) >= P.ACK.size:
                cmd, cseq, status, _ = P.ACK.unpack(p[:P.ACK.size])
                if cmd == P.SC_READ_FILE and cseq == seq:
                    raise CydError(f"read file: {P.status_text(status)}")
                return None
            if rtype != P.SF_FILE_DATA or len(p) < P.FILE_DATA.size:
                return None
            fkind, flags, n, no, off, crc = P.FILE_DATA.unpack(p[:P.FILE_DATA.size])
            if fkind != kind or no != file_no:
                return None
            if off == P.NO_FILE:
                if flags & P.FD_BUSY:
                    raise CydBusy(f"{FileEntry(kind, file_no, 0).name} is still being written")
                raise CydError(f"{FileEntry(kind, file_no, 0).name}: no such file on the handheld")
            data = p[P.FILE_DATA.size:P.FILE_DATA.size + n]
            if off != offset or len(data) != n or zlib.crc32(data) & 0xFFFFFFFF != crc:
                return None                 # an answer to an older request, or damaged: wait, then ask again
            return data, bool(flags & P.FD_EOF)
        return self.request(P.SC_READ_FILE, P.READ_FILE.pack(kind, length, file_no, offset), accept)

    def fetch(self, entry: FileEntry, dest: str | Path, progress: Callable[[int, int], None] | None = None,
              cut: Callable[[bytes], int | None] | None = None) -> Path:
        """Download a file to ``dest``. Bytes go to ``dest + ".part"`` as they
        arrive; if that file is there from an interrupted transfer (and still
        starts like the handheld's file) the download carries on after it.

        ``cut(new_bytes)`` sees the file as it arrives and may return a length:
        the rest is not wanted (``UsedLength``: the preallocated tail of a
        drive file that power cut short). The file is cut there and kept."""
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        offset = 0
        if part.exists():
            have = part.stat().st_size
            head_len = min(have, P.CHUNK)
            if 0 < have <= entry.size and head_len:
                with open(part, "rb") as fh:
                    head = fh.read(head_len)
                first, _ = self.read_chunk(entry.kind, entry.file_no, 0, head_len)
                if first == head:
                    offset = have
        stop = None
        if cut is not None and offset:
            stop = cut(part.read_bytes())
        with open(part, "ab" if offset else "wb") as fh:
            done = offset >= entry.size and entry.size > 0
            while not done and stop is None:
                data, done = self.read_chunk(entry.kind, entry.file_no, offset)
                fh.write(data)
                fh.flush()
                offset += len(data)
                if progress:
                    progress(offset, entry.size)
                if not data and not done:
                    raise CydError(f"{entry.name}: the handheld sent an empty chunk before the end")
                if cut is not None:
                    stop = cut(data)
            if stop is not None and stop < offset:
                fh.truncate(stop)
        os.replace(part, dest)
        return dest

    def push(self, kind: int, data: bytes, progress: Callable[[int, int], None] | None = None) -> int:
        """Send HANDHELD.MAP (``F_MAP``) or HANDHELD.CFG (``F_CFG``). The
        handheld checks it (length, CRC, and that a map loads) before it
        replaces the old file, and uses it from its next start. An
        interrupted push carries on where it stopped. Returns the handheld's
        note: for a CFG, the number of lines it will skip."""
        crc = zlib.crc32(data) & 0xFFFFFFFF
        status, offset = self.command(P.SC_PUT_BEGIN, P.PUT_BEGIN.pack(kind, len(data), crc))
        if status != P.OK:
            raise CydError(f"push: {P.status_text(status)}")
        step = 400
        while offset < len(data):
            chunk = data[offset:offset + step]
            status, value = self.command(P.SC_PUT_DATA, struct.pack("<I", offset) + chunk)
            if status == P.E_OFFSET:
                offset = value              # it has more (or less) than we thought: go from there
                continue
            if status != P.OK:
                raise CydError(f"push: {P.status_text(status)} at byte {offset}")
            offset = value
            if progress:
                progress(offset, len(data))
        status, value = self.command(P.SC_PUT_END, timeout=5.0)
        if status != P.OK:
            raise CydError(f"push: {P.status_text(status)}")
        return value


__all__ = ["CH340_ID", "CydBusy", "CydClient", "CydError", "CydEvent", "CydPoller", "FileEntry", "PREFIX",
           "SerialPort", "UDEV_SYMLINK", "UsedLength", "find_device", "is_cyd_port", "is_sim_port", "list_ports", "make_port"]
