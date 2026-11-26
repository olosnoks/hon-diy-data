"""The handheld next to the laptop's own KKL cable (firmware/handheld/SERIAL.md).

When the app reads the ECU through the laptop's cable (``Poller`` on a
non-``cyd:`` port) and the handheld is on USB at the same time, its serial
link is opened as well, for what only the handheld has: its GPS (fix, sats,
PPS clock). ``HandheldCompanion`` is a ``CydPoller`` (the same reader, baud
hunt, heartbeat and clock exchange, the same GPS decode) that is *not* the
channel source: its fixes go to ``on_fix`` on the ECU poller's time axis
(``t0``), and ``LiveSession`` records them in the ``gps`` stream as source
``handheld``. With a fix they win over the laptop's own location.

Port choice (``spec``): ``auto`` looks for a handheld as ``cyd:`` does (the
udev link, a CH340 ``1a86:7523`` such as ``COM22``, the simulator's pty) and
never takes the ECU's port; ``off`` opens nothing; anything else names the
port (``COM22``, ``/dev/ttyUSB0``, ``cyd:COM22@460800``, ``cyd:sim``).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Mapping

from ..addressmap import AddressMap
from ..poller import PORT_LOST
from . import serial_link as SL

AUTO, OFF = "auto", "off"
SOURCE = "handheld"
#: a fix older than this no longer counts: the laptop's location takes over again
GPS_FRESH_S = 5.0
#: how often "auto" looks for a handheld that is not there
RESCAN_S = 2.0


def _norm(name: str) -> str:
    """A port name for comparison: no ``cyd:`` prefix or ``@baud``; COM names
    in capitals, paths through their links."""
    name = name[len(SL.PREFIX):] if name.startswith(SL.PREFIX) else name
    name = name.partition("@")[0].strip()
    if not name:
        return ""
    if name.upper().startswith("COM") or name.startswith("\\\\.\\"):
        return name.upper().replace("\\\\.\\", "")
    if sys.platform != "win32" and os.path.exists(name):
        return os.path.realpath(name)
    return name


def same_port(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return bool(na) and na == nb


def find_handheld(exclude: str = "") -> str | None:
    """A ``cyd:`` port name for a handheld that is not ``exclude`` (the ECU's port), or None."""
    for name, _label in SL.list_ports():
        if not same_port(name, exclude):
            return name
    return None


def spec_port(spec: str) -> str:
    """``COM22`` -> ``cyd:COM22``; ``cyd:...`` stays."""
    return spec if spec.startswith(SL.PREFIX) else SL.PREFIX + spec


class HandheldCompanion(SL.CydPoller):
    """The handheld's link while the laptop owns the K-line. See the module text.

    ``on_fix(t, fix)``: session seconds (on ``t0``) and the ``gps`` stream's
    columns, for fixes only (no-fix records update ``sats`` but are not passed on).
    """

    source = SOURCE

    def __init__(self, spec: Any, amap: AddressMap, exclude: str = "", t0: float | None = None,
                 on_fix: Callable[[float, Mapping[str, Any]], None] | None = None):
        self.spec = spec if not isinstance(spec, str) else (spec.strip() or AUTO)
        self.exclude = exclude
        auto = isinstance(self.spec, str) and self.spec.lower() == AUTO
        port: Any = None if auto else (spec_port(self.spec) if isinstance(self.spec, str) else self.spec)
        super().__init__(port if port is not None else SL.PREFIX, amap, t0=t0)
        self.name = "hondalog-handheld"
        self.auto = auto
        self.on_fix = on_fix
        self.on_gps = self._gps
        self.last_fix: dict[str, Any] | None = None
        self.last_fix_at = 0.0              # monotonic, of the last record with a fix
        self.sats = 0
        self.fixes = 0
        self._next_scan = 0.0
        self.found = ""                     # the port "auto" picked
        if not auto and isinstance(self.spec, str) and same_port(self.spec, exclude):
            self._refused = f"{self.spec} is the ECU's port"
        else:
            self._refused = ""

    # -- GPS --

    def _gps(self, t: float, fix: Mapping[str, Any]) -> None:
        self.sats = int(fix.get("gps_sats") or 0)
        if not fix.get("gps_fix"):
            return
        self.last_fix = dict(fix)
        self.last_fix_at = time.monotonic()
        self.fixes += 1
        if self.on_fix is not None:
            self.on_fix(t, fix)

    @property
    def gps_ok(self) -> bool:
        """A fix from the handheld in the last GPS_FRESH_S seconds."""
        return self.last_fix is not None and time.monotonic() - self.last_fix_at < GPS_FRESH_S

    @property
    def starting(self) -> bool:
        """Opened a moment ago, no hello yet: its GPS may be about to arrive."""
        return self.hello is None and self.state != PORT_LOST and time.monotonic() - self._t_open < SL.HUNT_S + 1.0

    def gps_text(self) -> str:
        if self.gps_ok:
            return f"GPS: handheld ({self.sats} sats)"
        return ""

    def status_text(self) -> str:
        """One short line: which handheld, or why there is none."""
        if self._refused:
            return f"handheld: {self._refused}"
        h = self.hello
        if h is None:
            if self.state == PORT_LOST:
                return f"handheld: {self.last_error.removeprefix('port: ')}" if self.last_error else "handheld: none"
            return "handheld: connecting"
        return f"handheld {h.device} fw {h.fw}"

    # -- the port: "auto" looks again after every loss, never at the ECU's port --

    def _open(self) -> None:
        if self._refused:
            self.state = PORT_LOST
            self.last_error = self._refused
            self.ready.set()
            return
        if self.auto and self.port is None:
            now = time.monotonic()
            if now < self._next_scan:
                self.state = PORT_LOST
                return
            self._next_scan = now + RESCAN_S
            found = find_handheld(self.exclude)
            if found is None:
                self.state = PORT_LOST
                self.last_error = "no handheld found"
                self.ready.set()
                return
            self.found = found
            self._port_arg = self.port_name = found
        super()._open()

    def _port_lost(self, exc: Exception) -> None:
        super()._port_lost(exc)
        if self.auto:
            self.port = None                # it may come back on another port
            self._port_arg = SL.PREFIX
