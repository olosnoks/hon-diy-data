"""Drive files written by the handheld's C engine, read by the Python parser
and imported into the SQLite database."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

import hhhost
from hondalog import addressmap
from hondalog.data import open_log
from hondalog.handheld import cli
from hondalog.handheld import format as F
from hondalog.handheld.drivefile import parse_drive_file, read_drive_file
from hondalog.handheld.importer import import_card, import_file
from hondalog.handheld.mapfile import export_map

UTC_151126_123519 = 1794746119


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


@pytest.fixture(scope="session")
def amap():
    return addressmap.load_default()


def _card(tmp_path, amap, name="sd"):
    card = tmp_path / name
    export_map(amap, card)
    return card


def _handheld(L, card, cfg="prealloc_mb = 1\n", **kw):
    return hhhost.Handheld(L, card, (card / "HANDHELD.MAP").read_bytes(), cfg=cfg, **kw)


def _drive_with_everything(L, card):
    """One drive: 40 s of idle, a GPS fix, a marker, a low-battery alarm, a
    stall with the ECU alive, key off, the 120 s gap."""
    h = _handheld(L, card)
    h.run_until(5.0)
    h.gps("GPGGA,123519,3217.100,N,09007.200,W,1,08,0.9,95.4,M,-29.0,M,,")
    h.gps("GPRMC,123519,A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W")
    for i in range(1, 30):
        h.run_until(5.0 + i)
        h.gps(f"GPRMC,1235{19 + i:02d},A,3217.{100 + i},N,09007.200,W,022.4,084.4,151126,003.1,W")
        if i == 10:
            h.mark()
    h.run_until(40.0)
    h.model.stall()
    h.run_until(60.0)
    h.model.power_cut()
    h.run_until(200.0)
    return h


def test_drive_file_reads_back_through_the_python_parser(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _drive_with_everything(L, card)
    assert h.get("log.recording") == 0
    reads_sent = sum(1 for baud, f in h.sent if len(f) == 5)
    h.close()
    df = read_drive_file(h.drive(1))
    assert df.kind == F.FK_DRIVE and df.file_no == 1 and df.boot_no == 1 and df.device == "HOSTTEST"
    assert df.map_hash == amap.version_hash and df.ecu_id == b"SIM-P2P\x00\x00\x01" and df.fw == "0.1.0"
    assert df.complete and df.end["reason"] == "link_lost" and df.bad_blocks == 0 and df.missing_blocks == 0
    assert df.used_bytes == df.size                         # preallocated, then cut to length at the end
    assert df.flags & F.HF_PREALLOC and df.prealloc == 1 << 20
    # every read the engine got an answer to is in the file, with the bytes the ECU sent
    assert reads_sent - 250 < len(df.reads) <= reads_sent
    assert df.end["n_reads"] == len(df.reads)
    assert {(r.addr, len(r.data)) for r in df.reads} == {(0, 3), (8, 10), (18, 4), (23, 3), (32, 3), (36, 3),
                                                         (39, 2), (60, 1)}
    times = [r.t_us for r in df.reads]
    assert times == sorted(times) and all(900 < r.latency_us < 20000 for r in df.reads)
    first_rpm = next(r for r in df.reads if r.addr == 0)
    assert amap.channel("rpm").convert(first_rpm.data[:2]) == pytest.approx(1500, abs=200)
    # GPS, the clock from GPS, marker, stall, link events
    assert len(df.gps) == 30 and df.gps[0].fix == 1 and df.gps[0].sats == 8 and df.gps[0].hdop == 0.9
    assert df.gps[0].lat == pytest.approx(32 + 17.1 / 60, abs=1e-6) and df.gps[0].lon == pytest.approx(-90.12, abs=1e-6)
    assert df.gps[0].speed_kmh == pytest.approx(41.5, abs=0.1) and df.gps[0].utc == UTC_151126_123519
    # file seconds count from the drive's start (the ECU's first answer), not from power-on
    off = df.t0_us / 1e6
    assert 0.3 < off < 1.0
    assert df.epoch_unix == pytest.approx(UTC_151126_123519 - 5.0 + off, abs=0.05)
    names = [e.name for e in df.events]
    assert names.count("marker") == 1 and names.count("stall") == 1 and names[0] == "drive_start"
    assert "link_up" in names and "link_down" in names
    stall = next(e for e in df.events if e.name == "stall")
    assert 40.0 < off + df.seconds(stall.t_us) < 40.5 and stall.src == 1 and 50 <= stall.value < 400     # the last rpm before it
    (cap,) = df.captures
    assert cap.complete and cap.reason == 1 and cap.t_trigger_us == stall.t_us
    assert df.seconds(cap.t_start_us) == pytest.approx(df.seconds(stall.t_us) - 30, abs=1e-3)
    assert df.seconds(cap.t_end_us) == pytest.approx(df.seconds(stall.t_us) + 10, abs=1e-3)
    assert "drive_gap_s = 120" in df.config and df.alarm_names[0] == "ect_high"
    # the capture file: the 30 s before the stall, flushed at once, then the 10 s after
    cf = read_drive_file(h.capture(1))
    assert cf.kind == F.FK_CAPTURE and cf.drive_no == 1 and cf.reason == 1 and cf.complete
    assert cf.trigger_t_us == stall.t_us
    span = [r.t_us for r in cf.reads]
    assert df.seconds(min(span)) == pytest.approx(df.seconds(stall.t_us) - 30, abs=1.2)
    assert df.seconds(max(span)) == pytest.approx(df.seconds(stall.t_us) + 10, abs=0.3)
    in_drive = {(r.t_us, r.addr, r.data) for r in df.reads}
    assert all((r.t_us, r.addr, r.data) in in_drive for r in cf.reads)      # the same reads, not others


def test_import_makes_a_drive_like_a_laptop_recording(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _drive_with_everything(L, card)
    h.close()
    off = read_drive_file(h.drive(1)).t0_us / 1e6            # drive second 0 on the handheld's clock
    epoch = UTC_151126_123519 - 5.0 + off
    db = tmp_path / "hondalog.db"
    results = import_card(card, db)
    assert [r.status for r in results] == ["imported", "skipped"], [str(r) for r in results]
    assert "window of drive D000001" in results[1].reason
    res = results[0]
    assert res.captures == 1 and res.gps == 30 and res.reads > 2000 and not res.notes

    log = open_log(res.drive)
    meta = log.meta
    assert meta["address_map_hash"] == amap.version_hash and meta["address_map_tag"] == amap.version_tag
    assert meta["source"] == "handheld" and meta["handheld"]["device"] == "HOSTTEST"
    assert meta["ecu_id"].endswith('"SIM-P2P..."'.replace('...', '...')) or "SIM-P2P" in meta["ecu_id"]
    assert meta["epoch_unix"] == pytest.approx(epoch, abs=0.05)
    # named by when the drive really started (from GPS), not by when it was imported
    start = datetime.fromtimestamp(epoch)
    assert res.drive.name == f"drive_{start:%Y_%m_%d_%H%M}"
    # streams as the recorder names them, raw bytes kept, values decoded by the map
    assert {"ecu/00+3", "ecu/08+10", "ecu/12+4", "ecu/link", "gps"} <= set(log.streams)
    rpm = log.series("rpm")
    assert len(rpm.t) > 500 and rpm.v[5] == pytest.approx(1500, abs=200)
    stream = log.streams["ecu/00+3"]
    raw = stream.raw
    assert raw.shape[1] == 3 and (raw[:, 0].astype(int) * 256 + raw[:, 1]) / 4 == pytest.approx(stream.column("rpm"))
    assert float(np.nanmax(stream.latency)) < 0.02
    after = rpm.v[(rpm.t > 41.0 - off) & (rpm.t < 59.0 - off)]
    assert len(after) > 100 and float(np.nanmax(after)) < 50            # stalled, ECU still answering
    assert log.t1 == pytest.approx(60.0 - off, abs=3.0)
    lat = log.series("gps_lat")
    assert len(lat.t) == 30 and lat.v[0] == pytest.approx(32.285, abs=1e-5) and lat.v[-1] > lat.v[0]
    assert log.series("gps_speed").v[0] == pytest.approx(41.5, abs=0.1)
    assert [text for _, text in log.markers if text.startswith("MARK")] == ["MARK 1"]
    log.close()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, end_reason, map_hash, stall_count, capture_count, ecu_id, "
                       "round(duration_s) FROM drives").fetchone()
    assert row[0] == "imported" and row[1] == "link_lost" and row[2] == amap.version_hash
    assert row[3] == 1 and row[4] == 1 and "SIM-P2P" in row[5] and 50 <= row[6] <= 70
    kind, rule, reason, t_trigger, t_start, t_end, complete, rows, wall = conn.execute(
        "SELECT kind, rule, reason, t_trigger, t_start, t_end, complete, rows, wall_unix FROM captures").fetchone()
    assert (kind, rule, reason, complete) == ("stall", "stall", "rpm", 1)
    assert 40.0 < off + t_trigger < 40.5 and t_start == pytest.approx(t_trigger - 30, abs=0.01)
    assert t_end == pytest.approx(t_trigger + 10, abs=0.01) and rows > 1500
    assert wall == pytest.approx(epoch + t_trigger, abs=0.1)
    # the map text is stored once, and it is the map on the card
    (text,) = conn.execute("SELECT text FROM address_maps").fetchone()
    assert addressmap.loads(text).version_hash == amap.version_hash
    assert conn.execute("SELECT count(*) FROM events WHERE kind='trigger'").fetchone()[0] == 1
    # the capture opens as its window of the drive
    (cap_name,) = conn.execute("SELECT name FROM captures").fetchone()
    conn.close()
    cap = open_log(f"{db}#{cap_name}")
    assert cap.t0 == pytest.approx(t_start, abs=0.5) and cap.t1 <= t_end + 0.01
    cap.close()


def test_import_is_idempotent(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _handheld(L, card)
    h.run_until(20.0)
    h.close()
    h = _handheld(L, card, boot_no=2, last_drive_no=1)      # the next key-on: another drive
    h.run_until(15.0)
    h.close()
    db = tmp_path / "hondalog.db"
    first = import_card(card, db)
    assert [r.status for r in first] == ["imported", "imported"]
    again = import_card(card, db)
    assert [r.status for r in again] == ["skipped", "skipped"]
    assert again[0].reason == f"already imported as {first[0].drive.name}"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM drives").fetchone()[0] == 2
    n_rows = conn.execute("SELECT count(*) FROM samples").fetchone()[0]
    conn.close()
    # --force replaces, it does not add
    forced = import_file(h.drive(2), db, force=True)
    assert forced.status == "imported"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM drives").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM samples").fetchone()[0] == n_rows
    conn.close()
    # the CLI: same thing, exit code 0, and it says what it skipped
    assert cli.main(["import", str(card), "--db", str(db)]) == 0


def test_power_cut_file_with_stale_space_imports_cleanly(L, tmp_path, amap, capsys):
    """Key-off kills the power mid-write. The file's preallocated megabyte
    still holds an older drive. Only this drive's blocks may come out."""
    old_card = _card(tmp_path, amap, "old")
    h = _handheld(L, old_card, cfg="prealloc_mb = 0\n", boot_no=1)
    h.run_until(90.0)
    h.close()
    old = read_drive_file(h.drive(1))
    assert len(old.reads) > 4000

    card = _card(tmp_path, amap, "sd")
    h = _handheld(L, card, boot_no=9)
    h.stale(old_card / "DRIVES" / "D000001.HHD")
    h.run_until(10.0)
    h.mark()
    h.run_until(20.0)
    h.power_cut_after(3333)                                 # dies part-way through a block
    h.run_until(40.0)
    h.close()
    df = read_drive_file(h.drive(1))
    assert df.size == 1 << 20 and not df.complete and df.end is None
    assert df.boot_no == 9 and df.bad_blocks == 0 and df.missing_blocks == 0
    # 20 s, plus the 3333 bytes that still got out (about 4 s); none of the 4000 stale reads
    assert 1000 < len(df.reads) < 1700
    assert 20.0 < df.seconds(df.t_last_us) < 26.0
    assert max(r.t_us for r in df.reads) < 26_000_000 < max(r.t_us for r in old.reads)

    db = tmp_path / "hondalog.db"
    assert cli.main(["import", str(card), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "no end marker (power was cut)" in out and "no GPS time" in out
    conn = sqlite3.connect(db)
    status, reason, duration = conn.execute("SELECT status, end_reason, duration_s FROM drives").fetchone()
    assert status == "imported" and reason == "power_off" and 19.0 < duration < 26.0
    assert conn.execute("SELECT count(*) FROM events WHERE kind='marker'").fetchone()[0] == 1
    conn.close()


def test_torn_and_damaged_files(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _handheld(L, card, cfg="prealloc_mb = 0\n")
    h.run_until(30.0)
    h.close()
    data = h.drive(1).read_bytes()
    whole = parse_drive_file(data)
    assert whole.complete
    # cut anywhere: a clean prefix, never an exception, never a read that was not written
    keys = [(r.t_us, r.addr, r.data) for r in whole.reads]
    for cut in (128, 129, 200, 1000, 5000, len(data) // 2, len(data) - 30, len(data) - 1):
        part = parse_drive_file(data[:cut])
        assert not part.complete and part.bad_blocks == 0
        assert [(r.t_us, r.addr, r.data) for r in part.reads] == keys[:len(part.reads)]
    # a damaged stretch in the middle costs that block only
    broken = bytearray(data)
    broken[len(data) // 2] ^= 0xFF
    part = parse_drive_file(bytes(broken))
    assert part.complete and part.bad_blocks == 1 and part.missing_blocks == 1
    assert 0 < len(whole.reads) - len(part.reads) < 80
    # a damaged header is refused with a reason
    broken = bytearray(data)
    broken[20] ^= 1
    with pytest.raises(ValueError, match="header"):
        parse_drive_file(bytes(broken))
    with pytest.raises(ValueError):
        parse_drive_file(b"not a drive file" * 20)
    bad = card / "DRIVES" / "D000009.HHD"
    bad.write_bytes(b"\0" * 4096)
    results = import_card(card, tmp_path / "x.db")
    assert [r.status for r in results] == ["imported", "failed"]


def test_link_loss_capture_and_drive_without_its_file(L, tmp_path, amap):
    """A capture file whose drive file is gone is imported as its own drive."""
    card = _card(tmp_path, amap)
    h = _handheld(L, card)
    h.run_until(45.0)
    h.model.power_cut()                                     # main relay: ECU gone at speed
    h.run_until(60.0)
    h.model.restart()
    h.run_until(70.0)
    h.close()
    df = read_drive_file(h.drive(1))
    (cap,) = df.captures
    assert cap.reason == 2 and 45.9 < df.seconds(cap.t_trigger_us) < 46.6
    h.drive(1).unlink()
    db = tmp_path / "hondalog.db"
    (res,) = import_card(card, db)
    assert res.status == "imported" and res.captures == 1
    log = open_log(res.drive)
    assert log.meta["handheld"]["kind"] == "capture"
    rpm = log.series("rpm")
    assert 28.0 < rpm.t[-1] - rpm.t[0] < 43.0 and len(rpm.t) > 500
    log.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT kind, reason FROM captures").fetchone() == ("stall", "link")
    conn.close()


def test_iso_only_drive_imports_as_obd2_streams(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _handheld(L, card, cfg="iso_after_s = 4\nprealloc_mb = 0\n", honda_protocol=False, dtcs=("P0301",))
    h.run_until(40.0)
    h.close()
    df = read_drive_file(h.drive(1))
    assert df.link_mode == 1 and not df.reads and len(df.iso) > 100
    db = tmp_path / "hondalog.db"
    (res,) = import_card(card, db)
    assert res.status == "imported", str(res)
    log = open_log(res.drive)
    assert "obd2/0C" in log.streams and log.meta["port"].endswith("ISO 9141-2")
    assert log.series("rpm").v[-1] == pytest.approx(h.model.rpm, rel=0.05)
    log.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT text FROM events WHERE kind='dtc'").fetchone() == ("P0301",)
    conn.close()


def test_alarms_reach_the_alarms_table(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _handheld(L, card, cfg="prealloc_mb = 0\n")
    h.run_until(5.0)
    low = int(11.0 * 10.45)                                 # 114 -> 10.9 V
    step = h.model.step
    h.model.step = lambda: (step(), h.model.ram.__setitem__(0x17, low))[0]     # 11.0 V, engine running
    h.run_until(15.0)
    assert h.get("top_alarm_level") == 1 and h.text("top_alarm_text") == "Battery low"
    h.ack()
    h.close()
    db = tmp_path / "hondalog.db"
    (res,) = import_card(card, db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT name, state, severity, channel, round(value, 1), text FROM alarms ORDER BY t").fetchall()
    conn.close()
    assert rows[0][:5] == ("batt_low", "raised", "warn", "batt", 10.9)
    assert rows[0][5].startswith("ALARM batt_low: Battery 10.9")
    assert ("batt_low", "acknowledged") in [r[:2] for r in rows]


def test_a_drive_recorded_with_another_map_needs_that_map(L, tmp_path, amap):
    card = _card(tmp_path, amap)
    h = _handheld(L, card, cfg="prealloc_mb = 0\n")
    h.run_until(10.0)
    h.close()
    # the card now carries a newer map (an address was corrected after the drive)
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8").replace('formula = "x / 10.45"', 'formula = "x / 10.5"')
    newer_path = tmp_path / "newer.toml"
    newer_path.write_text(text, encoding="utf-8")
    newer = addressmap.load(newer_path)
    export_map(newer, card)
    db = tmp_path / "hondalog.db"
    # the default map is still the recorded one: found by hash, not taken from the card
    (res,) = import_card(card, db)
    assert res.status == "imported"
    log = open_log(res.drive)
    assert log.meta["address_map_hash"] == amap.version_hash != newer.version_hash
    log.close()
    # a file whose map is nowhere to be found is refused, with its hash
    data = bytearray(h.drive(1).read_bytes())
    data[40:52] = b"0123456789ab"
    data[124:128] = F.crc32(bytes(data[:124])).to_bytes(4, "little")
    orphan = tmp_path / "D000077.HHD"
    orphan.write_bytes(bytes(data))
    res = import_file(orphan, tmp_path / "other.db")
    assert res.status == "failed" and "#0123456789ab" in res.reason
    # unless told to decode with a given map anyway; the drive then says so
    res = import_file(orphan, tmp_path / "other.db", amap=newer, allow_other_map=True)
    assert res.status == "imported" and any("recorded with #0123456789ab" in n for n in res.notes)


def test_info_cli(L, tmp_path, amap, capsys):
    card = _card(tmp_path, amap)
    h = _handheld(L, card)
    h.run_until(8.0)
    h.close()
    assert cli.main(["info", str(h.drive(1))]) == 0
    out = capsys.readouterr().out
    assert "drive 1, boot 1" in out and f"map #{amap.version_hash}" in out and "ended: stopped" in out
    assert cli.main(["info", str(tmp_path / "nope.HHD")]) == 1
    assert datetime.now(timezone.utc).year >= 2026
