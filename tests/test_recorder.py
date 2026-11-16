"""The recorder: drives in the database, captures, key-off; plus retention, library, CLI."""

import sqlite3
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from hondalog import addressmap, logtool
from hondalog.data import Column, DriveRef, annotate, db, new_drive, open_log
from hondalog.data.library import Library
from hondalog.data.redecode import redecode
from hondalog.data.retention import RetentionPolicy
from hondalog.data.synth import write_synthetic
from hondalog.logger import Recorder, StallEvent, read_log
from hondalog.poller import Sample
from hondalog.triggers import TriggerRule

T0 = datetime(2026, 11, 15, 8, 0, 0)


@pytest.fixture()
def amap():
    return addressmap.load_default()


def sample(t, rpm=800.0, link=True, **more):
    values = {"rpm": rpm, "ect": 85.0, "batt": 13.9, **more} if link else {}
    return Sample(t, T0 + timedelta(seconds=t), link, values)


def recorder(amap, tmp_path, **opts):
    opts.setdefault("sync_captures", True)
    opts.setdefault("fsync", False)
    return Recorder(amap, "SIM-P2P", "sim:", out_dir=tmp_path, **opts)


def run(rec, t0, t1, rpm=800.0, link=True, step=0.1):
    for t in np.arange(t0, t1, step):
        rec.feed(sample(float(t), rpm, link))


def test_sweep_recording_and_stall_capture(amap, tmp_path):
    rec = recorder(amap, tmp_path)
    assert isinstance(rec.path, DriveRef) and rec.path.db == tmp_path / "hondalog.db"
    assert rec.path.name.startswith("drive_") and rec.drives == [rec.path] and rec.path.exists()
    run(rec, 0, 50)
    rec.mark("MARK 1")
    rec.mark("it stumbled")
    run(rec, 50, 52)
    run(rec, 52, 63, rpm=0)
    assert rec.notices.get_nowait().reason == "rpm"         # known at once, before the capture is saved
    event = rec.events.get_nowait()
    assert isinstance(event, StallEvent) and event.reason == "rpm" and event.rule == "stall"
    assert event.t == pytest.approx(52.0) and event.path.name == "stall_20261115_080052"
    assert event.path.drive == rec.path.name and event.path.db == rec.path.db
    run(rec, 63, 64, rpm=0)
    rec.note("towed home")
    rec.stop()
    rec.stop()
    assert rec.events.empty() and rec.rows == 640 and rec.markers == 2 and rec.write_error is None

    with open_log(rec.path) as log:
        assert log.status == "complete" and log.meta["end_reason"] == "stopped" and log.streams["ecu"].rows == 640
        assert log.meta["address_map_version"] == amap.version and log.meta["ecu_id"] == "SIM-P2P"
        assert "[[channel]]" in log.meta["address_map_text"]            # the map travels with the log
        assert [r["name"] for r in log.meta["triggers"]][:2] == ["stall", "link_loss"]
        assert [(round(e.t, 1), e.kind, e.text) for e in log.events] == [
            (50.0, "marker", "MARK 1"), (50.0, "marker", "it stumbled"),
            (52.0, "trigger", "STALL (rpm 0)"), (63.9, "note", "towed home")]
        assert log.events[2].data["message"] == "rpm dropped to 0"
        assert log.units("ect") == "°C" and log.column("vtec").kind == "flag"
        assert np.isnan(log.series("map").v).all()                      # not in these samples

    with open_log(event.path) as cap:
        assert cap.meta["trigger"] == "stall" and cap.meta["stall_trigger"] == "rpm dropped to 0"
        assert float(cap.meta["stall_time_s"]) == pytest.approx(52.0) and cap.meta["window_complete"]
        assert cap.meta["capture_of"] == rec.path.name and cap.meta["stall_wall_time"].startswith("2026-11-15T08:00:52")
        rpm = cap.series("rpm")
        assert rpm.t[0] == pytest.approx(22.0, abs=0.11) and 62.0 <= rpm.t[-1] <= 62.2
        assert rpm.v[0] == 800 and rpm.v[-1] == 0 and event.rows == cap.rows
        assert [e.text for e in cap.events] == ["MARK 1", "it stumbled", "STALL (rpm 0)"]

    flat = read_log(event.path)                                         # what the replay tab opens
    assert flat.names[0] == "link" and "address_map_text" not in flat.meta
    assert flat.data["rpm"][-1] == 0 and ("STALL (rpm 0)" in [m[1] for m in flat.markers])

    # the capture is a row, not a copy: one drive's worth of samples, and plain SQL sees the stall
    conn = sqlite3.connect(rec.db)
    assert conn.execute("SELECT count(*), count(DISTINCT drive_id) FROM samples WHERE part = 0").fetchone() == (640, 1)
    assert conn.execute("SELECT name, drive, reason, t_trigger, t_start, t_end, complete, rows FROM stalls"
                        ).fetchone() == ("stall_20261115_080052", rec.path.name, "rpm", pytest.approx(52.0),
                                         pytest.approx(22.0), pytest.approx(62.0), 1, event.rows)
    assert conn.execute("SELECT stall_count, capture_count, status FROM drives").fetchone() == (1, 1, "complete")
    assert conn.execute(f"SELECT rpm, link FROM {rec.path.name} WHERE t > 62.95").fetchall() == [(0.0, 1.0)] * 10


def test_link_loss_capture_and_stop_mid_capture(amap, tmp_path):
    rec = recorder(amap, tmp_path, post_s=5, link_down_s=1.0)
    run(rec, 0, 10)
    run(rec, 10, 12, link=False)
    assert rec.capturing and rec.events.empty()
    rec.stop()                                              # recording stopped before post_s was over
    event = rec.events.get_nowait()
    assert event.reason == "link" and event.t == pytest.approx(11.0, abs=0.11)
    cap = read_log(event.path)
    assert "link lost" in cap.meta["stall_trigger"] and cap.meta["window_complete"] == "False"
    assert cap.data["link"][-1] == 0 and np.isnan(cap.data["rpm"][-1]) and cap.data["rpm"][0] == 800


def test_capture_is_saved_off_the_feed_thread(amap, tmp_path):
    rec = recorder(amap, tmp_path, sync_captures=False, post_s=0.3)
    run(rec, 0, 3)
    run(rec, 3, 3.5, rpm=0)
    deadline = time.monotonic() + 5
    while rec.events.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    event = rec.events.get_nowait()
    assert open_log(event.path).series("rpm").v[-1] == 0
    rec.stop()


def test_custom_rules_manual_trigger_and_meta(amap, tmp_path):
    hot = TriggerRule("overtemp", when="ect > 105", hold_s=0.5, pre_s=2, post_s=1, kind="overtemp",
                      message="coolant above 105")
    quiet = TriggerRule("note_only", when="batt < 12", actions=("mark",))
    rec = recorder(amap, tmp_path, rules=[hot, quiet])
    assert [r.name for r in rec.rules] == ["overtemp", "note_only"]
    run(rec, 0, 5)
    for t in np.arange(5, 8, 0.1):
        rec.feed(sample(float(t), ect=110.0, batt=11.5))
    event = rec.events.get_nowait()
    assert event.reason == "overtemp" and event.path.name.startswith("overtemp_") and event.label == "OVERTEMP"
    assert rec.notices.qsize() == 1                         # "note_only" marks but does not notify
    rec.set_meta(dtc=["P0135"], notes="hot day")
    rec.event("alarm", "ECT high", {"limit": 105})
    rec.trigger()                                           # the capture button
    run(rec, 8, 20)
    manual = rec.events.get_nowait()
    assert manual.reason == "manual" and manual.path.name.startswith("capture_")
    assert manual.path.drive == event.path.drive                        # two captures, one drive
    rec.stop()
    with open_log(rec.path) as log:
        assert log.meta["dtc"] == ["P0135"] and log.meta["notes"] == "hot day"
        kinds = [(e.kind, e.text) for e in log.events]
        assert ("trigger", "OVERTEMP") in kinds and ("trigger", "NOTE_ONLY") in kinds
        assert ("alarm", "ECT high") in kinds and ("trigger", "CAPTURE") in kinds
    with open_log(manual.path) as cap:
        assert cap.meta["dtc"] == ["P0135"] and cap.t0 == pytest.approx(0, abs=0.01)
    rec.mark("late")
    rec.feed(sample(99))                                    # after stop: ignored, no error
    rec.feed_read(99, 0, b"\x00\x00")
    rec.feed_link(99, False)
    rec.feed_values("x", 99, {"a": 1})
    rec.trigger()
    assert rec.rows == 200


def test_per_read_recording_keeps_raw_bytes_and_true_times(amap, tmp_path):
    rec = recorder(amap, tmp_path, post_s=1)
    for i in range(100):
        t = i * 0.06
        rpm = 3000 if i < 80 else 0
        rec.feed_read(t, 0x00, (rpm * 4).to_bytes(2, "big"), latency=0.0035)
        rec.feed_read(t + 0.02, 0x12, bytes([60, 141, 24 + 2 * (i % 50), 23]))
        if i % 10 == 0:
            rec.feed_read(t + 0.04, 0x16, bytes([0, 145, 128, 0]))
    rec.feed_link(6.1, False)
    rec.stop()
    event = rec.events.get_nowait()
    assert event.reason == "rpm" and event.t == pytest.approx(4.8)
    with open_log(rec.path) as log:
        assert set(log.streams) == {"ecu/00+2", "ecu/12+4", "ecu/16+4", "ecu/link"}
        fast = log.streams["ecu/00+2"]
        assert fast.info.raw_len == 2 and fast.info.attrs["addr"] == 0 and fast.rows == 100
        assert bytes(fast.raw[0]) == b"\x2e\xe0" and log.series("rpm").v[0] == 3000
        np.testing.assert_allclose(fast.latency, 0.0035)
        assert np.isnan(log.streams["ecu/12+4"].latency).all()         # not given: unknown, not zero
        assert log.series("map").v[0] == pytest.approx(60 * 0.716 - 5) and log.series("tps").v[1] == 1
        assert log.streams["ecu/16+4"].info.column_names == ["batt", "alt_fr", "eld"]
        assert log.series("rpm").t[1] == pytest.approx(0.06) and log.series("tps").t[1] == pytest.approx(0.08)
        assert log.series("batt").v[0] == pytest.approx(145 / 10.45, rel=1e-6) and log.series("batt").rate_hz < 2
        assert log.series("link").v.tolist() == [1, 0]
        table = log.table(["rpm", "tps", "batt"], base="rpm")
        assert len(table) == 100 and table["tps"][5] == log.series("tps").v[4]   # read 20 ms after rpm

        # the address map changes: decode the same bytes again
        better = addressmap.load_default()
        better.version = "p2p-verified-2"
        ch = better.channel("rpm")
        ch.formula = "x / 2"
        ch.__post_init__()
        new = redecode(log, better)
        assert new.series("rpm").v[0] == 6000 and new.meta["address_map_version"] == "p2p-verified-2"
        assert new.meta["redecoded_from"] == amap.version and new.meta["redecoded_streams"] == 3
        assert new.series("batt").v[0] == pytest.approx(log.series("batt").v[0])
        assert new.series("link").v.tolist() == [1, 0] and len(new.events) == len(log.events)


def test_black_box_stream_shares_the_capture(amap, tmp_path):
    rec = recorder(amap, tmp_path, post_s=1, pre_s=3)
    cols = [Column("ecu_b_min", units="V"), Column("ig1_min", units="V")]
    for i in range(1000):
        t = i * 0.01
        volts = 13.8 if t < 7.5 else 0.2
        rec.feed_values("blackbox/rails", t, {"ecu_b_min": volts, "ig1_min": 13.7}, columns=cols)
        if i % 10 == 0:
            rec.feed(sample(t, 800 if t < 7.5 else 0, link=t < 7.5))
    rec.stop()
    event = rec.events.get_nowait()
    assert event.reason == "link"
    with open_log(event.path) as cap:
        assert set(cap.streams) == {"ecu", "blackbox/rails"}
        rail = cap.series("ecu_b_min")
        assert rail.rate_hz == pytest.approx(100) and rail.units == "V" and cap.streams["blackbox/rails"].info.source == "blackbox"
        drop = rail.t[np.argmax(rail.v < 6)]
        assert drop == pytest.approx(7.5, abs=0.011)        # the rail fell a full second before the trigger
        assert event.t - drop == pytest.approx(1.0, abs=0.11)


def test_black_box_values_can_trigger(amap, tmp_path):
    rule = TriggerRule("rail_drop", when="ecu_b_min < 6", pre_s=1, post_s=0.5, kind="rail")
    rec = recorder(amap, tmp_path, rules=[rule])
    for i in range(300):
        rec.feed_values("blackbox/rails", i * 0.01, {"ecu_b_min": 13.8 if i < 200 else 1.0})
    rec.stop()
    assert rec.events.get_nowait().t == pytest.approx(2.0)


def test_key_off_ends_the_drive_and_the_next_answer_starts_a_new_one(amap, tmp_path):
    rec = recorder(amap, tmp_path, drive_gap_s=20.0, pre_s=15, post_s=2, link_down_s=1.0)
    cols = [Column("ecu_b_min", units="V")]

    def box(t0, t1):
        for t in np.arange(t0, t1, 0.05):
            rec.feed_values("blackbox/rails", float(t), {"ecu_b_min": 12.5}, columns=cols, evaluate=False)
    run(rec, 0, 30)
    box(0, 30)
    run(rec, 30, 33, link=False)                            # key off: the link-loss capture opens
    box(30, 33)
    run(rec, 33, 49.9, link=False)                          # not yet 20 s of silence
    link_cap = rec.events.get_nowait()
    assert link_cap.reason == "link" and not rec.parked and len(rec.drives) == 1
    run(rec, 49.9, 55, link=False)
    assert rec.parked and len(rec.drives) == 1 and rec.status().buffer_s == 0
    box(55, 100)                                            # the box keeps talking: not recorded
    rec.mark("pushed it home")                              # waits for the next drive
    fed = rec.rows
    run(rec, 100, 130)                                      # key on again
    assert not rec.parked and len(rec.drives) == 2 and rec.rows == fed + 300
    run(rec, 130, 135, rpm=0)                               # a stall 30 s into the second drive
    rec.stop()
    stall = rec.events.get_nowait()
    first, second = rec.drives
    assert first.name != second.name and stall.path.drive == second.name and link_cap.path.drive == first.name
    with open_log(first) as a, open_log(second) as b:
        assert (a.status, a.meta["end_reason"]) == ("complete", "link_lost")
        assert (b.status, b.meta["end_reason"]) == ("complete", "stopped")
        assert a.t0 == 0 and a.t1 == pytest.approx(50.0, abs=0.11)         # ends when the silence was long enough
        assert b.t0 == pytest.approx(100.0) and b.t1 == pytest.approx(134.9)    # same clock, nothing in between
        assert b.meta["previous_drive"] == first.name and b.meta["drive_of_session"] == 2
        assert b.meta["epoch_unix"] == a.meta["epoch_unix"] and b.meta["ecu_id"] == "SIM-P2P"
        assert a.series("ecu_b_min").t[-1] < 50.1 and "ecu_b_min" not in b.channels
        assert [e.text for e in b.events_of("marker")] == ["pushed it home"]
    with open_log(stall.path) as cap:                       # 15 s before the stall, all in the second drive
        rpm = cap.series("rpm")
        assert rpm.t[0] == pytest.approx(115.0, abs=0.11) and rpm.t[-1] >= 132.0 and np.all(np.diff(rpm.t) > 0)
    conn = sqlite3.connect(rec.db)
    assert conn.execute("SELECT drive, stalls, captures FROM drive_summary ORDER BY drive_id").fetchall() == [
        (first.name, 1, 1), (second.name, 1, 1)]
    started = [r[0] for r in conn.execute("SELECT started_iso FROM drives ORDER BY id")]
    assert started[1].startswith("2026-11-15T08:01:40")     # the second drive began 100 s into the session


def test_key_on_with_a_dead_ecu_stays_in_the_drive(amap, tmp_path):
    """The stall being hunted: key on, ECU dead. While another source (the
    black box) sees the key on, the drive stays open and its frames are
    recorded; key off ends the drive; key on with the ECU still dead starts
    the next one; the ECU coming back lands in that same drive."""
    rec = recorder(amap, tmp_path, drive_gap_s=20.0, pre_s=15, post_s=2, link_down_s=1.0)
    cols = [Column("bb_ig1", units="V"), Column("bb_ecub", units="V")]

    def box(t0, t1, key=True, ecub=12.3):
        for t in np.arange(t0, t1, 0.05):
            rec.feed_values("blackbox/live", float(t), {"bb_ig1": 12.4 if key else 0.0, "bb_ecub": ecub},
                            source="blackbox", columns=cols, evaluate=False, alive=key)

    def both(t0, t1, **kw):                                 # interleaved, one second at a time
        for t in range(t0, t1):
            run(rec, t, t + 1, **kw)
            box(t, t + 1, ecub=12.3 if kw.get("link", True) else 0.2)
    both(0, 30)
    both(30, 100, link=False)                               # ECU dead for 70 s, key on all the while
    assert not rec.parked and len(rec.drives) == 1          # well past drive_gap_s: still one drive
    assert rec.events.get_nowait().reason == "link"         # the stall catcher saw the ECU go
    for t in range(100, 130):                               # key off: the box says so, the ECU stays silent
        run(rec, t, t + 1, link=False)
        box(t, t + 1, key=False, ecub=0.0)
    assert rec.parked and len(rec.drives) == 1
    box(130, 200, key=False, ecub=0.0)                      # parked: nothing is recorded
    run(rec, 130, 200, link=False, step=1.0)
    assert rec.parked
    box(200, 210, ecub=0.2)                                 # key on again, the ECU does not answer
    assert not rec.parked and len(rec.drives) == 2
    run(rec, 200, 210, link=False, step=0.5)
    both(210, 220)                                          # now it does
    rec.stop()
    first, second = rec.drives
    with open_log(first) as a, open_log(second) as b:
        assert a.meta["end_reason"] == "key_off" and "started_by" not in a.meta
        ig1 = a.series("bb_ig1")
        assert ig1.t[0] == 0 and ig1.t[-1] == pytest.approx(120.0, abs=0.06)    # through the dead ECU and 20 s of key off
        dead = (ig1.t >= 30) & (ig1.t < 100)
        assert dead.sum() == 1400 and np.all(ig1.v[dead] > 12)                  # every frame of the silent stretch
        assert np.all(a.series("bb_ecub").v[dead] < 1)                          # ... and what it was for: ECU +B gone
        rpm = a.series("rpm")
        assert np.isnan(rpm.v[rpm.t >= 30]).all() and a.series("link").v[-1] == 0  # the ECU side: nothing after 30 s
        assert b.meta["started_by"].startswith("key on seen") and b.meta["previous_drive"] == first.name
        assert b.t0 == pytest.approx(200.0) and b.series("bb_ig1").t[0] == pytest.approx(200.0)
        rpm = b.series("rpm")
        assert rpm.t[~np.isnan(rpm.v)][0] == pytest.approx(210.0) and b.meta["end_reason"] == "stopped"
        link = b.series("link")
        assert link.v[0] == 0 and link.v[-1] == 1           # silent at first, answering at the end
    conn = sqlite3.connect(rec.db)
    assert conn.execute("SELECT count(*) FROM drives").fetchone()[0] == 2


def test_a_source_that_cannot_see_the_key_does_not_hold_the_drive_open(amap, tmp_path):
    """alive=False from the first frame (the IG1 input is not wired): the
    drive ends on the ECU's silence as it did before, and is not ended early
    while the ECU answers."""
    rec = recorder(amap, tmp_path, drive_gap_s=5.0, link_down_s=60.0)
    for t in range(0, 30):
        run(rec, t, t + 1)
        rec.feed_values("blackbox/live", t + 0.5, {"bb_ig1": 0.0}, source="blackbox", alive=False)
    assert not rec.parked                                   # 30 s of "key off" from the box, the ECU answering
    for t in range(30, 40):
        run(rec, t, t + 1, link=False)
        rec.feed_values("blackbox/live", t + 0.5, {"bb_ig1": 0.0}, source="blackbox", alive=False)
    assert rec.parked
    rec.stop()
    with open_log(rec.drives[0]) as log:
        assert log.t1 == pytest.approx(35.0, abs=0.6) and log.meta["end_reason"] == "key_off"


def test_a_capture_near_the_start_of_a_drive_is_cut_at_the_drive(amap, tmp_path):
    rec = recorder(amap, tmp_path, drive_gap_s=5.0, pre_s=30, post_s=1, link_down_s=60.0)
    run(rec, 0, 10)
    run(rec, 10, 16, link=False)
    assert rec.parked
    run(rec, 40, 44)
    run(rec, 44, 46, rpm=0)
    rec.stop()
    event = rec.events.get_nowait()
    with open_log(event.path) as cap:
        assert cap.t0 == pytest.approx(40.0) and cap.meta["window"] == "30 s before, 1 s after"
    assert sqlite3.connect(rec.db).execute("SELECT t_start, t_trigger FROM captures").fetchone() == (
        pytest.approx(40.0), pytest.approx(44.0))


def test_the_drive_does_not_end_while_a_capture_is_open(amap, tmp_path):
    rec = recorder(amap, tmp_path, drive_gap_s=2.0, pre_s=2, post_s=6, link_down_s=1.0)
    run(rec, 0, 4)
    run(rec, 4, 9, link=False)                              # 2 s of silence pass while the window is open
    assert rec.capturing and not rec.parked
    run(rec, 9, 12, link=False)
    assert not rec.capturing and rec.parked and rec.events.get_nowait().reason == "link"
    rec.stop()
    assert len(rec.drives) == 1 and open_log(rec.path).meta["end_reason"] == "link_lost"
    never = recorder(amap, tmp_path, drive_gap_s=None)      # None: one drive for the whole session
    run(never, 0, 2)
    run(never, 2, 500, link=False, step=5)
    assert not never.parked
    never.stop()


def test_two_recorders_share_one_database(amap, tmp_path):
    a = recorder(amap, tmp_path, flush_interval=0.01)
    b = recorder(amap, tmp_path)                            # same minute: the names must differ
    assert a.path != b.path and a.db == b.db
    for i in range(600):
        a.feed(sample(i * 0.1))
        b.feed(sample(i * 0.1, rpm=3000.0))
        if i % 100 == 0:
            time.sleep(0.02)
    a.stop()
    with open_log(b.path) as live:
        assert live.status == "recording"                   # a's stop did not touch b's drive
    b.stop()
    with open_log(a.path) as one, open_log(b.path) as two:
        assert one.rows == two.rows == 600 and one.series("rpm").v.max() == 800 and two.series("rpm").v.min() == 3000
    assert a.write_error is None and b.write_error is None


def test_disk_error_does_not_stop_feeding(amap, tmp_path):
    rec = recorder(amap, tmp_path, flush_interval=0.01)
    run(rec, 0, 1)
    rec.flush()
    real = rec._store._conn

    class Full:
        def execute(self, sql, *args):
            if sql.startswith("BEGIN"):
                raise sqlite3.OperationalError("database or disk is full")
            return real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real, name)
    rec._store._conn = Full()
    run(rec, 1, 2)
    time.sleep(0.1)
    run(rec, 2, 3)                                          # still accepts samples
    assert isinstance(rec.write_error, OSError) and "full" in str(rec.write_error)
    rec._store._conn = real
    rec.stop()
    with open_log(rec.path) as log:
        assert log.status == "complete" and 10 <= log.rows < 30      # what was fed while it failed is lost


# ---- retention ----

NOW = 1_800_000_000


def aged(path, days, rows=50, capture=False, **meta):
    """A finished drive that started ``days`` ago."""
    started = NOW - days * 86400
    with new_drive(path, {"started_unix": started, "epoch_unix": started, **meta}) as w:
        w.add_stream("s", [Column("a"), Column("rpm")], raw_len=8)
        t = np.arange(rows, dtype=float)
        w.append_columns("s", t, {"a": t, "rpm": 800 + t}, np.zeros((rows, 8), np.uint8))
        if capture:
            w.capture("stall", 5.0, 0.0, 10.0, reason="rpm", message="rpm dropped to 0",
                      wall=datetime.fromtimestamp(started + 5))
    return w.ref


def test_retention_policy(tmp_path):
    path = tmp_path / "hondalog.db"
    old = aged(path, 40)
    kept_notes = aged(path, 39, notes="first verified run")
    stall = aged(path, 38, capture=True)
    tagged = aged(path, 37, tags=["dyno"])
    flagged = aged(path, 36, keep=True)
    plain = aged(path, 20)
    mid = aged(path, 10, rows=20_000)
    new = aged(path, 1)
    assert RetentionPolicy().plan(path, NOW) == [] and not RetentionPolicy().active
    assert RetentionPolicy(max_age_days=30).plan(path, NOW) == [old]        # notes, tags, keep and captures protect
    assert RetentionPolicy(max_age_days=30, keep_captures=False).plan(path, NOW) == [old, stall]
    assert RetentionPolicy(max_drives=5).plan(path, NOW) == [old, plain, mid]
    assert RetentionPolicy(max_total_mb=0.5).plan(path, NOW) == [old, plain, mid]
    assert RetentionPolicy(max_total_mb=0.7).plan(path, NOW) == [old, plain, mid]     # mid is most of the file
    assert RetentionPolicy(max_total_mb=5).plan(path, NOW) == []
    assert RetentionPolicy(max_age_days=0).plan(path, NOW) == [old, plain, mid]       # the newest always stays
    assert RetentionPolicy(max_age_days=0, keep_newest=0).plan(path, NOW)[-1] == new
    assert RetentionPolicy(max_age_days=30).plan(tmp_path / "missing", NOW) == []
    assert RetentionPolicy(max_age_days=30).plan(tmp_path, NOW) == [old]    # the folder names the database too

    before = db.file_size(path)
    assert RetentionPolicy(max_drives=5).apply(path, NOW) == [old, plain, mid]
    assert not old.exists() and not mid.exists()
    assert all(r.exists() for r in (kept_notes, stall, tagged, flagged, new))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT count(*) FROM drives").fetchone()[0] == 5
    assert conn.execute("SELECT count(DISTINCT drive_id), count(*) FROM samples").fetchone() == (5, 5 * 50)
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0         # the pages went back to the disk
    assert db.file_size(path) < before - 0.6e6 and conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    assert old.name not in views and mid.name not in views and new.name in views
    assert open_log(stall.db, "last").rows == 50 and open_log(stall).rows == 50
    assert RetentionPolicy(max_drives=5).apply(path, NOW) == []
    assert RetentionPolicy(max_drives=1, keep_captures=False, full_vacuum=True).apply(path, NOW) == [stall]
    assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 0


def test_delete_one_drive_or_one_capture(amap, tmp_path):
    from hondalog.data import retention
    path = tmp_path / "hondalog.db"
    big = aged(path, 9, rows=20_000)
    stall = aged(path, 8, capture=True)
    flagged = aged(path, 7, keep=True)
    capture = next(e for e in Library(path).refresh() if e.kind == "stall")
    assert retention.lookup("last", path).name == flagged.name
    assert capture.ref.drive == stall.name and "The drive and its data stay" in retention.describe(capture)
    assert "20000 rows" in retention.describe(retention.lookup(big.name, path))

    # a capture is only an entry: the drive keeps every row, its counts follow
    assert retention.delete(capture.name, path).name == capture.name
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 0
    assert conn.execute("SELECT stall_count, capture_count FROM drives WHERE name=?",
                        (stall.name,)).fetchone() == (0, 0)
    assert open_log(stall).rows == 50
    with pytest.raises(ValueError, match="no drive or capture"):
        retention.delete(capture.name, path)

    # a drive goes with everything in it, and the file shrinks (incremental vacuum)
    before = db.file_size(path)
    assert retention.delete(big, path).name == big.name and not big.exists()
    assert db.file_size(path) < before - 0.5e6
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert big.name not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}

    # keep: only when the caller says the user confirmed that as well
    with pytest.raises(retention.KeepProtected, match="marked keep"):
        retention.delete(flagged.name, path)
    assert flagged.exists()
    assert retention.delete(flagged.name, path, allow_keep=True).keep and not flagged.exists()

    # the drive being recorded is refused, with or without keep
    rec = recorder(amap, tmp_path)
    run(rec, 0, 1)
    rec.flush()
    with pytest.raises(ValueError, match="being recorded"):
        retention.delete(rec.path, allow_keep=True)
    rec.stop()
    assert retention.delete(rec.path).name == rec.path.name
    assert [e.name for e in Library(path).refresh()] == [stall.name]


def test_cli_rm_asks_and_keep_asks_twice(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HONDALOG_DIR", str(tmp_path))
    path = tmp_path / "hondalog.db"
    plain, stall, flagged = aged(path, 9), aged(path, 8, capture=True), aged(path, 7)
    capture = next(e.name for e in Library(path).refresh() if e.kind == "stall")
    answers: list[str] = []
    asked: list[str] = []

    def fake_input(prompt):
        asked.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)
    monkeypatch.setattr("builtins.input", fake_input)

    assert logtool.main(["keep", flagged.name]) == 0 and "marked keep" in capsys.readouterr().out
    assert logtool.main(["list"]) == 0 and "[keep]" in capsys.readouterr().out

    answers[:] = ["n"]
    assert logtool.main(["rm", plain.name]) == 1 and plain.exists()
    assert asked[-1].startswith(f"Delete drive {plain.name} (") and asked[-1].endswith("[y/N] ")
    assert f"kept {plain.name}" in capsys.readouterr().out
    assert logtool.main(["rm", plain.name]) == 1 and plain.exists()            # no answer (EOF) is a no
    answers[:] = ["y"]
    assert logtool.main(["rm", plain.name]) == 0 and not plain.exists()
    assert f"deleted {plain.name}" in capsys.readouterr().out

    answers[:] = ["yes"]
    assert logtool.main(["rm", capture]) == 0 and stall.exists()
    assert "The drive and its data stay" in asked[-1]
    assert not [e for e in Library(path).refresh() if e.kind == "stall"]

    # keep: a second question, answered by typing the name; --yes alone is not enough
    asked.clear()
    answers[:] = ["y", "y"]
    assert logtool.main(["rm", flagged.name]) == 1 and flagged.exists()
    assert len(asked) == 2 and "marked keep" in asked[1] and "Type its name" in asked[1]
    assert logtool.main(["rm", "--yes", flagged.name]) == 1 and flagged.exists()
    assert "--force" in capsys.readouterr().out
    answers[:] = [flagged.name]
    assert logtool.main(["rm", "--yes", flagged.name]) == 0 and not flagged.exists()
    assert logtool.main(["keep", stall.name]) == 0 and logtool.main(["keep", stall.name, "--off"]) == 0
    assert logtool.main(["keep", stall.name]) == 0
    assert logtool.main(["rm", "--yes", "--force", stall.name, "drive_nope"]) == 2     # one gone, one unknown
    assert not stall.exists() and "no drive or capture 'drive_nope'" in capsys.readouterr().err
    assert Library(path).refresh() == []


def test_retention_leaves_a_running_drive_alone(amap, tmp_path):
    old = aged(tmp_path / "hondalog.db", 5000)
    rec = recorder(amap, tmp_path)
    run(rec, 0, 1)
    rec.flush()
    policy = RetentionPolicy(max_age_days=0, keep_newest=0)
    assert policy.apply(tmp_path) == [old] and rec.path.exists()            # the one being recorded stays
    run(rec, 1, 2)
    rec.stop()
    assert open_log(rec.path).rows == 20


def test_recorder_applies_retention_on_stop(amap, tmp_path):
    old = aged(tmp_path / "hondalog.db", 5000)
    rec = recorder(amap, tmp_path, retention=RetentionPolicy(max_age_days=30))
    run(rec, 0, 1)
    rec.stop()
    assert not old.exists() and rec.path.exists()


# ---- library ----

def test_library_lists_searches_and_annotates(amap, tmp_path):
    rec = recorder(amap, tmp_path)
    run(rec, 0, 40)
    rec.mark("idle hunting")
    run(rec, 40, 42)
    run(rec, 42, 55, rpm=0)
    rec.stop()
    old = tmp_path / "log_20261110_075801.csv"
    old.write_text(
        "# hondalog 0.1.0\n# started: 2026-11-10T07:58:01\ntime_s,wall_time,link,rpm,marker\n0.1,x,1,750,\n0.2,x,1,760,cold start\n")
    (tmp_path / "readme.txt").write_text("not a log")
    lib = Library(tmp_path)
    entries = {e.name: e for e in lib.refresh()}
    assert len(entries) == 2 and lib.path == tmp_path / "hondalog.db"       # files are not listed: import them
    main, stall = entries[rec.path.name], next(e for e in entries.values() if e.kind == "stall")
    assert main.kind == "drive" and main.duration == pytest.approx(54.9) and main.rows == 550
    assert main.ref == rec.path and main.status == "complete" and (main.stalls, main.captures) == (1, 1)
    assert main.map_version == amap.version and main.markers == ["idle hunting", "STALL (rpm 0)"]
    assert stall.trigger == "rpm dropped to 0" and stall.duration == pytest.approx(40, abs=0.3)
    assert stall.ref.capture == stall.name and stall.ref.drive == main.name and stall.markers == main.markers
    assert 0 < len(main.thumb_hi) <= 64 and max(main.thumb_hi) == 800 and min(main.thumb_lo) == 0
    assert lib.drives() == [main]

    assert logtool.main(["--db", str(tmp_path), "import", str(old)]) == 0
    imported = next(e for e in lib.refresh() if e.status == "imported")
    assert imported.started == "2026-11-10T07:58:01" and imported.name == "drive_2026_11_10_0758"
    assert {e.name for e in lib.search("hunting")} == {stall.name, main.name}
    assert [e.name for e in lib.search("cold start")] == [imported.name]
    assert lib.search("hunting nonsense") == [] and len(lib.search(kinds=["stall"])) == 1
    lib.set_notes(lib.get(main.name), notes="Fixed by cleaning the IACV", tags=["idle", "fixed"], keep=True)
    found = lib.search("iacv")
    assert {e.name for e in found} == {stall.name, main.name} and all(e.tags == ["idle", "fixed"] and e.keep for e in found)
    assert lib.search(tag="fixed") == found and lib.search(tag="nope") == []
    lib.set_notes(imported.name, notes="the first log I ever took")         # by name works too
    assert lib.get(imported.name).notes.startswith("the first")
    with pytest.raises(ValueError):
        lib.set_notes(old, notes="x")                                       # a file is not in the library

    annotate(rec.path, notes="changed")                                     # no cache to go stale
    assert Library(tmp_path / "hondalog.db").refresh() and Library(tmp_path).get(main.name) is None
    assert {e.notes for e in Library(tmp_path / "hondalog.db").refresh() if e.drive_id == main.drive_id} == {"changed"}
    assert Library(tmp_path / "missing").refresh() == []
    crashed = recorder(amap, tmp_path)
    run(crashed, 0, 2)
    crashed.flush()
    db.mark_active(crashed.db, crashed._log.id, False)                      # its process dies
    assert lib.get(crashed.path.name) is None and lib.refresh()
    assert lib.get(crashed.path.name).status == "crashed" and lib.get(crashed.path.name).rows == 20
    crashed.stop()                                                          # it was only asleep: it finishes its drive
    assert lib.refresh() and lib.get(crashed.path.name).status == "complete"


# ---- command line ----

def test_logtool(tmp_path, capsys, amap, monkeypatch):
    monkeypatch.setenv("HONDALOG_DIR", str(tmp_path))
    monkeypatch.delenv("HONDALOG_DB", raising=False)
    src = write_synthetic(tmp_path / "hondalog.db", duration_s=60)
    assert logtool.main(["info"]) == 0                                      # the newest drive, default database
    out = capsys.readouterr().out
    assert "ecu/00+2" in out and "2 raw bytes/row" in out and "MARK 1" in out and "16.0 Hz" in out
    assert f"hondalog.db#{src.name}" in out and "status: complete" in out
    assert logtool.main(["info", src.name]) == 0 and logtool.main(["info", "1"]) == 0
    assert logtool.main(["--db", str(tmp_path / "hondalog.db"), "info", "last"]) == 0
    capsys.readouterr()

    mlg_path = tmp_path / "out.mlg"
    assert logtool.main(["export", src.name, str(mlg_path), "--channels", "rpm,map", "--filter", "rpm > 2000",
                         "--start", "10", "--end", "50", "--compact"]) == 0
    back = open_log(mlg_path)
    assert back.channels == ["rpm", "map"] and back.series("rpm").v.min() > 2000
    assert logtool.main(["export", src.name, str(tmp_path / "x.parquet")]) == 2
    assert "unknown export format" in capsys.readouterr().err
    assert logtool.main(["export", src.name, str(tmp_path / "cut.db"), "--start", "10", "--end", "20"]) == 0
    with open_log(tmp_path / "cut.db") as cut:                              # a database to hand to someone
        assert cut.t0 >= 10 and cut.t1 <= 20 and cut.streams["ecu/00+2"].raw.shape[1] == 2
        assert cut.meta["exported_from"].endswith(src.name)

    old = tmp_path / "old.csv"
    old.write_text("# hondalog 0.1.0\ntime_s,wall_time,link,rpm,marker\n0.1,x,1,750,\n0.2,x,1,760,MARK 1\n")
    assert logtool.main(["import", str(old)]) == 0
    line = capsys.readouterr().out
    imported = line.split(" -> ")[1].split()[0]
    assert imported.startswith("drive_") and "(2 rows, 1 events)" in line
    with open_log(imported) as log:
        assert log.markers == [(0.2, "MARK 1")] and log.status == "imported" and log.meta["imported_from"] == str(old)
    assert logtool.main(["info", str(old)]) == 0 and "status" not in capsys.readouterr().out   # a file, read as it is

    assert logtool.main(["report", src.name, "-o", str(tmp_path / "r.html")]) == 0
    assert (tmp_path / "r.html").read_text().startswith("<!DOCTYPE")
    assert logtool.main(["redecode", imported, "--map", str(addressmap.DEFAULT_MAP)]) == 1     # no raw bytes in a CSV
    rec = recorder(amap, tmp_path)
    rec.feed_read(0.0, 0, b"\x0c\x80")
    rec.stop()
    capsys.readouterr()
    assert logtool.main(["redecode", rec.path.name, "--map", str(addressmap.DEFAULT_MAP)]) == 0
    again = capsys.readouterr().out.split()[1]
    with open_log(again) as log:
        assert log.series("rpm").v.tolist() == [800] and log.meta["redecoded_drive"] == rec.path.name
        assert log.streams["ecu/00+2"].raw.tolist() == [[0x0c, 0x80]]
    assert logtool.main(["redecode", rec.path.name, str(tmp_path / "other.db"), "--map", str(addressmap.DEFAULT_MAP)]) == 0
    assert open_log(tmp_path / "other.db").series("rpm").v.tolist() == [800]

    capsys.readouterr()
    assert logtool.main(["list", "-q", "zzz"]) == 0 and capsys.readouterr().out == ""    # the query matches nothing
    assert logtool.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert all(name in listed for name in (src.name, imported, rec.path.name, again)) and "60.0 s" in listed

    # compare: two drives of this database, and one from another file
    assert logtool.main(["compare", src.name, f"{tmp_path / 'cut.db'}#last", "--channels", "rpm,map,nope"]) == 0
    out = capsys.readouterr().out
    assert "laid over A with -10." in out and "aligned on start" in out and "rpm" in out and "nope" not in out
    assert logtool.main(["compare", src.name, imported]) == 0 and "only in A:" in capsys.readouterr().out
    assert logtool.main(["compare", src.name, "drive_1999"]) == 2 and "no drive" in capsys.readouterr().err

    # SQL passthrough
    assert logtool.main(["sql", "SELECT drive, status, rows FROM drive_summary ORDER BY drive_id LIMIT 1"]) == 0
    assert capsys.readouterr().out.splitlines() == ["drive\tstatus\trows", f"{src.name}\tcomplete\t5958"]
    assert logtool.main(["sql", "--csv", f"SELECT round(max(rpm)), count(*) FROM {src.name}"]) == 0
    assert capsys.readouterr().out.splitlines()[1].endswith(",960")
    assert logtool.main(["sql", "SELECT raw FROM ecu_reads LIMIT 1"]) == 0 and len(capsys.readouterr().out.split()[1]) == 4
    assert logtool.main(["sql", "DELETE FROM drives"]) == 2                 # read-only
    assert logtool.main(["sql", "SELEC nonsense"]) == 2
    assert "readonly" in capsys.readouterr().err.replace("-", "").replace(" ", "")

    assert logtool.main(["prune"]) == 1
    capsys.readouterr()
    assert logtool.main(["prune", "--days", "0"]) == 0
    out = capsys.readouterr().out
    assert f"would delete {src.name}" in out and "dry run" in out and src.exists()
    size = db.file_size(src.db)
    assert logtool.main(["prune", "--days", "0", "--yes"]) == 0 and not src.exists()
    assert db.file_size(src.db) < size
    assert logtool.main(["vacuum"]) == 0 and "check: ok" in capsys.readouterr().out
    assert logtool.main(["vacuum", "--full"]) == 0 and "rewritten" in capsys.readouterr().out
    assert logtool.main(["info", "drive_nope"]) == 2
    assert logtool.main(["--db", str(tmp_path / "nowhere"), "info"]) == 2
    err = capsys.readouterr().err
    assert "no drive, capture or file 'drive_nope'" in err and "no database there" in err
    assert logtool.main(["--db", str(tmp_path / "nowhere"), "list"]) == 0
