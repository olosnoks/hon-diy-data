import struct

import numpy as np
import pytest

from hondalog.tuning import (ChecksumSpec, Definition, DefinitionError, Storage,
                             loads_definition)
from hondalog.tuning.definition import dumps_toml, slugify
from hondalog.tuning.demo import DEMO_DEFINITION, demo_definition


def test_storage_packed_big_endian_16():
    data = bytearray(64)
    data[4:12] = struct.pack(">4H", 1, 2, 300, 65535)
    st = Storage(4, bits=16, rows=2, cols=2)
    assert st.read(data).tolist() == [[1, 2], [300, 65535]]
    assert st.ranges() == [(4, 12)] and st.end == 12


def test_storage_signed_little_endian_and_write_back():
    data = bytearray(16)
    st = Storage(2, bits=16, signed=True, lsb_first=True, cols=3)
    st.write(data, np.array([[-1, 2, -300]]))
    assert bytes(data[2:8]) == struct.pack("<3h", -1, 2, -300)
    assert st.read(data).tolist() == [[-1, 2, -300]]


def test_storage_column_major():
    data = bytearray(range(16))
    st = Storage(0, rows=2, cols=3, column_major=True)
    assert st.read(data).tolist() == [[0, 2, 4], [1, 3, 5]]


def test_storage_strides_match_tunerpro_rule():
    # 2 rows of 4 words, 12 bytes from the last word of a row to the next row
    st = Storage(0x1050, bits=16, rows=2, cols=4, minor_stride_bits=96)
    assert st.offsets().tolist() == [[0x1050, 0x1052, 0x1054, 0x1056],
                                     [0x1062, 0x1064, 0x1066, 0x1068]]
    assert st.ranges() == [(0x1050, 0x1058), (0x1062, 0x106A)]
    st = Storage(0, bits=8, cols=3, major_stride_bits=16)
    assert st.offsets().tolist() == [[0, 2, 4]]


def test_storage_float_and_24_bit():
    data = bytearray(16)
    st = Storage(0, bits=32, is_float=True, lsb_first=True)
    st.write(data, np.array([[1.5]]))
    assert bytes(data[:4]) == struct.pack("<f", 1.5) and st.read(data)[0, 0] == 1.5
    st = Storage(8, bits=24)
    st.write(data, np.array([[0x123456]]))
    assert bytes(data[8:11]) == b"\x12\x34\x56"


def test_storage_changes_lists_only_differing_bytes():
    data = bytearray(8)
    st = Storage(0, cols=8)
    raw = st.read(data)
    assert st.changes(data, raw) == []
    raw[0, 1], raw[0, 2], raw[0, 6] = 5, 6, 7
    assert st.changes(data, raw) == [(1, b"\x05\x06"), (6, b"\x07")]


@pytest.mark.parametrize("kwargs,match", [
    (dict(address=0, bits=12), "not supported"),
    (dict(address=0, bits=16, is_float=True), "float"),
    (dict(address=0, rows=0), "at least 1"),
    (dict(address=-1), "negative"),
    (dict(address=0, major_stride_bits=4), "whole bytes"),
])
def test_storage_rejects_bad_layouts(kwargs, match):
    with pytest.raises(DefinitionError, match=match):
        Storage(**kwargs)


def test_storage_range_checks():
    st = Storage(6, bits=16, cols=2)
    with pytest.raises(DefinitionError, match="outside"):
        st.read(bytearray(8))
    with pytest.raises(DefinitionError, match="out of range"):
        Storage(0).encode(np.array([[256]]))
    with pytest.raises(DefinitionError, match="shape"):
        Storage(0).encode(np.array([[1, 2]]))


def test_demo_definition_loads_and_validates():
    d = demo_definition()
    assert d.validate() == []
    assert [t.id for t in d.tables] == ["fuel", "ignition", "target_lambda", "ect_fuel"]
    assert d.table("fuel").rows == 8 and d.table("fuel").cols == 10
    assert d.table("Fuel (low cam)") is d.table("fuel")
    assert d.checksum.kind == "sum8" and d.checksum.active
    assert d.signatures[0].data == b"HLDEMO"
    assert set(d.by_category()) == {"Fuel", "Ignition", "Limits", "(none)"}
    with pytest.raises(KeyError, match="no item named"):
        d.item("nope")
    with pytest.raises(KeyError, match="not a table"):
        d.table("rev_limit")
    with pytest.raises(KeyError):
        d.scalar("fuel")
    with pytest.raises(KeyError):
        d.flag("fuel")
    with pytest.raises(KeyError):
        d.patch("fuel")


def test_native_toml_round_trip_is_stable():
    d = demo_definition()
    text = d.to_toml()
    again = loads_definition(text)
    assert again.to_toml() == text
    assert [i.id for i in again.items()] == [i.id for i in d.items()]
    assert again.table("ignition").z.storage == d.table("ignition").z.storage
    assert again.patches[0].entries[0].base == bytes.fromhex("c3f0")


def test_save_and_load(tmp_path):
    from hondalog.tuning import load_definition
    path = tmp_path / "d.toml"
    demo_definition().save(path)
    assert load_definition(path).title == demo_definition().title


def test_owner_map_and_validate_problems():
    d = demo_definition()
    owners = d.owner_map()
    assert owners[0x4020].id == "fuel" and owners[0x4080].id == "ignition"
    assert owners[0x4000].id == "fuel" and owners[0] is None
    d.tables[0].z.storage.address = 0x7FF0
    d.tables[1].id = "fuel"
    d.checksum.store = 0x9000
    problems = d.validate()
    assert any("outside the ROM" in p for p in problems)
    assert any("duplicate id" in p for p in problems)
    assert any("checksum store" in p for p in problems)


@pytest.mark.parametrize("text,match", [
    ("format = 'other'", "not a hondalog definition"),
    ("format = 'hondalog-definition'\nformat_version = 99", "newer"),
    ("format = 'hondalog-definition'\n[[table]]\nid='a'", "table.z"),
    ("format = 'hondalog-definition'\n[[table]]\nid='a'\n[table.z]\nrows=1", "address"),
    ("format = 'hondalog-definition'\n[[scalar]]\nid='a'\naddress=1\n"
     "[[scalar]]\nid='a'\naddress=2", "duplicate"),
    ("format = 'hondalog-definition'\n[checksum]\nkind='crc99'", "checksum"),
    ("format = 'hondalog-definition'\n[[signature]]\naddress=1\ndata='zz'", "signature"),
    ("format = 'hondalog-definition'\n[[patch]]\nid='p'\n[[patch.entries]]\naddress=1",
     "patch p"),
    ("this is not toml =", "definition"),
])
def test_bad_native_definitions(text, match):
    with pytest.raises(DefinitionError, match=match):
        loads_definition(text)


def test_checksum_spec_rules():
    assert ChecksumSpec("sum16").store_bits == 16
    assert not ChecksumSpec("sum8", disabled=True).active
    with pytest.raises(DefinitionError):
        ChecksumSpec("sum8", mode="weird")


def test_toml_writer_escapes_and_nests():
    import tomllib
    doc = {"a": 'q"\\\n\x01', "f": 1.5, "b": True, "address": 255, "n": -3,
           "t": {"k": [1, 2]}, "rows": [{"x": 1, "inner": [{"y": "z"}]}], "none": None,
           "empty": [], "inline": [{"a": 1}, 2]}
    back = tomllib.loads(dumps_toml(doc))
    assert back["a"] == doc["a"] and back["address"] == 255 and back["n"] == -3
    assert back["rows"][0]["inner"][0]["y"] == "z" and back["t"]["k"] == [1, 2]
    assert back["inline"] == [{"a": 1}, 2]
    with pytest.raises(DefinitionError):
        dumps_toml({"x": float("nan")})
    with pytest.raises(DefinitionError):
        dumps_toml({"x": object()})


def test_slugify():
    taken = set()
    assert slugify("Main Fuel (Low)", taken) == "main_fuel_low"
    assert slugify("Main Fuel (Low)", taken) == "main_fuel_low_2"
    assert slugify("12 volts") == "t_12_volts" and slugify("!!!") == "item"


def test_shipped_demo_file_has_the_warning():
    assert "NOT A P28" in DEMO_DEFINITION.read_text()
    assert isinstance(demo_definition(), Definition)
