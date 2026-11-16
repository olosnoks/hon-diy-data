"""Replay drawn from hondalog.analysis.ReplaySession: envelopes, two cursors,
zoom and pan, event jumps, a second drive laid over the first."""

from datetime import datetime

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from conftest import spin
from hondalog.analysis import ReplaySession
from hondalog.data import Column, new_drive
from hondalog.ui import adapters as ad
from hondalog.ui import main_window as mw
from hondalog.ui import wired
from hondalog.ui.backends import demo_stall_view
from hondalog.ui.replaymodel import ReplayModel


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
    assert (b.t0, b.t1) == pytest.approx(r.model.span) and b.span == pytest.approx((0.0, 50.0), abs=0.1)
    spin(app, shell)
    fpv = lane(b, "fpv")
    columns = b.plot_w()
    assert fpv.has_data and 0 < points(fpv.curve) <= 2 * columns       # 50 000 samples, one envelope
    assert points(lane(b, "rpm").curve) == 600                         # the K-line reads fit as they are
    before = b.traces
    b.set_cursor(21.0)                                                 # moving the cursor draws nothing anew
    assert b.traces == before and fpv.value == "13.60" and lane(b, "tach").value == "27"
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
    def wheel(dy, mods=Qt.NoModifier, x=407.0):
        ev = QWheelEvent(QPointF(x, 100), QPointF(x, 100), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, mods,
                         Qt.NoScrollPhase, False)
        b.overlay.wheelEvent(ev)
    at = b.t_of(407.0)
    wheel(120)
    assert b.t1 - b.t0 == pytest.approx(40.0, abs=0.1) and b.t_of(407.0) == pytest.approx(at, abs=0.05)
    t0 = b.t0
    wheel(-120, Qt.ShiftModifier)
    assert b.t0 > t0 and b.t1 - b.t0 == pytest.approx(40.0, abs=0.1)
    t0 = b.t0
    QTest.mousePress(b.overlay, Qt.RightButton, Qt.NoModifier, QPoint(400, 100))
    QTest.mouseMove(b.overlay, QPoint(300, 100))
    # QTest does not carry button state on a move: drive the handler the way Qt would
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    b.overlay.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(300, 100), QPointF(300, 100), Qt.NoButton,
                                         Qt.RightButton, Qt.NoModifier))
    assert b.t0 == pytest.approx(min(t0 + 100 / b.plot_w() * 40.0, 10.0), abs=0.1)
    QTest.mouseRelease(b.overlay, Qt.RightButton, Qt.NoModifier, QPoint(300, 100))

    # a capture opens on the stall window, with the trigger at zero
    assert r.open("stall_20261110_071240") and (b.t0, b.t1) == pytest.approx((-30.0, 10.0), abs=0.1)
    assert b.trigger == 0.0 and r.loss.rows and r.loss.rows[0].channel_id == "fpv"


def test_two_cursors_give_every_lane_a_delta(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    b.set_cursor(-5.0)
    assert all(not x.delta for x in b.lanes) and r._delta_text == ""
    key(shell, Qt.Key_D)                                    # B drops where A is
    assert b.cursor_b == pytest.approx(-5.0) and r.model.cursor_b == pytest.approx(-5.0)
    b.set_cursor(1.0)
    assert r._delta_text == "Δ +6.00 s" and r._cursor_text == "+1.0 s"
    fpv, rpm, flag = lane(b, "fpv"), lane(b, "rpm"), lane(b, "closed_loop")
    view = r.view
    was, now = view.value_at("fpv", -5.0), view.value_at("fpv", 1.0)
    assert fpv.delta == f"Δ −{was - now:.2f}" and fpv.value == f"{now:.2f}"
    assert rpm.delta.startswith("Δ −") and flag.delta == ""            # ON/OFF has no difference to show
    # Shift+click moves B, a plain click moves A
    QTest.mouseClick(b.overlay, Qt.LeftButton, Qt.ShiftModifier, QPoint(int(b.x_of(-10.0)), 100))
    assert b.cursor_b == pytest.approx(-10.0, abs=0.06) and r._delta_text.startswith("Δ +1")
    QTest.mouseClick(b.overlay, Qt.LeftButton, Qt.NoModifier, QPoint(int(b.x_of(-12.0)), 100))
    assert b.cursor == pytest.approx(-12.0, abs=0.06) and r._delta_text.startswith("Δ −")
    spin(app, shell)
    shot = shell.replay.grab().toImage()
    assert shot.width() > 0
    key(shell, Qt.Key_D)
    assert b.cursor_b is None and r.model.cursor_b is None and not fpv.delta and r._delta_text == ""


def test_jumps_walk_the_sessions_events_and_the_order_of_loss(shell, app):
    shell.show_screen(mw.REPLAY)
    r, b = shell.replay, shell.replay.block
    kinds = [kind for _, kind, _ in r.model.events()]
    assert kinds.count("marker") == 2 and "trigger" in kinds and "loss" in kinds
    key(shell, Qt.Key_Home)
    seen = []
    for _ in range(len(kinds) + 1):
        key(shell, 0, text="]")
        seen.append(round(b.cursor, 2))
    assert seen[:2] == [-22.0, -9.0] and seen[-1] == 10.0             # past the last event: the end
    assert r.loss.rows[0].t in seen and 0.0 in seen and seen == sorted(seen)
    key(shell, 0, text="[")
    assert b.cursor == 0.0
    b.zoom(0.1)
    key(shell, Qt.Key_Home)
    key(shell, 0, text="]")
    assert b.cursor == pytest.approx(-22.0) and b.t0 <= -22.0 <= b.t1  # the window follows the jump
    r.jump_seg.picked.emit("stall")
    assert b.cursor == 0.0 and b.t0 <= 0.0 <= b.t1


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
