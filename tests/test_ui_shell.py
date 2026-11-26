"""The whole shell offscreen at 1366x768: layout, the keyboard map, theme
switching, and the frame budget. Uses pytest-qt when it is installed and
plain QTest otherwise."""

import math
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from conftest import spin
from hondalog.ui import main_window as mw
from hondalog.ui import theme
from hondalog.ui.bus import ChannelSpec
from hondalog.ui.tiles import COMPACT, SENSOR, Tile

try:
    import pytestqt  # noqa: F401
    HAVE_QTBOT = True
except ImportError:
    HAVE_QTBOT = False


def key(win, k, mod=Qt.NoModifier, text=""):
    """A key press through the application, as the user would make it."""
    target = QApplication.focusWidget() or win
    if text:
        QTest.keyClicks(target, text, mod)
    else:
        QTest.keyClick(target, k, mod)
    QApplication.processEvents()


@pytest.fixture
def active(shell, app):
    shell.activateWindow()
    QTest.qWaitForWindowActive(shell, 1000)
    if not shell.isActiveWindow():
        pytest.skip("this platform does not activate windows")
    return shell


def test_frame_regions_match_the_handoff(shell):
    assert (shell.width(), shell.height()) == (1366, 768)
    assert shell.status.geometry() == QRect(0, 0, 1366, 44)
    assert shell.tabs.geometry() == QRect(0, 44, 1366, 44)
    assert shell.stack.geometry() == QRect(0, 88, 1366, 648)
    assert shell.strip.geometry() == QRect(0, 736, 1366, 32)
    assert shell.status.mark_btn.width() >= 150


def test_all_sensors_on_one_screen_no_scrolling(shell):
    tiles = shell.sensors.tiles
    assert len(tiles) == 32
    assert [t.state.spec.id for t in tiles[:5]] == ["rpm", "vss", "map", "tps", "inj"]
    area = shell.sensors.rect()
    for t in tiles:
        assert area.contains(t.geometry()), t.state.spec.id
        assert t.height() == 142 and 163 <= t.width() <= 164 and t.m is SENSOR
    for i, a in enumerate(tiles):
        for b in tiles[i + 1:]:
            assert not a.geometry().intersects(b.geometry())
    # rows where the handoff puts them (window y 116, 264, 412 and 580)
    ys = sorted({t.y() + 88 for t in tiles})
    assert ys == [116, 264, 412, 580]


def test_forty_channels_use_the_compact_fifth_row(shell, app):
    for i in range(8):
        shell.session.bus.register(ChannelSpec(f"math{i}", f"Math {i}", source="ECU"))
    spin(app, shell)
    tiles = shell.sensors.tiles
    assert len(tiles) == 40
    assert all(t.m is COMPACT and t.height() <= 112 for t in tiles)
    assert all(shell.sensors.rect().contains(t.geometry()) for t in tiles)


@pytest.mark.parametrize("name", ["daylight", "night"])
def test_every_screen_lays_out_inside_the_window(shell, app, name):
    theme.T.set(name)
    for index, screen in enumerate(shell.screens):
        shell.show_screen(index)
        spin(app, shell)
        assert screen.size() == shell.stack.size()
        for child in screen.findChildren(QWidget):
            if not child.isVisible() or child.parent() is not screen:
                continue
            assert screen.rect().contains(child.geometry()), (
                f"{type(screen).__name__}: {type(child).__name__} {child.geometry()} is clipped")
        image = shell.grab().toImage()
        assert (image.width(), image.height()) == (1366, 768)
        ground = theme.T.bg.rgb()
        other = sum(1 for x in range(20, 1366, 40) for y in range(100, 730, 30)
                    if image.pixel(x, y) != ground)
        assert other > 40, f"{type(screen).__name__} drew almost nothing"


def test_tile_states_are_carried_by_shape_and_fill(shell, app):
    shell.show_screen(mw.ALARMS)
    spin(app, shell)
    tiles = shell.alarms.key_tiles
    states = [(t._level, t._stale) for t in tiles]
    assert states == [("", False), ("", False), ("warn", False), ("alarm", False),
                      ("", True), ("", False), ("", False), ("alarm", False)]
    alarm = tiles[3].grab().toImage()
    assert alarm.pixelColor(80, 100).name().upper() == theme.DAYLIGHT.alarm
    warn = tiles[2].grab().toImage()
    assert warn.pixelColor(100, 6).name().upper() == theme.DAYLIGHT.warn       # header band
    assert warn.pixelColor(100, 100).name().upper() == theme.DAYLIGHT.panel    # body stays plain
    stale = tiles[4].grab().toImage()
    seen = {stale.pixelColor(x, 120).name().upper() for x in range(4, 150)}
    assert theme.DAYLIGHT.sunk in seen and tiles[4]._text == "---"             # hatch + dashes


def test_tile_repaints_only_when_something_visible_changed(shell, app):
    tile = shell.sensors.tiles[0]
    paints = []
    original = Tile.paintEvent
    try:
        Tile.paintEvent = lambda self, ev: (paints.append(self), original(self, ev))[1]
        spin(app, shell, 3)
        paints.clear()
        spin(app, shell, 5)                 # demo clock is frozen: same values every frame
        assert tile not in paints
    finally:
        Tile.paintEvent = original


def test_a_50_hz_tile_redraws_its_value_ten_times_a_second(app):
    """The black box publishes at 50 Hz. Its tiles redraw the value at 10 Hz;
    a channel that arrives slower than the frame timer is drawn at once."""
    from hondalog.ui.bus import ChannelBus, ChannelSpec
    bus = ChannelBus()
    fast = bus.register(ChannelSpec("fpv", "FP feed", "V", 2, 0, 16, source="BB"))
    slow = bus.register(ChannelSpec("ect", "Coolant", "°C", 0, -40, 140))
    tiles = {"fpv": Tile(fast), "ect": Tile(slow)}
    for tile in tiles.values():
        tile.resize(163, 142)
    shown = {"fpv": [], "ect": []}
    t, frame = 0.0, 0
    while t < 2.0:                          # 60 frames; fpv gets a new value every 20 ms
        while fast.t < t:
            bus.publish({"fpv": 12.0 + 0.01 * fast.gen}, t=fast.t + 0.02 if fast.gen else 0.0)
        if frame % 6 == 0:                  # ect: 5 Hz
            bus.publish({"ect": 80.0 + frame}, t=t)
        for cid, tile in tiles.items():
            before = tile._text
            tile.sync(t)
            if tile._text != before:
                shown[cid].append(t)
        t += 1 / 30
        frame += 1
    assert 17 <= len(shown["fpv"]) <= 22                    # about 10 Hz, not 30
    assert len(shown["ect"]) == 10                          # every one of its updates, the frame it came
    assert tiles["fpv"]._text == fast.spec.format(fast.value) or abs(
        float(tiles["fpv"]._text) - fast.value) < 0.06      # never more than 0.1 s behind


@pytest.mark.parametrize("kind", ["value", "flag", "warn", "alarm", "drive"])
def test_a_tile_repainted_band_by_band_equals_one_painted_whole(app, kind):
    """A new value repaints the value band, a new sparkline sample the
    sparkline band. What is on screen after any run of such partial repaints
    must be exactly what one full repaint gives."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap, QRegion
    from hondalog.ui.bus import ChannelBus, ChannelSpec
    from hondalog.ui.tiles import DRIVE

    class Alarms:
        def state(self, _cid):
            return {"warn": "warn", "alarm": "alarm"}.get(kind, "")

        def unacked(self, _cid):
            return False
    bus = ChannelBus()
    spec = (ChannelSpec("vtec", "VTEC", kind="flag") if kind == "flag"
            else ChannelSpec("ect", "Coolant", "°C", 1, -40, 140, verified=True))
    state = bus.register(spec)
    tile = Tile(state, DRIVE if kind == "drive" else SENSOR)
    tile.resize(328, 312) if kind == "drive" else tile.resize(163, 142)
    screen = QPixmap(tile.size())
    updates = []
    real = tile.update
    tile.update = lambda *a: (updates.append(a), real(*a))[1]

    def step(t, value):
        """One frame: publish, sync, and paint only what the tile asked for."""
        bus.publish({spec.id: value}, t=t)
        bus.tick(t)
        updates.clear()
        tile.sync(t, Alarms())
        for args in updates:
            region = QRegion(tile.rect()) if not args else QRegion(*[int(a) for a in args])
            tile.render(screen, region.boundingRect().topLeft(), region)
        return list(updates)

    def values(i):
        return float(i % 2) if kind == "flag" else 60.0 + 30.0 * abs(((i * 7) % 20) - 10) / 10.0

    first = step(0.0, values(0))
    assert first == [()]                                    # the first frame paints the whole tile
    bands = set()
    for i in range(1, 90):                                  # 9 s: values at 10 Hz, sparkline samples at 2 Hz
        for args in step(i * 0.1, values(i)):
            assert args, "after the first frame only bands are repainted"
            bands.add((args[1], args[3]))
    assert len(bands) >= 2                                  # the value band and the sparkline band, apart
    assert all(top >= 2 + tile.m.header_h for top, _ in bands)      # the header is never among them
    whole = QPixmap(tile.size())
    tile.render(whole, QPoint(), QRegion(tile.rect()))
    assert screen.toImage() == whole.toImage()


def test_function_keys_switch_screens_and_f9_toggles_theme(active, app, settings):
    win = active
    for i, k in enumerate((Qt.Key_F1, Qt.Key_F2, Qt.Key_F3, Qt.Key_F4, Qt.Key_F5, Qt.Key_F6,
                           Qt.Key_F7, Qt.Key_F8)):
        key(win, k)
        assert win.current() == i and win.tabs.current == i
    assert not theme.T.night
    key(win, Qt.Key_F10)
    assert win.current() == 8 and win.tabs.current == 8
    key(win, Qt.Key_F9)
    assert theme.T.night and settings.value("theme") == "night"
    assert win.status.theme_btn.text() == "NIGHT"
    key(win, Qt.Key_F9)
    assert not theme.T.night and settings.value("theme") == "daylight"


def test_space_marks_everywhere_but_not_while_typing(active, app):
    win = active
    rec = win.session.recording
    for screen in (mw.SENSORS, mw.DRIVE, mw.MAP3D, mw.TUNE):
        win.show_screen(screen)
        before = rec.marks
        key(win, Qt.Key_Space)
        assert rec.marks == before + 1
    win.show_screen(mw.RAW)
    win.raw.tx.setFocus()
    app.processEvents()
    before = rec.marks
    key(win, Qt.Key_Space)
    assert rec.marks == before and win.raw.tx.text().endswith(" ")
    key(win, Qt.Key_Escape)
    assert not win.raw.tx.hasFocus()
    key(win, Qt.Key_Space)
    assert rec.marks == before + 1


def test_escape_acknowledges_and_dismisses_enter_opens_the_stall(active, app, tmp_path):
    from datetime import datetime
    from hondalog.ui import adapters as ad
    win = active
    alarms = win.session.alarms
    bus = win.session.bus
    bus.channel("ect").spec.alarm_hi = 50.0             # demo coolant is 87: alarm
    alarms._version = -1
    spin(app, win, 3)
    assert alarms.state("ect") == "alarm" and alarms.unacked("ect")
    key(win, Qt.Key_Escape)
    assert alarms.state("ect") == "alarm" and not alarms.unacked("ect")

    cap = ad.StallCapture(tmp_path / "stall_x.csv", "rpm", datetime(2026, 11, 15, 14, 2, 11))
    win.session.banner = cap
    win.banner.show_stall("STALL 14:02:11", "RPM dropped to 0.")
    app.processEvents()
    assert win.banner.isVisible() and win.banner.geometry() == QRect(0, 44, 1366, 44)
    assert win.stack.geometry().y() == 88, "content does not move under the banner"
    key(win, Qt.Key_Escape)
    assert not win.banner.isVisible() and win.session.banner is None
    win.session.banner = cap
    win.banner.show_stall("STALL 14:02:11", "RPM dropped to 0.")
    key(win, Qt.Key_Return)                             # file is missing: stays put, no crash
    assert not win.banner.isVisible()


def test_map3d_keys(active, app):
    win = active
    win.show_screen(mw.MAP3D)
    scene, screen = win.map3d.scene, win.map3d
    az, el, dist = scene.cam.az, scene.cam.el, scene.cam.dist
    key(win, Qt.Key_Right)
    assert scene.cam.az == pytest.approx(az + math.radians(5))
    key(win, Qt.Key_Up)
    assert scene.cam.el == pytest.approx(el + math.radians(5))
    key(win, Qt.Key_PageUp)
    assert scene.cam.dist < dist
    key(win, Qt.Key_2)
    assert (scene.cam.az, scene.cam.el) == (0.0, 1.5)
    key(win, Qt.Key_V)
    assert screen.mode == "hist" and screen.model.mode == "hist"
    key(win, Qt.Key_V)
    assert screen.mode == "stall" and len(screen.model.paths) == 3
    key(win, Qt.Key_Z)
    assert screen.zkey == "ign" and screen.model.z_label.startswith("IGN")
    key(win, Qt.Key_C)
    assert screen.color_by == "hits"
    assert screen.coverage.seen == 77 and screen.coverage.total == 200


def test_replay_keys_cursor_events_and_dual_cursor(active, app):
    win = active
    win.show_screen(mw.REPLAY)
    r, b = win.replay, win.replay.block
    assert r.view.trigger_t == 0.0 and len(b.lanes) == 13 and all(l.has_data for l in b.lanes)
    assert r.loss.rows[0].channel_id == "fpv"
    key(win, Qt.Key_Home)
    assert b.cursor == -30.0
    key(win, Qt.Key_Right)
    assert b.cursor == pytest.approx(-29.9)
    key(win, Qt.Key_Right, Qt.ShiftModifier)
    assert b.cursor == pytest.approx(-28.9)
    key(win, 0, text="]")
    assert b.cursor == pytest.approx(-22.0), "next event is MARK 1"
    key(win, 0, text="]")
    assert b.cursor == pytest.approx(-9.0)
    key(win, 0, text="[")
    assert b.cursor == pytest.approx(-22.0)
    key(win, Qt.Key_D)
    key(win, Qt.Key_End)
    assert b.cursor == 10.0 and b.cursor_b == pytest.approx(-22.0)
    assert r._delta_text == "Δ +32.00 s"
    width = b.t1 - b.t0
    key(win, 0, text="+")
    assert b.t1 - b.t0 == pytest.approx(width / 2) and b.t0 <= b.cursor <= b.t1
    key(win, 0, text="-")
    assert b.t1 - b.t0 == pytest.approx(width)
    r.loss.jump.emit(r.loss.rows[0].t)
    assert b.cursor == pytest.approx(r.loss.rows[0].t)
    assert b.lanes[7].cid == "fpv" and float(b.lanes[7].value) < 13.0
    key(win, Qt.Key_P)
    assert r.playing
    time.sleep(0.05)
    spin(app, win, 2)
    time.sleep(0.05)
    spin(app, win, 2)
    assert b.cursor > r.loss.rows[0].t
    key(win, Qt.Key_P)
    assert not r.playing
    key(win, Qt.Key_L)                                  # live lanes from the bus
    assert r.view.live and r._cursor_text == "LIVE"


def test_raw_tools_keys(active, app):
    win = active
    win.show_screen(mw.RAW)
    raw, m = win.raw, win.session.mapper
    raw.grid.select(0x14)
    key(win, Qt.Key_Right)
    key(win, Qt.Key_Down)
    assert raw.grid.sel == 0x25 and raw.inspector.addr == 0x25
    key(win, Qt.Key_S)
    assert m.sweeping
    m.fill(1.0)
    m.fill(2.0)
    spin(app, win)
    assert m.changes.sum() > 0 and raw.sweep_btn.text() == "Sweeping"
    key(win, Qt.Key_C)
    assert m.changes.sum() == 0
    key(win, Qt.Key_S)
    assert not m.sweeping
    key(win, Qt.Key_N)
    assert m.wizard().step == 1
    key(win, Qt.Key_B)
    assert m.wizard().step == 0
    key(win, Qt.Key_Return)
    assert "demo" in m.console()


def test_tune_keys_edit_through_the_table_model(active, app):
    win = active
    win.show_screen(mw.TUNE)
    tune, tm = win.tune, win.session.tables
    assert tm.writable and tune.grid.values.shape == (20, 10)
    tune.grid.cur = tune.grid.anchor = (8, 5)
    base = float(tm.base("fl")[8, 5])
    key(win, 0, text="=")
    assert tm.values("fl")[8, 5] == pytest.approx(base + 0.05)
    key(win, 0, text="+", mod=Qt.ShiftModifier)
    assert tm.values("fl")[8, 5] == pytest.approx(base + 0.30)
    key(win, 0, text="-")
    assert tm.values("fl")[8, 5] == pytest.approx(base + 0.25)
    spin(app, win)
    assert tune.grid._changed[8, 5] and "1 cells changed" in tune.chip.text
    assert tune.model.z[5, 8] == pytest.approx(base + 0.25), "the surface follows the edit"
    key(win, Qt.Key_Right, Qt.ShiftModifier)
    key(win, Qt.Key_Down, Qt.ShiftModifier)
    assert len(tune.grid.selection()) == 4 and tune.grid.cur == (9, 6)
    key(win, Qt.Key_S)
    key(win, Qt.Key_Z, Qt.ControlModifier)
    key(win, Qt.Key_Z, Qt.ControlModifier)
    assert tm.values("fl")[8, 5] == pytest.approx(base + 0.30)
    key(win, Qt.Key_T)
    assert tune.table.id == "fh"


def test_read_only_tables_explain_themselves(app, settings):
    from hondalog.ui.session import build_session
    session = build_session()                           # stock setup: no demo, no chip
    win = mw.Shell(session, settings)
    try:
        win.resize(1366, 768)
        win.show()
        win.show_screen(mw.TUNE)
        spin(app, win)
        assert not win.tune.inc_btn.isEnabled() and not win.tune.chip.write_btn.isEnabled()
        assert "P28" in win.tune.chip.text
        # 27 ECU channels (fan and fuel pump are in the map now, no placeholders)
        # + O2 post (OBD2-only) + 8 black box + the map's math channels (W3)
        math = len(session.bus.channels("MATH"))
        assert math >= 5 and len(win.sensors.tiles) == 36 + math
        assert win.session.ecu_status.state == "off"
    finally:
        win.close()


def test_pin_to_drive_and_slots_persist(shell, app, settings):
    shell.sensors.tiles[5].clicked.emit("ign")
    assert shell.drive.slots[0] == "ign" and settings.value("drive/slots").startswith("ign,")
    shell.show_screen(mw.DRIVE)
    spin(app, shell)
    assert shell.drive.tiles[0].state.spec.id == "ign"
    t = shell.drive.tiles[0]
    assert (t.width(), t.height()) == (329, 312) or (t.width(), t.height()) == (328, 312)
    assert shell.drive.mark.geometry().width() == 340
    shell.drive.set_four_up(True)
    assert not shell.drive.tiles[5].isVisible() and shell.drive.tiles[0].width() >= 496


def test_frame_budget_offscreen(shell, app):
    """Coarse regression guard; tools/bench_ui.py is the real measurement."""
    if app.platformName() != "offscreen":
        pytest.skip("timing guard is calibrated for the offscreen platform")
    from hondalog.ui.perf import Perf
    shell.perf, shell.flush_paint = Perf(), True
    shell.session.ecu.clock = shell.session.blackbox.clock = None      # live values
    for index in (mw.SENSORS, mw.DRIVE, mw.REPLAY):
        shell.show_screen(index)
        end = time.monotonic() + 0.7
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.002)
    s = shell.perf.summary()
    assert s["frames"] > 30 and s["frame_ms"]["p95"] < 33.0, s
    assert s["latency_ms"]["p95"] < 100.0, s


@pytest.mark.skipif(not HAVE_QTBOT, reason="pytest-qt not installed")
def test_mark_button_click_with_qtbot(app, shell, qtbot):
    before = shell.session.recording.marks
    qtbot.mouseClick(shell.status.mark_btn, Qt.LeftButton)
    assert shell.session.recording.marks == before + 1
    qtbot.mouseClick(shell.tabs, Qt.LeftButton, pos=shell.tabs._rects[2].center().toPoint())
    assert shell.current() == mw.MAP3D
