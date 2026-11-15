"""Log viewer: open a log (.hlog, old .csv, .msl, .mlg), scrub through it, see the markers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QMenu, QMessageBox, QPushButton, QSlider, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..addressmap import Channel
from ..blackbox.channels import bb_channel
from ..logger import log_dir, read_log
from . import theme
from .plots import StripCharts

MAX_PLOTS = 8


class Replay(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.amap = None
        self.log = None
        self._channels: dict = {}
        self._plotted: list[str] = []
        self._index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        bar = QHBoxLayout()
        open_btn = QPushButton("Open log…")
        open_btn.clicked.connect(self.open_dialog)
        bar.addWidget(open_btn)
        self.chan_btn = QToolButton(text="Plot channels")
        self.chan_btn.setPopupMode(QToolButton.InstantPopup)
        self.chan_menu = QMenu(self)
        self.chan_btn.setMenu(self.chan_menu)
        bar.addWidget(self.chan_btn)
        self.prev_btn = QPushButton("◀ marker")
        self.prev_btn.clicked.connect(lambda: self._jump_marker(-1))
        self.next_btn = QPushButton("marker ▶")
        self.next_btn.clicked.connect(lambda: self._jump_marker(+1))
        fit_btn = QPushButton("Show all")
        fit_btn.clicked.connect(self._show_all)
        self.export_btn = QPushButton("Export…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_dialog)
        for b in (self.prev_btn, self.next_btn, fit_btn, self.export_btn):
            bar.addWidget(b)
        self.file_label = QLabel("No log open. Logs are in " + str(log_dir()))
        self.file_label.setStyleSheet(f"color:{theme.DIM};")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.file_label, 1)
        root.addLayout(bar)

        body = QHBoxLayout()
        self.charts = StripCharts(interactive=True)
        self.charts.cursorMoved.connect(self._cursor_from_chart)
        body.addWidget(self.charts, 1)

        side = QVBoxLayout()
        self.time_label = QLabel("t = --")
        self.time_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        side.addWidget(self.time_label)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Channel", "Value at cursor"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(21)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        side.addWidget(self.table, 3)
        side.addWidget(QLabel("Markers (click to jump)"))
        self.marker_list = QListWidget()
        self.marker_list.currentRowChanged.connect(self._marker_clicked)
        side.addWidget(self.marker_list, 1)
        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet(f"color:{theme.DIM}; font-size: 8.5pt;")
        side.addWidget(self.meta_label)
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(290)
        side.setContentsMargins(0, 0, 0, 0)
        body.addWidget(holder)
        root.addLayout(body, 1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._set_index)
        root.addWidget(self.slider)

    def set_map(self, amap) -> None:
        self.amap = amap

    # -- loading --

    def open_dialog(self) -> None:
        start = self.settings.value("replay/dir", str(log_dir()))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open log", start, "Logs and black box captures (*.hlog *.csv *.msl *.mlg *.bbx);;"
            "Black box capture (*.bbx);;All files (*)")
        if path:
            self.open(path)

    def export_dialog(self) -> None:
        """Save the open log in another tool's format (MegaLogViewer, CSV, NumPy)."""
        if self.log is None:
            return
        from ..data import open_log
        from ..export import FORMATS, export_log
        kinds = ";;".join(f"{label} (*{ext})" for ext, label in FORMATS.items())
        start = str(Path(self.log.path).with_suffix(".mlg"))
        path, chosen = QFileDialog.getSaveFileName(self, "Export log", start, kinds)
        if not path:
            return
        if not Path(path).suffix:
            path += chosen[chosen.rfind("*") + 1:-1]
        try:
            with open_log(self.log.path) as log:
                export_log(log, path)
        except Exception as exc:
            QMessageBox.warning(self, "hondalog", f"Could not export to {path}:\n{exc}")
            return
        self.file_label.setText(f"Exported {path}")

    def open(self, path) -> bool:
        try:
            log = read_log(path)
            if len(log.t) == 0:
                raise ValueError("no rows")
        except Exception as exc:
            QMessageBox.warning(self, "hondalog", f"Could not open {path}:\n{exc}")
            return False
        self.log = log
        self.settings.setValue("replay/dir", str(Path(path).parent))
        self._channels = {}
        for name in log.names:
            ch = (self.amap.channel(name) if self.amap else None) or bb_channel(name)
            if ch is None:                  # column the current map does not know
                kind = "flag" if name == "link" else "value"
                ch = Channel(name, 0, label=name, decimals=0 if kind == "flag" else 2,
                             kind=kind, status="verified")
            self._channels[name] = ch

        self.file_label.setText(str(path))
        self.export_btn.setEnabled(True)
        meta = log.meta
        self.meta_label.setText("\n".join(
            f"{k}: {meta[k]}" for k in ("started", "ecu_id", "address_map_version",
                                        "stall_trigger", "stall_time_s", "bb_verdict",
                                        "bb_alignment", "bb_notes") if k in meta))

        default = [n for n in log.names if (c := self._channels[n]).plot and c.kind == "value"]
        default = default or [n for n in log.names if n != "link"][:5]
        self.chan_menu.clear()
        for name in log.names:
            ch = self._channels[name]
            act = self.chan_menu.addAction(ch.label)
            act.setCheckable(True)
            act.setChecked(name in default)
            act.setData(name)
            act.toggled.connect(self._plots_changed)

        self.table.setRowCount(len(log.names))
        for row, name in enumerate(log.names):
            ch = self._channels[name]
            label = f"{ch.label} ({ch.units})" if ch.units else ch.label
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem("--"))

        self.marker_list.blockSignals(True)
        self.marker_list.clear()
        for t, text in log.markers:
            self.marker_list.addItem(f"{t:8.1f} s   {text}")
        self.marker_list.blockSignals(False)

        self._apply_plots(default)
        self.slider.blockSignals(True)
        self.slider.setRange(0, len(log.t) - 1)
        self.slider.setEnabled(True)
        self.slider.blockSignals(False)
        # start at the stall if this is a stall capture, else at the beginning
        start_t = float(meta["stall_time_s"]) if "stall_time_s" in meta else log.t[0]
        self._goto_time(start_t)
        return True

    def _apply_plots(self, names) -> None:
        self._plotted = names[:MAX_PLOTS]
        self.charts.clear_markers()
        self.charts.set_channels([self._channels[n] for n in self._plotted])
        for name in self._plotted:
            self.charts.set_series(name, self.log.t, self.log.data[name], fit=True)
        for t, text in self.log.markers:
            self.charts.add_marker(t, text)
        self._show_all()
        self._set_index(self._index)

    def _plots_changed(self) -> None:
        self._apply_plots([a.data() for a in self.chan_menu.actions() if a.isChecked()])

    def _show_all(self) -> None:
        if self.log is not None:
            self.charts.set_x_range(float(self.log.t[0]), float(self.log.t[-1]) + 1e-3)

    # -- scrubbing --

    def _goto_time(self, t: float) -> None:
        if self.log is None:
            return
        i = int(np.clip(np.searchsorted(self.log.t, t), 0, len(self.log.t) - 1))
        if i > 0 and abs(self.log.t[i - 1] - t) < abs(self.log.t[i] - t):
            i -= 1
        self.slider.blockSignals(True)
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self._set_index(i)

    def _cursor_from_chart(self, t: float) -> None:
        self._goto_time(t)

    def _set_index(self, i: int) -> None:
        if self.log is None:
            return
        i = max(0, min(int(i), len(self.log.t) - 1))
        self._index = i
        t = float(self.log.t[i])
        self.charts.set_cursor(t)
        self.time_label.setText(f"t = {t:.2f} s   (row {i + 1}/{len(self.log.t)})")
        for row, name in enumerate(self.log.names):
            value = self.log.data[name][i]
            self.table.item(row, 1).setText(self._channels[name].format(value))
        for name in self._plotted:
            self.charts.set_readout(name, self.log.data[name][i])

    def _marker_clicked(self, row: int) -> None:
        if self.log is not None and 0 <= row < len(self.log.markers):
            self._goto_time(self.log.markers[row][0])

    def _jump_marker(self, direction: int) -> None:
        if self.log is None or not self.log.markers:
            return
        now = float(self.log.t[self._index])
        times = [t for t, _ in self.log.markers]
        if direction > 0:
            later = [t for t in times if t > now + 1e-6]
            target = later[0] if later else times[-1]
        else:
            earlier = [t for t in times if t < now - 1e-6]
            target = earlier[-1] if earlier else times[0]
        self._goto_time(target)
