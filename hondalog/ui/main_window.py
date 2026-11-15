"""Main window: connection bar on top, three tabs below."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__, addressmap, poller as pl
from ..live import LivePipeline
from ..logger import Recorder, log_dir
from ..transport import make_transport, list_ports
from . import theme
from .dashboard import Dashboard
from .rawtools import RawTools
from .replay import Replay

ICON = Path(__file__).parent.parent / "data" / "hondalog.svg"
UI_HZ = 20

STATE_STYLE = {
    pl.OK: (theme.GOOD, "ECU OK"),
    pl.CONNECTING: (theme.WARN, "CONNECTING"),
    pl.NO_REPLY: (theme.BAD, "NO REPLY"),
    pl.PORT_LOST: (theme.BAD, "PORT LOST"),
    pl.STOPPED: (theme.DIM, "OFFLINE"),
}


def _stat(title: str) -> tuple[QWidget, QLabel]:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(6, 0, 6, 0)
    lay.setSpacing(0)
    cap = QLabel(title)
    cap.setStyleSheet(f"color:{theme.DIM}; font-size:8pt;")
    val = QLabel("--")
    val.setStyleSheet("font-size:12pt; font-weight:bold;")
    val.setMinimumWidth(58)
    lay.addWidget(cap)
    lay.addWidget(val)
    return box, val


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"hondalog {__version__}")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.settings = QSettings("hondalog", "hondalog")
        self.poller: pl.Poller | None = None
        self.recorder: Recorder | None = None
        self.amap = None
        self.pipeline: LivePipeline | None = None    # math channels + alarms (W3)
        self._ready_seen = False
        self._tick_n = 0

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_bar())
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard = Dashboard(self.settings)
        self.rawtools = RawTools()
        self.replay = Replay(self.settings)
        self.tabs.addTab(self.dashboard, "Dashboard")
        self.tabs.addTab(self.rawtools, "Raw tools")
        self.tabs.addTab(self.replay, "Replay")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.dashboard.banner.openRequested.connect(self.open_in_replay)

        self.map_label = QLabel()
        self.map_label.setStyleSheet(f"color:{theme.DIM};")
        edit = QPushButton("Edit address map")
        edit.setFlat(True)
        edit.setToolTip("Opens the TOML file in your text editor. Reconnect to apply changes.")
        edit.clicked.connect(self.edit_map)
        logs = QPushButton("Open log folder")
        logs.setFlat(True)
        logs.clicked.connect(lambda: (log_dir().mkdir(parents=True, exist_ok=True),
                                      QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))))
        self.alarm_label = QLabel()                  # minimal alarm readout until the new UI
        self.alarm_label.setToolTip("Highest-priority active alarm. Click to acknowledge.")
        self.alarm_label.mousePressEvent = lambda _e: self.acknowledge_alarms()
        self.statusBar().addPermanentWidget(self.alarm_label)
        self.statusBar().addPermanentWidget(self.map_label)
        self.statusBar().addPermanentWidget(edit)
        self.statusBar().addPermanentWidget(logs)

        self.load_map(show_errors=False)
        self.rescan_ports()
        self._update_bar()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.CoarseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000 // UI_HZ)
        QApplication.instance().installEventFilter(self)

    # ---- connection bar ----

    def _build_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(f"QFrame#bar {{ background:{theme.PANEL}; }}")
        bar.setObjectName("bar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(250)
        self.port_combo.setToolTip("Serial port. The KKL cable is auto-detected (FTDI 0403:6001).\n"
                                   "You can also type a device path.")
        lay.addWidget(self.port_combo, 1)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan_ports)
        lay.addWidget(rescan)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setMinimumWidth(100)
        self.connect_btn.clicked.connect(self.toggle_connection)
        lay.addWidget(self.connect_btn)

        self.state_label = QLabel("OFFLINE")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setMinimumWidth(112)
        lay.addWidget(self.state_label)
        for title, attr in (("READS", "reads_val"), ("ERRORS", "errors_val"), ("RATE", "hz_val")):
            box, val = _stat(title)
            setattr(self, attr, val)
            lay.addWidget(box)

        lay.addStretch(1)
        self.auto_rec = QCheckBox("Record on connect")
        self.auto_rec.setChecked(self.settings.value("record/auto", "true") == "true")
        self.auto_rec.toggled.connect(
            lambda on: self.settings.setValue("record/auto", "true" if on else "false"))
        lay.addWidget(self.auto_rec)
        self.rec_label = QLabel("")
        self.rec_label.setMinimumWidth(96)
        self.rec_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.rec_label)
        self.rec_btn = QPushButton("Record")
        self.rec_btn.setMinimumWidth(84)
        self.rec_btn.clicked.connect(self.toggle_recording)
        lay.addWidget(self.rec_btn)
        self.mark_btn = QPushButton("MARK  [Space]")
        self.mark_btn.setMinimumSize(150, 40)
        self.mark_btn.setFocusPolicy(Qt.NoFocus)
        self.mark_btn.setToolTip("Drop an event marker into the log (\"it just stumbled\")")
        self.mark_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.WARN}; color:#000; font-weight:bold; font-size:12pt;"
            " border-radius:5px; }"
            "QPushButton:disabled { background:#4a4333; color:#8a8370; }"
            "QPushButton:pressed { background:#ffd060; }")
        self.mark_btn.clicked.connect(self.mark)
        lay.addWidget(self.mark_btn)
        return bar

    def rescan_ports(self) -> None:
        current = self.port_combo.currentData() or self.port_combo.currentText()
        last = self.settings.value("port/last", "")
        self.port_combo.clear()
        ports = list_ports()
        for p in ports:
            self.port_combo.addItem(p.label, p.device)
        devices = [p.device for p in ports]
        pick = next((d for d in (current, last) if d in devices), None)
        if pick is None:            # the real cable first, else whatever is on top
            pick = next((p.device for p in ports if p.is_ftdi), devices[0])
        self.port_combo.setCurrentIndex(devices.index(pick))

    def current_port(self) -> str:
        text = self.port_combo.currentText().strip()
        i = self.port_combo.findText(text)
        return self.port_combo.itemData(i) if i >= 0 else text.split()[0] if text else ""

    # ---- address map ----

    def load_map(self, show_errors: bool = True) -> bool:
        try:
            amap = addressmap.load_user_map()
            problem = ""
        except (addressmap.MapError, OSError) as exc:
            problem = str(exc)
            amap = addressmap.load_default()
        if problem and show_errors:
            QMessageBox.warning(self, "hondalog", "Your address map has a problem, so the "
                                f"built-in default is used instead:\n\n{problem}")
        self.amap = amap
        self.pipeline = LivePipeline(amap)
        self.dashboard.set_map(amap)
        self.replay.set_map(amap)
        unverified = sum(1 for c in amap.channels if not c.verified)
        self.map_label.setText(f"Map: {amap.version}  |  {len(amap.channels)} channels, "
                               f"{unverified} unverified" + ("  |  USER MAP BROKEN" if problem else ""))
        self.map_label.setToolTip(amap.path)
        return not problem

    def edit_map(self) -> None:
        path = addressmap.user_map_path()
        if not path.exists():
            addressmap.load_user_map()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.statusBar().showMessage(f"{path}  (disconnect and connect again to apply)", 8000)

    # ---- connect / disconnect ----

    def toggle_connection(self) -> None:
        if self.poller is None:
            self.connect_port()
        else:
            self.disconnect_port()

    def connect_port(self, port: str | None = None) -> None:
        if self.poller is not None:
            return
        port = port or self.current_port()
        if not port:
            return
        self.load_map()
        if self.current_port() != port:
            self.port_combo.setEditText(port)
        self.settings.setValue("port/last", port)
        self._ready_seen = False
        if self.pipeline is not None:
            self.pipeline.reset()
        self.poller = pl.Poller(make_transport(port, self.amap.framing), self.amap)
        self.poller.port_name = port
        self.poller.start()
        self.rawtools.set_poller(self.poller)
        self.statusBar().showMessage(f"Connecting to {port} …")
        self._update_bar()

    def disconnect_port(self, message: str = "Disconnected") -> None:
        self.stop_recording()
        self.rawtools.set_poller(None)
        if self.poller is not None:
            self.poller.stop()
            self._drain()
            self.poller = None
        self.statusBar().showMessage(message, 10000)
        self._update_bar()

    def _on_ready(self) -> None:
        """The poller has opened the port and tried to read the ECU id."""
        p = self.poller
        if p.state == pl.PORT_LOST:
            error = p.last_error
            self.disconnect_port("Could not open port")
            hint = ""
            if "ermission" in error:
                hint = ("\n\nYou are not allowed to open the port. Run:\n"
                        "    sudo usermod -aG dialout $USER\n"
                        "then log out and back in.")
            QMessageBox.critical(self, "hondalog", f"Could not open {p.port_name}:\n{error}{hint}")
            return
        if p.ecu_id is None:
            self.statusBar().showMessage(
                "Port open, but the ECU did not answer the ID read. Key ON? "
                f"Last error: {p.last_error}", 15000)
        else:
            self.statusBar().showMessage(f"ECU id: {p.ecu_id_text}", 15000)
        if self.auto_rec.isChecked():
            self.start_recording()

    # ---- recording ----

    def toggle_recording(self) -> None:
        if self.recorder is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        if self.poller is None or self.recorder is not None:
            return
        try:
            self.recorder = Recorder(self.amap, self.poller.ecu_id_text, self.poller.port_name)
        except OSError as exc:
            QMessageBox.critical(self, "hondalog", f"Cannot write the log file:\n{exc}")
            return
        self._rec_t0 = time.monotonic()
        self.poller.on_sample = self.recorder.feed
        self.statusBar().showMessage(f"Recording to {self.recorder.path}", 8000)
        self._update_bar()

    def stop_recording(self) -> None:
        if self.recorder is None:
            return
        if self.poller is not None:
            self.poller.on_sample = None
        rec, self.recorder = self.recorder, None
        rec.stop()
        self._stall_events(rec)
        self.statusBar().showMessage(f"Saved {rec.path}  ({rec.rows} rows, {rec.markers} markers)", 15000)
        self._update_bar()

    def mark(self) -> None:
        if self.recorder is None or self.poller is None:
            self.statusBar().showMessage("Not recording: marker ignored", 3000)
            return
        n = self.recorder.markers + 1
        self.recorder.mark(f"MARK {n}")
        self.dashboard.add_marker(time.monotonic() - self.poller.t0, f"MARK {n}")
        self.statusBar().showMessage(f"Marker {n} dropped", 3000)

    def _stall_events(self, rec: Recorder) -> None:
        while not rec.events.empty():
            event = rec.events.get_nowait()
            self.dashboard.banner.show_event(event)
            self.dashboard.add_marker(event.t, "STALL")
            self.tabs.setCurrentWidget(self.dashboard)
            QApplication.beep()

    def open_in_replay(self, path: str) -> None:
        if self.replay.open(path):
            self.tabs.setCurrentWidget(self.replay)

    # ---- periodic update ----

    def _drain(self) -> None:
        samples = self.poller.samples
        while samples:
            sample = samples.popleft()
            if self.pipeline is not None:
                self._alarm_events(self.pipeline.feed(sample).events)
            self.dashboard.add_sample(sample)

    # ---- alarms (logic lives in hondalog.alarms; this is only the readout) ----

    def _alarm_events(self, events) -> None:
        for event in events:
            if event.kind == "raised":
                if "marker" in event.actions and self.recorder is not None:
                    self.recorder.mark(event.marker_text)
                    if self.poller is not None:
                        self.dashboard.add_marker(event.t, event.name)
                if "beep" in event.actions:
                    QApplication.beep()
            self.statusBar().showMessage(event.marker_text, 8000)
        if events:
            self._show_alarm()

    def _show_alarm(self) -> None:
        top = self.pipeline.alarms.top() if self.pipeline is not None else None
        if top is None:
            self.alarm_label.setText("")
            return
        color = theme.BAD if top.severity == "critical" else theme.WARN
        more = len(self.pipeline.alarms.active()) - 1
        text = top.message + (" ?" if top.unverified else "") + (f"  (+{more})" if more else "")
        self.alarm_label.setText(f"<b style='color:{color}'>{top.severity.upper()}: {text}</b>")

    def acknowledge_alarms(self) -> None:
        if self.pipeline is not None:
            self._alarm_events(self.pipeline.alarms.acknowledge())
            self._show_alarm()

    def tick(self) -> None:
        self._tick_n += 1
        p = self.poller
        if p is not None:
            if not self._ready_seen and p.ready.is_set():
                self._ready_seen = True
                self._on_ready()
                if self.poller is None:
                    return
            self._drain()
            if self.recorder is not None:
                self._stall_events(self.recorder)
            self.rawtools.tick()
            if not p.is_alive() and self._ready_seen:
                self.disconnect_port(f"Poller stopped: {p.last_error}")
        self.dashboard.refresh()
        if self._tick_n % 5 == 0:
            self._update_bar()

    def _update_bar(self) -> None:
        p = self.poller
        state = p.state if p is not None else pl.STOPPED
        color, text = STATE_STYLE[state]
        fg = "#000" if state != pl.STOPPED else theme.TEXT
        bg = color if state != pl.STOPPED else theme.PANEL_HI
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"background:{bg}; color:{fg}; font-weight:bold;"
                                       " border-radius:5px; padding:6px;")
        self.state_label.setToolTip(p.last_error if p is not None else "")
        self.reads_val.setText(str(p.reads) if p else "--")
        self.errors_val.setText(str(p.errors) if p else "--")
        self.errors_val.setStyleSheet("font-size:12pt; font-weight:bold;"
                                      + (f" color:{theme.BAD};" if p and p.errors else ""))
        self.hz_val.setText(f"{p.hz:.1f} Hz" if p else "--")
        self.connect_btn.setText("Disconnect" if p else "Connect")
        self.port_combo.setEnabled(p is None)
        self.rec_btn.setEnabled(p is not None)
        self.mark_btn.setEnabled(self.recorder is not None)
        if self.recorder is not None:
            secs = int(time.monotonic() - self._rec_t0)
            self.rec_label.setText(f"● REC {secs // 60:02d}:{secs % 60:02d}")
            self.rec_label.setStyleSheet(f"color:{theme.BAD}; font-weight:bold; font-size:11pt;")
            self.rec_label.setToolTip(str(self.recorder.path))
            self.rec_btn.setText("Stop")
        else:
            self.rec_label.setText("not recording")
            self.rec_label.setStyleSheet(f"color:{theme.DIM};")
            self.rec_btn.setText("Record")

    # ---- events ----

    def eventFilter(self, obj, event) -> bool:
        # Space drops a marker from anywhere, except while typing in a field.
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space \
                and not event.isAutoRepeat() and self.isActiveWindow():
            focus = QApplication.focusWidget()
            typing = isinstance(focus, (QLineEdit, QAbstractSpinBox, QPlainTextEdit)) or (
                isinstance(focus, QComboBox) and focus.isEditable())
            if not typing:
                self.mark()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.disconnect_port()
        super().closeEvent(event)
