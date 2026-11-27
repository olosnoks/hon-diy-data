"""GPS exports: GPX and KML of a drive's track.

    export_geo(log, "drive.gpx")                  # track with time, waypoints for markers and stalls
    export_geo(log, "drive.kml", channel="rpm")   # the track coloured by a channel (default: speed)

GPX 1.1: one ``trk`` with a ``trkseg`` per stretch without a gap, each
``trkpt`` with its UTC time; speed and heading (and the chosen channel) in a
``hondalog:`` extension; ``wpt`` for every marker, alarm and capture.

KML 2.2: the track cut into pieces of one colour each (KML has no colour per
vertex), the same ramp and range as the Map screen, the range in the
document's description; pins for markers, alarms and captures.

Times need the drive's wall clock (``epoch_unix``); a log without one gets
no times in the GPX (the positions are still right).

No Qt in here.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .. import __version__, ramps
from ..geo.track import Track, Waypoint, log_waypoints

GEO_FORMATS = {".gpx": "GPX 1.1 (GPS track with times, waypoints for markers and stalls)",
               ".kml": "KML (GPS track coloured by a channel, for Google Earth)",
               ".geojson": "GeoJSON (the track as LineStrings, markers and stalls as Points)"}
GPX_NS = "http://www.topografix.com/GPX/1/1"
KML_NS = "http://www.opengis.net/kml/2.2"
HL_NS = "https://github.com/olosnoks/hon-diy-data/gpx/1"
#: how far in time a waypoint may be from a fix and still be placed on it
PLACE_WITHIN_S = 60.0
KML_BINS = 16
#: waypoint kind -> (KML icon colour aabbggrr, GPX sym)
PIN_STYLE = {"stall": ("ff2000b0", "Crossing"), "capture": ("ffa83f0a", "Flag, Blue"),
             "alarm": ("ff00d0ff", "Danger Area"), "marker": ("ff000000", "Flag, Red")}


def epoch_of(log: Any) -> float | None:
    """Wall time (unix) of session second 0 of a log, if it is known."""
    meta = getattr(log, "meta", {}) or {}
    for key in ("epoch_unix", "started_unix"):
        try:
            value = float(meta[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def speed_channel(log: Any) -> str:
    """What "speed" means for this log: the ECU's vehicle speed if it has
    readings, else the GPS's."""
    for name in ("vss", "gps_speed"):
        if log.column(name) is not None:
            series = log.series(name)
            if len(series) and np.isfinite(series.v).any() and (name == "gps_speed" or np.nanmax(series.v) > 0):
                return name
    return "gps_speed"


def place(track: Track, t: float) -> tuple[float, float] | None:
    """(lat, lon) of a moment: on the track, else the nearest fix in time."""
    pos = track.at(t)
    if pos is not None:
        return pos.lat, pos.lon
    if track.empty:
        return None
    i = int(np.argmin(np.abs(track.t - t)))
    if abs(track.t[i] - t) <= PLACE_WITHIN_S:
        return float(track.lat[i]), float(track.lon[i])
    return None


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _segments(track: Track, i0: int = 0, i1: int | None = None) -> list[np.ndarray]:
    """Index runs of the track without a gap."""
    i1 = len(track) if i1 is None else i1
    starts = [int(i) for i in np.flatnonzero(track.breaks[i0:i1]) + i0]
    if not starts or starts[0] != i0:
        starts = [i0] + starts
    bounds = starts + [i1]
    return [np.arange(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def _cut(track: Track, t0: float | None, t1: float | None) -> Track:
    if t0 is None and t1 is None:
        return track
    lo = -np.inf if t0 is None else t0
    hi = np.inf if t1 is None else t1
    keep = (track.t >= lo) & (track.t <= hi)
    return Track(track.t[keep], track.lat[keep], track.lon[keep], track.speed[keep], track.heading[keep],
                 track.sats[keep])


def write_gpx(path: str | os.PathLike, track: Track, waypoints: Sequence[Waypoint] = (), *, name: str = "",
              epoch: float | None = None, extra: tuple[str, str, np.ndarray] | None = None) -> Path:
    """``extra``: (channel, units, value at every fix) written into each trkpt's extension."""
    ET.register_namespace("", GPX_NS)
    ET.register_namespace("hondalog", HL_NS)
    q = lambda tag: f"{{{GPX_NS}}}{tag}"            # noqa: E731
    h = lambda tag: f"{{{HL_NS}}}{tag}"             # noqa: E731
    root = ET.Element(q("gpx"), {"version": "1.1", "creator": f"hondalog {__version__}"})
    meta = ET.SubElement(root, q("metadata"))
    ET.SubElement(meta, q("name")).text = name or "hondalog drive"
    ET.SubElement(meta, q("desc")).text = ("Recorded by hondalog (GPS from the handheld). Times are UTC."
                                           if epoch is not None else
                                           "Recorded by hondalog. The drive's wall clock is not known: no times.")
    if epoch is not None and len(track):
        ET.SubElement(meta, q("time")).text = _iso(epoch + float(track.t[0]))
    if len(track):
        ET.SubElement(meta, q("bounds"), {"minlat": f"{track.lat.min():.7f}", "minlon": f"{track.lon.min():.7f}",
                                          "maxlat": f"{track.lat.max():.7f}", "maxlon": f"{track.lon.max():.7f}"})
    for w in waypoints:
        where = place(track, w.t)
        if where is None:
            continue
        wpt = ET.SubElement(root, q("wpt"), {"lat": f"{where[0]:.7f}", "lon": f"{where[1]:.7f}"})
        if epoch is not None:
            ET.SubElement(wpt, q("time")).text = _iso(epoch + w.t)
        ET.SubElement(wpt, q("name")).text = w.name
        if w.text and w.text != w.name:
            ET.SubElement(wpt, q("desc")).text = w.text
        ET.SubElement(wpt, q("sym")).text = PIN_STYLE.get(w.kind, PIN_STYLE["marker"])[1]
        ET.SubElement(wpt, q("type")).text = w.kind
    trk = ET.SubElement(root, q("trk"))
    ET.SubElement(trk, q("name")).text = name or "hondalog drive"
    ET.SubElement(trk, q("type")).text = "driving"
    for seg in _segments(track):
        trkseg = ET.SubElement(trk, q("trkseg"))
        for i in seg:
            pt = ET.SubElement(trkseg, q("trkpt"), {"lat": f"{track.lat[i]:.7f}", "lon": f"{track.lon[i]:.7f}"})
            if epoch is not None:
                ET.SubElement(pt, q("time")).text = _iso(epoch + float(track.t[i]))
            items = []
            if np.isfinite(track.speed[i]):
                items.append(("speed_kmh", f"{track.speed[i]:.1f}"))
            if np.isfinite(track.heading[i]):
                items.append(("heading", f"{track.heading[i]:.0f}"))
            if extra is not None and np.isfinite(extra[2][i]):
                items.append((extra[0], f"{extra[2][i]:.4g}"))
            if items:
                ext = ET.SubElement(pt, q("extensions"))
                for key, text in items:
                    ET.SubElement(ext, h(key)).text = text
    path = Path(path)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return path


def _kml_colour(rgb) -> str:
    r, g, b = (int(round(float(c) * 255)) for c in rgb)
    return f"ff{b:02x}{g:02x}{r:02x}"


def write_kml(path: str | os.PathLike, track: Track, values: np.ndarray | None = None, *, name: str = "",
              channel: str = "", units: str = "", lo: float | None = None, hi: float | None = None,
              waypoints: Sequence[Waypoint] = (), epoch: float | None = None, width: float = 5.0) -> Path:
    """The track as coloured LineStrings: consecutive segments in the same
    one of ``KML_BINS`` colour bins share a Placemark."""
    ET.register_namespace("", KML_NS)
    q = lambda tag: f"{{{KML_NS}}}{tag}"            # noqa: E731
    root = ET.Element(q("kml"))
    doc = ET.SubElement(root, q("Document"))
    ET.SubElement(doc, q("name")).text = name or "hondalog drive"
    values = np.full(len(track), np.nan) if values is None else np.asarray(values, dtype=np.float64)
    lo, hi, diverging = ramps.value_range(channel or "", values, lo, hi)
    colours = ramps.bin_colours(KML_BINS, diverging)
    when = f" Started {datetime.fromtimestamp(epoch + float(track.t[0])):%Y-%m-%d %H:%M}." \
        if epoch is not None and len(track) else ""
    ET.SubElement(doc, q("description")).text = (
        f"Coloured by {channel or 'nothing'}{f' ({units})' if units else ''}: "
        f"{'blue' if diverging else 'dark blue'} {lo:.4g} to {'orange' if diverging else 'yellow'} {hi:.4g}"
        f"{' (0 in the middle)' if diverging else ''}; grey = no value.{when} Exported by hondalog {__version__}.")
    for k in range(KML_BINS):
        style = ET.SubElement(doc, q("Style"), {"id": f"bin{k}"})
        line = ET.SubElement(style, q("LineStyle"))
        ET.SubElement(line, q("color")).text = _kml_colour(colours[k])
        ET.SubElement(line, q("width")).text = f"{width:g}"
    style = ET.SubElement(doc, q("Style"), {"id": "nodata"})
    line = ET.SubElement(style, q("LineStyle"))
    ET.SubElement(line, q("color")).text = "ff8c8c8c"
    ET.SubElement(line, q("width")).text = f"{width:g}"
    for kind, (colour, _) in PIN_STYLE.items():
        style = ET.SubElement(doc, q("Style"), {"id": f"pin_{kind}"})
        icon = ET.SubElement(style, q("IconStyle"))
        ET.SubElement(icon, q("color")).text = colour
    folder = ET.SubElement(doc, q("Folder"))
    ET.SubElement(folder, q("name")).text = f"Track ({channel})" if channel else "Track"
    b = ramps.bins(values, lo, hi, KML_BINS)
    for seg in _segments(track):
        if len(seg) < 2:
            continue
        start = 0
        for j in range(1, len(seg) + 1):
            # a run ends where the next segment's colour differs; it keeps that point so the pieces join
            if j == len(seg) or b[seg[j]] != b[seg[start]] or j - start >= 500:
                run = seg[start:min(j + 1, len(seg))]
                if len(run) >= 2:
                    pm = ET.SubElement(folder, q("Placemark"))
                    k = int(b[seg[start]])
                    ET.SubElement(pm, q("styleUrl")).text = f"#bin{k}" if k >= 0 else "#nodata"
                    ls = ET.SubElement(pm, q("LineString"))
                    ET.SubElement(ls, q("tessellate")).text = "1"
                    ET.SubElement(ls, q("coordinates")).text = " ".join(
                        f"{track.lon[i]:.7f},{track.lat[i]:.7f}" for i in run)
                start = j
    if waypoints:
        pins = ET.SubElement(doc, q("Folder"))
        ET.SubElement(pins, q("name")).text = "Markers, alarms and captures"
        for w in waypoints:
            where = place(track, w.t)
            if where is None:
                continue
            pm = ET.SubElement(pins, q("Placemark"))
            ET.SubElement(pm, q("name")).text = w.name
            if w.text:
                ET.SubElement(pm, q("description")).text = w.text
            if epoch is not None:
                ts = ET.SubElement(pm, q("TimeStamp"))
                ET.SubElement(ts, q("when")).text = _iso(epoch + w.t)
            ET.SubElement(pm, q("styleUrl")).text = f"#pin_{w.kind if w.kind in PIN_STYLE else 'marker'}"
            point = ET.SubElement(pm, q("Point"))
            ET.SubElement(point, q("coordinates")).text = f"{where[1]:.7f},{where[0]:.7f}"
    path = Path(path)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return path


def write_geojson(path: str | os.PathLike, track: Track, waypoints: Sequence[Waypoint] = (), *, name: str = "",
                  epoch: float | None = None, channel: str = "", values: np.ndarray | None = None) -> Path:
    """The drive as GeoJSON (RFC 7946): one LineString per stretch without a
    gap (times, and the channel's value at every fix), one Point per marker.
    The same shape as the web app's ``writeGeoJson``."""
    def g4(v: float):
        return float(f"{v:.4g}")
    features = []
    for seg in _segments(track):
        coords = [[round(float(track.lon[i]), 7), round(float(track.lat[i]), 7)] for i in seg]
        times = [_iso(epoch + float(track.t[i])) if epoch is not None else round(float(track.t[i]), 3)
                 for i in seg]
        props: dict = {"kind": "track", "name": name or "hondalog drive", "times": times}
        if values is not None and channel:
            props[channel] = [g4(float(values[i])) if np.isfinite(values[i]) else None for i in seg]
        geometry = ({"type": "LineString", "coordinates": coords} if len(coords) > 1
                    else {"type": "Point", "coordinates": coords[0]})
        features.append({"type": "Feature", "geometry": geometry, "properties": props})
    for w in waypoints:
        where = place(track, w.t)
        if where is None:
            continue
        props = {"kind": w.kind, "name": w.name, "text": w.text, "t": round(float(w.t), 3)}
        if epoch is not None:
            props["time"] = _iso(epoch + w.t)
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": "Point", "coordinates": [round(where[1], 7), round(where[0], 7)]}})
    path = Path(path)
    path.write_text(json.dumps({"type": "FeatureCollection", "generator": f"hondalog {__version__}",
                                "features": features}, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def export_geo(log: Any, path: str | os.PathLike, fmt: str | None = None, *, channel: str | None = None,
               t0: float | None = None, t1: float | None = None) -> Path:
    """Write a log's GPS track as ``.gpx``, ``.kml`` or ``.geojson`` (from ``fmt`` or the
    extension). ``channel`` colours the KML (default: speed) and is added to
    every GPX point. Raises ValueError when the log has no GPS fixes."""
    path = Path(path)
    ext = "." + fmt.lstrip(".").lower() if fmt else path.suffix.lower()
    if ext not in GEO_FORMATS:
        raise ValueError(f"unknown GPS export format {ext!r}; choose from {', '.join(GEO_FORMATS)}")
    track = _cut(Track.from_log(log), t0, t1)
    if track.empty:
        raise ValueError(f"{log.name or 'this log'}: no GPS fixes to export (the GPS comes from the handheld)")
    epoch = epoch_of(log)
    lo_t, hi_t = float(track.t[0]), float(track.t[-1])
    pins = [w for w in log_waypoints(log) if lo_t - PLACE_WITHIN_S <= w.t <= hi_t + PLACE_WITHIN_S]
    name = log.name or "hondalog drive"
    if channel is None:
        channel = speed_channel(log)
    elif log.column(channel) is None:
        raise ValueError(f"no channel {channel!r} in {name}")
    series = log.series(channel)
    values = track.sample(series.t, series.v)
    if ext == ".gpx":
        extra = (channel, log.units(channel), values) if channel not in ("gps_speed",) else None
        return write_gpx(path, track, pins, name=name, epoch=epoch, extra=extra)
    if ext == ".geojson":
        return write_geojson(path, track, pins, name=name, epoch=epoch, channel=channel, values=values)
    return write_kml(path, track, values, name=name, channel=channel, units=log.units(channel), waypoints=pins,
                     epoch=epoch)


__all__ = ["GEO_FORMATS", "epoch_of", "export_geo", "place", "speed_channel", "write_geojson", "write_gpx",
           "write_kml"]
