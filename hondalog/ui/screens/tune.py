"""F6, tuning table editor shell (HANDOFF 5.6): exact values in a 2D grid,
shape in the 3D surface next to it. All table logic is in the TableModel.

"Open ROM…" and "Definition…" (a TunerPro .xdf or a hondalog .toml) change
what the model holds; the screen rebuilds its table list, grid and surface
when ``TableModel.layout`` moves. Saving, the checksum line, undo / redo and
"Burn image…" all go through the model, which is W5's ``RomSession`` with
its safety checks; without a ROM the sample tables stay.

The live cell, its trail on the grid and the point and trail on the surface
come from W5's ``TableTracer`` (``hondalog.tuning.trace``), fed with RPM and
MAP from the bus."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPolygonF
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QWidget

from ...tuning import AxisError, TableTracer

from .. import paint, theme
from ..controls import Button, Card, Segment
from ..scene3d import MapModel, MapScene
from ..theme import T

HEAD_W, HEAD_H, ROW_H = 60, 24, 24
TRAIL_S = 7.0                   # how long the trail on the table and the surface lasts
ROM_FILTER = "ROM images (*.bin *.rom *.BIN *.ROM);;All files (*)"
DEFINITION_FILTER = "Definitions (*.xdf *.toml *.XDF);;All files (*)"


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
        self.trail: np.ndarray | None = None    # (n_rpm, n_map) 0..1: where the engine has just been
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

    def set_live(self, cell, trail: np.ndarray | None = None) -> None:
        """The cell the engine is in and the tracer's fading heat for the
        cells it has just been through (same shape as the values, 0..1)."""
        if trail is not None:
            trail = np.round(trail * 8) / 8         # eight steps: repaint only when a bar moves
            if not trail.any():
                trail = None
        same = (trail is None and self.trail is None) or (
            trail is not None and self.trail is not None and np.array_equal(trail, self.trail))
        if cell != self.live or not same:
            self.live, self.trail = cell, trail
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
        trail = self.trail if self.trail is not None and self.trail.shape == (nr, nc) else None
        p.setFont(cell)
        for r in range(nr):
            heat, text, changed = self._heat[r], self._text[r], self._changed[r]
            for c in range(nc):
                rect = self._rect(r, c)
                p.fillRect(rect, heat[c])
                if trail is not None and trail[r, c] > 0:
                    # the trail: a bar along the bottom that shortens as the visit ages
                    p.fillRect(QRectF(rect.x() + 2, rect.bottom() - 3,
                                      (rect.width() - 4) * float(trail[r, c]), 3), ink)
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


class RomCard(Card):
    """The ROM: what is open, its checksum, and the file actions."""

    def __init__(self, parent=None):
        super().__init__("ROM · chip", parent=parent)
        self.text = ""
        self.alert = False              # the first line is a problem: draw it in the alarm colour
        self._lines: list = []
        self.open_btn = Button("Open ROM…", height=40, parent=self)
        self.def_btn = Button("Definition…", height=40, parent=self)
        self.save_btn = Button("Save .bin", height=40, parent=self)
        self.burn_btn = Button("Burn image…", height=40, parent=self)
        self.write_btn = Button("Write chip", height=40, parent=self)
        self.write_btn.setToolTip("Write to chip: needs a P28 with a burner (minipro) or an emulator")
        self.write_btn.setEnabled(False)
        self.open_btn.setToolTip("Open a ROM image (.bin) with the definition loaded last")
        self.def_btn.setToolTip("Load a definition: a TunerPro .xdf or a hondalog .toml. "
                                "The open ROM is read again with it.")
        self.burn_btn.setToolTip("Write the chip-ready image to a file. Refused when the "
                                 "checksum or the signature is wrong.")

    def set_text(self, text: str, alert: bool = False) -> None:
        if (text, alert) != (self.text, self.alert):
            self.text, self.alert = text, alert
            self._lines = []
            self.update()

    def resizeEvent(self, _event) -> None:
        w = self.width() - 20
        half, third = (w - 6) // 2, (w - 12) // 3
        y = self.height() - 10 - 40
        self.save_btn.setGeometry(10, y, third, 40)
        self.burn_btn.setGeometry(10 + third + 6, y, third, 40)
        self.write_btn.setGeometry(10 + 2 * third + 12, y, w - 2 * third - 12, 40)
        y -= 46
        self.open_btn.setGeometry(10, y, half, 40)
        self.def_btn.setGeometry(10 + half + 6, y, w - half - 6, 40)
        self._lines = []

    def paint_body(self, p, x, y, w, h) -> None:
        font = T.font(14, 700)
        if not self._lines:
            self._lines = [line for part in self.text.split("\n") for line in paint.wrap(font, part, w)]
        p.setFont(font)
        room = max(1, int((self.open_btn.y() - y - 4) // 18))
        for i, line in enumerate(self._lines[:room]):
            if i == room - 1 and len(self._lines) > room:
                line = paint.elide(font, line + " …", w)
            p.setPen(T.alarm if self.alert and i == 0 else T.fg)
            p.drawText(QRectF(x, y + 18 * i - 2, w, 18), Qt.AlignVCenter | Qt.AlignLeft, line)


class TuneScreen(QWidget):
    def __init__(self, session, settings=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.settings = settings
        self.tm = session.tables
        self.infos = list(self.tm.tables())
        self.table = self.infos[0]
        self.seg = Segment([(i.id, i.name) for i in self.infos], self.table.id, parent=self)
        self.seg.picked.connect(self.set_table)
        # a definition with more tables than fit a segment gets one button and a menu
        self.pick_btn = Button("", "T", parent=self)
        self.pick_btn.clicked.connect(self.table_menu)
        self.grid = TableGrid(self)
        self.grid.moved.connect(self._selection_changed)
        self.model = MapModel(self.tm.rpm_axis, self.tm.map_axis)
        self.model.logged = False
        self.scene = MapScene(self.model, dist=4.9, el=0.46,
                              key_items=(("live", "live point + trail"), ("sel", "selected cell")),
                              hint="", label_px=(13, 15), wire_skip=3, settings=settings,
                              parent=self)
        self.dec_btn = Button("−", parent=self)
        self.inc_btn = Button("+", parent=self)
        self.int_btn = Button("Interpolate", "I", parent=self)
        self.smo_btn = Button("Smooth", "S", parent=self)
        self.undo_btn = Button("Undo", "^Z", parent=self)
        self.redo_btn = Button("Redo", parent=self)
        self.redo_btn.setToolTip("Redo (Ctrl+Y)")
        self.dec_btn.clicked.connect(lambda: self.bump(-1))
        self.inc_btn.clicked.connect(lambda: self.bump(1))
        self.int_btn.clicked.connect(self.interpolate)
        self.smo_btn.clicked.connect(self.smooth)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self.sel_card = ValueCard("Selected cell", self)
        self.live_card = ValueCard("Live cell · interpolated", self)
        self.chip = RomCard(self)
        self.chip.open_btn.clicked.connect(lambda: self.open_rom())
        self.chip.def_btn.clicked.connect(lambda: self.load_definition())
        self.chip.save_btn.clicked.connect(lambda: self.save())
        self.chip.burn_btn.clicked.connect(lambda: self.build_burn_image())
        #: asks before unsaved edits are dropped; a test replaces it. (title, text) -> bool
        self.confirm = self._confirm_box
        self.message = ""               # the result of the last file action, shown in the ROM card
        self._problem = False
        self.tracer: TableTracer | None = None
        self._rev = -1
        self._layout = getattr(self.tm, "layout", 0)
        self._gen = -1
        self._make_tracer()
        self._load()

    # ---- model -> widgets ----

    def _make_tracer(self) -> None:
        """W5's tracer on the axes of the tables that are showing: the ROM
        table's own when a ROM is open, the sample grid's otherwise."""
        editor = getattr(self.tm, "editor", None)
        try:
            self.tracer = (TableTracer.for_table(editor, trail_seconds=TRAIL_S) if editor is not None
                           else TableTracer(self.tm.rpm_axis, self.tm.map_axis, trail_seconds=TRAIL_S))
        except AxisError:
            self.tracer = None          # an axis that does not rise: no live cursor on this table
        self.grid.set_live(None)
        self.scene.set_track(None, ())

    def _rebuild(self) -> None:
        """The model holds other tables (a ROM or a definition was opened)."""
        tm = self.tm
        self._layout = getattr(tm, "layout", 0)
        self.infos = list(tm.tables())
        keep = next((i for i in self.infos if i.id == self.table.id), None)
        self.table = keep or self.infos[0]
        self.seg.set_options([(i.id, i.name) for i in self.infos], self.table.id)
        self.seg.set_current(self.table.id)
        self.grid.cur = self.grid.anchor = (0, 0)
        self.model = MapModel(tm.rpm_axis, tm.map_axis)
        self.model.logged = False
        self.scene.set_model(self.model)
        self._make_tracer()
        self._load()
        self.resizeEvent(None)

    def set_table(self, table_id: str) -> None:
        self.table = next(i for i in self.infos if i.id == table_id)
        self.seg.set_current(table_id)
        self._load()

    def next_table(self) -> None:
        ids = [i.id for i in self.infos]
        self.set_table(ids[(ids.index(self.table.id) + 1) % len(ids)])

    def table_menu(self) -> None:
        menu = QMenu(self)
        for info in self.infos:
            action = menu.addAction(info.name, lambda tid=info.id: self.set_table(tid))
            action.setCheckable(True)
            action.setChecked(info.id == self.table.id)
        menu.exec(QCursor.pos())

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
        self.pick_btn.set_text(f"{info.name}  ({self.infos.index(info) + 1} of {len(self.infos)})")
        self._sync_rom_card()
        self._selection_changed()
        self._gen = -1

    def _sync_rom_card(self) -> None:
        """File, definition, checksum and what is unsaved, from the model."""
        if getattr(self.tm, "layout", 0) != self._layout:
            self._rebuild()                 # the tables changed under us: that ends here again
            return
        tm, info = self.tm, self.table
        n = int((np.abs(tm.values(info.id) - tm.base(info.id)) > 1e-6).sum())
        status = getattr(tm, "status", None)
        state = status() if status is not None else None
        lines = []
        if self.message:
            lines.append(self.message)
        if state is not None and state["loaded"]:
            lines.append(f"{state['rom']} · {state['definition']}")
            lines.append(state["checksum"] + (" · not saved" if state["dirty"] else "")
                         + (f" · {n} cells changed here" if n else ""))
            lines += state["warnings"][:1]
        else:
            if state is not None and state["problem"] and not self.message:
                lines.append(state["problem"])
            if state is not None and state["rom"]:
                lines.append(f"{state['rom']} needs its definition: Definition…")
            if n:
                lines.append(f"{n} cells changed, not written.")
            lines.append(tm.read_only_reason or "Save .bin writes the edited tables to a file.")
        alert = self._problem or bool(state is not None and state["problem"] and not self.message
                                      and not state["loaded"])
        self.chip.set_text("\n".join(lines), alert)
        can_open = hasattr(tm, "open")
        loaded = bool(state is not None and state["loaded"])
        self.chip.open_btn.setEnabled(can_open)
        self.chip.def_btn.setEnabled(can_open)
        self.chip.save_btn.setEnabled(tm.writable)
        self.chip.burn_btn.setEnabled(loaded)
        self.chip.write_btn.setEnabled(tm.can_write_chip())
        for b in (self.dec_btn, self.inc_btn, self.int_btn, self.smo_btn):
            b.setEnabled(tm.writable)
        self.undo_btn.setEnabled(tm.writable and bool(getattr(tm, "can_undo", True)))
        self.redo_btn.setEnabled(bool(getattr(tm, "can_redo", False)))

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

    def _say(self, text: str, problem: bool = False) -> None:
        self.message, self._problem = text, problem
        self._sync_rom_card()

    # ---- edits (all through the TableModel) ----

    def bump(self, steps: float) -> None:
        self.tm.adjust(self.table.id, self.grid.selection(), steps)

    def interpolate(self) -> None:
        self.tm.interpolate(self.table.id, self.grid.selection())

    def smooth(self) -> None:
        self.tm.smooth(self.table.id, self.grid.selection())

    def undo(self) -> None:
        self.tm.undo()

    def redo(self) -> None:
        redo = getattr(self.tm, "redo", None)
        if redo is not None:
            redo()

    # ---- files (all through the TableModel, which is the RomSession) ----

    def _confirm_box(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Cancel, self)
        yes = box.addButton("Discard them", QMessageBox.DestructiveRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is yes

    def _may_replace(self, what: str) -> bool:
        """Unsaved edits are about to go: ask."""
        if not getattr(self.tm, "dirty", False):
            return True
        return self.confirm("Unsaved changes", f"The ROM has edits that are not saved.\n\n{what} "
                                               "drops them. Save .bin first if you want them.")

    def _folder(self, key: str) -> str:
        return str(self.settings.value(f"tune/{key}", "")) if self.settings is not None else ""

    def _remember(self, key: str, path) -> None:
        if self.settings is not None:
            self.settings.setValue(f"tune/{key}", str(path))

    def open_rom(self, path=None) -> bool:
        """Open ROM…: a ROM image with the definition that was loaded last
        (the built-in demo definition until one is). True if it opened."""
        if not hasattr(self.tm, "open") or not self._may_replace("Opening another ROM"):
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open a ROM image", self._folder("rom"), ROM_FILTER)
            if not path:
                return False
        definition = getattr(self.tm, "definition_path", None) or self._folder("definition") or None
        problem = self.tm.open(path, definition)
        self._remember("rom", path)
        if problem:
            self._say(f"{problem}", problem=True)
            return False
        self._say(f"Opened {getattr(path, 'name', None) or str(path).rsplit('/', 1)[-1]}")
        return True

    def load_definition(self, path=None) -> bool:
        """Definition…: a TunerPro .xdf or a hondalog .toml. The table list is
        rebuilt from it; the open ROM is read again with it."""
        if not hasattr(self.tm, "load_definition"):
            return False
        if getattr(self.tm, "loaded", False) and not self._may_replace("Loading another definition"):
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Load a ROM definition", self._folder("definition"),
                                                  DEFINITION_FILTER)
            if not path:
                return False
        problem = self.tm.load_definition(path)
        if problem:
            self._say(problem, problem=True)
            return False
        self._remember("definition", path)
        name = getattr(path, "name", None) or str(path).rsplit("/", 1)[-1]
        self._say(f"Definition {name}: {len(self.tm.tables())} tables" if getattr(self.tm, "loaded", False)
                  else f"Definition {name} loaded. Now Open ROM…")
        return True

    def save(self, path=None) -> bool:
        """Save .bin: RomSession.save (never onto the opened ROM, checksum fixed, snapshot)."""
        if not self.tm.writable:
            return False
        if path is None:
            default = getattr(self.tm, "default_save_path", lambda: None)()
            path, _ = QFileDialog.getSaveFileName(self, "Save tables", str(default or "tables.bin"),
                                                  "Binary (*.bin)")
            if not path:
                return False
        try:
            text = self.tm.save(path)
        except ValueError as exc:
            self._say(f"Not saved: {exc}", problem=True)
            return False
        self._say(text or f"Saved {path}")
        return True

    def build_burn_image(self, path=None) -> bool:
        """Burn image…: the chip-ready file, if the session's checks pass."""
        build = getattr(self.tm, "burn_image", None)
        if build is None or not getattr(self.tm, "loaded", False):
            return False
        if path is None:
            default = getattr(self.tm, "default_save_path", lambda: None)()
            suggestion = str(default.with_suffix(".chip.bin")) if default is not None else "chip.bin"
            path, _ = QFileDialog.getSaveFileName(self, "Build the burn image", suggestion, "Binary (*.bin)")
            if not path:
                return False
        try:
            self._say(build(path))
        except ValueError as exc:
            self._say(str(exc), problem=True)
            return False
        return True

    # ---- frame ----

    def refresh(self, now: float) -> None:
        if getattr(self.tm, "layout", 0) != self._layout:
            self._rebuild()                 # another ROM or definition: new table list and axes
        elif self.tm.revision != self._rev:
            self._load()                    # surface follows an edit immediately
        self.scene.refresh()
        bus = self.session.bus
        rpm, kpa = bus.channel("rpm"), bus.channel("map")
        if rpm is None or kpa is None or rpm.gen == self._gen:
            return
        self._gen = rpm.gen
        info, tracer = self.table, self.tracer
        if tracer is None or rpm.stale(now) or kpa.stale(now):
            if tracer is not None:
                tracer.update(now, None, None)      # a gap: no dwell is counted across it
            self.scene.set_track(None, ())
            self.grid.set_live(None)
            self.live_card.set("no live data", "---", info.units, "")
            return
        # W5's tracer: where RPM and MAP fall on this table's axes, and where they have just been
        pos = tracer.update(now, rpm.value, kpa.value)
        row, col = pos.nearest                      # the tracer's rows are MAP, its columns RPM
        self.grid.set_live((col, row), tracer.heat(now).T)
        self.scene.set_track((pos.col, pos.row), tracer.trail(now))
        values = self.tm.values(info.id)
        value = sum(w * float(values[c, r]) for r, c, w in pos.cells)
        trim = 0.0
        for cid in ("stft", "ltft"):
            st = bus.channel(cid)
            if st is not None and st.value == st.value:
                trim += st.value
        # off the table the tracer holds the value of the edge cell: say so
        where = "at edge · " if pos.clamped else ""
        self.live_card.set(f"{rpm.value:.0f} rpm × {kpa.value:.0f} kPa",
                           f"{value:.{info.decimals}f}".replace("-", "−"), info.units,
                           f"{where}trim {'+' if trim > 0 else chr(0x2212) if trim < 0 else ''}"
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
        elif ctrl and (k == Qt.Key_Y or (shift and k == Qt.Key_Z)):
            self.redo()
        elif ctrl and k == Qt.Key_Z:
            self.undo()
        elif ctrl and k == Qt.Key_S:
            self.save()
        elif ctrl and k == Qt.Key_O:
            self.open_rom()
        elif ctrl and k == Qt.Key_D:
            self.load_definition()
        elif ctrl and k == Qt.Key_B:
            self.build_burn_image()
        elif text in ("=", "+"):
            self.bump(5 if text == "+" else 1)
        elif text in ("-", "_"):
            self.bump(-5 if text == "_" else -1)
        elif k == Qt.Key_I:
            self.interpolate()
        elif k == Qt.Key_S:
            self.smooth()
        elif k == Qt.Key_T:
            self.next_table()
        else:
            return False
        return True

    # ---- layout ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        left = 560
        many = len(self.infos) > 4          # more tables than a 560 px segment can name
        self.seg.setVisible(not many)
        self.pick_btn.setVisible(many)
        self.seg.setGeometry(8, 8, left, 44)
        self.pick_btn.setGeometry(8, 8, left, 44)
        rows = self.grid.values.shape[0]
        gh = HEAD_H + rows * ROW_H + 4
        self.grid.setGeometry(8, 58, left, gh)
        x = 8
        for b in (self.dec_btn, self.inc_btn, self.int_btn, self.smo_btn, self.undo_btn, self.redo_btn):
            bw = b.sizeHint().width()
            b.setGeometry(x, 58 + gh + 6, bw, 44)
            x += bw + 6
        rx = 8 + left + 8
        rw = w - 8 - rx
        vh = 404
        self.scene.setGeometry(rx, 8, rw, vh)
        cy = 8 + vh + 8
        ch = h - 8 - cy
        cw = 214                            # the two value cards; the ROM card takes the rest
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
