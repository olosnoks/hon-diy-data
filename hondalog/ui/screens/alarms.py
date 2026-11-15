"""F7, status bar and alarms (HANDOFF 5.7): the shape key, the tile-state
key, the live alarm list and the limits table."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import adapters as ad
from .. import chrome, paint
from ..bus import ChannelBus, ChannelSpec
from ..controls import Card
from ..theme import T
from ..tiles import SENSOR, Tile

S = ad.SourceStatus
STATE_ROWS = (
    ("Disconnected", (chrome.source_chip("ECU", S(ad.OFF, detail="no port")),
                      chrome.source_chip("BLACK BOX", S(ad.OFF, detail="not found")),
                      chrome.rec_chip(False, 0), chrome.stall_chip(ad.StallStatus("off")))),
    ("Connecting", (chrome.source_chip("ECU", S(ad.CONNECTING, detail="waking K-line")),
                    chrome.source_chip("BLACK BOX", S(ad.OK, 1000.0)),
                    chrome.rec_chip(False, 0), chrome.stall_chip(ad.StallStatus("off")))),
    ("Live", (chrome.source_chip("ECU", S(ad.OK, 10.8)),
              chrome.source_chip("BLACK BOX", S(ad.OK, 1000.0)),
              chrome.rec_chip(True, 30), chrome.stall_chip(ad.StallStatus("armed")))),
    ("ECU lost", (chrome.source_chip("ECU", S(ad.SILENT, silent_s=1.4)),
                  chrome.source_chip("BLACK BOX", S(ad.OK, 1000.0)),
                  chrome.rec_chip(True, 31),
                  chrome.stall_chip(ad.StallStatus("saving", post_left_s=10)))),
)
SHAPES = (("ok", "filled circle: link up, data flowing"),
          ("off", "hollow square: off or not connected"),
          ("half", "half circle: working on it"),
          ("ring", "ring: stall catcher armed"),
          ("tri", "triangle + yellow band: warning limit"),
          ("oct", "octagon + filled tile: alarm limit"),
          ("unv", "dashed box: address is a guess"))
KEY_TILES = (
    (ChannelSpec("k_rpm", "RPM", "rpm", 0, 0, 8000, verified=True), 3731, "", False,
     "Normal, verified"),
    (ChannelSpec("k_map", "MAP", "kPa", 0, 0, 110), 78, "", False, "Normal, unverified"),
    (ChannelSpec("k_iat", "Intake air", "°C", 0, -20, 80), 46, "warn", False, "Warning"),
    (ChannelSpec("k_fpv", "FP feed", "V", 2, 0, 16, source="BB", verified=True), 9.8, "alarm",
     False, "Alarm"),
    (ChannelSpec("k_o2", "O2", "V", 2, 0, 1.2), 0.4, "", True, "No data: hatched"),
    (ChannelSpec("k_fp", "Fuel pump", kind="flag"), 1, "", False, "Flag on: filled"),
    (ChannelSpec("k_vtec", "VTEC", kind="flag"), 0, "", False, "Flag off: hollow"),
    (ChannelSpec("k_mil", "MIL", kind="flag"), 1, "alarm", False, "Flag in alarm"),
)


class StatesCard(Card):
    def __init__(self, parent=None):
        super().__init__("Connection bar states · shape first, colour second", parent=parent)

    def paint_body(self, p, x, y, w, h) -> None:
        for n, (name, chips) in enumerate(STATE_ROWS):
            cy = y + 2 + n * 42
            paint.caption(p, x, cy + 8, name)
            cx = x + 132
            for chip in chips:
                if cx + chrome.chip_width(chip) > x + w:
                    break
                cx += chrome.draw_chip(p, cx, cy, chip) + 8


class ShapeCard(Card):
    def __init__(self, parent=None):
        super().__init__("Shape key", parent=parent)

    def paint_body(self, p, x, y, w, h) -> None:
        font = T.font(14, 700)
        step = min(22.0, h / len(SHAPES))
        for n, (kind, text) in enumerate(SHAPES):
            cy = y + n * step
            if kind == "unv":
                paint.marker(p, x - 2, cy + (step - 18) / 2, False)
            else:
                paint.chip_icon(p, kind, x, cy + (step - 14) / 2, T.fg)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(QRectF(x + 24, cy, w - 24, step), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(font, text, w - 24))


class AlarmList(Card):
    ROW = 34

    def __init__(self, alarms, parent=None):
        super().__init__("Alarm list · live, newest first", parent=parent)
        self.alarms = alarms
        self.rows: list = []
        self.now = 0.0

    def sync(self, now: float) -> None:
        self.now = now
        self.rows = list(self.alarms.active()) + list(self.alarms.history())
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        x, w = 2.0, self.width() - 4.0
        y = 30.0
        body, bold, mono = T.font(14, 600), T.font(14, 800), T.font(14, 700, mono=True)
        if not self.rows:
            p.setFont(body)
            p.setPen(T.fg2)
            p.drawText(QRectF(x + 10, y, w, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       "Nothing yet. Warnings and alarms appear here as they happen.")
        for ev in self.rows[:max(1, int((self.height() - 34) // self.ROW))]:
            active = ev.t_end is None
            r = QRectF(x, y, w, self.ROW)
            if active:
                fill, ink = (T.alarm, T.on_alarm) if ev.severity == "alarm" else (T.warn, T.on_warn)
                p.fillRect(r, fill)
                paint.chip_icon(p, "oct" if ev.severity == "alarm" else "tri", x + 10, y + 9,
                                ink, size=16)
                word = "ALARM" if ev.severity == "alarm" else "WARN"
                age = "active" + (" · ack" if ev.acked and ev.severity == "alarm" else "")
            else:
                ink = T.fg
                paint.chip_icon(p, "off", x + 11, y + 10, ink)
                word = "CLEARED"
                secs = max(0.0, self.now - ev.t_end)
                age = f"{secs:.0f} s ago" if secs < 90 else f"{secs / 60:.0f} min ago"
            p.fillRect(QRectF(x, y + self.ROW - 2, w, 2), T.sunk)
            p.setPen(ink)
            p.setFont(bold)
            p.drawText(QRectF(x + 44, y, 100, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft, word)
            p.setFont(body)
            p.drawText(QRectF(x + 152, y, 190, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(body, f"{ev.source} {ev.label}", 186))
            p.setFont(mono)
            p.drawText(QRectF(x + 346, y, 120, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       ev.value_text)
            p.setFont(body)
            lim = ev.limit_text if active else f"{ev.limit_text} for {ev.t_end - ev.t_start:.1f} s"
            p.drawText(QRectF(x + 470, y, w - 470 - 110, self.ROW - 2),
                       Qt.AlignVCenter | Qt.AlignLeft, paint.elide(body, lim, w - 470 - 114))
            p.setFont(mono)
            p.drawText(QRectF(x, y, w - 10, self.ROW - 2), Qt.AlignVCenter | Qt.AlignRight, age)
            y += self.ROW


class LimitsCard(Card):
    def __init__(self, alarms, parent=None):
        super().__init__("Limits \u00b7 built-in placeholders", parent=parent)
        self.alarms = alarms
        self.rows: list = []

    def sync(self) -> None:
        rows = list(self.alarms.limits())
        if rows != self.rows:
            self.rows = rows
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        mono = T.font(13, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        c2, c3 = x + w * 0.46, x + w * 0.72
        for n, (name, warn, alarm) in enumerate(
                [("CHANNEL", "WARN", "ALARM")] + [(r.name, r.warn, r.alarm) for r in self.rows]):
            cy = y + n * 16
            if cy + 16 > y + h + 4:
                break
            p.drawText(QRectF(x, cy, c2 - x - 4, 17), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mono, name, c2 - x - 4))
            p.drawText(QRectF(c2, cy, c3 - c2, 17), Qt.AlignVCenter | Qt.AlignLeft, warn)
            p.drawText(QRectF(c3, cy, x + w - c3, 17), Qt.AlignVCenter | Qt.AlignLeft, alarm)


class AlarmsScreen(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.states = StatesCard(self)
        self.shapes = ShapeCard(self)
        self.list = AlarmList(session.alarms, self)
        self.limits = LimitsCard(session.alarms, self)
        # the tile-state key runs on its own tiny bus with fixed values
        self._key_bus = ChannelBus()
        self.key_tiles: list[Tile] = []
        for spec, value, level, stale, _cap in KEY_TILES:
            state = self._key_bus.register(spec)
            self._key_bus.publish({spec.id: value}, t=0.0)
            state.vmin, state.vmax = value * 0.82, value * 1.08
            state.timeout = float("inf")
            if not spec.is_flag:
                k = np.arange(len(state.spark))
                state.spark[:] = value * (1 + 0.04 * np.sin(k * 0.21 + len(self.key_tiles)))
            else:
                state.spark[:] = value
            state.spark_gen += 1
            tile = Tile(state, SENSOR, self)
            tile.demo_level, tile.demo_stale = level, stale
            self.key_tiles.append(tile)
        self._rev = -1
        self._t = -1.0
        self._banner = QRectF()

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        side = 420
        self.states.setGeometry(8, 8, w - 16 - side - 8, 204)
        self.shapes.setGeometry(w - 8 - side, 8, side, 204)
        self._banner = QRectF(8, 220, w - 16, 44)
        ty = 272
        tw = (w - 16 - 7 * 6) / 8
        for i, tile in enumerate(self.key_tiles):
            x0, x1 = round(8 + i * (tw + 6)), round(8 + i * (tw + 6) + tw)
            tile.setGeometry(x0, ty, x1 - x0, 142)
        ly = ty + 142 + 26
        self.list.setGeometry(8, ly, w - 16 - side - 8, h - 8 - ly)
        self.limits.setGeometry(w - 8 - side, ly, side, h - 8 - ly)

    def retheme(self) -> None:
        for tile in self.key_tiles:
            tile._value_font = None
            tile._gen = -1

    def refresh(self, now: float) -> None:
        for tile in self.key_tiles:
            tile.sync(0.0, None, True)
        if self.session.alarms.revision != self._rev or now - self._t >= 1.0:
            self._rev, self._t = self.session.alarms.revision, now
            self.list.sync(now)
            self.limits.sync()

    def key(self, _event) -> bool:
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        s = self.session
        if s.banner is not None:
            title, cause = f"STALL {s.banner.wall:%H:%M:%S}", s.banner_cause
        else:
            title = "STALL 14:02:11"
            cause = "Example of the stall banner. It covers the tab row when the catcher fires."
        chrome.paint_banner(p, self._banner, title, cause)
        for tile, (_, _, _, _, cap) in zip(self.key_tiles, KEY_TILES):
            g = tile.geometry()
            paint.caption(p, g.x(), g.bottom() + 5, cap, width=g.width())
        p.end()
