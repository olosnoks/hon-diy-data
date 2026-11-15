"""How the rows of a high-rate stream are packed into one ``chunks.data`` BLOB.

The codec's name is stored next to the BLOB (``chunks.codec``), so a new
codec can be added without touching old rows.

``raw``  the rows as they sit in memory: the stream's packed little-endian
         record (t, the columns, the raw bytes). The recorder writes these
         every commit; they cost no CPU and are what survives a crash.

``d1``   lossless, column by column, then zlib. The recorder turns a
         minute of ``raw`` chunks into one ``d1`` block.

         - time (f8): the second difference of the doubles' bit patterns.
           Evenly spaced times become runs of small numbers.
         - a float column whose every value is a whole number of 1, 1/2,
           1/10, 1/100, 1/1000 or 1/10000 (what a sensor read through an
           ADC and scaled looks like: millivolts, tenths of a degree) is
           stored as those integers. Decoding gives back the identical
           floats; a column that does not fit is stored as it is.
         - integers (those, and integer columns) are stored as (zigzag) differences
           when the signal is smooth, as offsets from the smallest value
           when it is noise.
         - every column is byte-shuffled (all first bytes, all second
           bytes, ...) so zlib sees the slow high bytes together.

Layout of a ``d1`` BLOB before zlib: per field one mode byte, one f8 scale
and one i8 base, then the fields' payloads in order.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

import numpy as np

RAW = "raw"
PACKED = "d1"
CODECS = (RAW, PACKED)

_AS_IS, _DELTA32, _DELTA64, _TIME, _OFFSET32 = 0, 1, 2, 3, 4
_SCALES = (1.0, 2.0, 10.0, 100.0, 1000.0, 10000.0)
_HEAD = struct.Struct("<Bdq")
_I32_MAX = 2 ** 31 - 1
ZLIB_LEVEL = 6


def _shuffle(values: np.ndarray) -> bytes:
    flat = np.ascontiguousarray(values)
    if flat.dtype.itemsize == 1 or len(flat) == 0:
        return flat.tobytes()
    return flat.view(np.uint8).reshape(len(flat), -1).T.tobytes()


def _unshuffle(data: Any, dtype: np.dtype, n: int) -> np.ndarray:
    size = dtype.itemsize
    if size == 1 or n == 0:
        return np.frombuffer(data, dtype, n)
    planes = np.frombuffer(data, np.uint8, n * size).reshape(size, n)
    return np.ascontiguousarray(planes.T).view(dtype).reshape(n)


def _zigzag(x: np.ndarray) -> np.ndarray:
    """Signed -> unsigned with small magnitudes staying small (0, -1, 1, -2 -> 0, 1, 2, 3)."""
    return ((x << 1) ^ (x >> 63)).view(np.uint64)


def _unzigzag(u: np.ndarray) -> np.ndarray:
    x = u.astype(np.uint64)
    return (x >> np.uint64(1)).view(np.int64) ^ -(x & np.uint64(1)).view(np.int64)


def _ints(ints: np.ndarray) -> tuple[int, int, bytes]:
    """(mode, base, payload) for an int64 column."""
    base = int(ints.min())
    delta = np.diff(ints, prepend=np.int64(0))
    offset = ints - base                                    # never negative: the high bytes stay 0
    big = int(np.abs(delta).max())
    if int(offset.max()) <= _I32_MAX and int(offset.sum()) < 2 * int(np.abs(delta[1:]).sum()):
        return _OFFSET32, base, _shuffle(offset.astype("<u4"))
    if big < 2 ** 30:
        return _DELTA32, 0, _shuffle(_zigzag(delta).astype("<u4"))
    return _DELTA64, 0, _shuffle(_zigzag(delta))


def _encode_field(name: str, col: np.ndarray) -> tuple[int, float, int, bytes]:
    kind = col.dtype.kind
    if col.ndim != 1 or len(col) == 0:                      # the raw bytes of each row
        return _AS_IS, 0.0, 0, np.ascontiguousarray(col.T if col.ndim == 2 else col).tobytes()
    if name == "t" and col.dtype == np.dtype("<f8"):
        bits = col.view("<i8")
        with np.errstate(over="ignore"):
            second = np.diff(np.diff(bits, prepend=np.int64(0)), prepend=np.int64(0))
        return _TIME, 0.0, 0, _shuffle(_zigzag(second))
    if kind in "iu" and col.dtype.itemsize < 8:
        mode, base, payload = _ints(col.astype(np.int64))
        return mode, 1.0, base, payload
    if kind == "f" and np.isfinite(col).all():
        wide = col.astype(np.float64)
        for scale in _SCALES:
            q = np.rint(wide * scale)
            if np.abs(q).max() >= 2.0 ** 52:
                break
            if np.array_equal((q / scale).astype(col.dtype), col):
                mode, base, payload = _ints(q.astype(np.int64))
                return mode, scale, base, payload
    return _AS_IS, 0.0, 0, _shuffle(col)


def encode(rows: np.ndarray, codec: str = PACKED) -> bytes:
    """Pack a structured array (the stream's dtype) with ``codec``."""
    if codec == RAW:
        return rows.tobytes()
    if codec != PACKED:
        raise ValueError(f"unknown chunk codec {codec!r}")
    heads, payloads = [], []
    for name in rows.dtype.names or ():
        mode, scale, base, payload = _encode_field(name, rows[name])
        heads.append(_HEAD.pack(mode, scale, base))
        payloads.append(payload)
    return zlib.compress(b"".join(heads + payloads), ZLIB_LEVEL)


def decode(codec: str, data: bytes, dtype: np.dtype, n_rows: int) -> np.ndarray:
    """The rows of one chunk as a structured array of ``dtype``."""
    if codec == RAW:
        return np.frombuffer(data, dtype, n_rows)
    if codec != PACKED:
        raise ValueError(f"unknown chunk codec {codec!r}; this log was written by a newer hondalog")
    blob = memoryview(zlib.decompress(data))
    names = dtype.names or ()
    out = np.empty(n_rows, dtype)
    at = _HEAD.size * len(names)
    for k, name in enumerate(names):
        mode, scale, base = _HEAD.unpack_from(blob, k * _HEAD.size)
        field = dtype[name]
        if field.subdtype is not None:                      # raw bytes: (rows, raw_len) of u1
            width = field.subdtype[1][0]
            size = n_rows * width
            out[name] = np.frombuffer(blob[at:at + size], np.uint8).reshape(width, n_rows).T
        elif mode == _AS_IS:
            size = n_rows * field.itemsize
            out[name] = _unshuffle(blob[at:at + size], field, n_rows)
        elif mode == _TIME:
            size = n_rows * 8
            second = _unzigzag(_unshuffle(blob[at:at + size], np.dtype("<u8"), n_rows))
            with np.errstate(over="ignore"):
                out[name] = np.cumsum(np.cumsum(second)).view("<f8")
        elif mode in (_DELTA32, _DELTA64, _OFFSET32):
            item = np.dtype("<u8") if mode == _DELTA64 else np.dtype("<u4")
            size = n_rows * item.itemsize
            stored = _unshuffle(blob[at:at + size], item, n_rows)
            ints = stored.astype(np.int64) + base if mode == _OFFSET32 else np.cumsum(_unzigzag(stored))
            out[name] = ints / scale if field.kind == "f" else ints
        else:
            raise ValueError(f"damaged chunk: unknown column mode {mode}")
        at += size
    return out
