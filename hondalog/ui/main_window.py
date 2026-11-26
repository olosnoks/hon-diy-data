"""The shell: status bar, tab bar, eight screens, alarm strip, the keyboard
map (HANDOFF section 7) and the one frame timer everything paints from."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QInputDialog, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from . import adapters as ad
from .chrome import AlarmStrip, StallBanner, StatusBar, TabBar
from .perf import Perf
from .screens.alarms import AlarmsScreen
from .screens.drive import DriveScreen
from .screens.gpsmap import MapScreen
from .screens.map3d import Map3DScreen
from .screens.handheld import HandheldScreen
from .screens.rawtools import RawScreen
from .screens.replay import ReplayScreen
from .screens.sensors import SensorsScreen
from .screens.tune import TuneScreen
from .theme import T
from .units import PRESETS, QUANTITIES, U

ICON = Path(__file__).parent.parent / "data" / "hondalog.svg"
FRAME_MS = 33                   # about 30 fps
SENSORS, DRIVE, MAP3D, REPLAY, RAW, TUNE, ALARMS, MAP, HANDHELD = range(9)


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
        U.load(self.settings)               # display units; nothing behind the UI changes
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
        self.map = MapScreen(session, self.replay, self.settings)
        self.handheld = HandheldScreen(session, self.settings)
        self.screens = [self.sensors, self.drive, self.map3d, self.replay, self.raw,
                        self.tune, self.alarms, self.map, self.handheld]
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
        self.map.openRequested.connect(self.open_capture)
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
            QWidget.update(widget)      # not widget.update(): item views override it with update(index) on Debian's PySide6
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

    def open_capture(self, ref) -> None:
        """A pin of the Map screen's All stalls view: that capture in Replay
        (None: Replay already has it, as in the demo)."""
        if ref is None:
            self.show_screen(REPLAY)
        elif not self.open_in_replay(ref):
            self.say(f"Cannot open {getattr(ref, 'name', ref)}")

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
        link_text = getattr(s.ecu, "link_text", None)
        if link_text is not None and link_text():
            for part in link_text().split(" · "):
                menu.addAction(part).setEnabled(False)      # rates per class, wire load, errors
            menu.addSeparator()
        if s.connected:
            if getattr(s.ecu, "handheld_map_differs", lambda: False)():
                menu.addAction("Send my map to the handheld (for relay)",
                               lambda: self.say(s.ecu.push_handheld_map()))
            menu.addAction(f"Disconnect {s.port}", s.disconnect)
        else:
            last = str(self.settings.value("port/last", ""))
            for device, label in s.ports():
                action = menu.addAction(f"Connect  {label}")
                action.triggered.connect(lambda _=False, d=device: self.connect_port(d))
                if device == last:
                    menu.setDefaultAction(action)
            menu.addAction("Other port\u2026", self.ask_port)
            if hasattr(s.ecu, "first_contact"):
                menu.addAction("Run first contact\u2026", self.first_contact)
        menu.addSeparator()
        auto = QAction("Record on connect", menu, checkable=True)
        auto.setChecked(s.auto_record)
        auto.toggled.connect(self._set_auto_record)
        menu.addAction(auto)
        menu.addAction("Start / stop recording\tCtrl+R", self.toggle_recording)
        menu.addSeparator()
        self.settings_menu(menu.addMenu("Settings"))
        menu.addAction("Edit address map", self.edit_map)
        menu.addAction("Open log folder", self.open_logs)
        menu.exec(pos)

    def settings_menu(self, menu: QMenu) -> QMenu:
        """Settings > Units: the two presets, then each quantity on its own."""
        units = menu.addMenu("Units")
        current, preset = U.as_dict(), U.preset_name()
        labels = {"US": "US: mph, °F, gal/h (MAP stays kPa)", "Metric": "Metric: km/h, °C, L/h"}
        for name in PRESETS:
            action = QAction(labels.get(name, name), units, checkable=True)
            action.setChecked(name == preset)
            action.triggered.connect(lambda _=False, n=name: self.set_units(preset=n))
            units.addAction(action)
        units.addSeparator()
        for field, title, base in QUANTITIES:
            sub = units.addMenu(f"{title}: {current[field]}")
            for unit in U.choices(base):
                action = QAction(unit, sub, checkable=True)
                action.setChecked(unit == current[field])
                action.triggered.connect(lambda _=False, f=field, u=unit: self.set_units(f, u))
                sub.addAction(action)
        units.addSeparator()
        note = units.addAction("Logs and exports stay in the address map's units")
        note.setEnabled(False)
        self.tiles_menu(menu.addMenu("Map tiles"))
        return menu

    def tiles_menu(self, menu: QMenu) -> QMenu:
        """Settings > Map tiles: downloads on or off, the server, the cache."""
        from ..geo import tiles as gt
        from .screens.gpsmap import tile_settings
        cfg = tile_settings(self.settings)
        source = gt.source_for(cfg["url"], cfg["attribution"])
        online = QAction("Download map tiles (off: only what is cached)", menu, checkable=True)
        online.setChecked(cfg["online"])
        online.toggled.connect(lambda on: self._set_tiles(online=on))
        menu.addAction(online)
        menu.addSeparator()
        menu.addAction(f"Server: {source.url}").setEnabled(False)
        if source.attribution:
            menu.addAction(f"Shown on the map: {source.attribution}").setEnabled(False)
        menu.addAction("Tile server…", self.ask_tile_server)
        if cfg["url"]:
            menu.addAction("Back to OpenStreetMap", lambda: self._set_tiles(url="", attribution=""))
        menu.addSeparator()
        tiles = self.map.tiles
        size = tiles.cache.size_bytes() / 1e6 if tiles is not None else 0.0
        menu.addAction(f"Cache: {size:.0f} of {cfg['cap_mb']:.0f} MB in {gt.cache_root()}").setEnabled(False)
        menu.addAction("Clear the tile cache…", self.clear_tile_cache)
        return menu

    def _set_tiles(self, **items) -> None:
        for key, value in items.items():
            name = {"url": "map/tile_url", "attribution": "map/tile_attribution", "online": "map/online"}[key]
            self.settings.setValue(name, ("true" if value else "false") if key == "online" else value)
        self.map.configure_tiles()
        cfg = items.get("online")
        if cfg is not None:
            self.say("Map tiles: downloading what the map shows" if cfg else "Map tiles: cached only")
        elif "url" in items:
            self.say(f"Map tiles from {items['url'] or 'OpenStreetMap'}")

    def ask_tile_server(self) -> None:
        from ..geo import tiles as gt
        from .screens.gpsmap import tile_settings
        cfg = tile_settings(self.settings)
        url, ok = QInputDialog.getText(self, "hondalog", "Tile URL with {z} {x} {y} (empty: OpenStreetMap):",
                                       text=cfg["url"] or gt.OSM_URL)
        if not ok:
            return
        url = url.strip()
        if url == gt.OSM_URL:
            url = ""
        if url and not gt.TileSource(url).valid:
            self.say("A tile URL needs {z}, {x} and {y} and http(s) or file")
            return
        attribution = ""
        if url:
            current = cfg["attribution"] or ""
            attribution, ok = QInputDialog.getText(self, "hondalog", "Attribution the server's terms ask for "
                                                   "(shown on the map):", text=current or gt.OSM_ATTRIBUTION)
            if not ok:
                return
        self._set_tiles(url=url, attribution=attribution.strip())

    def clear_tile_cache(self) -> None:
        tiles = self.map.tiles
        if tiles is None:
            return
        if QApplication.platformName() not in ("offscreen", "minimal") and QMessageBox.question(
                self, "hondalog", f"Delete the cached map tiles in {tiles.cache.dir}?") != QMessageBox.Yes:
            return
        tiles.clear_cache()
        self.map.canvas._layout()
        self.say("Tile cache cleared")

    def set_units(self, field: str = "", unit: str = "", preset: str = "") -> None:
        """Pick display units (one quantity, or a preset) and remember them."""
        if preset:
            U.preset(preset)
        else:
            U.set_unit(field, unit)
        U.save(self.settings)
        self.map.units_changed()
        for widget in self.findChildren(QWidget):
            QWidget.update(widget)      # not widget.update(): item views override it with update(index) on Debian's PySide6
        self.screens[self.current()].refresh(self.session.bus.now())
        chosen = U.as_dict()
        self.say("Units: " + ", ".join(chosen[f] for f, _, _ in QUANTITIES[:4]))

    def first_contact(self) -> None:
        """W1's first-contact sequence on the last port, in the background.
        The verdict comes back through the status line; the report is a file."""
        from ..transport import default_port
        port = str(self.settings.value("port/last", "")) or default_port()
        port, ok = QInputDialog.getText(self, "hondalog", "First contact on port (key ON, about "
                                        "15 s, read-only):", text=port)
        if not ok or not port.strip():
            return
        problem = self.session.ecu.first_contact(port.strip())
        self._fc_seen = False
        self.say(problem or f"First contact on {port.strip()} …")

    def ask_port(self) -> None:
        port, ok = QInputDialog.getText(self, "hondalog", "Serial port (e.g. /dev/ttyUSB0, or the handheld: "
                                        "cyd:/dev/ttyUSB1):",
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
        limits = [f"{name} {U.limit(spec, value)}" for name, value in (
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
            link = getattr(s.ecu, "link_text", lambda: "")()
            gps = " · ".join(t for t in (getattr(x, "info_text", lambda: "")() for x in s.extra_sources) if t)
            self.tabs.set_info(f"{s.port or 'no port'} · map {s.map_name()} · {n} ch "
                               f"· {unverified} unverified" + (f" · {link}" if link else "")
                               + (f" · {gps}" if gps else ""))
            notice = getattr(s.ecu, "take_notice", lambda: "")()
            if notice:
                self.say(notice)
            report = getattr(s.ecu, "first_contact_report", None)
            if report is not None and not getattr(self, "_fc_seen", True):
                self._fc_seen = True
                where = getattr(report, "path", None)
                self.say(f"First contact: {report.verdict}. {report.summary}"
                         + (f"  Report: {where}" if where else ""))
            elif getattr(s.ecu, "first_contact_running", lambda: False)():
                self.say(s.ecu.first_contact_progress())
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
        if Qt.Key_F1 <= key <= Qt.Key_F8:
            self.show_screen(key - Qt.Key_F1)
            return True
        if key == Qt.Key_F10:
            self.show_screen(HANDHELD)
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
        self.map.close_tiles()
        self.handheld.shutdown()
        QApplication.instance().removeEventFilter(self)
        try:
            T.changed.disconnect(self.retheme)
        except (RuntimeError, TypeError):
            pass
        self.session.close()
        super().closeEvent(event)
