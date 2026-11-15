"""The sensor tile and the big drive tile (HANDOFF section 4).

One QWidget, one paintEvent. ``sync`` is called from the frame timer; it
compares the channel's generation counters with what the tile last drew and
calls ``update()`` only when something visible changed. Everything the paint
needs (strings, pixel positions, the sparkline polygon) is prepared in
``sync``, so painting allocates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pyqtgraph.functions as fn
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from . import paint
from .bus import SPARK_N, ChannelState
from .theme import T


@dataclass(frozen=True)
class Metrics:
    header_h: int
    pad_l: int
    pad_r: int
    label_px: int
    icon: int
    row_h: int              # value row
    value_px: int
    value_base: int         # baseline, px above the bottom of the value row
    units_px: int
    gauge_h: int
    gauge_m: tuple          # top, sides, bottom
    spark_h: int
    foot_h: int             # 0 = no min/max footer
    foot_px: int
    flag_sq: int
    flag_border: int
    flag_px: int
    big: bool               # drive layout: units in the header, value right-aligned


SENSOR = Metrics(24, 8, 6, 13, 16, 44, 40, 7, 14, 10, (3, 8, 4), 30, 18, 12, 30, 5, 36, False)
COMPACT = Metrics(24, 8, 6, 13, 16, 44, 40, 7, 14, 10, (3, 8, 4), 22, 0, 12, 30, 5, 36, False)
DRIVE = Metrics(40, 12, 10, 22, 24, 150, 104, 35, 22, 22, (6, 14, 10), 44, 30, 18, 60, 9, 104, True)


class Tile(QWidget):
    clicked = Signal(str)
    menuRequested = Signal(str, QPoint)

    def __init__(self, state: ChannelState | None = None, metrics: Metrics = SENSOR,
                 parent=None):
        super().__init__(parent)
        self.m = metrics
        self.state: ChannelState | None = None
        self.demo_level: str | None = None      # the Alarms key forces a state
        self.demo_stale = False
        self.max_decimals = 1 if metrics.big else 9
        self._poly = fn.create_qpolygonf(SPARK_N)
        self._pts = fn.ndarray_from_qpolygonf(self._poly)
        self._pts[:] = 0.0
        self._spark_pm: QPixmap | None = None
        self._reset()
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        if state is not None:
            self.bind(state)

    def _reset(self) -> None:
        self._gen = self._spark_gen = -1
        self._spec = None
        self._text = "---"
        self._value_font = None
        self._value_w = 0.0
        self._label = ""
        self._units = ""
        self._min = self._max = ""
        self._frac = self._min_frac = self._max_frac = -1.0
        self._level = ""
        self._stale = True
        self._icon_on = True
        self._on = False
        self._have_spark = False
        self._spark_key = None
        self._foot = ""
        self._tag = ""

    def bind(self, state: ChannelState | None) -> None:
        self.state = state
        self._reset()
        self.update()

    # ---- frame timer ----

    def sync(self, now: float, alarms=None, blink_on: bool = True,
             spark: bool = True) -> None:
        st = self.state
        if st is None:
            return
        spec = st.spec
        dirty = False
        if spec is not self._spec:              # first frame, or the map was reloaded
            self._spec = spec
            self._fspec = spec if spec.decimals <= self.max_decimals else replace(
                spec, decimals=self.max_decimals)
            self._label = spec.label.upper()
            self._units = spec.units
            self._tag = "BLACK BOX" if spec.source == "BB" else spec.source
            self._gen = self._spark_gen = -1
            dirty = True
        stale = self.demo_stale or st.stale(now)
        level = self.demo_level if self.demo_level is not None else (
            alarms.state(spec.id) if alarms is not None else "")
        icon_on = blink_on or level != "alarm" or alarms is None or not alarms.unacked(spec.id)
        if stale != self._stale or level != self._level or icon_on != self._icon_on:
            self._stale, self._level, self._icon_on = stale, level, icon_on
            self._gen = -1
            dirty = True
        if st.gen != self._gen:
            self._gen = st.gen
            dirty |= self._refresh_value(st, spec, stale)
        if spark and st.spark_gen != self._spark_gen:
            self._spark_gen = st.spark_gen
            self._refresh_spark(st, spec)
            dirty = True
        if dirty:
            self.update()

    def _fmt(self, spec, value) -> str:
        return self._fspec.format(value)

    def _refresh_value(self, st, spec, stale: bool) -> bool:
        value = st.value
        text = "---" if stale and not spec.is_flag else self._fmt(spec, value)
        if stale and spec.is_flag:
            text = "---"
        span = spec.hi - spec.lo
        w = max(1.0, self.width() - 4.0 - 2 * self.m.gauge_m[1])

        def px(v):
            if v != v:
                return -1.0
            return round(min(1.0, max(0.0, (v - spec.lo) / span)) * w) / w
        frac = 0.0 if stale else px(value)
        lo, hi = px(st.vmin), px(st.vmax)
        vmin, vmax = self._fmt(spec, st.vmin), self._fmt(spec, st.vmax)
        on = value >= 0.5 and not stale
        if (text == self._text and frac == self._frac and lo == self._min_frac
                and hi == self._max_frac and vmin == self._min and vmax == self._max
                and on == self._on):
            return False
        if text != self._text or self._value_font is None:
            self._text = text
            self._fit_value()
        self._frac, self._min_frac, self._max_frac = frac, lo, hi
        self._min, self._max, self._on = vmin, vmax, on
        return True

    def _fit_value(self) -> None:
        m = self.m
        if self._spec is not None and self._spec.is_flag:
            avail = self.width() - 4 - 2 * m.pad_l - m.flag_sq - 12
            base = m.flag_px
        else:
            units_w = 0.0 if m.big else paint.text_w(T.font(m.units_px, 700), self._units) + 6
            avail = self.width() - 4 - 2 * m.pad_l - units_w
            base = m.value_px
        for scale in (1.0, 0.85, 0.72, 0.6):
            font = T.font(int(base * scale), 800)
            width = paint.text_w(font, self._text)
            if width <= avail:
                break
        self._value_font, self._value_w = font, width

    def _refresh_spark(self, st, spec) -> None:
        m = self.m
        data = st.spark
        valid = data == data
        n_bad = SPARK_N - int(np.count_nonzero(valid))
        if n_bad == SPARK_N:
            self._have_spark = False
            return
        w, h = self.width() - 4.0, float(m.spark_h)
        top = self._spark_top()
        pts = self._pts
        pts[:, 0] = np.linspace(2.0, 2.0 + w, SPARK_N)
        if spec.is_flag:
            pts[:, 1] = top + h - 3.0 - np.nan_to_num(data) * (h - 8.0)
            self._foot = f"{int(np.count_nonzero(np.diff(data[valid])))} changes / 60 s"
        else:
            lo, hi = float(np.nanmin(data)), float(np.nanmax(data))
            span = max(hi - lo, 0.04 * (spec.hi - spec.lo)) or 1.0
            mid = 0.5 * (hi + lo)
            pts[:, 1] = top + h / 2.0 - (np.nan_to_num(data, nan=mid) - mid) / span * (h - 8.0)
        if n_bad:                               # collapse gaps onto the next good point
            first = int(np.argmax(valid))
            pts[:first] = pts[first]
            bad = np.flatnonzero(~valid[first:]) + first
            for i in bad:
                pts[i, 1] = pts[i - 1, 1]
        self._have_spark = True

    def _render_spark(self, ink) -> None:
        m = self.m
        w, h = max(1, self.width() - 4), m.spark_h + 4
        pm = self._spark_pm
        if pm is None or pm.width() != w or pm.height() != h:
            pm = self._spark_pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.translate(-2.0, 2.0 - self._spark_top())
        qp.setPen(T.cpen(ink, 2, Qt.RoundCap, Qt.RoundJoin))
        qp.drawPolyline(self._poly)
        qp.end()

    def _spark_top(self) -> float:
        m = self.m
        return 2.0 + m.header_h + m.row_h + m.gauge_m[0] + m.gauge_h + m.gauge_m[2]

    def resizeEvent(self, event) -> None:
        self._gen = self._spark_gen = -1
        self._value_font = None
        super().resizeEvent(event)

    # ---- input ----

    def mousePressEvent(self, event) -> None:
        if self.state is None:
            return
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.state.spec.id)
        elif event.button() == Qt.RightButton:
            self.menuRequested.emit(self.state.spec.id, event.globalPosition().toPoint())

    # ---- paint ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        m = self.m
        w, h = float(self.width()), float(self.height())
        spec = self._spec
        level, stale = self._level, self._stale
        alarm = level == "alarm"
        ink = T.on_alarm if alarm else T.fg
        ground = T.alarm if alarm else T.panel
        full = QRectF(0, 0, w, h)
        if stale and not alarm:
            p.fillRect(full, paint.hatch_brush())
        else:
            p.fillRect(full, ground)
        p.setBrush(Qt.NoBrush)
        p.setPen(T.cpen(T.alarm if alarm else T.rule, 2))
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
        if spec is None:
            p.end()
            return

        # header
        head_ink = ink
        if level == "warn":
            p.fillRect(QRectF(2, 2, w - 4, m.header_h), T.warn)
            p.setPen(T.cpen(T.warn, 3))
            p.drawRect(QRectF(3.5, 3.5, w - 7, h - 7))
            head_ink = T.on_warn
        x = 2.0 + m.pad_l
        cy = 2.0 + m.header_h / 2.0
        if level:
            if self._icon_on:
                p.setRenderHint(QPainter.Antialiasing, True)
                paint.shape(p, paint.TRIANGLE if level == "warn" else paint.OCTAGON,
                            x, cy - m.icon / 2.0, m.icon, head_ink)
                p.setRenderHint(QPainter.Antialiasing, False)
            x += m.icon + 5
        mx = w - 2 - m.pad_r - 18
        label_font = T.font(m.label_px, 800, spacing=0.04)
        right = mx - 5
        if m.big and self._units:
            ufont = T.font(m.units_px, 700)
            uw = paint.text_w(ufont, self._units)
            p.setFont(ufont)
            p.setPen(head_ink)
            p.drawText(QRectF(right - uw - 2, 2, uw + 2, m.header_h),
                       Qt.AlignVCenter | Qt.AlignRight, self._units)
            right -= uw + 8
        p.setFont(label_font)
        p.setPen(head_ink)
        p.drawText(QRectF(x, 2, right - x, m.header_h), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(label_font, self._label, right - x))
        if level == "warn":
            paint.marker(p, mx, cy - 9, spec.verified, T.on_warn, T.warn)
        elif alarm:
            paint.marker(p, mx, cy - 9, spec.verified, T.on_alarm, T.alarm)
        else:
            paint.marker(p, mx, cy - 9, spec.verified)

        # value
        row_top = 2.0 + m.header_h
        base_y = row_top + m.row_h - m.value_base
        p.setPen(ink)
        if self._value_font is None:
            self._fit_value()
        if spec.is_flag:
            sq = float(m.flag_sq)
            block_h = m.row_h + m.gauge_m[0] + m.gauge_h
            sy = row_top + (block_h - sq) / 2.0 + (0 if m.big else 2)
            sx = 2.0 + m.pad_l
            if not stale:
                b = float(m.flag_border)
                p.setPen(T.cpen(ink, b))
                p.setBrush(ink if self._on else Qt.NoBrush)
                p.drawRect(QRectF(sx + b / 2, sy + b / 2, sq - b, sq - b))
                p.setBrush(Qt.NoBrush)
                p.setPen(ink)
            p.setFont(self._value_font)
            p.drawText(QRectF(sx + (0 if stale else sq + 12), sy - 20, w, sq + 40),
                       Qt.AlignVCenter | Qt.AlignLeft, self._text)
        else:
            p.setFont(self._value_font)
            if m.big:
                p.drawText(QPointF(w - 2 - 14 - self._value_w, base_y), self._text)
            else:
                vx = 2.0 + m.pad_l
                p.drawText(QPointF(vx, base_y), self._text)
                p.setFont(T.font(m.units_px, 700))
                p.drawText(QPointF(vx + self._value_w + 6, base_y), self._units)
            # gauge
            gx = 2.0 + m.gauge_m[1]
            gy = row_top + m.row_h + m.gauge_m[0]
            gw = w - 4 - 2 * m.gauge_m[1]
            gh = float(m.gauge_h)
            if alarm:
                paint.track(p, QRectF(gx, gy, gw, gh), self._frac, ink, ink, ground)
            else:
                paint.track(p, QRectF(gx, gy, gw, gh), self._frac)
            tick = ink if alarm else T.accent
            for frac in (self._min_frac, self._max_frac):
                if frac >= 0:
                    p.fillRect(QRectF(gx + frac * gw - 1.5, gy - 5, 3, gh + 10), tick)

        # sparkline: stroked into a pixmap at 2 Hz, blitted on every other paint
        if self._have_spark and not stale:
            key = (self._spark_gen, ink.rgba(), self.width())
            if key != self._spark_key:
                self._spark_key = key
                self._render_spark(ink)
            p.drawPixmap(2, int(self._spark_top()) - 2, self._spark_pm)

        # footer
        if m.foot_h:
            fy = self._spark_top() + m.spark_h
            font = T.font(m.foot_px, 700, mono=True)
            p.setFont(font)
            p.setPen(ink)
            rect = QRectF(2 + m.pad_l, fy, w - 4 - 2 * m.pad_l, m.foot_h)
            if not spec.mapped:
                p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, "not in address map")
            elif spec.is_flag:
                p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, self._foot)
            else:
                ts = m.foot_px * 0.62
                ty = fy + (m.foot_h - ts) / 2.0
                p.setRenderHint(QPainter.Antialiasing, True)
                paint.shape(p, paint.DOWN, rect.x(), ty, ts * 2, ink)
                maxw = paint.text_w(font, self._max)
                paint.shape(p, paint.UP, rect.right() - maxw - ts - 7, ty, ts * 2, ink)
                p.setRenderHint(QPainter.Antialiasing, False)
                p.drawText(rect.adjusted(ts + 7, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           self._min)
                p.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, self._max)
                if m.big:
                    p.drawText(rect, Qt.AlignCenter, self._tag)
        p.end()
