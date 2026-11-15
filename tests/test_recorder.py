"""The recorder: native logging, captures, rotation; plus retention, library, CLI."""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pytest

from hondalog import addressmap, logtool
from hondalog.data import Column, LogWriter, annotate, open_log, read_hlog
from hondalog.data.library import CACHE_NAME, Library
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
    assert rec.path.suffix == ".hlog" and rec.path.name.startswith("log_")
    run(rec, 0, 50)
    rec.mark("MARK 1")
    rec.mark("it stumbled")
    run(rec, 50, 52)
    run(rec, 52, 63, rpm=0)
    assert rec.notices.get_nowait().reason == "rpm"         # known at once, before the file exists
    event = rec.events.get_nowait()
    assert isinstance(event, StallEvent) and event.reason == "rpm" and event.rule == "stall"
    assert event.t == pytest.approx(52.0) and event.path.name == "stall_20261115_080052.hlog"
    run(rec, 63, 64, rpm=0)
    rec.note("towed home")
    rec.stop()
    rec.stop()
    assert rec.events.empty() and rec.rows == 640 and rec.markers == 2 and rec.write_error is None

    with open_log(rec.path) as log:
        assert log.indexed and log.streams["ecu"].rows == 640
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
    assert read_hlog(event.path).series("rpm").v[-1] == 0
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


@dataclass
class Read:
    t: float
    addr: int
    data: bytes


@dataclass
class ReadSample:
    t: float
    wall: datetime
    link: bool
    values: dict = field(default_factory=dict)
    reads: list = field(default_factory=list)


def test_per_read_recording_keeps_raw_bytes_and_true_times(amap, tmp_path):
    rec = recorder(amap, tmp_path, post_s=1)
    for i in range(100):
        t = i * 0.06
        rpm = 3000 if i < 80 else 0
        rec.feed_read(t, 0x00, (rpm * 4).to_bytes(2, "big"))
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


def test_sample_with_reads_attribute(amap, tmp_path):
    rec = recorder(amap, tmp_path)
    for i in range(5):
        t = i * 0.1
        rec.feed(ReadSample(t + 0.05, T0, True, {}, [Read(t, 0, b"\x0c\x80"), Read(t + 0.03, 0x12, b"\x3c\x8d\x18\x17")]))
    rec.feed(ReadSample(0.6, T0, False))
    rec.stop()
    with open_log(rec.path) as log:
        assert "ecu" not in log.streams and log.series("rpm").v.tolist() == [800] * 5
        assert log.series("link").v.tolist() == [1, 0] and log.streams["ecu/12+4"].raw.shape == (5, 4)


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


def test_rotation_and_capture_across_parts(amap, tmp_path):
    rec = recorder(amap, tmp_path, rotate_s=20.0, pre_s=15, post_s=2)
    run(rec, 0, 45)
    run(rec, 45, 50, rpm=0)
    rec.stop()
    assert [p.name for p in rec.paths] == [rec.paths[0].name, rec.paths[0].stem + "_p002.hlog",
                                           rec.paths[0].stem + "_p003.hlog"]
    parts = [read_hlog(p) for p in rec.paths]
    assert all(p.indexed for p in parts) and sum(p.streams["ecu"].rows for p in parts) == 500
    assert parts[1].meta["previous_part"] == rec.paths[0].name and parts[2].meta["part"] == 3
    assert parts[1].t0 == pytest.approx(20.0) and parts[0].t1 < 20.0
    event = rec.events.get_nowait()                          # trigger at 45 s, 5 s into part 3
    rpm = read_hlog(event.path).series("rpm")
    assert rpm.t[0] == pytest.approx(30.0, abs=0.11) and rpm.t[-1] >= 47.0    # pre-window reaches into part 2
    assert np.all(np.diff(rpm.t) > 0) and len(rpm) == pytest.approx(171, abs=2)


def test_rotation_waits_for_an_open_capture(amap, tmp_path):
    rec = recorder(amap, tmp_path, rotate_s=5.0, pre_s=2, post_s=4)
    run(rec, 0, 4)
    run(rec, 4, 7, rpm=0)                                   # 5 s passes while the window is open
    assert len(rec.paths) == 1
    run(rec, 7, 9, rpm=0)
    assert len(rec.paths) == 2
    rec.stop()


def test_size_rotation_and_unique_names(amap, tmp_path):
    a = recorder(amap, tmp_path, rotate_mb=0.02, rotate_s=None, flush_interval=0.01)
    b = recorder(amap, tmp_path)                            # same second: must not overwrite
    assert a.path != b.path
    for i in range(600):
        a.feed(sample(i * 0.1))
        if i % 100 == 0:
            time.sleep(0.05)
    a.stop()
    b.stop()
    assert len(a.paths) > 1 and sum(read_hlog(p).rows for p in a.paths) == 600


def test_disk_error_does_not_stop_feeding(amap, tmp_path, monkeypatch):
    rec = recorder(amap, tmp_path, flush_interval=0.01)
    run(rec, 0, 1)

    def full(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fdatasync", full)
    rec._writer_opts["fsync"] = True
    rec._log.fsync = True
    run(rec, 1, 2)
    time.sleep(0.1)
    run(rec, 2, 3)                                          # still accepts samples
    assert isinstance(rec.write_error, OSError)
    monkeypatch.undo()
    rec.stop()


# ---- retention ----

def aged(folder, name, days, size=1000, **meta):
    path = folder / name
    if name.endswith(".hlog"):
        with LogWriter(path, meta, background=False) as w:
            w.add_stream("s", [Column("a")])
            w.append_columns("s", np.arange(size // 12, dtype=float), {})
    else:
        path.write_bytes(b"x" * size)
    stamp = 1_800_000_000 - days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_retention_policy(tmp_path):
    now = 1_800_000_000
    old = aged(tmp_path, "log_20260101_000000.hlog", 40)
    kept_notes = aged(tmp_path, "log_20260102_000000.hlog", 39, notes="first verified run")
    stall = aged(tmp_path, "stall_20260103_000000.hlog", 38)
    csv = aged(tmp_path, "log_20260201_000000.csv", 20)
    mid = aged(tmp_path, "log_20260301_000000.hlog", 10, size=600_000)
    new = aged(tmp_path, "log_20260310_000000.hlog", 1)
    aged(tmp_path, "notes.txt", 400)
    assert RetentionPolicy().plan(tmp_path, now) == [] and not RetentionPolicy().active
    assert RetentionPolicy(max_age_days=30).plan(tmp_path, now) == [old]     # notes and captures protect
    assert RetentionPolicy(max_age_days=30, keep_captures=False).plan(tmp_path, now) == [old, stall]
    assert RetentionPolicy(max_files=2).plan(tmp_path, now) == [old, csv, mid]
    assert RetentionPolicy(max_total_mb=0.5).plan(tmp_path, now) == [old, csv, mid]
    assert RetentionPolicy(max_age_days=0).plan(tmp_path, now) == [old, csv, mid]    # newest always stays
    assert RetentionPolicy(max_age_days=30).plan(tmp_path / "missing") == []
    broken = aged(tmp_path, "log_20251201_000000.hlog", 90)
    broken.write_bytes(b"garbage")
    os.utime(broken, (now - 90 * 86400,) * 2)
    assert RetentionPolicy(max_age_days=30).apply(tmp_path, now) == [old]    # unreadable is left alone
    assert not old.exists() and all(p.exists() for p in (kept_notes, stall, csv, mid, new, broken))


def test_recorder_applies_retention_on_stop(amap, tmp_path):
    old = aged(tmp_path, "log_20200101_000000.hlog", 5000)
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
    (tmp_path / "log_20261110_075801.csv").write_text(
        "# hondalog 0.1.0\n# started: 2026-11-10T07:58:01\ntime_s,wall_time,link,rpm,marker\n0.1,x,1,750,\n0.2,x,1,760,cold start\n")
    (tmp_path / "broken.hlog").write_bytes(b"nope")
    (tmp_path / "readme.txt").write_text("not a log")
    lib = Library(tmp_path)
    entries = {e.name: e for e in lib.refresh()}
    assert len(entries) == 4 and (tmp_path / CACHE_NAME).exists()
    main, stall = entries[rec.path.name], next(e for e in entries.values() if e.kind == "stall")
    assert main.kind == "log" and main.duration == pytest.approx(54.9) and main.rows == 550
    assert main.map_version == amap.version and main.markers == ["idle hunting", "STALL (rpm 0)"]
    assert stall.trigger == "rpm dropped to 0" and stall.duration == pytest.approx(40, abs=0.3)
    assert 0 < len(main.thumb_hi) <= 64 and max(main.thumb_hi) == 800 and min(main.thumb_lo) == 0
    assert entries["broken.hlog"].error and entries["log_20261110_075801.csv"].started == "2026-11-10T07:58:01"

    assert [e.name for e in lib.search("hunting")] == [stall.name, main.name] or \
           [e.name for e in lib.search("hunting")] == [main.name, stall.name]
    assert [e.name for e in lib.search("cold start")] == ["log_20261110_075801.csv"]
    assert lib.search("hunting nonsense") == [] and len(lib.search(kinds=["stall"])) == 1
    lib.set_notes(main, notes="Fixed by cleaning the IACV", tags=["idle", "fixed"], keep=True)
    found = lib.search("iacv")
    assert [e.name for e in found] == [main.name] and found[0].tags == ["idle", "fixed"]
    assert lib.search(tag="fixed") == found and lib.search(tag="nope") == []
    with pytest.raises(ValueError):
        lib.set_notes(tmp_path / "log_20261110_075801.csv", notes="x")

    again = Library(tmp_path)                               # second scan comes from the cache
    opened = []
    import hondalog.data.library as library_module
    real = library_module.summarise
    library_module.summarise = lambda p, c="rpm": opened.append(p) or real(p, c)
    try:
        assert len(again.refresh()) == 4 and opened == []
        annotate(rec.path, notes="changed")
        again.refresh()
        assert [p.name for p in opened] == [rec.path.name]
    finally:
        library_module.summarise = real
    (tmp_path / CACHE_NAME).write_text("{broken")
    assert len(Library(tmp_path).refresh()) == 4 and Library(tmp_path / "missing").refresh() == []


# ---- command line ----

def test_logtool(tmp_path, capsys, amap):
    src = write_synthetic(tmp_path / "log_20261115_080000.hlog", duration_s=60)
    assert logtool.main(["info", str(src)]) == 0
    out = capsys.readouterr().out
    assert "ecu/00+2" in out and "2 raw bytes/row" in out and "MARK 1" in out and "16.0 Hz" in out

    mlg_path = tmp_path / "out.mlg"
    assert logtool.main(["export", str(src), str(mlg_path), "--channels", "rpm,map", "--filter", "rpm > 2000",
                         "--start", "10", "--end", "50", "--compact"]) == 0
    back = open_log(mlg_path)
    assert back.channels == ["rpm", "map"] and back.series("rpm").v.min() > 2000
    assert logtool.main(["export", str(src), str(tmp_path / "x.parquet")]) == 2
    assert "unknown export format" in capsys.readouterr().err

    old = tmp_path / "old.csv"
    old.write_text("# hondalog 0.1.0\ntime_s,wall_time,link,rpm,marker\n0.1,x,1,750,\n0.2,x,1,760,MARK 1\n")
    assert logtool.main(["import", str(old), "-o", str(tmp_path)]) == 0
    assert read_hlog(tmp_path / "old.hlog").markers == [(0.2, "MARK 1")]
    assert logtool.main(["import", str(old)]) == 0 and "exists" in capsys.readouterr().out
    assert logtool.main(["import", str(old), "--force"]) == 0

    torn = tmp_path / "torn.hlog"
    torn.write_bytes(src.read_bytes()[:200_000])
    assert logtool.main(["info", str(torn)]) == 0 and "run 'repair'" in capsys.readouterr().out
    assert logtool.main(["repair", str(torn), str(src)]) == 0
    out = capsys.readouterr().out
    assert "index written" in out and "already complete" in out and read_hlog(torn).indexed

    assert logtool.main(["report", str(src)]) == 0 and src.with_suffix(".html").read_text().startswith("<!DOCTYPE")
    assert logtool.main(["redecode", str(tmp_path / "old.hlog"), str(tmp_path / "r.hlog"),
                         "--map", str(addressmap.DEFAULT_MAP)]) == 1       # no raw bytes in a CSV import
    rec = recorder(amap, tmp_path)
    rec.feed_read(0.0, 0, b"\x0c\x80")
    rec.stop()
    assert logtool.main(["redecode", str(rec.path), str(tmp_path / "r.hlog"), "--map", str(addressmap.DEFAULT_MAP)]) == 0
    assert read_hlog(tmp_path / "r.hlog").series("rpm").v.tolist() == [800]

    capsys.readouterr()
    assert logtool.main(["list", str(tmp_path), "-q", "zzz"]) == 0
    assert "log_20261115_080000.hlog" not in capsys.readouterr().out       # the query matches nothing
    assert logtool.main(["list", str(tmp_path)]) == 0 and "log_20261115_080000.hlog" in capsys.readouterr().out
    assert logtool.main(["prune", str(tmp_path)]) == 1
    os.utime(src, (1, 1))
    assert logtool.main(["prune", str(tmp_path), "--days", "30"]) == 0
    assert "would delete" in capsys.readouterr().out and src.exists()
    assert logtool.main(["prune", str(tmp_path), "--days", "30", "--yes"]) == 0 and not src.exists()
    assert logtool.main(["info", str(tmp_path / "nope.hlog")]) == 2
