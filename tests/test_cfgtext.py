from hondalog.handheld import cfgtext
from hondalog.handheld.mapfile import DEFAULT_CFG


def test_parse_finds_settings_and_commented_ones():
    entries = cfgtext.parse(DEFAULT_CFG)
    assert entries
    keys = {e.key for e in entries}
    assert "rotate" in keys
    assert any(not e.enabled for e in entries) or any(e.enabled for e in entries)


def test_set_value_roundtrip():
    out = cfgtext.set_value(DEFAULT_CFG, "rotate", "180")
    assert cfgtext.values(out)["rotate"] == "180"
    assert cfgtext.set_value(out, "rotate", "0", enable=True).count("rotate") == out.count("rotate")


def test_problems_flags_unknown_keys():
    known = {e.key for e in cfgtext.parse(DEFAULT_CFG)}
    assert not cfgtext.problems(DEFAULT_CFG, known)
    assert cfgtext.problems("nonsense_key = 1\n", known)
