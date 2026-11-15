"""Exports and importers: MLG checked byte by byte against the spec, MSL, CSV, NPZ."""

import struct

import numpy as np
import pytest

from hondalog.analysis import Filter, FilterSet
from hondalog.data import Column, new_drive, open_log, write_log
from hondalog.data.model import Table
from hondalog.export import FORMATS, export_capture, export_log, export_table, mlg, textlog

OLD_CSV = """# hondalog 0.1.0
# started: 2026-11-10T07:58:01
# ecu: P2P expected, not confirmed
# ecu_id: 53 49 4D 2D 50 32 50 00 00 01  "SIM-P2P..."
# address_map_version: p2p-guess-1 (kerpz P2T map)
# units: rpm=rpm ect=°C batt=V vtec=-
time_s,wall_time,link,rpm,ect,batt,vtec,marker
0.103,2026-11-10T07:58:01.201,1,752,81.5,13.9,0,
0.204,2026-11-10T07:58:01.302,1,760,81.5,13.9,0,MARK 1
0.305,2026-11-10T07:58:01.403,0,,,,,
0.406,2026-11-10T07:58:01.504,1,0,81.6,12.1,0,it stumbled | STALL (rpm 0)
"""


@pytest.fixture()
def small(tmp_path):
    with new_drive(tmp_path / "small.db", {"ecu": "P2P", "ecu_id": "SIM", "started_unix": 1794729600.0,
                                            "address_map_version": "v7", "address_map_text": "x" * 5000}) as w:
        w.add_stream("ecu", [Column("link", "u1", kind="flag", decimals=0),
                             Column("rpm", units="rpm", decimals=0),
                             Column("ect", units="°C", decimals=1),
                             Column("ign", units="° BTDC", decimals=1)], source="ecu")
        for i in range(400):
            down = 200 <= i < 210
            w.append("ecu", i * 0.1, [0 if down else 1, None if down else 800 + i,
                                      None if down else 80 + i * 0.01, None if down else -3.5 + i * 0.05])
        w.event(5.03, "marker", "MARK 1")
        w.event(30.0, "trigger", "STALL (rpm 0)")
        w.event(31.0, "note", "not a marker")
    with open_log(w.ref) as log:
        yield log


def test_mlg_v2_layout_matches_the_spec(tmp_path, small):
    """Independent decode with struct, field by field, at the offsets the
    EFI Analytics MLG 2.0 document gives."""
    blob = export_log(small, tmp_path / "a.mlg").read_bytes()
    assert blob[0:6] == b"MLVLG\x00"                                   # file format
    version, stamp, info_start, data_begin, rec_len, nfields = struct.unpack_from(">HIIIHH", blob, 6)
    assert (version, stamp, nfields) == (2, 1794729600, 5)             # Time + 4 channels
    assert info_start == 24 + 89 * nfields and blob[data_begin - 1] == 0
    assert blob[info_start:data_begin - 1].decode().startswith(
        "ecu=P2P; ecu_id=SIM; address_map_version=v7; started=2026-11-1")

    fields = []
    for i in range(nfields):
        base = 24 + 89 * i
        ftype = blob[base]
        name = blob[base + 1:base + 35].split(b"\0")[0].decode()
        units = blob[base + 35:base + 45].split(b"\0")[0].decode()
        style = blob[base + 45]
        scale, transform = struct.unpack_from(">ff", blob, base + 46)
        digits = struct.unpack_from(">b", blob, base + 54)[0]
        category = blob[base + 55:base + 89].split(b"\0")[0].decode()
        fields.append((ftype, name, units, style, scale, transform, digits, category))
    assert fields[0] == (7, "Time", "s", 0, 1.0, 0.0, 3, "")
    assert fields[1] == (0, "link", "", 4, 1.0, 0.0, 0, "")           # a clean flag: U08, On/Off
    assert fields[2] == (7, "rpm", "rpm", 0, 1.0, 0.0, 0, "")
    assert fields[3][:3] == (7, "ect", "degC") and fields[4][2] == "deg BTDC"
    assert rec_len == 4 + 1 + 4 * 3

    pos, records, markers, counters = data_begin, [], [], []
    while pos < len(blob):
        kind, counter, stamp16 = struct.unpack_from(">BBH", blob, pos)
        counters.append(counter)
        if kind == 0:
            body = blob[pos + 4:pos + 4 + rec_len]
            assert blob[pos + 4 + rec_len] == sum(body) & 0xFF         # CRC: sum of the record bytes
            records.append((stamp16, struct.unpack(">fBfff", body)))
            pos += 4 + rec_len + 1
        else:
            assert kind == 1
            markers.append((len(records), blob[pos + 4:pos + 54].split(b"\0")[0].decode()))
            pos += 54
    assert pos == len(blob) and len(records) == 400
    assert counters == [i % 256 for i in range(402)]                   # one rolling counter for all blocks
    assert markers == [(51, "MARK 1"), (300, "STALL (rpm 0)")]         # before the first record at/after it
    assert [r[0] for r in records[:4]] == [0, 10000, 20000, 30000]     # 10 us units
    assert records[70][0] == 700000 % 65536                            # and it wraps
    assert records[399][1][0] == pytest.approx(39.9) and records[399][1][2] == 1199
    assert records[205][1][2] == 999                                   # link gap holds the last value


def test_mlg_v1_layout(tmp_path, small):
    blob = export_log(small, tmp_path / "a.mlg", mlg_version=1).read_bytes()
    version, stamp, info_start, data_begin, rec_len, nfields = struct.unpack_from(">HIHIHH", blob, 6)
    assert (version, nfields, rec_len) == (1, 5, 17)
    assert info_start == 22 + 55 * 5 and data_begin > info_start       # 22-byte header, 55-byte fields
    assert blob[22 + 55 + 1:22 + 55 + 5] == b"link"
    parsed = mlg.parse_mlg(blob)
    assert parsed.version == 1 and parsed.crc_errors == 0 and len(parsed.t) == 400
    assert parsed.fields[2].category == "" and parsed.info.startswith("ecu=P2P")


@pytest.mark.parametrize("version", [1, 2])
def test_mlg_round_trip(tmp_path, small, version):
    path = export_log(small, tmp_path / "a.mlg", mlg_version=version)
    back = open_log(path)
    assert back.channels == ["link", "rpm", "ect", "ign"] and back.rows == 400
    np.testing.assert_allclose(back.series("rpm").t, np.arange(400) * 0.1, atol=1e-5)
    held = np.where((np.arange(400) >= 200) & (np.arange(400) < 210), 999, 800 + np.arange(400))
    np.testing.assert_array_equal(back.series("rpm").v, held)
    assert back.units("ect") == "degC" and back.meta["started_unix"] == 1794729600
    assert [text for _, text in back.markers] == ["MARK 1", "STALL (rpm 0)"]
    assert back.markers[0][0] == pytest.approx(5.1, abs=1e-5)


def test_mlg_compact_fields(tmp_path):
    t = np.arange(1000) * 0.1
    table = Table(t, {"rpm": 800.0 + np.arange(1000), "ect": 80 + np.arange(1000) * 0.01,
                      "vtec": (np.arange(1000) % 2).astype(float), "big": np.arange(1000) * 1000.0,
                      "gap": np.where(np.arange(1000) == 5, np.nan, 1.0)},
                  {"rpm": "rpm"})
    cols = {"rpm": Column("rpm", decimals=0), "ect": Column("ect", decimals=1),
            "vtec": Column("vtec", kind="flag"), "big": Column("big", decimals=0), "gap": Column("gap")}
    plain = mlg.write_mlg(tmp_path / "p.mlg", table, columns=cols)
    small_ = mlg.write_mlg(tmp_path / "c.mlg", table, columns=cols, compact=True, fill="keep")
    parsed = mlg.parse_mlg(small_.read_bytes())
    kinds = {f.name: (f.type, f.scale, f.style) for f in parsed.fields}
    assert kinds["rpm"][0] == mlg.S16 and kinds["ect"][:2] == (mlg.S16, pytest.approx(0.1))
    assert kinds["vtec"] == (mlg.U08, 1.0, mlg.STYLE_ON_OFF)
    assert kinds["big"][0] == mlg.F32 and kinds["gap"][0] == mlg.F32          # do not fit / have NaN
    np.testing.assert_array_equal(parsed.data["rpm"], table["rpm"])
    np.testing.assert_allclose(parsed.data["ect"], table["ect"], atol=0.05 + 1e-6)
    assert np.isnan(parsed.data["gap"][5]) and parsed.crc_errors == 0
    assert small_.stat().st_size < plain.stat().st_size
    assert parsed.record_length == 4 + 2 + 2 + 1 + 4 + 4


def test_mlg_reader_edge_cases(tmp_path):
    with pytest.raises(mlg.MlgError):
        mlg.parse_mlg(b"not an mlg file at all......")
    with pytest.raises(mlg.MlgError):
        mlg.parse_mlg(b"MLVLG\x00\x00\x09" + bytes(40))
    with pytest.raises(mlg.MlgError):
        mlg.write_mlg(tmp_path / "x.mlg", Table(np.zeros(0), {}), version=3)
    table = Table(np.array([0.0, 0.5, 1.0, 1.5]), {"a": np.array([1.0, 2, 3, 4])})
    path = mlg.write_mlg(tmp_path / "a.mlg", table, [(9.0, "late"), (0.0, "x" * 80)])
    blob = path.read_bytes()
    parsed = mlg.parse_mlg(blob)
    assert parsed.markers == [(0.0, "x" * 49), (1.5, "late")]                 # 50 bytes with the NUL
    cut = mlg.parse_mlg(blob[:-3])                                            # torn last block
    assert len(cut.t) == 4 and len(cut.markers) == 1
    # a file without a Time field: time comes from the wrapping 10 us stamps
    head = mlg.HEADER_V2.pack(mlg.MAGIC, 2, 0, 0, 24 + 89, 1, 1)
    field = mlg.FIELD_V2.pack(0, b"a", b"", 0, 2.0, 1.0, 0, b"")
    blocks = b"".join(struct.pack(">BBHBB", 0, i, (i * 30000) % 65536, i, i) for i in range(6))
    bare = mlg.parse_mlg(head + field + blocks)
    np.testing.assert_allclose(bare.t, np.arange(6) * 0.3)                    # unwrapped
    assert bare.data["a"].tolist() == [2, 4, 6, 8, 10, 12]                    # (raw + transform) * scale
    empty = mlg.write_mlg(tmp_path / "e.mlg", Table(np.zeros(0), {"a": np.zeros(0)}), [(1.0, "m")])
    assert mlg.parse_mlg(empty.read_bytes()).markers == [(0.0, "m")]
    bits = mlg.FIELD_V2.pack(10, b"flags", b"", 2, 0, 0, 0, b"")
    assert mlg.parse_mlg(mlg.HEADER_V2.pack(mlg.MAGIC, 2, 0, 0, 24 + 89, 1, 1) + bits + blocks).data["flags"][2] == 2
    assert mlg.ascii_text("λ = 1.0 °", 10) == b"lambda = \x00"


def test_msl_export_and_import(tmp_path, small):
    path = export_log(small, tmp_path / "a.msl")
    lines = path.read_text().splitlines()
    assert lines[0].startswith('"hondalog') and lines[1].startswith('"Capture Date: ')
    assert lines[2] == "Time\tlink\trpm\tect\tign" and lines[3] == "s\t\trpm\tdegC\tdeg BTDC"
    assert lines[4].split("\t")[:3] == ["0.000", "1", "800"]
    assert "MARK 001 - MARK 1" in lines and "MARK 002 - STALL (rpm 0)" in lines
    assert all(cell for line in lines[4:] if not line.startswith("MARK") for cell in line.split("\t"))
    back = open_log(path)
    assert back.channels == ["link", "rpm", "ect", "ign"] and back.rows == 400
    assert back.units("ign") == "deg BTDC" and "hondalog" in back.meta["title"]
    assert [(round(t, 1), text) for t, text in back.markers] == [(5.1, "MARK 1"), (30.0, "STALL (rpm 0)")]
    assert back.series("rpm").v[205] == 999 and back.series("link").v[205] == 0


def test_csv_export_and_import(tmp_path, small):
    path = export_log(small, tmp_path / "a.csv")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# hondalog ") and "# units: link=- rpm=rpm ect=°C ign=° BTDC" in text
    assert "address_map_text" not in text and "time_s,link,rpm,ect,ign,marker" in text
    assert "20.5000,0,,,," in text                                            # gaps stay empty
    back = open_log(path)
    assert back.meta["address_map_version"] == "v7" and back.units("ect") == "°C"
    assert np.isnan(back.series("rpm").v[205]) and back.series("rpm").v[399] == 1199
    assert back.column("link").kind == "flag"
    assert [e.kind for e in back.events] == ["marker", "trigger"]


def test_import_old_csv_and_migrate(tmp_path):
    src = tmp_path / "log_20261110_075801.csv"
    src.write_text(OLD_CSV, encoding="utf-8")
    log = open_log(src)
    assert log.channels == ["link", "rpm", "ect", "batt", "vtec"]              # wall_time dropped
    assert log.meta["address_map_version"].startswith("p2p-guess-1") and "SIM-P2P" in log.meta["ecu_id"]
    assert log.units("ect") == "°C" and log.units("vtec") == ""
    rpm = log.series("rpm")
    assert rpm.t.tolist() == [0.103, 0.204, 0.305, 0.406] and rpm.v[0] == 752 and np.isnan(rpm.v[2])
    assert log.series("link").v.tolist() == [1, 1, 0, 1]
    assert [(e.t, e.kind, e.text) for e in log.events] == [
        (0.204, "marker", "MARK 1"), (0.406, "marker", "it stumbled"), (0.406, "trigger", "STALL (rpm 0)")]
    native = open_log(write_log(log, tmp_path / "hondalog.db", {"imported_from": str(src)}))
    assert native.status == "imported" and native.rows == 4 and native.meta["imported_from"] == str(src)
    assert native.name == "drive_2026_11_10_0758" and native.meta["started"] == "2026-11-10T07:58:01"
    assert native.markers == log.markers and native.series("batt").v[3] == pytest.approx(12.1)


def test_generic_text_logs():
    semi = textlog.parse_text_log("Time;AFR;Note\n0;14.7;ok\n1;15,1;lean\n2;13.9;\n")
    assert semi.channels == ["AFR"] and np.isnan(semi.series("AFR").v[1])     # bad cell, text column
    no_time = textlog.parse_text_log("a,b\n1,2\n3,4\n")
    assert no_time.series("a").t.tolist() == [0, 1] and "row number" in no_time.meta["time_axis"]
    odd = textlog.parse_text_log("time,t,rpm,rpm\n2,9,3,4\n1,8,1,2\n\nMARK 7\n")
    assert odd.channels == ["t_", "rpm", "rpm_2"] and odd.series("rpm").t.tolist() == [1, 2]   # sorted
    assert odd.markers == [(2.0, "MARK 7")] or odd.markers == [(1.0, "MARK 7")]
    with pytest.raises(ValueError):
        textlog.parse_text_log("# only a comment\n")
    head_only = textlog.parse_text_log("time_s,rpm,marker\n")
    assert head_only.rows == 0 and head_only.channels == ["rpm"]


def test_export_options(tmp_path, synth):
    fs = FilterSet([Filter("warm", "ect > 80")])
    table, markers = export_table(synth, ["rpm", "map"], filters=fs, t0=400, t1=500)
    assert table.names == ["rpm", "map"] and table.t[0] >= 400 and table.t[-1] <= 500
    assert [m[1] for m in markers] == ["MARK 9", "MARK 10"]
    everything, _ = export_table(synth, ["rpm"], t0=400, t1=500)
    assert len(table) == len(everything)            # warm by then: the filter keeps all rows
    cold, _ = export_table(synth, ["rpm"], filters=fs, t1=100)
    assert len(cold) == 0
    slow = open_log(export_log(synth, tmp_path / "s.csv", channels=["rpm", "ect"], hz=2))
    assert slow.channels == ["rpm", "ect"] and slow.series("rpm").rate_hz == pytest.approx(2)
    cut = open_log(export_log(synth, tmp_path / "cut.db", t0=10, t1=20))
    assert cut.t0 >= 10 and cut.t1 <= 20 and set(cut.streams) == set(synth.streams)
    assert cut.streams["blackbox/rails"].rows == pytest.approx(500, abs=2) and ".db" in FORMATS
    npz = np.load(export_log(synth, tmp_path / "a.npz", channels=["rpm", "tps"]))
    assert npz["names"].tolist() == ["rpm", "tps"] and len(npz["t"]) == len(npz["ch_rpm"])
    assert npz["marker_text"][0] == "MARK 1" and npz["units"].tolist() == ["rpm", "%"]
    with pytest.raises(ValueError):
        export_log(synth, tmp_path / "a.parquet")
    assert export_log(synth, tmp_path / "noext", fmt="mlg", channels=["rpm"]).read_bytes()[:5] == b"MLVLG"
    assert ".parquet" not in FORMATS


def test_one_hour_mlg_size_is_in_budget(tmp_path):
    """The feature list wants an hour of MLG at about 1.5-2 MB (10 Hz, 25 channels)."""
    t = np.arange(36000) * 0.1
    rng = np.random.default_rng(0)
    table = Table(t, {f"ch{i}": np.round(rng.uniform(0, 250, len(t)), 1) for i in range(25)})
    cols = {n: Column(n, decimals=1) for n in table.names}
    size = mlg.write_mlg(tmp_path / "h.mlg", table, columns=cols, compact=True).stat().st_size
    assert 1.5e6 < size < 2.3e6


def test_export_capture_to_a_file(tmp_path):
    """A capture is a row in the database; this is how it becomes a file again."""
    with new_drive(tmp_path / "hondalog.db", {"ecu_id": "SIM"}) as w:
        w.add_stream("ecu/00+2", [Column("rpm", units="rpm")], source="ecu", raw_len=2)
        w.add_stream("blackbox/live", [Column("bb_ecub", units="V")], source="blackbox")
        for i in range(600):
            w.append("ecu/00+2", i * 0.1, [3000.0 if i < 400 else 0.0], b"\x00\x00")
            w.append("blackbox/live", i * 0.1, [13.8])
        w.event(38.0, "marker", "MARK 1")
        ref, rows = w.capture("stall", 40.0, 10.0, 50.0, reason="rpm", message="rpm dropped to 0",
                              meta={"stall_time_s": "40.000"})
    back = open_log(export_capture(ref, tmp_path / "stall.mlg"))
    assert back.channels == ["rpm", "bb_ecub"] and back.duration == pytest.approx(40) and len(back.markers) == 1
    text = open_log(export_capture(str(ref), tmp_path / "stall.csv", channels=["rpm"]))
    assert text.t0 >= 10 and text.t1 <= 50 and text.series("rpm").v[-1] == 0 and text.markers == [(38.0, "MARK 1")]
    alone = open_log(export_capture(ref, tmp_path / "stall.db"))          # a database with just that window
    assert alone.rows == rows == 802 and alone.meta["stall_time_s"] == "40.000" and alone.duration == pytest.approx(40)
    assert alone.streams["ecu/00+2"].raw.shape == (401, 2) and alone.meta["exported_from"] == str(ref)
