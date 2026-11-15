"""ROM definitions: where the tables, scalars and flags live in a ROM image.

This is the native model. ``xdf.py`` converts TunerPro XDF files to and from
it; this module reads and writes the native TOML form, which can also hold
things XDF cannot (checksum rules, signature bytes, verification notes).

Addresses are offsets into the *logical* ROM (32 KB for a P28), before
``base_offset`` is added. X is the column axis, Y the row axis, Z the cells.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .mathexpr import Conversion, MathError

FORMAT_NAME = "hondalog-definition"
FORMAT_VERSION = 1


class DefinitionError(Exception):
    pass


# ---- storage ----------------------------------------------------------------

@dataclass
class Storage:
    """How a block of numbers is laid out in the ROM.

    Strides are in bits, 0 meaning "packed". ``major_stride_bits`` is the step
    from one element to the next along a line (a row, or a column when
    ``column_major``); ``minor_stride_bits`` is the step from the last element
    of a line to the first of the next. This matches TunerPro, checked against
    a real XDF and bin with a gap between rows.
    """

    address: int
    bits: int = 8
    signed: bool = False
    lsb_first: bool = False
    is_float: bool = False
    rows: int = 1
    cols: int = 1
    column_major: bool = False
    major_stride_bits: int = 0
    minor_stride_bits: int = 0

    def __post_init__(self) -> None:
        if self.bits not in (8, 16, 24, 32, 64):
            raise DefinitionError(f"element size {self.bits} bits is not supported")
        if self.is_float and self.bits not in (32, 64):
            raise DefinitionError("float data must be 32 or 64 bits")
        if self.rows < 1 or self.cols < 1:
            raise DefinitionError("rows and cols must be at least 1")
        if self.address < 0:
            raise DefinitionError("address must not be negative")
        for stride in (self.major_stride_bits, self.minor_stride_bits):
            if stride % 8:
                raise DefinitionError("strides must be whole bytes")

    @property
    def element_bytes(self) -> int:
        return self.bits // 8

    @property
    def count(self) -> int:
        return self.rows * self.cols

    def offsets(self) -> np.ndarray:
        """Byte offset of every element's first byte, shape (rows, cols)."""
        major = (self.major_stride_bits // 8) or self.element_bytes
        minor = (self.minor_stride_bits // 8) or major
        r, c = np.meshgrid(np.arange(self.rows), np.arange(self.cols), indexing="ij")
        if self.column_major:
            line, i, n = c, r, self.rows
        else:
            line, i, n = r, c, self.cols
        return self.address + line * ((n - 1) * major + minor) + i * major

    def byte_offsets(self) -> np.ndarray:
        """Every byte the block occupies, sorted and unique."""
        offs = self.offsets().ravel()[:, None] + np.arange(self.element_bytes)[None, :]
        return np.unique(offs)

    def ranges(self) -> list[tuple[int, int]]:
        """Occupied bytes as merged half-open (start, end) ranges."""
        offs = self.byte_offsets()
        if offs.size == 0:
            return []
        breaks = np.nonzero(np.diff(offs) != 1)[0]
        starts = np.concatenate(([offs[0]], offs[breaks + 1]))
        ends = np.concatenate((offs[breaks], [offs[-1]])) + 1
        return [(int(s), int(e)) for s, e in zip(starts, ends)]

    @property
    def end(self) -> int:
        return int(self.byte_offsets()[-1]) + 1

    def check_fits(self, size: int, base: int = 0) -> None:
        offs = self.byte_offsets() + base
        if offs[0] < 0 or offs[-1] >= size:
            raise DefinitionError(
                f"data at 0x{self.address:X} runs outside the {size}-byte ROM")

    def read(self, data: bytes | bytearray, base: int = 0) -> np.ndarray:
        """Raw numbers, shape (rows, cols); int64, or float64 for float data."""
        self.check_fits(len(data), base)
        n = self.element_bytes
        buf = np.frombuffer(bytes(data), dtype=np.uint8)
        idx = (self.offsets() + base)[..., None] + np.arange(n)
        chunk = buf[idx]                                  # (rows, cols, n)
        if self.lsb_first:
            chunk = chunk[..., ::-1]                      # now big-endian
        if self.is_float:
            dtype = ">f4" if n == 4 else ">f8"
            return np.ascontiguousarray(chunk).view(dtype)[..., 0].astype(float)
        value = np.zeros(chunk.shape[:-1], dtype=np.int64)
        for k in range(n):
            value = (value << 8) | chunk[..., k].astype(np.int64)
        if self.signed:
            sign = 1 << (self.bits - 1)
            value = np.where(value & sign, value - (1 << self.bits), value)
        return value

    def encode(self, raw: np.ndarray) -> np.ndarray:
        """Raw numbers -> bytes in ROM order, shape (rows, cols, element_bytes)."""
        raw = np.asarray(raw)
        if raw.shape != (self.rows, self.cols):
            raise DefinitionError(
                f"expected shape {(self.rows, self.cols)}, got {raw.shape}")
        n = self.element_bytes
        if self.is_float:
            dtype = ">f4" if n == 4 else ">f8"
            chunk = np.ascontiguousarray(raw.astype(dtype)).view(np.uint8)
            chunk = chunk.reshape(self.rows, self.cols, n)
        else:
            ints = np.asarray(raw, dtype=np.int64)
            lo = -(1 << (self.bits - 1)) if self.signed else 0
            hi = (1 << (self.bits - 1)) - 1 if self.signed else (1 << self.bits) - 1
            if ints.min() < lo or ints.max() > hi:
                raise DefinitionError(
                    f"raw value out of range {lo}..{hi} for {self.bits}-bit data")
            ints = ints & ((1 << self.bits) - 1)
            chunk = np.empty((self.rows, self.cols, n), dtype=np.uint8)
            for k in range(n):
                chunk[..., k] = (ints >> (8 * (n - 1 - k))) & 0xFF
        return chunk[..., ::-1] if self.lsb_first else chunk

    def changes(self, data: bytes | bytearray, raw: np.ndarray,
                base: int = 0) -> list[tuple[int, bytes]]:
        """Byte writes needed to store ``raw``; only bytes that differ."""
        self.check_fits(len(data), base)
        chunk = self.encode(raw)
        idx = ((self.offsets() + base)[..., None] + np.arange(self.element_bytes)).ravel()
        new = chunk.ravel()
        old = np.frombuffer(bytes(data), dtype=np.uint8)[idx]
        out: dict[int, int] = {}
        for off, val in zip(idx[old != new].tolist(), new[old != new].tolist()):
            out[off] = val
        return _merge_bytes(out)

    def write(self, data: bytearray, raw: np.ndarray, base: int = 0) -> None:
        for off, blob in self.changes(data, raw, base):
            data[off:off + len(blob)] = blob


def _merge_bytes(by_offset: dict[int, int]) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytearray]] = []
    for off in sorted(by_offset):
        if out and out[-1][0] + len(out[-1][1]) == off:
            out[-1][1].append(by_offset[off])
        else:
            out.append((off, bytearray([by_offset[off]])))
    return [(off, bytes(blob)) for off, blob in out]


# ---- items ------------------------------------------------------------------

@dataclass
class MathVar:
    """An extra variable in an equation, read from the ROM or another item."""

    name: str
    kind: str = "address"                 # "address" or "link"
    storage: Storage | None = None        # for kind == "address"
    link: str = ""                        # for kind == "link": item id / uniqueid


@dataclass
class Axis:
    """One axis of a table. For X and Y: breakpoints. For Z: the cells."""

    storage: Storage | None = None
    units: str = ""
    decimals: int | None = None
    math: str = "X"
    vars: list[MathVar] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    count: int = 0
    link: str = ""                        # another item supplies the values
    min: float | None = None
    max: float | None = None
    output_type: int | None = None        # XDF outputtype, kept for round trip

    def __post_init__(self) -> None:
        if self.storage is not None and not self.count:
            self.count = self.storage.count
        if not self.count and self.labels:
            self.count = len(self.labels)

    def conversion(self, variables: dict[str, float] | None = None) -> Conversion:
        st = self.storage
        return Conversion(self.math, bits=st.bits if st else 16,
                          signed=st.signed if st else True,
                          is_float=st.is_float if st else False,
                          variables=variables)


@dataclass
class Item:
    id: str
    title: str = ""
    description: str = ""
    categories: list[str] = field(default_factory=list)
    uniqueid: str = ""                    # XDF uniqueid, kept for round trip
    flags: int = 0                        # XDF flags attribute
    notes: list[str] = field(default_factory=list)   # parser warnings

    def storages(self) -> list[Storage]:
        return []

    def ranges(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for st in self.storages():
            out.extend(st.ranges())
        return sorted(out)

    @property
    def kind(self) -> str:
        return type(self).__name__.lower()


@dataclass
class Table(Item):
    x: Axis = field(default_factory=Axis)
    y: Axis = field(default_factory=Axis)
    z: Axis = field(default_factory=Axis)
    curve: bool = False                   # XDFFUNCTION: one row, X breakpoints

    def __post_init__(self) -> None:
        if self.z.storage is None:
            raise DefinitionError(f"table {self.id!r} has no cell data address")

    @property
    def rows(self) -> int:
        return self.z.storage.rows        # type: ignore[union-attr]

    @property
    def cols(self) -> int:
        return self.z.storage.cols        # type: ignore[union-attr]

    def storages(self) -> list[Storage]:
        return [a.storage for a in (self.z, self.x, self.y) if a.storage is not None]


@dataclass
class Scalar(Item):
    storage: Storage = field(default_factory=lambda: Storage(0))
    math: str = "X"
    vars: list[MathVar] = field(default_factory=list)
    units: str = ""
    decimals: int | None = None
    min: float | None = None
    max: float | None = None

    def storages(self) -> list[Storage]:
        return [self.storage]

    def conversion(self, variables: dict[str, float] | None = None) -> Conversion:
        return Conversion(self.math, bits=self.storage.bits, signed=self.storage.signed,
                          is_float=self.storage.is_float, variables=variables)


@dataclass
class Flag(Item):
    storage: Storage = field(default_factory=lambda: Storage(0))
    mask: int = 0x01

    def storages(self) -> list[Storage]:
        return [self.storage]


@dataclass
class PatchEntry:
    address: int
    data: bytes
    base: bytes | None = None             # bytes expected before patching
    name: str = ""


@dataclass
class Patch(Item):
    entries: list[PatchEntry] = field(default_factory=list)

    def ranges(self) -> list[tuple[int, int]]:
        return sorted((e.address, e.address + len(e.data)) for e in self.entries)


@dataclass
class ChecksumSpec:
    """How the ROM's own checksum works, if it has one.

    kind:  "none", "sum8", "sum16", "xor8"
    mode:  "balance" - the stored value is chosen so the checksum over
                       [start, end) *including* the stored bytes equals ``target``.
           "store"   - the stored value is the checksum over [start, end)
                       *excluding* the stored bytes.
    disabled: the base ROM's checksum routine is patched out (as Crome does),
              so nothing needs to be recomputed.
    status: "verified" or "unverified" - whether anyone has confirmed this
            rule against the real ECU.
    """

    kind: str = "none"
    start: int = 0
    end: int = 0
    store: int = 0
    store_bits: int = 8
    lsb_first: bool = False
    mode: str = "balance"
    target: int = 0
    disabled: bool = False
    status: str = "unverified"
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("none", "sum8", "sum16", "xor8"):
            raise DefinitionError(f"unknown checksum kind {self.kind!r}")
        if self.mode not in ("balance", "store"):
            raise DefinitionError(f"unknown checksum mode {self.mode!r}")
        if self.kind == "sum16":
            self.store_bits = 16
        elif self.kind in ("sum8", "xor8"):
            self.store_bits = 8

    @property
    def active(self) -> bool:
        return self.kind != "none" and not self.disabled


@dataclass
class Signature:
    """Bytes that must be present for the ROM to match this definition."""

    address: int
    data: bytes
    name: str = ""


@dataclass
class Category:
    index: int
    name: str


@dataclass
class Defaults:
    bits: int = 8
    signed: bool = False
    lsb_first: bool = False
    is_float: bool = False
    sigdigits: int = 2


@dataclass
class Definition:
    title: str = ""
    description: str = ""
    author: str = ""
    version: str = ""
    base_offset: int = 0                  # file offset = address + base_offset
    rom_size: int = 0x8000
    defaults: Defaults = field(default_factory=Defaults)
    categories: list[Category] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    scalars: list[Scalar] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    checksum: ChecksumSpec = field(default_factory=ChecksumSpec)
    signatures: list[Signature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    # -- lookup --

    def items(self) -> Iterator[Item]:
        yield from self.tables
        yield from self.scalars
        yield from self.flags
        yield from self.patches

    def item(self, key: str) -> Item:
        """Find by id, then XDF uniqueid, then exact title."""
        for attr in ("id", "uniqueid", "title"):
            for it in self.items():
                if key and getattr(it, attr) == key:
                    return it
        raise KeyError(f"the definition has no item named {key!r}")

    def table(self, key: str) -> Table:
        it = self.item(key)
        if not isinstance(it, Table):
            raise KeyError(f"{key!r} is a {it.kind}, not a table")
        return it

    def scalar(self, key: str) -> Scalar:
        it = self.item(key)
        if not isinstance(it, Scalar):
            raise KeyError(f"{key!r} is a {it.kind}, not a scalar")
        return it

    def flag(self, key: str) -> Flag:
        it = self.item(key)
        if not isinstance(it, Flag):
            raise KeyError(f"{key!r} is a {it.kind}, not a flag")
        return it

    def patch(self, key: str) -> Patch:
        it = self.item(key)
        if not isinstance(it, Patch):
            raise KeyError(f"{key!r} is a {it.kind}, not a patch")
        return it

    def by_category(self) -> dict[str, list[Item]]:
        out: dict[str, list[Item]] = {}
        for it in self.items():
            for cat in it.categories or ["(none)"]:
                out.setdefault(cat, []).append(it)
        return out

    def owner_map(self, size: int | None = None) -> list[Item | None]:
        """For every ROM byte, the item that owns it (first one wins)."""
        size = size or self.rom_size
        owners: list[Item | None] = [None] * size
        for it in self.items():
            if isinstance(it, Patch):
                continue                  # patches overlap code, not calibration
            for start, end in it.ranges():
                for off in range(max(0, start + self.base_offset),
                                 min(size, end + self.base_offset)):
                    if owners[off] is None:
                        owners[off] = it
        return owners

    def validate(self, size: int | None = None) -> list[str]:
        """Problems that would stop this definition working on a ROM of ``size``."""
        size = size or self.rom_size
        problems: list[str] = []
        seen: set[str] = set()
        for it in self.items():
            if it.id in seen:
                problems.append(f"duplicate id {it.id!r}")
            seen.add(it.id)
            for start, end in it.ranges():
                if start + self.base_offset < 0 or end + self.base_offset > size:
                    problems.append(
                        f"{it.id}: 0x{start:X}-0x{end - 1:X} is outside the ROM")
            if isinstance(it, Table):
                for name, axis, n in (("x", it.x, it.cols), ("y", it.y, it.rows)):
                    if axis.count and axis.count != n:
                        problems.append(
                            f"{it.id}: {name} axis has {axis.count} points, "
                            f"table has {n}")
                for axis in (it.x, it.y, it.z):
                    try:
                        axis.conversion({v.name: 1.0 for v in axis.vars})
                    except MathError as exc:
                        problems.append(f"{it.id}: {exc}")
        cs = self.checksum
        if cs.kind != "none":
            if not 0 <= cs.start < cs.end <= size:
                problems.append("checksum range is outside the ROM")
            if not 0 <= cs.store <= size - cs.store_bits // 8:
                problems.append("checksum store address is outside the ROM")
        return problems

    # -- TOML --

    def to_toml(self) -> str:
        return dumps_toml(_definition_to_dict(self))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_toml(), encoding="utf-8")


def slugify(text: str, taken: set[str] | None = None) -> str:
    """A stable id from a title: lower-case, underscores, unique in ``taken``."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"
    if slug[0].isdigit():
        slug = "t_" + slug
    if taken is not None:
        base, n = slug, 2
        while slug in taken:
            slug = f"{base}_{n}"
            n += 1
        taken.add(slug)
    return slug


# ---- TOML writing -----------------------------------------------------------

_HEX_KEYS = {"address", "start", "end", "store", "mask", "base_offset", "rom_size"}


def _toml_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"0x{value:X}" if key in _HEX_KEYS and value >= 0 else str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise DefinitionError(f"{key}: cannot store {value}")
        return repr(value)
    if isinstance(value, str):
        escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
        escaped = "".join(c if 0x20 <= ord(c) != 0x7F else f"\\u{ord(c):04X}"
                          for c in escaped)
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(key, v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(k, v)}"
                                for k, v in value.items()) + " }"
    raise DefinitionError(f"{key}: cannot write {type(value).__name__} to TOML")


def dumps_toml(doc: dict[str, Any]) -> str:
    """A small TOML writer: scalars, arrays, tables and arrays of tables."""
    lines: list[str] = []

    def is_table_list(v: Any) -> bool:
        return isinstance(v, list) and bool(v) and all(isinstance(i, dict) for i in v)

    def emit(table: dict[str, Any], path: str) -> None:
        for key, value in table.items():
            if isinstance(value, dict) or is_table_list(value) or value is None \
                    or value == []:
                continue
            lines.append(f"{key} = {_toml_value(key, value)}")
        for key, value in table.items():
            name = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                lines.append("")
                lines.append(f"[{name}]")
                emit(value, name)
            elif is_table_list(value):
                for entry in value:
                    lines.append("")
                    lines.append(f"[[{name}]]")
                    emit(entry, name)

    emit(doc, "")
    return "\n".join(lines).strip("\n") + "\n"


def _storage_to_dict(st: Storage, shape: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"address": st.address, "bits": st.bits}
    if shape:
        out["rows"], out["cols"] = st.rows, st.cols
    for key, value in (("signed", st.signed), ("lsb_first", st.lsb_first),
                       ("float", st.is_float), ("column_major", st.column_major)):
        if value:
            out[key] = True
    if st.major_stride_bits:
        out["major_stride_bits"] = st.major_stride_bits
    if st.minor_stride_bits:
        out["minor_stride_bits"] = st.minor_stride_bits
    return out


def _vars_to_list(vars_: list[MathVar]) -> list[dict[str, Any]]:
    out = []
    for v in vars_:
        d: dict[str, Any] = {"name": v.name, "kind": v.kind}
        if v.kind == "link":
            d["link"] = v.link
        elif v.storage is not None:
            d.update(_storage_to_dict(v.storage, False))
        out.append(d)
    return out


def _axis_to_dict(axis: Axis, is_z: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if axis.storage is not None:
        out.update(_storage_to_dict(axis.storage, is_z))
    if not is_z:
        out["count"] = axis.count
    if axis.units:
        out["units"] = axis.units
    if axis.decimals is not None:
        out["decimals"] = axis.decimals
    if axis.math.strip().upper() != "X":
        out["math"] = axis.math
    if axis.vars:
        out["vars"] = _vars_to_list(axis.vars)
    if axis.labels:
        out["labels"] = list(axis.labels)
    if axis.link:
        out["link"] = axis.link
    if axis.min is not None:
        out["min"] = float(axis.min)
    if axis.max is not None:
        out["max"] = float(axis.max)
    if axis.output_type is not None:
        out["output_type"] = axis.output_type
    return out


def _item_head(it: Item) -> dict[str, Any]:
    out: dict[str, Any] = {"id": it.id, "title": it.title}
    if it.description:
        out["description"] = it.description
    if it.categories:
        out["categories"] = list(it.categories)
    if it.uniqueid:
        out["uniqueid"] = it.uniqueid
    if it.flags:
        out["flags"] = it.flags
    return out


def _definition_to_dict(d: Definition) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": FORMAT_NAME, "format_version": FORMAT_VERSION,
        "title": d.title, "description": d.description, "author": d.author,
        "version": d.version, "base_offset": d.base_offset, "rom_size": d.rom_size,
    }
    doc["defaults"] = {"bits": d.defaults.bits, "signed": d.defaults.signed,
                       "lsb_first": d.defaults.lsb_first, "float": d.defaults.is_float,
                       "sigdigits": d.defaults.sigdigits}
    cs = d.checksum
    doc["checksum"] = {"kind": cs.kind, "start": cs.start, "end": cs.end,
                       "store": cs.store, "lsb_first": cs.lsb_first, "mode": cs.mode,
                       "target": cs.target, "disabled": cs.disabled,
                       "status": cs.status, "note": cs.note}
    doc["category"] = [{"index": c.index, "name": c.name} for c in d.categories]
    doc["signature"] = [{"address": s.address, "data": s.data.hex(), "name": s.name}
                        for s in d.signatures]
    tables = []
    for t in d.tables:
        entry = _item_head(t)
        if t.curve:
            entry["curve"] = True
        entry["x"] = _axis_to_dict(t.x, False)
        entry["y"] = _axis_to_dict(t.y, False)
        entry["z"] = _axis_to_dict(t.z, True)
        tables.append(entry)
    doc["table"] = tables
    scalars = []
    for s in d.scalars:
        entry = _item_head(s)
        entry.update(_storage_to_dict(s.storage, False))
        if s.math.strip().upper() != "X":
            entry["math"] = s.math
        if s.vars:
            entry["vars"] = _vars_to_list(s.vars)
        if s.units:
            entry["units"] = s.units
        if s.decimals is not None:
            entry["decimals"] = s.decimals
        if s.min is not None:
            entry["min"] = float(s.min)
        if s.max is not None:
            entry["max"] = float(s.max)
        scalars.append(entry)
    doc["scalar"] = scalars
    flags = []
    for f in d.flags:
        entry = _item_head(f)
        entry.update(_storage_to_dict(f.storage, False))
        entry["mask"] = f.mask
        flags.append(entry)
    doc["flag"] = flags
    patches = []
    for p in d.patches:
        entry = _item_head(p)
        entry["entries"] = [
            {k: v for k, v in (("address", e.address), ("data", e.data.hex()),
                               ("base", e.base.hex() if e.base is not None else None),
                               ("name", e.name)) if v not in (None, "")}
            for e in p.entries]
        patches.append(entry)
    doc["patch"] = patches
    return doc


# ---- TOML reading -----------------------------------------------------------

def _storage_from_dict(d: dict[str, Any], defaults: Defaults, *, rows: int = 1,
                       cols: int = 1, where: str = "") -> Storage:
    if "address" not in d:
        raise DefinitionError(f"{where}: needs an address")
    try:
        return Storage(
            address=int(d["address"]), bits=int(d.get("bits", defaults.bits)),
            signed=bool(d.get("signed", defaults.signed)),
            lsb_first=bool(d.get("lsb_first", defaults.lsb_first)),
            is_float=bool(d.get("float", defaults.is_float)),
            rows=int(d.get("rows", rows)), cols=int(d.get("cols", cols)),
            column_major=bool(d.get("column_major", False)),
            major_stride_bits=int(d.get("major_stride_bits", 0)),
            minor_stride_bits=int(d.get("minor_stride_bits", 0)))
    except DefinitionError as exc:
        raise DefinitionError(f"{where}: {exc}") from exc


def _vars_from_list(items: list[dict[str, Any]], defaults: Defaults,
                    where: str) -> list[MathVar]:
    out = []
    for v in items:
        kind = v.get("kind", "address")
        if kind == "link":
            out.append(MathVar(str(v["name"]), "link", None, str(v.get("link", ""))))
        else:
            out.append(MathVar(str(v["name"]), "address",
                               _storage_from_dict(v, defaults, where=where)))
    return out


def _axis_from_dict(d: dict[str, Any], defaults: Defaults, where: str,
                    is_z: bool) -> Axis:
    storage = None
    if "address" in d:
        count = int(d.get("count", 1))
        storage = _storage_from_dict(d, defaults, rows=1, cols=1 if is_z else count,
                                     where=where)
    return Axis(storage=storage, units=str(d.get("units", "")),
                decimals=d.get("decimals"), math=str(d.get("math", "X")),
                vars=_vars_from_list(d.get("vars", []), defaults, where),
                labels=[str(v) for v in d.get("labels", [])],
                count=int(d.get("count", 0)), link=str(d.get("link", "")),
                min=d.get("min"), max=d.get("max"), output_type=d.get("output_type"))


def _head_from_dict(d: dict[str, Any], taken: set[str]) -> dict[str, Any]:
    title = str(d.get("title", ""))
    ident = str(d.get("id", "")) or slugify(title)
    if ident in taken:
        raise DefinitionError(f"duplicate id {ident!r}")
    taken.add(ident)
    return {"id": ident, "title": title or ident,
            "description": str(d.get("description", "")),
            "categories": [str(c) for c in d.get("categories", [])],
            "uniqueid": str(d.get("uniqueid", "")), "flags": int(d.get("flags", 0))}


def definition_from_dict(doc: dict[str, Any], source: str = "") -> Definition:
    if doc.get("format") != FORMAT_NAME:
        raise DefinitionError(
            f"not a hondalog definition (format = {doc.get('format')!r})")
    if int(doc.get("format_version", 1)) > FORMAT_VERSION:
        raise DefinitionError("this definition was written by a newer hondalog")
    dd = doc.get("defaults", {})
    defaults = Defaults(int(dd.get("bits", 8)), bool(dd.get("signed", False)),
                        bool(dd.get("lsb_first", False)), bool(dd.get("float", False)),
                        int(dd.get("sigdigits", 2)))
    d = Definition(title=str(doc.get("title", "")),
                   description=str(doc.get("description", "")),
                   author=str(doc.get("author", "")), version=str(doc.get("version", "")),
                   base_offset=int(doc.get("base_offset", 0)),
                   rom_size=int(doc.get("rom_size", 0x8000)), defaults=defaults,
                   source=source)
    cs = doc.get("checksum", {})
    try:
        d.checksum = ChecksumSpec(
            kind=str(cs.get("kind", "none")), start=int(cs.get("start", 0)),
            end=int(cs.get("end", 0)), store=int(cs.get("store", 0)),
            lsb_first=bool(cs.get("lsb_first", False)), mode=str(cs.get("mode", "balance")),
            target=int(cs.get("target", 0)), disabled=bool(cs.get("disabled", False)),
            status=str(cs.get("status", "unverified")), note=str(cs.get("note", "")))
    except DefinitionError as exc:
        raise DefinitionError(f"[checksum]: {exc}") from exc
    d.categories = [Category(int(c["index"]), str(c["name"]))
                    for c in doc.get("category", [])]
    for s in doc.get("signature", []):
        try:
            d.signatures.append(Signature(int(s["address"]), bytes.fromhex(s["data"]),
                                          str(s.get("name", ""))))
        except (KeyError, ValueError) as exc:
            raise DefinitionError(f"[[signature]]: {exc}") from exc
    taken: set[str] = set()
    for t in doc.get("table", []):
        head = _head_from_dict(t, taken)
        where = f"table {head['id']}"
        z = t.get("z")
        if not isinstance(z, dict):
            raise DefinitionError(f"{where}: needs a [table.z] section")
        zaxis = _axis_from_dict(z, defaults, where + ".z", True)
        zaxis.storage = _storage_from_dict(z, defaults, where=where + ".z")
        table = Table(**head, x=_axis_from_dict(t.get("x", {}), defaults, where + ".x", False),
                      y=_axis_from_dict(t.get("y", {}), defaults, where + ".y", False),
                      z=zaxis, curve=bool(t.get("curve", False)))
        table.x.count = table.x.count or table.cols
        table.y.count = table.y.count or table.rows
        d.tables.append(table)
    for s in doc.get("scalar", []):
        head = _head_from_dict(s, taken)
        where = f"scalar {head['id']}"
        d.scalars.append(Scalar(
            **head, storage=_storage_from_dict(s, defaults, where=where),
            math=str(s.get("math", "X")),
            vars=_vars_from_list(s.get("vars", []), defaults, where),
            units=str(s.get("units", "")), decimals=s.get("decimals"),
            min=s.get("min"), max=s.get("max")))
    for f in doc.get("flag", []):
        head = _head_from_dict(f, taken)
        d.flags.append(Flag(**head,
                            storage=_storage_from_dict(f, defaults, where=f"flag {head['id']}"),
                            mask=int(f.get("mask", 1))))
    for p in doc.get("patch", []):
        head = _head_from_dict(p, taken)
        entries = []
        for e in p.get("entries", []):
            try:
                entries.append(PatchEntry(
                    int(e["address"]), bytes.fromhex(e["data"]),
                    bytes.fromhex(e["base"]) if "base" in e else None,
                    str(e.get("name", ""))))
            except (KeyError, ValueError) as exc:
                raise DefinitionError(f"patch {head['id']}: {exc}") from exc
        d.patches.append(Patch(**head, entries=entries))
    return d


def loads_definition(text: str, source: str = "") -> Definition:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError(f"{source or 'definition'}: {exc}") from exc
    return definition_from_dict(doc, source)


def load_definition(path: str | Path) -> Definition:
    """Load a definition from ``.toml`` (native) or ``.xdf`` (TunerPro)."""
    path = Path(path)
    if path.suffix.lower() == ".xdf":
        from .xdf import load_xdf
        return load_xdf(path)
    return loads_definition(path.read_text(encoding="utf-8"), str(path))
