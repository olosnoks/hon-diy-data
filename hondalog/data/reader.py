"""Reading logs: the ``Log`` object that replay, analysis and exports work on.

A ``Log`` is either backed by a ``.hlog`` file (streams are loaded on first
use) or built in memory by an importer. Both look the same from outside.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from . import container
from .container import CHUNK_HEADER, DATA, EVENT, META, STREAM, LogFormatError
from .model import MARKER_KINDS, Column, Event, FloatArray, Series, StreamInfo, Table


class Stream:
    """One table of a log. ``data`` is a structured array: t, the columns, raw."""

    def __init__(self, info: StreamInfo, rows: int = 0, t0: float = np.nan, t1: float = np.nan,
                 loader: Callable[[], np.ndarray] | None = None, data: np.ndarray | None = None):
        self.info = info
        self._loader = loader
        self._data = data
        if data is not None:
            rows = len(data)
            if rows:
                t0, t1 = float(data["t"][0]), float(data["t"][-1])
        self.rows, self.t0, self.t1 = rows, t0, t1

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def data(self) -> np.ndarray:
        if self._data is None:
            self._data = self._loader() if self._loader else np.zeros(0, self.info.dtype)
        return self._data

    @property
    def t(self) -> FloatArray:
        return self.data["t"]

    def column(self, name: str) -> FloatArray:
        return self.data[name].astype(np.float64)

    @property
    def raw(self) -> np.ndarray:
        """The raw bytes of every row, shape (rows, raw_len)."""
        if not self.info.raw_len:
            return np.zeros((self.rows, 0), np.uint8)
        return self.data["raw"]


class Log:
    """A whole log: metadata, streams, events."""

    def __init__(self, meta: Mapping[str, Any] | None = None,
                 streams: Iterable[Stream] = (), events: Iterable[Event] = (),
                 path: str | os.PathLike | None = None):
        self.path = Path(path) if path is not None else None
        self.meta: dict[str, Any] = dict(meta or {})
        self.streams: dict[str, Stream] = {s.name: s for s in streams}
        self.events: list[Event] = sorted(events, key=lambda e: e.t)
        self.indexed = True         # False: the file had no index (crash) and was scanned
        self.junk_bytes = 0         # bytes skipped as damaged
        self._buf: Any = None
        self._series: dict[str, Series] = {}

    # -- lifetime --

    def close(self) -> None:
        if self._buf is not None and hasattr(self._buf, "close"):
            self._buf.close()
        self._buf = None

    def __enter__(self) -> "Log":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def load_all(self) -> "Log":
        """Read every stream into memory now (and release the file)."""
        for stream in self.streams.values():
            stream.data  # noqa: B018 - property access loads it
        self.close()
        return self

    # -- what is in it --

    @property
    def channels(self) -> list[str]:
        """Every channel name, in definition order, each listed once."""
        seen: dict[str, None] = {}
        for stream in self.streams.values():
            for col in stream.info.columns:
                seen.setdefault(col.name)
        return list(seen)

    def column(self, name: str) -> Column | None:
        """The definition (units, label, range) of a channel."""
        for stream in self.streams.values():
            col = stream.info.column(name)
            if col is not None:
                return col
        return None

    def units(self, name: str) -> str:
        col = self.column(name)
        return col.units if col else ""

    def streams_with(self, name: str) -> list[Stream]:
        return [s for s in self.streams.values() if s.info.column(name) is not None]

    @property
    def t0(self) -> float:
        starts = [s.t0 for s in self.streams.values() if s.rows]
        starts += [e.t for e in self.events[:1]] if not starts else []
        return float(min(starts)) if starts else 0.0

    @property
    def t1(self) -> float:
        ends = [s.t1 for s in self.streams.values() if s.rows]
        ends += [e.t for e in self.events[-1:]] if not ends else []
        return float(max(ends)) if ends else 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    @property
    def rows(self) -> int:
        return sum(s.rows for s in self.streams.values())

    @property
    def markers(self) -> list[tuple[float, str]]:
        """(time, text) of everything the replay view draws as a marker line."""
        return [(e.t, e.text or e.kind) for e in self.events if e.kind in MARKER_KINDS]

    def events_of(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    # -- data access --

    def series(self, name: str) -> Series:
        """One channel at its own timestamps. A channel that is read in more
        than one stream (fast and slow blocks) is merged in time order."""
        cached = self._series.get(name)
        if cached is not None:
            return cached
        parts = [s for s in self.streams_with(name) if s.rows]
        if not parts:
            if self.column(name) is None:
                raise KeyError(f"no channel {name!r} in this log")
            series = Series(name, np.zeros(0), np.zeros(0), self.units(name))
        elif len(parts) == 1:
            series = Series(name, parts[0].t.astype(np.float64), parts[0].column(name), self.units(name))
        else:
            t = np.concatenate([s.t for s in parts]).astype(np.float64)
            v = np.concatenate([s.column(name) for s in parts])
            order = np.argsort(t, kind="stable")
            series = Series(name, t[order], v[order], self.units(name))
        self._series[name] = series
        return series

    def table(self, names: Sequence[str] | None = None, *, base: str | None = None,
              hz: float | None = None, t: FloatArray | None = None, how: str = "hold",
              max_age: float | None = None) -> Table:
        """Put channels on one time grid.

        The grid is, in order of preference: ``t`` if given; a uniform grid at
        ``hz``; the timestamps of channel ``base``; the timestamps of the
        requested channel with the most samples. Other channels are sampled
        with ``how``: "hold" takes the last value read at or before each grid
        time, "linear" interpolates. A value older than ``max_age`` seconds
        becomes NaN (default: three sample periods of that channel, at least
        0.5 s), so a link dropout shows as a gap and not as a frozen number.
        """
        names = list(names) if names is not None else self.channels
        series = [self.series(n) for n in names]
        if t is None:
            if hz:
                t = np.arange(self.t0, self.t1 + 0.5 / hz, 1.0 / hz)
            elif base is not None:
                t = self.series(base).t
            elif series:
                t = max(series, key=len).t
            else:
                t = np.zeros(0)
        grid = np.asarray(t, dtype=np.float64)
        cols = {s.name: align(s, grid, how, max_age) for s in series}
        return Table(grid, cols, {n: self.units(n) for n in names})

    def window(self, t0: float, t1: float) -> "Log":
        """An in-memory copy holding only rows and events with t0 <= t <= t1."""
        streams = []
        for stream in self.streams.values():
            data = stream.data
            lo = int(np.searchsorted(data["t"], t0, side="left"))
            hi = int(np.searchsorted(data["t"], t1, side="right"))
            streams.append(Stream(stream.info, data=data[lo:hi].copy()))
        events = [e for e in self.events if t0 <= e.t <= t1]
        return Log(self.meta, streams, events)


def align(series: Series, grid: FloatArray, how: str = "hold",
          max_age: float | None = None) -> FloatArray:
    """Sample ``series`` at the times in ``grid``."""
    out = np.full(len(grid), np.nan)
    if len(series) == 0 or len(grid) == 0:
        return out
    st, sv = series.t, series.v
    if st is grid or (len(st) == len(grid) and np.array_equal(st, grid)):
        return sv.astype(np.float64, copy=True)
    if max_age is None:
        max_age = max(3.0 * float(np.median(np.diff(st))), 0.5) if len(st) > 1 else np.inf
    idx = np.searchsorted(st, grid, side="right") - 1
    ok = idx >= 0
    idx = np.clip(idx, 0, len(st) - 1)
    if how == "linear":
        nxt = np.clip(idx + 1, 0, len(st) - 1)
        span = st[nxt] - st[idx]
        with np.errstate(invalid="ignore", divide="ignore"):
            frac = np.where(span > 0, (grid - st[idx]) / span, 0.0)
        frac = np.clip(frac, 0.0, 1.0)
        values = sv[idx] + (sv[nxt] - sv[idx]) * frac
        ok &= (span <= max_age) | (span == 0)
        ok &= grid - st[idx] <= max_age
        ok &= grid <= st[-1]            # interpolate, never extrapolate past the last read
    elif how == "hold":
        values = sv[idx]
        ok &= grid - st[idx] <= max_age
    else:
        raise ValueError(f"how must be 'hold' or 'linear', not {how!r}")
    out[ok] = values[ok]
    return out


# ---- opening files ----------------------------------------------------------

def read_hlog(path: str | os.PathLike) -> Log:
    """Open a native log. Works on a file that is still being written and on
    one cut off by a crash (``log.indexed`` is False then)."""
    buf, size = container.open_mmap(path)
    try:
        table = container.read_chunk_table(buf, size)
    except LogFormatError as exc:
        if hasattr(buf, "close"):
            buf.close()
        raise LogFormatError(f"{path}: {exc}") from None
    log = Log(path=path)
    log.indexed, log.junk_bytes = table.indexed, table.junk
    log._buf = buf
    entries = table.entries
    head = CHUNK_HEADER.size
    infos: dict[int, StreamInfo] = {}
    events: list[Event] = []
    small = entries[entries["type"] != DATA]
    for row in small:
        body = int(row["offset"]) + head
        try:
            doc = json.loads(bytes(buf[body:body + int(row["length"])]))
        except ValueError:
            continue
        if row["type"] == META and isinstance(doc, dict):
            log.meta.update(doc)
        elif row["type"] == STREAM:
            infos[int(row["stream"])] = StreamInfo.from_json(doc)
        elif row["type"] == EVENT:
            events.extend(Event.from_json(e) for e in doc)
    log.events = sorted(events, key=lambda e: e.t)

    data = entries[entries["type"] == DATA]
    for sid, info in infos.items():
        mine = data[data["stream"] == sid]
        mine = mine[mine["rows"] > 0]
        rows = int(mine["rows"].sum())
        t0 = float(mine["t0"].min()) if rows else np.nan
        t1 = float(mine["t1"].max()) if rows else np.nan
        log.streams[info.name] = Stream(info, rows, t0, t1,
                                        loader=_loader(log, info, mine["offset"] + head, mine["rows"]))
    return log


def _loader(log: Log, info: StreamInfo, offsets: np.ndarray, counts: np.ndarray) -> Callable[[], np.ndarray]:
    def load() -> np.ndarray:
        dtype = info.dtype
        out = np.empty(int(counts.sum()), dtype)
        buf = log._buf
        opened = False
        if buf is None:
            if log.path is None:
                raise ValueError("log is closed")
            buf, _ = container.open_mmap(log.path)
            opened = True
        at = 0
        for offset, n in zip(offsets.tolist(), counts.tolist()):
            out[at:at + n] = np.frombuffer(buf, dtype, n, offset)
            at += n
        if opened and hasattr(buf, "close"):
            buf.close()
        if len(out) > 1 and np.any(np.diff(out["t"]) < 0):
            out = out[np.argsort(out["t"], kind="stable")]
        return out
    return load


def open_log(path: str | os.PathLike) -> Log:
    """Open any log this program understands: native ``.hlog``, hondalog
    0.1.0 CSV, generic CSV, MegaLogViewer ``.msl`` and ``.mlg``."""
    path = Path(path)
    with open(path, "rb") as fh:
        magic = fh.read(8)
    if magic == container.FILE_MAGIC:
        return read_hlog(path)
    if path.suffix == container.EXTENSION:
        raise LogFormatError(f"{path}: not a hondalog log (bad magic)")
    from ..export import mlg, textlog          # importers; imported late to avoid a cycle
    if magic[:6] == mlg.MAGIC:
        return mlg.read_mlg(path)
    return textlog.read_text_log(path)
