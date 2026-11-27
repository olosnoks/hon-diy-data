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
from hondalog.ui.tiles import SENSOR, Tile

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


def test_live_shows_every_panel_on_one_screen_no_scrolling(shell):
    """F1 Live: system panels, no scrolling; the demo's channels in their groups
    (tests/test_ui_live.py has the rest)."""
    from hondalog.ui.tiles import PANEL
    live = shell.sensors
    assert [p.title for p in live.panels] == ["ENGINE", "IDLE CONTROL", "FUEL", "IGNITION",
                                              "ELECTRICAL", "CYD · BLACK BOX"]
    shown = [t for t in live.tiles if t.parent() is live] + list(live.virtual.values())
    assert [t.state.spec.id for t in live.panels[0].tiles] == ["rpm", "map", "tps", "ect", "iat"]
    area = live.rect()
    for t in shown:
        assert area.contains(t.geometry()), t.state.spec.id
        assert t.m is PANEL, t.state.spec.id            # 1366 x 768: the full tile with min / max
    for i, a in enumerate(shown):
        for b in shown[i + 1:]:
            assert not a.geometry().intersects(b.geometry())
    assert not any(t.state.spec.source == "BB" for t in live.tiles)    # no --bb: no USB black box


def test_channels_without_a_group_go_to_the_raw_drawer(shell, app):
    for i in range(8):
        shell.session.bus.register(ChannelSpec(f"raw_{i:02x}", f"Raw 0x{i:02X}", source="ECU"))
    spin(app, shell)
    live = shell.sensors
    raw = [t.state.spec.id for t in live.drawer.tiles]
    assert raw[:8] == [f"raw_{i:02x}" for i in range(8)]
    assert live.raw_button.text == f"Raw bytes ({len(raw)}) ▸" and not live.drawer.isVisible()
    live.toggle_raw()
    spin(app, shell)
    assert live.drawer.isVisible() and live.rect().contains(live.drawer.geometry())
    assert all(live.drawer.rect().contains(t.geometry()) for t in live.drawer.tiles if t.isVisible())


@pytest.mark.parametrize("name", ["dracula", "darker"])
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
    D = theme.DRACULA
    states = [(t._level, t._stale) for t in tiles]
    assert states == [("", False), ("", False), ("warn", False), ("alarm", False),
                      ("", True), ("", False), ("", False), ("alarm", False)]
    alarm = tiles[3].grab().toImage()
    assert alarm.pixelColor(80, 100).name().upper() == D.alarm
    warn = tiles[2].grab().toImage()
    assert warn.pixelColor(100, 6).name().upper() == D.warn       # header band
    assert warn.pixelColor(100, 100).name().upper() == D.panel    # body stays plain
    stale = tiles[4].grab().toImage()
    seen = {stale.pixelColor(x, 120).name().upper() for x in range(4, 150)}
    assert D.sunk in seen and tiles[4]._text == "---"             # hatch + dashes


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


@pytest.mark.parametrize("kind", ["value", "flag", "warn", "alarm", "drive", "panel", "panel-warn"])
def test_a_tile_repainted_band_by_band_equals_one_painted_whole(app, kind):
    """A new value repaints the value band, a new sparkline sample the
    sparkline band. What is on screen after any run of such partial repaints
    must be exactly what one full repaint gives."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap, QRegion
    from hondalog.ui.bus import ChannelBus, ChannelSpec
    from hondalog.ui.tiles import DRIVE, PANEL

    class Alarms:
        def state(self, _cid):
            return {"warn": "warn", "alarm": "alarm", "panel-warn": "warn"}.get(kind, "")

        def unacked(self, _cid):
            return False
    bus = ChannelBus()
    spec = (ChannelSpec("vtec", "VTEC", kind="flag") if kind == "flag"
            else ChannelSpec("ect", "Coolant", "°C", 1, -40, 140, verified=True))
    state = bus.register(spec)
    tile = Tile(state, DRIVE if kind == "drive" else PANEL if kind.startswith("panel") else SENSOR)
    tile.resize(328, 312) if kind == "drive" else tile.resize(163, 142)
    if kind.startswith("panel"):
        tile.resize(137, 96)
        tile.set_note("baro 101 kPa")                  # the Live tile's note line
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
    assert win.current() == mw.SETUP
    assert not theme.T.darker and theme.T.name == "dracula"
    key(win, Qt.Key_F10)                                # F10 is gone: the handheld lives in Setup
    assert win.current() == mw.SETUP
    key(win, Qt.Key_F9)
    assert theme.T.darker and settings.value("theme") == "darker"
    assert win.status.theme_btn.text() == "DARKER"
    assert theme.T.bg.name().upper() == "#191A21"
    key(win, Qt.Key_F9)
    assert not theme.T.darker and settings.value("theme") == "dracula"
    assert win.status.theme_btn.text() == "DRACULA" and theme.T.bg.name().upper() == "#282A36"


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
    from hondalog.ui.backends import demo_stall_view
    r.show_view(demo_stall_view())                  # the demo opens on a simulated drive: take the capture
    assert r.view.trigger_t == 0.0 and b.lane_ids() == ["rpm", "tps", "map", "lat_g", "fpv"]
    assert all(l.has_data for l in b.lanes) and r.forensics.loss[0].channel_id == "fpv"
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
    key(win, Qt.Key_Home)
    for _ in range(3):
        key(win, 0, text="]")                       # the marks, then what fell first
    assert b.cursor == pytest.approx(r.forensics.loss[0].t)
    assert b.lanes[4].cid == "fpv" and float(b.lanes[4].value) < 13.0
    key(win, Qt.Key_P)
    assert r.playing
    time.sleep(0.05)
    spin(app, win, 2)
    time.sleep(0.05)
    spin(app, win, 2)
    assert b.cursor > r.forensics.loss[0].t
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
        # + O2 post (OBD2-only) + the map's math channels (W3); the 8 of the USB
        # black box are hidden without --bb. Live: each one a tile or a switch.
        math = len(session.bus.channels("MATH"))
        live = win.sensors
        shown = [t.state.spec.id for t in live.tiles] + [c.state.spec.id for c in live.flags]
        from hondalog.ui.screens.sensors import CONSUMED       # GPS position: read into notes
        mapped = {s.spec.id for s in session.bus.channels()
                  if s.spec.mapped and s.spec.source != "BB" and s.spec.id not in CONSUMED}
        assert math >= 5 and len(shown) == len(set(shown)) and set(shown) == mapped
        assert len(shown) >= 28 + math
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
    from hondalog.ui.tiles import DRIVE_COMPACT
    assert t.width() in (328, 329) and t.height() >= 252 and t.m is DRIVE_COMPACT
    assert shell.drive.idle_bar.isVisible() and shell.drive.launch_bar.isVisible()
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
    assert shell.current() == mw.EVENTS


# ---- the redesign: screen order, Tune only with a ROM, the CYD black box chip ----

NEW_ORDER = ["Live", "Drive", "Events", "Replay", "Power", "GPS map", "Bytes", "Setup"]


def test_tabs_follow_the_new_order(shell, app):
    from hondalog.ui import chrome
    assert [k for k, _ in chrome.TABS] == [f"F{i}" for i in range(1, 9)]
    assert [n for _, n in chrome.TABS] == NEW_ORDER
    assert (mw.LIVE, mw.DRIVE, mw.EVENTS, mw.REPLAY, mw.POWER, mw.MAP, mw.BYTES, mw.SETUP) == tuple(range(8))
    assert shell.tabs.tabs == chrome.TABS                   # the demo opens no ROM: no Tune tab
    assert len(shell.tabs._rects) == 8
    assert shell.screens[mw.LIVE] is shell.sensors and shell.screens[mw.REPLAY] is shell.replay
    assert shell.screens[mw.MAP] is shell.map and shell.screens[mw.DRIVE] is shell.drive
    for i in range(8):
        shell.tabs.picked.emit(i)
        assert shell.current() == i and shell.tabs.current == i
        assert shell.stack.currentWidget() is shell.screens[i]


def test_tune_is_a_tab_only_with_a_rom(shell, app):
    shell.show_screen(mw.TUNE)                              # still reachable (menu, Setup)
    assert shell.stack.currentWidget() is shell.tune and shell.tabs.current == -1
    shell.session.rom = "x.bin"
    shell.tabs.set_tabs(shell.tab_list())
    spin(app, shell)
    assert shell.tabs.tabs[-1][1] == "Tune" and len(shell.tabs._rects) == 9
    shell.tabs.picked.emit(8)
    assert shell.current() == mw.TUNE and shell.tabs.current == 8


def test_hosted_screens_open_in_their_host(shell, app):
    for sub, host in ((mw.MAP3D, mw.POWER), (mw.ALARMS, mw.SETUP), (mw.HANDHELD, mw.SETUP)):
        shell.show_screen(sub)
        spin(app, shell)
        assert shell.current() == sub and shell.tabs.current == host
        widget = shell.stack.currentWidget()
        name = mw.SUBSCREENS[sub][0]
        inner = getattr(shell, name)
        assert widget is inner or widget is shell.screens[host]
        assert widget.size() == shell.stack.size()


def test_old_theme_names_map_to_dracula_and_darker(app, tmp_path):
    from PySide6.QtCore import QSettings
    from hondalog.ui.session import build_session
    for stored, expect in (("night", "darker"), ("daylight", "dracula"), ("darker", "darker"), ("", "dracula")):
        settings = QSettings(str(tmp_path / f"{stored or 'none'}.ini"), QSettings.IniFormat)
        if stored:
            settings.setValue("theme", stored)
        win = mw.Shell(build_session(demo=True, demo_clock=lambda: 100.0), settings)
        try:
            assert theme.T.name == expect and settings.value("theme") == expect
        finally:
            win.close()
            win.deleteLater()
            app.processEvents()
            theme.T.set("dracula")


def test_black_box_chip_only_with_bb_cyd_chip_always(shell, app, settings):
    texts = [c[3] for c in shell.status._chips]
    assert not any(t.startswith("BLACK BOX") for t in texts)
    assert any(t.startswith("CYD BLACK BOX") for t in texts)
    assert texts[0].startswith("ECU") and any(t.startswith("REC") for t in texts)
    assert any(t.startswith("STALL CATCHER") for t in texts)
    drawn = [k for k, _ in shell.status._rects]
    assert "cyd" in drawn, "the CYD chip fits at 1366 px"
    from hondalog.ui.session import build_session
    win = mw.Shell(build_session(demo=True, demo_clock=lambda: 100.0), settings, show_bb=True)
    try:
        win.resize(1366, 768)
        spin(app, win)
        assert any(c[3].startswith("BLACK BOX") for c in win.status._chips)
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def test_cyd_chip_states():
    from hondalog.ui.chrome import cyd_chip
    from hondalog.ui.session import CydState
    assert cyd_chip(CydState())[3] == "CYD BLACK BOX off"
    assert cyd_chip(CydState("lost"))[2] == "bad"
    key, icon, style, text, monos = cyd_chip(CydState("relay", "rec", 41, True))
    assert (key, icon, style, text) == ("cyd", "ok", "", "CYD BLACK BOX")
    assert monos == ("RELAY", "SD REC #41", "map ok")
    _, icon, style, _, monos = cyd_chip(CydState("usb", "ok", 0, False))
    assert style == "caution" and "MAP DIFFERS" in monos


def light_blocks(image, block=48, step=8):
    """Top-left corners of block x block squares that are all light and grey
    (a white or light-grey surface; text strokes and thin rows never fill one)."""
    import numpy as np
    from PySide6.QtGui import QImage
    img = image.convertToFormat(QImage.Format_RGB32)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    px = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes())
    rgb = px.reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)[::step, ::step, :3].astype(np.int16)
    hi, lo = rgb.max(axis=2), rgb.min(axis=2)
    light = ((hi + lo) / 2 > 200) & (hi - lo < 40)      # light and nearly grey
    n = block // step
    # summed-area table: a window of n x n samples is a light block if all n*n are light
    sat = np.pad(light.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    win = sat[n:, n:] - sat[:-n, n:] - sat[n:, :-n] + sat[:-n, :-n]
    js, is_ = np.nonzero(win == n * n)
    return [(int(i) * step, int(j) * step) for j, i in zip(js, is_)]


@pytest.mark.parametrize("name", ["dracula", "darker"])
def test_nothing_renders_white(shell, app, name):
    """No white or light surface anywhere: every screen, both themes."""
    theme.T.set(name)
    for index in list(range(len(shell.screens))) + list(mw.SUBSCREENS):
        shell.show_screen(index)
        spin(app, shell)
        hits = light_blocks(shell.grab().toImage())
        assert not hits, f"screen {index} in {name}: light surface at {hits[:5]}"


def test_text_editors_count_as_typing(app):
    from PySide6.QtWidgets import QComboBox, QLabel, QPlainTextEdit, QSpinBox, QTextEdit
    assert mw.is_typing(QPlainTextEdit()) and mw.is_typing(QTextEdit()) and mw.is_typing(QSpinBox())
    combo = QComboBox()
    assert not mw.is_typing(combo)
    combo.setEditable(True)
    assert mw.is_typing(combo)
    ro = QPlainTextEdit()
    ro.setReadOnly(True)
    assert not mw.is_typing(ro) and not mw.is_typing(QLabel()) and not mw.is_typing(None)


def test_space_types_into_a_text_editor_f_keys_still_switch(active, app):
    from PySide6.QtWidgets import QPlainTextEdit
    win = active
    rec = win.session.recording
    edit = QPlainTextEdit(win.screens[mw.SETUP])
    edit.show()
    win.show_screen(mw.SETUP)
    edit.setFocus()
    app.processEvents()
    before = rec.marks
    key(win, Qt.Key_Space)
    assert rec.marks == before and edit.toPlainText() == " "
    key(win, Qt.Key_F2)                                     # F-keys still act
    assert win.current() == mw.DRIVE and not edit.hasFocus()
    edit.deleteLater()


def test_replay_wiring_tiles_power_and_title(shell, app):
    seen = []
    if hasattr(shell.replay, "set_tiles"):                 # Replay's GPS pane takes the map screen's layer
        shell.replay.set_tiles = seen.append
        mw.Shell._wire_replay_tiles(shell)
        assert seen == [shell.map.tiles]
    calls = []
    shell.power.show_pull = lambda pull, view=None: (calls.append((pull, view)), True)[1]
    shell.open_pull(object())
    assert shell.current() == mw.POWER and len(calls) == 1
    shell.open_pull(None)                                   # no pull under the cursor: just the screen
    assert shell.current() == mw.POWER and len(calls) == 1
    shell.show_screen(mw.REPLAY)
    spin(app, shell)
    own = getattr(shell.replay, "title_text", None)
    if callable(own) and own():
        assert shell.tabs.info == own()
    shell.show_screen(mw.LIVE)
    spin(app, shell)
    assert "map" in shell.tabs.info                         # back to the link summary


def test_rom_opened_in_setup_gives_tune_its_tab(shell, app):
    shell.rom_opened("C:/roms/p28.bin")
    assert shell.tabs.tabs[-1][1] == "Tune" and shell.current() == mw.TUNE and shell.tabs.current == 8
    shell.rom_opened("")
    assert shell.tabs.tabs[-1][1] != "Tune"
