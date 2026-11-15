"""MegaLogViewer binary logs (``.mlg``, "MLVLG"), writer and reader.

Layout per EFI Analytics' "MLG Binary Log Format 2.0" (all big-endian):

    header v2 (24 bytes)   "MLVLG\\0" | u16 version | u32 unix time | u32 info data start
                           | u32 data begin index | u16 record length | u16 field count
    header v1 (22 bytes)   the same, but info data start is u16
    field (v2: 89 bytes)   u8 type | 34s name | 10s units | u8 display style
                           | f32 scale | f32 transform | i8 digits | 34s category
    field (v1: 55 bytes)   the same without the category
    data block             u8 0 | u8 counter | u16 timestamp (10 us units, wraps)
                           | record | u8 sum of the record bytes
    marker block           u8 1 | u8 counter | u16 timestamp | 50s message

    display value = (raw + transform) * scale

The v2 layout was checked against the published specification. The v1
layout (22-byte header, 55-byte fields) is not in that document; it follows
the description in third-party parsers, so treat v1 as "believed right".

The 16-bit block timestamp wraps every 0.655 s, so like TunerStudio we also
write a ``Time`` field in seconds; the reader prefers it when present.
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..data.model import Column, Event, StreamInfo, Table
from ..data.reader import Log, Stream

MAGIC = b"MLVLG\x00"
HEADER_V2 = struct.Struct(">6sHIIIHH")
HEADER_V1 = struct.Struct(">6sHIHIHH")
FIELD_V1 = struct.Struct(">B34s10sBffb")
FIELD_V2 = struct.Struct(">B34s10sBffb34s")
MARKER_LEN = 54
MARKER_TEXT = 50

# type code -> (numpy big-endian dtype, size)
TYPES = {0: ">u1", 1: ">i1", 2: ">u2", 3: ">i2", 4: ">u4", 5: ">i4", 6: ">i8", 7: ">f4",
         10: ">u1", 11: ">u2", 12: ">u4"}       # 10-12 are bit fields
U08, S08, U16, S16, U32, S32, S64, F32 = range(8)
STYLE_FLOAT, STYLE_ON_OFF = 0, 4
_INT_RANGE = {U08: (0, 255), S16: (-32768, 32767), U16: (0, 65535)}


class MlgError(ValueError):
    pass


@dataclass
class MlgField:
    name: str
    units: str = ""
    type: int = F32
    style: int = STYLE_FLOAT
    scale: float = 1.0
    transform: float = 0.0
    digits: int = 2
    category: str = ""

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(TYPES[self.type])


def ascii_text(text: str, limit: int) -> bytes:
    """Null-padded ASCII of at most ``limit - 1`` characters."""
    text = text.replace("°", "deg").replace("λ", "lambda").replace("µ", "u")
    raw = text.encode("ascii", "replace")[:limit - 1]
    return raw.ljust(limit, b"\x00")


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def _fill(values: np.ndarray, how: str) -> np.ndarray:
    """Replace NaN: "hold" repeats the last good value (0 before the first)."""
    bad = np.isnan(values)
    if how == "keep" or not bad.any():
        return values
    if how == "zero":
        return np.where(bad, 0.0, values)
    idx = np.where(~bad, np.arange(len(values)), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, values[np.clip(idx, 0, None)], 0.0)


def plan_fields(table: Table, columns: dict[str, Column] | None = None, compact: bool = False,
                category: str = "") -> list[MlgField]:
    """Choose a field type per channel.

    Default: every value channel is a 32-bit float (lossless). ``compact``
    stores a channel as a scaled 16-bit integer when its values fit at the
    channel's display precision, which roughly halves the file.
    """
    columns = columns or {}
    fields = [MlgField("Time", "s", F32, digits=3, category=category)]
    for name in table.names:
        col = columns.get(name) or Column(name)
        values = table[name]
        field = MlgField(name, table.units.get(name, col.units), F32, digits=col.decimals,
                         category=category)
        clean = not np.isnan(values).any()
        if col.kind == "flag" and clean and len(values) and values.min() >= 0 and values.max() <= 1:
            field.type, field.style, field.digits = U08, STYLE_ON_OFF, 0
        elif compact and clean and len(values):
            scale = 10.0 ** -max(0, col.decimals)
            raw = np.round(values / scale)
            for code in (S16, U16):
                lo, hi = _INT_RANGE[code]
                if raw.min() >= lo and raw.max() <= hi:
                    field.type, field.scale = code, scale
                    break
        fields.append(field)
    return fields


def write_mlg(path: str | os.PathLike, table: Table, markers: Sequence[tuple[float, str]] = (), *,
              version: int = 2, columns: dict[str, Column] | None = None, info: str = "",
              timestamp: float | None = None, compact: bool = False, fill: str = "hold",
              category: str = "") -> Path:
    """Write a table (one row per record) and its markers as an MLG file."""
    if version not in (1, 2):
        raise MlgError("MLG version must be 1 or 2")
    path = Path(path)
    fields = plan_fields(table, columns, compact, category)
    n = len(table)
    rec_dtype = np.dtype([(f"f{i}", f.dtype) for i, f in enumerate(fields)])
    block_dtype = np.dtype([("type", "u1"), ("counter", "u1"), ("stamp", ">u2"),
                            ("rec", rec_dtype), ("crc", "u1")])
    blocks = np.zeros(n, dtype=block_dtype)
    t = table.t - (table.t[0] if n else 0.0)
    blocks["stamp"] = (np.round(t * 1e5).astype(np.int64) % 65536).astype(np.uint16)
    rec = blocks["rec"]
    rec["f0"] = t
    for i, field in enumerate(fields[1:], start=1):
        values = _fill(table[field.name], fill)
        if field.type != F32:
            values = np.round(values / field.scale - field.transform)
        rec[f"f{i}"] = values

    # markers go in front of the first record at or after their time
    slots = (np.searchsorted(table.t, [m[0] for m in markers], side="left") if n
             else np.zeros(len(markers), dtype=np.intp))
    order = np.argsort(slots, kind="stable")
    total = n + len(markers)
    counters = np.arange(total, dtype=np.int64) % 256
    is_marker = np.zeros(total, dtype=bool)
    marker_pos = [int(slots[j]) + k for k, j in enumerate(order)]
    is_marker[marker_pos] = True
    blocks["counter"] = counters[~is_marker]
    raw = blocks.view(np.uint8).reshape(n, block_dtype.itemsize) if n else np.zeros((0, block_dtype.itemsize), np.uint8)
    if n:
        raw[:, -1] = raw[:, 4:-1].sum(axis=1, dtype=np.uint64) & 0xFF

    info_bytes = info.encode("ascii", "replace") + b"\x00" if info else b""
    field_struct = FIELD_V2 if version == 2 else FIELD_V1
    head_size = (HEADER_V2 if version == 2 else HEADER_V1).size
    info_start = head_size + field_struct.size * len(fields) if info_bytes else 0
    data_begin = head_size + field_struct.size * len(fields) + len(info_bytes)
    if version == 1 and info_start > 0xFFFF:
        raise MlgError("too many fields for an MLG v1 info block; use version 2")
    stamp = int(time.time() if timestamp is None else timestamp) & 0xFFFFFFFF
    header = (HEADER_V2 if version == 2 else HEADER_V1).pack(
        MAGIC, version, stamp, info_start, data_begin, rec_dtype.itemsize, len(fields))

    with open(path, "wb") as fh:
        fh.write(header)
        for field in fields:
            args = [field.type, ascii_text(field.name, 34), ascii_text(field.units, 10),
                    field.style, field.scale, field.transform, field.digits]
            if version == 2:
                args.append(ascii_text(field.category, 34))
            fh.write(field_struct.pack(*args))
        fh.write(info_bytes)
        if not len(markers):
            fh.write(raw.tobytes())
        else:
            at = 0
            for k, j in enumerate(order):
                slot = int(slots[j])
                fh.write(raw[at:slot].tobytes())
                at = slot
                mt, text = markers[j]
                stamp16 = int(round((mt - (table.t[0] if n else 0.0)) * 1e5)) % 65536
                fh.write(struct.pack(">BBH", 1, int(counters[marker_pos[k]]), stamp16)
                         + ascii_text(text, MARKER_TEXT))
            fh.write(raw[at:].tobytes())
    return path


@dataclass
class MlgFile:
    """A parsed MLG file. ``data`` maps field name to display values."""

    version: int
    timestamp: int
    info: str
    fields: list[MlgField]
    t: np.ndarray                       # seconds
    data: dict[str, np.ndarray]
    markers: list[tuple[float, str]]
    crc_errors: int
    record_length: int
    data_begin: int


def parse_mlg(blob: bytes) -> MlgFile:
    """Decode an MLG file held in memory. Raises MlgError if it is not one."""
    if len(blob) < HEADER_V1.size or blob[:6] != MAGIC:
        raise MlgError("not an MLG file (no MLVLG header)")
    version = struct.unpack_from(">H", blob, 6)[0]
    if version == 1:
        _, _, stamp, info_start, data_begin, rec_len, count = HEADER_V1.unpack_from(blob, 0)
        head, fstruct = HEADER_V1.size, FIELD_V1
    elif version == 2:
        _, _, stamp, info_start, data_begin, rec_len, count = HEADER_V2.unpack_from(blob, 0)
        head, fstruct = HEADER_V2.size, FIELD_V2
    else:
        raise MlgError(f"unsupported MLG version {version}")
    if head + fstruct.size * count > len(blob) or data_begin > len(blob):
        raise MlgError("MLG header points past the end of the file")

    fields: list[MlgField] = []
    for i in range(count):
        parts = fstruct.unpack_from(blob, head + i * fstruct.size)
        ftype = parts[0]
        if ftype not in TYPES:
            raise MlgError(f"field {i}: unknown type code {ftype}")
        if ftype >= 10:                 # bit field: the scale/transform bytes mean something else
            fields.append(MlgField(_cstr(parts[1]) or f"bits{i}", _cstr(parts[2]), ftype, parts[3],
                                   1.0, 0.0, 0, _cstr(parts[7]) if version == 2 else ""))
        else:
            fields.append(MlgField(_cstr(parts[1]), _cstr(parts[2]), ftype, parts[3], parts[4],
                                   parts[5], parts[6], _cstr(parts[7]) if version == 2 else ""))
    rec_dtype = np.dtype([(f"f{i}", f.dtype) for i, f in enumerate(fields)])
    if rec_dtype.itemsize != rec_len:
        raise MlgError(f"record length {rec_len} does not match the fields ({rec_dtype.itemsize})")
    info = _cstr(blob[info_start:data_begin]) if info_start else ""

    block_len = rec_len + 5
    offsets: list[int] = []
    marker_at: list[tuple[int, str]] = []       # (records seen so far, text)
    pos, size = data_begin, len(blob)
    while pos < size:
        kind = blob[pos]
        if kind == 0 and pos + block_len <= size:
            offsets.append(pos)
            pos += block_len
        elif kind == 1 and pos + MARKER_LEN <= size:
            marker_at.append((len(offsets), _cstr(blob[pos + 4:pos + MARKER_LEN])))
            pos += MARKER_LEN
        else:
            break                               # truncated or unknown block: stop cleanly
    n = len(offsets)
    block_dtype = np.dtype([("type", "u1"), ("counter", "u1"), ("stamp", ">u2"),
                            ("rec", rec_dtype), ("crc", "u1")])
    flat = np.frombuffer(blob, np.uint8)
    if n:
        index = np.asarray(offsets)[:, None] + np.arange(block_len)[None, :]
        raw = np.ascontiguousarray(flat[index])
        blocks = raw.view(block_dtype).reshape(n)
        crc_errors = int(np.count_nonzero((raw[:, 4:-1].sum(axis=1, dtype=np.uint64) & 0xFF) != raw[:, -1]))
    else:
        blocks = np.zeros(0, block_dtype)
        crc_errors = 0

    data: dict[str, np.ndarray] = {}
    for i, field in enumerate(fields):
        raw_values = blocks["rec"][f"f{i}"].astype(np.float64)
        data[field.name] = (raw_values + field.transform) * field.scale
    if "Time" in data and n:
        t = data["Time"].copy()
    else:                                       # rebuild time from the wrapping 10 us stamps
        stamps = blocks["stamp"].astype(np.int64)
        steps = np.diff(stamps, prepend=stamps[:1])
        steps[steps < 0] += 65536
        t = np.cumsum(steps) * 1e-5
    markers = []
    for seen, text in marker_at:
        when = float(t[min(seen, n - 1)]) if n else 0.0
        markers.append((when, text))
    return MlgFile(version, stamp, info, fields, t, data, markers, crc_errors, rec_len, data_begin)


def read_mlg(path: str | os.PathLike) -> Log:
    """Import an MLG file as a ``Log`` with one stream."""
    with open(path, "rb") as fh:
        mlg = parse_mlg(fh.read())
    fields = [f for f in mlg.fields if f.name != "Time"]
    columns = tuple(Column(f.name, "f8" if f.type in (U32, S32, S64) else "f4", f.units,
                           kind="flag" if f.style >= 4 else "value", decimals=max(0, f.digits))
                    for f in fields)
    info = StreamInfo("mlg", columns, source="import")
    rows = np.zeros(len(mlg.t), dtype=info.dtype)
    rows["t"] = mlg.t
    for f in fields:
        rows[f.name] = mlg.data[f.name]
    meta: dict[str, Any] = {"format": f"MLG v{mlg.version}", "imported_from": str(path)}
    if mlg.info:
        meta["info"] = mlg.info
    if mlg.timestamp:
        meta["started_unix"] = mlg.timestamp
    events = [Event(t, "marker", text) for t, text in mlg.markers]
    return Log(meta, [Stream(info, data=rows)], events, path=path)
