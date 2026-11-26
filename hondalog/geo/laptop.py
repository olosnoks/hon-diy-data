"""The laptop's own location, for when no handheld GPS is connected.

On Windows this is the Windows Location Service (Wi-Fi / IP, or a GPS if the
laptop has one). ``LaptopGps`` runs a backend on a thread, about once a second,
and hands each *new* fix to ``on_fix`` as the ``gps`` stream's columns plus
``gps_alt`` and ``gps_acc``. A fix is never made up: location off, denied or
not known yet means no fix and ``status`` says why.

Backends, first that works (``default_backend``):

- ``QtBackend``: PySide6.QtPositioning (the "winrt" plugin on Windows); needs a
  QCoreApplication, so the UI gets it and a bare CLI falls through.
- ``WinRtBackend``: Windows.Devices.Geolocation through ``winsdk`` / ``winrt``.
- ``PowerShellBackend``: System.Device.Location.GeoCoordinateWatcher.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..data.model import Column
from .track import NULL_ISLAND_DEG, gps_columns

SOURCE = "laptop"
OFF_TEXT = "Windows location is off: Settings > Privacy & security > Location"
DENIED_TEXT = ("Windows location is denied to desktop apps: Settings > Privacy & security > "
               "Location > Let desktop apps access your location")
WAITING_TEXT = "waiting for a location"


def laptop_columns() -> list[Column]:
    """The ``gps`` stream's columns when the laptop is the receiver: the usual
    ones (same order, the views read them by position), then altitude and accuracy."""
    return gps_columns() + [
        Column("gps_alt", "f4", "m", "Altitude", decimals=0, lo=-500.0, hi=5000.0),
        Column("gps_acc", "f4", "m", "Accuracy", decimals=0, lo=0.0, hi=5000.0),
    ]


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass(frozen=True)
class Fix:
    lat: float
    lon: float
    utc: float                      # when the fix was taken (unix seconds)
    speed_kmh: float | None = None
    heading: float | None = None
    alt_m: float | None = None
    acc_m: float | None = None      # horizontal accuracy

    @property
    def valid(self) -> bool:
        return (-90 <= self.lat <= 90 and -180 <= self.lon <= 180
                and not (abs(self.lat) < NULL_ISLAND_DEG and abs(self.lon) < NULL_ISLAND_DEG))

    def values(self) -> dict[str, float | None]:
        """The row for the ``gps`` stream (``laptop_columns``)."""
        return {"gps_lat": self.lat, "gps_lon": self.lon, "gps_speed": self.speed_kmh,
                "gps_heading": self.heading, "gps_sats": None, "gps_fix": 1.0, "gps_hdop": None,
                "gps_utc": self.utc, "gps_alt": self.alt_m, "gps_acc": self.acc_m}


class Backend(Protocol):
    name: str
    #: why there is no fix ("" when there is one or none is expected yet)
    reason: str

    def open(self) -> None: ...
    def read(self, timeout: float) -> Fix | None: ...
    def close(self) -> None: ...


# ---- Qt ---------------------------------------------------------------------

class QtBackend:
    """QGeoPositionInfoSource.createDefaultSource on the reading thread."""

    name = "Qt positioning"

    def __init__(self) -> None:
        self.reason = ""
        self._src: Any = None
        self._loop: Any = None
        self._last: Fix | None = None

    @staticmethod
    def usable() -> bool:
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtPositioning import QGeoPositionInfoSource
        except Exception:
            return False
        return QCoreApplication.instance() is not None and bool(QGeoPositionInfoSource.availableSources())

    def open(self) -> None:
        from PySide6.QtCore import QEventLoop
        from PySide6.QtPositioning import QGeoPositionInfoSource
        self._loop = QEventLoop()
        src = QGeoPositionInfoSource.createDefaultSource(None)
        if src is None:
            raise RuntimeError("no Qt position source")
        src.setUpdateInterval(1000)
        src.positionUpdated.connect(self._on_pos)
        src.errorOccurred.connect(self._on_error)
        self._src = src
        src.startUpdates()

    def _on_error(self, err: Any) -> None:
        from PySide6.QtPositioning import QGeoPositionInfoSource as S
        if err == S.Error.AccessError:
            self.reason = DENIED_TEXT
        elif err == S.Error.ClosedError:
            self.reason = OFF_TEXT
        elif err == S.Error.UpdateTimeoutError:
            self.reason = WAITING_TEXT
        else:
            self.reason = f"location error: {err}"

    def _on_pos(self, info: Any) -> None:
        from PySide6.QtPositioning import QGeoPositionInfo as I
        c = info.coordinate()
        if not info.isValid() or not c.isValid():
            return

        def attr(a: Any) -> float | None:
            return _num(info.attribute(a)) if info.hasAttribute(a) else None

        speed = attr(I.Attribute.GroundSpeed)
        self._last = Fix(c.latitude(), c.longitude(), info.timestamp().toMSecsSinceEpoch() / 1000.0,
                         speed * 3.6 if speed is not None else None, attr(I.Attribute.Direction),
                         _num(c.altitude()), attr(I.Attribute.HorizontalAccuracy))
        self.reason = ""

    def read(self, timeout: float) -> Fix | None:
        from PySide6.QtCore import QEventLoop
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._loop.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
            time.sleep(0.05)
        fix, self._last = self._last, None
        return fix

    def close(self) -> None:
        if self._src is not None:
            self._src.stopUpdates()
        self._src = None


# ---- winsdk / winrt -----------------------------------------------------------

class WinRtBackend:
    """Windows.Devices.Geolocation.Geolocator (``pip install winsdk``)."""

    name = "Windows.Devices.Geolocation"

    def __init__(self) -> None:
        self.reason = ""
        self._geo: Any = None
        self._mod: Any = None

    @staticmethod
    def module() -> Any:
        for name in ("winsdk.windows.devices.geolocation", "winrt.windows.devices.geolocation"):
            try:
                return __import__(name, fromlist=["Geolocator"])
            except Exception:
                continue
        return None

    def open(self) -> None:
        import asyncio
        mod = self._mod = self.module()
        if mod is None:
            raise RuntimeError("winsdk/winrt not installed")
        access = asyncio.run(_await(mod.Geolocator.request_access_async()))
        if access != mod.GeolocationAccessStatus.ALLOWED:
            self.reason = DENIED_TEXT if access == mod.GeolocationAccessStatus.DENIED else OFF_TEXT
        self._geo = mod.Geolocator()

    def read(self, timeout: float) -> Fix | None:
        import asyncio
        t0 = time.monotonic()
        mod = self._mod
        if self._geo.location_status in (mod.PositionStatus.DISABLED, mod.PositionStatus.NOT_AVAILABLE):
            self.reason = OFF_TEXT
            time.sleep(timeout)
            return None
        try:
            pos = asyncio.run(_await(self._geo.get_geoposition_async()))
        except Exception as exc:
            self.reason = OFF_TEXT if "denied" in str(exc).lower() else f"location error: {exc}"[:80]
            time.sleep(max(0.0, timeout - (time.monotonic() - t0)))
            return None
        c = pos.coordinate
        p = c.point.position
        speed = _num(c.speed)
        when = c.timestamp
        utc = when.timestamp() if hasattr(when, "timestamp") else time.time()
        self.reason = ""
        time.sleep(max(0.0, timeout - (time.monotonic() - t0)))
        return Fix(p.latitude, p.longitude, utc, speed * 3.6 if speed is not None else None,
                   _num(c.heading), _num(p.altitude), _num(c.accuracy))

    def close(self) -> None:
        self._geo = None


async def _await(op: Any) -> Any:
    return await op


# ---- PowerShell ---------------------------------------------------------------

_PS = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
$w.Start()
while ($true) {
  $p = $w.Position; $l = $p.Location
  $o = @{ status = "$($w.Status)"; permission = "$($w.Permission)"; unknown = $l.IsUnknown }
  if (-not $l.IsUnknown) {
    $o.lat = $l.Latitude; $o.lon = $l.Longitude; $o.speed = "$($l.Speed)"; $o.course = "$($l.Course)"
    $o.alt = "$($l.Altitude)"; $o.acc = "$($l.HorizontalAccuracy)"
    $o.utc = $p.Timestamp.ToUnixTimeMilliseconds() / 1000.0
  }
  [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress)); [Console]::Out.Flush()
  Start-Sleep -Milliseconds 1000
}
"""


class PowerShellBackend:
    """GeoCoordinateWatcher in a PowerShell child, one JSON line a second."""

    name = "PowerShell GeoCoordinateWatcher"

    def __init__(self) -> None:
        self.reason = ""
        self._proc: subprocess.Popen[str] | None = None

    def open(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                      creationflags=flags)

    def read(self, timeout: float) -> Fix | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        line = proc.stdout.readline()
        if not line:
            self.reason = "PowerShell location reader stopped"
            time.sleep(timeout)
            return None
        return self.parse(line)

    def parse(self, line: str) -> Fix | None:
        try:
            o = json.loads(line)
        except ValueError:
            return None
        if o.get("permission") == "Denied":
            self.reason = DENIED_TEXT
            return None
        if o.get("status") == "Disabled":
            self.reason = OFF_TEXT
            return None
        if o.get("unknown", True):
            self.reason = WAITING_TEXT
            return None
        speed = _num(o.get("speed"))
        self.reason = ""
        return Fix(float(o["lat"]), float(o["lon"]), float(o.get("utc") or time.time()),
                   speed * 3.6 if speed is not None else None, _num(o.get("course")),
                   _num(o.get("alt")), _num(o.get("acc")))

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.kill()


def default_backend() -> Backend | None:
    """The first backend this machine has; None off Windows (unless
    ``HONDALOG_LAPTOP_GPS=qt`` asks for Qt positioning anyway, e.g. geoclue)."""
    want = os.environ.get("HONDALOG_LAPTOP_GPS", "").lower()
    if want == "off":
        return None
    if sys.platform != "win32" and want != "qt":
        return None
    if QtBackend.usable():
        return QtBackend()
    if sys.platform == "win32":
        if WinRtBackend.module() is not None:
            return WinRtBackend()
        return PowerShellBackend()
    return None


# ---- the thread -----------------------------------------------------------------

class LaptopGps:
    """Reads ``backend`` on a thread and calls ``on_fix(fix)`` for every new fix."""

    def __init__(self, on_fix: Callable[[Fix], None] | None = None,
                 backend: Backend | None = None, interval: float = 1.0):
        self.on_fix = on_fix
        self.backend = backend
        self.interval = interval
        self.last: Fix | None = None
        self.last_mono = 0.0
        self.fixes = 0
        self._status = "laptop location: not available on this computer" if backend is None else "starting"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> str:
        """``laptop ±12 m``, or why there is no position."""
        return self._status

    def start(self) -> None:
        if self.backend is None or self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hondalog-laptop-gps", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)

    def _run(self) -> None:
        backend = self.backend
        assert backend is not None
        try:
            backend.open()
        except Exception as exc:
            self._status = f"laptop location unavailable ({backend.name}: {exc})"[:120]
            return
        self._status = backend.reason or WAITING_TEXT
        try:
            while not self._stop.is_set():
                try:
                    fix = backend.read(self.interval)
                except Exception as exc:
                    self._status = f"laptop location error: {exc}"[:120]
                    self._stop.wait(self.interval)
                    continue
                if fix is None or not fix.valid:
                    if backend.reason or self.last is None:
                        self._status = backend.reason or WAITING_TEXT
                    continue
                if self.last is not None and fix == self.last:
                    continue                    # the same fix again: Windows had nothing new
                self.last, self.last_mono = fix, time.monotonic()
                self.fixes += 1
                acc = f" ±{fix.acc_m:.0f} m" if fix.acc_m is not None else ""
                self._status = f"laptop{acc}"
                hook = self.on_fix
                if hook is not None:
                    try:
                        hook(fix)
                    except Exception as exc:
                        self._status = f"laptop location: {exc}"[:120]
        finally:
            try:
                backend.close()
            except Exception:
                pass


__all__ = ["DENIED_TEXT", "OFF_TEXT", "SOURCE", "Fix", "LaptopGps", "PowerShellBackend", "QtBackend",
           "WinRtBackend", "default_backend", "laptop_columns"]
