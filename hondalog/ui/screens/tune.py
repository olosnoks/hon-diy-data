"""F6, tuning table editor shell (HANDOFF 5.6): exact values in a 2D grid,
shape in the 3D surface next to it. All table logic is in the TableModel."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QFileDialog, QWidget

from .. import paint, theme
from ..controls import Button, Card, Segment
from ..scene3d import MapModel, MapScene
from ..theme import T

HEAD_W, HEAD_H, ROW_H = 60, 24, 24


class TableGrid(QWidget):
    moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = np.zeros((1, 1))
        self.base = self.values
        self.rpm = np.zeros(1)
        self.map = np.zeros(1)
        self.decimals = 2
        self.lo, self.hi = 0.0, 1.0
        self.cur = (0, 0)               # (row = rpm index, col = map index)
        self.anchor = (0, 0)
        self.live: tuple | None = None
        self._text: list = []
        self._heat: list = []
        self._changed = np.zeros((1, 1), dtype=bool)
        self.setCursor(Qt.PointingHandCursor)

    def load(self, values, base, rpm, kpa, decimals, lo, hi) -> None:
        self.values, self.base, self.rpm, self.map = values, base, rpm, kpa
        self.decimals, self.lo, self.hi = decimals, lo, hi
        nr, nc = values.shape
        self.cur = (min(self.cur[0], nr - 1), min(self.cur[1], nc - 1))
        self.anchor = (min(self.anchor[0], nr - 1), min(self.anchor[1], nc - 1))
        self.recolor()

    def recolor(self) -> None:
        """Texts and heat colours, once per edit / theme change, not per paint."""
        v = self.values
        t = np.clip((v - self.lo) / ((self.hi - self.lo) or 1.0), 0, 1)
        rgb = (theme.heat_ramp(T.tokens, t) * 255).astype(int)
        nr, nc = v.shape
        self._heat = [[QColor(int(rgb[r, c, 0]), int(rgb[r, c, 1]), int(rgb[r, c, 2]))
                       for c in range(nc)] for r in range(nr)]
        self._text = [[f"{v[r, c]:.{self.decimals}f}".replace("-", "−")
                       for c in range(nc)] for r in range(nr)]
        self._changed = np.abs(v - self.base) > 1e-6
        self.update()

    def selection(self) -> list:
        (r0, c0), (r1, c1) = self.anchor, self.cur
        return [(r, c) for r in range(min(r0, r1), max(r0, r1) + 1)
                for c in range(min(c0, c1), max(c0, c1) + 1)]

    def move(self, dr: int, dc: int, extend: bool = False) -> None:
        nr, nc = self.values.shape
        self.cur = (max(0, min(nr - 1, self.cur[0] + dr)), max(0, min(nc - 1, self.cur[1] + dc)))
        if not extend:
            self.anchor = self.cur
        self.update()
        self.moved.emit()

    def set_live(self, cell) -> None:
        if cell != self.live:
            self.live = cell
            self.update()

    def cell_w(self) -> float:
        return (self.width() - HEAD_W - 4) / self.values.shape[1]

    def _rect(self, r: int, c: int) -> QRectF:
        cw = self.cell_w()
        x0 = round(HEAD_W + 2 + c * cw)
        return QRectF(x0, HEAD_H + 2 + r * ROW_H, round(HEAD_W + 2 + (c + 1) * cw) - x0, ROW_H)

    def mousePressEvent(self, event) -> None:
        c = int((event.position().x() - HEAD_W - 2) // self.cell_w())
        r = int((event.position().y() - HEAD_H - 2) // ROW_H)
        nr, nc = self.values.shape
        if 0 <= r < nr and 0 <= c < nc:
            self.cur = (r, c)
            if not event.modifiers() & Qt.ShiftModifier:
                self.anchor = self.cur
            self.update()
            self.moved.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        nr, nc = self.values.shape
        p.fillRect(self.rect(), T.panel)
        head = T.font(13, 700, mono=True)
        p.setFont(T.font(12, 700, mono=True))
        p.setPen(T.fg)
        p.drawText(QRectF(6, 2, HEAD_W, 12), Qt.AlignVCenter | Qt.AlignLeft, "RPM ↓")
        p.drawText(QRectF(6, 13, HEAD_W, 12), Qt.AlignVCenter | Qt.AlignLeft, "kPa →")
        p.setFont(head)
        for c in range(nc):
            r = self._rect(0, c)
            p.drawText(QRectF(r.x(), 2, r.width(), HEAD_H), Qt.AlignCenter, f"{self.map[c]:g}")
        for r in range(nr):
            p.drawText(QRectF(2, HEAD_H + 2 + r * ROW_H, HEAD_W - 8, ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"{self.rpm[r]:g}")
        cell, bold = T.font(13, 600, mono=True), T.font(13, 800, mono=True)
        ink = T.on_heat
        tri = QPolygonF([QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)])
        p.setFont(cell)
        for r in range(nr):
            heat, text, changed = self._heat[r], self._text[r], self._changed[r]
            for c in range(nc):
                rect = self._rect(r, c)
                p.fillRect(rect, heat[c])
                p.setPen(ink)
                if self.live == (r, c):
                    p.setFont(bold)
                    p.drawText(rect, Qt.AlignCenter, text[c])
                    p.setFont(cell)
                else:
                    p.drawText(rect, Qt.AlignCenter, text[c])
                if changed[c]:
                    tri[0] = QPointF(rect.right() - 9, rect.y())
                    tri[1] = QPointF(rect.right() + 1, rect.y())
                    tri[2] = QPointF(rect.right() + 1, rect.y() + 10)
                    p.setPen(Qt.NoPen)
                    p.setBrush(ink)
                    p.drawPolygon(tri)
                    p.setBrush(Qt.NoBrush)
        p.fillRect(QRectF(HEAD_W, 0, 2, self.height()), T.rule)
        p.fillRect(QRectF(0, HEAD_H, self.width(), 2), T.rule)
        if self.live is not None and self.live[0] < nr and self.live[1] < nc:
            p.setPen(T.cpen(T.on_heat, 3))
            p.drawRect(self._rect(*self.live).adjusted(1.5, 1.5, -1.5, -1.5))
        (r0, c0), (r1, c1) = self.anchor, self.cur
        box = self._rect(min(r0, r1), min(c0, c1)).united(self._rect(max(r0, r1), max(c0, c1)))
        p.setPen(T.cpen(T.accent if not T.night else T.accent, 3, Qt.FlatCap, dash=(2.0, 1.4)))
        p.drawRect(box.adjusted(1.5, 1.5, -1.5, -1.5))
        paint.frame(p, QRectF(self.rect()), 2, T.rule)
        p.end()


class ValueCard(Card):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent=parent)
        self.name = self.value = self.units = self.foot = ""

    def set(self, name, value, units, foot) -> None:
        if (name, value, units, foot) != (self.name, self.value, self.units, self.foot):
            self.name, self.value, self.units, self.foot = name, value, units, foot
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        mono = T.font(15, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(mono, self.name, w))
        big = T.font(52, 800)
        p.setFont(big)
        p.drawText(QPointF(x, y + 70), self.value)
        p.setFont(T.font(18, 700))
        p.drawText(QPointF(x + paint.text_w(big, self.value) + 8, y + 70), self.units)
        p.setFont(mono)
        p.drawText(QRectF(x, y + 82, w, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(mono, self.foot, w))


class ChipCard(Card):
    def __init__(self, parent=None):
        super().__init__("Chip · P28 only", parent=parent)
        self.text = ""
        self._lines: list = []
        self.save_btn = Button("Save .bin", parent=self)
        self.write_btn = Button("Write to chip", parent=self)
        self.write_btn.setEnabled(False)

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self._lines = []
            self.update()

    def resizeEvent(self, _event) -> None:
        y = self.height() - 10 - 44
        half = (self.width() - 20 - 8) // 2
        self.save_btn.setGeometry(10, y, half, 44)
        self.write_btn.setGeometry(10 + half + 8, y, self.width() - 20 - half - 8, 44)

    def paint_body(self, p, x, y, w, h) -> None:
        font = T.font(15, 700)
        if not self._lines:
            self._lines = paint.wrap(font, self.text, w)
        p.setFont(font)
        p.setPen(T.fg)
        room = int((h - 52) // 20)
        for i, line in enumerate(self._lines[:room]):
            if i == room - 1 and len(self._lines) > room:
                line = paint.elide(font, line + " …", w)
            p.drawText(QRectF(x, y + 20 * i, w, 20), Qt.AlignVCenter | Qt.AlignLeft, line)


class TuneScreen(QWidget):
    def __init__(self, session, settings=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.tm = session.tables
        self.infos = list(self.tm.tables())
        self.table = self.infos[0]
        self.seg = Segment([(i.id, i.name) for i in self.infos], self.table.id, parent=self)
        self.seg.picked.connect(self.set_table)
        self.grid = TableGrid(self)
        self.grid.moved.connect(self._selection_changed)
        self.model = MapModel(self.tm.rpm_axis, self.tm.map_axis)
        self.model.logged = False
        self.scene = MapScene(self.model, dist=4.9, el=0.46,
                              key_items=(("live", "live point"), ("sel", "selected cell")),
                              hint="", label_px=(13, 15), wire_skip=3, settings=settings,
                              parent=self)
        self.dec_btn = Button("−", "-", parent=self)
        self.inc_btn = Button("+", "=", parent=self)
        self.int_btn = Button("Interpolate", "I", parent=self)
        self.smo_btn = Button("Smooth", "S", parent=self)
        self.undo_btn = Button("Undo", "^Z", parent=self)
        self.dec_btn.clicked.connect(lambda: self.bump(-1))
        self.inc_btn.clicked.connect(lambda: self.bump(1))
        self.int_btn.clicked.connect(self.interpolate)
        self.smo_btn.clicked.connect(self.smooth)
        self.undo_btn.clicked.connect(self.undo)
        self.sel_card = ValueCard("Selected cell", self)
        self.live_card = ValueCard("Live cell", self)
        self.chip = ChipCard(self)
        self.chip.save_btn.clicked.connect(self.save)
        for b in (self.dec_btn, self.inc_btn, self.int_btn, self.smo_btn, self.undo_btn):
            b.setEnabled(self.tm.writable)
        self._rev = -1
        self._gen = -1
        self._load()

    # ---- model -> widgets ----

    def set_table(self, table_id: str) -> None:
        self.table = next(i for i in self.infos if i.id == table_id)
        self.seg.set_current(table_id)
        self._load()

    def _load(self) -> None:
        tm, info = self.tm, self.table
        self._rev = tm.revision
        values = tm.values(info.id)
        self.grid.load(values, tm.base(info.id), tm.rpm_axis, tm.map_axis, info.decimals,
                       info.lo, info.hi)
        m = self.model
        m.z = values.T
        m.z_range, m.z_label = (info.lo, info.hi), info.z_label
        m.sel = (self.grid.cur[0], self.grid.cur[1])
        m.touch()
        self.scene.refresh()
        n = int((np.abs(values - tm.base(info.id)) > 1e-6).sum())
        self.chip.set_text((f"{n} cells changed, not written. " if n else "")
                           + (tm.read_only_reason or
                              "Save .bin writes the edited tables to a file."))
        self.chip.write_btn.setEnabled(tm.can_write_chip())
        self._selection_changed()
        self._gen = -1

    def _selection_changed(self) -> None:
        tm, info = self.tm, self.table
        r, c = self.grid.cur
        v, b = float(tm.values(info.id)[r, c]), float(tm.base(info.id)[r, c])
        delta = (v - b) / b * 100 if b else 0.0
        sign = "+" if delta > 0 else ("−" if delta < 0 else "")
        self.sel_card.set(f"{tm.rpm_axis[r]:g} rpm × {tm.map_axis[c]:g} kPa",
                          f"{v:.{info.decimals}f}".replace("-", "−"), info.units,
                          f"base {b:.{info.decimals}f} · {sign}{abs(delta):.1f} %")
        if self.model.sel != (r, c):
            self.model.sel = (r, c)
            self.model.touch()
            self.scene.refresh()

    # ---- edits (all through the TableModel) ----

    def bump(self, steps: float) -> None:
        self.tm.adjust(self.table.id, self.grid.selection(), steps)

    def interpolate(self) -> None:
        self.tm.interpolate(self.table.id, self.grid.selection())

    def smooth(self) -> None:
        self.tm.smooth(self.table.id, self.grid.selection())

    def undo(self) -> None:
        self.tm.undo()

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save tables", "tables.bin", "Binary (*.bin)")
        if path:
            self.tm.save(path)

    # ---- frame ----

    def refresh(self, now: float) -> None:
        if self.tm.revision != self._rev:
            self._load()                    # surface follows an edit immediately
        self.scene.refresh()
        bus = self.session.bus
        rpm, kpa = bus.channel("rpm"), bus.channel("map")
        if rpm is None or kpa is None or rpm.gen == self._gen:
            return
        self._gen = rpm.gen
        info = self.table
        if rpm.stale(now) or kpa.stale(now):
            self.scene.set_live(None, None, now)
            self.grid.set_live(None)
            self.live_card.set("no live data", "---", info.units, "")
            return
        self.scene.set_live(rpm.value, kpa.value, now)
        _, _, _, i, j, value = self.model.locate(rpm.value, kpa.value)
        self.grid.set_live((i, j))
        trim = 0.0
        for cid in ("stft", "ltft"):
            st = bus.channel(cid)
            if st is not None and st.value == st.value:
                trim += st.value
        self.live_card.set(f"{rpm.value:.0f} rpm × {kpa.value:.0f} kPa",
                           f"{value:.{info.decimals}f}".replace("-", "−"), info.units,
                           f"interpolated · trim {'+' if trim > 0 else chr(0x2212) if trim < 0 else ''}"
                           f"{abs(trim):.1f} %")

    def key(self, event) -> bool:
        k, mods, text = event.key(), event.modifiers(), event.text()
        shift, ctrl = bool(mods & Qt.ShiftModifier), bool(mods & Qt.ControlModifier)
        if k == Qt.Key_Up:
            self.grid.move(-1, 0, shift)
        elif k == Qt.Key_Down:
            self.grid.move(1, 0, shift)
        elif k == Qt.Key_Left:
            self.grid.move(0, -1, shift)
        elif k == Qt.Key_Right:
            self.grid.move(0, 1, shift)
        elif ctrl and k == Qt.Key_Z:
            self.undo()
        elif ctrl and k == Qt.Key_S:
            self.save()
        elif text in ("=", "+"):
            self.bump(5 if text == "+" else 1)
        elif text in ("-", "_"):
            self.bump(-5 if text == "_" else -1)
        elif k == Qt.Key_I:
            self.interpolate()
        elif k == Qt.Key_S:
            self.smooth()
        elif k == Qt.Key_T:
            self.seg.cycle()
        else:
            return False
        return True

    # ---- layout ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        left = 560
        self.seg.setGeometry(8, 8, left, 44)
        gh = HEAD_H + self.grid.values.shape[0] * ROW_H + 4
        self.grid.setGeometry(8, 58, left, gh)
        x = 8
        for b in (self.dec_btn, self.inc_btn, self.int_btn, self.smo_btn, self.undo_btn):
            bw = b.sizeHint().width()
            b.setGeometry(x, 58 + gh + 6, bw, 44)
            x += bw + 8
        rx = 8 + left + 8
        rw = w - 8 - rx
        vh = 404
        self.scene.setGeometry(rx, 8, rw, vh)
        cy = 8 + vh + 8
        ch = h - 8 - cy
        cw = (rw - 16) // 3
        self.sel_card.setGeometry(rx, cy, cw, ch)
        self.live_card.setGeometry(rx + cw + 8, cy, cw, ch)
        self.chip.setGeometry(rx + 2 * cw + 16, cy, rw - 2 * cw - 16, ch)

    def retheme(self) -> None:
        self.grid.recolor()
        self.scene.refresh()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.end()
