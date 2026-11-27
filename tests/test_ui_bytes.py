"""F7 Bytes offscreen: the memory scanner page, the address-map editor, the
unknown-bytes list and the poke-test launcher."""

import shutil
import time

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from hondalog import addressmap
from hondalog.ui.screens import bytesmap as bm
from hondalog.ui.screens.rawtools import BytesScreen, RawScreen


def press(screen, k):
    return screen.key(QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))


@pytest.fixture
def session():
    from hondalog.ui.session import build_session
    return build_session(demo=True, demo_clock=lambda: 100.0)


@pytest.fixture
def screen(app, settings, session):
    scr = BytesScreen(session, settings)
    scr.resize(1366, 648)
    scr.show()
    app.processEvents()
    yield scr
    scr.shutdown()
    scr.close()


def test_bytes_pages_and_keys(screen, app):
    raw = screen.raw
    assert isinstance(raw, RawScreen) and screen.page == "memory"
    screen.refresh(100.0)
    # the hex grid gives way so the console still fits under it
    assert raw.grid.row_h < 26
    assert raw.console.geometry().bottom() <= raw.height() and raw.console.height() >= 90
    assert press(screen, Qt.Key_S) and screen.session.mapper.sweeping      # memory keys still work
    press(screen, Qt.Key_S)
    assert press(screen, Qt.Key_Tab) and screen.page == "map"
    assert not press(screen, Qt.Key_S)                                     # other pages: not the scanner's
    for name in ("unknown", "poke", "memory"):
        screen.show_page(name)
        assert screen.stack.currentWidget() is screen.pages[name]
        screen.grab()


def test_unknown_list_seed_notes_and_rank(screen, settings):
    page = screen.unknown
    keys = [r["key"] for r in page.model.rows]
    assert "0x27" in keys and "0x0B b6" in keys and "0x2F" in keys and "0x3C" in keys
    row = page.model.find("0x27")
    assert "1/4°" in row["what"] and row["source"].endswith("index-bytes.md")
    page.model.set("0x27", note="watch it at WOT", confidence="high")
    again = bm.UnknownList(settings)
    assert again.find("0x27")["note"] == "watch it at WOT" and again.find("0x27")["confidence"] == "high"
    page.add_edit.setText("0x41 b3")
    assert page.add_from_field() and bm.UnknownList(settings).find("0x41 b3") is not None
    page.add_edit.setText("0x1FF")
    assert not page.add_from_field()
    assert bm.parse_key("0b b6") == (0x0B, 6) and bm.parse_key("0x0B b9") is None
    # the table shows who owns a byte in the map
    page.fill()
    i = keys.index("0x1C") if "0x1C" in keys else 0
    assert page.table.item(i, 0).text() == page.model.rows[i]["key"]
    # "Rank against it": the memory page at that byte with the correlation finder open
    screen.rank_byte(0x2F)
    assert screen.page == "memory" and screen.raw.grid.sel == 0x2F
    assert screen.raw.rank is None or screen.raw.rank.isVisible()


def test_map_page_validate_edit_bump_diff_save(screen, tmp_path):
    page = screen.map_page
    path = tmp_path / "addressmap.toml"
    shutil.copyfile(addressmap.DEFAULT_MAP, path)
    page.path, page.writable = path, True
    page.reload()
    assert page.amap is not None and page.status.text().startswith("VALID")
    assert f"#{page.amap.version_hash}" in page.status.text()
    good = page.text.toPlainText()
    page.text.setPlainText(good + "\n[[channel]]\nname = 'bad'\naddr = 0x1FF\n")
    assert not page.validate() and page.status.text().startswith("NOT VALID")
    page.text.setPlainText(good)
    assert page.validate()

    name = page.amap.channels[1].name
    assert page.select_channel(name)
    page.f_status.setCurrentText("suspect")
    page.f_notes.setText("checked against a meter: wrong")
    assert page.apply_form()
    ch = next(c for c in page.amap.channels if c.name == name)
    assert ch.status == "suspect" and ch.notes == "checked against a meter: wrong"
    assert "# hondalog address map" in page.text.toPlainText()           # comments kept
    old_version = page.amap.version
    new = page.bump_version()
    assert new != old_version and page.amap.version == new
    page.show_diff()
    assert "status" in page.diff_view.toPlainText() and "version" in page.diff_view.toPlainText()

    asked = []
    page.confirm = lambda title, text: (asked.append(text), False)[1]
    assert not page.save() and asked and path.read_text(encoding="utf-8") == good
    page.confirm = lambda title, text: True
    assert page.save()
    saved = addressmap.load(path)
    assert saved.version == new and saved.channel(name).status == "suspect"
    assert path.with_name(path.name + ".bak").exists()
    assert "edited" not in page.status.text()
    page.writable = False
    assert not page.save()


def test_demo_map_page_never_edits_the_users_map(session):
    path, writable = bm.map_path_for(session)
    assert writable and path != addressmap.user_map_path() and "hondalog-demo" in str(path)


class FakeSession:
    demo = False

    def __init__(self):
        self.connected, self.port, self.calls = True, "COM21", []

    def disconnect(self):
        self.calls.append("disconnect")
        self.connected = False

    def connect(self, port):
        self.calls.append(("connect", port))
        self.connected = True


def test_poke_launcher_pauses_and_resumes_the_live_session(app, tmp_path):
    s = FakeSession()
    page = bm.PokePage(s, QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    page.sim.setChecked(False)
    page.port.setText("COM21")
    args = page.arguments()
    assert args[:2] == ["-u", "-c"] and "pt.take_cable" in args[2] and args[3] == "run"
    assert "--port" in args and "COM21" in args and "--dry-run" not in args
    page.sim.setChecked(True)
    assert "--dry-run" in page.arguments() and "--port" not in page.arguments()
    page.pause_live()
    assert s.calls == ["disconnect"] and page.paused_port == "COM21"
    page.pause_live()                                   # only once
    assert s.calls == ["disconnect"]
    page.resume_live()
    assert s.calls[-1] == ("connect", "COM21") and page.paused_port == ""


@pytest.mark.skipif(not bm.POKE_TOOL.exists(), reason="tools/poke_test.py not in this checkout")
def test_poke_launcher_runs_the_tool(app, tmp_path):
    page = bm.PokePage(FakeSession(), QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    assert page.start(listing=True)
    end = time.monotonic() + 60
    while page.running() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()
    assert not page.running() and "baseline" in page.log.toPlainText(), page.log.toPlainText()[-500:]
    assert page.session.calls == []                     # listing the steps takes no cable
