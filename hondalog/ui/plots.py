"""Stacked strip charts shared by the live dashboard and the log viewer."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal

from . import theme

pg.setConfigOptions(antialias=False, background=theme.BG, foreground=theme.DIM)


class StripCharts(pg.GraphicsLayoutWidget):
    """One plot row per channel, all sharing the time axis."""

    cursorMoved = Signal(float)

    def __init__(self, interactive: bool = False, parent=None):
        super().__init__(parent)
        self.interactive = interactive
        self.ci.layout.setSpacing(2)
        self.ci.layout.setContentsMargins(2, 2, 2, 2)
        self.channels: list = []
        self._plots: dict = {}       # name -> PlotItem
        self._curves: dict = {}
        self._labels: dict = {}
        self._ranges: dict = {}      # name -> [lo, hi] currently shown
        self._readouts: dict = {}
        self._markers: list = []     # (t, text, [InfiniteLine per plot])
        self._cursors: list = []
        self._cursor_t: float | None = None
        self._syncing = False
        if interactive:
            self.scene().sigMouseClicked.connect(self._clicked)

    # -- layout --

    def set_channels(self, channels) -> None:
        marks = [(t, text) for t, text, _ in self._markers]
        cursor = self._cursor_t
        self.clear()
        self._plots, self._curves, self._labels, self._ranges = {}, {}, {}, {}
        self._readouts = {}
        self._markers, self._cursors = [], []
        self.channels = list(channels)
        first = None
        for row, ch in enumerate(self.channels):
            color = theme.SERIES[row % len(theme.SERIES)]
            plot = self.addPlot(row=row, col=0)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            # No grid: pyqtgraph draws grid lines through the axis items, which
            # then repaint with every curve update and eat a whole CPU core.
            plot.setMouseEnabled(x=self.interactive, y=False)
            plot.getAxis("left").setWidth(46)
            plot.setClipToView(True)
            plot.setDownsampling(auto=True, mode="peak")
            if row < len(self.channels) - 1:
                plot.hideAxis("bottom")
            else:
                plot.setLabel("bottom", "time", units="s")
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            curve = plot.plot(pen=pg.mkPen(color, width=1.5), connect="finite")
            label = pg.LabelItem(justify="left")
            label.setParentItem(plot.vb)
            label.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(6, 0))
            self._plots[ch.name], self._curves[ch.name] = plot, curve
            self._labels[ch.name] = (label, color)
            self._ranges[ch.name] = [float(ch.min), float(ch.max)]
            self._apply_range(ch.name)
            self.set_readout(ch.name, None)
        for t, text in marks:
            self.add_marker(t, text)
        if cursor is not None:
            self.set_cursor(cursor)

    # -- data --

    def set_series(self, name: str, t, y, fit: bool = False) -> None:
        curve = self._curves.get(name)
        if curve is None:
            return
        curve.setData(t, y)
        if len(y) == 0:
            return
        with np.errstate(all="ignore"):
            lo, hi = np.nanmin(y), np.nanmax(y)
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return
        rng = self._ranges[name]
        if fit:
            new = [float(lo), float(hi if hi > lo else lo + 1)]
        else:
            new = [min(rng[0], float(lo)), max(rng[1], float(hi))]
        if new != rng:
            self._ranges[name] = new
            self._apply_range(name)

    def _apply_range(self, name: str) -> None:
        lo, hi = self._ranges[name]
        span = (hi - lo) or 1.0
        # headroom on top keeps the trace clear of the readout label
        self._plots[name].setYRange(lo - 0.04 * span, hi + 0.22 * span, padding=0)

    def set_readout(self, name: str, value) -> None:
        label, color = self._labels[name]
        ch = next(c for c in self.channels if c.name == name)
        text = ch.format(value)
        if self._readouts.get(name) == text:
            return                      # setHtml is not free at 20 Hz
        self._readouts[name] = text
        flag = "" if ch.verified else f" <span style='color:{theme.WARN}'>?</span>"
        label.setText(f"<span style='color:{color}'>{ch.label}</span>{flag} "
                      f"<b style='color:{theme.TEXT}'>{text}</b> {ch.units}", size="9pt")

    def set_x_range(self, lo: float, hi: float) -> None:
        if self._plots:
            next(iter(self._plots.values())).setXRange(lo, hi, padding=0)

    # -- markers and cursor --

    def add_marker(self, t: float, text: str) -> None:
        stall = "STALL" in text
        pen = pg.mkPen(theme.BAD if stall else theme.WARN, width=1.5, style=Qt.DashLine)
        lines = []
        for i, plot in enumerate(self._plots.values()):
            opts = {}
            if i == 0:
                opts = dict(label=text[:40], labelOpts=dict(
                    position=0.04, color=theme.BAD if stall else theme.WARN,
                    movable=False, fill=(0, 0, 0, 150)))
            line = pg.InfiniteLine(pos=t, angle=90, pen=pen, movable=False, **opts)
            plot.addItem(line, ignoreBounds=True)
            lines.append(line)
        self._markers.append((t, text, lines))

    def clear_markers(self, older_than: float | None = None) -> None:
        keep = []
        for t, text, lines in self._markers:
            if older_than is not None and t >= older_than:
                keep.append((t, text, lines))
                continue
            for plot, line in zip(self._plots.values(), lines):
                plot.removeItem(line)
        self._markers = keep

    def set_cursor(self, t: float) -> None:
        self._cursor_t = t
        if not self._cursors:
            pen = pg.mkPen("#ffffff", width=1)
            for plot in self._plots.values():
                line = pg.InfiniteLine(pos=t, angle=90, pen=pen, movable=self.interactive)
                line.setZValue(50)
                line.sigPositionChanged.connect(self._cursor_dragged)
                plot.addItem(line, ignoreBounds=True)
                self._cursors.append(line)
        self._syncing = True
        for line in self._cursors:
            line.setValue(t)
        self._syncing = False

    def _cursor_dragged(self, line) -> None:
        if not self._syncing:
            self.set_cursor(line.value())
            self.cursorMoved.emit(line.value())

    def _clicked(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        for plot in self._plots.values():
            if plot.vb.sceneBoundingRect().contains(event.scenePos()):
                t = plot.vb.mapSceneToView(event.scenePos()).x()
                self.set_cursor(t)
                self.cursorMoved.emit(t)
                return
