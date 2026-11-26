"""Editing map files in place: comments kept, evidence written, versions, diff."""

import shutil
import tomllib

import numpy as np
import pytest

from hondalog import addressmap as am
from hondalog.mapping import mapedit as me


@pytest.fixture
def mapfile(tmp_path):
    path = tmp_path / "addressmap.toml"
    shutil.copyfile(am.DEFAULT_MAP, path)
    return path


def test_mark_channel_keeps_comments_and_hash(mapfile):
    before = am.load(mapfile)
    ev = am.Evidence(method="guided:tps_sweep", summary="monotonic 3% -> 96%", log="v1.json")
    after = me.mark_channel(mapfile, "tps", ev)
    tps = after.channel("tps")
    assert tps.verified and tps.evidence.method == "guided:tps_sweep"
    assert tps.evidence.map_hash == before.version_hash
    assert tps.evidence.result == "pass" and tps.evidence.date
    assert after.version_hash == before.version_hash
    text = mapfile.read_text()
    assert "# kerpz uses 0x21 in one place" in text            # comments survive
    assert (mapfile.parent / "addressmap.toml.bak").exists()
    assert not me.MapFile.open(mapfile).rewritten


def test_mark_channel_on_another_variant_goes_into_the_variant(mapfile):
    after = me.mark_channel(mapfile, "batt", am.Evidence(method="manual"), variant="P2T")
    assert after.variant == "P2T" and after.channel("batt").verified
    assert not am.load(mapfile).channel("batt").verified         # P2P untouched
    doc = tomllib.loads(mapfile.read_text())
    batt = next(c for c in doc["channel"] if c["name"] == "batt")
    assert batt["variants"]["P2T"]["status"] == "verified"
    # and the other way: rpm, verified on the P2P, is not verified on the P2T
    assert am.load(mapfile).channel("rpm").verified
    assert not am.load(mapfile, variant="P2T").channel("rpm").verified


def test_mark_suspect_and_bad_status(mapfile):
    after = me.mark_channel(mapfile, "o2", am.Evidence(summary="flat"), status="suspect")
    assert after.channel("o2").status == "suspect"
    assert after.channel("o2").evidence.result == "fail"
    with pytest.raises(am.MapError):
        me.mark_channel(mapfile, "o2", am.Evidence(), status="great")
    with pytest.raises(am.MapError, match="no channel"):
        me.mark_channel(mapfile, "nope", am.Evidence())


def test_update_channel_resets_verification_and_bumps_version(mapfile):
    me.mark_channel(mapfile, "ltft", am.Evidence(method="manual"))
    before = am.load(mapfile)
    after = me.update_channel(mapfile, "ltft", addr=0x21, source="scanner")
    ch = after.channel("ltft")
    assert ch.addr == 0x21 and not ch.verified and ch.evidence is None
    assert after.version == "p2p-guess-3 (kerpz P2T map)"
    assert after.version_hash != before.version_hash
    same = me.update_channel(mapfile, "ltft", notes="only a note")
    assert same.version == after.version


def test_add_remove_items_and_constants(mapfile):
    mf = me.MapFile.open(mapfile)
    mf.add_item("math", {"name": "rpm_k", "expr": "[rpm] / 1000", "units": "krpm"})
    mf.add_item("alarm", {"name": "hot_iat", "channel": "iat", "high": 60})
    mf.set_constant("cylinders", 4)
    mf.set_constant("new_const", 1.5)
    with pytest.raises(am.MapError, match="already"):
        mf.add_item("math", {"name": "rpm_k", "expr": "1"})
    mf.save()
    amap = am.load(mapfile)
    assert "rpm_k" in amap.math_names and amap.constants["new_const"] == 1.5
    mf.remove_item("alarm", "hot_iat")
    mf.set_constant("new_const", None)
    assert "hot_iat" not in mf.names("alarm") and "new_const" not in mf.doc["constants"]
    assert not mf.rewritten


def test_save_refuses_an_invalid_map(mapfile):
    mf = me.MapFile.open(mapfile)
    mf.set_channel("rpm", formula="x +")
    with pytest.raises(am.MapError):
        mf.save()
    assert am.load(mapfile).channel("rpm").formula != "x +"


def test_dumps_round_trips_a_map_built_in_code():
    amap = am.load_default()
    text = me.dumps(me.to_doc(amap))
    again = am.loads(text)
    assert again.version_hash == amap.version_hash
    assert again.names == amap.names and again.math_names == amap.math_names
    assert [a.name for a in again.alarms] == [a.name for a in amap.alarms]


def test_upgrade_v1_to_v2(tmp_path):
    path = tmp_path / "old.toml"
    shutil.copyfile(__import__("pathlib").Path(__file__).parent / "data_v1_addressmap.toml", path)
    amap, skipped = me.upgrade_to_v2(path)
    assert amap.schema == 2 and "inj_duty" in amap.math_names and "ect_high" in [
        a.name for a in amap.alarms]
    assert skipped == []
    assert me.upgrade_to_v2(path)[1] == []                       # idempotent


def test_next_version():
    assert me.next_version("p2p-guess-2 (kerpz)") == "p2p-guess-3 (kerpz)"
    assert me.next_version("mine") == "mine r2"
    assert me.next_version("") == "r2"


def test_diff_and_redecode(mapfile):
    old = am.load(mapfile)
    new = me.update_channel(mapfile, "ect", formula="x - 39")
    diff = me.diff_maps(old, new)
    assert diff and diff.decode_changed
    assert any(c.name == "ect" for c in diff.changes)
    assert "ect" in diff.text()
    frames = [(0.0, {a: 100 for a in range(256)}), (0.1, {0x00: 1})]
    cols = me.redecode(new, frames)
    assert cols["t"].tolist() == [0.0, 0.1]
    assert cols["ect"][0] == pytest.approx(new.decode({0x10: 100})["ect"])
    assert np.isnan(cols["ect"][1])
    assert "inj_duty" in cols
