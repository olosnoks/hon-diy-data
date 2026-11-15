"""F1, all sensors: every channel on one screen, no scrolling (HANDOFF 5.1)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint
from ..chrome import _rate
from ..theme import T
from ..tiles import COMPACT, SENSOR, Tile

COLS, GAP, PAD_X, PAD_Y, HEAD_H = 8, 6, 8, 6, 18


class SensorsScreen(QWidget):
    pinRequested = Signal(str)
    menuRequested = Signal(str, QPoint)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.tiles: list[Tile] = []
        self._groups: list = []         # (source, title, [tiles], header y)
        self._version = -1
        self._info: dict = {}
        self._info_t = -1.0
        self._frame = 0

    # ---- build / layout ----

    def _rebuild(self) -> None:
        self._version = self.session.bus.version
        for tile in self.tiles:
            tile.setParent(None)
            tile.deleteLater()
        self.tiles, self._groups = [], []
        for source, title in (("ECU", "ECU · K-line"), ("BB", "Black box · USB"),
                              ("MATH", "Math channels")):
            states = self.session.ordered(source)
            if not states:
                continue
            tiles = []
            for state in states:
                tile = Tile(state, SENSOR, self)
                tile.clicked.connect(self.pinRequested)
                tile.menuRequested.connect(self.menuRequested)
                tile.show()
                tiles.append(tile)
            self.tiles += tiles
            self._groups.append([source, title, tiles, 0])
        self._layout()

    def _layout(self) -> None:
        if not self._groups:
            return
        w, h = self.width(), self.height()
        rows = sum(math.ceil(len(g[2]) / COLS) for g in self._groups)
        heads = len(self._groups) * (HEAD_H + 2)
        tile_h = min(142, (h - 2 * PAD_Y - heads - (rows - 1) * GAP) // max(1, rows))
        if tile_h < 142:                    # fifth row: drop the footer, 112 px tiles
            tile_h = min(112, tile_h)
        metrics = SENSOR if tile_h >= 142 else COMPACT
        tile_w = (w - 2 * PAD_X - (COLS - 1) * GAP) / COLS
        y = PAD_Y
        for group in self._groups:
            group[3] = y
            y += HEAD_H + 4
            for i, tile in enumerate(group[2]):
                r, c = divmod(i, COLS)
                x0 = round(PAD_X + c * (tile_w + GAP))
                x1 = round(PAD_X + c * (tile_w + GAP) + tile_w)
                if tile.m is not metrics:
                    tile.m = metrics
                tile.setGeometry(x0, y + r * (tile_h + GAP), x1 - x0, tile_h)
            y += math.ceil(len(group[2]) / COLS) * (tile_h + GAP) - 2
        self.update()

    def resizeEvent(self, _event) -> None:
        self._layout()

    def retheme(self) -> None:
        for tile in self.tiles:
            tile._value_font = None

    # ---- frame ----

    def refresh(self, now: float) -> None:
        if self.session.bus.version != self._version:
            self._rebuild()
        alarms = self.session.alarms
        blink = (now % 1.0) < 0.5
        # Values update at once; the 2 Hz sparkline redraws are spread over
        # four frames so 32 polylines never land in one.
        self._frame = phase = (self._frame + 1) % 4
        for i, tile in enumerate(self.tiles):
            tile.sync(now, alarms, blink, i % 4 == phase)
        if now - self._info_t >= 1.0:
            self._info_t = now
            info = {}
            for source, _, tiles, _ in self._groups:
                n = sum(1 for t in tiles if t.state.spec.mapped)
                if source == "ECU":
                    hz = self.session.ecu_status.rate_hz
                    info[source] = f"{n} channels · {_rate(hz)}" if hz else f"{n} channels"
                elif source == "BB":
                    st = self.session.bb_status
                    info[source] = (f"{n} channels · {_rate(st.rate_hz)}"
                                    + (" · simulated" if st.simulated else "")
                                    if st.rate_hz else f"{n} channels · {st.detail or 'off'}")
                else:
                    info[source] = f"{n} channels"
            if info != self._info:
                self._info = info
                self.update()

    def key(self, _event) -> bool:
        return False

    # ---- paint ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        mono = T.font(13, 500, mono=True)
        cap = T.font(12, 800, spacing=0.06)
        for n, (source, title, _tiles, y) in enumerate(self._groups):
            p.setFont(cap)
            p.setPen(T.fg)
            text = title.upper()
            p.drawText(QRectF(PAD_X, y, 400, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, text)
            x = PAD_X + paint.text_w(cap, text) + 12
            p.setFont(mono)
            p.drawText(QRectF(x, y, 600, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft,
                       self._info.get(source, ""))
            if n == 0:
                self._legend(p, y)
        p.end()

    def _legend(self, p: QPainter, y: float) -> None:
        body = T.font(13, 600)
        mono = T.font(13, 500, mono=True)
        tail = "session min / max · trace = last 60 s"
        items = (("unv", "address unverified"), ("ver", "verified"), ("tri", "warn"),
                 ("oct", "alarm"))
        total = paint.text_w(mono, tail) + 26
        for _, text in items:
            total += 18 + 6 + paint.text_w(body, text) + 12
        x = self.width() - PAD_X - total
        if x < 430:
            return
        for kind, text in items:
            if kind in ("unv", "ver"):
                paint.marker(p, x, y, kind == "ver")
            else:
                p.setRenderHint(QPainter.Antialiasing, True)
                paint.shape(p, paint.TRIANGLE if kind == "tri" else paint.OCTAGON,
                            x + 1, y + 1, 16, T.fg)
                p.setRenderHint(QPainter.Antialiasing, False)
            p.setFont(body)
            p.setPen(T.fg2)
            p.drawText(QRectF(x + 24, y, 300, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += 24 + paint.text_w(body, text) + 12
        p.setRenderHint(QPainter.Antialiasing, True)
        paint.shape(p, paint.DOWN, x, y + 5, 18, T.fg)
        paint.shape(p, paint.UP, x + 11, y + 5, 18, T.fg)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(x + 26, y, 400, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, tail)
