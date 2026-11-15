"""Address map schema 2: backwards compatibility, variants, evidence, hash."""

from pathlib import Path

import pytest

from hondalog import addressmap as am

V1_MAP = """
version = "old"
ecu = "P2P"
[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "1875000 / (x + 1)"
status = "verified"
[[channel]]
name = "ect"
addr = 0x10
formula = "x - 40"
"""

V2_MAP = """
schema = 2
version = "test-1"
ecu_variant = "P2P"
[variants.P2P]
description = "target"
[variants.P28]
description = "obd1"
constants = { k = 2 }
[constants]
k = 1
[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "x"
endian = "little"
rate = "fast"
status = "verified"
source = "test"
[channel.evidence]
method = "guided:idle"
summary = "agreed with tach"
[channel.variants.P28]
formula = "1875000 / (x + 1)"
[[channel]]
name = "ect"
addr = 0x10
formula = "x - 40"
rate = "slow"
[channel.variants.P28]
absent = true
[[math]]
name = "rpm_k"
expr = "[rpm] * k"
"""


def test_v1_file_loads_unchanged():
    amap = am.loads(V1_MAP)
    assert amap.schema == 1
    assert amap.names == ["rpm", "ect"]
    assert amap.channel("rpm").verified
    # no rate in the file: the name decides (W1's scheduler classes), never blank
    assert amap.channel("rpm").rate == "fast" and amap.channel("ect").rate == "slow"
    assert amap.channel("rpm").endian == "big"
    assert amap.decode({0: 0x01, 1: 0x00, 0x10: 120}) == pytest.approx(
        {"rpm": 1875000 / 257, "ect": 80})
    assert amap.math == [] and amap.alarms == []


def test_v1_file_with_unknown_top_keys_still_loads():
    assert am.loads("custom = 1\n" + V1_MAP).names == ["rpm", "ect"]


def test_shipped_v1_default_still_parses():
    """The 0.1.0 default map is the oldest real v1 file."""
    old = am.load(Path(__file__).parent / "data_v1_addressmap.toml")
    new = am.load_default()
    assert old.schema == 1 and old.names == new.names
    mem = {a: (a * 37 + 11) & 0xFF for a in range(256)}
    assert old.decode(mem) == pytest.approx(new.decode(mem), nan_ok=True)


def test_v2_fields_variants_and_constants():
    amap = am.loads(V2_MAP)
    assert amap.schema == 2 and amap.variant == "P2P" and amap.base_variant == "P2P"
    rpm = amap.channel("rpm")
    assert rpm.endian == "little" and rpm.rate == "fast" and rpm.source == "test"
    assert rpm.evidence is not None and rpm.evidence.method == "guided:idle"
    assert amap.decode({0: 0x01, 1: 0x02, 0x10: 50})["rpm"] == 0x0201
    assert amap.by_rate()["slow"] == [amap.channel("ect")]
    assert amap.math_engine().update({"rpm": 100.0, "ect": 1.0}, 0.0)["rpm_k"] == 100.0

    p28 = am.loads(V2_MAP, variant="P28")
    assert p28.variant == "P28" and p28.names == ["rpm"]           # ect absent on P28
    assert p28.channel("rpm").formula == "1875000 / (x + 1)"
    assert not p28.channel("rpm").verified                         # never inherited
    assert p28.channel("rpm").evidence is None
    assert p28.constants["k"] == 2.0
    assert p28.ecu == "obd1"


def test_math_verified_follows_its_inputs():
    amap = am.loads(V2_MAP)
    assert amap.is_verified("rpm") and amap.is_verified("rpm_k")
    assert not amap.is_verified("ect") and not amap.is_verified("nope")


@pytest.mark.parametrize("patch, why", [
    ("schema = 3\n", "schema 3"),
    ("schema = 2\nbogus = 1\n", "unknown section"),
])
def test_v2_rejects(patch, why):
    with pytest.raises(am.MapError, match=why):
        am.loads(patch + V1_MAP)


@pytest.mark.parametrize("old, new, why", [
    ('rate = "fast"', 'rate = "warp"', "rate must be"),
    ('endian = "little"', 'endian = "middle"', "endian must be"),
    ('[channel.variants.P28]\nformula', '[channel.variants.P28]\nkind = "flag"\nformula',
     "cannot set"),
    ('[[math]]\nname = "rpm_k"\nexpr = "[rpm] * k"', '[[math]]\nname = "m"\nexpr = "[m]"', "loop"),
    ('summary = "agreed with tach"', 'colour = "red"', "unknown evidence field"),
    ('ecu_variant = "P2P"', 'ecu_variant = "P30"', "not listed"),
    ("[constants]\nk = 1", "[constants]\nrpm = 1", "same name"),
])
def test_v2_validation(old, new, why):
    text = V2_MAP.replace(old, new, 1)
    assert text != V2_MAP
    with pytest.raises(am.MapError, match=why):
        am.loads(text)


def test_formula_cannot_use_history():
    with pytest.raises(am.MapError, match="history"):
        am.Channel("x", 0, formula="avg(x, 100)")


def test_version_hash_tracks_decoding_only():
    a = am.loads(V2_MAP)
    assert len(a.version_hash) == 12 and a.version_tag.startswith("test-1 #")
    relabelled = am.loads(V2_MAP.replace('source = "test"', 'source = "test"\nlabel = "Engine"'))
    unverified = am.loads(V2_MAP.replace('status = "verified"', 'status = "unverified"', 1))
    assert relabelled.version_hash == a.version_hash == unverified.version_hash
    moved = am.loads(V2_MAP.replace("addr = 0x10", "addr = 0x11"))
    reformulated = am.loads(V2_MAP.replace('formula = "x - 40"', 'formula = "x - 39"'))
    assert len({a.version_hash, moved.version_hash, reformulated.version_hash}) == 3
    assert am.loads(V2_MAP, variant="P28").version_hash != a.version_hash


def test_shipped_default_is_v2_with_three_variants():
    amap = am.load_default()
    assert amap.schema == 2 and amap.variant == "P2P"
    assert set(amap.variants) == {"P2P", "P2T", "P28"}
    assert all(c.source for c in amap.channels)
    assert {"inj_duty", "fuel_flow", "total_trim", "rpm_rate"} <= set(amap.math_names)
    assert "ect_high" in [a.name for a in amap.alarms]
    for variant in amap.variants:
        assert am.load(am.DEFAULT_MAP, variant=variant).names
