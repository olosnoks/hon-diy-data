import pytest

from hondalog.tuning import (BurnRefused, ChecksumSpec, RomError, RomImage,
                             build_burn_image, checksum_fix, checksum_report, chip_image,
                             detect_layout)
from hondalog.tuning.demo import build_demo_rom, demo_definition
from hondalog.tuning.rom import expected_checksum, is_blank, sha256_hex

ROM = build_demo_rom()
BLANK = b"\xff" * 0x8000


def test_demo_rom_is_deterministic_and_valid():
    assert build_demo_rom() == ROM and len(ROM) == 0x8000
    assert checksum_report(ROM, demo_definition().checksum).status == "ok"


@pytest.mark.parametrize("blob,kind,copy", [
    (ROM, "plain", 0), (ROM + ROM, "doubled", 0), (BLANK + ROM, "upper", 1),
    (ROM + BLANK, "lower", 0), (b"\0" * 0x8000 + ROM, "upper", 1),
    (ROM * 4, "doubled", 0), (BLANK * 3 + ROM, "upper", 3),
])
def test_layout_detection(blob, kind, copy):
    layout = detect_layout(blob)
    assert (layout.kind, layout.used_copy) == (kind, copy)
    rom = RomImage.from_bytes(blob)
    assert bytes(rom.data) == ROM and rom.sha256 == sha256_hex(ROM)
    assert rom.layout.describe()


def test_mixed_file_needs_a_choice():
    other = bytes(b ^ 0x55 for b in ROM)
    with pytest.raises(RomError, match="say which one"):
        RomImage.from_bytes(ROM + other)
    upper = RomImage.from_bytes(ROM + other, half="upper")
    assert bytes(upper.data) == other and upper.layout.kind == "mixed"
    assert any("different images" in w for w in upper.warnings)
    assert bytes(RomImage.from_bytes(ROM + other, half="lower").data) == ROM
    assert bytes(RomImage.from_bytes(ROM + other, half=1).data) == other
    assert RomImage.from_bytes(ROM + ROM, half="upper").layout.kind == "doubled"
    with pytest.raises(RomError, match="half must be"):
        RomImage.from_bytes(ROM + other, half="middle")


@pytest.mark.parametrize("size", [0, 100, 0x4000, 0x8001, 0xC001])
def test_wrong_sizes_are_refused(size):
    with pytest.raises(RomError, match="expected 32768"):
        detect_layout(b"\x01" * size)


def test_sanity_warnings():
    assert "blank" in RomImage.from_bytes(BLANK).warnings[0]
    mostly = bytearray(b"\xff" * 0x8000)
    mostly[:100] = bytes(range(100))
    assert "90%" in RomImage.from_bytes(bytes(mostly)).warnings[0]
    assert RomImage.from_bytes(ROM).warnings == []
    assert is_blank(b"\0\0") and not is_blank(b"") and not is_blank(b"\0\xff")


def test_chip_image_layouts():
    assert chip_image(ROM) == ROM + ROM
    assert chip_image(ROM, layout="upper") == BLANK + ROM
    assert chip_image(ROM, layout="lower", fill=0) == ROM + b"\0" * 0x8000
    assert chip_image(ROM, 0x8000) == ROM
    with pytest.raises(RomError, match="does not fit"):
        chip_image(ROM[:100])
    with pytest.raises(RomError, match="unknown chip layout"):
        chip_image(ROM, layout="sideways")


def test_load_save_round_trip(tmp_path):
    src = tmp_path / "chip.bin"
    src.write_bytes(BLANK + ROM)
    rom = RomImage.load(src)
    assert rom.layout.kind == "upper" and rom.file_sha256 == sha256_hex(BLANK + ROM)
    assert rom.save(tmp_path / "plain.bin").read_bytes() == ROM
    assert rom.save(tmp_path / "same.bin", "same").read_bytes() == BLANK + ROM
    assert rom.save(tmp_path / "dbl.bin", "doubled").read_bytes() == ROM + ROM
    with pytest.raises(RomError, match="already exists"):
        rom.save(tmp_path / "plain.bin")
    rom.save(tmp_path / "plain.bin", overwrite=True)
    assert not list(tmp_path.glob("*.part"))
    with pytest.raises(RomError, match="cannot read"):
        RomImage.load(tmp_path / "missing.bin")
    (tmp_path / "short.bin").write_bytes(b"abc")
    with pytest.raises(RomError, match="short.bin"):
        RomImage.load(tmp_path / "short.bin")
    assert RomImage.from_bytes(ROM).container_bytes("same") == ROM


def test_signature_check():
    d = demo_definition()
    assert RomImage.from_bytes(ROM).check_signatures(d) == []
    bad = bytearray(ROM)
    bad[0x10] ^= 0xFF
    assert "demo marker" in RomImage.from_bytes(bytes(bad)).check_signatures(d)[0]
    d.rom_size = 0x10000
    assert "65536-byte ROM" in RomImage.from_bytes(ROM).check_signatures(d)[0]


def spec(**kw):
    base = dict(kind="sum8", start=0, end=16, store=15)
    base.update(kw)
    return ChecksumSpec(**base)


def test_sum8_balance():
    data = bytearray(range(1, 17))
    s = spec()
    assert checksum_report(data, s).status == "bad"
    (off, blob), = checksum_fix(data, s)
    data[off:off + 1] = blob
    assert sum(data) % 256 == 0 and checksum_report(data, s).status == "ok"
    assert checksum_fix(data, s) == []
    s = spec(target=0x5A)
    data[15] = expected_checksum(data, s)
    assert sum(data) % 256 == 0x5A and checksum_report(data, s).ok


def test_store_mode_and_store_outside_range():
    data = bytearray(range(1, 17))
    s = spec(mode="store", start=0, end=8, store=12)
    assert checksum_report(data, s).expected == sum(range(1, 9))
    data[12] = 36
    assert checksum_report(data, s).status == "ok"
    s = spec(mode="store", start=0, end=16, store=15)       # store inside: skipped
    data[15] = sum(data[:15]) % 256
    assert checksum_report(data, s).status == "ok"
    s = spec(mode="balance", start=0, end=8, store=12, target=0)
    data[12] = (-36) % 256
    assert checksum_report(data, s).status == "ok"


def test_xor8_and_sum16():
    data = bytearray(range(1, 17))
    s = spec(kind="xor8")
    data[15] = expected_checksum(data, s)
    x = 0
    for b in data:
        x ^= b
    assert x == 0 and checksum_report(data, s).status == "ok"
    s = spec(kind="sum16", mode="store", start=0, end=14, store=14)
    for off, blob in checksum_fix(data, s):
        data[off:off + len(blob)] = blob
    assert int.from_bytes(data[14:16], "big") == sum(data[:14])
    s = spec(kind="sum16", mode="store", start=0, end=14, store=14, lsb_first=True)
    for off, blob in checksum_fix(data, s):
        data[off:off + len(blob)] = blob
    assert int.from_bytes(data[14:16], "little") == sum(data[:14])
    s = spec(kind="sum16", mode="balance", start=0, end=16, store=14, target=300)
    for off, blob in checksum_fix(data, s):
        data[off:off + len(blob)] = blob
    assert sum(data) == 300 and checksum_report(data, s).status == "ok"
    with pytest.raises(RomError, match="no 16-bit"):
        expected_checksum(bytearray(16), spec(kind="sum16", store=14, target=5000))


def test_none_disabled_and_out_of_range():
    assert checksum_report(ROM, ChecksumSpec()).status == "none"
    r = checksum_report(ROM, spec(disabled=True, status="verified"))
    assert r.status == "disabled" and r.ok and r.verified_rule
    assert checksum_fix(bytearray(ROM), spec(disabled=True)) == []
    with pytest.raises(RomError, match="outside the ROM"):
        checksum_report(b"\0" * 8, spec())


def test_burn_image_is_doubled_and_hashed():
    image = build_burn_image(ROM, demo_definition())
    assert image.data == ROM + ROM and image.chip_size == 0x10000
    assert image.data[0x8000:] == ROM
    assert image.rom_sha256 == sha256_hex(ROM) and image.image_sha256 == sha256_hex(ROM * 2)
    assert image.checksum.status == "ok"
    assert any("unverified" in w for w in image.warnings)
    upper = build_burn_image(ROM, demo_definition(), layout="upper")
    assert upper.data == BLANK + ROM


def test_burn_refusals():
    d = demo_definition()
    bad = bytearray(ROM)
    bad[0x4020] ^= 1
    with pytest.raises(BurnRefused, match="Fix the checksum"):
        build_burn_image(bad, d)
    with pytest.raises(BurnRefused, match="blank"):
        build_burn_image(BLANK, d)
    with pytest.raises(BurnRefused, match="expected 32768"):
        build_burn_image(ROM + ROM, d)
    wrong = bytearray(ROM)
    wrong[0x10], wrong[0x11] = wrong[0x11], wrong[0x10]       # same sum, bad signature
    with pytest.raises(BurnRefused, match="does not match the definition"):
        build_burn_image(wrong, d)
    forced = build_burn_image(wrong, d, allow_signature_mismatch=True)
    assert any("signature" in w for w in forced.warnings)
    d.checksum.end = 0x9000
    with pytest.raises(BurnRefused, match="outside the ROM"):
        build_burn_image(ROM, d)
    with pytest.raises(BurnRefused, match="does not fit"):
        build_burn_image(ROM, demo_definition(), chip_size=0xC000)


def test_burn_without_checksum_knowledge_warns():
    image = build_burn_image(ROM)
    assert image.checksum.status == "none" and "not checked" in image.warnings[0]
    d = demo_definition()
    d.checksum = ChecksumSpec()
    assert any("not checked" in w for w in build_burn_image(ROM, d).warnings)
    d.checksum = ChecksumSpec("sum8", 0, 0x8000, 0x7FFF, disabled=True, status="verified")
    assert build_burn_image(ROM, d).warnings == []
