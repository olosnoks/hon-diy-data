"""F3, Map 3D: observed map surface, time-in-cell bars, stall paths (HANDOFF 5.3)."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme
from ..controls import Card, Segment
from ..scene3d import MapModel, MapScene
from ..theme import T

Z_KEYS = {"inj": ("INJ ms", (0.0, 10.0), 2), "ign": ("IGN °BTDC", (-5.0, 45.0), 1),
          "trim": ("TRIM %", (-12.0, 12.0), 1)}


class OperatingCard(Card):
    def __init__(self, parent=None):
        super().__init__("Operating point", parent=parent)
        self.cells = [("RPM", "---"), ("MAP kPa", "---"), ("", "---"), ("", "---")]

    def set(self, cells) -> None:
        if cells != self.cells:
            self.cells = cells
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        lab, val = T.font(12, 700), T.font(38, 800)
        for n, (label, value) in enumerate(self.cells):
            r, c = divmod(n, 2)
            cx, cy = x + c * (w / 2 + 4), y + r * 56
            p.setFont(lab)
            p.setPen(T.fg2)
            p.drawText(QRectF(cx, cy, w / 2, 14), Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setFont(val)
            p.setPen(T.fg)
            p.drawText(QRectF(cx, cy + 12, w / 2, 42), Qt.AlignVCenter | Qt.AlignLeft, value)


class LegendCard(Card):
    def __init__(self, parent=None):
        super().__init__("", parent=parent)
        self.stops = theme.SEQ_RAMP
        self.labels = ("", "", "")
        self.swatches = None

    def set(self, title, stops, labels, swatches=None) -> None:
        self.title, self.stops, self.labels, self.swatches = title, stops, labels, swatches
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        bar = QRectF(x, y + 2, w, 22)
        if self.swatches:
            sw = w / len(self.swatches)
            for i, token in enumerate(self.swatches):
                p.fillRect(QRectF(x + i * sw, y + 2, sw, 22), T.color(token))
        else:
            grad = QLinearGradient(bar.topLeft(), bar.topRight())
            for i, stop in enumerate(self.stops):
                grad.setColorAt(i / (len(self.stops) - 1), QColor(stop))
            p.fillRect(bar, grad)
        paint.frame(p, bar, 2, T.rule2)
        mono = T.font(13, 700, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        row = QRectF(x, y + 28, w, 18)
        for text, flag in zip(self.labels, (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)):
            p.drawText(row, Qt.AlignVCenter | flag, text)
        p.fillRect(QRectF(x, y + 52, 16, 16), T.nodata)
        paint.frame(p, QRectF(x, y + 52, 16, 16), 2, T.rule2)
        p.setFont(T.font(13, 600))
        p.setPen(T.fg)
        p.drawText(QRectF(x + 24, y + 50, w - 24, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   "grey = no samples logged in that cell yet")


class CoverageCard(Card):
    def __init__(self, parent=None):
        super().__init__("Coverage", parent=parent)
        self.seen, self.total = 0, 200

    def set(self, seen: int, total: int) -> None:
        if (seen, total) != (self.seen, self.total):
            self.seen, self.total = seen, total
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        p.setFont(T.font(14, 700))
        p.setPen(T.fg)
        p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignLeft, "Cells with data")
        p.setFont(T.font(14, 700, mono=True))
        p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignRight,
                   f"{self.seen} / {self.total}")
        paint.track(p, QRectF(x, y + 24, w, 12), self.seen / max(1, self.total))


class Map3DScreen(QWidget):
    def __init__(self, session, settings=None, parent=None):
        super().__init__(parent)
        self.session = session
        obs = session.obsmap
        self.model = MapModel(obs.rpm_axis, obs.map_axis)
        self.scene = MapScene(self.model, settings=settings, parent=self)
        self.mode, self.zkey, self.color_by = "surface", "inj", "trim"
        self.view_seg = Segment([("surface", "Surface"), ("hist", "Time in cell"),
                                 ("stall", "Stall paths")], "surface", parent=self)
        self.z_seg = Segment([("inj", "Injector ms"), ("ign", "Ignition °"),
                              ("trim", "Fuel trim %")], "inj", parent=self)
        self.col_seg = Segment([("z", "Same as Z"), ("trim", "Fuel trim"), ("hits", "Samples")],
                               "trim", parent=self)
        self.cam_seg = Segment([("iso", "Iso"), ("top", "Top"), ("rpm", "RPM side"),
                                ("map", "MAP side")], momentary=True, parent=self)
        self.view_seg.picked.connect(self._set_mode)
        self.z_seg.picked.connect(self._set_z)
        self.col_seg.picked.connect(self._set_color)
        self.cam_seg.picked.connect(self.scene.set_preset)
        self.op = OperatingCard(self)
        self.legend = LegendCard(self)
        self.coverage = CoverageCard(self)
        self._caps: list = []
        self._obs_rev = -1
        self._built_t = -10.0
        self._gen = -1
        self._path_names: list = []
        self._apply()

    # ---- modes ----

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "stall":
            paths = self.session.stall_paths()
            self._path_names = [name for name, _, _ in paths]
            self.model.paths = [(rpm, kpa) for _, rpm, kpa in paths]
        self._apply()

    def _set_z(self, key: str) -> None:
        self.zkey = key
        self._apply()

    def _set_color(self, key: str) -> None:
        self.color_by = key
        self._apply()

    def _apply(self) -> None:
        m = self.model
        m.mode, m.color_by = self.mode, self.color_by
        m.z_label, m.z_range, _ = Z_KEYS[self.zkey]
        self._load_cells()
        zname = m.z_label
        lo, hi = m.z_range
        if self.mode == "stall":
            n = len(self.model.paths)
            title = "Stall approach paths"
            caption = ("Last 30 s before each saved stall, drawn on the observed map. Diamond = "
                       "where the engine died. Bigger bead = closer to the stall."
                       if n else "No stall captures saved yet. They appear here once the "
                                 "stall catcher has fired.")
            names = [x.replace("stall_", "").replace(".csv", "")[-11:] for x in self._path_names]
            self.legend.set("Paths: one colour per stall file", None,
                            tuple((names + ["", "", ""])[:3]),
                            ("series1", "series2", "series3")[:max(1, n)])
        elif self.mode == "hist":
            title = "Time in cell"
            caption = ("Bar height and colour = samples logged in each RPM x MAP cell (log "
                       "scale). Shows where the engine actually lives.")
            self.legend.set("Samples per cell (log)", theme.SEQ_RAMP, ("1", "30", "900"))
        else:
            title = f"Observed map: {zname} over RPM x MAP"
            caption = ("Height = mean logged value per cell"
                       + (". Sample data" if self.session.demo else " this session")
                       + ". Built from the log, so it works on the stock P2P before any chip.")
            if self.color_by == "trim":
                self.legend.set("Colour: fuel trim (short + long)", theme.DIV_RAMP,
                                ("−12 % rich", "0", "+12 % lean"))
            elif self.color_by == "hits":
                self.legend.set("Colour: samples per cell (log)", theme.SEQ_RAMP,
                                ("1", "30", "900"))
            else:
                self.legend.set(f"Colour: {zname}", theme.SEQ_RAMP,
                                (f"{lo:g}", f"{(lo + hi) / 2:g}", f"{hi:g}"))
        self.scene.key_items = ((("stall", "diamond = where the engine died"),)
                                if self.mode == "stall" else
                                (("live", "live point + 7 s trail"),))
        self.scene.set_text(title, caption)
        self.scene.overlay.update()
        self.scene.refresh()
        self._gen = -1

    def _load_cells(self) -> None:
        obs = self.session.obsmap
        self._obs_rev = obs.revision
        m = self.model
        m.hits = obs.hits().copy()
        m.trim = obs.mean("trim")
        m.z = obs.mean(self.zkey)
        m.touch()
        self.coverage.set(int((m.hits > 0).sum()), m.hits.size)

    # ---- layout ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        side = 374
        sx = w - 8 - side
        self.scene.setGeometry(8, 8, sx - 16, h - 16)
        y = 8
        self._caps = []
        for cap, seg in (("View", self.view_seg), ("Height (Z)", self.z_seg),
                         ("Colour", self.col_seg)):
            self._caps.append((cap, sx, y))
            seg.setGeometry(sx, y + 17, side, 44)
            y += 70
        self.op.setGeometry(sx, y - 1, side, 146)
        y += 153
        self.legend.setGeometry(sx, y, side, 110)
        y += 118
        self._caps.append(("Camera", sx, y))
        self.cam_seg.setGeometry(sx, y + 17, side, 44)
        y += 70
        self.coverage.setGeometry(sx, y - 1, side, max(74, h - 8 - y + 1))

    def retheme(self) -> None:
        self.scene.refresh()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        for cap, x, y in self._caps:
            paint.caption(p, x, y, cap)
        p.end()

    # ---- frame ----

    def refresh(self, now: float) -> None:
        obs = self.session.obsmap
        if obs.revision != self._obs_rev and now - self._built_t >= 1.0:
            self._built_t = now             # surface rebuild at most once a second
            self._load_cells()
        self.scene.refresh()
        bus = self.session.bus
        rpm, kpa = bus.channel("rpm"), bus.channel("map")
        if rpm is None or kpa is None:
            return
        if rpm.gen == self._gen:
            return
        self._gen = rpm.gen
        if rpm.stale(now) or kpa.stale(now):
            self.scene.set_live(None, None, now)
            self.op.set([("RPM", "---"), ("MAP kPa", "---"),
                         (f"{self.model.z_title()} at point", "---"), ("Samples in cell", "---")])
            return
        self.scene.set_live(rpm.value, kpa.value, now)
        _, _, _, i, j, value = self.model.locate(rpm.value, kpa.value)
        hits = int(self.model.hits[j, i])
        decimals = Z_KEYS[self.zkey][2]
        ztext = str(hits) if self.mode == "hist" else (
            f"{value:.{decimals}f}".replace("-", "−") if hits or not self.model.logged
            else "---")
        self.op.set([("RPM", f"{rpm.value:.0f}"), ("MAP kPa", f"{kpa.value:.0f}"),
                     (f"{self.model.z_title()} at point", ztext),
                     (f"Samples in cell R{i + 1} M{j + 1}", str(hits))])

    def key(self, event) -> bool:
        k = event.key()
        step = math.radians(5)
        if k == Qt.Key_Left:
            self.scene.orbit(-step, 0)
        elif k == Qt.Key_Right:
            self.scene.orbit(step, 0)
        elif k == Qt.Key_Up:
            self.scene.orbit(0, step)
        elif k == Qt.Key_Down:
            self.scene.orbit(0, -step)
        elif k == Qt.Key_PageUp:
            self.scene.zoom(0.9)
        elif k == Qt.Key_PageDown:
            self.scene.zoom(1.1)
        elif k in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            self.scene.set_preset(("iso", "top", "rpm", "map")[k - Qt.Key_1])
        elif k == Qt.Key_V:
            self.view_seg.cycle()
        elif k == Qt.Key_Z:
            self.z_seg.cycle()
        elif k == Qt.Key_C:
            self.col_seg.cycle()
        else:
            return False
        return True
