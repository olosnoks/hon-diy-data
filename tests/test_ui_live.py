"""F1 Live (sensors grouped by system) and F2 Drive's idle bars."""

import math

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from conftest import spin
from hondalog.ui import main_window as mw
from hondalog.ui.bus import ChannelBus, ChannelSpec
from hondalog.ui.screens import sensors as live_mod
from hondalog.ui.screens.sensors import cyd_facts, gear_of, short_note
from hondalog.ui.tiles import (PANEL, PANEL_COMPACT, PANEL_TIGHT, IdleBar, Tile,
                               metrics_height)

# the channels of the current car map that the demo does not simulate
CAR = [
    (ChannelSpec("idle_target", "Idle target", "rpm", 0, 0, 2000), 824.0),
    (ChannelSpec("iac_cmd", "IAC command", "", 0, 0, 255), 89.0),
    (ChannelSpec("iac_learn", "IAC learn", "", 0, 0, 255), 3.0),
    (ChannelSpec("load_ecu", "Load (ECU)", "%", 0, 0, 100), 22.0),
    (ChannelSpec("o2_2", "O2 rear", "V", 2, 0, 1.2), 0.94),
    (ChannelSpec("fuel_status", "Fuel system", "", 0, 0, 16), 2.0),
    (ChannelSpec("vtec_psw", "VTEC press. sw", kind="flag"), 0.0),
    (ChannelSpec("starter", "Starter", kind="flag"), 0.0),
    (ChannelSpec("psp", "PSP switch", kind="flag"), 1.0),
    (ChannelSpec("o2_heater", "O2 heater?", kind="flag"), 1.0),
    (ChannelSpec("raw_2a", "Raw 0x2A", "", 0, 0, 255), 18.0),
    (ChannelSpec("raw_03", "Raw 0x03", "", 0, 0, 255), 111.0),
    (ChannelSpec("total_trim", "Total trim", "%", 1, -50, 50, source="MATH", signed=True), 18.0),
]


def add_car(shell, app, rpm=812.0, kmh=0.0):
    bus = shell.session.bus
    for spec, _ in CAR:
        bus.register(spec)

    def feed():
        bus.publish({s.id: v for s, v in CAR})
        bus.publish({"rpm": rpm, "vss": kmh, "baro": 100.0})
    feed()
    shell.sensors._info_t = -1.0            # the notes now, not in a second
    shell.frame = (lambda frame: (lambda: (feed(), frame())))(shell.frame)
    spin(app, shell, 3)


def tile(live, cid):
    return next(t for t in list(live.tiles) + list(live.virtual.values())
                if t.state.spec.id == cid)


# ---- pure helpers ----

def test_gear_from_rpm_over_speed():
    assert gear_of(812, 0) == "N"
    assert gear_of(2672, 20) == "1"                 # 133.6 rpm per km/h (analysis.motion)
    assert gear_of(1536, 20) == "2"
    assert gear_of(3500, 100) == "4"
    assert gear_of(2780, 100) == "5"
    assert gear_of(1500, 80) == "–"                 # clutch in: no gear matches
    assert gear_of(float("nan"), 50) == "---"


def test_map_notes_are_cut_to_one_line():
    assert short_note("2026-11-26: offset fitted to OBD2 PID 0E (raw 94 = 12.0 deg). Was x") == \
        "offset fitted to OBD2 PID 0E"
    assert short_note("Behaves like field duty: +16 counts per step") == "Behaves like field duty"
    long = short_note("word " * 30)
    assert len(long) <= 44 and long.endswith("…")


def test_group_table_names_each_channel_once():
    seen = [cid for _, _, ids in live_mod.PANELS for cid in ids] + list(live_mod.SWITCHES) \
        + list(live_mod.CONSUMED)
    assert len(seen) == len(set(seen))
    # the web app's group table (web/src/core/addressmap/groups.ts)
    g = live_mod.group_of
    assert g("ign_limit") == "ignition" and g("psp") == "switches" and g("load_ecu") == "engine"
    assert g("raw_2a") == "raw" and g("gps_lat") == "cyd" and g("iac_whatever") == "idle"
    assert g("o2_new") == "fuel" and g("thing", "flag") == "switches" and g("thing") == "raw"
    assert live_mod.LABELS["psp"] == "Steering load" and live_mod.LABELS["rpm_map_idx"] == "RPM×MAP idx"
    assert live_mod.MAX_TILES == 9


def test_idle_bar_readout_and_zones(app):
    bar = IdleBar("idle")
    bar.set_values(812, 824)
    assert bar.readout() == "812 / 824 · −12" and bar.zone() == ""
    bar.set_values(560, 824)
    assert bar.zone() == "warn"                      # in the stall zone
    bar.set_values(None, 824)
    assert bar.readout() == "--- / 824"
    launch = IdleBar("launch", big=True)
    launch.set_values(948, 824)
    assert launch.readout() == "+124 rpm" and launch.zone() == ""
    launch.set_values(760, 824)
    assert launch.readout() == "−64 rpm" and launch.zone() == "warn"
    launch.set_values(600, 824)
    assert launch.zone() == "alarm"
    for b in (bar, launch):                          # paints without a target too
        b.set_values(800, None, has_target=False)
        b.resize(400, IdleBar.preferred_height(b.big))
        assert not b.grab().isNull()


def test_panel_metrics_step_down_by_band():
    # min / max sit small beside the sparkline (as in the web app): no footer row
    assert metrics_height(PANEL) == 82 and PANEL.foot_h == 0 and PANEL_COMPACT is PANEL
    assert metrics_height(PANEL_TIGHT) == 66 and PANEL_TIGHT.spark_h == 0


# ---- the Live screen ----

def test_car_map_fills_the_panels_like_the_artboard(shell, app):
    add_car(shell, app)
    live = shell.sensors
    ids = {p.key: [t.state.spec.id for t in p.tiles] for p in live.panels}
    # the web app's listed tiles first (missing ones hatched), then the map's others of the group
    assert ids["engine"] == ["rpm", "map", "tps", "load", "ect", "iat", "vss", "baro", "load_ecu"]
    assert ids["idle"] == ["idle_target", "iac_cmd", "iacv", "iac_learn", "rpm_map_idx", "vss"]
    assert ids["fuel"] == ["stft", "ltft", "o2", "o2_post", "inj", "purge", "closed_loop", "o2_2",
                           "fuel_status"]
    assert ids["ignition"] == ["ign", "vtec", "vtec_psw", "rpm_idx_3c", "knock", "ign_limit"]
    assert ids["electrical"] == ["batt", "alt_fr", "eld"]
    assert ids["cyd"] == ["gps_speed", "@gear", "@trip", "@cyd_sd", "@cyd_link", "@cyd_stalls"]
    missing = tile(live, "rpm_map_idx")
    assert not missing.state.spec.mapped and missing._note_src == "not in address map"
    assert missing._label == "RPM×MAP idx" and missing._text == "---"
    assert [c.state.spec.id for c in live.flags] == ["starter", "brake", "psp", "ac_sw", "ac_clutch",
                                                    "fuel_pump", "closed_loop", "o2_heater", "mil",
                                                    "vtec", "fan", "vtec_psw"]
    assert [c.label for c in live.flags][2] == "Steering load"
    drawer = [t.state.spec.id for t in live.drawer.tiles]
    assert drawer[:2] == ["raw_03", "raw_2a"] and "total_trim" in drawer
    assert "gps_lat" not in drawer and "gps_sats" not in drawer
    assert live.raw_button.text == f"Raw bytes ({len(drawer)}) ▸"
    # names and the web's notes
    assert tile(live, "tps")._label == "Throttle" and tile(live, "tps")._note_src == "opening, closed = 0"
    assert tile(live, "ect")._note_src == "reads ~14 °C low?" and tile(live, "ect")._note_hot
    assert tile(live, "map")._note_src.startswith("baro ")   # in the display unit
    assert tile(live, "ltft")._note_src == "total +3 %" and not tile(live, "ltft")._note_hot
    assert tile(live, "vss")._note_src == "gear N"
    assert tile(live, "rpm")._note_src.startswith("min ") and " · max " in tile(live, "rpm")._note_src
    # the idle bar
    bar = next(p.bar for p in live.panels if p.key == "idle")
    assert bar.readout() == "812 / 824 · −12"
    # worked-out tiles
    assert tile(live, "@gear")._text == "N"
    assert tile(live, "@cyd_link")._text == "DEMO" and tile(live, "@cyd_sd")._text == "REC"
    # nothing clipped, nothing overlapping, at 1366 x 768
    shown = [t for t in live.tiles if t.parent() is live] + list(live.virtual.values())
    for t in shown:
        assert live.rect().contains(t.geometry()), t.state.spec.id
    rects = [t.geometry() for t in shown] + [c.geometry() for c in live.flags if c.isVisible()]
    for i, a in enumerate(rects):
        assert not any(a.intersects(b) for b in rects[i + 1:])
    assert all(c.isVisible() for c in live.flags)


def test_raw_drawer_opens_and_esc_closes_it(shell, app):
    add_car(shell, app)
    live = shell.sensors
    live.raw_button.clicked.emit()
    spin(app, shell)
    assert live.raw_open and live.drawer.isVisible() and live.raw_button.text.endswith("▾")
    raw = tile(live, "raw_2a")
    assert raw.isVisible() and raw._text == "18"
    esc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    assert live.key(esc) and not live.drawer.isVisible()
    assert not live.key(esc)                        # closed: Esc is the shell's again


def test_drawer_tile_click_pins_to_drive(shell, app):
    add_car(shell, app)
    shell.sensors.toggle_raw()
    tile(shell.sensors, "raw_03").clicked.emit("raw_03")
    assert "raw_03" in shell.drive.slots


def test_switch_chip_shows_on_and_stale(shell, app):
    add_car(shell, app)
    chips = {c.state.spec.id: c for c in shell.sensors.flags}
    assert chips["psp"].is_on() and not chips["starter"].is_on()
    image = chips["psp"].grab().toImage()
    from hondalog.ui import theme
    assert image.pixelColor(image.width() - 6, image.height() // 2) == theme.T.accent


def test_unverified_and_verified_marks(shell, app):
    from hondalog.ui import theme
    live = shell.sensors
    rpm, mapt = tile(live, "rpm"), tile(live, "map")
    assert rpm.state.spec.verified and not mapt.state.spec.verified
    img = rpm.grab().toImage()
    x = 2 + PANEL.pad_l + 4
    y = 2 + PANEL.header_h // 2
    assert img.pixelColor(x, y) == theme.T.ok                       # filled square
    img = mapt.grab().toImage()
    assert img.pixelColor(x, y) != theme.T.unverified               # an outline: hollow middle


def test_usb_black_box_panel_only_with_bb(app, settings):
    from hondalog.ui.session import build_session
    win = mw.Shell(build_session(demo=True, demo_clock=lambda: 100.0), settings, show_bb=True)
    try:
        win.resize(1366, 768)
        win.show()
        spin(app, win, 3)
        live = win.sensors
        if not getattr(live, "show_bb", None) and not getattr(win, "show_bb", False):
            pytest.skip("this shell does not pass show_bb to the screens")
        panel = next(p for p in live.panels if p.key == "bb")
        assert [t.state.spec.id for t in panel.tiles][:3] == ["bbatt", "ecub", "fpv"]
        assert len(live.panels) == 7
        for t in live.tiles:
            if t.parent() is live:
                assert live.rect().contains(t.geometry())
        win.drive.pin("fpv", 4)
        win.show_screen(mw.DRIVE)
        spin(app, win)
        assert win.drive.tiles[4].state.spec.id == "fpv"        # with --bb a BB slot shows
    finally:
        win.close()


def test_small_window_steps_the_tiles_down(shell, app):
    live = shell.sensors
    live.resize(1100, 520)
    live._layout()
    ms = {t.m for t in live.tiles if t.parent() is live}
    assert ms and ms <= {PANEL_COMPACT, PANEL_TIGHT}
    for t in live.tiles:
        if t.parent() is live:
            assert live.rect().contains(t.geometry()), t.state.spec.id


def test_cyd_facts_without_a_handheld_is_empty():
    class Session:
        demo = False
        cyd = None

        class ecu:
            poller = None
    assert cyd_facts(Session()) == {}


def test_cyd_facts_reads_the_handheld_stats():
    from hondalog.handheld import serial_proto as P
    from hondalog.ui.session import CydState

    class Hello:
        map_hash = "8f3b48"

    class Poller:
        source = "cyd"
        hello = Hello()
        device = {"flags": P.ST_RECORDING | P.ST_SD_OK, "drive_no": 41, "free_mb": 1229,
                  "stall_count": 2}

    class Session:
        demo = False
        cyd = CydState("kline", "rec", 41, True)

        class ecu:
            poller = Poller()
    facts = cyd_facts(Session())
    assert facts["sd"] == ("REC", "drive 41 · 1.2 GB free")
    assert facts["link"] == ("K-LINE", "map #8f3b48 ✓", False)
    assert facts["stalls"] == (2, "")
    Session.cyd = CydState("relay", "ok", 0, False)
    facts = cyd_facts(Session())
    assert facts["link"][0] == "RELAY" and facts["link"][2] and facts["sd"][0] == "SD OK"


# ---- the slim tile ----

def test_slim_tile_draws_label_as_written_and_the_note(app):
    bus = ChannelBus()
    state = bus.register(ChannelSpec("ltft", "Long trim", "%", 1, -25, 25, signed=True))
    t = Tile(state, PANEL)
    t.resize(137, metrics_height(PANEL))
    bus.publish({"ltft": 8.6}, t=0.0)
    t.sync(0.0)
    t.set_note("total +18 %", hot=True)
    assert t._label == "Long trim" and t._text == "+8.6"
    img = t.grab().toImage()
    assert not img.isNull()
    t.text_override = "N"
    bus.publish({"ltft": 8.7}, t=0.1)
    t.sync(0.1)
    assert t._text == "N"


# ---- F2 Drive ----

def test_drive_shows_rpm_against_the_idle_target(shell, app):
    add_car(shell, app, rpm=948.0)
    shell.show_screen(mw.DRIVE)
    spin(app, shell, 2)
    drive = shell.drive
    assert drive.idle_bar.readout() == "948 / 824 · +124"
    assert drive.launch_bar.readout() == "+124 rpm" and drive.launch_bar.zone() == ""
    for bar in (drive.idle_bar, drive.launch_bar):
        assert drive.rect().contains(bar.geometry())
        for t in drive.tiles:
            if t.isVisible():
                assert not t.geometry().intersects(bar.geometry())
    assert not drive.mark.geometry().intersects(drive.idle_bar.geometry())


def test_drive_hides_the_usb_black_box_without_bb(shell, app):
    assert "fpv" not in shell.drive.slots and "map" in shell.drive.slots   # the default is ECU only
    shell.drive.pin("fpv", 4)                               # an older saved layout with a BB slot
    shell.show_screen(mw.DRIVE)
    spin(app, shell)
    ids = [t.state.spec.id for t in shell.drive.tiles if t.state is not None]
    assert "fpv" in shell.drive.slots                       # the saved slot is kept
    assert not any(shell.session.bus.channel(c).spec.source == "BB" for c in ids)


def test_drive_tall_window_gets_the_full_tiles(shell, app):
    from hondalog.ui.tiles import DRIVE
    drive = shell.drive
    drive.resize(1366, 900)
    drive._layout()
    assert drive.tiles[0].m is DRIVE and not drive.idle_bar.isHidden()
    drive.resize(1366, 560)
    drive._layout()
    assert drive.idle_bar.isHidden()                        # too short: the tiles keep the room
    assert not math.isnan(drive.tiles[0].height())
