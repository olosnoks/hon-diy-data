"""Decode a log's raw bytes again with another address map.

The whole point of keeping the raw RAM bytes: when a formula or an address
turns out to be wrong, old logs do not have to be thrown away.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .model import Column, StreamInfo
from .reader import Log, Stream


def _decode_column(channel: Any, raw: np.ndarray) -> np.ndarray:
    """Run one channel's formula over every row's bytes (shape rows x length)."""
    out = np.full(len(raw), np.nan, dtype=np.float32)
    if len(raw) == 0:
        return out
    # the formula is plain Python; evaluate it once per distinct byte pattern
    patterns, inverse = np.unique(raw, axis=0, return_inverse=True)
    values = np.array([np.nan if (v := channel.convert(bytes(p))) is None else v
                       for p in patterns.tolist()], dtype=np.float32)
    return values[inverse.reshape(-1)]


def redecode(log: Log, amap: Any) -> Log:
    """A new in-memory log whose ECU streams are decoded with ``amap``.

    Only streams that carry raw bytes (per-read recording) can be decoded
    again; the others are copied as they are. The new map's version is put
    in the metadata and the old one is kept under ``redecoded_from``.
    """
    streams = []
    done = 0
    for stream in log.streams.values():
        info = stream.info
        addr, length = info.attrs.get("addr"), info.raw_len
        if not length or addr is None:
            streams.append(Stream(info, data=stream.data))
            continue
        channels = [c for c in amap.channels if addr <= c.addr and c.addr + c.length <= addr + length]
        columns = tuple(Column(c.name, "f4", c.units, c.label if c.label != c.name else "",
                               c.kind, c.decimals, float(c.min), float(c.max)) for c in channels)
        new_info = StreamInfo(info.name, columns, info.source, length, info.attrs)
        old = stream.data
        rows = np.zeros(len(old), dtype=new_info.dtype)
        rows["t"] = old["t"]
        rows["raw"] = old["raw"]
        for ch in channels:
            lo = ch.addr - addr
            rows[ch.name] = _decode_column(ch, old["raw"][:, lo:lo + ch.length])
        streams.append(Stream(new_info, data=rows))
        done += 1
    meta = dict(log.meta)
    meta["redecoded_from"] = meta.get("address_map_version", "")
    meta["address_map_version"] = amap.version
    meta["address_map_file"] = amap.path
    meta["units"] = " ".join(f"{c.name}={c.units or '-'}" for c in amap.channels)
    meta["redecoded_streams"] = done
    return Log(meta, streams, log.events)
