"""F4, stall forensics replay (HANDOFF 5.4).

Stacked 2D lanes with one cursor: time-synced signals read better this way
than in 3D. All traces live in a single pyqtgraph ViewBox (one unit of y is
one pixel), and the cursor is an overlay that repaints only a thin strip.

A drive or capture is drawn from ``hondalog.analysis.ReplaySession`` through
``ui/replaymodel.ReplayModel``: one min/max envelope per lane for the visible
window and the plot's pixel width (so an hour of 1 kHz black box data costs
what a minute does and a one-sample dropout is still drawn), two cursors A
and B with per-lane delta readouts, zoom and pan, jumps from event to event,
and a second drive laid over the first with a choice of what to line them up
on. The stall forensics (order of loss, held up, reading) sit beside it.

With no file open the lanes follow the live bus (last 40 s).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QPainter
from PySide6.QtWidgets import QGraphicsItem, QMenu, QWidget

from .. import adapters as ad
from .. import paint
from ..controls import Button, Card, Segment, TextCard
from ..replaymodel import ALIGN_SHORT, ALIGNMENTS, ReplayModel
from ..theme import T
from ..units import U

LABEL_W = 150
GROUPS = (
    ("ECU", "ECU · K-line", "series1", 40, ("rpm", "map", "tps", "o2", "inj")),
    ("BB", "Black box", "series2", 40, ("bbatt", "ecub", "fpv", "ig1", "tach")),
    ("STATE", "States", "fg", 20, ("fuel_pump", "closed_loop", "bbmark")),
)
HEAD_H, AXIS_H = 16, 22
LIVE_WINDOW = 40.0


class Lane:
    __slots__ = ("cid", "label", "token", "y", "h", "curve", "twin", "over", "has_data", "value",
                 "delta", "flag", "lo", "hi")

    def __init__(self, cid, token, y, h):
        self.cid, self.token, self.y, self.h = cid, token, y, h
        self.label = cid.upper()
        self.curve = None
        self.twin = None            # live mode: second 1 px trace, one pixel lower
        self.over = None            # a second drive laid over this one
        self.has_data = False
        self.value = ""             # at cursor A
        self.delta = ""             # A minus B, when cursor B is set
        self.flag = False
        self.lo, self.hi = 0.0, 1.0


def signed_time(t: float, decimals: int = 1) -> str:
    sign = "−" if t < -0.5 * 10 ** -decimals else ("+" if t > 0 else "")
    return f"{sign}{abs(t):.{decimals}f}"


def signed_span(spec, span: float) -> str:
    """A difference of two readings in the display unit: "+1.25", "−320", "0"."""
    if span != span:
        return "---"
    shown = U.spec(spec)
    value = span * U.scale(spec)[0]
    text = f"{abs(value):.{shown.decimals}f}"
    if float(text) == 0.0:
        return text
    return ("−" if value < 0 else "+") + text


class LaneBlock(QWidget):
    """Label column + plot + overlay. Shows what the ReplayModel holds: the
    window, the two cursors, the traces."""

    cursorMoved = Signal(float)
    windowChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: ad.LogView | None = None
        self.model: ReplayModel | None = None       # None while the lanes are live
        self.lanes: list[Lane] = []
        self.heads: list = []               # (y, text, token)
        y = 2
        for _key, title, token, lane_h, ids in GROUPS:
            self.heads.append([y, title, token])
            y += HEAD_H
            for cid in ids:
                self.lanes.append(Lane(cid, token, y, lane_h))
                y += lane_h
        self.axis_y = y
        self.total_h = y + AXIS_H + 2
        self.setFixedHeight(self.total_h)
        self.t0, self.t1 = -30.0, 10.0
        self.span = (-30.0, 10.0)
        self.cursor = 0.0
        self.cursor_b: float | None = None
        self.marks: list = []               # (t, text)
        self.trigger: float | None = None
        self.axis_title = "s from stall"
        self.traces = 0                     # how often the lanes were redrawn from the model

        self.gv = pg.GraphicsLayoutWidget(parent=self)
        self.gv.ci.setContentsMargins(0, 0, 0, 0)
        self.gv.setFrameShape(self.gv.Shape.NoFrame)
        self.gv.setFocusPolicy(Qt.NoFocus)
        self.vb = self.gv.addViewBox(enableMouse=False, enableMenu=False, invertY=True)
        self.vb.setDefaultPadding(0)
        for lane in self.lanes:
            step = lane.token == "fg"
            over = pg.PlotDataItem(antialias=True, stepMode="right" if step else None, connect="finite")
            over.setClipToView(True)
            self.vb.addItem(over)           # first: the second drive is drawn under the first
            lane.over = over
            curve = pg.PlotDataItem(antialias=True, stepMode="right" if step else None)
            curve.setClipToView(True)
            # Rendered once into a pixmap: moving the cursor only blits.
            curve.curve.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            self.vb.addItem(curve)
            lane.curve = curve
            twin = pg.PlotDataItem(antialias=False, stepMode="right" if step else None)
            twin.setDownsampling(auto=True, method="peak")
            twin.setClipToView(True)
            self.vb.addItem(twin)
            lane.twin = twin
        self._live_pens: bool | None = None
        self.overlay = _Overlay(self)
        self.retheme()

    # ---- theme / geometry ----

    def retheme(self) -> None:
        self.gv.setBackground(T.panel)
        self._set_pens(bool(self._live_pens))
        self.update()
        self.overlay.update()

    def _set_pens(self, live: bool) -> None:
        """A file view is stroked once, antialiased, 2 px, and cached; what it
        strokes is the model's envelope, already reduced to the plot's width.
        A live view redraws every lane ten times a second from the bus: there
        each trace is two 1 px cosmetic lines a pixel apart, which the raster
        engine draws several times faster than one stroked 2 px line, and
        pyqtgraph thins the points."""
        self._live_pens = live
        for lane in self.lanes:
            colour = T.color(lane.token)
            lane.curve.opts["antialias"] = not live
            lane.curve.opts["connect"] = "all" if live else "finite"
            lane.curve.setDownsampling(auto=live, method="peak")
            lane.curve.setPen(pg.mkPen(colour, width=1 if live else 2))
            lane.twin.setPen(pg.mkPen(colour, width=1))
            lane.over.setPen(pg.mkPen(T.color("rule2"), width=2))
            if not live:
                lane.twin.setData([], [])
            else:
                lane.over.setData([], [])

    def plot_w(self) -> int:
        return max(10, self.width() - LABEL_W - 2)

    def resizeEvent(self, _event) -> None:
        r = QRect(LABEL_W, 2, self.plot_w(), self.axis_y - 2)
        self.gv.setGeometry(r)
        self.overlay.setGeometry(LABEL_W, 0, self.plot_w(), self.total_h)
        self._range()

    def _range(self) -> None:
        self.vb.setRange(xRange=(self.t0, self.t1), yRange=(2, self.axis_y), padding=0)
        if self.model is not None:
            self._retrace()
        self.overlay.update()

    def x_of(self, t: float) -> float:
        return (t - self.t0) / (self.t1 - self.t0) * self.plot_w()

    def t_of(self, x: float) -> float:
        return self.t0 + x / self.plot_w() * (self.t1 - self.t0)

    # ---- data ----

    def set_view(self, view: ad.LogView, keep_window: bool = False) -> None:
        self.view = view
        live = view.live
        if live != self._live_pens:
            self._set_pens(live)
        if live:
            self.model = None
        elif self.model is None or self.model.view is not view:
            self.model = ReplayModel(view)
            keep_window = False
        for lane in self.lanes:
            spec = view.specs.get(lane.cid)
            t = view.t.get(lane.cid)
            lane.has_data = spec is not None and t is not None and len(t) > 1
            if not lane.has_data:
                lane.curve.setData([], [])
                lane.twin.setData([], [])
                lane.over.setData([], [])
                if spec is not None:
                    lane.label = f"{spec.label.upper()} {U.spec(spec).units}".strip()
                continue
            v = view.data[lane.cid]
            lane.flag = spec.is_flag
            units = U.spec(spec).units          # the lane is drawn to its own scale: only the label and
            units = "" if units.lower() == spec.label.lower() else units    # the readout are converted
            lane.label = f"{spec.label.upper()} {units}".strip()
            if spec.is_flag:
                lo, hi = 0.0, 1.0
            elif live:
                lo, hi = spec.lo, spec.hi
            else:
                lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
                pad = max(0.1 * (hi - lo), 0.04 * (spec.hi - spec.lo))
                lo, hi = lo - pad, hi + pad
            lane.lo, lane.hi = lo, hi
            if live:
                y = self._lane_y(lane, v)
                lane.curve.setData(t, y)
                lane.twin.setData(t, y + 1.0)
        self.heads[0][1] = "ECU · K-line"
        self.heads[1][1] = "Black box"
        if view.align_residual_ms is not None and view.align_residual_ms > 5:
            self.heads[1][1] += (f" · aligned to ECU on tach edges, residual "
                                 f"{view.align_residual_ms:.0f} ms")
        self.marks = list(view.markers)
        self.trigger = view.trigger_t
        self.axis_title = ("s from now" if live else
                           "s from stall" if view.trigger_t is not None else "s from start")
        model = self.model
        if model is not None and not keep_window:
            lo, hi = self.span = model.span
            if view.trigger_t is not None and min(hi, 10.0) - max(lo, -30.0) >= 1.0:
                model.set_window(max(lo, -30.0), min(hi, 10.0))     # the stall window (HANDOFF 5.4)
            else:
                model.show_all()
            self.t0, self.t1 = model.window
            self.cursor, self.cursor_b = model.cursor_a, None
            model.clear_cursor_b()
            self._range()
        elif model is not None:
            self._retrace()
        self.update()
        self.overlay.update()

    @staticmethod
    def _lane_y(lane: Lane, v) -> np.ndarray:
        f = np.clip((np.asarray(v, dtype=float) - lane.lo) / ((lane.hi - lane.lo) or 1.0), 0.0, 1.0)
        return lane.y + lane.h - 4 - f * (lane.h - 8)

    def _retrace(self) -> None:
        """The traces for the window that is showing now: one envelope per
        lane from the replay session, at one column per plot pixel."""
        model = self.model
        if model is None:
            return
        columns = self.plot_w()
        self.traces += 1
        for lane in self.lanes:
            if not lane.has_data or not model.has(lane.cid):
                continue
            x, v, _reduced = model.trace(lane.cid, columns)
            lane.curve.setData(x, self._lane_y(lane, v))
            over = model.overlay_trace(lane.cid, columns)
            if over is None:
                lane.over.setData([], [])
            else:
                lane.over.setData(over[0], self._lane_y(lane, over[1]))

    def values_at_cursor(self) -> None:
        """The label column: each lane's value at cursor A and, with cursor B
        set, how much it differs from the value at B (the session's readouts)."""
        view, model = self.view, self.model
        rows = model.readouts([lane.cid for lane in self.lanes if lane.has_data], full=False) if model else {}
        for lane in self.lanes:
            lane.delta = ""
            if view is None or not lane.has_data:
                lane.value = "---"
                continue
            spec = view.specs[lane.cid]
            row = rows.get(lane.cid)
            if row is None:                 # live: the newest sample on the bus
                lane.value = U.text(spec, view.value_at(lane.cid, self.cursor))
                continue
            lane.value = U.text(spec, row.value)
            if row.stats is not None and not spec.is_flag:
                lane.delta = "Δ " + signed_span(spec, -row.stats.delta)     # A minus B, like the time

    # ---- cursors / zoom / pan ----

    def set_cursor(self, t: float, emit: bool = True, force: bool = False) -> None:
        """Move cursor A (and scroll the window if it would leave it)."""
        model = self.model
        if model is not None:
            t = model.set_cursor(t)
        else:
            lo, hi = self.span
            t = min(hi, max(lo, t))
        if t == self.cursor and not force:
            return
        old_x = self.x_of(self.cursor)
        self.cursor = t
        if model is not None and model.reveal(t):       # keep the cursor in view
            self.t0, self.t1 = model.window
            self._range()
            self.windowChanged.emit()
        elif model is not None and (self.t0, self.t1) != model.window:
            self.sync_window()
        else:
            for x in (old_x, self.x_of(t)):
                self.overlay.update(QRect(int(x) - 14, 0, 28, self.total_h))
        self.values_at_cursor()
        self.update(QRect(0, 0, LABEL_W, self.total_h))
        if emit:
            self.cursorMoved.emit(t)

    def set_cursor_b(self, t: float | None) -> None:
        """Place, move or (None) remove cursor B, the reference. The readouts
        then show A minus B."""
        model = self.model
        if model is None:
            return
        if t is None:
            model.clear_cursor_b()
            self.cursor_b = None
        else:
            self.cursor_b = model.set_cursor(t, "b")
        self.values_at_cursor()
        self.update(QRect(0, 0, LABEL_W, self.total_h))
        self.overlay.update()
        self.cursorMoved.emit(self.cursor)

    def sync_window(self) -> None:
        """Take the window from the model after it changed there."""
        if self.model is not None:
            self.t0, self.t1 = self.model.window
            self._range()
            self.windowChanged.emit()

    def zoom(self, factor: float, around: float | None = None) -> None:
        """factor < 1 zooms in, around the cursor unless told otherwise."""
        if self.model is None:
            return
        self.model.zoom(factor, self.cursor if around is None else around)
        self.sync_window()

    def pan(self, fraction: float) -> None:
        if self.model is not None:
            self.model.pan(fraction)
            self.sync_window()

    def show_all(self) -> None:
        if self.model is not None:
            self.model.show_all()
            self.sync_window()

    # ---- paint (label column and frame) ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        for y, text, token in self.heads:
            p.fillRect(QRectF(2, y, LABEL_W - 2, HEAD_H), T.sunk)
            _head_text(p, 8, y, text, token)
        name, mono, small = T.font(13, 800), T.font(15, 700, mono=True), T.font(12, 700, mono=True)
        for lane in self.lanes:
            p.fillRect(QRectF(2, lane.y + lane.h - 2, LABEL_W - 2, 2), T.sunk)
            two = bool(lane.delta) and lane.h >= 36     # value at A above, A minus B below
            row = QRectF(8, lane.y, LABEL_W - 16, lane.h - 2)
            p.setPen(T.fg)
            p.setFont(mono)
            vw = paint.text_w(mono, lane.value)
            if two:
                p.drawText(QRectF(8, lane.y + 1, LABEL_W - 16, 20), Qt.AlignVCenter | Qt.AlignRight,
                           lane.value)
                p.setFont(small)
                p.setPen(T.accent)
                p.drawText(QRectF(8, lane.y + 20, LABEL_W - 16, 16), Qt.AlignVCenter | Qt.AlignRight,
                           paint.elide(small, lane.delta, LABEL_W - 16))
                p.setPen(T.fg)
                row = QRectF(8, lane.y + 1, LABEL_W - 16, 20)
            else:
                p.drawText(row, Qt.AlignVCenter | Qt.AlignRight, lane.value)
            p.setFont(name)
            p.drawText(row, Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(name, lane.label, LABEL_W - 22 - vw))
        p.setFont(T.font(12, 700, mono=True))
        p.setPen(T.fg)
        p.drawText(QRectF(8, self.axis_y, LABEL_W, AXIS_H), Qt.AlignVCenter | Qt.AlignLeft,
                   self.axis_title)
        paint.frame(p, QRectF(0, 0, w, self.total_h), 2, T.rule)
        p.end()


def _head_text(p: QPainter, x: float, y: float, text: str, token: str) -> None:
    """Group header: colour swatch + fg caption (series colours are never text
    on sunk: series 2 is only 6.4:1 there)."""
    if token != "fg":
        p.fillRect(QRectF(x, y + 3, 10, 10), T.color(token))
        x += 18
    p.setFont(T.font(12, 800, spacing=0.06))
    p.setPen(T.fg)
    p.drawText(QRectF(x, y, 900, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, text.upper())


class _Overlay(QWidget):
    """Everything drawn over the traces: headers, markers, cursors, time axis."""

    def __init__(self, block: LaneBlock):
        super().__init__(block)
        self.block = block
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.SizeHorCursor)
        self._pan_from: tuple | None = None

    def mousePressEvent(self, event) -> None:
        b = self.block
        x = event.position().x()
        if event.button() == Qt.RightButton:        # drag with the right button: pan
            self._pan_from = (x, b.t0, b.t1)
        elif event.modifiers() & Qt.ShiftModifier:  # Shift: the second cursor
            b.set_cursor_b(b.t_of(x))
        else:
            b.set_cursor(b.t_of(x))

    def mouseMoveEvent(self, event) -> None:
        b = self.block
        x = event.position().x()
        if event.buttons() & Qt.RightButton and self._pan_from is not None and b.model is not None:
            x0, t0, t1 = self._pan_from
            shift = (x0 - x) / b.plot_w() * (t1 - t0)
            b.model.set_window(t0 + shift, t1 + shift)
            b.sync_window()
        elif event.buttons() & Qt.LeftButton:
            if event.modifiers() & Qt.ShiftModifier:
                b.set_cursor_b(b.t_of(x))
            else:
                b.set_cursor(b.t_of(x))

    def mouseReleaseEvent(self, _event) -> None:
        self._pan_from = None

    def wheelEvent(self, event) -> None:
        """Wheel: zoom around the pointer. Shift + wheel: pan."""
        b = self.block
        delta = event.angleDelta().y() or event.angleDelta().x()
        if b.model is None or not delta:
            return
        if event.modifiers() & Qt.ShiftModifier:
            b.pan(-0.15 if delta > 0 else 0.15)
        else:
            b.zoom(0.8 if delta > 0 else 1.25, around=b.t_of(event.position().x()))
        event.accept()

    def paintEvent(self, event) -> None:
        b = self.block
        p = QPainter(self)
        p.setClipRect(event.rect())
        w = self.width()
        for y, text, token in b.heads:
            p.fillRect(QRectF(0, y, w, HEAD_H), T.sunk)
            p.save()
            p.translate(-LABEL_W, 0)
            _head_text(p, 8, y, text, token)
            p.restore()
        for lane in b.lanes:
            if not lane.has_data:
                p.fillRect(QRectF(0, lane.y, w, lane.h - 2), paint.hatch_brush())
            p.fillRect(QRectF(0, lane.y + lane.h - 2, w, 2), T.sunk)
        # time axis
        ay = b.axis_y
        p.fillRect(QRectF(0, ay, w, AXIS_H), T.panel)
        span = b.t1 - b.t0
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600)
                     if s / span * w >= 64), 600)
        mono = T.font(13, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        k = int(np.ceil(b.t0 / step - 1e-9))
        while k * step <= b.t1 + 1e-9:
            t = k * step
            x = b.x_of(t)
            text = signed_time(t, 1 if step < 1 else 0) if b.trigger is not None or t < 0 \
                else f"{t:g}"
            tw = paint.text_w(mono, text)
            p.drawText(QPointF(min(max(2.0, x - tw / 2), w - tw - 2), ay + 16), text)
            k += 1
        # markers
        tag = T.font(12, 800, spacing=0.04)
        for t, text in b.marks:
            if b.t0 <= t <= b.t1:
                x = b.x_of(t)
                p.setPen(T.cpen(T.fg, 3, Qt.FlatCap, dash=(2.0, 1.4)))
                p.drawLine(QPointF(x, HEAD_H), QPointF(x, ay))
                tw = paint.text_w(tag, text) + 12
                p.fillRect(QRectF(x - 1.5, 2, tw, HEAD_H - 1), T.fg)
                p.setFont(tag)
                p.setPen(T.bg)
                p.drawText(QRectF(x - 1.5, 2, tw, HEAD_H - 1), Qt.AlignCenter, text)
        if b.trigger is not None and b.t0 <= b.trigger <= b.t1:
            x = b.x_of(b.trigger)
            p.fillRect(QRectF(x - 2, 2, 4, ay - 2), T.alarm)
            tw = paint.text_w(tag, "STALL") + 14
            p.fillRect(QRectF(x - 2, 2, tw, HEAD_H - 1), T.alarm)
            p.setFont(tag)
            p.setPen(T.on_alarm)
            p.drawText(QRectF(x - 2, 2, tw, HEAD_H - 1), Qt.AlignCenter, "STALL")
        # cursors: A solid with the handle, B dashed; lettered once there are two
        both = b.cursor_b is not None
        letter = T.font(12, 800)
        if both and b.t0 <= b.cursor_b <= b.t1:
            x = b.x_of(b.cursor_b)
            p.setPen(T.cpen(T.accent, 3, Qt.FlatCap, dash=(3.0, 2.0)))
            p.drawLine(QPointF(x, 2), QPointF(x, ay))
            self._tag(p, letter, x, ay + 3, "B")
        if b.view is not None and not b.view.live and b.t0 <= b.cursor <= b.t1:
            x = b.x_of(b.cursor)
            p.fillRect(QRectF(x - 2, 2, 4, ay + 6 if not both else ay - 2), T.accent)
            if both:
                self._tag(p, letter, x, ay + 3, "A")
            else:
                p.setRenderHint(QPainter.Antialiasing, True)
                paint.shape(p, paint.UP, x - 8, ay + 8, 32, T.accent)
        p.end()

    def _tag(self, p: QPainter, font, x: float, y: float, text: str) -> None:
        r = QRectF(min(max(0.0, x - 8), self.width() - 16.0), y, 16, 16)
        p.fillRect(r, T.accent)
        p.setFont(font)
        p.setPen(T.on_accent)
        p.drawText(r, Qt.AlignCenter, text)


class Scrubber(QWidget):
    """Full-width slider under the lanes, for touchpad-free scrubbing."""

    moved = Signal(float)                   # 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frac = 0.0
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, frac: float) -> None:
        frac = min(1.0, max(0.0, frac))
        if abs(frac - self.frac) > 1e-4:
            self.frac = frac
            self.update()

    def _emit(self, event) -> None:
        x0, x1 = LABEL_W + 10.0, self.width() - 10.0
        self.moved.emit(min(1.0, max(0.0, (event.position().x() - x0) / (x1 - x0))))

    mousePressEvent = _emit

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._emit(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.setFont(T.font(13, 700))
        p.setPen(T.fg)
        p.drawText(QRectF(6, 0, LABEL_W, 28), Qt.AlignVCenter | Qt.AlignLeft,
                   "Scrub · ← → step")
        x0, x1 = LABEL_W + 10.0, self.width() - 10.0
        x = x0 + self.frac * (x1 - x0)
        paint.track(p, QRectF(x0, 10, x1 - x0, 8), self.frac, T.accent)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(T.cpen(T.bg, 2))
        p.setBrush(T.accent)
        p.drawEllipse(QPointF(x, 14), 9, 9)
        p.end()


class LossCard(Card):
    """Order of loss: rank, time before the stall, signal, what happened."""

    jump = Signal(float)
    ROW = 50

    def __init__(self, parent=None):
        super().__init__("Order of loss · first to fall", parent=parent)
        self.rows: list = []
        self._wrapped: list = []
        self.setCursor(Qt.PointingHandCursor)

    def set_rows(self, rows) -> None:
        self.rows = list(rows)
        self._wrapped = []
        self.update()

    def visible_rows(self) -> list:
        room = max(1, int((self.height() - 32) // self.ROW))
        if len(self.rows) <= room:
            return self.rows
        return self.rows[:room - 1] + self.rows[-1:]     # always keep the trigger row

    def mousePressEvent(self, event) -> None:
        i = int((event.position().y() - 30) // self.ROW)
        rows = self.visible_rows()
        if 0 <= i < len(rows):
            self.jump.emit(rows[i].t)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        paint.frame(p, QRectF(self.rect()), 2, T.rule, T.panel)
        paint.caption(p, 10, 8, self.title)
        rank, mono = T.font(18, 700, mono=True), T.font(14, 700, mono=True)
        sig, what = T.font(14, 800), T.font(13, 600)
        rows = self.visible_rows()
        if len(self._wrapped) != len(rows):
            self._wrapped = [paint.wrap(what, r.what, w - 136)[:2] for r in rows]
        y = 30.0
        if not rows:
            p.setFont(T.font(14, 600))
            p.setPen(T.fg2)
            p.drawText(QRectF(10, y, w - 20, 40), Qt.AlignVCenter | Qt.AlignLeft,
                       "Open a stall capture to see what fell first.")
        for n, row in enumerate(rows):
            r = QRectF(2, y, w - 4, self.ROW)
            hot = n == 0 and len(rows) > 1
            if hot:
                p.fillRect(r, T.alarm)
            ink = T.on_alarm if hot else T.fg
            p.fillRect(QRectF(2, y + self.ROW - 2, w - 4, 2), T.alarm if hot else T.sunk)
            p.setPen(ink)
            p.setFont(rank)
            idx = self.rows.index(row) + 1
            p.drawText(QRectF(12, y, 26, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft, str(idx))
            p.setFont(mono)
            p.drawText(QRectF(36, y, 78, self.ROW - 2), Qt.AlignVCenter | Qt.AlignRight,
                       f"{signed_time(row.t, 2)} s")
            lines = self._wrapped[n]
            top = y + (self.ROW - 2 - 18 - 16 * len(lines)) / 2
            p.setFont(sig)
            p.drawText(QRectF(126, top, w - 136, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(sig, row.signal, w - 136))
            p.setFont(what)
            for i, line in enumerate(lines):
                p.drawText(QRectF(126, top + 18 + 16 * i, w - 136, 16),
                           Qt.AlignVCenter | Qt.AlignLeft, paint.elide(what, line, w - 136))
            y += self.ROW
        p.end()


class ReplayScreen(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.view: ad.LogView | None = None
        self.forensics = ad.Forensics()
        self.playing = False
        self._play_t = 0.0
        self._live_t = -1.0
        self._file = ""
        self._note = ""
        self._view_note = ""
        self._cursor_text = ""
        self._delta_text = ""
        self._swatch = False                # the note describes the grey traces: draw their swatch
        self.prev_btn = Button(icon=paint.PREV, parent=self)
        self.play_btn = Button(icon=paint.PLAY, parent=self)
        self.next_btn = Button(icon=paint.NEXT, parent=self)
        self.compare_btn = Button("Compare", "C", parent=self)
        self.open_btn = Button("Open", "O", parent=self)
        self.live_btn = Button("Live", "L", parent=self)
        self.prev_btn.clicked.connect(lambda: self.step_event(-1))
        self.next_btn.clicked.connect(lambda: self.step_event(1))
        self.play_btn.clicked.connect(self.toggle_play)
        self.compare_btn.clicked.connect(self.compare)
        self.open_btn.clicked.connect(self.open_dialog)
        self.live_btn.clicked.connect(self.go_live)
        self.compare_btn.setToolTip("Lay a second drive over this one (C). With one laid over: "
                                    "what to line them up on (A cycles), , and . slide it, "
                                    "Shift+C removes it.")
        self.block = LaneBlock(self)
        self.block.cursorMoved.connect(self._cursor_moved)
        self.scrub = Scrubber(self)
        self.scrub.moved.connect(self._scrubbed)
        self.loss = LossCard(self)
        self.loss.jump.connect(self.block.set_cursor)
        self.held = TextCard("Held up through the stall")
        self.held.setParent(self)
        self.reading = TextCard("Reading", border=4, px=16, weight=700)
        self.reading.setParent(self)
        self.jump_seg = Segment([("stall", "STALL")], momentary=True, parent=self)
        self.jump_seg.picked.connect(self._jump)
        self._jumps: dict = {}
        if session.demo:
            from ..backends import demo_stall_view
            self.show_view(demo_stall_view())
        else:
            self.go_live()

    @property
    def model(self) -> ReplayModel | None:
        """The replay session behind the lanes (None while they are live)."""
        return self.block.model

    # ---- loading ----

    def open(self, path) -> bool:
        try:
            view = self.session.logs.read(path)
        except (OSError, ValueError) as exc:
            self._note = f"cannot open: {exc}"
            self.update()
            return False
        self.show_view(view)
        return True

    def _pick(self):
        """The drive picker: drives and captures from the database, or a file to import."""
        from ..drivepicker import DrivePicker
        picker = DrivePicker(self.session.logs, self)
        return picker.chosen if picker.exec() and picker.chosen is not None else None

    def open_dialog(self) -> None:
        chosen = self._pick()
        if chosen is not None:
            self.open(chosen)

    def show_view(self, view: ad.LogView) -> None:
        self.view = view
        self.playing = False
        self.play_btn.icon_path = paint.PLAY
        self.forensics = self.session.logs.analyze(view) if not view.live else ad.Forensics()
        self.block.set_view(view)
        self._file, self._note = view.title, view.note
        self._view_note, self._swatch = view.note, False
        self.loss.set_rows(self.forensics.loss if view.trigger_t is not None else [])
        self.held.set_text(self.forensics.held or "—")
        self.reading.set_text(self.forensics.reading or "—")
        jumps = [(f"m{i}", text, t) for i, (t, text) in enumerate(view.markers[:3])]
        if view.trigger_t is not None:
            jumps.append(("stall", "STALL", view.trigger_t))
        if not jumps:
            jumps = [("start", "Start", view.span()[0]), ("end", "End", view.span()[1])]
        self._jumps = {k: t for k, _, t in jumps}
        self.jump_seg.set_options([(k, text) for k, text, _ in jumps])
        self.jump_seg.current = None
        model = self.model
        if model is not None:
            # what fell, in order, joins the log's markers, triggers and alarms
            # as moments [ and ] step through
            known = {round(t, 3) for t, _, _ in model.events()}
            for row in self.forensics.loss:
                if row.channel_id and round(row.t, 3) not in known:
                    model.add_event(row.t, "loss", row.signal)
        self.compare_btn.setEnabled(model is not None)
        start = self.forensics.loss[0].t if self.forensics.loss else view.span()[0]
        self.block.set_cursor(start, force=True)
        self.block.overlay.update()
        self.update()

    def go_live(self) -> None:
        self.playing = False
        self._live_t = -1.0
        self._refresh_live(self.session.bus.now(), first=True)

    def _refresh_live(self, now: float, first: bool = False) -> None:
        bus = self.session.bus
        view = ad.LogView(path=None, title="live", note="last 40 s · O opens a drive",
                          live=True)
        for lane in self.block.lanes:
            state = bus.channel(lane.cid)
            if state is None or not state.spec.mapped:
                continue
            t, v = bus.history(lane.cid, now - LIVE_WINDOW)
            view.specs[lane.cid] = state.spec
            view.t[lane.cid], view.data[lane.cid] = t - now, v
        if first:
            self.show_view(view)
            self.block.span = (-LIVE_WINDOW, 0.0)
            self.block.t0, self.block.t1 = -LIVE_WINDOW, 0.0
            self.block._range()
        else:
            self.view = view
            self.block.set_view(view, keep_window=True)
        self.block.cursor, self.block.cursor_b = 0.0, None
        self.block.values_at_cursor()
        self.block.update()
        self._cursor_moved(0.0)

    # ---- a second drive over this one ----

    def compare(self) -> None:
        """C: pick the drive to lay over this one; with one already there, the
        menu of what to line them up on."""
        model = self.model
        if model is None:
            self._say("Open a drive first (O): the live lanes have nothing to compare with")
            return
        if not model.overlay_name:
            self.pick_overlay()
            return
        menu = QMenu(self)
        menu.addAction(f"Over it: {model.overlay_name}").setEnabled(False)
        menu.addSeparator()
        for key, text in ALIGNMENTS:
            action = QAction(f"Line up on {text}", menu, checkable=True)
            action.setChecked(key == model.align)
            action.triggered.connect(lambda _=False, k=key: self.align_overlay(k))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Slide it 0.1 s earlier\t,", lambda: self.nudge_overlay(-0.1))
        menu.addAction("Slide it 0.1 s later\t.", lambda: self.nudge_overlay(0.1))
        menu.addSeparator()
        menu.addAction("Another drive…", self.pick_overlay)
        menu.addAction("Remove it\tShift+C", self.clear_overlay)
        menu.exec(QCursor.pos())

    def pick_overlay(self) -> None:
        chosen = self._pick()
        if chosen is not None:
            self.set_overlay(chosen)

    def set_overlay(self, path, align: str | None = None) -> bool:
        """Lay the drive or capture ``path`` (or an already read LogView) over
        the one that is open. Stall captures line up on the stall; anything
        else on the first sample, unless ``align`` says otherwise."""
        model = self.model
        if model is None or self.view is None:
            return False
        try:
            other = path if isinstance(path, ad.LogView) else self.session.logs.read(path)
        except (OSError, ValueError) as exc:
            self._say(f"cannot open: {exc}")
            return False
        if align is None:
            both = self.view.trigger_t is not None and other.trigger_t is not None
            align = "trigger" if both else "start"
        model.set_overlay(other, align)
        self._overlay_changed()
        return True

    def align_overlay(self, align: str) -> None:
        model = self.model
        if model is None or not model.overlay_name:
            return
        used = model.align_overlay(align)
        self._overlay_changed("" if used == align else
                              f"no {dict(ALIGNMENTS)[align]} in both drives: lined up on the first sample")

    def cycle_alignment(self) -> None:
        model = self.model
        if model is not None and model.overlay_name:
            keys = [k for k, _ in ALIGNMENTS]
            self.align_overlay(keys[(keys.index(model.align) + 1) % len(keys)] if model.align in keys
                               else keys[0])

    def nudge_overlay(self, seconds: float) -> None:
        model = self.model
        if model is not None and model.overlay_name:
            model.nudge_overlay(seconds)
            self._overlay_changed()

    def clear_overlay(self) -> None:
        model = self.model
        if model is not None and model.overlay_name:
            model.set_overlay(None)
            self._overlay_changed()

    def _overlay_changed(self, problem: str = "") -> None:
        model = self.model
        b = self.block
        self._swatch = model is not None and bool(model.overlay_name) and not problem
        if model is not None and model.overlay_name:
            # one short line next to a grey swatch: which drive, how far it was moved, on what
            self._note = problem or (f"{model.overlay_name} {signed_time(model.overlay_offset, 2)} s "
                                     f"on {ALIGN_SHORT.get(model.align, model.align)}")
        else:
            self._note = self._view_note
        b._retrace()
        b.update()
        b.overlay.update()
        self.update()

    def _say(self, text: str) -> None:
        self._note = text
        self.update()

    # ---- cursor ----

    def _cursor_moved(self, t: float) -> None:
        b = self.block
        lo, hi = b.span
        self.scrub.set((t - lo) / ((hi - lo) or 1.0))
        text = "LIVE" if self.view is not None and self.view.live else f"{signed_time(t)} s"
        # B is the reference that D dropped; the readouts say how far A has moved from it
        delta = f"Δ {signed_time(t - b.cursor_b, 2)} s" if b.cursor_b is not None else ""
        if (text, delta) != (self._cursor_text, self._delta_text):
            self._cursor_text, self._delta_text = text, delta
            self.update(QRect(0, 0, self.width(), 58))

    def _scrubbed(self, frac: float) -> None:
        lo, hi = self.block.span
        self.block.set_cursor(lo + frac * (hi - lo))

    def _jump(self, key: str) -> None:
        if key in self._jumps:
            self.block.set_cursor(self._jumps[key])

    def step_event(self, direction: int) -> None:
        """[ and ]: to the previous or next event of the replay session
        (markers, triggers, alarms, and what fell in the order of loss); past
        the last one, to the end of the drive."""
        b = self.block
        model = self.model
        if model is None:
            return
        target = model.jump(direction)
        if target is None:
            target = b.span[0] if direction < 0 else b.span[1]
        b.set_cursor(target, force=True)

    def toggle_cursor_b(self) -> None:
        """D: drop the second cursor where the first is, or take it away."""
        b = self.block
        b.set_cursor_b(None if b.cursor_b is not None else b.cursor)

    def toggle_play(self) -> None:
        if self.view is None or self.view.live:
            return
        self.playing = not self.playing
        if self.playing and self.block.cursor >= self.block.span[1] - 1e-6:
            self.block.set_cursor(self.block.span[0])
        self._play_t = -1.0
        self.play_btn.icon_path = paint.PAUSE if self.playing else paint.PLAY
        self.play_btn.update()

    # ---- frame ----

    def refresh(self, now: float) -> None:
        if self.view is not None and self.view.live:
            if now - self._live_t >= 0.1:
                self._live_t = now
                self._refresh_live(now)
            return
        if self.playing:
            if self._play_t >= 0:
                self.block.set_cursor(self.block.cursor + (now - self._play_t))
                if self.block.cursor >= self.block.span[1] - 1e-6:
                    self.toggle_play()
            self._play_t = now

    def key(self, event) -> bool:
        k, mods = event.key(), event.modifiers()
        shift, ctrl = bool(mods & Qt.ShiftModifier), bool(mods & Qt.ControlModifier)
        b = self.block
        text = event.text()
        if k in (Qt.Key_Left, Qt.Key_Right) and ctrl:      # Ctrl + arrows: pan a quarter of the window
            b.pan(-0.25 if k == Qt.Key_Left else 0.25)
        elif k == Qt.Key_Left:
            b.set_cursor(b.cursor - (1.0 if shift else 0.1))
        elif k == Qt.Key_Right:
            b.set_cursor(b.cursor + (1.0 if shift else 0.1))
        elif k == Qt.Key_Home:
            b.set_cursor(b.span[0])
        elif k == Qt.Key_End:
            b.set_cursor(b.span[1])
        elif text == "[":
            self.step_event(-1)
        elif text == "]":
            self.step_event(1)
        elif k == Qt.Key_P:
            self.toggle_play()
        elif text in ("+", "="):
            b.zoom(0.5)
        elif text in ("-", "_"):
            b.zoom(2.0)
        elif text == "0":                       # the whole drive
            b.show_all()
        elif k == Qt.Key_O:
            self.open_dialog()
        elif k == Qt.Key_L:
            self.go_live()
        elif k == Qt.Key_D:                     # second cursor for the delta readouts
            self.toggle_cursor_b()
        elif k == Qt.Key_C:
            self.clear_overlay() if shift else self.compare()
        elif k == Qt.Key_A:
            self.cycle_alignment()
        elif text in (",", "<"):
            self.nudge_overlay(-1.0 if text == "<" else -0.1)
        elif text in (".", ">"):
            self.nudge_overlay(1.0 if text == ">" else 0.1)
        else:
            return False
        return True

    # ---- layout / paint ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        side = 374
        sx = w - 8 - side
        left_w = sx - 16
        x = 8
        for btn in (self.prev_btn, self.play_btn, self.next_btn):
            btn.setGeometry(x, 8, 48, 44)
            x += 56
        self._file_x = x + 4
        self._tool_right = 8 + left_w - 206
        x = self._tool_right
        for btn in (self.live_btn, self.open_btn, self.compare_btn):
            bw = btn.sizeHint().width()
            x -= bw
            btn.setGeometry(x, 8, bw, 44)
            x -= 8
        self.block.setGeometry(8, 58, left_w, self.block.total_h)
        self.scrub.setGeometry(8, 58 + self.block.total_h + 4, left_w, 28)
        loss_h = min(292, max(132, h - 16 - 3 * 8 - 96 - 112 - 62))
        self.loss.setGeometry(sx, 8, side, loss_h)
        y = 8 + loss_h + 8
        self.held.setGeometry(sx, y, side, 96)
        y += 104
        self.reading.setGeometry(sx, y, side, 112)
        y += 120
        self._jump_y = y
        self.jump_seg.setGeometry(sx, y + 17, side, 44)

    def retheme(self) -> None:
        self.block.retheme()
        if self.model is not None:
            self.block._retrace()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        mono = T.font(15, 700, mono=True)
        note = T.font(13, 600)
        room = self.compare_btn.x() - self._file_x - 12
        # two lines when the name and the note do not fit side by side
        name_w, note_w = paint.text_w(mono, self._file), paint.text_w(note, self._note)
        stacked = bool(self._note) and name_w + 10 + note_w > room
        p.setFont(mono)
        p.setPen(T.fg)
        swatch = 16 if self._swatch else 0  # the colour of the second drive's traces
        stacked = stacked or bool(swatch)
        if stacked:
            p.drawText(QRectF(self._file_x, 9, room, 22), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mono, self._file, room))
            if swatch:
                p.fillRect(QRectF(self._file_x, 36, 10, 10), T.color("rule2"))
            p.setFont(note)
            p.setPen(T.fg2)
            p.drawText(QRectF(self._file_x + swatch, 31, room - swatch, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(note, self._note, room - swatch))
        else:
            p.drawText(QRectF(self._file_x, 8, room, 44), Qt.AlignVCenter | Qt.AlignLeft, self._file)
            p.setFont(note)
            p.setPen(T.fg2)
            p.drawText(QRectF(self._file_x + name_w + 10, 8, note_w + 4, 44),
                       Qt.AlignVCenter | Qt.AlignLeft, self._note)
        right = self.block.geometry().right()
        big = T.font(26, 800, mono=True)
        p.setFont(big)
        p.setPen(T.fg)
        cw = paint.text_w(big, self._cursor_text)
        if self._delta_text:
            # cursor A's time above, A minus B below, both right-aligned
            p.setFont(T.font(20, 800, mono=True))
            p.drawText(QRectF(self._tool_right + 6, 8, right - self._tool_right - 6, 24),
                       Qt.AlignVCenter | Qt.AlignRight, "A " + self._cursor_text)
            p.setFont(T.font(15, 700, mono=True))
            p.setPen(T.accent)
            p.drawText(QRectF(self._tool_right + 6, 31, right - self._tool_right - 6, 20),
                       Qt.AlignVCenter | Qt.AlignRight, "A−B " + self._delta_text[2:])
        else:
            p.drawText(QRectF(right - 180, 8, 180, 44), Qt.AlignVCenter | Qt.AlignRight,
                       self._cursor_text)
            paint.caption(p, right - cw - 78, 22, "Cursor")
        paint.caption(p, self.jump_seg.x(), self._jump_y, "Jump to")
        p.end()
