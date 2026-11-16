"""Every capture in the database with where it happened, and which of them
happened at the same place.

The question this answers: do the stalls happen at the same places? A
cluster on one stretch of road (a bumpy one, a railroad crossing) points at
something shaken loose; stalls spread all over town point elsewhere.

    pins = stall_pins(db_path, since=..., reason="rpm")     # from the stall_locations view
    groups = clusters(pins, radius_m=150)                   # the biggest group first
    doc = geojson(pins)                                     # hondalog-log stalls --geojson

No Qt in here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .. import __version__
from ..data import db
from ..data.db import DriveRef
from . import proj

#: what "the same place" means
CLUSTER_RADIUS_M = 150.0


@dataclass(frozen=True)
class StallPin:
    capture_id: int
    name: str                   # stall_20261116_071455
    kind: str                   # stall, capture, ...
    reason: str                 # rpm, link, external, alarm, manual, ...
    message: str
    drive: str
    wall_time: str              # ISO
    wall_unix: float | None
    t_trigger: float            # session seconds
    lat: float | None           # None: no fix within a minute of the trigger
    lon: float | None
    speed_kmh: float | None     # at the nearest fix
    max_speed_30s: float | None     # the fastest in the 30 s before
    heading: float | None
    gps_dt: float | None        # nearest fix minus trigger, seconds
    db: str = ""

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def ref(self) -> DriveRef:
        return DriveRef(Path(self.db), self.drive, self.name)

    @property
    def is_stall(self) -> bool:
        return self.kind == "stall"


def stall_pins(database: str | os.PathLike, *, since: float | None = None, until: float | None = None,
               reason: str | None = None, kinds: Sequence[str] | None = ("stall",)) -> list[StallPin]:
    """Captures from the ``stall_locations`` view, oldest first. ``since`` /
    ``until`` are unix times (on the capture's wall clock), ``reason`` one
    trigger reason ("rpm", "link", ...), ``kinds`` the capture kinds (None:
    every kind). A database that does not exist yet has none."""
    path = db.db_path(database)
    if not db.is_database(path):
        return []
    sql = ("SELECT capture_id, name, kind, reason, message, drive, wall_time, wall_unix, t_trigger, lat, lon, "
           "speed, max_speed_30s, heading, gps_dt FROM stall_locations WHERE 1")
    args: list[Any] = []
    if since is not None:
        sql += " AND wall_unix >= ?"
        args.append(float(since))
    if until is not None:
        sql += " AND wall_unix < ?"
        args.append(float(until))
    if reason:
        sql += " AND reason = ?"
        args.append(reason)
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    sql += " ORDER BY wall_unix, capture_id"
    try:
        conn = db.connect(path)
    except (OSError, ValueError):
        return []
    try:
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    def num(v):
        return None if v is None else float(v)
    return [StallPin(int(r[0]), str(r[1]), str(r[2]), str(r[3] or ""), str(r[4] or ""), str(r[5]),
                     str(r[6] or ""), num(r[7]), float(r[8]), num(r[9]), num(r[10]), num(r[11]), num(r[12]),
                     num(r[13]), num(r[14]), str(path)) for r in rows]


def reasons(database: str | os.PathLike) -> list[tuple[str, str, int]]:
    """(kind, reason, count) of every capture in the database, most first."""
    path = db.db_path(database)
    if not db.is_database(path):
        return []
    try:
        conn = db.connect(path)
    except (OSError, ValueError):
        return []
    try:
        return [(str(k), str(r or ""), int(n)) for k, r, n in conn.execute(
            "SELECT kind, reason, count(*) FROM captures GROUP BY kind, reason ORDER BY 3 DESC, 1, 2")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def clusters(pins: Sequence[StallPin], radius_m: float = CLUSTER_RADIUS_M) -> list[list[int]]:
    """Groups of pins (indices) that lie within ``radius_m`` of another pin
    of the group (single linkage), biggest first; lone pins are groups of one.
    Pins without a position are left out."""
    idx = [i for i, p in enumerate(pins) if p.located]
    if not idx:
        return []
    lat = np.array([pins[i].lat for i in idx])
    lon = np.array([pins[i].lon for i in idx])
    d = proj.haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    parent = list(range(len(idx)))

    def root(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            if d[a, b] <= radius_m:
                parent[root(a)] = root(b)
    groups: dict[int, list[int]] = {}
    for a in range(len(idx)):
        groups.setdefault(root(a), []).append(idx[a])
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def centre(pins: Sequence[StallPin], group: Sequence[int]) -> tuple[float, float]:
    lat = float(np.mean([pins[i].lat for i in group]))
    lon = float(np.mean([pins[i].lon for i in group]))
    return lat, lon


def spread_m(pins: Sequence[StallPin], group: Sequence[int]) -> float:
    """The farthest any pin of the group is from the group's centre."""
    lat, lon = centre(pins, group)
    return float(max(proj.haversine_m(lat, lon, pins[i].lat, pins[i].lon) for i in group))


def summary(pins: Sequence[StallPin], radius_m: float = CLUSTER_RADIUS_M,
            speed: Any = None, distance: Any = None) -> str:
    """One or two sentences: how many, how many have a place, the biggest group.
    ``speed(kmh)`` / ``distance(m)`` format a speed / distance (default km/h, m)."""
    speed = speed or (lambda kmh: f"{kmh:.0f} km/h")
    distance = distance or (lambda m: f"{m:.0f} m")
    if not pins:
        return "No captures match: nothing to compare yet."
    located = [p for p in pins if p.located]
    what = "stall" if all(p.is_stall for p in pins) else "capture"
    head = f"{len(pins)} {what}{'s' if len(pins) != 1 else ''}"
    if not located:
        return head + ", none with a GPS fix near its trigger."
    head += f", {len(located)} with a place" if len(located) < len(pins) else ""
    groups = clusters(pins, radius_m)
    big = groups[0]
    if len(big) < 2:
        return head + f". No two within {distance(radius_m)} of each other: no one place stands out."
    lat, lon = centre(pins, big)
    moving = [pins[i].max_speed_30s for i in big if pins[i].max_speed_30s is not None]
    fast = f", moving at up to {speed(float(np.median(moving)))} before" if moving else ""
    return (head + f". {len(big)} of them within {distance(max(1.0, 2 * spread_m(pins, big)))} of one another, "
            f"near {lat:.5f}, {lon:.5f}{fast}.")


def geojson(pins: Sequence[StallPin], radius_m: float = CLUSTER_RADIUS_M) -> dict[str, Any]:
    """A FeatureCollection: one Point per capture with a place (RFC 7946:
    [lon, lat]); each feature's properties hold the capture and its group."""
    groups = clusters(pins, radius_m)
    group_of = {i: g for g, members in enumerate(groups) for i in members}
    features = []
    for i, p in enumerate(pins):
        if not p.located:
            continue
        props = {k: v for k, v in asdict(p).items() if k not in ("lat", "lon", "db")}
        props["cluster"] = group_of.get(i)
        props["cluster_size"] = len(groups[group_of[i]]) if i in group_of else 1
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
                         "properties": props})
    return {"type": "FeatureCollection", "features": features,
            "generator": f"hondalog {__version__}", "unlocated": sum(1 for p in pins if not p.located)}


def write_geojson(pins: Sequence[StallPin], path: str | os.PathLike) -> Path:
    path = Path(path)
    path.write_text(json.dumps(geojson(pins), indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def parse_date(text: str, end: bool = False) -> float:
    """"2026-11-16" (or a full ISO time) as unix time; ``end`` makes a bare
    date mean the end of that day."""
    when = datetime.fromisoformat(text.strip())
    if end and len(text.strip()) <= 10:
        return when.timestamp() + 86400.0
    return when.timestamp()


__all__ = ["CLUSTER_RADIUS_M", "StallPin", "centre", "clusters", "geojson", "parse_date", "reasons",
           "spread_m", "stall_pins", "summary", "write_geojson"]
