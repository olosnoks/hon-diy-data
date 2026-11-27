"""F4 Replay: a recording played back as if it were live, on one cursor.

Layout (Replay.dc.html): a transport row (play, 0.5× / 1× / 4×, the time at
the cursor, a scrubber over the whole drive with a tick per event, Open and
Compare); on the left the GPS map (track coloured by speed or a channel, the
car at the cursor, a pin per event), a g-g plot with a 5 s trail and the pull
under the cursor (torque and hp from the acceleration); in the middle the
lanes (rpm with the idle target, speed, throttle, MAP, lateral g by default;
any channel can be added) over eight calculated readouts (power, torque,
gear, acceleration, lateral g, grade, fuel flow, rpm − target); on the right
every sensor at the cursor, grouped like Live (a click adds its lane).

The lanes are drawn from ``hondalog.analysis.ReplaySession`` through
``ui/replaymodel.ReplayModel``: one min/max envelope per lane for the visible
window at the plot's pixel width, two cursors A and B with per-lane deltas,
zoom and pan, [ and ] from event to event (``hondalog.analysis.events``), and
a second drive laid over the first. The calculated channels come from
``hondalog.analysis.derived`` (motion and power: gear by the coupling test,
acceleration from vss, lateral g from the GPS heading rate with its 1.7 s lag,
the modelled grade, fuel from the injector pulse).

With no file open the lanes follow the live bus (last 40 s).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QMenu, QWidget

from ...analysis import derived as dv
from ...analysis import power
from ...analysis.motion import Car, gear_name
from ...geo import proj
from ...geo.track import Track
from .. import adapters as ad
from .. import paint
from ..bus import ChannelSpec
from ..controls import Button, Card, Segment
from ..mapview import MapCanvas, Pin
from ..replaymodel import ALIGN_SHORT, ALIGNMENTS, ReplayModel, analyse, view_epoch
from ..theme import T
from ..units import U

LABEL_W = 132
AXIS_H = 22
LIVE_WINDOW = 40.0
MAX_LANES = 8
DEFAULT_LANES = ("rpm", "vss", "tps", "map", "lat_g")
#: used to fill the default lanes when a view lacks some of them (a stall capture)
FALLBACK_LANES = ("fpv", "bbatt", "ecub", "inj", "o2", "ig1", "tach")
LANE_TOKENS = {"rpm": "series1", "vss": "series4", "tps": "series5", "map": "series3", "lat_g": "series2"}
CYCLE = ("series1", "series2", "series3", "series4", "series5")
SPEEDS = (("0.5", "0.5×"), ("1", "1×"), ("4", "4×"))
#: event kind -> (token of its tick and pin colour, tick height)
EVENT_STYLE = {"stall": ("alarm", 24), "near_stall": ("warn", 22), "dropout": ("series3", 18),
               "key_off": ("rule", 18), "vtec": ("ok", 18), "harsh_accel": ("series5", 16),
               "harsh_brake": ("series5", 16), "corner": ("series5", 16), "knock": ("unverified", 12),
               "mark": ("fg", 18)}
EVENT_PIN = {"stall": "stall", "near_stall": "alarm", "dropout": "capture", "key_off": "start", "mark": "marker",
             "harsh_brake": "capture", "harsh_accel": "capture", "corner": "capture"}
#: the sensors panel, grouped like Live (anything else goes under OTHER)
SENSOR_GROUPS = (
    ("ENGINE", ("rpm", "map", "tps", "load_ecu", "load", "ect", "iat", "baro", "vss")),
    ("IDLE CONTROL", ("idle_target", "iac_cmd", "iacv", "rpm_err")),
    ("FUEL", ("stft", "ltft", "total_trim", "o2", "o2_2", "inj", "inj_duty", "fuel_flow", "fuel_status",
              "closed_loop", "afr_est")),
    ("IGNITION", ("ign", "ign_limit", "knock", "vtec")),
    ("ELECTRICAL", ("batt", "alt_fr", "eld", "batt_min_5s", "bbatt", "ecub", "ig1", "fpv")),
    ("SWITCHES", ("brake", "ac_sw", "ac_clutch", "fan", "fuel_pump", "mil")),
    ("CYD · BLACK BOX", ("gps_speed", "gps_heading", "gps_sats", "gps_hdop", "gps_fix", "tach", "igt", "injp",
                         "bbmark")),
    ("CALCULATED", tuple(dv.CHANNELS)),
)
#: never listed or drawn: the position itself stays on the map
HIDDEN = ("gps_lat", "gps_lon", "gps_utc", "link")
FUEL_SPEC = ChannelSpec("fuel_lph", "Fuel flow", "L/h", 1, 0, 20, source="CALC")
COLOUR_BY = (("speed", "speed"), ("rpm", "rpm"), ("tps", "throttle"), ("lat_g", "lateral g"), ("power", "power"))


def derived_spec(cid: str) -> ChannelSpec:
    label, units, dec, lo, hi = dv.CHANNELS[cid]
    return ChannelSpec(cid, label, units, dec, lo, hi, source="CALC", verified=False, signed=cid in ("lat_g", "long_g", "grade", "rpm_err"))


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


def _num(x: float, fmt: str, unit: str = "", signed: bool = False) -> str:
    if x is None or x != x:
        return "—"
    text = format(abs(x) if signed else x, fmt)
    if signed:
        text = ("−" if x < 0 else "+") + text
    return f"{text} {unit}".strip()


# ---- the lanes --------------------------------------------------------------

class Lane:
    __slots__ = ("cid", "label", "token", "y", "h", "curve", "twin", "over", "extra", "has_data", "value",
                 "delta", "flag", "lo", "hi")

    def __init__(self, cid, token):
        self.cid, self.token = cid, token
        self.y, self.h = 0.0, 40.0
        self.label = cid.upper()
        self.curve = self.twin = self.over = self.extra = None
        self.has_data = False
        self.value = ""             # at cursor A
        self.delta = ""             # A minus B, when cursor B is set
        self.flag = False
        self.lo, self.hi = 0.0, 1.0


class LaneBlock(QWidget):
    """Label column + plot + overlay. Shows what the ReplayModel holds: the
    window, the two cursors, the traces. The lanes share the height."""

    cursorMoved = Signal(float)
    windowChanged = Signal()
    laneMenu = Signal(str, object)          # lane id, global position
    laneClosed = Signal(str)                # the lane's × was clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: ad.LogView | None = None
        self.model: ReplayModel | None = None       # None while the lanes are live
        self.lanes: list[Lane] = []
        self.specs: dict[str, ChannelSpec] = {}     # derived channels' specs
        self.events: list = []                      # DriveEvent on view time
        self.axis_y = 100
        self.total_h = 122
        self.t0, self.t1 = -30.0, 10.0
        self.span = (-30.0, 10.0)
        self.cursor = 0.0
        self.cursor_b: float | None = None
        self.marks: list = []               # (t, text)
        self.trigger: float | None = None
        self.axis_title = "s"
        self.epoch: float | None = None     # wall clock of view time 0
        self.traces = 0                     # how often the lanes were redrawn from the model

        self.gv = pg.GraphicsLayoutWidget(parent=self)
        self.gv.ci.setContentsMargins(0, 0, 0, 0)
        self.gv.setFrameShape(self.gv.Shape.NoFrame)
        self.gv.setFocusPolicy(Qt.NoFocus)
        self.vb = self.gv.addViewBox(enableMouse=False, enableMenu=False, invertY=True)
        self.vb.setDefaultPadding(0)
        self._live_pens: bool | None = None
        self.overlay = _Overlay(self)
        self.set_lanes(DEFAULT_LANES)
        self.retheme()

    # ---- lanes ----

    def set_lanes(self, cids) -> None:
        for lane in self.lanes:
            for item in (lane.curve, lane.twin, lane.over, lane.extra):
                if item is not None:
                    self.vb.removeItem(item)
        self.lanes = []
        used: set[str] = set()
        for cid in list(dict.fromkeys(cids))[:MAX_LANES]:
            token = LANE_TOKENS.get(cid) or next((c for c in CYCLE if c not in used), "series1")
            used.add(token)
            lane = Lane(cid, token)
            over = pg.PlotDataItem(antialias=True, connect="finite")
            over.setClipToView(True)
            self.vb.addItem(over)           # first: the second drive is drawn under the first
            lane.over = over
            if cid == "rpm":
                lane.extra = pg.PlotDataItem(antialias=True, connect="finite")
                lane.extra.setClipToView(True)
                self.vb.addItem(lane.extra)
            curve = pg.PlotDataItem(antialias=True)
            curve.setClipToView(True)
            # Rendered once into a pixmap: moving the cursor only blits.
            curve.curve.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            self.vb.addItem(curve)
            lane.curve = curve
            twin = pg.PlotDataItem(antialias=False)
            twin.setDownsampling(auto=True, method="peak")
            twin.setClipToView(True)
            self.vb.addItem(twin)
            lane.twin = twin
            self.lanes.append(lane)
        self._live_pens = None
        self._set_pens(bool(self.view is not None and self.view.live))
        self._layout()
        if self.view is not None:
            self.set_view(self.view, keep_window=True)

    def lane_ids(self) -> list[str]:
        return [lane.cid for lane in self.lanes]

    def add_lane(self, cid: str) -> bool:
        if cid in self.lane_ids() or cid in HIDDEN:
            return False
        ids = self.lane_ids()
        if len(ids) >= MAX_LANES:
            ids = ids[:-1]
        self.set_lanes(ids + [cid])
        return True

    def remove_lane(self, cid: str) -> bool:
        ids = self.lane_ids()
        if cid not in ids or len(ids) <= 1:
            return False
        self.set_lanes([x for x in ids if x != cid])
        return True

    def _layout(self) -> None:
        n = max(1, len(self.lanes))
        avail = max(n * 30, self.height() - AXIS_H - 4)
        h = avail / n
        y = 2.0
        for lane in self.lanes:
            lane.y, lane.h = y, h
            y += h
        self.axis_y = int(round(y))
        self.total_h = self.axis_y + AXIS_H + 2

    # ---- theme / geometry ----

    def retheme(self) -> None:
        self.gv.setBackground(T.panel)
        self._set_pens(bool(self._live_pens))
        self.update()
        self.overlay.update()

    def _set_pens(self, live: bool) -> None:
        """A file view is stroked once, antialiased, 2 px, and cached; a live
        view redraws ten times a second: two 1 px lines a pixel apart there."""
        self._live_pens = live
        for lane in self.lanes:
            colour = T.color(lane.token)
            lane.curve.opts["antialias"] = not live
            lane.curve.opts["connect"] = "all" if live else "finite"
            lane.curve.setDownsampling(auto=live, method="peak")
            lane.curve.setPen(pg.mkPen(colour, width=1 if live else 2))
            lane.twin.setPen(pg.mkPen(colour, width=1))
            lane.over.setPen(pg.mkPen(T.color("rule2"), width=2))
            if lane.extra is not None:
                lane.extra.setPen(pg.mkPen(T.color("fg2"), width=1.5, style=Qt.DashLine))
            if not live:
                lane.twin.setData([], [])
            else:
                lane.over.setData([], [])

    def plot_w(self) -> int:
        return max(10, self.width() - LABEL_W - 2)

    def resizeEvent(self, _event) -> None:
        self._layout()
        self.gv.setGeometry(QRect(LABEL_W, 2, self.plot_w(), max(4, self.axis_y - 2)))
        self.overlay.setGeometry(LABEL_W, 0, self.plot_w(), self.height())
        self._range()

    def _range(self) -> None:
        self.vb.setRange(xRange=(self.t0, self.t1), yRange=(2, self.axis_y), padding=0)
        if self.model is not None:
            self._retrace()
        self.overlay.update()

    def x_of(self, t: float) -> float:
        return (t - self.t0) / ((self.t1 - self.t0) or 1.0) * self.plot_w()

    def t_of(self, x: float) -> float:
        return self.t0 + x / self.plot_w() * (self.t1 - self.t0)

    # ---- data ----

    def spec_of(self, cid: str) -> ChannelSpec | None:
        if self.view is not None and cid in self.view.specs:
            return self.view.specs[cid]
        if cid in self.specs:
            return self.specs[cid]
        return derived_spec(cid) if cid in dv.CHANNELS else None

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
        model = self.model
        for lane in self.lanes:
            spec = self.spec_of(lane.cid)
            if model is not None:
                lane.has_data = spec is not None and model.has(lane.cid) and (
                    lane.cid in model.derived or len(view.t.get(lane.cid, ())) > 1)
            else:
                t = view.t.get(lane.cid)
                lane.has_data = spec is not None and t is not None and len(t) > 1
            units = U.spec(spec).units if spec is not None else ""
            name = spec.label if spec is not None else lane.cid
            units = "" if units.lower() == name.lower() else units
            lane.label = f"{name.upper()} {units}".strip()
            if not lane.has_data:
                for item in (lane.curve, lane.twin, lane.over, lane.extra):
                    if item is not None:
                        item.setData([], [])
                continue
            lane.flag = spec.is_flag
            if lane.cid in (model.derived if model is not None else {}):
                v = model.derived[lane.cid][1]
            else:
                v = np.asarray(view.data[lane.cid], dtype=float)
            if spec.is_flag:
                lo, hi = 0.0, 1.0
            elif live:
                lo, hi = spec.lo, spec.hi
            else:
                fin = v[np.isfinite(v)]
                lo, hi = (float(fin.min()), float(fin.max())) if len(fin) else (spec.lo, spec.hi)
                if lane.cid in ("lat_g", "long_g", "grade", "rpm_err"):
                    m = max(abs(lo), abs(hi), (spec.hi - spec.lo) / 6)
                    lo, hi = -m, m
                pad = max(0.1 * (hi - lo), 0.04 * (spec.hi - spec.lo))
                lo, hi = lo - pad, hi + pad
            lane.lo, lane.hi = lo, hi
            if live:
                y = self._lane_y(lane, v)
                lane.curve.setData(view.t[lane.cid], y)
                lane.twin.setData(view.t[lane.cid], y + 1.0)
        self.marks = list(view.markers)
        self.trigger = view.trigger_t
        self.epoch = None if live else view_epoch(view)
        self.axis_title = ("s from now" if live else "s from stall" if view.trigger_t is not None
                           else "clock" if self.epoch is not None else "s from start")
        if model is not None and not keep_window:
            lo, hi = self.span = model.span
            if view.trigger_t is not None and min(hi, 10.0) - max(lo, -30.0) >= 1.0:
                model.set_window(max(lo, -30.0), min(hi, 10.0))     # the stall window
            else:
                model.set_window(lo, min(hi, lo + 60.0))             # a minute: replay is a playback
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
        top = 18.0 if lane.h >= 48 else 6.0                 # room for the lane's name inside the plot
        return lane.y + lane.h - 4 - f * (lane.h - 4 - top)

    def _retrace(self) -> None:
        """The traces for the window showing now: one envelope per lane, at
        one column per plot pixel."""
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
            if lane.extra is not None:
                if model.has("idle_target"):
                    tx, tv, _ = model.trace("idle_target", columns)
                    lane.extra.setData(tx, self._lane_y(lane, tv))
                else:
                    lane.extra.setData([], [])

    def values_at_cursor(self) -> None:
        """The label column: each lane's value at cursor A and, with B set,
        how much it differs from the value at B."""
        view, model = self.view, self.model
        rows = model.readouts([lane.cid for lane in self.lanes if lane.has_data], full=False) if model else {}
        for lane in self.lanes:
            lane.delta = ""
            spec = self.spec_of(lane.cid)
            if view is None or not lane.has_data or spec is None:
                lane.value = "---"
                continue
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
                self.overlay.update(QRect(int(x) - 14, 0, 28, self.height()))
        self.values_at_cursor()
        self.update(QRect(0, 0, LABEL_W, self.height()))
        if emit:
            self.cursorMoved.emit(t)

    def set_cursor_b(self, t: float | None) -> None:
        """Place, move or (None) remove cursor B, the reference."""
        model = self.model
        if model is None:
            return
        if t is None:
            model.clear_cursor_b()
            self.cursor_b = None
        else:
            self.cursor_b = model.set_cursor(t, "b")
        self.values_at_cursor()
        self.update(QRect(0, 0, LABEL_W, self.height()))
        self.overlay.update()
        self.cursorMoved.emit(self.cursor)

    def sync_window(self) -> None:
        if self.model is not None:
            self.t0, self.t1 = self.model.window
            self._range()
            self.windowChanged.emit()

    def zoom(self, factor: float, around: float | None = None) -> None:
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

    def time_text(self, t: float, decimals: int = 1) -> str:
        if self.epoch is not None and self.trigger is None:
            stamp = datetime.fromtimestamp(self.epoch + t)
            text = stamp.strftime("%H:%M:%S")
            return text + (f".{int(stamp.microsecond / 10 ** (6 - decimals)):0{decimals}d}" if decimals else "")
        return f"{signed_time(t, decimals)} s"

    # ---- paint (label column and frame) ----

    @staticmethod
    def close_rect(lane) -> QRectF:
        """The lane's × (top right of its label), as in the web app."""
        return QRectF(LABEL_W - 22, lane.y + 3, 18, 18)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if pos.x() >= LABEL_W:
            return
        lane = self.lane_at(pos.y())
        if lane is None:
            return
        if event.button() == Qt.RightButton:
            self.laneMenu.emit(lane.cid, event.globalPosition().toPoint())
        elif event.button() == Qt.LeftButton and len(self.lanes) > 1 and self.close_rect(lane).contains(pos):
            self.laneClosed.emit(lane.cid)

    def lane_at(self, y: float) -> Lane | None:
        return next((lane for lane in self.lanes if lane.y <= y < lane.y + lane.h), None)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        name, mono, small = T.font(12, 800), T.font(15, 700, mono=True), T.font(12, 700, mono=True)
        for lane in self.lanes:
            p.fillRect(QRectF(2, lane.y + lane.h - 1, w - 4, 1), T.line)
            p.fillRect(QRectF(8, lane.y + 6, 4, min(20.0, lane.h - 10)), T.color(lane.token))
            p.setFont(name)
            p.setPen(T.fg)
            p.drawText(QRectF(18, lane.y + 3, LABEL_W - 44, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(name, lane.label, LABEL_W - 44))
            if len(self.lanes) > 1:             # × closes the lane (the sensors panel adds it back)
                p.setFont(T.font(15, 700))
                p.setPen(T.fg2)
                p.drawText(self.close_rect(lane), Qt.AlignCenter, "×")
            p.setFont(mono)
            p.drawText(QRectF(18, lane.y + 21, LABEL_W - 24, 20), Qt.AlignVCenter | Qt.AlignLeft, lane.value)
            if lane.delta and lane.h >= 60:
                p.setFont(small)
                p.setPen(T.accent)
                p.drawText(QRectF(18, lane.y + 41, LABEL_W - 24, 16), Qt.AlignVCenter | Qt.AlignLeft,
                           paint.elide(small, lane.delta, LABEL_W - 24))
        p.setFont(T.font(12, 700))
        p.setPen(T.fg2)
        p.drawText(QRectF(8, self.axis_y, LABEL_W, AXIS_H), Qt.AlignVCenter | Qt.AlignLeft, self.axis_title)
        paint.frame(p, QRectF(0, 0, w, self.height()), 1, T.line)
        p.end()


class _Overlay(QWidget):
    """Everything drawn over the traces: events, markers, cursors, time axis."""

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
        ay = b.axis_y
        for lane in b.lanes:
            if not lane.has_data:
                p.fillRect(QRectF(0, lane.y, w, lane.h - 1), paint.hatch_brush())
            p.fillRect(QRectF(0, lane.y + lane.h - 1, w, 1), T.line)
        # events: a band on the rpm lane for a near-stall, a tick for the rest
        tag = T.font(12, 700)
        rpm_lane = next((lane for lane in b.lanes if lane.cid == "rpm"), b.lanes[0] if b.lanes else None)
        for e in b.events:
            if e.t_end < b.t0 or e.t > b.t1:
                continue
            token = EVENT_STYLE.get(e.kind, ("fg", 16))[0]
            colour = T.color(token)
            x0, x1 = b.x_of(e.t), b.x_of(max(e.t_end, e.t))
            if e.kind in ("near_stall", "dropout", "stall") and rpm_lane is not None:
                band = QColor(colour)
                band.setAlpha(46)
                lx = min(x0, x1 - 1) - 8 if e.kind == "near_stall" else x0
                rx = max(x1, x0 + 1) + 8 if e.kind == "near_stall" else max(x1, x0 + 3)
                p.fillRect(QRectF(lx, rpm_lane.y, rx - lx, rpm_lane.h - 1), band)
                if e.kind == "near_stall":
                    p.setFont(tag)
                    p.setPen(colour)
                    text = f"dip {e.value:,.0f}"
                    tw = paint.text_w(tag, text)
                    p.drawText(QPointF(min(max(2.0, lx - tw - 4), w - tw - 2), rpm_lane.y + rpm_lane.h - 6), text)
            else:
                p.fillRect(QRectF(x0 - 1.5, 0, 3, 8), colour)
        # time axis
        p.fillRect(QRectF(0, ay, w, AXIS_H), T.panel)
        span = b.t1 - b.t0
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
                     if s / (span or 1.0) * w >= 96), 600)
        mono = T.font(12, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        k = int(np.ceil(b.t0 / step - 1e-9))
        while k * step <= b.t1 + 1e-9:
            t = k * step
            x = b.x_of(t)
            if b.epoch is not None and b.trigger is None:
                text = b.time_text(t, 1 if step < 1 else 0)
            else:
                text = signed_time(t, 1 if step < 1 else 0) if b.trigger is not None or t < 0 else f"{t:g}"
            tw = paint.text_w(mono, text)
            p.fillRect(QRectF(x, ay, 1, 4), T.rule)
            p.drawText(QPointF(min(max(2.0, x - tw / 2), w - tw - 2), ay + 16), text)
            k += 1
        # markers
        tag = T.font(12, 800, spacing=0.04)
        for t, text in b.marks:
            if b.t0 <= t <= b.t1 and not str(text).upper().startswith(("ALARM", "CLEAR")):
                x = b.x_of(t)
                p.setPen(T.cpen(T.fg, 2, Qt.FlatCap, dash=(2.0, 2.0)))
                p.drawLine(QPointF(x, 10), QPointF(x, ay))
                label = str(text)[:18]
                tw = paint.text_w(tag, label) + 12
                tagr = QRectF(x - 1, 0, tw, 15)        # a sunk tag outlined in fg: no light fills
                paint.frame(p, tagr, 1, T.fg, T.sunk)
                p.setFont(tag)
                p.setPen(T.fg)
                p.drawText(tagr, Qt.AlignCenter, label)
        if b.trigger is not None and b.t0 <= b.trigger <= b.t1:
            x = b.x_of(b.trigger)
            p.fillRect(QRectF(x - 2, 0, 4, ay), T.alarm)
            tw = paint.text_w(tag, "STALL") + 14
            p.fillRect(QRectF(x - 2, 0, tw, 15), T.alarm)
            p.setFont(tag)
            p.setPen(T.on_alarm)
            p.drawText(QRectF(x - 2, 0, tw, 15), Qt.AlignCenter, "STALL")
        # cursors: A solid in the live-point colour, B dashed; lettered once there are two
        both = b.cursor_b is not None
        letter = T.font(12, 800)
        if both and b.t0 <= b.cursor_b <= b.t1:
            x = b.x_of(b.cursor_b)
            p.setPen(T.cpen(T.accent, 2, Qt.FlatCap, dash=(3.0, 2.0)))
            p.drawLine(QPointF(x, 0), QPointF(x, ay))
            self._tag(p, letter, x, ay + 3, "B", T.accent, T.on_accent)
        if b.view is not None and not b.view.live and b.t0 <= b.cursor <= b.t1:
            x = b.x_of(b.cursor)
            p.fillRect(QRectF(x - 1, 0, 2.5, ay), T.live)
            if both:
                self._tag(p, letter, x, ay + 3, "A", T.live, T.bg)
        p.end()

    def _tag(self, p: QPainter, font, x: float, y: float, text: str, ground, ink) -> None:
        r = QRectF(min(max(0.0, x - 8), self.width() - 16.0), y, 16, 16)
        p.fillRect(r, ground)
        p.setFont(font)
        p.setPen(ink)
        p.drawText(r, Qt.AlignCenter, text)


# ---- the transport's scrubber ----------------------------------------------

class Scrubber(QWidget):
    """The whole drive: what has played, a tick per event, the cursor."""

    moved = Signal(float)                   # 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frac = 0.0
        self.ticks: list[tuple[float, str]] = []     # (0..1, event kind)
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, frac: float) -> None:
        frac = min(1.0, max(0.0, frac))
        if abs(frac - self.frac) > 1e-4:
            self.frac = frac
            self.update()

    def set_ticks(self, ticks) -> None:
        self.ticks = list(ticks)
        self.update()

    def _emit(self, event) -> None:
        x0, x1 = 4.0, self.width() - 4.0
        self.moved.emit(min(1.0, max(0.0, (event.position().x() - x0) / ((x1 - x0) or 1.0))))

    mousePressEvent = _emit

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._emit(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        x0, x1 = 4.0, self.width() - 4.0
        p.fillRect(QRectF(x0, 13, x1 - x0, 4), T.line)
        p.fillRect(QRectF(x0, 13, (x1 - x0) * self.frac, 4), T.rule)
        for frac, kind in self.ticks:
            token, h = EVENT_STYLE.get(kind, ("fg", 16))
            if kind == "knock":
                continue
            x = x0 + frac * (x1 - x0)
            p.fillRect(QRectF(x - 2, 15 - h / 2, 4, h), T.color(token))
        x = x0 + self.frac * (x1 - x0)
        p.fillRect(QRectF(x - 1.5, 0, 3, 30), T.live)
        p.end()


# ---- the left column: g-g plot and the pull --------------------------------

class GGPlot(QWidget):
    """Lateral against longitudinal g: the point now and the last 5 s."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.trail_x = np.zeros(0)
        self.trail_y = np.zeros(0)
        self.now: tuple[float, float] | None = None
        self.full_g = 0.5

    def set_points(self, x, y, now) -> None:
        self.trail_x, self.trail_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        self.now = now
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        paint.frame(p, r, 1, T.line, T.panel)
        cx, cy = r.center().x(), r.center().y() + 8
        rad = max(10.0, min(r.width(), r.height() - 30) / 2 - 10)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(T.cpen(T.line, 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.drawEllipse(QPointF(cx, cy), rad / 2, rad / 2)
        p.drawLine(QPointF(cx - rad, cy), QPointF(cx + rad, cy))
        p.drawLine(QPointF(cx, cy - rad), QPointF(cx, cy + rad))
        scale = rad / self.full_g
        if len(self.trail_x) > 1:
            pts = [QPointF(cx + float(np.clip(x, -0.7, 0.7)) * scale, cy - float(np.clip(y, -0.7, 0.7)) * scale)
                   for x, y in zip(self.trail_x, self.trail_y)]
            p.setPen(T.cpen(T.rule, 2))
            p.drawPolyline(QPolygonF(pts))
        if self.now is not None and all(v == v for v in self.now):
            x, y = self.now
            p.setPen(T.cpen(T.bg, 2))
            p.setBrush(T.live)
            p.drawEllipse(QPointF(cx + float(np.clip(x, -0.7, 0.7)) * scale,
                                  cy - float(np.clip(y, -0.7, 0.7)) * scale), 6, 6)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setFont(T.font(12, 700))
        p.setPen(T.fg2)
        p.drawText(QPointF(8, 17), "g-g · 5 s trail")
        p.drawText(QPointF(cx + rad - 34, cy - 4), f"{self.full_g:.1f} g")
        p.drawText(QPointF(cx + 4, cy - rad - 4), "accel")
        p.end()


class PullCard(Card):
    """The pull (or the run in one gear) under the cursor."""

    def __init__(self, parent=None):
        super().__init__("This pull", border=1, parent=parent)
        self.line1 = "No pull at the cursor"
        self.big = "—"
        self.line2 = "a run in one gear with the throttle open"
        self.power_btn = Button("Open in Power", "F5", height=34, parent=self)
        self.power_btn.px = 13

    def set_text(self, line1: str, big: str, line2: str) -> None:
        if (line1, big, line2) != (self.line1, self.big, self.line2):
            self.line1, self.big, self.line2 = line1, big, line2
            self.update()

    def resizeEvent(self, _event) -> None:
        self.power_btn.setGeometry(10, self.height() - 44, self.width() - 20, 34)

    def ground(self):
        return T.bg

    def paint_body(self, p, x, y, w, h) -> None:
        f1 = T.font(13, 600)
        lines = paint.wrap(f1, self.line1, w)[:2]
        p.setFont(f1)
        p.setPen(T.fg2)
        for i, line in enumerate(lines):
            p.drawText(QPointF(x, y + 14 + 17 * i), line)
        yy = y + 14 + 17 * len(lines) + 6
        big = T.font(18, 700, mono=True)
        p.setFont(big)
        p.setPen(T.fg)
        p.drawText(QPointF(x, yy + 18), paint.elide(big, self.big, w))
        p.setFont(T.font(12, 600))
        p.setPen(T.fg2)
        p.drawText(QPointF(x, yy + 38), paint.elide(T.font(12, 600), self.line2, w))


class ReadoutGrid(QWidget):
    """Eight calculated readouts, four to a row."""

    LABELS = ("Power now", "Torque now", "Gear", "Accel", "Lateral", "Grade (est.)", "Fuel flow", "RPM − target")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = ["—"] * len(self.LABELS)
        self.setFixedHeight(2 * 50 + 6)

    def set_values(self, values) -> None:
        values = list(values)
        if values != self.values:
            self.values = values
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        cols, gap = 4, 6
        cw = (self.width() - gap * (cols - 1)) / cols
        label, mono = T.font(12, 700), T.font(17, 700, mono=True)
        for i, (name, value) in enumerate(zip(self.LABELS, self.values)):
            r = QRectF((i % cols) * (cw + gap), (i // cols) * (50 + gap), cw, 50)
            paint.frame(p, r, 1, T.line, T.panel)
            p.setFont(label)
            p.setPen(T.fg2)
            p.drawText(QPointF(r.x() + 8, r.y() + 17), paint.elide(label, name, cw - 16))
            p.setFont(mono)
            p.setPen(T.fg)
            p.drawText(QPointF(r.x() + 8, r.y() + 41), paint.elide(mono, value, cw - 16))
        p.end()


class SensorPanel(QWidget):
    """Every sensor at the cursor, in Live's groups; a click adds its lane."""

    laneRequested = Signal(str)
    ROW_H = 21

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list[tuple[str, list[tuple[str, str, str]]]] = []     # title, [(cid, label, value)]
        self.lanes: set[str] = set()
        self.scroll = 0
        self._hits: list[tuple[QRectF, str]] = []
        self.setCursor(Qt.PointingHandCursor)

    def set_groups(self, groups, lanes) -> None:
        self.groups, self.lanes = groups, set(lanes)
        self.update()

    def wheelEvent(self, event) -> None:
        self.scroll = max(0, self.scroll - int(event.angleDelta().y() / 2))
        self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for r, cid in self._hits:
            if r.contains(pos):
                self.laneRequested.emit(cid)
                return

    def content_h(self) -> int:
        h = 34
        for _title, rows in self.groups:
            h += 22 + ((len(rows) + 1) // 2) * self.ROW_H + 6
        return h

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        paint.frame(p, QRectF(self.rect()), 1, T.line, T.bg)
        p.setFont(T.font(12, 800, spacing=0.06))
        p.setPen(T.fg)
        p.drawText(QPointF(10, 20), "ALL SENSORS AT CURSOR")
        hint = T.font(12, 600)
        p.setFont(hint)
        p.setPen(T.fg2)
        tw = paint.text_w(hint, "click = add lane")
        p.drawText(QPointF(w - tw - 10, 20), "click = add lane")
        p.fillRect(QRectF(1, 30, w - 2, 1), T.line)
        self.scroll = max(0, min(self.scroll, self.content_h() - h + 8))
        p.setClipRect(QRectF(1, 31, w - 2, h - 32))
        y = 36.0 - self.scroll
        colw = (w - 20 - 12) / 2
        key_f, val_f = T.font(12, 600), T.font(12, 700, mono=True)
        self._hits = []
        for title, rows in self.groups:
            p.setFont(T.font(12, 800, spacing=0.06))
            p.setPen(T.accent)
            p.drawText(QPointF(10, y + 14), title)
            y += 22
            for i, (cid, label, value) in enumerate(rows):
                x = 10 + (i % 2) * (colw + 12)
                ry = y + (i // 2) * self.ROW_H
                r = QRectF(x, ry, colw, self.ROW_H)
                if cid in self.lanes:
                    p.fillRect(r, T.sunk)
                p.fillRect(QRectF(x, ry + self.ROW_H - 1, colw, 1), T.sunk)
                p.setFont(val_f)
                p.setPen(T.fg)
                vw = paint.text_w(val_f, value)
                p.drawText(QRectF(x, ry, colw - 2, self.ROW_H), Qt.AlignVCenter | Qt.AlignRight, value)
                p.setFont(key_f)
                p.setPen(T.fg2)
                p.drawText(QRectF(x + 2, ry, colw - vw - 8, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft,
                           paint.elide(key_f, label, colw - vw - 8))
                if 31 <= ry + self.ROW_H / 2 <= h:
                    self._hits.append((r, cid))
            y += ((len(rows) + 1) // 2) * self.ROW_H + 6
        p.end()


# ---- the screen ------------------------------------------------------------

class ReplayScreen(QWidget):
    """``powerRequested`` (Open in Power): the pull under the cursor, or None."""

    powerRequested = Signal(object)
    message = Signal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.car = Car()
        self.view: ad.LogView | None = None
        self.forensics = ad.Forensics()
        self.derived: dv.Derived | None = None
        self.drive_events: list = []
        self.pulls: list = []
        self.track: Track | None = None
        self.colour_by = "speed"
        self.playing = False
        self.speed = 1.0
        self._play_t = -1.0
        self._live_t = -1.0
        self._file = ""
        self._note = ""
        self._view_note = ""
        self._cursor_text = ""
        self._delta_text = ""
        self._swatch = False                # the note describes the grey traces: draw their swatch
        self._map_t = None
        # transport
        self.play_btn = Button(icon=paint.PLAY, kind="primary", parent=self)
        self.play_btn.clicked.connect(self.toggle_play)
        self.play_btn.setToolTip("Play / pause (P)")
        self.speed_seg = Segment(SPEEDS, "1", parent=self)
        self.speed_seg.picked.connect(lambda key: self.set_speed(float(key)))
        self.scrub = Scrubber(self)
        self.scrub.moved.connect(self._scrubbed)
        self.prev_btn = Button(icon=paint.PREV, parent=self)
        self.next_btn = Button(icon=paint.NEXT, parent=self)
        self.prev_btn.clicked.connect(lambda: self.step_event(-1))
        self.next_btn.clicked.connect(lambda: self.step_event(1))
        self.prev_btn.setToolTip("Previous event ([)")
        self.next_btn.setToolTip("Next event (])")
        self.open_btn = Button("Open", "O", parent=self)
        self.open_btn.clicked.connect(self.open_dialog)
        self.compare_btn = Button("Compare", "C", parent=self)
        self.compare_btn.clicked.connect(self.compare)
        self.compare_btn.setToolTip("Lay a second drive over this one (C). With one laid over: "
                                    "what to line them up on (A cycles), , and . slide it, "
                                    "Shift+C removes it.")
        self.live_btn = Button("Live", "L", parent=self)
        self.live_btn.clicked.connect(self.go_live)
        for b in (self.play_btn, self.prev_btn, self.next_btn, self.open_btn, self.compare_btn, self.live_btn):
            b.setFixedHeight(36)
        self.speed_seg.setFixedHeight(36)
        # left column
        self.canvas = MapCanvas(None, self)
        self.canvas.clicked.connect(self._map_clicked)
        self.canvas.pinClicked.connect(self._pin_clicked)
        self.colour_btn = Button("Colour: speed", height=30, parent=self)
        self.colour_btn.px = 12
        self.colour_btn.clicked.connect(self.colour_menu)
        self.gg = GGPlot(self)
        self.pull_card = PullCard(self)
        self.pull_card.power_btn.clicked.connect(self.open_power)
        # middle and right
        self.block = LaneBlock(self)
        self.block.cursorMoved.connect(self._cursor_moved)
        self.block.laneMenu.connect(self.lane_menu)
        self.block.laneClosed.connect(self.toggle_lane)
        self.readouts = ReadoutGrid(self)
        self.sensors = SensorPanel(self)
        self.sensors.laneRequested.connect(self.toggle_lane)
        if session.demo:
            from ..replaymodel import demo_drive_view
            self.show_view(demo_drive_view())
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
        live = view.live
        self.forensics = self.session.logs.analyze(view) if not live and view.trigger_t is not None \
            else ad.Forensics()
        if live:
            self.derived, self.drive_events, self.pulls = None, [], []
        else:
            self.derived, self.drive_events = analyse(view, self.car)
            self.pulls = self._find_pulls(view)
        self.block.events = self.drive_events
        self.block.view = None              # the lanes are chosen for this view before it is drawn
        if not live:
            self.block.model = ReplayModel(view)
            for cid in dv.CHANNELS:
                pair = self.derived.series(cid) if self.derived is not None else None
                if pair is not None and np.isfinite(pair[1]).any():
                    self.block.model.set_derived(cid, *pair)
            self.block.model.set_events(self.drive_events)
            for row in self.forensics.loss:
                if row.channel_id:
                    self.block.model.add_event(row.t, "loss", row.signal)
            self.block.set_lanes(self.default_lanes(view))
        self.block.set_view(view)
        self._file, self._note = view.title, view.note
        self._view_note, self._swatch = view.note, False
        self.compare_btn.setEnabled(not live)
        lo, hi = self.block.span
        self.scrub.set_ticks([((e.t - lo) / ((hi - lo) or 1.0), e.kind) for e in self.drive_events])
        self._load_track(view)
        start = 0.0 if view.trigger_t is not None else view.span()[0]
        self.block.set_cursor(start, force=True)
        self.block.overlay.update()
        self.update()

    def default_lanes(self, view: ad.LogView) -> list[str]:
        """rpm, speed, throttle, MAP, lateral g; what the view lacks is filled
        from the black box rails and the rest."""
        model = self.block.model

        def has(cid):
            return (cid in view.t and len(view.t[cid]) > 1) or (model is not None and cid in model.derived)
        lanes = [c for c in DEFAULT_LANES if has(c)]
        for c in FALLBACK_LANES:
            if len(lanes) >= len(DEFAULT_LANES):
                break
            if has(c) and c not in lanes:
                lanes.append(c)
        return lanes or list(DEFAULT_LANES)

    def _find_pulls(self, view: ad.LogView) -> list:
        """The pulls of the drive, by the Power screen's rule (power.find_pulls)."""
        need = ("rpm", "vss", "tps")
        if not all(c in view.t and len(view.t[c]) > 10 for c in need):
            return []
        mp = view.data.get("map")
        try:
            return power.find_pulls(view.t["rpm"], view.data["rpm"], view.data["vss"], view.data["tps"], mp,
                                    t_vss=view.t["vss"], t_tps=view.t["tps"],
                                    t_map=view.t.get("map"), car=self.car)
        except (ValueError, IndexError):
            return []

    def go_live(self) -> None:
        self.playing = False
        self._live_t = -1.0
        self._refresh_live(self.session.bus.now(), first=True)

    def _refresh_live(self, now: float, first: bool = False) -> None:
        bus = self.session.bus
        view = ad.LogView(path=None, title="live", note="last 40 s · O opens a drive", live=True)
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

    # ---- the map ----

    def set_tiles(self, layer) -> None:
        """Map tiles for the GPS pane (the GPS map screen's layer)."""
        self.canvas.set_tiles(layer)

    def _borrow_tiles(self) -> None:
        if self.canvas.tiles is None:
            shell = self.window()
            layer = getattr(getattr(shell, "map", None), "tiles", None)
            if layer is not None:
                self.canvas.set_tiles(layer)

    def _load_track(self, view: ad.LogView) -> None:
        track = Track.from_view(view) if not view.live else None
        self.track = track if track is not None and not track.empty else None
        self.canvas.set_track(self.track)
        self._colour_track()
        self._pins()
        if self.track is not None:
            self._fit_map()
            self.canvas.set_text(f"GPS · coloured by {dict(COLOUR_BY)[self.colour_by]}", "")
        else:
            self.canvas.set_text("GPS", "no GPS fixes in this recording")
            self.canvas.set_cursor(None)

    def _colour_track(self) -> None:
        tr = self.track
        if tr is None:
            return
        key = self.colour_by
        values, lo, hi = None, 0.0, 1.0
        if key == "speed":
            values = tr.speed
            lo, hi = 0.0, max(60.0, float(np.nanmax(values)) if np.isfinite(values).any() else 60.0)
        elif self.derived is not None and key in self.derived.values:
            values = tr.sample(self.derived.t, self.derived.values[key], 1.0)
            spec = derived_spec(key)
            lo, hi = spec.lo, spec.hi
            if key == "lat_g":
                values = np.abs(values)
                lo = 0.0
        elif self.view is not None and key in self.view.t:
            values = tr.sample(self.view.t[key], self.view.data[key])
            fin = values[np.isfinite(values)]
            lo, hi = (float(fin.min()), float(fin.max())) if len(fin) else (0.0, 1.0)
        self.canvas.set_values(values, lo, max(hi, lo + 1e-6))

    def _pins(self) -> None:
        tr = self.track
        pins = []
        if tr is not None:
            for e in self.drive_events:
                kind = EVENT_PIN.get(e.kind)
                pos = tr.at(e.t) if kind else None
                if pos is None:
                    continue
                label = "".join(ch for ch in e.summary if ch.isdigit())[:2] if e.kind == "mark" else ""
                pins.append(Pin(pos.x, pos.y, kind, label, payload=e))
        self.canvas.set_pins(pins)

    def colour_menu(self) -> None:
        menu = QMenu(self)
        for key, text in COLOUR_BY:
            action = QAction(f"Colour by {text}", menu, checkable=True)
            action.setChecked(key == self.colour_by)
            action.triggered.connect(lambda _=False, k=key: self.set_colour(k))
            menu.addAction(action)
        menu.exec(QCursor.pos())

    def set_colour(self, key: str) -> None:
        self.colour_by = key
        self.colour_btn.set_text(f"Colour: {dict(COLOUR_BY).get(key, key)}")
        self._colour_track()
        if self.track is not None:
            self.canvas.set_text(f"GPS · coloured by {dict(COLOUR_BY).get(key, key)}", "")

    def _map_clicked(self, x: float, y: float) -> None:
        if self.track is None:
            return
        hit = self.track.nearest(x, y, self.canvas.px_to_unit(14.0))
        if hit is not None:
            self.block.set_cursor(hit[0])

    def _pin_clicked(self, payload) -> None:
        if payload is not None and hasattr(payload, "t"):
            self.block.set_cursor(float(payload.t))

    def _map_cursor(self, t: float) -> None:
        if self.track is None:
            return
        pos = self.track.at(t)
        if pos is None:
            self.canvas.set_cursor(None)
            return
        self.canvas.set_cursor((pos.x, pos.y), pos.heading if pos.heading == pos.heading else None)
        if self.playing and not self.canvas.on_screen(pos.x, pos.y, 40.0):
            self.canvas.center_on(pos.x, pos.y)

    # ---- lanes ----

    def toggle_lane(self, cid: str) -> None:
        """A click in the sensors panel: add the lane (or take it away)."""
        if cid in self.block.lane_ids():
            self.block.remove_lane(cid)
        else:
            self.block.add_lane(cid)
        self.block.set_cursor(self.block.cursor, force=True)
        self._sensors()

    def lane_menu(self, cid: str, pos) -> None:
        menu = QMenu(self)
        menu.addAction(f"Remove the {cid} lane", lambda: self.toggle_lane(cid))
        menu.addAction("Default lanes", self.reset_lanes)
        menu.exec(pos)

    def reset_lanes(self) -> None:
        if self.view is not None:
            self.block.set_lanes(self.default_lanes(self.view))
            self.block.set_cursor(self.block.cursor, force=True)

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

    def seek(self, t: float) -> None:
        """Cursor A to view time ``t`` (from the Events screen)."""
        self.block.set_cursor(t, force=True)

    def _cursor_moved(self, t: float) -> None:
        b = self.block
        lo, hi = b.span
        self.scrub.set((t - lo) / ((hi - lo) or 1.0))
        live = self.view is not None and self.view.live
        text = "LIVE" if live else b.time_text(t)
        delta = f"Δ {signed_time(t - b.cursor_b, 2)} s" if b.cursor_b is not None else ""
        if (text, delta) != (self._cursor_text, self._delta_text):
            self._cursor_text, self._delta_text = text, delta
            self.update(QRect(0, 0, self.width(), 52))
        self._calculated(t)
        self._sensors()
        if not live:
            self._map_cursor(t)

    def _calculated(self, t: float) -> None:
        """The readouts, the g-g plot and the pull card at time t."""
        d = self.derived
        if d is None or not len(d.t):
            self.readouts.set_values(["—"] * 8)
            self.gg.set_points([], [], None)
            self.pull_card.set_text("No pull at the cursor", "—", "")
            return
        at = d.at(t)
        tq, hp = at.get("torque", np.nan), at.get("power", np.nan)
        g = at.get("gear", np.nan)
        target_err = at.get("rpm_err", np.nan)
        self.readouts.set_values([
            _num(hp, ".0f", "hp"), _num(tq, ".0f", "N·m"),
            gear_name(g) if g == g and g > 0 else ("neutral / clutch" if g == g else "—"),
            _num(at.get("long_g", np.nan), ".2f", "g", signed=True),
            _num(abs(at.get("lat_g", np.nan)), ".2f", "g"),
            _num(at.get("grade", np.nan), ".1f", "%", signed=True),
            U.with_unit(FUEL_SPEC, at["fuel_lph"]) if at.get("fuel_lph", np.nan) == at.get("fuel_lph", np.nan) else "—",
            _num(target_err, ",.0f", "", signed=True)])
        lat = d.values.get("lat_g")
        lon = d.values.get("long_g")
        if lat is not None and lon is not None:
            i1 = int(np.clip(np.round((t - d.t[0]) * 10), 0, len(d.t) - 1))
            i0 = max(0, i1 - 50)
            x, y = lat[i0:i1 + 1], lon[i0:i1 + 1]
            ok = np.isfinite(x) & np.isfinite(y)
            now = (float(lat[i1]), float(lon[i1])) if np.isfinite(lat[i1]) and np.isfinite(lon[i1]) else None
            self.gg.set_points(x[ok], y[ok], now)
        pull = next((p for p in self.pulls if p.t0 - 0.5 <= t <= p.t1 + 0.5), None)
        if pull is None:
            self.pull_card.set_text("No pull at the cursor", "—", "a run in one gear with the throttle open")
        else:
            here = pull.at(min(max(t, pull.t[0]), pull.t[-1]))
            rpm, ptq, php = here if here is not None else (np.nan, np.nan, np.nan)
            self.pull_card.set_text(pull.label, f"{_num(ptq, '.0f', 'N·m')} · {_num(php, '.0f', 'hp')}",
                                    f"at {rpm:,.0f} rpm, now · peak {pull.peak_power_hp:.0f} hp")

    def _sensors(self) -> None:
        view, model = self.view, self.model
        if view is None:
            return
        ids = [cid for cid in view.specs if cid not in HIDDEN]
        derived_ids = [cid for cid in dv.CHANNELS if model is not None and cid in model.derived]
        values: dict[str, str] = {}
        if model is not None:
            rows = model.readouts(ids + derived_ids, full=False)
            for cid, row in rows.items():
                spec = view.specs.get(cid) or derived_spec(cid)
                values[cid] = U.text(spec, row.value)
        else:
            bus = self.session.bus
            for cid in ids:
                state = bus.channel(cid)
                values[cid] = U.text(view.specs[cid], state.value if state is not None else None)
        groups = []
        seen: set[str] = set()
        for title, members in SENSOR_GROUPS:
            rows = []
            for cid in members:
                if cid in values and cid not in seen:
                    spec = view.specs.get(cid) or derived_spec(cid)
                    units = U.spec(spec).units
                    rows.append((cid, spec.label, f"{values[cid]} {units}".strip() if not spec.is_flag else values[cid]))
                    seen.add(cid)
            if rows:
                groups.append((title, rows))
        rest = [cid for cid in ids if cid not in seen and cid in values]
        if rest:
            groups.append(("OTHER", [(cid, view.specs[cid].label,
                                      f"{values[cid]} {U.spec(view.specs[cid]).units}".strip()) for cid in rest]))
        self.sensors.set_groups(groups, self.block.lane_ids())

    def _scrubbed(self, frac: float) -> None:
        lo, hi = self.block.span
        self.block.set_cursor(lo + frac * (hi - lo))

    def step_event(self, direction: int) -> None:
        """[ and ]: to the previous or next event; past the last one, to the
        end of the drive."""
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

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        self.speed_seg.set_current({0.5: "0.5", 1.0: "1", 4.0: "4"}.get(speed, "1"))

    def toggle_play(self) -> None:
        if self.view is None or self.view.live:
            return
        self.playing = not self.playing
        if self.playing and self.block.cursor >= self.block.span[1] - 1e-6:
            self.block.set_cursor(self.block.span[0])
        self._play_t = -1.0
        self.play_btn.icon_path = paint.PAUSE if self.playing else paint.PLAY
        self.play_btn.update()

    def open_power(self) -> None:
        t = self.block.cursor
        pull = next((p for p in self.pulls if p.t0 - 0.5 <= t <= p.t1 + 0.5), None)
        self.powerRequested.emit(pull)
        shell = self.window()
        show = getattr(shell, "show_screen", None)
        if callable(show):
            from .. import main_window as mw
            show(getattr(mw, "POWER", 4))

    # ---- frame ----

    def refresh(self, now: float) -> None:
        if self.view is not None and self.view.live:
            if now - self._live_t >= 0.1:
                self._live_t = now
                self._refresh_live(now)
            return
        self._borrow_tiles()
        self.canvas.refresh()
        if self.playing:
            if self._play_t >= 0:
                self.block.set_cursor(self.block.cursor + (now - self._play_t) * self.speed)
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
        elif text in ("1", "2", "4") and not ctrl:
            self.set_speed({"1": 0.5, "2": 1.0, "4": 4.0}[text])
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
        pad, gap = 8, 8
        # transport row
        y = 6
        x = pad
        self.play_btn.setGeometry(x, y, 44, 36)
        x += 52
        self.speed_seg.setGeometry(x, y, 156, 36)
        x += 164
        self._time_x = x
        x += 132
        right = w - pad
        for btn in (self.compare_btn, self.open_btn, self.live_btn, self.next_btn, self.prev_btn):
            bw = btn.sizeHint().width() if btn.text() else 40
            right -= bw
            btn.setGeometry(right, y, bw, 36)
            right -= 6
        self.scrub.setGeometry(x + 4, y + 3, max(40, right - x - 10), 30)
        top = 50
        body_h = h - top - pad
        # columns
        left_w = int(min(400, max(280, w * 0.29)))
        side_w = int(min(330, max(250, w * 0.23)))
        mid_x = pad + left_w + gap
        mid_w = w - mid_x - gap - side_w - pad
        bottom_h = int(min(200, max(150, body_h * 0.32)))
        map_h = body_h - bottom_h - gap
        self.canvas.setGeometry(pad, top, left_w, map_h)
        self.colour_btn.setGeometry(pad + left_w - 132, top + 8, 124, 30)
        gg_w = min(bottom_h, left_w // 2)
        self.gg.setGeometry(pad, top + map_h + gap, gg_w, bottom_h)
        self.pull_card.setGeometry(pad + gg_w + gap, top + map_h + gap, left_w - gg_w - gap, bottom_h)
        grid_h = self.readouts.height()
        self._title_rect = QRectF(mid_x, top, mid_w, 22)
        self.block.setGeometry(mid_x, top + 24, mid_w, body_h - grid_h - gap - 24)
        self.readouts.setGeometry(mid_x, top + body_h - grid_h, mid_w, grid_h)
        self.sensors.setGeometry(w - pad - side_w, top, side_w, body_h)
        self._fit_map()

    def _fit_map(self) -> None:
        if self.track is not None and self.canvas.width() > 64:
            self.canvas.fit(self.track.bbox())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_map()

    def retheme(self) -> None:
        self.block.retheme()
        self.canvas.retheme()
        if self.model is not None:
            self.block._retrace()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.fillRect(QRectF(0, 47, self.width(), 1), T.line)
        x = getattr(self, "_time_x", 220)
        if self._delta_text:
            p.setFont(T.font(14, 700, mono=True))
            p.setPen(T.fg)
            p.drawText(QRectF(x, 4, 130, 20), Qt.AlignVCenter | Qt.AlignLeft, "A " + self._cursor_text)
            p.setPen(T.accent)
            p.drawText(QRectF(x, 23, 130, 20), Qt.AlignVCenter | Qt.AlignLeft, "A−B " + self._delta_text[2:])
        else:
            font = T.font(16, 700, mono=True)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(QRectF(x, 6, 130, 36), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(font, self._cursor_text, 130))
        # what is open, above the lanes: its name, and the note (or the second drive and its swatch)
        r = getattr(self, "_title_rect", QRectF(0, 50, 400, 22))
        name = T.font(13, 800)
        note = T.font(12, 600)
        p.setFont(name)
        p.setPen(T.fg)
        nw = min(paint.text_w(name, self._file), r.width() * 0.6)
        p.drawText(QRectF(r.x(), r.y(), nw + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(name, self._file, nw + 2))
        x = r.x() + nw + 12
        if self._swatch:
            p.fillRect(QRectF(x, r.y() + 6, 10, 10), T.color("rule2"))
            x += 16
        p.setFont(note)
        p.setPen(T.fg2)
        p.drawText(QRectF(x, r.y(), r.right() - x, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(note, self._note, r.right() - x))
        p.end()

    def title_text(self) -> str:
        """What the shell's status line can show: "drive_2026_11_26_2251 · 35:48"."""
        if self.view is None or self.view.live:
            return "live"
        lo, hi = self.block.span
        length = hi - lo
        return f"{self._file} · {int(length // 60)}:{int(length % 60):02d}" + (f" · {self._note}" if self._note else "")
