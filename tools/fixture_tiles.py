"""Fixture map tiles: a stand-in tile server for screenshots, the UI benchmark
and tests, so none of them ever asks a real tile server for anything.

The tiles are drawn on demand from what ``hondalog.geo.route`` knows: the
Pearl, MS loop as main roads, a grid of side streets, a river to the west,
a park and the railroad across the rough stretch. They are not map data and
the map says so: the attribution reads "fixture tiles (tools/fixture_tiles.py)".

    layer = fixture_layer(cache_dir)          # a TileLayer drawing these
    resp = fixture_get(url, headers)          # one tile as a tile server would answer
"""

from __future__ import annotations

import math
import re
import threading

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from hondalog.geo import proj, route
from hondalog.geo import tiles as gt

URL = "http://fixture.invalid/{z}/{x}/{y}.png"          # .invalid never resolves (RFC 2606)
ATTRIBUTION = "fixture tiles (tools/fixture_tiles.py), not map data"
LAND, WATER, PARK = QColor("#EEEBE4"), QColor("#AAD3DF"), QColor("#CDEBB0")
_lock = threading.Lock()
requests: list[tuple[str, dict]] = []                   # what was asked, for tests


def _metres_line(lat0, lon0, pts):
    """[(east_m, north_m)] from (lat0, lon0) -> unit coordinates (n, 2)."""
    lat_lon = [proj.offset(lat0, lon0, e, n) for e, n in pts]
    x, y = proj.to_unit(np.array([p[1] for p in lat_lon]), np.array([p[0] for p in lat_lon]))
    return np.column_stack((x, y))


def _features():
    loop = route.PEARL_LOOP
    lat0, lon0 = loop.origin
    x, y = proj.to_unit(loop.lon, loop.lat)
    main = np.column_stack((x, y))
    streets = []
    for e in range(-1400, 4800, 230):
        streets.append(_metres_line(lat0, lon0, [(e, -2600), (e + 40, 3000)]))
    for n in range(-2600, 3000, 210):
        streets.append(_metres_line(lat0, lon0, [(-1400, n), (4800, n + 30)]))
    river = _metres_line(lat0, lon0, [(-1150 + 160 * math.sin(k / 3.0), -2800 + k * 230) for k in range(26)])
    park = _metres_line(lat0, lon0, [(1500, 300), (2300, 300), (2300, 900), (1500, 900)])
    a, b = loop.stretches["rough stretch"]
    rlat, rlon = loop.point("rough stretch", 0.55)
    rail = _metres_line(rlat, rlon, [(-4000, -900), (4000, 700)])
    return main, streets, river, park, rail


_FEATURES = None


def render(z: int, tx: int, ty: int) -> QImage:
    global _FEATURES
    if _FEATURES is None:
        _FEATURES = _features()
    main, streets, river, park, rail = _FEATURES
    img = QImage(proj.TILE, proj.TILE, QImage.Format_RGB32)
    img.fill(LAND)
    s = proj.world_px(z)
    m_px = proj.metres_per_px(32.27, z)

    def path(unit, closed=False):
        p = QPainterPath()
        pts = (unit * s) - np.array([tx * proj.TILE, ty * proj.TILE])
        p.moveTo(QPointF(*pts[0]))
        for q in pts[1:]:
            p.lineTo(QPointF(*q))
        if closed:
            p.closeSubpath()
        return p

    def width(metres, least=1.0):
        return max(least, metres / m_px)

    qp = QPainter(img)
    qp.setRenderHint(QPainter.Antialiasing, True)
    qp.setPen(Qt.NoPen)
    qp.setBrush(PARK)
    qp.drawPath(path(park, True))
    qp.setBrush(Qt.NoBrush)
    qp.setPen(QPen(WATER, width(70, 2), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    qp.drawPath(path(river))
    if z >= 13:
        for line in streets:
            qp.setPen(QPen(QColor("#D9D2C5"), width(11, 1.0), Qt.SolidLine, Qt.FlatCap))
            qp.drawPath(path(line))
        for line in streets:
            qp.setPen(QPen(QColor("#FFFFFF"), width(8, 0.8), Qt.SolidLine, Qt.FlatCap))
            qp.drawPath(path(line))
    rail_pen = QPen(QColor("#8C8C8C"), width(4, 1.5))
    rail_pen.setDashPattern([3, 3])
    qp.setPen(rail_pen)
    qp.drawPath(path(rail))
    qp.setPen(QPen(QColor("#C9A46A"), width(20, 2.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    qp.drawPath(path(main))
    qp.setPen(QPen(QColor("#F9D9A6"), width(15, 1.8), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    qp.drawPath(path(main))
    qp.end()
    return img


def png(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data.data())


def fixture_get(url: str, headers: dict) -> gt.Response:
    """A tile server's answer, drawn here. Records the request."""
    with _lock:
        requests.append((url, dict(headers)))
    m = re.search(r"/(\d+)/(\d+)/(\d+)\.png$", url)
    if not m:
        return gt.Response(404, {})
    z, x, y = (int(v) for v in m.groups())
    body = png(render(z, x, y))
    return gt.Response(200, {"Cache-Control": "max-age=604800", "ETag": f'"{z}-{x}-{y}"'}, body)


def fixture_source() -> gt.TileSource:
    return gt.TileSource(URL, ATTRIBUTION, 0, 19)


def fixture_layer(root, online: bool = True):
    from hondalog.ui.tilelayer import TileLayer
    return TileLayer(fixture_source(), online=online, root=root, get=fixture_get)


def prefill(root, box, zooms) -> int:
    """Draw the tiles covering ``box`` (unit x0, y0, x1, y1) at ``zooms`` into
    the disk cache of the fixture source. Returns how many."""
    cache = gt.TileCache(fixture_source(), root)
    n = 0
    for z in zooms:
        k = 1 << z
        x0, y0, x1, y1 = (int(v * k) for v in box)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                cache.put((z, tx, ty), gt.CachedTile(png(render(z, tx, ty)), 4e9))
                n += 1
    return n
