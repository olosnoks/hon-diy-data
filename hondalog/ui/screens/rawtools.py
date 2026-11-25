"""F5, raw tools and the memory mapper (HANDOFF 5.5): hex grid with change
heat, byte inspector, hex console, the host for the guided wizard, and the
"Rank addresses" panel: W3's correlation finder on the sweep history (which
addresses followed a channel, or moved steadily one way), with the result
assigned to a channel through the map editor."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pyqtgraph.functions as fn
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QWidget

from ... import protocol
from .. import paint
from ..controls import Button, Card, HexEdit
from ..theme import T

HEX = [f"{i:02X}" for i in range(256)]
ROW_HEAD, COL_HEAD, ROW_H = 58, 22, 26


class HexGrid(QWidget):
    selected = Signal(int)

    def __init__(self, mapper, parent=None):
        super().__init__(parent)
        self.mapper = mapper
        self.sel = 0x14
        self.guess = np.zeros(256, dtype=bool)      # in the map, not verified
        self.setFixedHeight(COL_HEAD + 16 * ROW_H + 4)
        self.setCursor(Qt.PointingHandCursor)

    def cell_w(self) -> float:
        return (self.width() - ROW_HEAD - 4) / 16.0

    def select(self, addr: int) -> None:
        addr = max(0, min(255, addr))
        if addr != self.sel:
            self.sel = addr
            self.update()
            self.selected.emit(addr)

    def mousePressEvent(self, event) -> None:
        col = int((event.position().x() - ROW_HEAD - 2) // self.cell_w())
        row = int((event.position().y() - COL_HEAD - 2) // ROW_H)
        if 0 <= col < 16 and 0 <= row < 16:
            self.select(row * 16 + col)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        m = self.mapper
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        cw = self.cell_w()
        x0, y0 = ROW_HEAD + 2.0, COL_HEAD + 2.0
        head = T.font(13, 700, mono=True)
        p.setFont(head)
        p.setPen(T.fg)
        for c in range(16):
            p.drawText(QRectF(x0 + c * cw, 2, cw, COL_HEAD), Qt.AlignCenter, f"x{c:X}")
        for r in range(16):
            p.drawText(QRectF(2, y0 + r * ROW_H, ROW_HEAD - 6, ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"0x{r:X}0")
        static, old, now = (T.font(14, 500, mono=True), T.font(14, 700, mono=True),
                            T.font(14, 800, mono=True))
        cur, fresh, changes, guess = m.cur, m.fresh, m.changes, self.guess
        for a in range(256):
            r, c = divmod(a, 16)
            rect = QRectF(round(x0 + c * cw), y0 + r * ROW_H,
                          round(x0 + (c + 1) * cw) - round(x0 + c * cw), ROW_H)
            v = int(cur[a])
            if fresh[a]:
                p.fillRect(rect, T.warn)
                p.setPen(T.on_warn)
                p.setFont(now)
            elif changes[a]:
                p.fillRect(rect, T.sunk)
                p.fillRect(QRectF(rect.x(), rect.bottom() - 4, rect.width(), 4), T.accent)
                p.setPen(T.fg)
                p.setFont(old)
            else:
                p.setPen(T.fg2)
                p.setFont(static)
            p.drawText(rect, Qt.AlignCenter, HEX[v] if v >= 0 else "--")
            if guess[a]:
                p.setPen(T.cpen(T.on_warn if fresh[a] else T.unverified, 2, Qt.FlatCap,
                                dash=(2.0, 1.5)))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(3, 3, -3, -3))
        p.setPen(T.pen("sunk", 2))
        for c in range(17):
            x = round(x0 + c * cw)
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + 16 * ROW_H))
        for r in range(17):
            y = y0 + r * ROW_H
            p.drawLine(QPointF(x0, y), QPointF(w - 2, y))
        r, c = divmod(self.sel, 16)
        rect = QRectF(round(x0 + c * cw), y0 + r * ROW_H, cw, ROW_H)
        p.setPen(T.cpen(T.fg, 3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
        paint.frame(p, QRectF(self.rect()), 2, T.rule)
        p.end()


class ByteInspector(Card):
    def __init__(self, mapper, parent=None):
        super().__init__("", parent=parent)
        self.mapper = mapper
        self.addr = 0x14
        self._poly = fn.create_qpolygonf(120)
        self._pts = fn.ndarray_from_qpolygonf(self._poly)
        self._n = 0

    def set_addr(self, addr: int) -> None:
        self.addr = addr
        self.sync()

    def sync(self) -> None:
        hist = self.mapper.history(self.addr)[-120:]
        self._n = n = len(hist)
        w = self.width() - 24.0
        if n >= 2:
            lo, hi = float(hist.min()), float(max(hist.max(), hist.min() + 1))
            self._pts[:n, 0] = 12 + w - (n - 1 - np.arange(n)) * (w / 119.0)
            self._pts[:n, 1] = 110 + 34 - (hist - lo) / (hi - lo) * 28
            self._pts[n:] = self._pts[n - 1]
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        m, a = self.mapper, self.addr
        v = int(m.cur[a])
        y = 10.0
        paint.caption(p, x, y + 16, "Byte")
        big = T.font(34, 800, mono=True)
        p.setFont(big)
        p.setPen(T.fg)
        p.drawText(QRectF(x + 50, y, 200, 40), Qt.AlignVCenter | Qt.AlignLeft, f"0x{a:02X}")
        dec = T.font(18, 700, mono=True)
        dw = paint.text_w(dec, str(max(v, 0))) + 10
        p.drawText(QRectF(x, y, w - dw, 40), Qt.AlignVCenter | Qt.AlignRight,
                   HEX[v] if v >= 0 else "--")
        p.setFont(dec)
        p.drawText(QRectF(x, y + 6, w, 34), Qt.AlignVCenter | Qt.AlignRight,
                   str(v) if v >= 0 else "")
        y += 48
        bit = T.font(15, 700, mono=True)
        p.setFont(bit)
        for q in range(8):
            on = v >= 0 and (v >> (7 - q)) & 1
            r = QRectF(x + q * 34, y, 30, 30)
            paint.frame(p, r, 2, T.rule, T.fg if on else T.panel)
            p.setPen(T.bg if on else T.fg)
            p.drawText(r, Qt.AlignCenter, "1" if on else "0")
        p.setPen(T.fg)
        p.setFont(T.font(13, 700, mono=True))
        p.drawText(QRectF(x + 8 * 34 + 6, y, 120, 30), Qt.AlignVCenter | Qt.AlignLeft,
                   "bit 7 → 0")
        y += 38
        paint.track(p, QRectF(x, 110, w, 40), 0.0)
        if self._n >= 2:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(T.cpen(T.fg, 2, Qt.RoundCap, Qt.RoundJoin))
            p.setClipRect(QRectF(x + 2, 112, w - 4, 36))
            p.drawPolyline(self._poly)
            p.setClipping(False)
            p.setRenderHint(QPainter.Antialiasing, False)
        y = 156.0
        mono = T.font(13, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        known = v >= 0
        row = QRectF(x, y, w, 20)
        p.drawText(row, Qt.AlignVCenter | Qt.AlignLeft,
                   f"min {int(m.minv[a])}" if known else "min --")
        p.drawText(row, Qt.AlignCenter, f"max {int(m.maxv[a])}" if known else "max --")
        p.drawText(row, Qt.AlignVCenter | Qt.AlignRight, f"{int(m.changes[a])} changes")
        info = m.mapped(a)
        text = ("Not in the address map" if info is None else
                f"Address map: {info[0]} · " + ("verified" if info[1] else "unverified guess"))
        p.setFont(T.font(14, 700))
        p.drawText(QRectF(x, y + 22, w, 20), Qt.AlignVCenter | Qt.AlignLeft, text)


class WizardCard(Card):
    """Hosts whatever MapperSession.wizard() describes. No mapping logic here."""

    pickAddr = Signal(int)
    ROW = 44

    def __init__(self, mapper, parent=None):
        super().__init__("Map a channel · guided", parent=parent)
        self.mapper = mapper
        self.w = mapper.wizard()
        self.sel_addr: int | None = None
        self.back = Button("Back", "B", parent=self)
        self.retry = Button("Not it, try again", parent=self)
        self.primary = Button("Next", "N", kind="primary", parent=self)
        self.back.clicked.connect(self.go_back)
        self.retry.clicked.connect(self.mapper.wizard_retry)
        self.primary.clicked.connect(self.go_next)
        self._lines: list = []
        self._key = None
        self.setCursor(Qt.PointingHandCursor)

    def go_next(self) -> None:
        self.mapper.wizard_next(self.sel_addr)
        self.sync()

    def go_back(self) -> None:
        self.mapper.wizard_back()
        self.sync()

    def sync(self) -> None:
        w = self.mapper.wizard()
        key = (w.step, w.title, w.body, w.primary, w.target, w.target_index,
               round(w.busy, 2) if w.busy is not None else None,
               tuple((c.addr, round(c.score, 2), c.lo, c.hi) for c in w.candidates),
               self.sel_addr, tuple(w.targets))
        if key == self._key:
            return
        self._key, self.w = key, w
        self.back.setEnabled(w.can_back)
        self.retry.setEnabled(w.step >= 2)
        self.primary.set_text(w.primary)
        self._place()
        self.update()

    def _place(self) -> None:
        y = self.height() - 10 - 44
        self.back.setGeometry(10, y, self.back.sizeHint().width(), 44)
        pw = max(150, self.primary.sizeHint().width())
        self.primary.setGeometry(self.width() - 10 - pw, y, pw, 44)
        rw = self.retry.sizeHint().width()
        self.retry.setGeometry(self.width() - 10 - pw - 8 - rw, y, rw, 44)

    def resizeEvent(self, _event) -> None:
        self._place()

    def _rows_top(self) -> float:
        return 30 + 34 + 92 + 26

    def mousePressEvent(self, event) -> None:
        i = int((event.position().y() - self._rows_top()) // self.ROW)
        w = self.w
        if w.step == 0 and 0 <= i < len(w.targets):
            self.mapper.wizard_pick_target(i + self._first_target())
            self.sync()
        elif w.step == 3 and 0 <= i < len(w.candidates):
            self.sel_addr = w.candidates[i].addr
            self.pickAddr.emit(self.sel_addr)
            self.sync()

    def _first_target(self) -> int:
        room = self._room()
        return max(0, min(self.w.target_index - room // 2, len(self.w.targets) - room))

    def _room(self) -> int:
        return max(1, int((self.height() - 64 - self._rows_top()) // self.ROW))

    def paint_body(self, p, x, y, w, h) -> None:
        wz = self.w
        p.setFont(T.font(14, 700))
        p.setPen(T.fg)
        p.drawText(QRectF(x + 200, 6, w - 200, 20), Qt.AlignVCenter | Qt.AlignRight,
                   paint.elide(T.font(14, 700), f"Target: {wz.target}" if wz.target else "",
                               w - 200))
        # steps
        sy = 32.0
        sw = w / len(wz.steps)
        name, num = T.font(13, 700), T.font(13, 700, mono=True)
        for i, step in enumerate(wz.steps):
            box = QRectF(x + i * sw, sy, 26, 26)
            if i < wz.step:
                paint.frame(p, box, 3, T.ok, T.ok)
                paint.stroke(p, paint.CHECK, box.x() + 5, box.y() + 5, 16, T.panel, 2.5)
                ink = T.fg
            elif i == wz.step:
                paint.frame(p, box, 3, T.fg, T.fg)
                p.setFont(num)
                p.setPen(T.bg)
                p.drawText(box, Qt.AlignCenter, str(i + 1))
                ink = T.fg
            else:
                paint.frame(p, box, 3, T.rule, T.panel)
                p.setFont(num)
                p.setPen(T.fg)
                p.drawText(box, Qt.AlignCenter, str(i + 1))
                ink = T.fg2
            p.setFont(name)
            p.setPen(ink)
            p.drawText(QRectF(box.right() + 6, sy, sw - 36, 26), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(name, step, sw - 36))
        # instruction box, the one emphasised box on this screen
        by = sy + 34
        box = QRectF(x, by, w, 86)
        paint.frame(p, box, 4, T.rule, T.panel)
        tf = T.font(22, 800)
        p.setFont(tf)
        p.setPen(T.fg)
        p.drawText(QRectF(x + 14, by + 8, w - 28, 28), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(tf, wz.title, w - 28))
        bf = T.font(15, 600)
        if self._lines == [] or self._lines[0] != wz.body:
            self._lines = [wz.body] + paint.wrap(bf, wz.body, w - 28)[:2]
        p.setFont(bf)
        for i, line in enumerate(self._lines[1:]):
            p.drawText(QRectF(x + 14, by + 38 + 20 * i, w - 28, 20),
                       Qt.AlignVCenter | Qt.AlignLeft, line)
        if wz.busy is not None:
            paint.track(p, QRectF(x + w - 174, by + 12, 160, 14), wz.busy, T.accent)
        # table
        ty = self._rows_top() - 22
        head = T.font(12, 700, mono=True)
        p.setFont(head)
        p.setPen(T.fg)
        mono = T.font(15, 700, mono=True)
        if wz.step == 0:
            p.drawText(QRectF(x + 8, ty, w, 20), Qt.AlignVCenter | Qt.AlignLeft, wz.column)
            first, room = self._first_target(), self._room()
            for n, text in enumerate(wz.targets[first:first + room]):
                r = QRectF(x, ty + 22 + n * self.ROW, w, self.ROW)
                on = n + first == wz.target_index
                if on:
                    p.fillRect(r, T.fg)
                p.fillRect(QRectF(x, r.bottom() - 2, w, 2), T.sunk)
                p.setFont(mono)
                p.setPen(T.bg if on else T.fg)
                p.drawText(r.adjusted(8, 0, -8, -2), Qt.AlignVCenter | Qt.AlignLeft,
                           paint.elide(mono, text, w - 16))
        elif wz.candidates:
            cols = (8, 88, 358, w - 8)
            p.drawText(QRectF(x + cols[0], ty, 80, 20), Qt.AlignVCenter | Qt.AlignLeft, "ADDR")
            p.drawText(QRectF(x + cols[1], ty, 260, 20), Qt.AlignVCenter | Qt.AlignLeft, wz.column)
            p.drawText(QRectF(x + cols[2], ty, 120, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       "RANGE SEEN")
            p.drawText(QRectF(x, ty, cols[3], 20), Qt.AlignVCenter | Qt.AlignRight, "NOW")
            sel = self.sel_addr if self.sel_addr is not None else wz.candidates[0].addr
            for n, c in enumerate(wz.candidates[:self._room()]):
                r = QRectF(x, ty + 22 + n * self.ROW, w, self.ROW)
                on = c.addr == sel
                if on:
                    p.fillRect(r, T.fg)
                p.fillRect(QRectF(x, r.bottom() - 2, w, 2), T.sunk)
                ink, ground = (T.bg, T.fg) if on else (T.fg, T.panel)
                p.setFont(mono)
                p.setPen(ink)
                p.drawText(QRectF(x + cols[0], r.y(), 80, self.ROW - 2),
                           Qt.AlignVCenter | Qt.AlignLeft, f"0x{c.addr:02X}")
                paint.track(p, QRectF(x + cols[1], r.y() + 15, 210, 12), c.score, ink, ink,
                            ground if on else T.sunk)
                p.setPen(ink)
                p.drawText(QRectF(x + cols[1] + 218, r.y(), 50, self.ROW - 2),
                           Qt.AlignVCenter | Qt.AlignLeft, f"{c.score:.2f}")
                p.drawText(QRectF(x + cols[2], r.y(), 120, self.ROW - 2),
                           Qt.AlignVCenter | Qt.AlignLeft, f"{c.lo}–{c.hi}")
                v = int(self.mapper.cur[c.addr])
                p.drawText(QRectF(x, r.y(), cols[3], self.ROW - 2),
                           Qt.AlignVCenter | Qt.AlignRight, HEX[v] if v >= 0 else "--")


class RankCard(Card):
    """Rank addresses: pick what to compare with, see what followed it, give
    the best one to a channel. Every answer comes from the MapperSession
    (``rank_references``, ``rank``, ``assign_targets``, ``assign``)."""

    pickAddr = Signal(int)
    ROW = 40
    TOP = 8

    def __init__(self, mapper, parent=None):
        super().__init__("Rank addresses · what followed a signal", parent=parent)
        self.mapper = mapper
        self.refs: list = []
        self.ref = None                 # the RankReference in use
        self.rows: list = []
        self.sel = 0
        self.message = ""
        self.ranked = False             # a ranking has been asked for (an empty list then means "nothing")
        self.ref_btn = Button("", height=40, parent=self)
        self.rank_btn = Button("Rank now", "G", height=40, parent=self)
        self.assign_btn = Button("Assign to channel…", kind="primary", height=40, parent=self)
        self.ref_btn.clicked.connect(self.reference_menu)
        self.rank_btn.clicked.connect(self.rank)
        self.assign_btn.clicked.connect(self.assign_menu)
        self.ref_btn.setToolTip("What to rank against: a channel of the map (a proven one is "
                                "the best reference), or a shape you make by hand")
        #: asks before the map file is written; a test replaces it. (title, text) -> bool
        self.confirm = self._confirm_box
        self._lines: list = []
        self._wrapped = ""
        self.setCursor(Qt.PointingHandCursor)
        self.reload()

    # ---- what to rank against ----

    def reload(self) -> None:
        """The reference list again (the map was reloaded, or a channel verified)."""
        self.refs = list(self.mapper.rank_references())
        keys = [r.key for r in self.refs]
        if self.ref is None or self.ref.key not in keys:
            self.ref = self.refs[0] if self.refs else None
        else:
            self.ref = self.refs[keys.index(self.ref.key)]
        for row in self.rows:               # an assignment changed who owns an address
            info = self.mapper.mapped(row.addr)
            row.mapped = info[0] if info else ""
        self._sync()

    def set_reference(self, key: str) -> None:
        ref = next((r for r in self.refs if r.key == key), None)
        if ref is not None and ref is not self.ref:
            self.ref, self.rows, self.ranked, self.message = ref, [], False, ""
            self._sync()

    def reference_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("A channel of the map").setEnabled(False)
        for ref in self.refs:
            if ref.kind == "shape" and not any(a.text() == "A shape you make" for a in menu.actions()):
                menu.addSeparator()
                menu.addAction("A shape you make").setEnabled(False)
            text = ref.label + ("   verified" if ref.verified else "   unverified" if ref.kind == "channel"
                                else " while it sweeps")
            action = menu.addAction(text, lambda k=ref.key: self.set_reference(k))
            action.setCheckable(True)
            action.setChecked(ref is self.ref)
        menu.exec(QCursor.pos())

    def instruction(self) -> str:
        ref = self.ref
        if ref is None:
            return "Connect and sweep first: there is nothing to compare with yet."
        if ref.kind == "shape":
            way = "up" if ref.key.endswith("rise") else "down"
            return (f"Clear changes (C), then move one thing steadily {way} while it sweeps "
                    "(a slow pedal press, the engine warming). Then Rank now.")
        proven = "" if ref.verified else " It is unverified itself: a weak reference."
        return (f"Sweep while {ref.label} changes. The addresses that moved with it are listed; "
                f"its own bytes are left out.{proven}")

    # ---- ranking ----

    def rank(self) -> None:
        if self.ref is None:
            return
        self.rows = list(self.mapper.rank(self.ref.key, self.TOP))
        self.ranked, self.sel = True, 0
        self.message = "" if self.rows else self.mapper.console()
        self._sync()
        if self.rows:
            self.pickAddr.emit(self.rows[0].addr)

    def selected(self):
        return self.rows[self.sel] if 0 <= self.sel < len(self.rows) else None

    def _room(self) -> int:
        return max(1, int((self.assign_btn.y() - 6 - self._rows_top()) // self.ROW))

    def _rows_top(self) -> float:
        return 30 + 46 + 46 + 22

    def mousePressEvent(self, event) -> None:
        i = int((event.position().y() - self._rows_top()) // self.ROW)
        if 0 <= i < min(len(self.rows), self._room()):
            self.sel = i
            self.pickAddr.emit(self.rows[i].addr)
            self._sync()

    # ---- assigning ----

    def _confirm_box(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Cancel, self)
        yes = box.addButton("Write it", QMessageBox.AcceptRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is yes

    def assign_menu(self) -> None:
        row = self.selected()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction(f"Give {row.label} to").setEnabled(False)
        for name, text in self.mapper.assign_targets():
            menu.addAction(text, lambda n=name: self.assign(n))
        menu.exec(QCursor.pos())

    def assign(self, channel: str) -> bool:
        """Write the selected address to ``channel`` through the map editor, after asking."""
        row = self.selected()
        if row is None:
            return False
        was = next((text for name, text in self.mapper.assign_targets() if name == channel), channel)
        if not self.confirm("Assign to channel", f"{was}\n\nwill read {row.label} from now on. It is marked "
                            "unverified and the map's version moves on. The formula is not changed."):
            return False
        self.message = self.mapper.assign(row, channel)
        self.reload()
        return True

    # ---- widgets ----

    def _sync(self) -> None:
        ref = self.ref
        self.ref_btn.set_text("Follows: " + (ref.label if ref is not None else "nothing yet")
                              if ref is None or ref.kind == "channel" else ref.label)
        available = self.mapper.available() and ref is not None
        self.ref_btn.setEnabled(bool(self.refs))
        self.rank_btn.setEnabled(available)
        self.assign_btn.setEnabled(self.selected() is not None)
        self._place()
        self.update()

    def _place(self) -> None:
        w = self.width()
        rw = self.rank_btn.sizeHint().width()
        self.rank_btn.setGeometry(w - 10 - rw, 30, rw, 40)
        self.ref_btn.setGeometry(10, 30, w - 20 - rw - 8, 40)
        aw = self.assign_btn.sizeHint().width()
        self.assign_btn.setGeometry(w - 10 - aw, self.height() - 10 - 40, aw, 40)

    def resizeEvent(self, _event) -> None:
        self._place()

    def paint_body(self, p, x, y, w, h) -> None:
        body = T.font(14, 600)
        text = self.instruction()
        if text != self._wrapped:
            self._wrapped, self._lines = text, paint.wrap(body, text, w)[:2]
        p.setFont(body)
        p.setPen(T.fg)
        for i, line in enumerate(self._lines):
            p.drawText(QRectF(x, 76 + 19 * i, w, 19), Qt.AlignVCenter | Qt.AlignLeft, line)
        ty = self._rows_top() - 22
        head, mono, small = T.font(12, 700, mono=True), T.font(15, 700, mono=True), T.font(13, 600)
        cols = (8, 122, 310, 392)
        p.setFont(head)
        for cx, title in zip(cols, ("ADDRESS", "FOLLOWS IT", "RANGE", "NOW")):
            p.drawText(QRectF(x + cx, ty, 190, 20), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.drawText(QRectF(x, ty, w - 8, 20), Qt.AlignVCenter | Qt.AlignRight, "IN THE MAP AS")
        p.fillRect(QRectF(x, ty + 20, w, 2), T.sunk)
        rows = self.rows[:self._room()]
        if not rows:
            p.setFont(body)
            p.setPen(T.fg2)
            empty = (self.message or "Nothing moved with it in these sweeps.") if self.ranked else \
                "Not ranked yet."
            for i, line in enumerate(paint.wrap(body, empty, w - 16)[:3]):
                p.drawText(QRectF(x + 8, ty + 28 + 19 * i, w - 16, 19), Qt.AlignVCenter | Qt.AlignLeft, line)
        for n, row in enumerate(rows):
            r = QRectF(x, ty + 22 + n * self.ROW, w, self.ROW)
            on = n == self.sel
            if on:
                p.fillRect(r, T.fg)
            p.fillRect(QRectF(x, r.bottom() - 2, w, 2), T.sunk)
            ink, ground = (T.bg, T.fg) if on else (T.fg, T.panel)
            p.setFont(mono)
            p.setPen(ink)
            line = QRectF(r.x(), r.y(), r.width(), self.ROW - 2)
            p.drawText(line.adjusted(cols[0], 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, row.label)
            paint.track(p, QRectF(x + cols[1], r.y() + 13, 118, 12), row.score, ink, ink,
                        ground if on else T.sunk)
            # the sign says which way the raw value runs against the reference
            p.drawText(QRectF(x + cols[1] + 126, r.y(), 70, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{row.score:.2f}{'↑' if row.r >= 0 else '↓'}")
            p.drawText(QRectF(x + cols[2], r.y(), 90, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{row.lo}–{row.hi}")
            v = int(self.mapper.cur[row.addr])
            p.drawText(QRectF(x + cols[3], r.y(), 40, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       HEX[v] if v >= 0 else "--")
            p.setFont(small)
            p.drawText(line.adjusted(cols[3] + 40, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight,
                       paint.elide(small, row.mapped or "—", w - cols[3] - 56))
        # the result of the last ranking or assignment, left of the button
        if self.message and rows:
            p.setFont(small)
            p.setPen(T.fg)
            room = self.assign_btn.x() - x - 12
            for i, line in enumerate(paint.wrap(small, self.message, room)[:2]):
                p.drawText(QRectF(x, self.assign_btn.y() + 2 + 18 * i, room, 18),
                           Qt.AlignVCenter | Qt.AlignLeft, paint.elide(small, line, room))
        else:
            sel = self.selected()
            if sel is not None and (sel.formula or sel.note):
                p.setFont(small)
                p.setPen(T.fg2)
                room = self.assign_btn.x() - x - 12
                hint = "; ".join(t for t in (f"fits {sel.formula}" if sel.formula else "", sel.note) if t)
                p.drawText(QRectF(x, self.assign_btn.y(), room, 40), Qt.AlignVCenter | Qt.AlignLeft,
                           paint.elide(small, hint, room))


class ConsoleCard(Card):
    def __init__(self, parent=None):
        super().__init__("Hex console", parent=parent)
        self.rx = ""

    def set_rx(self, text: str) -> None:
        if text != self.rx:
            self.rx = text
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        mono = T.font(15, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(x, 28, 28, 40), Qt.AlignVCenter | Qt.AlignLeft, "TX")
        p.drawText(QRectF(x, 72, w, 22), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(mono, "RX  " + (self.rx or "\u2014"), w))


class RawScreen(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.mapper = session.mapper
        self.sweep_btn = Button("Sweep", "S", icon="off", parent=self)
        self.sweep_btn.setCheckable(True)
        self.clear_btn = Button("Clear changes", "C", parent=self)
        self.export_btn = Button("Export", parent=self)
        self.grid = HexGrid(self.mapper, self)
        self.inspector = ByteInspector(self.mapper, self)
        self.wizard = WizardCard(self.mapper, self)
        # the correlation finder takes the wizard's place while it is in use
        self.rank_btn = Button("Rank", "R", parent=self)
        self.rank_btn.setCheckable(True)
        self.rank_btn.setToolTip("Rank addresses: which bytes followed a channel, or a shape "
                                 "you made, in the sweeps so far")
        self.rank_btn.setVisible(hasattr(self.mapper, "rank"))
        self.rank = RankCard(self.mapper, self) if hasattr(self.mapper, "rank") else None
        if self.rank is not None:
            self.rank.hide()
            self.rank.pickAddr.connect(lambda addr: self.grid.select(addr))
            self.rank_btn.clicked.connect(lambda: self.show_rank(self.rank_btn.isChecked()))
        self.console = ConsoleCard(self)
        self.tx = HexEdit(self.console)
        self.tx.setText("20 05 14 01")
        self.sum_btn = Button("+ checksum", parent=self.console)
        self.sum_btn.setCheckable(True)
        self.sum_btn.setChecked(True)
        # W1's read-only guard: off = only read requests go out
        self.expert_btn = Button("Expert", parent=self.console)
        self.expert_btn.setCheckable(True)
        self.expert_btn.setToolTip("Expert unlock: let the console send frames that are not "
                                   "reads. Every such frame is written to the log header.")
        self.expert_btn.setVisible(hasattr(self.mapper, "set_expert"))
        self.expert_btn.clicked.connect(self.toggle_expert)
        self.send_btn = Button("Send", kind="primary", parent=self.console)
        self.sweep_btn.clicked.connect(self.toggle_sweep)
        self.clear_btn.clicked.connect(self.mapper.clear_changes)
        self.export_btn.clicked.connect(self.export)
        self.send_btn.clicked.connect(self.send)
        self.tx.returnPressed.connect(self.send)
        self.grid.selected.connect(self.inspector.set_addr)
        self.wizard.pickAddr.connect(self.grid.select)
        self._rev = -1
        self._status = ""
        self._wiz_t = -1.0
        self._map_version: tuple | None = None

    # ---- actions ----

    def toggle_expert(self) -> None:
        setter = getattr(self.mapper, "set_expert", None)
        if setter is not None:
            setter(self.expert_btn.isChecked())

    def toggle_sweep(self) -> None:
        self.mapper.set_sweeping(not self.mapper.sweeping)
        self._rev = -1

    def show_rank(self, on: bool) -> None:
        """R: the Rank addresses panel in the wizard's place, or the wizard back."""
        if self.rank is None:
            return
        self.rank_btn.setChecked(on)
        self.rank.setVisible(on)
        self.wizard.setVisible(not on)
        if on:
            self.rank.reload()

    def send(self) -> None:
        try:
            data = protocol.parse_hex(self.tx.text())
        except ValueError:
            self.console.set_rx("!! not hex")
            return
        self.mapper.send(data, self.sum_btn.isChecked())

    def export(self) -> None:
        name = f"scan_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Export sweep", name, "Text (*.txt)")
        if path:
            self.mapper.export(path)

    # ---- frame ----

    def refresh(self, now: float) -> None:
        m = self.mapper
        version = (self.session.bus.version, getattr(m, "map_revision", 0))
        if version != self._map_version:
            self._map_version = version
            for a in range(256):
                info = m.mapped(a)
                self.grid.guess[a] = info is not None and not info[1]
            self._rev = -1
            if self.rank is not None and self.rank.isVisible():
                self.rank.reload()          # the map changed: other references, other "in the map as"
        if m.revision != self._rev:
            self._rev = m.revision
            self.sweep_btn.setChecked(m.sweeping)
            self.sweep_btn.set_text("Sweeping" if m.sweeping else "Sweep")
            self.sweep_btn.icon_path = "dot" if m.sweeping else "off"
            self.sweep_btn.setEnabled(m.available())
            self.send_btn.setEnabled(m.available())
            self.expert_btn.setEnabled(m.available())
            self.expert_btn.setChecked(bool(getattr(m, "expert", False)))
            self._status = (f"0x00–0xFF · sweep {m.sweeps} · {m.sweep_s:.1f} s / sweep"
                            if m.available() else
                            "OBD2 only · Honda reads are off" if getattr(m, "obd2_only", bool)()
                            else "not connected · click the ECU chip")
            self.console.set_rx(m.console())
            self.grid.update()
            self.inspector.sync()
            if self.rank is not None and self.rank.isVisible():
                self.rank._sync()           # "now" values, and Rank now once there is a link
            self.update()
        if now - self._wiz_t >= 0.2:
            self._wiz_t = now
            self.wizard.sync()

    def key(self, event) -> bool:
        k = event.key()
        g = self.grid
        if k == Qt.Key_Left:
            g.select(g.sel - 1)
        elif k == Qt.Key_Right:
            g.select(g.sel + 1)
        elif k == Qt.Key_Up:
            g.select(g.sel - 16 if g.sel >= 16 else g.sel)
        elif k == Qt.Key_Down:
            g.select(g.sel + 16 if g.sel < 240 else g.sel)
        elif k == Qt.Key_S:
            self.toggle_sweep()
        elif k == Qt.Key_C:
            self.mapper.clear_changes()
        elif k == Qt.Key_R and self.rank is not None:
            self.show_rank(not self.rank.isVisible())
        elif k == Qt.Key_G and self.rank is not None:
            self.show_rank(True)
            self.rank.rank()
        elif k == Qt.Key_N and self.wizard.isVisible():
            self.wizard.go_next()
        elif k == Qt.Key_B and self.wizard.isVisible():
            self.wizard.go_back()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.send()
        else:
            return False
        return True

    # ---- layout / paint ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        left = 772
        x = 8
        for btn in (self.sweep_btn, self.clear_btn, self.rank_btn):
            if not btn.isVisibleTo(self):
                continue
            bw = max(btn.sizeHint().width(), 120 if btn is self.sweep_btn else 0)
            btn.setGeometry(x, 8, bw, 44)
            x += bw + 8
        self._status_x = x
        ew = self.export_btn.sizeHint().width()
        self.export_btn.setGeometry(8 + left - ew, 8, ew, 44)
        self.grid.setGeometry(8, 58, left, self.grid.height())
        gy = 58 + self.grid.height()
        self._legend_y = gy + 4
        cy = gy + 38
        self.console.setGeometry(8, cy, left, max(96, h - 8 - cy))
        sw, bw = self.send_btn.sizeHint().width(), self.sum_btn.sizeHint().width()
        self.send_btn.setGeometry(left - 12 - sw, 28, sw, 40)
        self.sum_btn.setGeometry(left - 12 - sw - 8 - bw, 28, bw, 40)
        ew = self.expert_btn.sizeHint().width() + 8 if self.expert_btn.isVisibleTo(self.console) else 0
        if ew:
            self.expert_btn.setGeometry(left - 12 - sw - 8 - bw - ew, 28, ew - 8, 40)
        self.tx.setGeometry(38, 28, left - 12 - sw - 8 - bw - ew - 8 - 38, 40)
        for b in (self.send_btn, self.sum_btn, self.expert_btn):
            b.setFixedHeight(40)
        rx = 8 + left + 8
        rw = w - 8 - rx
        self.inspector.setGeometry(rx, 8, rw, 208)
        self.wizard.setGeometry(rx, 224, rw, h - 8 - 224)
        if self.rank is not None:
            self.rank.setGeometry(rx, 224, rw, h - 8 - 224)

    def retheme(self) -> None:
        self.tx.restyle()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        mono = T.font(13, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        room = self.export_btn.x() - self._status_x - 8
        status = self._status
        if paint.text_w(mono, status) > room:           # the Rank button took the room: drop the range
            status = status.replace("0x00–0xFF · ", "")
        p.drawText(QRectF(self._status_x, 8, room, 44), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(mono, status, room))
        # legend
        y = self._legend_y
        x = 8.0
        body = T.font(13, 700)
        cell = T.font(14, 700, mono=True)
        for kind, text in (("now", "changing now"), ("old", "changed earlier"),
                           ("map", "in address map, unverified"), ("sel", "selected")):
            r = QRectF(x, y, 34, 26)
            if kind == "now":
                p.fillRect(r, T.warn)
            elif kind == "old":
                p.fillRect(r, T.sunk)
                p.fillRect(QRectF(x, y + 22, 34, 4), T.accent)
            elif kind == "map":
                p.setPen(T.cpen(T.unverified, 2, Qt.FlatCap, dash=(2.0, 1.5)))
                p.drawRect(r.adjusted(2, 2, -2, -2))
            else:
                p.setPen(T.cpen(T.fg, 3))
                p.drawRect(r.adjusted(1.5, 1.5, -1.5, -1.5))
            p.setFont(cell)
            p.setPen(T.on_warn if kind == "now" else T.fg)
            p.drawText(r, Qt.AlignCenter, "3F")
            p.setFont(body)
            p.setPen(T.fg)
            p.drawText(QRectF(x + 42, y, 300, 26), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += 42 + paint.text_w(body, text) + 18
        p.end()
