"""F8, Setup: everything that is set once and left alone, one page at a time.

Alarms (``screens/alarms.py``), the CYD black box (``screens/handheld.py``:
status, SD card drives, config, the offline map push, console, firmware),
display units, the cable and ports, files (the drive database and the
address map in and out), the theme (Dracula or Darker), opening a ROM (which
brings the Tune tab) and About.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QListWidget, QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from ... import __version__, addressmap
from ...mapping import mapedit
from .. import paint
from ..controls import Button
from ..theme import DARKER, DRACULA, T
from ..units import PRESETS, QUANTITIES, U
from .alarms import AlarmsScreen
from .bytesmap import map_path_for
from .common import TAB_H, TabStrip, label, restyle

PAGES = (("alarms", "Alarms"), ("cyd", "CYD black box"), ("units", "Units"), ("cable", "Cable / ports"),
         ("files", "Files"), ("theme", "Theme"), ("rom", "ROM / Tune"), ("about", "About"))
ALIASES = {"handheld": "cyd"}
KEYS = (("F1", "Live"), ("F2", "Drive"), ("F3", "Events"), ("F4", "Replay"), ("F5", "Power"), ("F6", "GPS map"),
        ("F7", "Bytes"), ("F8", "Setup"), ("F9", "Dracula ↔ Darker"), ("F11", "Full screen"), ("Space", "Mark"),
        ("Esc", "Close panels, acknowledge"), ("Ctrl+R", "Start / stop recording"))


def _box(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("box", True)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    lay.addWidget(label(title.upper(), head=True))
    return frame, lay


def export_database(src: Path, dst: Path) -> Path:
    """A consistent copy of the drive database (SQLite's backup: safe while recording)."""
    dst = Path(dst)
    if dst.resolve() == Path(src).resolve():
        raise OSError("that is the database itself")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{Path(src).as_posix()}?mode=ro", uri=True) as source, sqlite3.connect(dst) as out:
        source.backup(out)
    return dst


class UnitsPage(QWidget):
    changed = Signal()

    def __init__(self, settings: QSettings, shell=None, parent=None):
        super().__init__(parent)
        self.settings, self.shell = settings, shell
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        frame, box = _box("Display units")
        row = QHBoxLayout()
        self.preset_btns = {}
        for name in PRESETS:
            btn = Button({"US": "US: mph, °F, gal/h", "Metric": "Metric: km/h, °C, L/h"}.get(name, name), height=40)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, n=name: self.set_preset(n))
            row.addWidget(btn)
            self.preset_btns[name] = btn
        row.addStretch(1)
        box.addLayout(row)
        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for field, title, base in QUANTITIES:
            combo = QComboBox()
            combo.addItems(U.choices(base))
            combo.currentTextChanged.connect(lambda unit, f=field: self.set_unit(f, unit))
            form.addRow(label(title), combo)
            self.combos[field] = combo
        box.addLayout(form)
        box.addWidget(label("Only what is shown changes: logs and exports stay in the address map's units "
                            "(MAP stays kPa).", dim=True, wrap=True))
        lay.addWidget(frame)
        lay.addStretch(1)
        self.sync()

    def sync(self) -> None:
        current, preset = U.as_dict(), U.preset_name()
        for name, btn in self.preset_btns.items():
            btn.setChecked(name == preset)
        for field, combo in self.combos.items():
            combo.blockSignals(True)
            combo.setCurrentText(current[field])
            combo.blockSignals(False)

    def _apply(self, **kw) -> None:
        hook = getattr(self.shell, "set_units", None)
        if callable(hook):
            hook(**kw)                      # saves, tells the map, repaints, says so
        else:
            if kw.get("preset"):
                U.preset(kw["preset"])
            else:
                U.set_unit(kw["field"], kw["unit"])
            U.save(self.settings)
        self.sync()
        self.changed.emit()

    def set_preset(self, name: str) -> None:
        self._apply(preset=name)

    def set_unit(self, field: str, unit: str) -> None:
        if unit and U.as_dict().get(field) != unit:
            self._apply(field=field, unit=unit)


class CablePage(QWidget):
    message = Signal(str)

    def __init__(self, session, settings: QSettings, shell=None, parent=None):
        super().__init__(parent)
        self.session, self.settings, self.shell = session, settings, shell
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        frame, box = _box("ECU cable (K-line)")
        self.state = label("", mono=True)
        box.addWidget(self.state)
        self.ports = QListWidget()
        self.ports.setFont(T.font(14, 600, mono=True))
        self.ports.setMaximumHeight(170)
        box.addWidget(self.ports)
        row = QHBoxLayout()
        self.rescan_btn = Button("Rescan", height=40)
        self.connect_btn = Button("Connect", kind="primary", height=40)
        self.disconnect_btn = Button("Disconnect", height=40)
        self.first_btn = Button("Run first contact…", height=40)
        for b in (self.rescan_btn, self.connect_btn, self.disconnect_btn, self.first_btn):
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        self.auto_record = QCheckBox("Record on connect")
        self.auto_record.setChecked(bool(getattr(session, "auto_record", True)))
        box.addWidget(self.auto_record)
        box.addWidget(label("The CYD black box has its own cable and its own page (CYD black box). The poke test "
                            "(F7 Bytes) borrows this cable and gives it back.", dim=True, wrap=True))
        lay.addWidget(frame)
        tiles, tbox = _box("Map tiles (F6 GPS map)")
        self.tiles_online = QCheckBox("Download map tiles (off: only what is cached)")
        self.tiles_online.setChecked(str(settings.value("map/online", "true")) == "true")
        tbox.addWidget(self.tiles_online)
        self.tile_server_btn = Button("Tile server…", height=40)
        trow = QHBoxLayout()
        trow.addWidget(self.tile_server_btn)
        trow.addStretch(1)
        tbox.addLayout(trow)
        lay.addWidget(tiles)
        lay.addStretch(1)
        self.rescan_btn.clicked.connect(self.rescan)
        self.connect_btn.clicked.connect(self.connect_selected)
        self.disconnect_btn.clicked.connect(self.disconnect)
        self.first_btn.clicked.connect(lambda: getattr(self.shell, "first_contact", lambda: None)())
        self.auto_record.toggled.connect(self.set_auto_record)
        self.tiles_online.toggled.connect(self.set_tiles_online)
        self.tile_server_btn.clicked.connect(lambda: getattr(self.shell, "ask_tile_server", lambda: None)())
        self.first_btn.setVisible(hasattr(getattr(session, "ecu", None), "first_contact") and shell is not None)
        self.tile_server_btn.setVisible(shell is not None and hasattr(shell, "ask_tile_server"))
        self._shown = None
        self.rescan()

    def rescan(self) -> None:
        self.ports.clear()
        last = str(self.settings.value("port/last", ""))
        try:
            found = list(self.session.ports())
        except Exception as exc:
            found = []
            self.message.emit(f"Ports: {exc}")
        for device, text in found:
            self.ports.addItem(text)
            self.ports.item(self.ports.count() - 1).setData(Qt.UserRole, device)
            if device == last:
                self.ports.setCurrentRow(self.ports.count() - 1)
        if not found:
            self.ports.addItem("(no serial ports found)")
            self.ports.item(0).setFlags(Qt.NoItemFlags)
        self.sync()

    def selected_port(self) -> str:
        item = self.ports.currentItem()
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    def connect_selected(self) -> None:
        port = self.selected_port()
        if not port:
            self.message.emit("Pick a port first")
            return
        hook = getattr(self.shell, "connect_port", None)
        if callable(hook):
            hook(port)
        else:
            self.settings.setValue("port/last", port)
            self.session.connect(port)
            self.message.emit(f"Connecting to {port}")
        self.sync()

    def disconnect(self) -> None:
        self.session.disconnect()
        self.sync()

    def set_auto_record(self, on: bool) -> None:
        self.session.auto_record = on
        self.settings.setValue("record/auto", "true" if on else "false")

    def set_tiles_online(self, on: bool) -> None:
        hook = getattr(self.shell, "_set_tiles", None)
        if callable(hook):
            hook(online=on)
        else:
            self.settings.setValue("map/online", "true" if on else "false")

    def sync(self) -> None:
        s = self.session
        shown = (s.connected, s.port)
        if shown == self._shown:
            return
        self._shown = shown
        self.state.setText(f"Connected: {s.port}" if s.connected else "Not connected")
        self.connect_btn.setEnabled(not s.connected)
        self.disconnect_btn.setEnabled(bool(s.connected))


class FilesPage(QWidget):
    message = Signal(str)
    mapChanged = Signal()

    def __init__(self, session, settings: QSettings, parent=None):
        super().__init__(parent)
        self.session, self.settings = session, settings
        #: asks before a file is overwritten; a test replaces it. (title, text) -> bool
        self.confirm = self._confirm_box
        from ...data.db import db_path
        self.db = db_path()
        self.map_path, self.map_writable = map_path_for(session)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        frame, box = _box("Drive database (hondalog.db)")
        box.addWidget(label(str(self.db), mono=True, wrap=True))
        row = QHBoxLayout()
        self.export_db_btn = Button("Export a copy…", height=40)
        self.import_log_btn = Button("Import a log (CSV / MSL / MLG)…", height=40)
        self.folder_btn = Button("Open the folder", height=40)
        for b in (self.export_db_btn, self.import_log_btn, self.folder_btn):
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(label("The copy is made with SQLite's backup, so it is safe while recording. An imported log "
                            "becomes a drive in the database (Replay, Power and Events can open it).", dim=True,
                            wrap=True))
        lay.addWidget(frame)
        mframe, mbox = _box("Address map")
        demo = " (the demo edits a copy, never your map)" if getattr(session, "demo", False) else ""
        mbox.addWidget(label(f"{self.map_path}{demo}", mono=True, wrap=True))
        mrow = QHBoxLayout()
        self.export_map_btn = Button("Export the map…", height=40)
        self.import_map_btn = Button("Import a map…", height=40)
        for b in (self.export_map_btn, self.import_map_btn):
            mrow.addWidget(b)
        mrow.addStretch(1)
        mbox.addLayout(mrow)
        mbox.addWidget(label("An imported map is checked first and the differences shown; the old file is kept "
                             "as .bak. F7 Bytes > Address map edits it in place.", dim=True, wrap=True))
        lay.addWidget(mframe)
        lay.addStretch(1)
        self.export_db_btn.clicked.connect(self._export_db_dialog)
        self.import_log_btn.clicked.connect(self._import_log_dialog)
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.db.parent))))
        self.export_map_btn.clicked.connect(self._export_map_dialog)
        self.import_map_btn.clicked.connect(self._import_map_dialog)
        self.import_map_btn.setEnabled(self.map_writable)
        self.export_db_btn.setEnabled(self.db.exists())

    def _confirm_box(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    # -- database --

    def _export_db_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export hondalog.db", "hondalog-copy.db", "SQLite (*.db)")
        if path:
            self.export_db(path)

    def export_db(self, path) -> bool:
        try:
            out = export_database(self.db, Path(path))
        except (OSError, sqlite3.Error) as exc:
            self.message.emit(f"Database not exported: {exc}")
            return False
        self.message.emit(f"Database copied to {out}")
        return True

    def _import_log_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import a log", "", "Logs (*.csv *.msl *.mlg)")
        if path:
            self.import_log(path)

    def import_log(self, path) -> bool:
        logs = getattr(self.session, "logs", None)
        if logs is None or not hasattr(logs, "import_file"):
            self.message.emit("This session cannot import logs")
            return False
        try:
            ref = logs.import_file(path)
        except (OSError, ValueError) as exc:
            self.message.emit(f"Not imported: {exc}")
            return False
        self.message.emit(f"Imported {Path(path).name} as {getattr(ref, 'name', ref)}")
        return True

    # -- map --

    def _export_map_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export the address map", "addressmap.toml", "TOML (*.toml)")
        if path:
            self.export_map(path)

    def export_map(self, path) -> bool:
        try:
            shutil.copyfile(self.map_path if self.map_path.exists() else addressmap.DEFAULT_MAP, path)
        except OSError as exc:
            self.message.emit(f"Map not exported: {exc}")
            return False
        self.message.emit(f"Address map copied to {path}")
        return True

    def _import_map_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import an address map", "", "TOML (*.toml)")
        if path:
            self.import_map(path)

    def import_map(self, path) -> bool:
        """Check the file, show what changes, then write it over the map (old one kept as .bak)."""
        if not self.map_writable:
            self.message.emit("The shipped map is read-only")
            return False
        try:
            text = Path(path).read_text(encoding="utf-8")
            new = addressmap.loads(text, path=str(path))
        except (OSError, addressmap.MapError) as exc:
            self.message.emit(f"Not a valid map: {exc}")
            return False
        try:
            old = addressmap.load(self.map_path)
            summary = mapedit.diff_maps(old, new).text()
        except addressmap.MapError:
            summary = "(the current map is not valid)"
        if not self.confirm("Import the address map", f"{Path(path).name} -> {self.map_path}\n\n{summary[:1500]}"):
            return False
        try:
            mapedit.MapFile(text, self.map_path).save(self.map_path)
        except (OSError, addressmap.MapError) as exc:
            self.message.emit(f"Map not written: {exc}")
            return False
        reload_map = getattr(getattr(self.session, "ecu", None), "reload_map", None)
        if callable(reload_map) and not getattr(self.session, "demo", False):
            try:
                reload_map()
            except Exception:
                pass
        self.mapChanged.emit()
        self.message.emit(f"Address map {new.version} imported (old file kept as .bak); reconnect to decode with it")
        return True


class ThemeSwatch(QWidget):
    """One theme drawn in its own colours: ground, panel, sunk and the inks."""

    clicked = Signal(str)

    def __init__(self, tokens, title: str, note: str, parent=None):
        super().__init__(parent)
        self.tokens, self.title, self.note = tokens, title, note
        self.setMinimumSize(300, 190)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, _event) -> None:
        self.clicked.emit(self.tokens.name)

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QColor
        t = self.tokens
        p = QPainter(self)
        r = QRectF(self.rect())
        on = T.name == t.name
        paint.frame(p, r, 4 if on else 2, T.accent if on else T.rule, QColor(t.bg))
        inner = r.adjusted(14, 44, -14, -50)
        p.fillRect(inner, QColor(t.panel))
        p.fillRect(QRectF(inner.x() + 10, inner.y() + 10, inner.width() - 20, 26), QColor(t.sunk))
        for i, ink in enumerate((t.series1, t.series2, t.series3, t.series4, t.series5)):
            p.fillRect(QRectF(inner.x() + 10 + i * 34, inner.bottom() - 30, 26, 20), QColor(ink))
        p.setFont(T.font(18, 800))
        p.setPen(QColor(t.fg))
        p.drawText(QRectF(14, 8, r.width() - 28, 30), Qt.AlignVCenter | Qt.AlignLeft,
                   self.title + ("  ✓" if on else ""))
        p.setFont(T.font(13, 600))
        p.setPen(QColor(t.fg2))
        p.drawText(QRectF(14, r.height() - 44, r.width() - 28, 36), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap,
                   self.note)
        p.end()


class ThemePage(QWidget):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        row = QHBoxLayout()
        self.swatches = [ThemeSwatch(DRACULA, "Dracula", "The default. Panels a step darker than the ground."),
                         ThemeSwatch(DARKER, "Darker", "Deeper grounds, the same inks: night driving.")]
        for sw in self.swatches:
            sw.clicked.connect(self.pick)
            row.addWidget(sw)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(label("F9 switches between them anywhere. There is no light theme: no white screens.",
                            dim=True, wrap=True))
        lay.addStretch(1)

    def pick(self, name: str) -> None:
        T.set(name)
        self.settings.setValue("theme", T.name)
        for sw in self.swatches:
            sw.update()


class RomPage(QWidget):
    message = Signal(str)
    romOpened = Signal(str)

    def __init__(self, session, shell=None, parent=None):
        super().__init__(parent)
        self.session, self.shell = session, shell
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        frame, box = _box("ROM image (Tune)")
        self.state = label("", mono=True, wrap=True)
        box.addWidget(self.state)
        box.addWidget(label("Tune edits the ROM's fuel and ignition tables (P28-family chips). It stays hidden until "
                            "a ROM is open: the stock P2P has no chip to write.", dim=True, wrap=True))
        row = QHBoxLayout()
        self.open_btn = Button("Open a ROM…", kind="primary", height=40)
        self.def_btn = Button("With a definition…", height=40)
        row.addWidget(self.open_btn)
        row.addWidget(self.def_btn)
        row.addStretch(1)
        box.addLayout(row)
        lay.addWidget(frame)
        lay.addStretch(1)
        self.open_btn.clicked.connect(lambda: self._dialog(False))
        self.def_btn.clicked.connect(lambda: self._dialog(True))
        self.sync()

    def sync(self) -> None:
        rom = getattr(self.session, "rom", None)
        self.state.setText(f"Open: {rom}  · the Tune tab is shown" if rom else "No ROM open: the Tune tab is hidden")

    def _dialog(self, with_definition: bool) -> None:
        rom, _ = QFileDialog.getOpenFileName(self, "Open a ROM image", "", "ROM (*.bin *.rom);;All files (*)")
        if not rom:
            return
        definition = None
        if with_definition:
            definition, _ = QFileDialog.getOpenFileName(self, "ROM definition", "", "Definition (*.toml *.xdf)")
            definition = definition or None
        self.open_rom(rom, definition)

    def open_rom(self, rom, definition=None) -> bool:
        tables = getattr(self.session, "tables", None)
        opener = getattr(tables, "open", None)
        if callable(opener):
            problem = opener(rom, definition)
            if problem:
                self.message.emit(f"ROM not opened: {problem}")
                return False
        self.session.rom = str(rom)
        shell = self.shell
        if shell is not None and hasattr(shell, "tabs") and hasattr(shell, "tab_list"):
            shell.tabs.set_tabs(shell.tab_list())          # Tune gets its tab
        self.sync()
        self.romOpened.emit(str(rom))
        self.message.emit(f"ROM {Path(rom).name} open: Tune is on the tab row")
        return True


class AboutPage(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        from ...data.db import db_path
        from ...logger import log_dir
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        frame, box = _box(f"hondalog {__version__}")
        box.addWidget(label("A K-line data logger for the 2000 Civic EX (stock P2P ECU, D16Y8), with the CYD "
                            "handheld as its black box. Read-only towards the ECU unless Expert is unlocked in "
                            "F7 Bytes.", wrap=True))
        map_path, _ = map_path_for(session)
        grid = QGridLayout()
        for i, (k, v) in enumerate((("Drive database", db_path()), ("Address map", map_path),
                                    ("Log folder", log_dir()), ("Mode", "demo (simulated car)"
                                                                if getattr(session, "demo", False) else "live"))):
            grid.addWidget(label(k, dim=True), i, 0)
            grid.addWidget(label(str(v), mono=True), i, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        lay.addWidget(frame)
        kframe, kbox = _box("Keys")
        kgrid = QGridLayout()
        for i, (k, v) in enumerate(KEYS):
            kgrid.addWidget(label(k, mono=True), i % 7, (i // 7) * 2)
            kgrid.addWidget(label(v), i % 7, (i // 7) * 2 + 1)
        kgrid.setColumnStretch(3, 1)
        kbox.addLayout(kgrid)
        lay.addWidget(kframe)
        lay.addStretch(1)


class SetupScreen(QWidget):
    message = Signal(str)
    romOpened = Signal(str)

    def __init__(self, session, settings: QSettings | None = None, shell=None, parent=None):
        super().__init__(parent)
        from .handheld import HandheldScreen
        self.setObjectName("setuproot")
        self.session = session
        self.settings = settings if settings is not None else QSettings("hondalog", "hondalog")
        self.shell = shell
        self.tabs = TabStrip(PAGES, "alarms", self)
        self.stack = QStackedWidget(self)
        self.alarms = AlarmsScreen(session)
        self.handheld = HandheldScreen(session, self.settings)
        self.units = UnitsPage(self.settings, shell)
        self.cable = CablePage(session, self.settings, shell)
        self.files = FilesPage(session, self.settings)
        self.theme_page = ThemePage(self.settings)
        self.rom = RomPage(session, shell)
        self.about = AboutPage(session)
        self.pages = {"alarms": self.alarms, "cyd": self.handheld, "units": self.units, "cable": self.cable,
                      "files": self.files, "theme": self.theme_page, "rom": self.rom, "about": self.about}
        for page in self.pages.values():
            self.stack.addWidget(page)
        for page in (self.handheld, self.cable, self.files, self.rom):
            page.message.connect(self.message)
        self.rom.romOpened.connect(self.romOpened)
        self.tabs.picked.connect(self.show_page)
        self.page = "alarms"
        self.retheme()

    def show_page(self, name: str) -> None:
        name = ALIASES.get(name, name)
        if name not in self.pages:
            return
        self.page = name
        self.tabs.set_current(name)
        self.stack.setCurrentWidget(self.pages[name])
        if name == "units":
            self.units.sync()
        elif name == "rom":
            self.rom.sync()
        elif name == "cable":
            self.cable.sync()

    def refresh(self, now: float) -> None:
        if self.page == "alarms":
            self.alarms.refresh(now)
        elif self.page == "cyd":
            self.handheld.refresh(now)
        elif self.page == "cable":
            self.cable.sync()

    def retheme(self) -> None:
        restyle(self, "setuproot")
        self.alarms.retheme()
        self.handheld.retheme()
        self.update()

    def key(self, event) -> bool:
        if event.key() == Qt.Key_Tab:
            self.tabs.step(1)
            return True
        if self.page == "alarms":
            return bool(self.alarms.key(event))
        return False

    def shutdown(self) -> None:
        self.handheld.shutdown()

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        self.tabs.setGeometry(8, 8, w - 16, TAB_H)
        top = 8 + TAB_H + 6
        self.stack.setGeometry(0, top, w, h - top)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.end()
