import pytest

from hondalog import addressmap as am


def test_default_map_loads_and_is_all_unverified():
    m = am.load_default()
    assert m.version
    for name in ("rpm", "ect", "iat", "map", "tps", "batt", "o2", "vss",
                 "ign", "inj", "stft", "ltft"):
        assert m.channel(name) is not None, name
    assert any(c.kind == "flag" for c in m.channels)
    assert all(not c.verified for c in m.channels)


def test_blocks_cover_every_channel_within_limits():
    m = am.load_default()
    blocks = m.blocks()
    covered = {a for addr, n in blocks for a in range(addr, addr + n)}
    for c in m.channels:
        assert set(range(c.addr, c.addr + c.length)) <= covered
    assert all(1 <= n <= 16 for _, n in blocks)
    assert len(blocks) <= 4


def test_plan_blocks_splits_and_merges():
    ch = [am.Channel("a", 0x00, 2), am.Channel("b", 0x0F), am.Channel("c", 0x10),
          am.Channel("d", 0x80)]
    assert am.plan_blocks(ch, 16) == [(0x00, 16), (0x10, 1), (0x80, 1)]


def test_decode_formulas():
    m = am.load_default()
    mem = {a: 0 for a in range(256)}
    mem[0x00], mem[0x01] = 0x0B, 0xB8          # 3000 / 4
    mem[0x17] = 146
    mem[0x0F] = 0x01
    v = m.decode(mem)
    assert v["rpm"] == 750
    assert v["batt"] == pytest.approx(13.97, abs=0.01)
    assert v["closed_loop"] == 1 and v["mil"] == 0


def test_decode_missing_bytes_gives_none():
    m = am.load_default()
    v = m.decode({0x00: 1, 0x01: 2})
    assert v["rpm"] is not None and v["ect"] is None


def test_formula_helpers():
    assert am.compile_formula("bit(x, 3)")(0x08, [8]) == 1
    assert am.compile_formula("signed(x, 8)")(0xFF, [255]) == -1
    assert am.compile_formula("b[0] * 256 + b[1]")(0, [1, 2]) == 258
    assert am.compile_formula("poly(x, 1, 2, 3)")(2, [2]) == 17
    assert am.compile_formula("x / 4 if x > 0 else 0")(8, [8]) == 2


@pytest.mark.parametrize("bad", [
    "__import__('os').system('true')", "x.real", "open('/etc/passwd')",
    "lambda: 1", "'a' * 3", "x +", "y + 1", "[i for i in b]",
])
def test_formula_rejects_anything_but_arithmetic(bad):
    with pytest.raises(am.MapError):
        am.compile_formula(bad)


def test_formula_runtime_error_gives_none():
    assert am.Channel("z", 0, formula="1 / x").convert(b"\x00") is None


def test_load_reports_bad_files(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text("version = 'x'\n")
    with pytest.raises(am.MapError, match="no .*channel"):
        am.load(f)
    f.write_text("[[channel]]\nname='a'\naddr=1\n[[channel]]\nname='a'\naddr=2\n")
    with pytest.raises(am.MapError, match="duplicate"):
        am.load(f)
    f.write_text("[[channel]]\nname='a'\naddr=300\n")
    with pytest.raises(am.MapError):
        am.load(f)
    f.write_text("[[channel]]\nname='a'\naddr=1\nbogus=2\n")
    with pytest.raises(am.MapError):
        am.load(f)
    f.write_text("this is not toml [")
    with pytest.raises(am.MapError):
        am.load(f)


def test_protocol_overrides_from_map(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text("[protocol]\nreply_timeout_s = 0.5\ninit_sequence = ''\n"
                 "[[channel]]\nname='a'\naddr=1\n")
    m = am.load(f)
    assert m.framing.reply_timeout_s == 0.5 and m.framing.init_sequence == b""


def test_user_map_created_on_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    m = am.load_user_map()
    assert (tmp_path / "hondalog" / "addressmap.toml").exists()
    assert m.channel("rpm")
