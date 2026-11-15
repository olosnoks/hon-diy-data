"""Live dashboard: gauges on the left, strip charts on the right."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .plots import StripCharts
from .widgets import GaugeTile, History, Lamp

WINDOWS = [("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300)]
MAX_PLOTS = 8


class StallBanner(QWidget):
    openRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"StallBanner {{ background:{theme.BAD}; border-radius:6px; }}"
                           "QLabel { color:#000; font-size:13pt; font-weight:bold; }")
        self._path = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel()
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        open_btn = QPushButton("Open in replay")
        open_btn.clicked.connect(lambda: self.openRequested.emit(self._path))
        close_btn = QPushButton("Dismiss")
        close_btn.clicked.connect(self.hide)
        lay.addWidget(open_btn)
        lay.addWidget(close_btn)
        self.hide()

    def show_event(self, event) -> None:
        self._path = str(event.path)
        why = "RPM dropped to 0" if event.reason == "rpm" else "ECU link lost"
        self.label.setText(f"STALL at {event.t:.1f} s ({why}). Saved {event.path.name}")
        self.setToolTip(self._path)
        self.show()


class Dashboard(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.amap = None
        self.history: History | None = None
        self.window_s = int(settings.value("dash/window", 60))
        self._gauges: dict = {}
        self._lamps: dict = {}
        self._plotted: list[str] = []
        self._latest_t = 0.0
        self._latest = None
        self._frame = 0
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        self.banner = StallBanner()
        root.addWidget(self.banner)
        body = QHBoxLayout()
        root.addLayout(body, 1)

        self.left = QWidget()
        self.left.setFixedWidth(372)
        self.left_lay = QVBoxLayout(self.left)
        self.left_lay.setContentsMargins(0, 0, 0, 0)
        self.gauge_grid = QGridLayout()
        self.gauge_grid.setSpacing(6)
        self.left_lay.addLayout(self.gauge_grid, 1)
        self.lamp_row = QHBoxLayout()
        self.lamp_row.setSpacing(6)
        self.left_lay.addLayout(self.lamp_row)
        body.addWidget(self.left)

        right = QVBoxLayout()
        bar = QHBoxLayout()
        self.chan_btn = QToolButton(text="Plot channels")
        self.chan_btn.setPopupMode(QToolButton.InstantPopup)
        self.chan_menu = QMenu(self)
        self.chan_btn.setMenu(self.chan_menu)
        bar.addWidget(self.chan_btn)
        bar.addWidget(QLabel("Window"))
        self.win_combo = QComboBox()
        for text, seconds in WINDOWS:
            self.win_combo.addItem(text, seconds)
        self.win_combo.setCurrentIndex(max(0, self.win_combo.findData(self.window_s)))
        self.win_combo.currentIndexChanged.connect(self._window_changed)
        bar.addWidget(self.win_combo)
        self.hint = QLabel(f"<span style='color:{theme.WARN}'>?</span> = address not verified on this ECU")
        self.hint.setStyleSheet(f"color:{theme.DIM};")
        bar.addStretch(1)
        bar.addWidget(self.hint)
        right.addLayout(bar)
        self.charts = StripCharts(interactive=False)
        right.addWidget(self.charts, 1)
        body.addLayout(right, 1)

    # -- setup --

    def set_map(self, amap) -> None:
        self.amap = amap
        self.history = History(amap.names)
        self._latest_t = 0.0
        self._latest = None
        self.charts.clear_markers()

        for store, layout in ((self._gauges, self.gauge_grid), (self._lamps, self.lamp_row)):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().setParent(None)
                    item.widget().deleteLater()
            store.clear()
        gauges = [c for c in amap.channels if c.gauge and c.kind == "value"]
        cols = 2
        for i, ch in enumerate(gauges):
            tile = GaugeTile(ch, theme.SERIES[i % len(theme.SERIES)])
            self.gauge_grid.addWidget(tile, i // cols, i % cols)
            self._gauges[ch.name] = tile
        for ch in amap.channels:
            if ch.kind == "flag":
                lamp = Lamp(ch)
                self.lamp_row.addWidget(lamp)
                self._lamps[ch.name] = lamp

        saved = self.settings.value("dash/plots", None)
        if isinstance(saved, str):
            saved = [saved]
        names = [n for n in (saved or []) if amap.channel(n)] or \
                [c.name for c in amap.channels if c.plot]
        self.chan_menu.clear()
        for ch in amap.channels:
            act = self.chan_menu.addAction(f"{ch.label}  ({ch.units})" if ch.units else ch.label)
            act.setCheckable(True)
            act.setChecked(ch.name in names)
            act.setData(ch.name)
            act.toggled.connect(self._plots_changed)
        self._apply_plots(names)

    def _apply_plots(self, names) -> None:
        self._plotted = names[:MAX_PLOTS]
        self.charts.set_channels([self.amap.channel(n) for n in self._plotted])
        self._dirty = True

    def _plots_changed(self) -> None:
        names = [a.data() for a in self.chan_menu.actions() if a.isChecked()]
        self.settings.setValue("dash/plots", names)
        self._apply_plots(names)

    def _window_changed(self) -> None:
        self.window_s = self.win_combo.currentData()
        self.settings.setValue("dash/window", self.window_s)
        self._dirty = True

    # -- data --

    def add_sample(self, sample) -> None:
        self.history.append(sample.t, sample.values if sample.link else {})
        self._latest_t = sample.t
        self._latest = sample
        self._dirty = True

    def add_marker(self, t: float, text: str) -> None:
        self.charts.add_marker(t, text)

    def refresh(self) -> None:
        """Called at ~20 Hz by the main window. Cheap when nothing changed."""
        if not self._dirty or self.history is None or self.history.n == 0:
            return
        self._dirty = False
        values = self._latest.values if self._latest.link else {}
        for name, tile in self._gauges.items():
            tile.set_value(values.get(name))
        for name, lamp in self._lamps.items():
            lamp.set_value(values.get(name))
        if not self.isVisible():
            return
        t1 = self._latest_t
        t0 = t1 - self.window_s
        start, t = self.history.window(t0)
        self._frame += 1
        for name in self._plotted:
            self.charts.set_series(name, t, self.history.series(name, start))
            if self._frame % 4 == 0:        # text layout is the costly part
                self.charts.set_readout(name, values.get(name))
        self.charts.set_x_range(t0, t1)
        self.charts.clear_markers(older_than=t1 - 700)
