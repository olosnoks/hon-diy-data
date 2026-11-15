"""Live link to the black box over USB CDC.

``BlackboxReader`` is to the black box what ``hondalog.poller.Poller`` is to
the ECU: a thread that owns the port and turns what arrives into
``hondalog.poller.Sample`` objects. The attributes the UI reads off a Poller
(``samples``, ``on_sample``, ``state``, ``hz``, ``reads``, ``errors``,
``last_error``, ``ready``, ``t0``, ``stop()``) exist here with the same
meaning, so both can be treated as channel sources. Channel names come from
``channels.LIVE_NAMES`` and never clash with ECU names (``bb_`` prefix).

Extra, black box only: ``events`` (triggers and markers as they happen),
``clock`` (offset between the box's clock and the laptop's), ``mark()``,
``trigger()``, config access and event download.
"""

from __future__ import annotations

import collections
import queue
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..poller import CONNECTING, NO_REPLY, OK, PORT_LOST, STOPPED, Sample
from . import format as F
from .channels import BB_CHANNELS, LIVE_NAMES, bb_channel

UDEV_SYMLINK = "/dev/hondalog-bb"
ESP_USB_ID = (0x303A, 0x1001)           # ESP32-S3 built-in USB serial/JTAG


class BlackboxError(Exception):
    """The box did not answer, or the port failed."""


class SerialPort:
    """The box's USB CDC port (or the pty that hondalog-bbsim makes)."""

    def __init__(self, name: str):
        self.name = name
        self._ser = None

    def open(self) -> None:
        import serial
        try:
            # CDC ignores the baud rate; DTR/RTS stay low so an ESP32 is not reset
            self._ser = serial.Serial()
            self._ser.port = self.name
            self._ser.baudrate = 115200
            self._ser.timeout = 0.05
            self._ser.write_timeout = 1.0
            self._ser.dtr = False
            self._ser.rts = False
            self._ser.open()
        except (serial.SerialException, OSError) as exc:
            self._ser = None
            raise BlackboxError(str(exc)) from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except Exception as exc:
            raise BlackboxError(str(exc)) from exc

    def read(self, timeout: float) -> bytes:
        try:
            self._ser.timeout = timeout
            data = self._ser.read(1)
            waiting = self._ser.in_waiting
            return data + (self._ser.read(waiting) if waiting else b"")
        except Exception as exc:
            raise BlackboxError(str(exc)) from exc


def make_port(name: str):
    """``bbsim:`` (optionally ``bbsim:main_relay@20``) is the built-in
    simulator; anything else is a serial device path."""
    if name.startswith("bbsim:"):
        from .sim import SimPort, device_from_port
        return SimPort(device_from_port(name))
    return SerialPort(name)


def list_ports() -> list[tuple[str, str]]:
    """(device, label) candidates, best guess first."""
    import os
    out = []
    if os.path.exists(UDEV_SYMLINK):
        out.append((UDEV_SYMLINK, f"{UDEV_SYMLINK}  (black box, udev link)"))
    try:
        from serial.tools import list_ports as lp
        for p in sorted(lp.comports(), key=lambda p: p.device):
            if (p.vid, p.pid) == ESP_USB_ID:
                out.append((p.device, f"{p.device}  (ESP32-S3 USB serial {p.vid:04x}:{p.pid:04x})"))
    except Exception:
        pass
    from .sim import BBSIM_SYMLINK
    if os.path.exists(BBSIM_SYMLINK):
        out.append((BBSIM_SYMLINK, f"{BBSIM_SYMLINK}  (hondalog-bbsim pty)"))
    out.append(("bbsim:", "bbsim:  (built-in black box simulator)"))
    return out


# ---- clock ------------------------------------------------------------------

class ClockSync:
    """Offset between the box clock and the laptop's monotonic clock.

    Each exchange gives ``offset = host_midpoint - device_midpoint`` and a
    round-trip time. The exchange with the shortest round trip in the recent
    window has the least USB jitter in it, so that one is used.
    ``host_us = device_us + offset_us``.
    """

    def __init__(self, window: int = 16):
        self._recent: collections.deque = collections.deque(maxlen=window)
        self.offset_us: float | None = None
        self.rtt_us: float | None = None        # round trip of the exchange in use
        self.count = 0

    @property
    def valid(self) -> bool:
        return self.offset_us is not None

    def add(self, host_tx_us: int, dev_rx_us: int, dev_tx_us: int, host_rx_us: int) -> None:
        rtt = (host_rx_us - host_tx_us) - (dev_tx_us - dev_rx_us)
        if rtt < 0:
            return
        offset = (host_tx_us + host_rx_us) / 2 - (dev_rx_us + dev_tx_us) / 2
        self._recent.append((rtt, offset))
        self.rtt_us, self.offset_us = min(self._recent)
        self.count += 1

    def to_host_us(self, dev_us: int) -> float:
        return dev_us + (self.offset_us or 0.0)


@dataclass
class Hello:
    proto: int
    file_ver: int
    fw: str
    device: str
    sample_hz: int
    live_hz: int
    boot_no: int
    event_count: int


@dataclass
class BBEvent:
    """A trigger or marker reported live by the box."""
    t_dev_us: int
    code: int
    src: int
    value: int
    event_no: int           # number of the event file this started, else 0
    started_file: bool
    t: float                # seconds on the reader's time axis (same as Sample.t)
    wall: datetime

    @property
    def name(self) -> str:
        return F.reason_name(self.code)

    @property
    def text(self) -> str:
        return F.reason_text(self.code)

    @property
    def is_trigger(self) -> bool:
        return 1 <= self.code <= F.R_MAX_TRIGGER


@dataclass
class FileInfo:
    event_no: int
    size: int
    reason: int
    complete: bool

    @property
    def reason_name(self) -> str:
        return F.reason_name(self.reason)


def decode_live(payload: bytes) -> tuple[int, dict, int, int, int]:
    """LIVE frame -> (device_us, values, state_bits, event_count, last_reason)."""
    t_us, rec, m0, m1, m2, m3, m4, events, state, last_reason, _ = F.LIVE.unpack(payload[:F.LIVE.size])
    mv = struct.unpack_from("<5H", rec, 0)
    rpm_tach, rpm_ign, pw, duty, dig = struct.unpack_from("<HHHBB", rec, 18)
    values = {
        "bb_vbat": mv[0] / 1000, "bb_ecub": mv[1] / 1000, "bb_pump": mv[2] / 1000,
        "bb_ig1": mv[3] / 1000, "bb_alt": mv[4] / 1000,
        # lowest since the previous frame: a dip shorter than a frame still shows
        "bb_vbat_min": m0 / 1000, "bb_ecub_min": m1 / 1000,
        "bb_rpm_tach": float(rpm_tach), "bb_rpm_ign": float(rpm_ign),
        "bb_inj_pw": pw / 1000, "bb_inj_duty": duty / 2,
        "bb_button": float(bool(dig & F.DIG_BUTTON)), "bb_ig1_on": float(bool(dig & F.DIG_IG1_ON)),
        "bb_running": float(bool(dig & F.DIG_RUNNING)),
    }
    return t_us, values, state, events, last_reason


class BlackboxReader(threading.Thread):
    """Reads the live stream; see the module docstring.

    ``t0`` is the monotonic time that ``Sample.t`` counts from. Pass the ECU
    poller's ``t0`` to put both sources on one time axis.
    """

    channels = BB_CHANNELS
    names = LIVE_NAMES

    def __init__(self, port, on_sample=None, on_event=None, max_queue: int = 2000,
                 t0: float | None = None, sync_period_s: float = 1.0, live_hz: int | None = None):
        super().__init__(name="hondalog-blackbox", daemon=True)
        self.port = make_port(port) if isinstance(port, str) else port
        self.port_name = getattr(self.port, "name", "?")
        self.on_sample = on_sample              # called on this thread
        self.on_event = on_event                # called on this thread with a BBEvent
        self.samples: collections.deque[Sample] = collections.deque(maxlen=max_queue)
        self.events: queue.Queue = queue.Queue()
        self.clock = ClockSync()
        self.t0 = time.monotonic() if t0 is None else t0
        self.ready = threading.Event()          # set once the box said hello (or failed to)
        self.state = CONNECTING
        self.reads = 0                          # good frames
        self.errors = 0                         # bad frames + port errors
        self.hz = 0.0
        self.last_error = ""
        self.hello: Hello | None = None
        self.status_bits = 0                    # format.ST_* from the last live frame
        self.event_count = 0
        self.last_reason = 0
        self._sync_period = sync_period_s
        self._live_hz = live_hz
        self._stop_evt = threading.Event()
        self._wlock = threading.Lock()
        self._rlock = threading.Lock()
        self._last_gap = 0.0
        self._seq = 0
        self._replies: queue.Queue = queue.Queue()      # (type, payload) for request()
        self._waiting: tuple = ()
        self._deframer = F.Deframer()
        self._last_live = None
        self._last_rx = 0.0
        self._time_set_offset = None

    # -- API for other threads --

    def channel(self, name: str):
        return bb_channel(name)

    @property
    def device_text(self) -> str:
        if self.hello is None:
            return "(no reply)"
        h = self.hello
        return f"black box {h.device} fw {h.fw}, boot {h.boot_no}, {h.event_count} events on card"

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout)

    def send(self, ftype: int, payload: bytes = b"") -> None:
        """Send one frame. Raises BlackboxError if the port is gone."""
        with self._wlock:
            if self.state in (PORT_LOST, STOPPED):
                raise BlackboxError(self.last_error or "not connected")
            self.port.write(F.frame_encode(ftype, payload, self._seq))
            self._seq = (self._seq + 1) & 0xFF

    def mark(self, number: int) -> None:
        """Stamp a marker into the box's record. Call it with the same number
        the ECU log's ``MARK n`` gets; alignment can then match them up."""
        self.send(F.F_MARK, struct.pack("<i", number))

    def trigger(self) -> None:
        """Save a capture now (reason "host")."""
        self.send(F.F_TRIGGER)

    def set_live_rate(self, hz: int) -> None:
        self.send(F.F_LIVE_RATE, struct.pack("<H", hz))

    def _exchange(self, ftype: int, payload: bytes, want: int, handler, timeout: float = 3.0):
        """Send a request and feed each reply of type ``want`` to
        ``handler(payload)`` until it returns something other than None."""
        with self._rlock:                       # one request at a time
            while not self._replies.empty():
                self._replies.get_nowait()
            self._waiting = (want,)
            try:
                self.send(ftype, payload)
                while True:
                    try:
                        _type, reply = self._replies.get(timeout=timeout)
                    except queue.Empty:
                        raise BlackboxError("the black box did not answer") from None
                    result = handler(reply)
                    if result is not None:
                        return result
            finally:
                self._waiting = ()

    def _text_until(self, ftype: int, payload: bytes, done, timeout: float = 3.0) -> str:
        text = []

        def handler(reply):
            text.append(reply.decode("ascii", "replace"))
            return "".join(text) if done("".join(text)) else None
        return self._exchange(ftype, payload, F.F_TEXT, handler, timeout)

    def get_config(self) -> str:
        """The box's config as text (see ``triggers.Config.parse``)."""
        text = self._text_until(F.F_GET_CONFIG, b"", lambda t: t.endswith("# end\n"))
        return text[:-len("# end\n")]

    def set_config(self, key: str, value) -> None:
        """Change one setting in RAM, e.g.
        ``set_config("rule.6", "vbat < 10500 500 20 running capture")``.
        ``save_config()`` writes the lot to the SD card."""
        line = f"{key}={value}"
        reply = self._text_until(F.F_SET_CONFIG, line.encode("ascii"), lambda t: t.endswith("\n"))
        if not reply.startswith("ok"):
            raise BlackboxError(f"the box rejected {line!r}")

    def save_config(self) -> None:
        reply = self._text_until(F.F_SAVE_CONFIG, b"", lambda t: t.endswith("\n"))
        if not reply.startswith("ok"):
            raise BlackboxError("the box could not write BLACKBOX.CFG")

    def selftest(self) -> str:
        """Run the box's self-test; returns its report (last line ``selftest: PASS`` or ``FAIL``)."""
        return self._text_until(F.F_SELFTEST, b"", lambda t: "selftest: " in t, timeout=15.0)

    def list_events(self) -> list[FileInfo]:
        """The newest event files on the card (up to 32), newest first."""
        out = []

        def handler(p):
            no, size, reason, complete = F.FILE_INFO.unpack(p[:F.FILE_INFO.size])
            if no == 0xFFFFFFFF:
                return out
            out.append(FileInfo(no, size, reason, bool(complete)))
            return None
        return self._exchange(F.F_LIST_FILES, b"", F.F_FILE_INFO, handler)

    def fetch_event(self, event_no: int, dest_dir, progress=None) -> Path:
        """Download ``EVnnnnnn.BBX`` into ``dest_dir`` and return the path.
        ``progress(bytes_done)`` is called as it goes."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        data = bytearray()

        def handler(p):
            no, offset = struct.unpack_from("<II", p)
            if no != event_no:
                return None
            if offset == 0xFFFFFFFF:
                raise BlackboxError(f"event {event_no} is not on the card (or is still being written)")
            return p[8:] if offset == len(data) else None

        while True:
            chunk = self._exchange(F.F_READ_FILE, F.READ_FILE.pack(event_no, len(data), F.FILE_CHUNK),
                                   F.F_FILE_DATA, handler)
            data += chunk
            if progress:
                progress(len(data))
            if len(chunk) < F.FILE_CHUNK:
                break
        stamp = ""
        if len(data) >= F.FILE_HDR.size:
            h = F.FILE_HDR.unpack_from(data)
            if h[8] & F.HF_TIME_VALID:
                stamp = datetime.fromtimestamp((h[13] + h[12]) / 1e6).strftime("_%Y%m%d_%H%M%S")
        path = dest / f"bb{stamp}_EV{event_no:06d}.bbx"
        path.write_bytes(data)
        return path

    # -- thread body --

    def run(self) -> None:
        try:
            self._open()
            next_sync = 0.0
            while not self._stop_evt.is_set():
                if self.state == PORT_LOST:
                    self._stop_evt.wait(0.5)
                    self._open()
                    continue
                now = time.monotonic()
                try:
                    if now >= next_sync:
                        # fast at first to settle the offset, then once a period
                        next_sync = now + (0.1 if self.clock.count < 5 else self._sync_period)
                        if self.hello is None:
                            self.send(F.F_GET_HELLO)
                        self.send(F.F_TIME_REQ, struct.pack("<q", int(now * 1e6)))
                    data = self.port.read(0.05)
                    now = time.monotonic()
                    if data:
                        self._last_rx = now
                        before = self._deframer.bad
                        for ftype, _seq, payload in self._deframer.feed(data):
                            self._frame(ftype, payload, now)
                        self.errors += self._deframer.bad - before
                except BlackboxError as exc:
                    self._port_lost(exc)
                    continue
                if data:
                    continue
                if self.state == OK and now - self._last_rx > 1.0:
                    self.state = NO_REPLY
                    self.hz = 0.0
                    self.last_error = "no data from the black box for 1 s"
                elif self.state == CONNECTING and now - self.t_open > 2.0:
                    self.state = NO_REPLY
                    self.last_error = "the black box did not say hello"
                    self.ready.set()
                if self.state == NO_REPLY and now - self._last_gap >= 0.25:
                    self._last_gap = now
                    self._emit(None, {}, False)     # keeps the time axis moving, like the poller
        finally:
            self.ready.set()
            self.state = STOPPED
            self.port.close()

    def _open(self) -> None:
        self.t_open = time.monotonic()
        try:
            self.port.open()
        except BlackboxError as exc:
            self._port_lost(exc)
            self.ready.set()
            return
        self.state = CONNECTING
        self.hello = None
        self._last_rx = self.t_open

    def _port_lost(self, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"port: {exc}"
        self.state = PORT_LOST
        self.hz = 0.0
        self.port.close()

    def _frame(self, ftype: int, p: bytes, now: float) -> None:
        self.reads += 1
        if ftype in self._waiting:
            self._replies.put((ftype, p))
        if ftype == F.F_LIVE and len(p) >= F.LIVE.size:
            t_us, values, self.status_bits, self.event_count, self.last_reason = decode_live(p)
            self.state = OK
            if self._last_live is not None and t_us > self._last_live:
                inst = 1e6 / (t_us - self._last_live)
                self.hz = inst if self.hz == 0 else 0.8 * self.hz + 0.2 * inst
            self._last_live = t_us
            self._emit(t_us, values, True)
        elif ftype == F.F_HELLO and len(p) >= F.HELLO.size:
            proto, fver, fw, dev, hz, live_hz, boot, events, _, _ = F.HELLO.unpack(p[:F.HELLO.size])
            self.hello = Hello(proto, fver, fw.split(b"\0")[0].decode("ascii", "replace"),
                               dev.split(b"\0")[0].decode("ascii", "replace"), hz, live_hz, boot, events)
            self.event_count = events
            if self._live_hz:
                self.send(F.F_LIVE_RATE, struct.pack("<H", self._live_hz))
            self.ready.set()
        elif ftype == F.F_TIME_RSP and len(p) >= F.TIME_RSP.size:
            host_tx, dev_rx, dev_tx = F.TIME_RSP.unpack(p[:F.TIME_RSP.size])
            self.clock.add(host_tx, dev_rx, dev_tx, int(now * 1e6))
            self._maybe_set_time()
        elif ftype == F.F_EVENT and len(p) >= F.EVENT_MSG.size:
            t_us, code, src, _act, value, event_no, started = F.EVENT_MSG.unpack(p[:F.EVENT_MSG.size])
            host_s = self.clock.to_host_us(t_us) / 1e6 if self.clock.valid else now
            ev = BBEvent(t_us, code, src, value, event_no, bool(started), host_s - self.t0,
                         datetime.fromtimestamp(time.time() - (now - host_s)))
            self.events.put(ev)
            if self.on_event is not None:
                try:
                    self.on_event(ev)
                except Exception as exc:
                    self.last_error = f"on_event: {exc!r}"

    def _maybe_set_time(self) -> None:
        """Tell the box what wall-clock time its t=0 was, so event files carry
        laptop time. Re-sent when the estimate moves by more than 2 ms."""
        if self.clock.count < 3:
            return
        mono_to_unix = time.time() - time.monotonic()
        unix_at_t0 = int(self.clock.offset_us + mono_to_unix * 1e6)
        if self._time_set_offset is None or abs(unix_at_t0 - self._time_set_offset) > 2000:
            self._time_set_offset = unix_at_t0
            self.send(F.F_TIME_SET, struct.pack("<q", unix_at_t0))

    def _emit(self, t_dev_us, values: dict, link: bool) -> None:
        now = time.monotonic()
        if link and self.clock.valid:
            host = self.clock.to_host_us(t_dev_us) / 1e6       # when it was measured,
            host = min(host, now)                               # not when it arrived
        else:
            host = now
        sample = Sample(host - self.t0, datetime.fromtimestamp(time.time() - (now - host)), link, values)
        self.samples.append(sample)
        if self.on_sample is not None:
            try:
                self.on_sample(sample)
            except Exception as exc:
                self.last_error = f"on_sample: {exc!r}"
