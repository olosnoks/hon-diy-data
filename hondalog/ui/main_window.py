"""The shell: the top row (keys + status), the eight screens (F1 Live, F2 Drive,
F3 Events, F4 Replay, F5 Power, F6 GPS map, F7 Bytes, F8 Setup), Tune while a
ROM is open, the alarm strip, the keyboard map and the one frame timer
everything paints from.

Screens that live inside another one now (Map 3D in Power's "Time in cell"
tab, Alarms and the handheld in Setup) keep their ids (``MAP3D``, ``ALARMS``,
``HANDHELD``): ``show_screen`` opens their host at that page. A screen
module that is not there yet gets a marked placeholder, and the screens it
would host stay reachable on their own."""

from __future__ import annotations

import importlib
import inspect
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPainter
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QInputDialog, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QStackedWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from .. import __version__
from . import adapters as ad
from . import theme as _theme
from .chrome import TABS, TOPBAR_H, TUNE_TAB, AlarmStrip, StallBanner, TopBar
from .perf import Perf
from .screens.alarms import AlarmsScreen
from .screens.drive import DriveScreen
from .screens.gpsmap import MapScreen
from .screens.handheld import HandheldScreen
from .screens.map3d import Map3DScreen
from .screens.replay import ReplayScreen
from .screens.tune import TuneScreen
from .theme import T
from .units import PRESETS, QUANTITIES, U

ICON = Path(__file__).parent.parent / "data" / "hondalog.svg"
FRAME_MS = 33                   # about 30 fps
# the tabs, in key order (F1..F8), then Tune (no key; a tab only while a ROM is open)
LIVE, DRIVE, EVENTS, REPLAY, POWER, MAP, BYTES, SETUP, TUNE = range(9)
# screens that live inside one of the above: opening one opens its host at that page
MAP3D, ALARMS, HANDHELD = 9, 10, 11
SUBSCREENS = {MAP3D: ("map3d", POWER), ALARMS: ("alarms", SETUP), HANDHELD: ("handheld", SETUP)}
# old names
SENSORS, RAW, GPSMAP = LIVE, BYTES, MAP


def apply_palette(app) -> None:
    """Menus, dialogs, scroll bars and tooltips are stock Qt widgets: give them the token colours."""
    _theme.apply_palette(app)


def is_typing(widget) -> bool:
    """A text field has the keyboard: Space and letters are its, not the shell's
    (F-keys and Esc still act)."""
    if isinstance(widget, (QLineEdit, QAbstractSpinBox)):
        return True
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    return False


def _load(module: str, *names: str):
    """The first of ``names`` in ``hondalog.ui.screens.<module>``, or None
    when the module or the class is not there (yet)."""
    try:
        mod = importlib.import_module(f"{__package__}.screens.{module}")
    except ImportError as exc:
        if exc.name and not exc.name.endswith(f"screens.{module}"):
            raise                       # the module is there but something it imports is broken: say so
        return None
    for name in names:
        cls = getattr(mod, name, None)
        if cls is not None:
            return cls
    return None


def _make(cls, **available):
    """Build a screen with the arguments its constructor takes (by name)."""
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return cls(available["session"])
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return cls(**available)
    return cls(**{k: v for k, v in available.items() if k in params})


class Placeholder(QWidget):
    """Stands in for a screen whose module is not written yet: clearly marked."""

    def __init__(self, title: str, module: str, note: str = "", parent=None):
        super().__init__(parent)
        self.title, self.module, self.note = title, module, note
        self.placeholder = True

    def refresh(self, _now: float) -> None:
        pass

    def retheme(self) -> None:
        self.update()

    def key(self, _event) -> bool:
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        card = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        p.fillRect(card, T.panel)
        p.fillRect(QRectF(card.x(), card.y(), card.width(), 6), T.unverified)
        p.setPen(T.cpen(T.rule, 2))
        p.drawRect(card.adjusted(1, 1, -1, -1))
        p.setFont(T.font(28, 800))
        p.setPen(T.fg)
        p.drawText(card.adjusted(32, 40, -32, 0), Qt.AlignTop | Qt.AlignLeft, self.title)
        p.setFont(T.font(15, 700))
        p.setPen(T.unverified)
        p.drawText(card.adjusted(32, 92, -32, 0), Qt.AlignTop | Qt.AlignLeft,
                   f"PLACEHOLDER: hondalog/ui/screens/{self.module}.py is not there yet")
        if self.note:
            p.setFont(T.font(14, 600))
            p.setPen(T.fg2)
            p.drawText(card.adjusted(32, 128, -32, -32), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap,
                       self.note)
        p.end()


class Shell(QMainWindow):
    def __init__(self, session, settings: QSettings | None = None, profile: bool = False,
                 show_bb: bool = False):
        super().__init__()
        self.session = session
        self.settings = settings if settings is not None else QSettings("hondalog", "hondalog")
        #: the separate ESP32 black box (bb sources): shown only with --bb; the CYD is the black box
        self.show_bb = show_bb
        self.setWindowTitle(f"hondalog {__version__}")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        T.set(str(self.settings.value("theme", _theme.DEFAULT)))       # "daylight"/"night" map to dracula/darker
        U.load(self.settings)               # display units; nothing behind the UI changes
        session.auto_record = self.settings.value("record/auto", "true") == "true"
        #: picks the ECU source by itself (``autoconnect.AutoConnect``): only when
        #: ``start_auto()`` is called (the app does at start-up; tests do not)
        self.auto = None
        session.start()

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # one top row (the artboards, the web app): the keys and the status together
        self.status = self.tabs = TopBar(show_bb=show_bb)
        self.stack = QStackedWidget()
        self.strip = AlarmStrip()
        self.tabs.infoChanged.connect(self.strip.set_info)
        lay.addWidget(self.status)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.strip)
        self.setCentralWidget(central)
        self.banner = StallBanner(central)
        self._build_screens()

        self.tabs.picked.connect(self.pick_tab)
        self.status.mark_btn.clicked.connect(self.mark)
        self.status.connectionMenu.connect(self.connection_menu)
        self.status.recordClicked.connect(self.toggle_recording)
        self.status.cydClicked.connect(lambda: self.show_screen(HANDHELD))
        self.banner.openRequested.connect(self.open_stall)
        self.banner.dismissed.connect(self.dismiss_banner)
        self._connect(self.sensors, "pinRequested", self.pin_to_drive)
        self._connect(self.sensors, "menuRequested", self.tile_menu)
        self._connect(self.drive, "markRequested", self.mark)
        self._connect(self.drive, "menuRequested", self.drive_menu)
        self._connect(self.map, "openRequested", self.open_capture)
        self._connect(self.events, "openRequested", self.open_capture)
        self._connect(self.replay, "powerRequested", self.open_pull)
        self._connect(self.replay, "message", self.say)
        self._wire_replay_tiles()
        for screen in (self.events, self.power, self.bytes, self.setup):
            self._connect(screen, "message", self.say)
        T.changed.connect(self.retheme)

        self.perf: Perf | None = Perf() if profile else None
        self.flush_paint = profile      # paint inside the frame so it can be timed
        self._seen_pub = -1
        self._info_t = -1.0
        self._perf_t = 0.0
        self._full = False
        self._current = LIVE
        self.retheme()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.frame)
        self.timer.start(FRAME_MS)
        QApplication.instance().installEventFilter(self)
        self.frame()

    # ---- screens ----

    def _build_screens(self) -> None:
        s, cfg = self.session, self.settings
        args = dict(session=s, settings=cfg, shell=self, show_bb=self.show_bb)
        live_cls = _load("sensors", "LiveScreen", "SensorsScreen")
        self.sensors = _make(live_cls, **args)
        self.live = self.sensors
        self.drive = _make(DriveScreen, **args)
        self.replay = ReplayScreen(s)
        args["replay"] = self.replay
        self.events = self._screen("events", ("EventsScreen",), "F3 Events", args,
                                   "Stalls, dropouts, near-stalls, VTEC pulls and marks, with a verdict on each.")
        self.power = self._screen("power", ("PowerScreen",), "F5 Power", args,
                                  "Torque and hp from a logged full-throttle pull. Until this screen hosts it, "
                                  "Map 3D (time in cell) is reachable on its own.")
        self.map = MapScreen(s, self.replay, cfg)
        bytes_cls = _load("rawtools", "BytesScreen", "RawScreen")
        self.bytes = _make(bytes_cls, **args)
        self.raw = getattr(self.bytes, "raw", self.bytes)
        self.setup = self._screen("setup", ("SetupScreen",), "F8 Setup", args,
                                  "Alarms, the CYD black box (map push, SD drives, firmware), units, cable "
                                  "and map files. Until this screen exists: the CYD chip opens the handheld, "
                                  "the ECU chip's menu has Alarms, units and map tiles.")
        self.tune = _make(TuneScreen, **args)
        self.screens = [self.sensors, self.drive, self.events, self.replay, self.power, self.map,
                        self.bytes, self.setup, self.tune]
        for screen in self.screens:
            self.stack.addWidget(screen)
        # the screens that live inside Power and Setup: theirs if they host them, else on their own
        self._hosted: dict[str, bool] = {}
        self._pages: dict[str, int] = {}
        for name, host, make in (("map3d", self.power, lambda: Map3DScreen(s, cfg)),
                                 ("alarms", self.setup, lambda: AlarmsScreen(s)),
                                 ("handheld", self.setup, lambda: HandheldScreen(s, cfg))):
            inner = getattr(host, name, None)
            self._hosted[name] = isinstance(inner, QWidget)
            if not self._hosted[name]:
                inner = make()
                self._pages[name] = self.stack.addWidget(inner)
            setattr(self, name, inner)
        self._connect(self.handheld, "message", self.say)
        self._connect(self.setup, "romOpened", self.rom_opened)
        self.tabs.set_tabs(self.tab_list())

    def _screen(self, module: str, names: tuple, title: str, args: dict, note: str) -> QWidget:
        cls = _load(module, *names)
        if cls is None:
            return Placeholder(title, module, note)
        return _make(cls, **args)

    @staticmethod
    def _connect(obj, signal: str, slot) -> None:
        sig = getattr(obj, signal, None)
        if sig is not None and hasattr(sig, "connect"):
            sig.connect(slot)

    @property
    def tune_available(self) -> bool:
        """A ROM is open (--rom): Tune gets its tab."""
        return bool(getattr(self.session, "rom", None))

    def tab_list(self) -> tuple:
        return TABS + ((TUNE_TAB,) if self.tune_available else ())

    def _wire_replay_tiles(self) -> None:
        """Replay's GPS pane shares the GPS map screen's tile layer (one cache, one fetcher)."""
        set_tiles = getattr(self.replay, "set_tiles", None)
        if callable(set_tiles):
            set_tiles(getattr(self.map, "tiles", None))

    def open_pull(self, pull) -> None:
        """Replay's "Open in Power": the Power screen at that pull."""
        self.show_screen(POWER)
        show_pull = getattr(self.power, "show_pull", None)
        if callable(show_pull) and pull is not None:
            if not show_pull(pull, getattr(self.replay, "view", None)):
                self.say("Power: no full-throttle pull matches the one at Replay's cursor")

    def title_text(self) -> str:
        """The top bar's right side: the active screen's own title (Replay's drive,
        Events' filter) when it has one, else the link summary."""
        screen = self.stack.currentWidget()
        own = getattr(screen, "title_text", None)
        if callable(own) and screen in (self.replay, self.events):
            try:
                return own() or ""
            except Exception:
                return ""
        return ""

    def rom_opened(self, path) -> None:
        """Setup opened a ROM: Tune gets its tab, and opens."""
        self.session.rom = str(path) if path else None
        self.tabs.set_tabs(self.tab_list())
        if self.session.rom:
            self.show_screen(TUNE)

    def pick_tab(self, index: int) -> None:
        self.show_screen(index if index < len(TABS) else TUNE)

    def show_screen(self, index: int) -> None:
        """Show a screen by id (LIVE..TUNE, or MAP3D / ALARMS / HANDHELD inside their host)."""
        if index in SUBSCREENS:
            name, host = SUBSCREENS[index]
            if self._hosted[name]:
                widget, tab = self.screens[host], host
                show_page = getattr(widget, "show_page", None)
                if callable(show_page):
                    show_page(name)
            else:
                widget, tab = self.stack.widget(self._pages[name]), host
        else:
            index = max(0, min(TUNE, index))
            widget = self.screens[index]
            tab = index if index < len(TABS) or self.tune_available else -1
        self._current = index
        self.stack.setCurrentWidget(widget)
        self.tabs.set_current(tab)
        focus = QApplication.focusWidget()
        if is_typing(focus):
            focus.clearFocus()
        widget.refresh(self.session.bus.now())
        self._update_info()

    def _update_info(self) -> None:
        self.tabs.set_info(self.title_text() or getattr(self, "_link_info", ""))

    def current(self) -> int:
        return self._current

    def all_screens(self) -> list:
        """Every screen the shell drives itself, once: the tabs' and the sub-screens
        standing on their own (a host refreshes and rethemes the ones it holds)."""
        out, seen = [], set()
        alone = [getattr(self, name) for name, hosted in self._hosted.items() if not hosted]
        for screen in self.screens + alone:
            if id(screen) not in seen:
                seen.add(id(screen))
                out.append(screen)
        return out

    def retheme(self) -> None:
        self.settings.setValue("theme", T.name)
        apply_palette(QApplication.instance())
        self.status.retheme()
        for screen in self.all_screens():
            hook = getattr(screen, "retheme", None)
            if hook is not None:
                hook()
        for widget in self.findChildren(QWidget):
            QWidget.update(widget)      # not widget.update(): item views override it with update(index) on Debian's PySide6
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.banner.setGeometry(0, TOPBAR_H, self.width(), 44)

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
            menu.addAction(f"Disconnect {s.port}", self.disconnect)
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
        screens = menu.addMenu("Go to")
        screens.addAction("CYD black box (handheld)", lambda: self.show_screen(HANDHELD))
        screens.addAction("Alarms", lambda: self.show_screen(ALARMS))
        screens.addAction("Time in cell (Map 3D)", lambda: self.show_screen(MAP3D))
        if self.tune_available:
            screens.addAction("Tune (ROM tables)", lambda: self.show_screen(TUNE))
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
        self._wire_replay_tiles()               # a new layer: Replay's pane follows
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
        """A port picked by hand: the automatic pick leaves it alone from now on."""
        if not port:
            return
        if self.auto is not None:
            self.auto.pause()
        self._connect_now(port)

    def _connect_now(self, port: str) -> None:
        self.settings.setValue("port/last", port)
        if getattr(self.session.ecu, "poller", None) is not None:
            self.session.disconnect()       # one source at a time: the old link closes first
        self.session.connect(port)
        self.say(f"Connecting to {port}")

    def disconnect(self) -> None:
        """Disconnect by hand: the automatic pick does not connect again by itself."""
        if self.auto is not None:
            self.auto.pause()
        self.session.disconnect()

    # ---- the automatic pick of the ECU source ----

    def start_auto(self, preferred: str = "", scanner=None) -> None:
        """Connect by itself, now and whenever a cable comes or goes: the KKL
        cable (``preferred`` first), else the CYD as ``cyd:``, else nothing.
        Never a simulator."""
        from . import autoconnect as ac
        if self.auto is not None:
            self.auto.stop()
        self.auto = ac.AutoConnect(self.session, connect=self._auto_connect, disconnect=self.session.disconnect,
                                   preferred=preferred, scanner=scanner or ac.scan)
        found = self.auto.scan_now()
        message = self.auto.step(found, time.monotonic())
        if not message and not self.session.connected:
            message = "No KKL cable and no CYD found: plug one in (it is picked up by itself)"
        self.say(message)
        self.auto.start()

    def _auto_connect(self, port: str) -> None:
        self.settings.setValue("port/last", port)
        self.session.connect(port)

    def connect_through_cyd(self) -> str:
        """Setup > Cable's "Connect through the CYD": the CYD reads the ECU on its
        own K-line cable. Whatever was connected closes first."""
        from . import autoconnect as ac
        found = self.auto.scan_now() if self.auto is not None else ac.scan()
        cyds = [f for f in found if f.is_cyd]
        if not cyds:
            return "No CYD found (CH340 1a86:7523): plug its USB-C cable in"
        self.connect_port(ac.CYD_PREFIX + cyds[0].device)
        return f"Connecting through the CYD on {cyds[0].device}"

    def resume_auto(self) -> str:
        if self.auto is None:
            self.start_auto()
            return "Cable: picked by itself (the KKL cable first, else through the CYD)"
        return self.auto.resume()

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
        for state in self.session.ordered("ECU") + (self.session.ordered("BB") if self.show_bb else []):
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
        if self.auto is not None:
            note = self.auto.tick()
            if note:
                self.say(note)
        for event in s.pump(now):
            if event.kind == "alarm":
                self.beep(2)
            elif event.kind == "stall":
                self.banner.show_stall(f"STALL {event.payload.wall:%H:%M:%S}", event.text)
                self.beep(2)
            elif event.text:
                self.say(event.text)
        self.status.sync(s, now)
        self.strip.set_badge(self.status.badge)
        self.strip.sync(s.alarms, now)
        if now - self._info_t >= 1.0:
            self._info_t = now
            n, unverified = s.counts()
            link = getattr(s.ecu, "link_text", lambda: "")()
            gps = " · ".join(t for t in (getattr(x, "info_text", lambda: "")() for x in s.extra_sources) if t)
            self._link_info = (f"{s.port or 'no port'} · map {s.map_name()} · {n} ch "
                               f"· {unverified} unverified" + (f" · {link}" if link else "")
                               + (f" · {gps}" if gps else ""))
            self._update_info()
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
        typing = is_typing(QApplication.focusWidget())
        if Qt.Key_F1 <= key <= Qt.Key_F8:
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
        handler = getattr(self.stack.currentWidget(), "key", None)
        return bool(handler(event)) if handler is not None else False

    def closeEvent(self, event) -> None:
        self.timer.stop()
        if self.auto is not None:
            self.auto.stop()
        self.map.close_tiles()
        shutdown = getattr(self.handheld, "shutdown", None)
        if shutdown is not None:
            shutdown()
        QApplication.instance().removeEventFilter(self)
        try:
            T.changed.disconnect(self.retheme)
        except (RuntimeError, TypeError):
            pass
        self.session.close()
        super().closeEvent(event)
