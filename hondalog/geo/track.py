"""A drive's GPS track: positions in time order, and what the map asks of it.

The ``gps`` stream of a drive (the handheld's fixes: imported from its SD
card, or recorded live over the ``cyd:`` serial link) becomes a ``Track``:
only the rows with a fix, sorted by time, projected to Web Mercator unit
coordinates, with a break wherever no fix came for ``GAP_S``. The map screen
and the GPX / KML exports work on it:

    track = Track.from_log(open_log("drive_2026_11_16_0712"))
    track.lod(15)                    # indices to draw at zoom 15 (about one per pixel)
    track.at(812.4)                  # where the car was at session second 812.4
    track.nearest(x, y, max_d)       # the time on the track closest to a point
    track.sample(rpm.t, rpm.v)       # a channel's value at every fix (to colour the line)

No Qt in here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..data.model import Column
from . import proj

GPS_STREAM = "gps"
#: no fix for longer than this and the line is broken (tunnel, cold start, the
#: handheld unplugged): a straight line across the gap would be a guess
GAP_S = 5.0
#: a fix this close to (0, 0) is a receiver that has not got one yet
NULL_ISLAND_DEG = 0.01


def gps_columns() -> list[Column]:
    """The "gps" stream's columns: a drive file's fixes, and the live ones over serial.
    The ``gps_track`` SQL view reads them by position: keep the order."""
    return [
        Column("gps_lat", "f8", "°", "Latitude", decimals=6, lo=-90.0, hi=90.0),
        Column("gps_lon", "f8", "°", "Longitude", decimals=6, lo=-180.0, hi=180.0),
        Column("gps_speed", "f4", "km/h", "GPS speed", decimals=1, lo=0.0, hi=200.0),
        Column("gps_heading", "f4", "°", "Heading", decimals=0, lo=0.0, hi=360.0),
        Column("gps_sats", "u1", "", "Satellites", decimals=0, lo=0.0, hi=16.0),
        Column("gps_fix", "u1", "", "GPS fix", kind="flag", decimals=0),
        Column("gps_hdop", "f4", "", "HDOP", decimals=1, lo=0.0, hi=20.0),
        Column("gps_utc", "f8", "s", "GPS time (unix)", decimals=0),
    ]


GPS_CHANNELS = tuple(c.name for c in gps_columns())


@dataclass(frozen=True)
class Waypoint:
    """Something that happened at a moment of a drive, to pin on the map."""

    t: float                    # same time base as the track
    kind: str                   # "marker", "alarm", "stall", "capture"
    name: str                   # "MARK 2", "stall_20261116_071455"
    text: str = ""              # "rpm dropped to 0", the alarm's message
    ref: Any = None             # a DriveRef for a capture: what Replay opens


@dataclass(frozen=True)
class Position:
    t: float
    x: float
    y: float
    lat: float
    lon: float
    speed_kmh: float            # NaN when the receiver did not say
    heading: float              # degrees from north, NaN when not known
    index: float                # fractional index into the track


class Track:
    """GPS fixes in time order. Times are whatever the caller's time base is
    (session seconds from a log, view seconds from the Replay screen)."""

    def __init__(self, t, lat, lon, speed=None, heading=None, sats=None):
        t = np.asarray(t, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        n = min(len(t), len(lat), len(lon))
        t, lat, lon = t[:n], lat[:n], lon[:n]

        def column(values):
            if values is None:
                return np.full(n, np.nan)
            out = np.asarray(values, dtype=np.float64)[:n]
            return out if len(out) == n else np.full(n, np.nan)

        speed, heading, sats = column(speed), column(heading), column(sats)
        ok = (np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= proj.MAX_LAT)
              & (np.abs(lon) <= 180.0) & ~((np.abs(lat) < NULL_ISLAND_DEG) & (np.abs(lon) < NULL_ISLAND_DEG)))
        order = np.argsort(t[ok], kind="stable")
        self.t = t[ok][order]
        self.lat = lat[ok][order]
        self.lon = lon[ok][order]
        self.speed = speed[ok][order]
        self.heading = heading[ok][order]
        self.sats = sats[ok][order]
        self.x, self.y = proj.to_unit(self.lon, self.lat)
        m = len(self.t)
        #: breaks[i]: no line from fix i-1 to fix i
        self.breaks = np.zeros(m, dtype=bool)
        if m:
            self.breaks[0] = True
            self.breaks[1:] = np.diff(self.t) > GAP_S
        step = proj.haversine_m(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:]) if m > 1 else np.zeros(0)
        step = np.where(self.breaks[1:], 0.0, step)
        self.dist = np.concatenate(([0.0], np.cumsum(step))) if m else np.zeros(0)
        self._lod: dict[int, np.ndarray] = {}

    # ---- making one ----

    @classmethod
    def from_columns(cls, columns: dict[str, tuple[np.ndarray, np.ndarray]]) -> "Track":
        """``columns``: channel name -> (t, v), as many of GPS_CHANNELS as there are.
        Latitude's times are the track's; the rest are matched to them."""
        if "gps_lat" not in columns or "gps_lon" not in columns:
            return cls([], [], [])
        t, lat = columns["gps_lat"]
        t = np.asarray(t, dtype=np.float64)

        def matched(name):
            if name not in columns:
                return None
            tt, vv = columns[name]
            tt, vv = np.asarray(tt, dtype=np.float64), np.asarray(vv, dtype=np.float64)
            if len(tt) == len(t) and (len(t) == 0 or (tt[0] == t[0] and tt[-1] == t[-1])):
                return vv
            return hold(tt, vv, t, 0.5)

        return cls(t, lat, matched("gps_lon"), matched("gps_speed"), matched("gps_heading"), matched("gps_sats"))

    @classmethod
    def from_log(cls, log: Any, shift: float = 0.0) -> "Track":
        """The track of a ``hondalog.data.Log`` (session seconds, minus ``shift``)."""
        cols = {}
        for name in GPS_CHANNELS:
            if log.column(name) is not None:
                series = log.series(name)
                cols[name] = (series.t - shift, series.v)
        return cls.from_columns(cols)

    @classmethod
    def from_view(cls, view: Any) -> "Track":
        """The track of a Replay ``LogView`` (view seconds)."""
        return cls.from_columns({name: (view.t[name], view.data[name]) for name in GPS_CHANNELS
                                 if name in view.t and name in view.data})

    # ---- what it is ----

    def __len__(self) -> int:
        return len(self.t)

    @property
    def empty(self) -> bool:
        return len(self.t) == 0

    @property
    def duration(self) -> float:
        return float(self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    @property
    def length_m(self) -> float:
        return float(self.dist[-1]) if len(self.dist) else 0.0

    def bbox(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) in unit coordinates."""
        if self.empty:
            return (0.0, 0.0, 1.0, 1.0)
        return float(self.x.min()), float(self.y.min()), float(self.x.max()), float(self.y.max())

    # ---- level of detail ----

    def lod(self, zoom: int, px: float = 1.5) -> np.ndarray:
        """Indices of the fixes to draw at ``zoom``: a fix is kept when it is at
        least ``px`` screen pixels from the last fix kept (radial distance), so
        a parked car is one point, GPS jitter smaller than a pixel is gone, and
        an hour of driving is about as many points as the line is pixels long
        divided by ``px``. Both ends of every gap are kept, and the last fix."""
        key = int(zoom) * 64 + int(round(px * 8))
        hit = self._lod.get(key)
        if hit is not None:
            return hit
        n = len(self.t)
        if n <= 2:
            out = np.arange(n)
        else:
            scale = proj.world_px(zoom)
            # first a cheap pass: drop fixes in the same pixel cell as the one before
            gx = np.floor(self.x * scale / px).astype(np.int64)
            gy = np.floor(self.y * scale / px).astype(np.int64)
            must = self.breaks.copy()
            must[:-1] |= self.breaks[1:]            # the last fix before a gap
            must[-1] = True
            cand = np.empty(n, dtype=bool)
            cand[0] = True
            cand[1:] = (gx[1:] != gx[:-1]) | (gy[1:] != gy[:-1])
            idx = np.flatnonzero(cand | must)
            # then the radial pass on what is left
            xs = (self.x[idx] * scale).tolist()
            ys = (self.y[idx] * scale).tolist()
            forced = must[idx].tolist()
            tol2 = px * px
            keep = [0]
            lx, ly = xs[0], ys[0]
            for k in range(1, len(idx)):
                x, y = xs[k], ys[k]
                if forced[k] or (x - lx) * (x - lx) + (y - ly) * (y - ly) >= tol2:
                    keep.append(k)
                    lx, ly = x, y
            out = idx[np.array(keep, dtype=np.intp)]
        self._lod[key] = out
        return out

    # ---- time <-> place ----

    def at(self, t: float) -> Position | None:
        """Where the car was at ``t`` (linear between fixes). None before the
        first fix, after the last, and inside a gap."""
        n = len(self.t)
        if n == 0 or not np.isfinite(t) or t < self.t[0] or t > self.t[-1]:
            return None
        i = int(np.searchsorted(self.t, t, side="right")) - 1
        if i >= n - 1:
            i, f = n - 1, 0.0
        else:
            if self.breaks[i + 1]:
                if t - self.t[i] > GAP_S:
                    return None
                f = 0.0
            else:
                span = self.t[i + 1] - self.t[i]
                f = float((t - self.t[i]) / span) if span > 0 else 0.0
        j = min(i + 1, n - 1)

        def lerp(a):
            return float(a[i] + (a[j] - a[i]) * f) if f else float(a[i])
        heading = float(self.heading[i]) if np.isfinite(self.heading[i]) else float("nan")
        return Position(float(t), lerp(self.x), lerp(self.y), lerp(self.lat), lerp(self.lon),
                        lerp(self.speed), heading, i + f)

    def nearest(self, x: float, y: float, max_d: float = float("inf")) -> tuple[float, float] | None:
        """(time, distance) of the point on the track nearest to unit point
        (x, y), measured along the drawn line (a click between two fixes gives
        a time between theirs). None when nothing is within ``max_d``."""
        n = len(self.t)
        if n == 0:
            return None
        best_t, best_d = float(self.t[0]), float(np.hypot(self.x[0] - x, self.y[0] - y))
        dx0 = self.x - x
        dy0 = self.y - y
        d_pts = np.hypot(dx0, dy0)
        k = int(np.argmin(d_pts))
        best_t, best_d = float(self.t[k]), float(d_pts[k])
        if n > 1:
            ax, ay = self.x[:-1], self.y[:-1]
            vx, vy = self.x[1:] - ax, self.y[1:] - ay
            seg2 = vx * vx + vy * vy
            with np.errstate(invalid="ignore", divide="ignore"):
                f = np.clip(((x - ax) * vx + (y - ay) * vy) / seg2, 0.0, 1.0)
            f = np.where(seg2 > 0, f, 0.0)
            d = np.hypot(ax + f * vx - x, ay + f * vy - y)
            d = np.where(self.breaks[1:], np.inf, d)
            s = int(np.argmin(d))
            if d[s] < best_d:
                best_d = float(d[s])
                best_t = float(self.t[s] + f[s] * (self.t[s + 1] - self.t[s]))
        return (best_t, best_d) if best_d <= max_d else None

    def sample(self, t_src, v_src, max_age: float | None = None) -> np.ndarray:
        """A channel's value at every fix: the last value read at or before
        the fix, NaN when that is older than ``max_age`` (default: three of
        the channel's sample periods, at least a second)."""
        return hold(np.asarray(t_src, dtype=np.float64), np.asarray(v_src, dtype=np.float64), self.t, max_age)


def hold(t_src: np.ndarray, v_src: np.ndarray, grid: np.ndarray, max_age: float | None = None) -> np.ndarray:
    """Sample-and-hold of (t_src, v_src) at the times ``grid``."""
    out = np.full(len(grid), np.nan)
    if len(t_src) == 0 or len(grid) == 0:
        return out
    if max_age is None:
        dt = float(np.median(np.diff(t_src))) if len(t_src) > 1 else 1.0
        max_age = max(1.0, 3.0 * dt)
    i = np.searchsorted(t_src, grid, side="right") - 1
    ok = i >= 0
    idx = np.clip(i, 0, len(t_src) - 1)
    ok &= (grid - t_src[idx]) <= max_age
    out[ok] = v_src[idx[ok]]
    return out


# ---- pins for a drive -------------------------------------------------------

def log_waypoints(log: Any, shift: float = 0.0) -> list[Waypoint]:
    """Markers, raised alarms and captures (stalls, trigger captures) of a
    log, in time order, times minus ``shift``. Captures come from the
    database when the log is a drive in one; otherwise the trigger events
    stand in for them."""
    out: list[Waypoint] = []
    for e in log.events:
        if e.kind == "marker":
            out.append(Waypoint(e.t - shift, "marker", e.text or "MARK"))
        elif e.kind == "alarm" and str((e.data or {}).get("state", "raised")) == "raised":
            out.append(Waypoint(e.t - shift, "alarm", e.text or "ALARM"))
    captures = drive_captures(log)
    if captures:
        out += [Waypoint(c.t - shift, c.kind, c.name, c.text, c.ref) for c in captures]
    else:
        for e in log.events:
            if e.kind == "trigger":
                kind = "stall" if "STALL" in (e.text or "").upper() else "capture"
                out.append(Waypoint(e.t - shift, kind, e.text or kind.upper()))
    return sorted(out, key=lambda w: w.t)


def drive_captures(log: Any) -> list[Waypoint]:
    """The captures of the drive a database log belongs to (session times)."""
    ref = getattr(log, "ref", None)
    drive_id = getattr(log, "drive_id", None)
    if ref is None or drive_id is None:
        return []
    from ..data import db
    from ..data.db import DriveRef
    try:
        conn = db.connect(ref.db)
    except (OSError, ValueError):
        return []
    try:
        rows = conn.execute("SELECT name, kind, reason, message, t_trigger FROM captures WHERE drive_id=? "
                            "ORDER BY t_trigger", (drive_id,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    lo, hi = (float(log.t0), float(log.t1)) if ref.capture else (-np.inf, np.inf)   # a capture: its window
    out = []
    for name, kind, reason, message, t in rows:
        t = float(t)
        if not lo - 1e-6 <= t <= hi + 1e-6:
            continue
        out.append(Waypoint(t, "stall" if kind == "stall" else "capture", str(name),
                            str(message or reason or kind), DriveRef(ref.db, ref.drive, str(name))))
    return out


def view_waypoints(view: Any) -> list[Waypoint]:
    """Pins of a Replay ``LogView`` in view seconds: from its log when it has
    one (``view.replay``), else from its markers and trigger."""
    session = getattr(view, "replay", None)
    log = getattr(session, "log", None)
    if log is not None:
        return log_waypoints(log, float(getattr(view, "origin", 0.0)))
    out = [Waypoint(float(t), "marker", str(text)) for t, text in getattr(view, "markers", ())]
    if getattr(view, "trigger_t", None) is not None:
        out.append(Waypoint(float(view.trigger_t), "stall", str(getattr(view, "title", "STALL"))))
    return sorted(out, key=lambda w: w.t)


def channel_names(names: Sequence[str]) -> list[str]:
    """Channels worth colouring a track by: everything but the GPS's own bookkeeping."""
    skip = {"gps_lat", "gps_lon", "gps_utc", "gps_fix", "link"}
    return [n for n in names if n not in skip]


__all__ = ["GAP_S", "GPS_CHANNELS", "GPS_STREAM", "Position", "Track", "Waypoint", "channel_names",
           "drive_captures", "gps_columns", "hold", "log_waypoints", "view_waypoints"]
