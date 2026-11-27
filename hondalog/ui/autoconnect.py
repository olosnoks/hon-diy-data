"""Pick the ECU source by itself, at start-up and whenever a cable comes or goes.

    KKL cable (FTDI 0403:6001) on the laptop   -> the laptop reads the ECU; a CYD
                                                  on USB next to it is the companion
                                                  (GPS, relay), as before
    no KKL cable, a CYD (CH340 1a86:7523)       -> ``cyd:<port>``: the CYD reads the
                                                  ECU on its own K-line cable and
                                                  streams every raw read
    neither                                     -> not connected (nothing is shown:
                                                  no simulator is ever started here)

A KKL cable that turns up while the CYD is the source wins: the CYD link is
closed first, then the cable is opened (and the CYD becomes its companion).
While the CYD is the source the laptop never opens a K-line, a relay or a
second link to the CYD (``LiveSession.connect`` opens no companion on ``cyd:``
ports).

Only connections this object made are switched. A port the user picks by
hand, or a disconnect by hand, pauses it until ``resume()`` (Setup > Cable's
"Pick the cable by itself").

The port scan runs on a thread of its own (pyserial's listing can take tens
of milliseconds on Windows); ``tick()`` on the UI thread acts on the last
scan. Tests call ``step(found, now)`` with made-up ports instead.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

KKL_ID = (0x0403, 0x6001)           # FTDI FT232R: the KKL cable
FTDI_VID = 0x0403
CH340_ID = (0x1A86, 0x7523)         # the CYD's USB-C serial
CYD_PREFIX = "cyd:"

KKL, CYD, SIM = "kkl", "cyd", "sim"
SCAN_S = 2.0                        # how often the ports are listed
RETRY_S = 5.0                       # after a connect, wait this long before trying another
MISSING_SCANS = 2                   # a cable is gone after this many scans without it (a replug blip is not)


@dataclass(frozen=True)
class Found:
    """One serial port as the scan saw it."""
    device: str
    vid: int | None = None
    pid: int | None = None

    @property
    def is_kkl(self) -> bool:
        return (self.vid, self.pid) == KKL_ID

    @property
    def is_ftdi(self) -> bool:
        return self.vid == FTDI_VID

    @property
    def is_cyd(self) -> bool:
        return (self.vid, self.pid) == CH340_ID


def scan() -> list[Found]:
    """The USB serial ports there are now (pyserial)."""
    try:
        from serial.tools import list_ports as lp
        return [Found(str(p.device), p.vid, p.pid) for p in sorted(lp.comports(), key=lambda p: p.device)]
    except Exception:
        return []


def _same(a: str, b: str) -> bool:
    """Port names compare without case on Windows (COM21 / com21)."""
    if os.name == "nt":
        return a.upper() == b.upper()
    return a == b or os.path.realpath(a) == os.path.realpath(b)


def device_of(port: str) -> str:
    """The device behind a port name: ``cyd:COM24@460800`` -> ``COM24``."""
    if port.startswith(CYD_PREFIX):
        port = port[len(CYD_PREFIX):]
    return port.partition("@")[0]


def source_kind(port: str) -> str:
    """What a connected port is: KKL (the laptop's own K-line cable), CYD
    (the handheld reads the ECU) or SIM (a simulator, only ever by hand)."""
    if not port:
        return ""
    if port.startswith(CYD_PREFIX):
        return SIM if port.startswith(CYD_PREFIX + "sim") else CYD
    if port.startswith("sim:") or "sim" in os.path.basename(port):
        return SIM
    return KKL


def is_explicit(port: str) -> bool:
    """A --port that names a particular source (a simulator, ``cyd:``...):
    connect to exactly that, no automatic pick. A plain device name (COM21,
    /dev/ttyUSB0) is only the cable to prefer."""
    return port.startswith(("sim:", CYD_PREFIX)) or "sim" in os.path.basename(port)


def pick(found: Iterable[Found], preferred: str = "") -> tuple[str, str]:
    """(port, kind) to connect: the KKL cable (``preferred`` first when it is
    there), else the CYD as ``cyd:<device>``, else ("", "")."""
    found = list(found)
    cyds = [f for f in found if f.is_cyd]
    if preferred:
        for f in found:
            if _same(f.device, preferred) and not f.is_cyd:
                return f.device, KKL
    for test in (lambda f: f.is_kkl, lambda f: f.is_ftdi):
        for f in found:
            if test(f):
                return f.device, KKL
    if cyds:
        return CYD_PREFIX + cyds[0].device, CYD
    return "", ""


class AutoConnect:
    """See the module text. ``connect(port)`` and ``disconnect()`` act on the
    session (the shell's, so the status line says what happened)."""

    def __init__(self, session, connect: Callable[[str], None], disconnect: Callable[[], None] | None = None,
                 preferred: str = "", scanner: Callable[[], list[Found]] = scan,
                 clock: Callable[[], float] = time.monotonic):
        self.session = session
        self._connect = connect
        self._disconnect = disconnect or session.disconnect
        self.preferred = preferred
        self.scanner = scanner
        self.clock = clock
        self.paused = False
        #: the port this object connected (None: the connection, if any, is the user's)
        self.mine: str | None = None
        self.last_found: list[Found] | None = None
        self._next_try = 0.0
        self._missing = 0
        self._seen_scan = -1
        self._scan_no = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- the scan thread ----

    def start(self) -> "AutoConnect":
        if self._thread is None:
            self._thread = threading.Thread(target=self._scan_loop, name="hondalog-autoconnect", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(1.0)

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            found = self.scanner()
            self.last_found = found
            self._scan_no += 1
            self._stop.wait(SCAN_S)

    def scan_now(self) -> list[Found]:
        """List the ports on this thread (Setup's buttons, start-up)."""
        found = self.scanner()
        self.last_found = found
        self._scan_no += 1
        return found

    # ---- the user ----

    def pause(self) -> None:
        """A connect or disconnect by hand: leave the port alone until resume()."""
        self.paused = True
        self.mine = None

    def resume(self) -> str:
        """Pick by itself again; a connection that is up now is adopted (a KKL cable still wins over the CYD)."""
        self.paused = False
        self.mine = self.session.port if self._connected() else None
        self._next_try = 0.0
        self._seen_scan = -1
        return "Cable: picked by itself (the KKL cable first, else through the CYD)"

    @property
    def active(self) -> bool:
        return not self.paused

    # ---- the UI thread ----

    def tick(self, now: float | None = None) -> str:
        """Once per frame: act on a scan that has not been looked at. Returns a line for the status bar, or ''."""
        found = self.last_found
        if found is None or self._scan_no == self._seen_scan:
            return ""
        self._seen_scan = self._scan_no
        return self.step(found, self.clock() if now is None else now)

    def _connected(self) -> bool:
        ecu = getattr(self.session, "ecu", None)
        if ecu is not None and hasattr(ecu, "poller"):
            return ecu.poller is not None
        return bool(getattr(self.session, "connected", False))

    def _open(self, port: str, kind: str, now: float) -> None:
        self.mine = port
        self._missing = 0
        self._next_try = now + RETRY_S
        self._connect(port)

    def step(self, found: list[Found], now: float) -> str:
        """The decision, for one scan. Returns what it did, for the status bar."""
        if self.paused or getattr(self.session, "demo", False):
            return ""
        want, kind = pick(found, self.preferred)
        if not self._connected():
            if want and now >= self._next_try:
                self._open(want, kind, now)
                return (f"KKL cable on {want}: connecting" if kind == KKL
                        else f"No KKL cable: connecting through the CYD on {device_of(want)}")
            return ""
        port = self.session.port or ""
        if self.mine is None or not _same(port, self.mine):
            return ""                       # the user's connection
        current = source_kind(port)
        present = any(_same(f.device, device_of(port)) for f in found)
        self._missing = 0 if present else self._missing + 1
        if current == CYD and kind == KKL:
            # the KKL cable wins: the CYD's link closes before the cable opens, so
            # the laptop and the CYD never both read the K-line
            self._disconnect()
            self._open(want, kind, now)
            return f"KKL cable on {want}: switching from the CYD to it"
        if self._missing >= MISSING_SCANS and want and not _same(want, port) and now >= self._next_try:
            self._disconnect()
            self._open(want, kind, now)
            return (f"{device_of(port)} is gone: connecting through the CYD on {device_of(want)}" if kind == CYD
                    else f"{device_of(port)} is gone: connecting to the KKL cable on {want}")
        return ""


__all__ = ["AutoConnect", "CH340_ID", "CYD", "Found", "KKL", "KKL_ID", "SIM", "device_of", "is_explicit", "pick",
           "scan", "source_kind"]
