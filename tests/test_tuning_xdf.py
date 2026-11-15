import dataclasses
import struct

import numpy as np
import pytest

from hondalog.tuning import (DefinitionError, RomImage, RomSession, dumps_xdf,
                             load_definition, load_xdf, loads_definition, loads_xdf,
                             save_xdf)
from hondalog.tuning.demo import build_demo_rom, demo_definition
from tuning_helpers import SAMPLE_XDF


def items_key(d):
    return [dataclasses.asdict(i) | {"uniqueid": "", "notes": []} for i in d.items()]


@pytest.fixture
def sample():
    return load_xdf(SAMPLE_XDF)


def sample_rom() -> bytearray:
    data = bytearray(0x8000)
    data[0x1000:0x1008] = struct.pack("<4H", 4000, 8000, 16000, 24000)
    data[0x1010:0x101C] = bytes(range(20, 140, 10))
    data[0x1040:0x1042] = bytes([30, 100])
    data[0x1050:0x1058] = struct.pack(">4h", 100, 150, -20, 300)
    data[0x1062:0x106A] = struct.pack(">4h", 80, 120, 200, 250)
    data[0x1080:0x1086] = bytes([1, 2, 3, 4, 5, 6])
    data[0x1090] = 3
    data[0x10A0:0x10A2] = bytes([7, 8])
    data[0x10B0:0x10B5] = bytes([20, 40, 60, 80, 120])
    data[0x10B8:0x10BD] = bytes([255, 128, 51, 0, 0])
    data[0x10C0:0x10C2] = struct.pack(">H", 250)
    data[0x10C4:0x10C8] = struct.pack("<f", 240.0)
    data[0x10D0] = 0x84
    data[0x20:0x22] = bytes.fromhex("1234")
    return data


def test_header(sample):
    assert sample.title == "hondalog test fixture"
    assert sample.description == "Line one\nLine two & more"
    assert sample.author == "hondalog tests" and sample.version == "0.3"
    assert sample.rom_size == 0x8000 and sample.base_offset == 0
    assert [c.name for c in sample.categories] == ["Fuel", "Spark", "Switches"]


def test_items_and_ids(sample):
    assert [(i.kind, i.id) for i in sample.items()] == [
        ("table", "main_fuel"), ("table", "spark_advance"), ("table", "column_major"),
        ("table", "needs_cell_function"), ("table", "warmup_enrichment"),
        ("scalar", "rev_limit"), ("scalar", "injector_scale"),
        ("flag", "closed_loop"), ("flag", "closed_loop_2"), ("patch", "disable_something")]
    assert sample.table("0x100").id == "main_fuel"           # lookup by uniqueid
    assert sample.table("column_major").categories == ["Fuel", "Spark"]


def test_warnings_name_what_was_dropped(sample):
    text = "\n".join(sample.warnings)
    assert "Header Only" in text and "No Address" in text
    assert "CELL() is not supported" in text
    assert "checksum block" in text and "XDFMYSTERY" in text


def test_table_axes(sample):
    t = sample.table("main_fuel")
    assert t.x.storage.lsb_first and t.x.storage.bits == 16 and t.x.math == "X/4"
    assert t.y.storage is None and t.y.labels == ["Low", "Mid", "High"]
    assert t.z.math == "0.05*X+1"        # cell variable renamed from "value"
    assert (t.z.min, t.z.max, t.z.decimals, t.z.units) == (0.0, 20.0, 2, "ms")
    spark = sample.table("spark_advance")
    assert spark.x.labels == ["1000", "2000", "4000", "6000"]
    assert spark.z.storage.signed and spark.z.storage.minor_stride_bits == 96
    cm = sample.table("column_major")
    assert cm.z.storage.column_major and cm.z.vars[0].name == "K"
    assert cm.z.vars[0].storage.address == 0x1090


def test_function_constant_flag_patch(sample):
    curve = sample.table("warmup_enrichment")
    assert curve.curve and curve.rows == 1 and curve.cols == 5
    assert curve.x.storage.address == 0x10B0 and curve.z.storage.address == 0x10B8
    assert curve.z.math == "x/2.55"            # case is kept as written
    rev = sample.scalar("rev_limit")
    assert rev.storage.bits == 16 and rev.max == 9000 and rev.units == "RPM"
    inj = sample.scalar("injector_scale")
    assert inj.storage.is_float and inj.storage.lsb_first
    assert sample.flag("closed_loop").mask == 0x04
    assert sample.flag("closed_loop_2").mask == 0x80
    patch = sample.patch("disable_something")
    assert patch.entries[0].data == b"\x00\xff" and patch.entries[0].base == b"\x12\x34"
    assert patch.entries[1].base is None


def test_values_read_through_the_definition(sample, tmp_path):
    s = RomSession(RomImage.from_bytes(bytes(sample_rom())), sample, store_dir=tmp_path)
    fuel = s.table("main_fuel")
    assert fuel.axis("x").values.tolist() == [1000, 2000, 4000, 6000]
    assert fuel.axis("y").labels == ["Low", "Mid", "High"] and not fuel.axis("y").numeric
    assert fuel.values()[0].tolist() == pytest.approx([2.0, 2.5, 3.0, 3.5])
    spark = s.table("spark_advance")
    assert spark.axis("x").values.tolist() == [1000, 2000, 4000, 6000]
    assert spark.values().tolist() == [[10, 15, -2, 30], [8, 12, 20, 25]]
    assert s.table("column_major").values().tolist() == [[3, 9, 15], [6, 12, 18]]
    assert s.table("warmup_enrichment").values()[0, :3].tolist() == pytest.approx(
        [100, 50.196, 20], abs=0.01)
    assert s.scalar("rev_limit") == 7500 and s.scalar("injector_scale") == 240
    assert s.flag("closed_loop") and s.flag("closed_loop_2")
    raw_only = s.table("needs_cell_function")
    assert not raw_only.real_available and raw_only.values("raw").tolist() == [[7, 8]]


def test_untouched_rom_is_reproduced_exactly(sample, tmp_path):
    """W5-01 acceptance: write every value back, get the same bytes."""
    rom = bytes(sample_rom())
    s = RomSession(RomImage.from_bytes(rom), sample, store_dir=tmp_path)
    for t in sample.tables:
        ed = s.table(t.id)
        view = "real" if ed.real_available else "raw"
        assert not ed.set_values(ed.values(view), view=view)
    for sc in sample.scalars:
        assert not s.set_scalar(sc.id, s.scalar(sc.id))
    for f in sample.flags:
        assert not s.set_flag(f.id, s.flag(f.id))
    assert bytes(s.data) == rom and not s.history


def test_xdf_round_trip(sample):
    again = loads_xdf(dumps_xdf(sample))
    assert items_key(again) == items_key(sample)
    assert (again.title, again.description, again.author, again.rom_size) == (
        sample.title, sample.description, sample.author, sample.rom_size)
    assert [c.name for c in again.categories] == [c.name for c in sample.categories]


def test_xdf_to_native_and_back(sample):
    native = loads_definition(sample.to_toml())
    assert items_key(native) == items_key(sample)
    assert items_key(loads_xdf(dumps_xdf(native))) == items_key(sample)


def test_native_demo_survives_xdf(tmp_path):
    d = demo_definition()
    path = tmp_path / "demo.xdf"
    save_xdf(d, path)
    back = load_definition(path)
    assert back.validate() == []
    rom = build_demo_rom()
    a = RomSession(RomImage.from_bytes(rom), d, store_dir=tmp_path)
    b = RomSession(RomImage.from_bytes(rom), back, store_dir=tmp_path)
    for t in d.tables:
        assert np.array_equal(a.table(t.id).values(), b.table(t.title).values())
        assert a.table(t.id).axis("x").labels == b.table(t.title).axis("x").labels
    assert back.checksum.kind == "none"          # checksum rules are native-only


def test_base_offset_forms():
    head = '<XDFFORMAT version="1.60"><XDFHEADER>{}</XDFHEADER></XDFFORMAT>'
    assert loads_xdf(head.format('<BASEOFFSET offset="16" subtract="0" />')).base_offset == 16
    assert loads_xdf(head.format('<BASEOFFSET offset="16" subtract="1" />')).base_offset == -16
    assert loads_xdf(head.format("<baseoffset>0x20</baseoffset>")).base_offset == 32
    d = loads_xdf(head.format('<BASEOFFSET offset="16" subtract="1" />'))
    assert loads_xdf(dumps_xdf(d)).base_offset == -16
    assert "no XDFHEADER" in loads_xdf('<XDFFORMAT version="1.60" />').warnings[0]


def test_axis_count_mismatch_is_noted():
    d = loads_xdf("""<XDFFORMAT><XDFTABLE uniqueid="0x1"><title>T</title>
      <XDFAXIS id="x"><EMBEDDEDDATA mmedaddress="0x10" mmedelementsizebits="8"
        mmedcolcount="5" /><indexcount>5</indexcount></XDFAXIS>
      <XDFAXIS id="y"><indexcount>2</indexcount><LABEL index="0" value="a" /></XDFAXIS>
      <XDFAXIS id="z"><EMBEDDEDDATA mmedaddress="0x20" mmedelementsizebits="8"
        mmedrowcount="3" mmedcolcount="4" /></XDFAXIS></XDFTABLE></XDFFORMAT>""")
    t = d.table("t")
    assert t.x.count == 4 and t.x.storage.cols == 4
    assert t.y.count == 3 and t.y.labels == ["a", "", ""]
    assert len(t.notes) == 2 and d.validate() == []


def test_per_cell_math_and_link_vars():
    d = loads_xdf("""<XDFFORMAT><XDFCONSTANT uniqueid="0x9"><title>C</title>
      <EMBEDDEDDATA mmedaddress="0x10" mmedelementsizebits="8" />
      <MATH equation="X*B"><VAR id="X" /><VAR id="B" type="link" linkid="0x7" /></MATH>
      <MATH equation="X*2" row="1"><VAR id="X" /></MATH></XDFCONSTANT>
      <XDFCONSTANT uniqueid="0x7"><title>Base</title>
      <EMBEDDEDDATA mmedaddress="0x11" mmedelementsizebits="8" /></XDFCONSTANT>
      </XDFFORMAT>""")
    c = d.scalar("c")
    assert c.vars[0].kind == "link" and c.vars[0].link == "0x7"
    assert "per-row" in c.notes[0]
    again = loads_xdf(dumps_xdf(d))
    assert again.scalar("c").vars[0].link == "0x7"


@pytest.mark.parametrize("text,match", [
    ("<notxdf />", "expected <XDFFORMAT>"),
    ("<XDFFORMAT>", "not valid XML"),
    ('<!DOCTYPE x [<!ENTITY a "b">]><XDFFORMAT />', "refused"),
    ('<XDFFORMAT><XDFCONSTANT><title>a</title><EMBEDDEDDATA mmedaddress="zz" />'
     "</XDFCONSTANT></XDFFORMAT>", "bad number"),
    ('<XDFFORMAT><XDFCONSTANT><title>a</title><EMBEDDEDDATA mmedaddress="1" '
     'mmedelementsizebits="12" /></XDFCONSTANT></XDFFORMAT>', "not supported"),
])
def test_bad_xdf(text, match):
    with pytest.raises(DefinitionError, match=match):
        loads_xdf(text)


def test_windows_encoding_and_bom():
    body = '<XDFFORMAT><XDFHEADER><deftitle>caf\xe9 \xb0C</deftitle></XDFHEADER></XDFFORMAT>'
    assert loads_xdf(body.encode("cp1252")).title == "café °C"
    assert loads_xdf(b"\xef\xbb\xbf" + body.encode("utf-8")).title == "café °C"
