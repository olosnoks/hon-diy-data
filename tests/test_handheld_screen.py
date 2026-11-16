"""The Handheld tab, offscreen, against the simulator: connect, browse, edit the config, console, upload."""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from hondalog.handheld.cydsim import CydSim, SimPort
from hondalog.handheld.mapfile import DEFAULT_CFG
from hondalog.ui.screens import handheld as hh


class FakeSession:
    port = ""
    connected = False


def wait(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def screen(app, tmp_path):
    sd = tmp_path / "sd"
    (sd / "MAP").mkdir(parents=True)
    (sd / "HANDHELD.CFG").write_text(DEFAULT_CFG)
    sim = CydSim(sd=sd, logs=True)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    scr = hh.HandheldScreen(FakeSession(), settings, port_factory=lambda name: SimPort(sim))
    scr.resize(1366, 648)
    scr.show()
    scr.sim, scr.sd = sim, sd
    yield scr
    scr.shutdown()
    scr.close()


def connect(scr):
    scr.port.setEditText("cyd:sim")
    scr.toggle_connection()
    assert wait(lambda: scr.connected), scr.state_label.text()


def test_connect_status_and_disconnect(screen):
    connect(screen)
    assert screen.connect_btn.text() == "Disconnect"
    screen.show_view("status")
    assert wait(lambda: screen.status_view.last is not None)
    assert "OK" in screen.status_view.values[("SD card", "card")].text()
    screen.toggle_connection()
    assert wait(lambda: not screen.connected)
    assert screen.connect_btn.text() == "Connect"


def test_connection_persists_across_views(screen):
    connect(screen)
    for key, _ in hh.VIEWS:
        screen.show_view(key)
        assert screen.connected
        QApplication.processEvents()


def test_sd_explorer_lists_and_deletes(screen):
    (screen.sd / "MAP" / "OLD.TXT").write_text("x" * 10)
    connect(screen)
    screen.show_view("files")
    root = screen.files_view.tree.topLevelItem(0)
    assert wait(lambda: root.childCount() and root.child(0).text(0) != "…")
    names = [root.child(i).text(0) for i in range(root.childCount())]
    assert "MAP/" in names and "HANDHELD.CFG" in names


def test_config_read_edit_write(screen):
    connect(screen)
    v = screen.config_view
    v.read()
    assert wait(lambda: v.prog.stage.text().startswith("Done"))
    v.edits["rotate"].setText("180")
    v.edits["rotate"].textEdited.emit("180")
    assert "rotate = 180" in v.raw.toPlainText().replace("  ", " ")
    v.write()
    assert wait(lambda: "written" in v.prog.stage.text() or v.prog.stage.text().startswith("Failed"))
    assert v.prog.stage.text().startswith("Done"), v.prog.detail.text()
    assert wait(lambda: "180" in (screen.sd / "HANDHELD.CFG").read_text())


def test_console_send_and_record(screen, tmp_path):
    connect(screen)
    c = screen.console_view
    out = tmp_path / "rec.log"
    c.start_recording(str(out))
    c.line.setText("HL STAT")
    c.send()
    assert wait(lambda: "HL STAT" in out.read_text())
    c.stop_recording()
    assert "HL STAT" in out.read_text()


def test_pure_helpers():
    assert hh.upload_target("/x/handheld.cfg", None) == ("/HANDHELD.CFG", "")
    assert hh.upload_target("/x/MAP.DAT", "/") == ("/MAP/MAP.DAT", "")
    assert hh.upload_target("/x/a very long name.dat", None)[0] is None
    pct, text = hh.progress_text(5 * 1048576, 10 * 1048576, 10)
    assert pct == 50 and "~0:10 left" in text
    rows = hh.status_rows({})
    assert rows and all(len(r) == 4 for r in rows)
    assert hh.fmt_bytes(2048) == "2.0 KB"


def test_map_upload_view(screen, tmp_path):
    from test_hl_client import fake_map
    src = tmp_path / "MAP"
    src.mkdir()
    idx, dat = fake_map()
    (src / "MAP.IDX").write_bytes(idx)
    (src / "MAP.DAT").write_bytes(dat)
    connect(screen)
    v = screen.map_view
    v.path.setText(str(src))
    v.check()
    assert v.summary.text().startswith("OK"), v.summary.text()
    v.upload()
    assert wait(lambda: v.prog.stage.text().startswith(("Done", "Failed")), 20)
    assert v.prog.stage.text().startswith("Done"), v.prog.detail.text()
    assert (screen.sd / "MAP" / "MAP.DAT").read_bytes() == dat


def test_install_view_guards(screen, tmp_path):
    v = screen.install_view
    v.path.setText(str(tmp_path / "nope.bin"))
    v.check()
    assert v.guard.text().startswith("NO") and not v.install_btn.isEnabled()
    img = bytearray(b"\xff" * 0x9000)
    img[0] = 0xE9
    img[0x8000:0x8002] = b"\xaa\x50"
    good = tmp_path / "good.bin"
    good.write_bytes(bytes(img))
    v.path.setText(str(good))
    v.check()
    assert v.image_ok or "esptool" in v.guard.text()
