"""F4 Replay drawn from hondalog.analysis.ReplaySession: envelopes, two cursors,
zoom and pan, event jumps, a second drive laid over the first; the playback
transport, the GPS pane, the g-g plot, the pull, the calculated readouts and
every sensor at the cursor."""

from datetime import datetime

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from conftest import spin
from hondalog.analysis import ReplaySession
from hondalog.data import Column, new_drive
from hondalog.ui import main_window as mw
from hondalog.ui import wired
from hondalog.ui.backends import demo_stall_view
from hondalog.ui.replaymodel import (DEMO_ORIGIN, ReplayModel, analyse, analysis_channels, demo_drive_view,
                                     view_epoch)


def make_drives(tmp_path):
    """Two drives with a stall capture each: 12 Hz ECU reads, a 1 kHz black
    box stream. In the second the pump dies 1.5 s later than in the first."""
    database = tmp_path / "hondalog.db"
    refs = []
    for day, pump_dies in ((10, 38.0), (12, 39.5)):
        started = datetime(2026, 11, day, 7, 12).timestamp()
        with new_drive(database, {"started_unix": started, "epoch_unix": started}) as w:
            w.add_stream("ecu/00+2", [Column("rpm", units="rpm", lo=0, hi=8000, decimals=0)], source="ecu",
                         raw_len=2)
            for i in range(600):
                t = i / 12.0
                w.append("ecu/00+2", t, [800.0 + 10 * np.sin(t) if t < 40 else 0.0], b"\x00\x00")
            t = np.arange(50_000) / 1000.0
            rows = {"bb_pump": np.where(t < pump_dies, 13.6, 0.2), "bb_rpm_tach": np.where(t < 40, 810.0, 0.0),
                    "bb_vbat": 14.0 + 0.05 * np.sin(t * 40)}
            rows["bb_pump"][20_000] = 3.0                   # one bad sample out of fifty thousand
            w.add_stream("blackbox/live", [Column(n, units="V" if "rpm" not in n else "rpm") for n in rows],
                         source="blackbox")
            w.append_columns("blackbox/live", t, rows)
            w.event(12.0, "marker", "MARK 1")
            w.event(40.0, "trigger", "STALL", {"rule": "stall"})
            w.capture("stall", 40.0, 10.0, 50.0, reason="rpm", message="rpm dropped to 0",
                      wall=datetime(2026, 11, day, 7, 12, 40), meta={"stall_time_s": "40.000"})
        refs.append(w.ref)
    return wired.DriveReader(directory=tmp_path), refs


# ---- the model (no window) ----

def test_model_speaks_view_time_and_ui_ids(tmp_path):
    logs, refs = make_drives(tmp_path)
    view = logs.read("stall_20261110_071240")
    assert isinstance(view.replay, ReplaySession) and view.origin == 40.0
    assert view.names["tach"] == ("bb_rpm_tach", pytest.approx(1 / 30)) and view.names["fpv"] == ("bb_pump", 1.0)
    model = ReplayModel(view)
    assert model.session is view.replay and model.span == pytest.approx((-30.0, 10.0), abs=0.1)
    assert model.window == pytest.approx(model.span)

    # an envelope, not the samples: 40 000 black box rows in 800 columns, the dropout kept
    x, y, reduced = model.trace("fpv", 800)
    assert reduced and len(x) <= 1600 and x[0] >= -30.0 and x[-1] <= 10.0
    assert y.min() == pytest.approx(0.2) and 3.0 in np.round(y, 3)
    x, y, reduced = model.trace("tach", 800)
    assert y.max() == pytest.approx(27.0)                   # the screen's unit (Hz), the log has rpm
    x, y, reduced = model.trace("rpm", 800)
    assert not reduced and len(x) == pytest.approx(480, abs=3)      # 12 Hz fits: the raw reads

    # window: clamped to the drive, never narrower than a quarter second
    assert model.set_window(-100.0, 500.0) == pytest.approx(model.span)
    assert model.zoom(0.5, around=0.0) == pytest.approx((-15.0, 5.0), abs=0.06)
    assert model.pan(0.25) == pytest.approx((-10.0, 10.0), abs=0.06)
    assert model.pan(1.0) == pytest.approx((-10.0, 10.0), abs=0.06)         # stops at the end
    t0, t1 = model.zoom(1e-6, around=-2.0)
    assert t1 - t0 == pytest.approx(0.25) and t0 <= -2.0 <= t1
    x, y, reduced = model.trace("fpv", 800)
    assert not reduced and len(x) < 300                     # zoomed in: the 1 kHz samples themselves
    assert model.show_all() == pytest.approx(model.span)
    assert model.reveal(-2.0) is False and model.zoom(0.1, around=-20.0) and model.reveal(5.0) is True
    assert model.window[0] <= 5.0 <= model.window[1]

    # two cursors and what lies between them
    assert model.set_cursor(-5.0) == pytest.approx(-5.0) and model.delta is None
    assert model.readouts(["fpv", "tach", "nope"]).keys() == {"fpv", "tach"}
    assert model.readouts(["fpv"])["fpv"].value == pytest.approx(13.6) and model.readouts(["fpv"])["fpv"].stats is None
    model.set_cursor(0.0, "b")
    row = model.readouts(["fpv", "tach"])
    assert model.delta == pytest.approx(5.0) and row["fpv"].stats.delta == pytest.approx(0.2 - 13.6)
    assert row["tach"].stats.a == pytest.approx(27.0) and row["tach"].stats.delta == pytest.approx(-27.0)
    assert row["fpv"].stats.min == pytest.approx(0.2) and row["fpv"].stats.n == 5001
    model.clear_cursor_b()
    assert model.cursor_b is None and model.set_cursor(99.0) == pytest.approx(model.span[1])
    assert -6.0 < model.step(-1, "rpm") < model.span[1]

    # events of the log, plus the screen's own; [ and ] walk all of them
    assert [(round(t, 1), kind) for t, kind, _ in model.events()] == [(-28.0, "marker"), (0.0, "trigger")]
    model.add_event(-2.0, "loss", "FP FEED")
    model.set_cursor(-30.0)
    assert [model.jump(+1) for _ in range(4)] == [pytest.approx(-28.0), pytest.approx(-2.0), pytest.approx(0.0), None]
    assert model.jump(-1) == pytest.approx(-2.0) and model.window[0] <= -2.0 <= model.window[1]


def test_model_lays_a_second_drive_over_the_first(tmp_path):
    logs, refs = make_drives(tmp_path)
    model = ReplayModel(logs.read("stall_20261110_071240"))
    assert model.overlay_trace("fpv", 800) is None and model.nudge_overlay(1.0) == 0.0
    other = logs.read("stall_20261112_071240")
    assert model.set_overlay(other, "trigger") == "trigger" and model.overlay_name == "stall_20261112_071240"
    assert model.overlay_offset == pytest.approx(0.0)       # both stalled 40 s in
    x, y, _ = model.overlay_trace("fpv", 800)
    died = x[np.flatnonzero(y < 1.0)[0]]
    assert died == pytest.approx(-0.5, abs=0.06)            # the second drive's pump held 1.5 s longer
    mine = model.trace("fpv", 800)
    assert mine[0][np.flatnonzero(mine[1] < 1.0)[0]] == pytest.approx(-2.0, abs=0.06)    # ours died at -2 s
    assert model.overlay_trace("tach", 800)[1].max() == pytest.approx(27.0)
    assert model.nudge_overlay(-1.5) == pytest.approx(-1.5)
    x, y, _ = model.overlay_trace("fpv", 800)
    assert x[np.flatnonzero(y < 1.0)[0]] == pytest.approx(-2.0, abs=0.06)   # slid by hand until they agree
    assert model.align_overlay("marker") == "marker" and model.overlay_offset == pytest.approx(0.0)
    assert model.align_overlay("crossing") == "start"       # the capture begins above 400 rpm: no crossing
    whole = logs.read(refs[1])                              # a whole drive over a capture: its start at ours
    assert model.set_overlay(whole, "start") == "start" and model.overlay_offset == pytest.approx(10.0)
    assert model.set_overlay(None) == "" and model.overlay_trace("fpv", 800) is None and not model.overlay_name


def test_a_view_without_a_log_gets_a_session_of_its_own():
    view = demo_stall_view()
    assert view.replay is None
    model = ReplayModel(view)
    assert isinstance(model.session, ReplaySession) and model.origin == 0.0
    assert model.span == pytest.approx((-30.0, 10.0))
    assert [(t, kind) for t, kind, _ in model.events()] == [(-22.0, "marker"), (-9.0, "marker"), (0.0, "trigger")]
    x, y, reduced = model.trace("rpm", 814)
    assert not reduced and len(x) == 401 and model.readouts(["fpv"])["fpv"].value < 14


def test_calculated_channels_and_events_join_the_model():
    view = demo_drive_view()
    lat = view.data["gps_lat"]
    assert abs(float(np.mean(lat)) - DEMO_ORIGIN[0]) < 0.05        # laid out around open ocean, not a road
    assert view_epoch(view) == pytest.approx(datetime(2026, 11, 26, 23, 0).timestamp())
    channels = analysis_channels(view)
    assert set(channels) >= {"rpm", "vss", "tps", "gps_heading"}
    d, found = analyse(view)
    kinds = [e.kind for e in found]
    assert kinds == ["near_stall", "knock", "vtec", "dropout", "harsh_brake", "mark", "key_off"]
    near = found[0]
    assert near.value == pytest.approx(460, abs=15) and near.verdict.startswith("throttle 0.")
    assert d.at(38.0)["gear"] == 2 and d.at(38.0)["power"] > 30

    model = ReplayModel(view)
    model.set_derived("lat_g", *d.series("lat_g"))
    assert model.has("lat_g") and not model.has("nope")
    x, y, reduced = model.trace("lat_g", 400)
    assert len(x) and np.nanmax(np.abs(y)) > 0.1
    assert model.overlay_trace("lat_g", 400) is None
    model.set_cursor(50.0)
    model.set_cursor(10.0, "b")
    row = model.readouts(["lat_g", "rpm"])
    assert row["lat_g"].value == pytest.approx(model.derived_at("lat_g", 50.0))
    assert row["lat_g"].stats.a == pytest.approx(row["lat_g"].value) and "rpm" in row
    model.clear_cursor_b()
    model.set_events(found)
    model.set_cursor(0.0)
    seen = []
    while (t := model.jump(+1)) is not None:
        seen.append(round(t, 1))
    assert seen == [round(e.t, 1) for e in found]           # only the drive's events, in order


# ---- the screen ----

def key(win, k, mods=Qt.NoModifier, text=""):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    win.handle_key(QKeyEvent(QEvent.KeyPress, k, mods, text))


def lane(block, cid):
    return next(x for x in block.lanes if x.cid == cid)


def points(curve) -> int:
    x, _ = curve.getData()
    return 0 if x is None else len(x)


def test_lanes_are_drawn_from_the_replay_session(shell, app, tmp_path):
    logs, refs = make_drives(tmp_path)
    shell.session.logs = logs
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    assert r.open(refs[0]) and r.model is not None and r.model.session is r.view.replay
    # what the view lacks of rpm / speed / throttle / MAP / lateral g comes from the black box
    assert b.lane_ids() == ["rpm", "fpv", "bbatt", "tach"]
    assert (b.t0, b.t1) == pytest.approx(r.model.span) and b.span == pytest.approx((0.0, 50.0), abs=0.1)
    spin(app, shell)
    fpv = lane(b, "fpv")
    columns = b.plot_w()
    assert fpv.has_data and 0 < points(fpv.curve) <= 2 * columns       # 50 000 samples, one envelope
    assert points(lane(b, "rpm").curve) == 600                         # the K-line reads fit as they are
    before = b.traces
    b.set_cursor(21.0)                                                 # moving the cursor draws nothing anew
    assert b.traces == before and fpv.value == "13.60" and lane(b, "tach").value == "27"
    assert r._cursor_text == "07:12:21.0"                              # the wall clock of the drive
    key(shell, 0, text="+")                                            # zooming does: a new envelope
    assert b.traces == before + 1 and b.t1 - b.t0 == pytest.approx(25.0, abs=0.1)
    assert (b.t0, b.t1) == pytest.approx(r.model.window) and b.t0 <= b.cursor <= b.t1
    for _ in range(8):
        key(shell, 0, text="+")
    assert b.t1 - b.t0 == pytest.approx(0.25) and points(fpv.curve) < 300   # down to the samples
    key(shell, Qt.Key_Right, Qt.ControlModifier)                       # Ctrl+arrows pan a quarter window
    assert b.t0 == pytest.approx(21.0 - 0.25 * 21 / 50 + 0.0625, abs=0.01)    # the cursor kept its place
    key(shell, 0, text="0")                                            # 0: the whole drive again
    assert (b.t0, b.t1) == pytest.approx(b.span)

    # the wheel zooms around the pointer, Shift + wheel pans, a right drag pans
    x0 = b.plot_w() * 0.8

    def wheel(dy, mods=Qt.NoModifier, x=x0):
        ev = QWheelEvent(QPointF(x, 100), QPointF(x, 100), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, mods,
                         Qt.NoScrollPhase, False)
        b.overlay.wheelEvent(ev)
    at = b.t_of(x0)
    wheel(120)
    assert b.t1 - b.t0 == pytest.approx(40.0, abs=0.1) and b.t_of(x0) == pytest.approx(at, abs=0.05)
    t0 = b.t0
    wheel(-120, Qt.ShiftModifier)
    assert b.t0 > t0 and b.t1 - b.t0 == pytest.approx(40.0, abs=0.1)
    t0 = b.t0
    QTest.mousePress(b.overlay, Qt.RightButton, Qt.NoModifier, QPoint(300, 100))
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    b.overlay.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(200, 100), QPointF(200, 100), Qt.NoButton,
                                         Qt.RightButton, Qt.NoModifier))
    assert b.t0 == pytest.approx(min(t0 + 100 / b.plot_w() * 40.0, 10.0), abs=0.1)
    QTest.mouseRelease(b.overlay, Qt.RightButton, Qt.NoModifier, QPoint(200, 100))

    # a capture opens on the stall window, with the trigger at zero and the cursor on it
    assert r.open("stall_20261110_071240") and (b.t0, b.t1) == pytest.approx((-30.0, 10.0), abs=0.1)
    assert b.trigger == 0.0 and b.cursor == 0.0 and r.forensics.loss[0].channel_id == "fpv"
    assert r._cursor_text == "+0.0 s" or r._cursor_text == "0.0 s"


def test_two_cursors_give_every_lane_a_delta(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    assert r.view.title.startswith("demo_drive")
    b.set_cursor(30.0)
    assert all(not x.delta for x in b.lanes) and r._delta_text == ""
    key(shell, Qt.Key_D)                                    # B drops where A is
    assert b.cursor_b == pytest.approx(30.0) and r.model.cursor_b == pytest.approx(30.0)
    b.set_cursor(38.0)
    assert r._delta_text == "Δ +8.00 s" and r._cursor_text == "23:00:38.0"
    rpm = lane(b, "rpm")
    was, now = r.view.value_at("rpm", 30.0), r.view.value_at("rpm", 38.0)
    assert rpm.value == f"{now:.0f}" and rpm.delta.startswith("Δ +") and rpm.delta == f"Δ +{now - was:.0f}"
    assert lane(b, "lat_g").delta.startswith("Δ")            # calculated lanes have one too
    # Shift+click moves B, a plain click moves A
    QTest.mouseClick(b.overlay, Qt.LeftButton, Qt.ShiftModifier, QPoint(int(b.x_of(20.0)), 100))
    assert b.cursor_b == pytest.approx(20.0, abs=0.2) and r._delta_text.startswith("Δ +1")
    QTest.mouseClick(b.overlay, Qt.LeftButton, Qt.NoModifier, QPoint(int(b.x_of(12.0)), 100))
    assert b.cursor == pytest.approx(12.0, abs=0.2) and r._delta_text.startswith("Δ −")
    spin(app, shell)
    shot = shell.replay.grab().toImage()
    assert shot.width() > 0
    key(shell, Qt.Key_D)
    assert b.cursor_b is None and r.model.cursor_b is None and not rpm.delta and r._delta_text == ""


def test_jumps_walk_the_drives_events(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    times = [round(e.t, 2) for e in r.drive_events]
    assert [e.kind for e in r.drive_events][0] == "near_stall" and len(times) == 7
    key(shell, Qt.Key_Home)
    seen = []
    for _ in range(len(times) + 1):
        key(shell, 0, text="]")
        seen.append(round(b.cursor, 2))
    assert seen[:-1] == times and seen[-1] == pytest.approx(b.span[1])  # past the last event: the end
    key(shell, 0, text="[")
    assert b.cursor == pytest.approx(times[-1], abs=0.01)
    b.zoom(0.1)
    key(shell, Qt.Key_Home)
    key(shell, 0, text="]")
    assert b.cursor == pytest.approx(times[0], abs=0.01) and b.t0 <= b.cursor <= b.t1   # the window follows
    # the scrubber has a tick per event and follows the cursor
    assert [k for _, k in r.scrub.ticks] == [e.kind for e in r.drive_events]
    lo, hi = b.span
    assert r.scrub.frac == pytest.approx((b.cursor - lo) / (hi - lo), abs=1e-3)
    r._scrubbed(0.5)
    assert b.cursor == pytest.approx(lo + 0.5 * (hi - lo), abs=0.1)


def test_playback_transport_and_speeds(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    b.set_cursor(10.0)
    key(shell, Qt.Key_P)
    assert r.playing and r.play_btn.icon_path is not None
    r.refresh(100.0)
    r.refresh(101.0)
    assert b.cursor == pytest.approx(11.0)
    r.speed_seg.picked.emit("4")
    assert r.speed == 4.0 and r.speed_seg.current == "4"
    r.refresh(101.5)
    assert b.cursor == pytest.approx(13.0)
    key(shell, 0, text="1")                                 # 1 / 2 / 4: half, normal, four times
    assert r.speed == 0.5
    key(shell, Qt.Key_P)
    assert not r.playing
    r.refresh(103.0)
    assert b.cursor == pytest.approx(13.0)
    key(shell, Qt.Key_Right)
    assert b.cursor == pytest.approx(13.1)                  # step 0.1 s
    b.set_cursor(b.span[1])
    key(shell, Qt.Key_P)                                    # from the end it starts over
    assert r.playing and b.cursor == pytest.approx(b.span[0])
    key(shell, Qt.Key_P)


def test_map_gg_pull_readouts_and_sensors(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    spin(app, shell)
    assert r.track is not None and len(r.track) == 195
    assert sorted(p.kind for p in r.canvas.pins) == ["alarm", "capture", "capture", "marker", "start"]
    assert r.canvas.title.startswith("GPS · coloured by speed")
    # the car follows the cursor; a click on a pin moves the cursor there
    b.set_cursor(60.0)
    pos = r.track.at(60.0)
    assert r.canvas.cursor == (pytest.approx(pos.x), pytest.approx(pos.y))
    near = next(p for p in r.canvas.pins if p.kind == "alarm")
    r._pin_clicked(near.payload)
    assert b.cursor == pytest.approx(near.payload.t)
    r.set_colour("rpm")
    assert r.colour_by == "rpm" and r.colour_btn.text() == "Colour: rpm"
    # in the 2nd-gear pull: gear, power, torque, the pull card
    b.set_cursor(38.0)
    values = dict(zip(r.readouts.LABELS, r.readouts.values))
    assert values["Gear"] == "2nd" and values["Power now"].endswith("hp") and values["Torque now"].endswith("N·m")
    assert values["Accel"].startswith("+") and values["Fuel flow"].endswith("/h")     # L/h or gal/h
    assert r.pull_card.line1.startswith("2nd gear") and "N·m" in r.pull_card.big
    # cornering: the g-g plot has its trail and a point
    b.set_cursor(50.0)
    assert len(r.gg.trail_x) > 10 and r.gg.now is not None and abs(r.gg.now[0]) > 0.05
    values = dict(zip(r.readouts.LABELS, r.readouts.values))
    assert values["Lateral"] != "—" and values["Grade (est.)"] != "—"
    # standing: neutral, no pull
    b.set_cursor(5.0)
    values = dict(zip(r.readouts.LABELS, r.readouts.values))
    assert values["Gear"] in ("neutral / clutch", "—") and r.pull_card.line1 == "No pull at the cursor"
    assert values["RPM − target"] != "—"
    # every sensor, grouped; never the position itself
    titles = [t for t, _ in r.sensors.groups]
    assert titles[:3] == ["ENGINE", "IDLE CONTROL", "FUEL"] and "CALCULATED" in titles
    cids = {cid for _, rows in r.sensors.groups for cid, _, _ in rows}
    assert not cids & {"gps_lat", "gps_lon"} and {"rpm", "batt", "gps_speed", "lat_g"} <= cids
    # a click adds the lane, another takes it away; the lane menu resets
    r.toggle_lane("batt")
    assert "batt" in b.lane_ids() and lane(b, "batt").has_data and lane(b, "batt").value
    r.toggle_lane("batt")
    assert "batt" not in b.lane_ids()
    r.toggle_lane("power")
    assert lane(b, "power").has_data
    r.reset_lanes()
    assert b.lane_ids() == ["rpm", "vss", "tps", "map", "lat_g"]
    assert lane(b, "rpm").extra is not None and points(lane(b, "rpm").extra) > 0    # the idle target
    assert "demo_drive" in r.title_text()


def test_compare_lays_a_second_drive_over_the_first(shell, app, tmp_path):
    logs, refs = make_drives(tmp_path)
    shell.session.logs = logs
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    assert r.open("stall_20261110_071240") and r.compare_btn.isEnabled()
    fpv = lane(b, "fpv")
    assert points(fpv.over) == 0
    assert r.set_overlay("stall_20261112_071240")           # two stalls: lined up on the stall
    assert r.model.align == "trigger" and 0 < points(fpv.over) <= 2 * b.plot_w()
    assert r._note == "stall_20261112_071240 0.00 s on stall" and r._swatch
    key(shell, 0, text=",")
    key(shell, 0, text=",")
    assert r.model.overlay_offset == pytest.approx(-0.2) and "−0.20 s" in r._note
    key(shell, 0, text=">")
    assert r.model.overlay_offset == pytest.approx(0.8)
    key(shell, Qt.Key_A)                                    # A: the next thing to line up on
    assert r.model.align == "crossing" or r.model.align == "start"
    r.align_overlay("marker")
    assert r.model.align == "marker" and "on marker" in r._note
    r.align_overlay("crossing")                             # no such moment: says so, falls back
    assert r.model.align == "start" and "lined up on the first sample" in r._note and not r._swatch
    spin(app, shell)
    key(shell, Qt.Key_C, Qt.ShiftModifier)                  # Shift+C takes it away
    assert not r.model.overlay_name and points(fpv.over) == 0 and r._note == ""
    assert not r.set_overlay("drive_nope") and "cannot open" in r._note

    r.go_live()                                             # live lanes: nothing to compare
    assert r.model is None and not r.compare_btn.isEnabled()
    r.compare()
    assert "Open a drive first" in r._note
    key(shell, 0, text="+")                                 # zoom, pan, B: no-ops while live, no crash
    key(shell, Qt.Key_D)
    assert b.cursor_b is None
