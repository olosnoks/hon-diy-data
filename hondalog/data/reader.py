"""Reading logs: the ``Log`` object that replay, analysis and exports work on.

A ``Log`` is one drive in the database (``read_drive``; streams are loaded on
first use), one capture (the same drive, cut to the capture's window), or a
log built in memory by an importer. All three look the same from outside.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from . import db
from .db import DriveRef
from .model import MARKER_KINDS, Column, Event, FloatArray, Series, StreamInfo, Table
from .schema import SLOTS, LogFormatError


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
        self._latency: np.ndarray | None = None

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

    @property
    def latency(self) -> FloatArray:
        """Estimated age of each K-line reply in seconds (NaN where unknown)."""
        data = self.data
        if self._latency is None or len(self._latency) != len(data):
            return np.full(len(data), np.nan)
        return self._latency


class Log:
    """A whole log: metadata, streams, events."""

    def __init__(self, meta: Mapping[str, Any] | None = None,
                 streams: Iterable[Stream] = (), events: Iterable[Event] = (),
                 path: str | os.PathLike | DriveRef | None = None):
        #: where it came from: a ``DriveRef`` for a drive or capture in the
        #: database, a ``Path`` for an imported file, None for an in-memory log
        self.path: Path | DriveRef | None = (
            path if isinstance(path, DriveRef) or path is None else Path(path))
        self.meta: dict[str, Any] = dict(meta or {})
        self.streams: dict[str, Stream] = {s.name: s for s in streams}
        self.events: list[Event] = sorted(events, key=lambda e: e.t)
        #: "recording", "complete", "crashed" (the recorder died; everything
        #: committed before that is here), "imported", or "memory"
        self.status = "memory"
        self.drive_id: int | None = None
        self._conn: sqlite3.Connection | None = None
        self._series: dict[str, Series] = {}

    @property
    def ref(self) -> DriveRef | None:
        """The drive or capture in the database this log is, if it is one."""
        return self.path if isinstance(self.path, DriveRef) else None

    @property
    def name(self) -> str:
        return self.path.name if self.path is not None else ""

    # -- lifetime --

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

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
        ref = self.ref
        if ref is not None and all(s._data is None for s in self.streams.values()):
            # nothing loaded yet: ask the database for just that range
            cut = _read(db.connect(ref.db), ref, t0, t1).load_all()
            return Log(self.meta, cut.streams.values(), cut.events)
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


# ---- drives in the database -------------------------------------------------

#: drives columns that are shown under another name in ``Log.meta``
META_COLUMNS = {"ecu": "ecu", "ecu_id": "ecu_id", "port": "port", "vehicle": "vehicle",
                "map_version": "address_map_version", "map_tag": "address_map_tag",
                "map_hash": "address_map_hash", "started_iso": "started", "started_at": "started_unix",
                "epoch_unix": "epoch_unix", "notes": "notes"}
JSON_COLUMNS = {"link_stats": "link", "unlocked_frames": "unlocked_frames", "read_plan": "read_plan",
                "tags": "tags"}


def drive_meta(conn: sqlite3.Connection, drive_id: int) -> dict[str, Any]:
    """A drive's row as the header dictionary the recorder was given."""
    cur = conn.execute("SELECT d.*, m.text AS map_text, m.file AS map_file FROM drives d "
                       "LEFT JOIN address_maps m ON m.id = d.map_id WHERE d.id=?", (drive_id,))
    row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
    meta: dict[str, Any] = json.loads(row["meta"] or "{}")
    for column, key in META_COLUMNS.items():
        if row[column] not in (None, ""):
            meta[key] = row[column]
    for column, key in JSON_COLUMNS.items():
        if row[column]:                                # NULL: the recorder never said
            value = json.loads(row[column])
            if value or key != "tags":
                meta[key] = value
    if row["expert_unlock"] or "unlocked_frames" in meta:
        meta["expert_unlock"] = bool(row["expert_unlock"])
    if row["keep"]:
        meta["keep"] = True
    if row["map_text"] is not None:
        meta["address_map_text"] = row["map_text"]
        if row["map_file"]:
            meta["address_map_file"] = row["map_file"]
    meta["drive"] = row["name"]
    meta["status"] = row["status"]
    if row["end_reason"]:
        meta["end_reason"] = row["end_reason"]
    if row["ended_iso"]:
        meta["ended"] = row["ended_iso"]
    return meta


def _between(t0: float | None, t1: float | None, args: list[Any]) -> str:
    sql = ""
    if t0 is not None:
        sql += " AND t >= ?"
        args.append(t0)
    if t1 is not None:
        sql += " AND t <= ?"
        args.append(t1)
    return sql


def load_rows(conn: sqlite3.Connection, drive_id: int, stream_id: int, info: StreamInfo,
              t0: float | None = None, t1: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The rows of one ``samples`` stream as (structured array, latency in seconds)."""
    dtype = info.dtype
    names = info.column_names
    parts = max(1, (len(names) + SLOTS - 1) // SLOTS)
    out = np.zeros(0, dtype)
    latency = np.zeros(0)
    for part in range(parts):
        mine = names[part * SLOTS:(part + 1) * SLOTS]
        head = "t, raw, latency_us" if part == 0 else "t"
        cols = "".join(f", v{k}" for k in range(len(mine)))
        args: list[Any] = [drive_id, stream_id, part]
        sql = (f"SELECT {head}{cols} FROM samples WHERE drive_id=? AND stream_id=? AND part=?"
               + _between(t0, t1, args))
        fetched = conn.execute(sql, args).fetchall()
        if part == 0:
            out = np.zeros(len(fetched), dtype)
            if not fetched:
                return out, latency
            columns = list(zip(*fetched))
            out["t"] = columns[0]
            latency = np.array(columns[2], dtype=np.float64) / 1e6
            if info.raw_len:
                try:
                    out["raw"] = np.frombuffer(b"".join(columns[1]), np.uint8).reshape(len(fetched), info.raw_len)
                except (TypeError, ValueError):        # a row without its bytes, or a short one
                    for i, blob in enumerate(columns[1]):
                        if blob is not None and len(blob) == info.raw_len:
                            out["raw"][i] = np.frombuffer(blob, np.uint8)
            first = 3
        else:
            if len(fetched) != len(out):
                break                                   # a torn multi-part row: keep what lines up
            columns = list(zip(*fetched))
            first = 1
        for k, name in enumerate(mine):
            values = np.array(columns[first + k], dtype=np.float64)
            if dtype[name].kind != "f":
                values = np.nan_to_num(values)
            out[name] = values
    return out, latency


def read_drive(path: str | os.PathLike, drive: str | int | None = None, capture: str | None = None) -> Log:
    """Open one drive (default: the newest) or one capture of a database.

    Works while the drive is still being recorded (it shows what has been
    committed so far) and after a crash (``log.status`` is "crashed").
    """
    path = Path(path)
    conn = db.connect(path)
    try:
        ref = db.find(conn, capture or ("" if drive is None else drive), path)
        if ref is None:
            what = capture or drive
            raise KeyError(f"{path}: no drive or capture {what!r}" if what not in (None, "")
                           else f"{path}: no drives recorded yet")
        return _read(conn, ref)
    except BaseException:
        conn.close()
        raise


def _read(conn: sqlite3.Connection, ref: DriveRef, t0: float | None = None, t1: float | None = None) -> Log:
    """The drive or capture ``ref``; with ``t0`` / ``t1`` only that time range of it."""
    drive_id, status = conn.execute("SELECT id, status FROM drives WHERE name=?", (ref.drive,)).fetchone()
    drive_id = int(drive_id)
    meta = drive_meta(conn, drive_id)
    if ref.capture:
        cur = conn.execute("SELECT * FROM captures WHERE name=?", (ref.capture,))
        cap = dict(zip([c[0] for c in cur.description], cur.fetchone()))
        t0 = float(cap["t_start"]) if t0 is None else max(t0, float(cap["t_start"]))
        t1 = float(cap["t_end"]) if t1 is None else min(t1, float(cap["t_end"]))
        meta.update(json.loads(cap["meta"] or "{}"))
        meta["capture"] = cap["name"]
        meta["capture_of"] = ref.drive
    log = Log(meta, path=ref)
    log.status, log.drive_id, log._conn = str(status), drive_id, conn
    live = status == "recording" or t0 is not None or t1 is not None

    stats = {int(sid): (int(rows), lo, hi) for sid, rows, lo, hi in conn.execute(
        "SELECT id, rows, t_first, t_last FROM streams WHERE drive_id=?", (drive_id,))}
    for sid, (info, storage) in db.stream_infos(conn, drive_id).items():
        data: np.ndarray | None = None
        if storage == "chunks":
            loader = _chunk_loader(log, drive_id, sid, info, t0, t1)
            if t0 is not None or t1 is not None:
                data = loader()                        # the only way to count a window exactly
            elif live:
                rows, lo, hi = conn.execute("SELECT coalesce(sum(n_rows), 0), min(t_start), max(t_end) "
                                            "FROM chunks WHERE drive_id=? AND stream_id=?", (drive_id, sid)).fetchone()
                stats[sid] = (int(rows), lo, hi)
        else:
            loader = _row_loader(log, drive_id, sid, info, t0, t1)
            if live:
                args: list[Any] = [drive_id, sid]
                rows, lo, hi = conn.execute("SELECT count(*), min(t), max(t) FROM samples WHERE drive_id=? "
                                            "AND stream_id=? AND part=0" + _between(t0, t1, args), args).fetchone()
                stats[sid] = (int(rows), lo, hi)
        rows, lo, hi = stats.get(sid, (0, None, None))
        log.streams[info.name] = Stream(info, rows, np.nan if lo is None else float(lo),
                                        np.nan if hi is None else float(hi), loader=loader, data=data)

    args = [drive_id]
    span = _between(t0, t1, args)
    events = [Event(float(t), kind, text or "", json.loads(data) if data else {})
              for t, kind, text, data in conn.execute(
                  "SELECT t, kind, text, data FROM events WHERE drive_id=?" + span + " ORDER BY t, id", args)]
    events += [Event(float(t), "alarm", text or "", json.loads(data) if data else {})
               for t, text, data in conn.execute(
                   "SELECT t, text, data FROM alarms WHERE drive_id=?" + span + " ORDER BY t, id", args)]
    log.events = sorted(events, key=lambda e: e.t)
    return log


def _connection(log: Log) -> tuple[sqlite3.Connection, bool]:
    """The log's connection, or a fresh one if the log was closed."""
    if log._conn is not None:
        return log._conn, False
    ref = log.ref
    if ref is None:
        raise ValueError("log is closed")
    return db.connect(ref.db), True


def _row_loader(log: Log, drive_id: int, sid: int, info: StreamInfo, t0: float | None,
                t1: float | None) -> Callable[[], np.ndarray]:
    def load() -> np.ndarray:
        conn, opened = _connection(log)
        try:
            data, latency = load_rows(conn, drive_id, sid, info, t0, t1)
        finally:
            if opened:
                conn.close()
        stream = log.streams.get(info.name)
        if stream is not None:
            stream._latency = latency
            stream.rows = len(data)
        return data
    return load


def _chunk_loader(log: Log, drive_id: int, sid: int, info: StreamInfo, t0: float | None,
                  t1: float | None) -> Callable[[], np.ndarray]:
    def load() -> np.ndarray:
        conn, opened = _connection(log)
        try:
            data = db.read_chunks(conn, drive_id, sid, info.dtype, t0, t1)
        finally:
            if opened:
                conn.close()
        stream = log.streams.get(info.name)
        if stream is not None:
            stream.rows = len(data)
        return data
    return load


# ---- opening anything -------------------------------------------------------

def open_log(path: str | os.PathLike | DriveRef, drive: str | int | None = None) -> Log:
    """Open any log this program understands.

    - a ``DriveRef`` or ``"database#name"``: that drive or capture
    - a database file: the drive named by ``drive`` (name or id), else the newest
    - a bare drive or capture name: looked up in the default database
    - a file from another tool, read into memory: hondalog 0.1.0 CSV,
      generic CSV, MegaLogViewer ``.msl`` and ``.mlg``
    """
    if isinstance(path, DriveRef):
        return read_drive(path.db, path.drive, path.capture or None)
    text = os.fspath(path)
    if "#" in text and not os.path.exists(text):
        head, name = text.rsplit("#", 1)
        return read_drive(Path(os.path.expanduser(head)) if head else db.db_path(), name)
    file = Path(text)
    if not file.exists() and os.sep not in text and db.is_database(db.db_path()):
        return read_drive(db.db_path(), text)
    with open(file, "rb") as fh:
        magic = fh.read(16)
    if magic == db.schema.SQLITE_MAGIC:
        return read_drive(file, drive)
    if file.suffix.lower() in db.DB_SUFFIXES:
        raise LogFormatError(f"{file}: not a hondalog database")
    from ..export import mlg, textlog          # importers; imported late to avoid a cycle
    if magic[:6] == mlg.MAGIC:
        return mlg.read_mlg(file)
    return textlog.read_text_log(file)
