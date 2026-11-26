"""HANDHELD.MAP: exporting the address map for the handheld, and the CLI."""

import hashlib
import math
from pathlib import Path

import pytest

import hhhost
from hondalog import addressmap
from hondalog.handheld import cli
from hondalog.handheld import format as F
from hondalog.handheld.mapfile import CFG_NAME, MAP_NAME, ExportReport, build_map, c_array, export_map, read_map

REPO = Path(__file__).resolve().parent.parent
BUILTIN = REPO / "firmware" / "handheld" / "components" / "hhcore" / "port" / "hh_default_map.inc"


@pytest.fixture(scope="module")
def amap():
    return addressmap.load_default()


def test_map_file_carries_the_map_its_hash_and_its_text(amap):
    report = ExportReport()
    data = build_map(amap, report=report)
    mf = read_map(data)
    assert mf.map_hash == amap.version_hash == report.map_hash
    assert mf.version == amap.version and mf.variant == amap.variant
    text = Path(amap.path).read_text(encoding="utf-8")
    assert mf.source == text and mf.map_sha256 == hashlib.sha256(text.encode()).digest()
    # the map inside decodes to the same hash: a drive recorded with this file
    # gets the address_map_hash a laptop recording with this map gets
    assert mf.address_map().version_hash == amap.version_hash
    assert report.core_bytes == len(mf.core) < F.MAP_MAX_CORE and report.total_bytes == len(data)
    assert mf.ecu_id_addr == 0x76 and mf.ecu_id_len == 10
    assert mf.protocol["baud"] == 9600 and mf.protocol["init_sequence"] == amap.framing.init_sequence
    assert mf.protocol["rate_fast_hz"] == 20.0 and mf.protocol["max_read_len"] == 16


def test_channels_limits_and_what_is_left_out(amap):
    report = ExportReport()
    mf = read_map(build_map(amap, report=report))
    by_name = {c["name"]: c for c in mf.channels}
    assert [c["name"] for c in mf.channels[:len(amap.channels)]] == amap.names     # measured first, map order
    assert report.math == ["inj_duty", "fuel_flow", "total_trim", "load", "afr_est"]
    assert {line.split(":")[0] for line in report.skipped} == {
        "math rpm_rate", "math rpm_smooth", "math map_avg", "math batt_min_5s"}     # they need history
    rpm, ect, batt, vtec = by_name["rpm"], by_name["ect"], by_name["batt"], by_name["vtec"]
    assert (rpm["addr"], rpm["length"], rpm["rate_class"], rpm["kind"]) == (0, 2, 0, F.KIND_VALUE)
    assert (rpm["lo"], rpm["hi"], rpm["units"], rpm["label"]) == (0, 7200, "rpm", "RPM")
    assert rpm["warn_hi"] == 6800 and math.isnan(rpm["alarm_hi"]) and math.isnan(rpm["warn_lo"])
    assert ect["alarm_hi"] == 105 and ect["units"] == "°C" and ect["rate_class"] == 2
    assert batt["warn_lo"] == 12.0 and batt["warn_hi"] == pytest.approx(15.2) and batt["decimals"] == 1
    assert vtec["kind"] == F.KIND_FLAG and (vtec["lo"], vtec["hi"]) == (0, 1)
    assert by_name["inj_duty"]["kind"] == F.KIND_MATH and by_name["inj_duty"]["rate_class"] == 0
    assert by_name["ign"]["units"] == "° BTDC"          # 7 bytes of UTF-8 in char[8]
    assert not any(c["verified"] for c in mf.channels)   # nothing in the default map is verified yet
    assert report.alarms == [r.name for r in amap.alarms]
    alarms = {a["name"]: a for a in mf.alarms}
    hot = alarms["ect_high"]
    assert hot["channel"] == amap.names.index("ect") and hot["severity"] == 2 and hot["hold_s"] == 2
    assert hot["flags"] & F.AF_LATCHING and hot["flags"] & F.AF_HIGH and not hot["flags"] & F.AF_LOW
    assert alarms["batt_low"]["gate"][1] > 0 and alarms["lean_under_load"]["channel"] == -1
    assert alarms["idle_collapse"]["flags"] & F.AF_RATE_LOW and alarms["idle_collapse"]["rate_low"] == -400
    assert mf.stall == {"rpm_run": 400, "rpm_zero": 50, "link_down_s": 1, "pre_s": 30, "post_s": 10, "rpm_channel": 0}


def test_the_protocol_section_reaches_the_handheld(tmp_path):
    """A stock P2P: Honda reads at 10400 baud after the wake-up bytes. The
    handheld runs the line at the map's baud, so it has to be in the file."""
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8").replace(
        "[protocol]\n", '[protocol]\nbaud = 10400\ninit_sequence = "68 6a f5 af bf b3 b2 c1 db b3 e9"\n'
        "init_delay_s = 0.35\n", 1)
    path = tmp_path / "p2p.toml"
    path.write_text(text, encoding="utf-8")
    mine = addressmap.load(path)
    assert mine.framing.baud == 10400
    mf = read_map(build_map(mine))
    assert mf.protocol["baud"] == 10400
    assert mf.protocol["init_sequence"] == bytes.fromhex("686af5afbfb3b2c1dbb3e9")
    assert math.isclose(mf.protocol["init_delay_s"], 0.35, rel_tol=1e-6)
    assert mf.address_map().framing.baud == 10400


def test_a_verified_channel_and_a_variant_reach_the_handheld(tmp_path):
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8").replace(
        'name = "rpm"\nlabel = "RPM"', 'name = "rpm"\nstatus = "verified"\nlabel = "RPM"', 1)
    text = text.replace('status = "unverified"\nrate = "fast"\nsource = "kerpz hobd_uni2 (P2T)"\n\n'
                        '[channel.variants.P28]', 'rate = "fast"\n\n[channel.variants.P28]', 1)
    path = tmp_path / "mine.toml"
    path.write_text(text, encoding="utf-8")
    mine = addressmap.load(path)
    mf = read_map(build_map(mine))
    assert mf.channels[0]["verified"] and not mf.channels[1]["verified"]
    other = addressmap.load(path, variant="P2T")
    mf2 = read_map(build_map(other))
    assert mf2.variant == "P2T" and mf2.map_hash == other.version_hash != mine.version_hash
    assert mf2.address_map().version_hash == other.version_hash      # the variant is kept with the text


def test_a_formula_the_handheld_cannot_run_stops_the_export(tmp_path):
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8")
    path = tmp_path / "bad.toml"
    path.write_text(text.replace('formula = "x / 51.3"', 'formula = "b[x]"', 1), encoding="utf-8")
    try:
        amap = addressmap.load(path)
    except addressmap.MapError:
        return                                  # the laptop refuses it first: fine too
    with pytest.raises(addressmap.MapError, match="o2"):
        build_map(amap)


def test_damaged_map_files_are_refused(amap):
    data = build_map(amap)
    with pytest.raises(ValueError):
        read_map(data[:40])
    with pytest.raises(ValueError):
        read_map(b"\0" * 200)
    broken = bytearray(data)
    broken[300] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        read_map(bytes(broken))
    # a damaged text part costs the text, not the map
    core_len = len(read_map(data).core)
    broken = bytearray(data)
    broken[core_len + 50] ^= 1
    assert read_map(bytes(broken)).source == "" and read_map(bytes(broken)).map_hash == amap.version_hash


def test_the_c_loader_takes_it_and_refuses_damage(amap, tmp_path_factory):
    L = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if L is None:
        pytest.skip("no C compiler")
    data = build_map(amap)
    assert L.hhhost_map_check(data, len(data)) == 0
    core_len = len(read_map(data).core)
    assert L.hhhost_map_check(data[:core_len], core_len) == 0          # the text is optional
    for at in range(0, core_len, 11):
        broken = bytearray(data)
        broken[at] ^= 0x20
        assert L.hhhost_map_check(bytes(broken), len(broken)) != 0, at


def test_the_firmware_builtin_map_is_current(amap):
    """components/hhcore/port/hh_default_map.inc is generated; regenerate with
    hondalog-handheld export-map --map hondalog/data/addressmap.toml --c-array <that file>"""
    assert BUILTIN.read_text(encoding="utf-8") == c_array(amap)


def test_export_map_cli_writes_the_card(tmp_path, capsys):
    card = tmp_path / "sd"
    assert cli.main(["export-map", str(card), "--map", str(addressmap.DEFAULT_MAP), "--pre", "20"]) == 0
    out = capsys.readouterr().out
    assert "22 channels, 5 math channels, 6 alarms" in out and "left out: math rpm_rate" in out
    assert "not verified" in out
    mf = read_map(card / MAP_NAME)
    assert mf.stall["pre_s"] == 20 and mf.map_hash == addressmap.load_default().version_hash
    assert (card / "DRIVES").is_dir() and (card / "CAPTURES").is_dir()
    cfg = card / CFG_NAME
    assert "drive_gap_s = 120" in cfg.read_text()
    cfg.write_text("drive_gap_s = 60\n")
    assert cli.main(["export-map", str(card), "--map", str(addressmap.DEFAULT_MAP)]) == 0
    assert cfg.read_text() == "drive_gap_s = 60\n"     # your settings are not overwritten
    assert not (card / "HANDHELD.TMP").exists()
    assert cli.main(["info", str(card / MAP_NAME)]) == 0
    assert "rpm" in capsys.readouterr().out


def test_default_cfg_is_what_the_firmware_defaults_to(tmp_path_factory):
    """HANDHELD.CFG as written by export-map parses on the handheld with no
    bad line and changes nothing."""
    import ctypes as C
    from hondalog.handheld.mapfile import DEFAULT_CFG
    L = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if L is None:
        pytest.skip("no C compiler")
    default, parsed = C.create_string_buffer(128), C.create_string_buffer(128)
    L.hh_config_default(default)
    L.hh_config_default(parsed)
    L.hh_config_parse.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
    text = DEFAULT_CFG.encode()
    assert L.hh_config_parse(parsed, text, len(text)) == 0
    assert default.raw == parsed.raw
