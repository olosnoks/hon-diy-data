"""The map canvas of the Map screen (F8): tiles, a coloured track, pins, the
Replay cursor, the live position, a scale bar and a north arrow.

One QWidget, one paintEvent, like the tiles. Everything the paint needs is
prepared when the view changes (pan, zoom, resize, new data), never inside
the paint:

- The track is kept in a reference frame (Web Mercator pixels at zoom 20,
  relative to the track) and drawn through one QTransform with cosmetic
  pens, so panning changes a transform and nothing else.
- Per whole zoom level the track is decimated to a point every 1.5 pixels
  (``Track.lod``) and cut into short runs of one colour bin, each a prebuilt
  QPolygonF with its bounding box (Qt strokes many short antialiased
  polylines far faster than one long one that crosses itself).
- The runs are drawn once into an image that covers the whole track at that
  zoom (or three screens around the view when the track is bigger); panning
  draws that image at another place. After a zoom, or when the view nears
  the image's edge, the next image is drawn a few milliseconds per frame
  (``refresh``) while the old one is shown, scaled: no frame waits for a
  whole redraw. A colour or theme change redraws it at once.
- Pens are made once per bin and theme.

The map is offline first: a tile that is not on disk (and cannot be
fetched) borrows a scaled-up lower-zoom tile, else a plain grid is drawn.
The track, the pins, the scale bar and the north arrow need no tiles.
"""

from __future__ import annotations

import collections
import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyqtgraph.functions as fn
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from .. import ramps
from ..geo import proj
from ..geo.track import Track
from . import paint
from .theme import T

REF_Z = 20
REF_PX = proj.world_px(REF_Z)
N_BINS = 24
RUN_MAX = 64                    # points per drawn run at most (keeps stroking fast, culling useful)
LINE_W, CASING_W = 4.0, 7.0
HIT_PX = 14.0
RENDER_SLICE_S = 0.006          # track drawing per frame while a new image is made
GRID_STEP = 64                  # the plain grid where no tile is


@dataclass
class Pin:
    x: float                    # unit coordinates
    y: float
    kind: str                   # "stall", "capture", "alarm", "marker", "start"
    label: str = ""             # "2" for MARK 2: drawn inside a marker pin
    payload: Any = None         # what pinClicked hands back
    selected: bool = False
    sx: float = 0.0             # screen position, set by the layout
    sy: float = 0.0


@dataclass
class _Geo:
    """The track at one zoom level, in the reference frame."""
    runs: list = field(default_factory=list)            # (pen bin, QPolygonF)
    run_box: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))
    casing: list = field(default_factory=list)          # QPolygonF
    casing_box: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))
    points: int = 0


def _poly(xy: np.ndarray) -> QPolygonF:
    poly = fn.create_qpolygonf(len(xy))
    fn.ndarray_from_qpolygonf(poly)[:] = xy
    return poly


def _box(xy: np.ndarray) -> tuple[float, float, float, float]:
    return float(xy[:, 0].min()), float(xy[:, 1].min()), float(xy[:, 0].max()), float(xy[:, 1].max())


# ---- pins: the shape vocabulary (shape first, colour second) ----------------

PIN_SIZE = {"stall": 24, "capture": 20, "alarm": 20, "marker": 18, "start": 16}


def draw_pin(p: QPainter, kind: str, cx: float, cy: float, label: str = "", selected: bool = False,
             size: float | None = None) -> None:
    """Stall: octagon in the alarm fill. Capture: diamond in accent. Alarm:
    triangle in the warn fill. Marker: inverted square with its number.
    Start of the track: a ring. All with a 2 px fg outline."""
    s = size or PIN_SIZE.get(kind, 18)
    x, y = cx - s / 2, cy - s / 2
    p.setRenderHint(QPainter.Antialiasing, True)
    if selected:
        p.setPen(T.cpen(T.accent, 4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), s / 2 + 7, s / 2 + 7)
    if kind == "stall":
        paint.shape(p, paint.OCTAGON, x, y, s, T.alarm)
        paint.stroke(p, paint.OCTAGON, x, y, s, T.fg, 2)
        paint.stroke(p, paint.CROSS, x + s * 0.25, y + s * 0.25, s * 0.5, T.on_alarm, 2.5)
    elif kind == "capture":
        paint.shape(p, paint.DIAMOND, x, y, s, T.accent)
        paint.stroke(p, paint.DIAMOND, x, y, s, T.fg, 2)
    elif kind == "alarm":
        paint.shape(p, paint.TRIANGLE, x, y, s, T.warn)
        paint.stroke(p, paint.TRIANGLE, x, y, s, T.fg, 2)
    elif kind == "start":
        p.setPen(T.cpen(T.bg, 7))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), s / 2 - 2, s / 2 - 2)
        p.setPen(T.cpen(T.fg, 3.5))
        p.drawEllipse(QPointF(cx, cy), s / 2 - 2, s / 2 - 2)
    else:                                   # marker: inverted tag, like Replay's MARK
        p.setRenderHint(QPainter.Antialiasing, False)
        r = QRectF(round(x), round(y), s, s)
        p.fillRect(r.adjusted(-2, -2, 2, 2), T.bg)
        p.fillRect(r, T.fg)
        if label:
            p.setFont(T.font(12, 800))
            p.setPen(T.bg)
            p.drawText(r, Qt.AlignCenter, label[:2])
    p.setRenderHint(QPainter.Antialiasing, False)


def draw_dot(p: QPainter, cx: float, cy: float, r: float = 8.0, heading: float | None = None) -> None:
    """The cursor / live position: the live-point magenta with a fg ring and a
    bg halo (4.5:1 on either ground; the ring carries the contrast). With a
    heading, a short fg tick shows which way the car points."""
    p.setRenderHint(QPainter.Antialiasing, True)
    if heading is not None and heading == heading:
        a = math.radians(heading)
        tip = QPointF(cx + math.sin(a) * (r + 10), cy - math.cos(a) * (r + 10))
        p.setPen(T.cpen(T.bg, 7, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), tip)
        p.setPen(T.cpen(T.fg, 3, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), tip)
    p.setPen(T.cpen(T.bg, 2))
    p.setBrush(T.live)
    p.drawEllipse(QPointF(cx, cy), r + 3, r + 3)
    p.setPen(T.cpen(T.fg, 2.5))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setRenderHint(QPainter.Antialiasing, False)


class MapCanvas(QWidget):
    """``clicked(x, y)``: a click (not a drag) in unit coordinates, when no pin
    was hit. ``pinClicked(payload)``. ``moved()``: the user panned or zoomed."""

    clicked = Signal(float, float)
    pinClicked = Signal(object)
    moved = Signal()

    def __init__(self, tiles=None, parent=None):
        super().__init__(parent)
        self.tiles = tiles
        self.setMouseTracking(False)
        self.setCursor(Qt.OpenHandCursor)
        self.zoom = 14
        self.cx, self.cy = proj.to_unit(-90.1320, 32.2730)      # Pearl, MS until there is something to show
        self.cx, self.cy = float(self.cx), float(self.cy)
        self.max_zoom = proj.MAX_ZOOM
        self.title, self.caption = "", ""
        self.miles = True
        # the track
        self.track: Track | None = None
        self._origin = (0.0, 0.0)            # unit coordinates of the reference frame's zero
        self._bins = np.zeros(0, dtype=np.int32)
        self._diverging = False
        self._geo: dict[int, _Geo] = {}
        self._pens: list[QPen] = []
        self._nodata_pen: QPen | None = None
        self._casing_pen: QPen | None = None
        self._pen_theme = ""
        # other content
        self.pins: list[Pin] = []
        self.cursor: tuple[float, float] | None = None
        self.cursor_heading: float | None = None
        self.live: tuple[float, float] | None = None
        self.live_heading: float | None = None
        self._trail: QPolygonF | None = None
        self._trail_pen: QPen | None = None
        # prepared by _layout
        self._xf = QTransform()
        self._runs_on: list = []
        self._casing_on: list = []
        self._timg: QImage | None = None     # the track drawn, at the zoom _timg_zoom
        self._timg_rect = QRectF()           # where, in world pixels at that zoom
        self._timg_key: tuple = ()
        self._timg_zoom = 0
        self._timg_whole = False
        self._timg_target = QRectF()         # where it goes on screen now (scaled after a zoom)
        self._job: dict | None = None        # the next image, being drawn a bit per frame
        self._values_rev = 0
        self.renders = 0
        self.render_ms = 0.0
        self._pins_on: list[Pin] = []
        self._stacks: list = []              # (x, y, "3"): pins that lie on top of each other
        self._tiles_on: list = []            # (target QRectF, QPixmap, source QRectF | None)
        self._grid_on: list = []             # QRectF of tiles with nothing to show
        self._grid_path = QPainterPath()     # the plain grid's lines, inside the empty tiles
        self._texts: dict[str, str] = {}     # elided title, caption, attribution
        self._north = (QPolygonF(), QPolygonF())
        self._scale = (0.0, "")
        self._cursor_xy: tuple[float, float] | None = None
        self._live_xy: tuple[float, float] | None = None
        self._boxes: dict[str, QRectF] = {}
        self._status = ""
        # interaction
        self._press: QPointF | None = None
        self._last: QPointF | None = None
        self._dragged = False
        # numbers for the benchmark
        self.paint_ms = 0.0
        self.paints = 0
        self.paint_times: collections.deque = collections.deque(maxlen=4000)

    # ---- content ----

    def set_tiles(self, tiles) -> None:
        self.tiles = tiles
        self._layout()

    def set_text(self, title: str, caption: str) -> None:
        if (title, caption) != (self.title, self.caption):
            self.title, self.caption = title, caption
            self._layout_boxes()
            self.update()

    def set_track(self, track: Track | None, values: np.ndarray | None = None, lo: float = 0.0,
                  hi: float = 1.0, diverging: bool = False) -> None:
        self.track = track if track is not None and not track.empty else None
        if self.track is not None:
            x0, y0, x1, y1 = self.track.bbox()
            self._origin = ((x0 + x1) / 2, (y0 + y1) / 2)
        self.set_values(values, lo, hi, diverging)

    def set_values(self, values: np.ndarray | None, lo: float = 0.0, hi: float = 1.0,
                   diverging: bool = False) -> None:
        """Colour the track: one bin per value (NaN: no value, drawn grey)."""
        n = len(self.track) if self.track is not None else 0
        if values is None or len(values) != n:
            self._bins = np.full(n, -1, dtype=np.int32)
        else:
            self._bins = ramps.bins(values, lo, hi, N_BINS)
        if diverging != self._diverging:
            self._pen_theme = ""
        self._diverging = diverging
        self._geo.clear()
        self._values_rev += 1
        self._layout()

    def set_pins(self, pins: list[Pin]) -> None:
        self.pins = list(pins)
        self._layout()

    def set_cursor(self, xy: tuple[float, float] | None, heading: float | None = None) -> None:
        if xy == self.cursor and heading == self.cursor_heading:
            return
        old = self._dot_rect(self._cursor_xy)
        self.cursor, self.cursor_heading = xy, heading
        self._cursor_xy = self.screen_of(*xy) if xy is not None else None
        self._update_rects(old, self._dot_rect(self._cursor_xy))

    def set_live(self, xy: tuple[float, float] | None, heading: float | None = None,
                 trail: np.ndarray | None = None) -> None:
        """The car now (unit x, y) and the way it came ((n, 2) unit coordinates)."""
        self.live, self.live_heading = xy, heading
        if trail is not None and len(trail) > 1:
            if self.track is None:
                self._origin = (float(trail[-1, 0]), float(trail[-1, 1]))
            ref = (trail - np.array(self._origin)) * REF_PX
            self._trail = _poly(ref)
        else:
            self._trail = None
        self._layout()

    # ---- view ----

    def screen_of(self, x: float, y: float) -> tuple[float, float]:
        s = proj.world_px(self.zoom)
        return (x - self.cx) * s + self.width() / 2.0, (y - self.cy) * s + self.height() / 2.0

    def unit_at(self, sx: float, sy: float) -> tuple[float, float]:
        s = proj.world_px(self.zoom)
        return self.cx + (sx - self.width() / 2.0) / s, self.cy + (sy - self.height() / 2.0) / s

    def px_to_unit(self, px: float) -> float:
        return px / proj.world_px(self.zoom)

    def visible_unit(self) -> tuple[float, float, float, float]:
        x0, y0 = self.unit_at(0, 0)
        x1, y1 = self.unit_at(self.width(), self.height())
        return x0, y0, x1, y1

    def center_on(self, x: float, y: float, zoom: int | None = None) -> None:
        if zoom is not None:
            self.zoom = int(max(proj.MIN_ZOOM, min(self.max_zoom, zoom)))
        self.cx, self.cy = float(np.clip(x, 0, 1)), float(np.clip(y, 0, 1))
        self._layout()

    def fit(self, box: tuple[float, float, float, float], max_zoom: int = 17) -> None:
        x0, y0, x1, y1 = box
        z = proj.fit_zoom(x0, y0, x1, y1, max(64, self.width()), max(64, self.height()), 48.0,
                          hi=min(max_zoom, self.max_zoom))
        self.center_on((x0 + x1) / 2, (y0 + y1) / 2, z)

    def zoom_by(self, steps: int, around: tuple[float, float] | None = None) -> None:
        """Whole zoom steps, keeping the point under ``around`` (screen px) in place."""
        z = int(max(proj.MIN_ZOOM, min(self.max_zoom, self.zoom + steps)))
        if z == self.zoom:
            return
        if around is not None:
            ux, uy = self.unit_at(*around)
            f = 2.0 ** (self.zoom - z)
            self.cx = ux + (self.cx - ux) * f
            self.cy = uy + (self.cy - uy) * f
        self.zoom = z
        self._layout()

    def pan_px(self, dx: float, dy: float) -> None:
        s = proj.world_px(self.zoom)
        self.cx = float(np.clip(self.cx - dx / s, 0, 1))
        self.cy = float(np.clip(self.cy - dy / s, 0, 1))
        self._layout()

    def on_screen(self, x: float, y: float, margin: float = 24.0) -> bool:
        sx, sy = self.screen_of(x, y)
        return margin <= sx <= self.width() - margin and margin <= sy <= self.height() - margin

    # ---- tiles arriving, theme ----

    def refresh(self) -> None:
        """Once per frame: a slice of the track image being drawn, tiles that
        arrived, the status text."""
        if self._job is not None:
            self._step_job(RENDER_SLICE_S)
        tiles = self.tiles
        if tiles is None:
            return
        changed = tiles.poll()
        status = tiles.status()
        if changed:
            self._layout_tiles()
            self.update()
        elif status != self._status:
            old = self._boxes.get("attr", QRectF())
            self._status = status
            self._layout_boxes()
            self.update(old.united(self._boxes["attr"]).toAlignedRect())
        self._status = status

    def retheme(self) -> None:
        self._pen_theme = ""
        self._layout()

    # ---- preparing the paint ----

    def _ensure_pens(self) -> None:
        key = T.name + ("d" if self._diverging else "s")
        if key == self._pen_theme:
            return
        self._pen_theme = key
        colours = ramps.bin_colours(N_BINS, self._diverging)

        def cosmetic(colour: QColor, width: float, cap=Qt.FlatCap, join=Qt.BevelJoin) -> QPen:
            pen = QPen(colour, width, Qt.SolidLine, cap, join)
            pen.setCosmetic(True)
            return pen
        self._pens = [cosmetic(QColor.fromRgbF(*map(float, c)), LINE_W) for c in colours]
        self._nodata_pen = cosmetic(T.nodata, LINE_W)
        self._casing_pen = cosmetic(T.fg, CASING_W)
        self._trail_pen = cosmetic(T.fg, 3.0, Qt.RoundCap, Qt.RoundJoin)

    def _geometry(self, zoom: int) -> _Geo:
        geo = self._geo.get(zoom)
        if geo is not None:
            return geo
        geo = _Geo()
        track = self.track
        if track is None:
            self._geo[zoom] = geo
            return geo
        idx = track.lod(zoom)
        ox, oy = self._origin
        xy = np.column_stack(((track.x[idx] - ox) * REF_PX, (track.y[idx] - oy) * REF_PX))
        bins = self._bins[idx] if len(self._bins) == len(track) else np.full(len(idx), -1)
        brk = track.breaks[idx]
        geo.points = len(idx)
        # stretches without a gap
        starts = list(np.flatnonzero(brk))
        if not starts or starts[0] != 0:
            starts = [0] + starts
        bounds = starts + [len(idx)]
        boxes, cboxes = [], []
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a < 2:
                continue
            for c0 in range(a, b - 1, RUN_MAX):             # casing in chunks (for culling)
                c1 = min(b, c0 + RUN_MAX + 1)
                part = xy[c0:c1]
                geo.casing.append(_poly(part))
                cboxes.append(_box(part))
            seg = bins[a:b - 1]                             # colour of the segment leaving each point
            cut = np.flatnonzero(np.diff(seg)) + 1
            edges = [0] + list(cut) + [len(seg)]
            for e0, e1 in zip(edges[:-1], edges[1:]):
                for r0 in range(e0, e1, RUN_MAX):
                    r1 = min(e1, r0 + RUN_MAX)
                    part = xy[a + r0:a + r1 + 1]            # one past: the runs join
                    geo.runs.append((int(seg[r0]), _poly(part)))
                    boxes.append(_box(part))
        geo.run_box = np.array(boxes) if boxes else np.zeros((0, 4))
        geo.casing_box = np.array(cboxes) if cboxes else np.zeros((0, 4))
        self._geo[zoom] = geo
        return geo

    def _layout(self) -> None:
        """Everything the paint draws, for the current view."""
        w, h = self.width(), self.height()
        self._ensure_pens()
        k = 2.0 ** (self.zoom - REF_Z)
        ox, oy = self._origin
        rcx, rcy = (self.cx - ox) * REF_PX, (self.cy - oy) * REF_PX
        self._xf = QTransform(k, 0.0, 0.0, k, w / 2.0 - rcx * k, h / 2.0 - rcy * k)
        self._layout_track()
        self._pins_on = []
        for pin in self.pins:
            sx, sy = self.screen_of(pin.x, pin.y)
            if -30 <= sx <= w + 30 and -30 <= sy <= h + 30:
                pin.sx, pin.sy = sx, sy
                self._pins_on.append(pin)
        self._pins_on.sort(key=lambda p: (p.selected, ("start", "marker", "alarm", "capture", "stall").index(
            p.kind) if p.kind in ("start", "marker", "alarm", "capture", "stall") else 0))
        # pins drawn on top of each other get a count, so three stalls in one place read as three
        stacks, used = [], set()
        stackable = [q for q in self._pins_on if q.kind in ("stall", "capture")]
        for i, a in enumerate(stackable):
            if i in used:
                continue
            group = [j for j in range(i, len(stackable)) if j not in used
                     and math.hypot(stackable[j].sx - a.sx, stackable[j].sy - a.sy) < PIN_SIZE["stall"]]
            used.update(group)
            if len(group) > 1:
                top = min((stackable[j] for j in group), key=lambda q: q.sy)
                stacks.append((top.sx + 12, top.sy - 22, str(len(group))))
        self._stacks = stacks
        self._cursor_xy = self.screen_of(*self.cursor) if self.cursor is not None else None
        self._live_xy = self.screen_of(*self.live) if self.live is not None else None
        lat = float(proj.from_unit(self.cx, self.cy)[1])
        self._scale = proj.scale_bar(proj.metres_per_px(lat, self.zoom), 150.0, self.miles)
        self._layout_tiles()
        self._layout_boxes()
        self.update()

    def _layout_track(self, sync: bool = False) -> None:
        """Make sure an image of the track covers the view: keep the one there
        is (moved, or scaled after a zoom) and start drawing the next one when
        it is not the right one. ``sync``: draw it now (colour, theme)."""
        if self.track is None:
            self._timg, self._timg_key, self._job = None, (), None
            self._runs_on, self._casing_on = [], []
            return
        w, h = self.width(), self.height()
        s = proj.world_px(self.zoom)
        left, top = self.cx * s - w / 2.0, self.cy * s - h / 2.0
        need = QRectF(left, top, w, h)
        soon = need.adjusted(-w / 2.0, -h / 2.0, w / 2.0, h / 2.0)
        key = (self.zoom, self._pen_theme, self._values_rev, id(self.track))
        good = key == self._timg_key and (self._timg_whole or self._timg_rect.contains(soon))
        if not good:
            job = self._job
            if job is None or job["key"] != key or not (job["whole"] or job["rect"].contains(need)):
                self._start_job(key, need)
            if sync or self._timg is None or (key[1:] != self._timg_key[1:]):
                self._step_job(None)            # first image, new colours or theme: draw it all now
        self._place_image()

    def _place_image(self) -> None:
        """Where the shown track image goes on screen (scaled if its zoom is not the view's)."""
        if self._timg is None:
            return
        w, h = self.width(), self.height()
        s = proj.world_px(self.zoom)
        left, top = self.cx * s - w / 2.0, self.cy * s - h / 2.0
        f = 2.0 ** (self.zoom - self._timg_zoom)
        r = self._timg_rect
        self._timg_target = QRectF(r.x() * f - left, r.y() * f - top, r.width() * f, r.height() * f)

    def _start_job(self, key: tuple, need: QRectF) -> None:
        """Set up the next track image: its region and what to draw in it."""
        if self._job is not None and self._job.get("painter") is not None:
            self._job["painter"].end()
        w, h = max(1, self.width()), max(1, self.height())
        z = self.zoom
        s = proj.world_px(z)
        x0, y0, x1, y1 = self.track.bbox()
        pad = CASING_W + 2
        whole_rect = QRectF(x0 * s - pad, y0 * s - pad, (x1 - x0) * s + 2 * pad, (y1 - y0) * s + 2 * pad)
        whole = whole_rect.width() <= 3 * w and whole_rect.height() <= 3 * h
        region = whole_rect if whole else need.adjusted(-w, -h, w, h).intersected(whole_rect)
        job = {"key": key, "zoom": z, "whole": whole, "rect": QRectF(need), "items": [], "i": 0,
               "image": None, "painter": None}
        self._job = job
        if region.isEmpty():
            return
        region = QRectF(math.floor(region.x()), math.floor(region.y()), math.ceil(region.width()) + 1,
                        math.ceil(region.height()) + 1)
        job["rect"] = region
        geo = self._geometry(z)
        ox, oy = self._origin
        rx0, ry0 = (region.x() / s - ox) * REF_PX, (region.y() / s - oy) * REF_PX
        rx1, ry1 = (region.right() / s - ox) * REF_PX, (region.bottom() / s - oy) * REF_PX

        def inside(boxes: np.ndarray) -> np.ndarray:
            if not len(boxes):
                return np.zeros(0, dtype=np.intp)
            return np.flatnonzero((boxes[:, 2] >= rx0) & (boxes[:, 0] <= rx1)
                                  & (boxes[:, 3] >= ry0) & (boxes[:, 1] <= ry1))
        pens, nodata, casing = self._pens, self._nodata_pen, self._casing_pen
        job["items"] = ([(casing, geo.casing[i]) for i in inside(geo.casing_box)]
                        + [(pens[b] if b >= 0 else nodata, poly)
                           for b, poly in (geo.runs[i] for i in inside(geo.run_box))])
        job["runs"] = len(job["items"])
        k = 2.0 ** (z - REF_Z)
        job["transform"] = QTransform(k, 0.0, 0.0, k, ox * s - region.x(), oy * s - region.y())

    def _step_job(self, budget_s: float | None) -> None:
        """Draw the next image for up to ``budget_s`` (None: to the end); show it when done."""
        job = self._job
        if job is None:
            return
        t0 = time.perf_counter()
        items = job["items"]
        if job["image"] is None and items:
            rect = job["rect"]
            img = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32_Premultiplied)
            img.fill(0)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setTransform(job["transform"])
            job["image"], job["painter"] = img, painter
        painter = job["painter"]
        i, n = job["i"], len(items)
        while i < n:
            pen, poly = items[i]
            painter.setPen(pen)
            painter.drawPolyline(poly)
            i += 1
            if budget_s is not None and (i & 15) == 0 and time.perf_counter() - t0 > budget_s:
                break
        job["i"] = i
        job["ms"] = job.get("ms", 0.0) + (time.perf_counter() - t0) * 1000.0
        if i < n:
            return
        if painter is not None:
            painter.end()
        self._job = None
        self._timg = job["image"]
        self._timg_rect, self._timg_key, self._timg_zoom = job["rect"], job["key"], job["zoom"]
        self._timg_whole = job["whole"]
        self._runs_on = items
        self.renders += 1
        self.render_ms = job["ms"]
        self._place_image()
        self.update()

    def finish_render(self) -> None:
        """Draw the pending track image to the end now (tests, screenshots)."""
        self._step_job(None)

    def _layout_tiles(self) -> None:
        w, h = self.width(), self.height()
        z = self.zoom
        n = 1 << z
        s = proj.world_px(z)
        left = self.cx * s - w / 2.0
        top = self.cy * s - h / 2.0
        tx0, ty0 = int(math.floor(left / proj.TILE)), int(math.floor(top / proj.TILE))
        tx1, ty1 = int(math.floor((left + w) / proj.TILE)), int(math.floor((top + h) / proj.TILE))
        theme = T.name
        tiles = self.tiles
        self._tiles_on, self._grid_on = [], []
        want = []
        centre = ((tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                target = QRectF(tx * proj.TILE - left, ty * proj.TILE - top, proj.TILE, proj.TILE)
                if not (0 <= ty < n):
                    continue
                key = (z, tx % n, ty)
                pm = tiles.pixmap(key, theme) if tiles is not None else None
                if pm is not None:
                    self._tiles_on.append((target, pm, None))
                    continue
                if tiles is not None:
                    want.append(((tx - centre[0]) ** 2 + (ty - centre[1]) ** 2, key))
                    fb = tiles.fallback(key, theme)
                    if fb is not None:
                        self._tiles_on.append((target, fb[0], fb[1]))
                        continue
                self._grid_on.append(target)
        if tiles is not None and (self.track is not None or self.pins or self.live is not None):
            # only when there is something to show: an empty map asks for nothing
            tiles.want([key for _, key in sorted(want)])
        # the plain grid where nothing is: lines every GRID_STEP px on the world's
        # grid, inside the tiles that have nothing to show
        path = QPainterPath()
        for r in self._grid_on:
            for k in range(0, proj.TILE, GRID_STEP):
                x, y = r.x() + k + 1, r.y() + k + 1
                path.moveTo(x, r.y())
                path.lineTo(x, r.bottom())
                path.moveTo(r.x(), y)
                path.lineTo(r.right(), y)
        self._grid_path = path

    def _layout_boxes(self) -> None:
        """The opaque boxes over the map: title, scale + north, attribution."""
        w, h = self.width(), self.height()
        boxes = {}
        texts = {}
        if self.title or self.caption:
            tw = min(w - 16, max(paint.text_w(T.font(15, 800), self.title),
                                 paint.text_w(T.font(13, 600), self.caption)) + 20)
            boxes["title"] = QRectF(8, 8, tw, 50 if self.caption else 30)
            texts["title"] = paint.elide(T.font(15, 800), self.title, tw - 20)
            texts["caption"] = paint.elide(T.font(13, 600), self.caption, tw - 20)
        box = boxes["scale"] = QRectF(8, h - 8 - 52, 214, 52)
        ax, ay = box.x() + 22, box.y() + 8
        self._north = (QPolygonF([QPointF(ax, ay + 14), QPointF(ax, ay + 38), QPointF(ax - 9, ay + 38)]),
                       QPolygonF([QPointF(ax, ay + 14), QPointF(ax + 9, ay + 38), QPointF(ax, ay + 38)]))
        attr = texts["attr"] = self._attribution()
        aw = min(w - 16 - 214 - 8, paint.text_w(T.font(12, 700), attr) + 16)
        texts["attr"] = paint.elide(T.font(12, 700), attr, aw - 12)
        boxes["attr"] = QRectF(w - 8 - aw, h - 8 - 24, aw, 24)
        self._boxes, self._texts = boxes, texts

    def _attribution(self) -> str:
        tiles = self.tiles
        parts = []
        if tiles is not None and tiles.source.attribution:
            parts.append(tiles.source.attribution)
        if self._status:
            parts.append(self._status)
        elif tiles is None:
            parts.append("no map tiles")
        return "  ·  ".join(parts) if parts else " "

    def _dot_rect(self, xy) -> QRectF | None:
        if xy is None:
            return None
        return QRectF(xy[0] - 26, xy[1] - 26, 52, 52)

    def _update_rects(self, *rects) -> None:
        for r in rects:
            if r is not None:
                self.update(r.toAlignedRect())

    def resizeEvent(self, _event) -> None:
        self._layout()

    # ---- paint ----

    def paintEvent(self, _event) -> None:
        t0 = time.perf_counter()
        p = QPainter(self)
        w, h = self.width(), self.height()
        # ground: tiles, else a plain grid
        p.fillRect(self.rect(), T.panel)
        for target, pm, source in self._tiles_on:
            if source is None:
                p.drawPixmap(target.topLeft(), pm)
            else:
                p.drawPixmap(target, pm, source)
        if self._grid_on:
            p.setPen(T.pen("sunk", 2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(self._grid_path)
        # the live trail through the view's transform, the track as its image
        if self._trail is not None:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setTransform(self._xf)
            p.setPen(self._trail_pen)
            p.drawPolyline(self._trail)
            p.resetTransform()
            p.setRenderHint(QPainter.Antialiasing, False)
        if self._timg is not None:
            p.drawImage(self._timg_target, self._timg)
        for pin in self._pins_on:
            draw_pin(p, pin.kind, pin.sx, pin.sy, pin.label, pin.selected)
        if self._stacks:
            font = T.font(13, 800)
            p.setFont(font)
            for x, y, text in self._stacks:            # an inverted tag: "3" here
                r = QRectF(x, y, 22, 20)
                p.fillRect(r.adjusted(-2, -2, 2, 2), T.bg)
                p.fillRect(r, T.fg)
                p.setPen(T.bg)
                p.drawText(r, Qt.AlignCenter, text)
        if self._live_xy is not None:
            draw_dot(p, self._live_xy[0], self._live_xy[1], 9.0, self.live_heading)
        if self._cursor_xy is not None:
            draw_dot(p, self._cursor_xy[0], self._cursor_xy[1], 8.0, self.cursor_heading)
        self._paint_boxes(p)
        p.setPen(T.pen("rule", 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
        p.end()
        self.paint_ms = (time.perf_counter() - t0) * 1000.0
        self.paints += 1
        self.paint_times.append(self.paint_ms)

    def _paint_boxes(self, p: QPainter) -> None:
        box = self._boxes.get("title")
        if box is not None:
            paint.frame(p, box, 2, T.rule, T.panel)
            p.setFont(T.font(15, 800))
            p.setPen(T.fg)
            p.drawText(QRectF(box.x() + 10, box.y() + 4, box.width() - 20, 22), Qt.AlignVCenter | Qt.AlignLeft,
                       self._texts["title"])
            if self.caption:
                p.setFont(T.font(13, 600))
                p.setPen(T.fg2)
                p.drawText(QRectF(box.x() + 10, box.y() + 25, box.width() - 20, 20),
                           Qt.AlignVCenter | Qt.AlignLeft, self._texts["caption"])
        box = self._boxes["scale"]
        paint.frame(p, box, 2, T.rule, T.panel)
        # north arrow: a filled half and a hollow half, "N" above
        ax, ay = box.x() + 22, box.y() + 8
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(T.fg)
        p.drawPolygon(self._north[0])
        p.setPen(T.cpen(T.fg, 2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(self._north[1])
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setFont(T.font(13, 800))
        p.setPen(T.fg)
        p.drawText(QRectF(ax - 10, ay - 2, 20, 16), Qt.AlignCenter, "N")
        length, label = self._scale
        bx = box.x() + 44
        by = box.y() + 34
        length = min(length, box.right() - 12 - bx)
        if length > 0:
            p.fillRect(QRectF(bx, by, length, 4), T.fg)
            p.fillRect(QRectF(bx, by - 8, 2, 12), T.fg)
            p.fillRect(QRectF(bx + length - 2, by - 8, 2, 12), T.fg)
            p.setFont(T.font(13, 700, mono=True))
            p.drawText(QRectF(bx, box.y() + 6, 160, 20), Qt.AlignVCenter | Qt.AlignLeft, label)
        box = self._boxes["attr"]
        paint.frame(p, box, 2, T.rule, T.panel)
        p.setFont(T.font(12, 700))
        p.setPen(T.fg2)
        p.drawText(box, Qt.AlignCenter, self._texts["attr"])

    # ---- mouse ----

    def pin_at(self, sx: float, sy: float) -> Pin | None:
        best, best_d = None, HIT_PX
        for pin in self._pins_on:
            d = math.hypot(pin.sx - sx, pin.sy - sy)
            if d <= best_d and pin.kind != "start":
                best, best_d = pin, d
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = self._last = event.position()
            self._dragged = False
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        pos = event.position()
        if not self._dragged and (pos - self._press).manhattanLength() < 5:
            return
        self._dragged = True
        d = pos - self._last
        self._last = pos
        self.pan_px(d.x(), d.y())
        self.moved.emit()

    def mouseReleaseEvent(self, event) -> None:
        press, self._press = self._press, None
        self.setCursor(Qt.OpenHandCursor)
        if press is None or self._dragged or event.button() != Qt.LeftButton:
            return
        pos = event.position()
        pin = self.pin_at(pos.x(), pos.y())
        if pin is not None:
            self.pinClicked.emit(pin.payload)
            return
        self.clicked.emit(*self.unit_at(pos.x(), pos.y()))

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position()
        self.zoom_by(1, (pos.x(), pos.y()))
        self.moved.emit()

    def wheelEvent(self, event) -> None:
        dy = event.angleDelta().y()
        if dy:
            pos = event.position()
            self.zoom_by(1 if dy > 0 else -1, (pos.x(), pos.y()))
            self.moved.emit()

    def stats(self) -> dict:
        geo = self._geo.get(self.zoom)
        times = np.array(self.paint_times) if self.paint_times else np.zeros(1)
        return {"zoom": self.zoom, "runs": len(self._runs_on), "pending": self._job is not None,
                "renders": self.renders, "render_ms": round(self.render_ms, 1),
                "points": geo.points if geo else 0, "track_points": len(self.track) if self.track is not None else 0,
                "tiles": len(self._tiles_on), "grid": len(self._grid_on), "pins": len(self._pins_on),
                "paint_ms": round(self.paint_ms, 2), "paints": self.paints,
                "paint_p50": round(float(np.percentile(times, 50)), 2),
                "paint_p95": round(float(np.percentile(times, 95)), 2), "paint_max": round(float(times.max()), 2)}


__all__ = ["MapCanvas", "Pin", "draw_dot", "draw_pin"]
