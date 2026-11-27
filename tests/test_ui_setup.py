"""F8 Setup offscreen: the pages, alarms and the CYD black box hosted, units,
theme, files (database copy, map in and out) and opening a ROM."""

import shutil
import sqlite3

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractButton

from hondalog import addressmap
from hondalog.ui import theme
from hondalog.ui.screens import setup as su
from hondalog.ui.screens.alarms import AlarmsScreen
from hondalog.ui.screens.handheld import HandheldScreen
from hondalog.ui.units import U


@pytest.fixture
def session():
    from hondalog.ui.session import build_session
    return build_session(demo=True, demo_clock=lambda: 100.0)


@pytest.fixture
def screen(app, settings, session):
    scr = su.SetupScreen(session, settings)
    scr.resize(1366, 648)
    scr.show()
    app.processEvents()
    yield scr
    scr.shutdown()
    scr.close()
    theme.T.set("dracula")
    U.set(None)


def test_setup_pages_host_alarms_and_the_cyd(screen, app):
    assert isinstance(screen.alarms, AlarmsScreen) and isinstance(screen.handheld, HandheldScreen)
    assert [k for k, _ in su.PAGES] == ["alarms", "cyd", "units", "cable", "files", "theme", "rom", "about"]
    assert dict(su.PAGES)["cyd"] == "CYD black box"
    screen.show_page("handheld")                    # the shell's name for the CYD page
    assert screen.page == "cyd" and screen.stack.currentWidget() is screen.handheld
    assert screen.key(QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier)) and screen.page == "units"
    for name, _ in su.PAGES:
        screen.show_page(name)
        screen.refresh(100.0)
        app.processEvents()
        page = screen.stack.currentWidget()
        assert page.geometry().bottom() <= screen.height()
        screen.grab()


def test_cyd_page_says_cyd_not_handheld(screen):
    texts = [b.text() for b in screen.handheld.findChildren(QAbstractButton)]
    assert "Restart the CYD" in texts
    assert not [t for t in texts if "handheld" in t.lower()]


def test_units_and_theme(screen, settings):
    screen.units.set_preset("Metric")
    assert U.preset_name() == "Metric" and screen.units.preset_btns["Metric"].isChecked()
    screen.units.set_preset("US")
    assert U.as_dict()["speed"] == "mph" and screen.units.combos["speed"].currentText() == "mph"
    screen.units.combos["speed"].setCurrentText("km/h")
    assert U.as_dict()["speed"] == "km/h" and str(settings.value("units/speed", "")) in ("km/h", "")
    screen.theme_page.pick("darker")
    assert theme.T.darker and settings.value("theme") == "darker"
    screen.theme_page.pick("dracula")
    assert not theme.T.darker


def test_files_database_copy_and_map_import(screen, tmp_path):
    files = screen.files
    src = tmp_path / "hondalog.db"
    with sqlite3.connect(src) as db:
        db.execute("create table drives (id integer, name text)")
        db.execute("insert into drives values (1, 'drive_2026_11_26_2251')")
    out = su.export_database(src, tmp_path / "copy" / "hondalog-copy.db")
    with sqlite3.connect(out) as db:
        assert db.execute("select name from drives").fetchone()[0] == "drive_2026_11_26_2251"
    with pytest.raises(OSError):
        su.export_database(src, src)

    target = tmp_path / "map" / "addressmap.toml"
    target.parent.mkdir()
    shutil.copyfile(addressmap.DEFAULT_MAP, target)
    files.map_path, files.map_writable = target, True
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 'x'\n[[channel]]\nname = 'rpm'\naddr = 0x1FF\n", encoding="utf-8")
    assert not files.import_map(bad)
    new = tmp_path / "new.toml"
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8")
    old_version = addressmap.load(addressmap.DEFAULT_MAP).version
    new.write_text(text.replace(f'version = "{old_version}"', 'version = "p2p-guess-99"', 1), encoding="utf-8")
    asked = []
    files.confirm = lambda title, body: (asked.append(body), True)[1]
    assert files.import_map(new)
    assert "version" in asked[0] and addressmap.load(target).version == "p2p-guess-99"
    assert target.with_name(target.name + ".bak").exists()
    assert files.export_map(tmp_path / "out.toml") and (tmp_path / "out.toml").read_text(encoding="utf-8")


def test_rom_page_opens_a_rom(screen, session, tmp_path):
    calls = []

    class Tables:
        problem = ""

        def open(self, rom, definition=None):
            calls.append((rom, definition))
            return self.problem

    session.tables = Tables()
    session.rom = None
    rom = tmp_path / "p28.bin"
    rom.write_bytes(b"\0" * 32768)
    seen = []
    screen.romOpened.connect(seen.append)
    assert screen.rom.open_rom(rom)
    assert session.rom == str(rom) and seen == [str(rom)] and "Tune tab is shown" in screen.rom.state.text()
    session.tables.problem = "no definition"
    session.rom = None
    assert not screen.rom.open_rom(rom) and session.rom is None
