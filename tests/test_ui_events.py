"""F3 Events: one list of everything worth a look, with filters, the verdict,
a timeline, Open in Replay / Show on GPS map, and notes and verdicts kept in
the drive's database."""

from datetime import datetime

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from conftest import spin
from hondalog.analysis import eventnotes
from hondalog.data import Column, new_drive
from hondalog.ui import main_window as mw
from hondalog.ui import wired
from hondalog.ui.screens.events import EventsScreen


def key(win, k, text=""):
    win.handle_key(QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier, text))


def near_stall_drive(tmp_path, day=26):
    """A drive with a launch that nearly stalls (throttle closed), a dropout
    while moving, a mark, and the key-off: 12 Hz fast reads, 1 Hz slow."""
    database = tmp_path / "hondalog.db"
    started = datetime(2026, 11, day, 22, 51).timestamp()
    hz = 12.0
    t = np.arange(0, 120, 1 / hz)
    rpm = np.full(len(t), 860.0)
    dip = (t >= 20) & (t < 22)
    rpm[dip] = 860 - 400 * np.sin(np.pi * (t[dip] - 20) / 2)
    vss = np.where(t >= 24, np.minimum(50.0, (t - 24) * 4), 0.0)
    rpm[t >= 26] = np.maximum(rpm[t >= 26], vss[t >= 26] * 49.7)
    tps = np.where((t >= 23) & (t < 90), 25.0, 7.8)
    slow = (t >= 85) & (t < 95)
    vss[slow] = 50.0 - (t[slow] - 85) * 5.0              # a gentle stop
    vss[t >= 95] = 0.0                                   # stopped, idling, then the key
    rpm[t >= 95] = 860.0
    keep = ~((t > 60) & (t < 66)) & (t < 110)
    with new_drive(database, {"started_unix": started, "epoch_unix": started}) as w:
        w.add_stream("ecu/00+3", [Column("rpm", units="rpm"), Column("vss", units="km/h"), Column("tps", units="%")],
                     source="ecu", raw_len=3)
        for i in np.flatnonzero(keep):
            w.append("ecu/00+3", float(t[i]), [float(rpm[i]), float(vss[i]), float(tps[i])], b"\x00\x00\x00")
        w.add_stream("ecu/76+2", [Column("idle_target", units="rpm")], source="ecu", raw_len=2)
        for s in range(0, 120):                             # the recorder runs on after the ECU went quiet
            w.append("ecu/76+2", float(s), [860.0], b"\x00\x00")
        w.event(40.0, "marker", "MARK 1")
    return wired.DriveReader(directory=tmp_path), w.ref


def test_events_screen_on_the_demo_drive(shell, app):
    shell.show_screen(mw.EVENTS)
    spin(app, shell)
    e = shell.events
    assert isinstance(e, EventsScreen) and e._loaded
    counts = e.counts()
    assert counts["near_stall"] == 1 and counts["dropout"] == 1 and counts["key_off"] == 1 and counts["vtec"] == 1
    assert counts["knock"] == 1 and counts["harsh_brake"] == 1 and counts["mark"] == 1
    # "All" leaves the knock retards to their own chip; newest first
    assert len(e.shown) == 7 and counts["corner"] == 1 and all(r.event.kind != "knock" for r in e.shown)
    assert e.shown[0].event.kind == "key_off" and e.list.chips()[0] == ("all", "All 7")
    assert e.title_text() == "2026-11-26 · 1 drive · 3 min"
    # filters
    e.set_filter("near_stall")
    assert [r.event.kind for r in e.shown] == ["near_stall"] and e.current == 0
    row = e.selected_row()
    assert row.wall == "23:00:21" and row.event.data["closest"]
    assert row.event.title.startswith("Near-stall · 4") and row.event.verdict.startswith("throttle 0.")
    key(shell, Qt.Key_F)
    assert e.filter != "near_stall"
    e.set_filter("knock")
    assert [r.event.kind for r in e.shown] == ["knock"]
    e.set_filter("all")
    # keys move the selection, Enter opens the event in Replay at its time
    key(shell, Qt.Key_Down)
    assert e.current == 1
    target = e.selected_row().event
    key(shell, Qt.Key_Return)
    assert shell.current() == mw.REPLAY and shell.replay.block.cursor == pytest.approx(target.t, abs=0.01)
    shell.show_screen(mw.EVENTS)
    e.select(e.shown.index(next(r for r in e.shown if r.event.kind == "dropout")))
    e.show_on_map()
    assert shell.current() == mw.MAP and shell.replay.block.cursor == pytest.approx(72.0, abs=0.2)
    shell.show_screen(mw.EVENTS)
    # notes and verdicts on a simulated drive are kept, not saved
    assert e.set_note("clutch let out too fast").startswith("Kept for now")
    assert e.selected_row().event.note == "clutch let out too fast"
    e.set_verdict("cable bumped")
    assert e.selected_row().event.shown_verdict == "cable bumped"
    spin(app, shell)
    assert shell.events.grab().width() > 0


def test_events_from_the_database_with_notes_that_stay(shell, app, tmp_path):
    logs, ref = near_stall_drive(tmp_path)
    shell.session.logs = logs
    e = shell.events
    e._loaded = False
    shell.show_screen(mw.EVENTS)
    spin(app, shell)
    kinds = sorted(r.event.kind for r in e.rows)
    assert kinds == ["dropout", "key_off", "mark", "near_stall"]
    assert e.day == "2026-11-26" and e.title_text().startswith("2026-11-26 · 1 drive")
    near = next(i for i, r in enumerate(e.shown) if r.event.kind == "near_stall")
    e.select(near)
    row = e.selected_row()
    assert row.event.value == pytest.approx(460, abs=6) and row.wall == "22:51:21"
    assert row.event.verdict == "throttle closed at clutch bite"
    assert e.set_note("first launch of the night") == "Saved with the drive"
    assert e.set_verdict("electrical load step") == "Saved with the drive"
    stored = eventnotes.load(ref.db, ref.drive)
    assert stored[row.event.key] == {"note": "first launch of the night", "verdict": "electrical load step",
                                     "kind": "near_stall", "t": pytest.approx(row.event.t)}
    # a fresh screen reads them back
    again = EventsScreen(shell.session, replay=shell.replay, shell=shell)
    again.refresh(0.0)
    back = next(r for r in again.rows if r.event.kind == "near_stall")
    assert back.event.note == "first launch of the night" and back.event.shown_verdict == "electrical load step"
    # choosing the computed verdict again clears the override
    again.select(again.shown.index(back))
    again.set_verdict(back.event.verdict)
    assert eventnotes.load(ref.db, ref.drive)[back.event.key]["verdict"] == ""
    # Open in Replay opens the drive there, at the event
    again.open_in_replay()
    assert shell.current() == mw.REPLAY and shell.replay.view.title == ref.drive
    assert shell.replay.block.cursor == pytest.approx(back.event.t - shell.replay.view.origin, abs=0.01)
    again.deleteLater()


def test_days_step_through_the_database(shell, app, tmp_path):
    near_stall_drive(tmp_path, day=25)
    logs, _ = near_stall_drive(tmp_path, day=26)
    shell.session.logs = logs
    e = EventsScreen(shell.session)
    e.resize(1200, 700)
    e.refresh(0.0)
    assert e.days == ["2026-11-26", "2026-11-25"] and e.day == "2026-11-26"
    assert not e.next_day.isEnabled() and e.prev_day.isEnabled()
    e.step_day(-1)
    assert e.day == "2026-11-25" and len(e.drives) == 1
    e.step_day(-1)
    assert e.day == "2026-11-25"                            # nothing older
    e.step_day(1)
    assert e.day == "2026-11-26"
    opened = []
    e.openRequested.connect(opened.append)                  # no Replay given: it asks the shell
    e.open_in_replay()
    assert len(opened) == 1 and opened[0].drive.startswith("drive_2026_11_26")
    e.deleteLater()
