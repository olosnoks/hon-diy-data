"""F4, stall forensics replay (HANDOFF 5.4).

Stacked 2D lanes with one cursor: time-synced signals read better this way
than in 3D. All traces live in a single pyqtgraph ViewBox (one unit of y is
one pixel), so auto-downsampling and clip-to-view keep 1 kHz black-box data
cheap, and the cursor is an overlay that repaints only a thin strip.

With no file open the lanes follow the live bus (last 40 s).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFileDialog, QGraphicsItem, QWidget

from .. import adapters as ad
from .. import paint
from ..controls import Button, Card, Segment, TextCard
from ..theme import T

LABEL_W = 150
GROUPS = (
    ("ECU", "ECU · K-line", "series1", 40, ("rpm", "map", "tps", "o2", "inj")),
    ("BB", "Black box", "series2", 40, ("bbatt", "ecub", "fpv", "ig1", "tach")),
    ("STATE", "States", "fg", 20, ("fuel_pump", "closed_loop", "bbmark")),
)
HEAD_H, AXIS_H = 16, 22
LIVE_WINDOW = 40.0


class Lane:
    __slots__ = ("cid", "label", "token", "y", "h", "curve", "twin", "has_data", "value",
                 "flag", "lo", "hi")

    def __init__(self, cid, token, y, h):
        self.cid, self.token, self.y, self.h = cid, token, y, h
        self.label = cid.upper()
        self.curve = None
        self.twin = None            # live mode: second 1 px trace, one pixel lower
        self.has_data = False
        self.value = ""
        self.flag = False
        self.lo, self.hi = 0.0, 1.0


def signed_time(t: float, decimals: int = 1) -> str:
    sign = "−" if t < -0.5 * 10 ** -decimals else ("+" if t > 0 else "")
    return f"{sign}{abs(t):.{decimals}f}"


class LaneBlock(QWidget):
    """Label column + plot + overlay. Owns the cursor."""

    cursorMoved = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: ad.LogView | None = None
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

        self.gv = pg.GraphicsLayoutWidget(parent=self)
        self.gv.ci.setContentsMargins(0, 0, 0, 0)
        self.gv.setFrameShape(self.gv.Shape.NoFrame)
        self.gv.setFocusPolicy(Qt.NoFocus)
        self.vb = self.gv.addViewBox(enableMouse=False, enableMenu=False, invertY=True)
        self.vb.setDefaultPadding(0)
        for lane in self.lanes:
            step = lane.token == "fg"
            curve = pg.PlotDataItem(antialias=True, stepMode="right" if step else None)
            curve.setDownsampling(auto=True, method="peak")
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
        self._live_pens = False
        self.overlay = _Overlay(self)
        self.retheme()

    # ---- theme / geometry ----

    def retheme(self) -> None:
        self.gv.setBackground(T.panel)
        self._set_pens(self._live_pens)
        self.update()
        self.overlay.update()

    def _set_pens(self, live: bool) -> None:
        """A file view is stroked once, antialiased, 2 px, and cached. A live
        view redraws every lane ten times a second: there each trace is two
        1 px cosmetic lines a pixel apart, which the raster engine draws
        several times faster than one stroked 2 px line."""
        self._live_pens = live
        for lane in self.lanes:
            colour = T.color(lane.token)
            lane.curve.opts["antialias"] = not live
            lane.curve.setPen(pg.mkPen(colour, width=1 if live else 2))
            lane.twin.setPen(pg.mkPen(colour, width=1))
            if not live:
                lane.twin.setData([], [])

    def plot_w(self) -> int:
        return max(10, self.width() - LABEL_W - 2)

    def resizeEvent(self, _event) -> None:
        r = QRect(LABEL_W, 2, self.plot_w(), self.axis_y - 2)
        self.gv.setGeometry(r)
        self.overlay.setGeometry(LABEL_W, 0, self.plot_w(), self.total_h)
        self._range()

    def _range(self) -> None:
        self.vb.setRange(xRange=(self.t0, self.t1), yRange=(2, self.axis_y), padding=0)
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
        for lane in self.lanes:
            spec = view.specs.get(lane.cid)
            t = view.t.get(lane.cid)
            lane.has_data = spec is not None and t is not None and len(t) > 1
            if not lane.has_data:
                lane.curve.setData([], [])
                lane.twin.setData([], [])
                if spec is not None:
                    lane.label = f"{spec.label.upper()} {spec.units}".strip()
                continue
            v = view.data[lane.cid]
            lane.flag = spec.is_flag
            units = "" if spec.units.lower() == spec.label.lower() else spec.units
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
            f = np.clip((v - lo) / ((hi - lo) or 1.0), 0.0, 1.0)
            y = lane.y + lane.h - 4 - f * (lane.h - 8)
            lane.curve.setData(t, y)
            if live:
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
        if not keep_window:
            lo, hi = view.span()
            self.span = (lo, hi)
            if view.trigger_t is not None:
                self.t0, self.t1 = max(lo, -30.0), min(hi, 10.0)
                if self.t1 - self.t0 < 1.0:
                    self.t0, self.t1 = lo, hi
            else:
                self.t0, self.t1 = lo, hi
            self.cursor_b = None
            self._range()
        self.update()
        self.overlay.update()

    def values_at_cursor(self) -> None:
        view = self.view
        for lane in self.lanes:
            if view is None or not lane.has_data:
                lane.value = "---"
                continue
            lane.value = view.specs[lane.cid].format(view.value_at(lane.cid, self.cursor))

    # ---- cursor / zoom ----

    def set_cursor(self, t: float, emit: bool = True, force: bool = False) -> None:
        lo, hi = self.span
        t = min(hi, max(lo, t))
        if t == self.cursor and not force:
            return
        old_x = self.x_of(self.cursor)
        self.cursor = t
        if t < self.t0 or t > self.t1:          # keep the cursor in view
            width = self.t1 - self.t0
            self.t0 = min(max(lo, t - width / 2), max(lo, hi - width))
            self.t1 = self.t0 + width
            self._range()
        else:
            for x in (old_x, self.x_of(t)):
                self.overlay.update(QRect(int(x) - 12, 0, 24, self.total_h))
        self.values_at_cursor()
        self.update(QRect(0, 0, LABEL_W, self.total_h))
        if emit:
            self.cursorMoved.emit(t)

    def zoom(self, factor: float) -> None:
        lo, hi = self.span
        width = min(hi - lo, max(1.0, (self.t1 - self.t0) * factor))
        frac = (self.cursor - self.t0) / (self.t1 - self.t0)
        t0 = self.cursor - frac * width
        t0 = min(max(lo, t0), hi - width)
        self.t0, self.t1 = t0, t0 + width
        self._range()

    # ---- paint (label column and frame) ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        for y, text, token in self.heads:
            p.fillRect(QRectF(2, y, LABEL_W - 2, HEAD_H), T.sunk)
            _head_text(p, 8, y, text, token)
        name, mono = T.font(13, 800), T.font(15, 700, mono=True)
        for lane in self.lanes:
            p.fillRect(QRectF(2, lane.y + lane.h - 2, LABEL_W - 2, 2), T.sunk)
            p.setPen(T.fg)
            p.setFont(mono)
            vw = paint.text_w(mono, lane.value)
            p.drawText(QRectF(8, lane.y, LABEL_W - 16, lane.h - 2),
                       Qt.AlignVCenter | Qt.AlignRight, lane.value)
            p.setFont(name)
            p.drawText(QRectF(8, lane.y, LABEL_W - 16, lane.h - 2),
                       Qt.AlignVCenter | Qt.AlignLeft,
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

    def mousePressEvent(self, event) -> None:
        b = self.block
        t = b.t_of(event.position().x())
        if event.modifiers() & Qt.ShiftModifier:
            b.cursor_b = t
            self.update()
            b.cursorMoved.emit(b.cursor)
        else:
            b.set_cursor(t)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and not event.modifiers() & Qt.ShiftModifier:
            self.block.set_cursor(self.block.t_of(event.position().x()))

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
        # cursors
        if b.cursor_b is not None and b.t0 <= b.cursor_b <= b.t1:
            x = b.x_of(b.cursor_b)
            p.setPen(T.cpen(T.accent, 3, Qt.FlatCap, dash=(3.0, 2.0)))
            p.drawLine(QPointF(x, 2), QPointF(x, ay))
        if b.view is not None and not b.view.live and b.t0 <= b.cursor <= b.t1:
            x = b.x_of(b.cursor)
            p.fillRect(QRectF(x - 2, 2, 4, ay + 6), T.accent)
            p.setRenderHint(QPainter.Antialiasing, True)
            paint.shape(p, paint.UP, x - 8, ay + 8, 32, T.accent)
        p.end()


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
        self._events: list[float] = []
        self._file = ""
        self._note = ""
        self._cursor_text = ""
        self._delta_text = ""
        self.prev_btn = Button(icon=paint.PREV, parent=self)
        self.play_btn = Button(icon=paint.PLAY, parent=self)
        self.next_btn = Button(icon=paint.NEXT, parent=self)
        self.open_btn = Button("Open", "O", parent=self)
        self.live_btn = Button("Live", "L", parent=self)
        self.prev_btn.clicked.connect(lambda: self.step_event(-1))
        self.next_btn.clicked.connect(lambda: self.step_event(1))
        self.play_btn.clicked.connect(self.toggle_play)
        self.open_btn.clicked.connect(self.open_dialog)
        self.live_btn.clicked.connect(self.go_live)
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

    def open_dialog(self) -> None:
        recent = self.session.logs.recent()
        start = str(recent[0].parent) if recent else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open log", start, "hondalog CSV (*.csv)")
        if path:
            self.open(path)

    def show_view(self, view: ad.LogView) -> None:
        self.view = view
        self.playing = False
        self.play_btn.icon_path = paint.PLAY
        self.forensics = self.session.logs.analyze(view) if not view.live else ad.Forensics()
        self.block.set_view(view)
        self._file, self._note = view.title, view.note
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
        ev = {round(t, 3) for t, _ in view.markers} | {round(r.t, 3) for r in self.forensics.loss}
        if view.trigger_t is not None:
            ev.add(view.trigger_t)
        self._events = sorted(ev)
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
        view = ad.LogView(path=None, title="live", note="last 40 s · O opens a file",
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
        self.block.cursor = 0.0
        self.block.values_at_cursor()
        self.block.update()
        self._cursor_moved(0.0)

    # ---- cursor ----

    def _cursor_moved(self, t: float) -> None:
        b = self.block
        lo, hi = b.span
        self.scrub.set((t - lo) / ((hi - lo) or 1.0))
        text = "LIVE" if self.view is not None and self.view.live else f"{signed_time(t)} s"
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
        t = self.block.cursor
        if direction < 0:
            earlier = [e for e in self._events if e < t - 0.05]
            self.block.set_cursor(earlier[-1] if earlier else self.block.span[0])
        else:
            later = [e for e in self._events if e > t + 0.05]
            self.block.set_cursor(later[0] if later else self.block.span[1])

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
        k, shift = event.key(), bool(event.modifiers() & Qt.ShiftModifier)
        b = self.block
        text = event.text()
        if k == Qt.Key_Left:
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
        elif k == Qt.Key_O:
            self.open_dialog()
        elif k == Qt.Key_L:
            self.go_live()
        elif k == Qt.Key_D:                    # second cursor for a delta readout
            b.cursor_b = None if b.cursor_b is not None else b.cursor
            b.overlay.update()
            self._cursor_moved(b.cursor)
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
        lw = self.live_btn.sizeHint().width()
        ow = self.open_btn.sizeHint().width()
        self._tool_right = 8 + left_w - 206
        self.live_btn.setGeometry(self._tool_right - lw, 8, lw, 44)
        self.open_btn.setGeometry(self._tool_right - lw - 8 - ow, 8, ow, 44)
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

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        mono = T.font(15, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        room = self.open_btn.x() - self._file_x - 12
        note = T.font(13, 600)
        note_w = min(paint.text_w(note, self._note), room * 0.45)
        name = paint.elide(mono, self._file, room - note_w - 10)
        p.drawText(QRectF(self._file_x, 8, room, 44), Qt.AlignVCenter | Qt.AlignLeft, name)
        nx = self._file_x + paint.text_w(mono, name) + 10
        p.setFont(note)
        p.setPen(T.fg2)
        p.drawText(QRectF(nx, 8, note_w + 4, 44), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(note, self._note, note_w + 2))
        right = self.block.geometry().right()
        big = T.font(26, 800, mono=True)
        p.setFont(big)
        p.setPen(T.fg)
        p.drawText(QRectF(right - 180, 8, 180, 44), Qt.AlignVCenter | Qt.AlignRight,
                   self._cursor_text)
        cw = paint.text_w(big, self._cursor_text)
        if self._delta_text:
            p.setFont(T.font(13, 700, mono=True))
            p.setPen(T.accent)
            p.drawText(QRectF(self._tool_right + 6, 8, right - cw - self._tool_right - 16, 44),
                       Qt.AlignVCenter | Qt.AlignRight, self._delta_text)
        else:
            paint.caption(p, right - cw - 78, 22, "Cursor")
        paint.caption(p, self.jump_seg.x(), self._jump_y, "Jump to")
        p.end()
