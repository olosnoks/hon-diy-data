"""The shell: status bar, tab bar, seven screens, alarm strip, the keyboard
map (HANDOFF section 7) and the one frame timer everything paints from."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QInputDialog, QLineEdit, QMainWindow, QMenu,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from . import adapters as ad
from .chrome import AlarmStrip, StallBanner, StatusBar, TabBar
from .perf import Perf
from .screens.alarms import AlarmsScreen
from .screens.drive import DriveScreen
from .screens.map3d import Map3DScreen
from .screens.rawtools import RawScreen
from .screens.replay import ReplayScreen
from .screens.sensors import SensorsScreen
from .screens.tune import TuneScreen
from .theme import T

ICON = Path(__file__).parent.parent / "data" / "hondalog.svg"
FRAME_MS = 33                   # about 30 fps
SENSORS, DRIVE, MAP3D, REPLAY, RAW, TUNE, ALARMS = range(7)


def apply_palette(app) -> None:
    """Menus and dialogs are stock Qt widgets: give them the token colours."""
    pal = QPalette()
    t = T
    for role, colour in ((QPalette.Window, t.panel), (QPalette.Base, t.bg),
                         (QPalette.AlternateBase, t.sunk), (QPalette.Button, t.panel),
                         (QPalette.WindowText, t.fg), (QPalette.Text, t.fg),
                         (QPalette.ButtonText, t.fg), (QPalette.ToolTipBase, t.panel),
                         (QPalette.ToolTipText, t.fg), (QPalette.Highlight, t.accent),
                         (QPalette.HighlightedText, t.on_accent),
                         (QPalette.PlaceholderText, t.fg2)):
        pal.setColor(role, colour)
    pal.setColor(QPalette.Disabled, QPalette.Text, t.fg2)
    pal.setColor(QPalette.Disabled, QPalette.WindowText, t.fg2)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, t.fg2)
    app.setPalette(pal)


class Shell(QMainWindow):
    def __init__(self, session, settings: QSettings | None = None, profile: bool = False):
        super().__init__()
        self.session = session
        self.settings = settings if settings is not None else QSettings("hondalog", "hondalog")
        self.setWindowTitle(f"hondalog {__version__}")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        T.set(str(self.settings.value("theme", "daylight")))
        session.auto_record = self.settings.value("record/auto", "true") == "true"
        session.start()

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.status = StatusBar()
        self.tabs = TabBar()
        self.stack = QStackedWidget()
        self.strip = AlarmStrip()
        for widget in (self.status, self.tabs):
            lay.addWidget(widget)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.strip)
        self.setCentralWidget(central)
        self.banner = StallBanner(central)

        self.sensors = SensorsScreen(session)
        self.drive = DriveScreen(session, self.settings)
        self.map3d = Map3DScreen(session, self.settings)
        self.replay = ReplayScreen(session)
        self.raw = RawScreen(session)
        self.tune = TuneScreen(session, self.settings)
        self.alarms = AlarmsScreen(session)
        self.screens = [self.sensors, self.drive, self.map3d, self.replay, self.raw,
                        self.tune, self.alarms]
        for screen in self.screens:
            self.stack.addWidget(screen)

        self.tabs.picked.connect(self.show_screen)
        self.status.mark_btn.clicked.connect(self.mark)
        self.status.connectionMenu.connect(self.connection_menu)
        self.status.recordClicked.connect(self.toggle_recording)
        self.banner.openRequested.connect(self.open_stall)
        self.banner.dismissed.connect(self.dismiss_banner)
        self.sensors.pinRequested.connect(self.pin_to_drive)
        self.sensors.menuRequested.connect(self.tile_menu)
        self.drive.markRequested.connect(self.mark)
        self.drive.menuRequested.connect(self.drive_menu)
        T.changed.connect(self.retheme)

        self.perf: Perf | None = Perf() if profile else None
        self.flush_paint = profile      # paint inside the frame so it can be timed
        self._seen_pub = -1
        self._info_t = -1.0
        self._perf_t = 0.0
        self._full = False
        self.retheme()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.frame)
        self.timer.start(FRAME_MS)
        QApplication.instance().installEventFilter(self)
        self.frame()

    # ---- screens ----

    def show_screen(self, index: int) -> None:
        index = max(0, min(len(self.screens) - 1, index))
        self.stack.setCurrentIndex(index)
        self.tabs.set_current(index)
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            focus.clearFocus()
        self.screens[index].refresh(self.session.bus.now())

    def current(self) -> int:
        return self.stack.currentIndex()

    def retheme(self) -> None:
        self.settings.setValue("theme", T.name)
        apply_palette(QApplication.instance())
        self.status.retheme()
        for screen in self.screens:
            screen.retheme()
        for widget in self.findChildren(QWidget):
            widget.update()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.banner.setGeometry(0, 44, self.width(), 44)

    # ---- actions ----

    def say(self, text: str) -> None:
        self.strip.show_message(text, self.session.bus.now())

    def mark(self) -> None:
        self.say(self.session.mark())

    def toggle_recording(self) -> None:
        self.say(self.session.toggle_recording())

    def pin_to_drive(self, channel_id: str) -> None:
        slot = self.drive.pin(channel_id)
        state = self.session.bus.channel(channel_id)
        self.say(f"{state.spec.label if state else channel_id} pinned to Drive, slot {slot + 1}")

    def open_in_replay(self, path) -> bool:
        ok = self.replay.open(path)
        if ok:
            self.show_screen(REPLAY)
        return ok

    def open_stall(self) -> None:
        cap = self.session.banner
        if cap is not None:
            self.dismiss_banner()
            self.open_in_replay(cap.path)

    def dismiss_banner(self) -> None:
        self.session.dismiss_banner()
        self.banner.hide()
        self.alarms.update()

    def toggle_fullscreen(self) -> None:
        self._full = not self._full
        self.showFullScreen() if self._full else self.showNormal()

    def beep(self, times: int = 2) -> None:
        if QApplication.platformName() in ("offscreen", "minimal"):
            return
        for i in range(times):
            QTimer.singleShot(260 * i, QApplication.beep)

    # ---- menus ----

    def connection_menu(self, pos) -> None:
        s = self.session
        menu = QMenu(self)
        st = s.ecu_status
        if st.state in (ad.SILENT, ad.LOST) and st.detail:
            menu.addAction(f"Last error: {st.detail[:90]}").setEnabled(False)
            menu.addSeparator()
        if s.connected:
            menu.addAction(f"Disconnect {s.port}", s.disconnect)
        else:
            last = str(self.settings.value("port/last", ""))
            for device, label in s.ports():
                action = menu.addAction(f"Connect  {label}")
                action.triggered.connect(lambda _=False, d=device: self.connect_port(d))
                if device == last:
                    menu.setDefaultAction(action)
            menu.addAction("Other port\u2026", self.ask_port)
        menu.addSeparator()
        auto = QAction("Record on connect", menu, checkable=True)
        auto.setChecked(s.auto_record)
        auto.toggled.connect(self._set_auto_record)
        menu.addAction(auto)
        menu.addAction("Start / stop recording\tCtrl+R", self.toggle_recording)
        menu.addSeparator()
        menu.addAction("Edit address map", self.edit_map)
        menu.addAction("Open log folder", self.open_logs)
        menu.exec(pos)

    def ask_port(self) -> None:
        port, ok = QInputDialog.getText(self, "hondalog", "Serial port (e.g. /dev/ttyUSB0):",
                                        text=str(self.settings.value("port/last", "")))
        if ok and port.strip():
            self.connect_port(port.strip())

    def _set_auto_record(self, on: bool) -> None:
        self.session.auto_record = on
        self.settings.setValue("record/auto", "true" if on else "false")

    def connect_port(self, port: str) -> None:
        if not port:
            return
        self.settings.setValue("port/last", port)
        self.session.connect(port)
        self.say(f"Connecting to {port}")

    def edit_map(self) -> None:
        from .. import addressmap
        path = addressmap.user_map_path()
        if not path.exists():
            addressmap.load_user_map()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.say(f"{path}: disconnect and connect again to apply changes")

    def open_logs(self) -> None:
        from ..logger import log_dir
        log_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def tile_menu(self, channel_id: str, pos) -> None:
        state = self.session.bus.channel(channel_id)
        if state is None:
            return
        spec = state.spec
        menu = QMenu(self)
        menu.addAction("Pin to Drive view", lambda: self.pin_to_drive(channel_id))
        if isinstance(self.session.ecu, ad.Connectable) and spec.source == "ECU" and spec.mapped:
            menu.addAction("Mark unverified" if spec.verified else "Mark verified",
                           lambda: self.say(self.session.ecu.set_verified(
                               channel_id, not spec.verified)))
        menu.addSeparator()
        limits = [f"{name} {value:g} {spec.units}" for name, value in (
            ("warn below", spec.warn_lo), ("warn above", spec.warn_hi),
            ("alarm below", spec.alarm_lo), ("alarm above", spec.alarm_hi)) if value is not None]
        for text in limits or ["No limits set"]:
            menu.addAction(text).setEnabled(False)
        menu.addAction("Limits are edited in addressmap.toml", self.edit_map)
        menu.exec(pos)

    def drive_menu(self, slot: int, pos) -> None:
        menu = QMenu(self)
        sub = menu.addMenu(f"Slot {slot + 1} shows")
        for state in self.session.ordered("ECU") + self.session.ordered("BB"):
            if state.spec.mapped:
                name = ("BB " if state.spec.source == "BB" else "") + state.spec.label
                sub.addAction(name, lambda cid=state.spec.id: self.drive.pin(cid, slot))
        four = QAction("4-up layout", menu, checkable=True)
        four.setChecked(self.drive.four_up)
        four.toggled.connect(self.drive.set_four_up)
        menu.addAction(four)
        menu.exec(pos)

    # ---- the frame ----

    def frame(self) -> None:
        t0 = time.perf_counter()
        s = self.session
        now = s.bus.now()
        for event in s.pump(now):
            if event.kind == "alarm":
                self.beep(2)
            elif event.kind == "stall":
                self.banner.show_stall(f"STALL {event.payload.wall:%H:%M:%S}", event.text)
                self.beep(2)
            elif event.text:
                self.say(event.text)
        self.status.sync(s, now)
        self.strip.sync(s.alarms, now)
        if now - self._info_t >= 1.0:
            self._info_t = now
            n, unverified = s.counts()
            self.tabs.set_info(f"{s.port or 'no port'} · map {s.map_name()} · {n} ch "
                               f"· {unverified} unverified")
        self.stack.currentWidget().refresh(now)
        perf = self.perf
        if perf is None and not self.flush_paint:
            return
        t1 = time.perf_counter()
        if self.flush_paint:
            QCoreApplication.sendPostedEvents(None, QEvent.UpdateRequest)
        t2 = time.perf_counter()
        if perf is not None:
            perf.frame(t0, t1, t2)
            if s.bus.pub_gen != self._seen_pub:
                self._seen_pub = s.bus.pub_gen
                perf.note_latency((time.monotonic() - s.bus.last_wire) * 1000.0)
            if t2 - self._perf_t >= 0.5:
                self._perf_t = t2
                st = s.ecu_status
                self.strip.set_right(f"{perf.line()} · poll {st.rate_hz:.1f} Hz · "
                                     f"q {st.queue}")

    # ---- keyboard map ----

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.KeyPress or not self.isActiveWindow():
            return False
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget():
            return False
        return self.handle_key(event)

    def handle_key(self, event) -> bool:
        key, mods = event.key(), event.modifiers()
        typing = isinstance(QApplication.focusWidget(), QLineEdit)
        if Qt.Key_F1 <= key <= Qt.Key_F7:
            self.show_screen(key - Qt.Key_F1)
            return True
        if key == Qt.Key_F9:
            T.toggle()
            return True
        if key == Qt.Key_F11:
            self.toggle_fullscreen()
            return True
        if key == Qt.Key_Escape:
            if typing:
                QApplication.focusWidget().clearFocus()
            if self.banner.isVisible():
                self.dismiss_banner()
            self.session.acknowledge()
            return True
        if typing:
            return False                    # the text field gets everything else
        if key == Qt.Key_Space:
            if not event.isAutoRepeat():
                self.mark()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter) and self.banner.isVisible():
            self.open_stall()
            return True
        if key == Qt.Key_R and mods & Qt.ControlModifier:
            self.toggle_recording()
            return True
        return bool(self.stack.currentWidget().key(event))

    def closeEvent(self, event) -> None:
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        try:
            T.changed.disconnect(self.retheme)
        except (RuntimeError, TypeError):
            pass
        self.session.close()
        super().closeEvent(event)
