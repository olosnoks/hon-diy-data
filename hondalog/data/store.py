"""Writing to the database: the ``Store`` (one connection, one writer thread)
and the ``DriveWriter`` it hands out per drive. Plus the whole-drive
operations: save a ``Log`` as a drive, cut out a window, annotate.

    store = Store("~/hondalog/hondalog.db")
    drive = store.begin_drive({"ecu_id": "..."})
    drive.add_stream("ecu/00+2", [Column("rpm")], source="ecu", raw_len=2, attrs={"addr": 0})
    drive.append("ecu/00+2", t, [812.0], raw=b"\\x0c\\xb0", latency=0.004)   # any thread
    drive.event(t, "marker", "MARK 1")
    drive.end()
    store.close()

``append`` and ``event`` only touch memory. Everything reaches the database
on the store's own thread: one transaction every ``flush_interval`` seconds
(0.5 s), written with ``executemany`` on prepared statements, WAL mode,
``synchronous=NORMAL``. A killed process therefore loses at most the last
half second plus the commit time, and the file is never left half-written:
the next opener marks the drive "crashed" (``db.recover``). The write-ahead
log is synced to the disk every ``checkpoint_s`` seconds, which is the most
a power cut can take.

High-rate streams (``storage="chunks"``, the default for black box streams)
are not written row by row: each commit appends the new rows as one ``raw``
BLOB, and once a minute the store packs those into a compressed block and
writes their one-second min / mean / max (``chunk_stats``).

``background=False`` runs everything on the calling thread, on ``flush`` and
``close`` only: what importers, tools and tests want.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import numpy as np

from . import codec, db, schema
from .db import DriveRef
from .model import Column, Event, StreamInfo
from .reader import Log, Stream, open_log
from .schema import SLOTS

HEARTBEAT_S = 5.0
_PLAIN = (float, int, type(None), bool)
BLOCK_ROWS = 65536                  # rows per packed block when whole arrays are stored
MAX_RETRY_ROWS = 400_000            # rows kept for another try while the database is locked

#: header keys that have their own column in ``drives``
_TEXT_COLUMNS = {"ecu": "ecu", "ecu_id": "ecu_id", "port": "port", "vehicle": "vehicle", "notes": "notes",
                 "address_map_version": "map_version", "address_map_tag": "map_tag",
                 "address_map_hash": "map_hash", "started": "started_iso"}
_REAL_COLUMNS = {"started_unix": "started_at", "epoch_unix": "epoch_unix"}
_JSON_COLUMNS = {"link": "link_stats", "unlocked_frames": "unlocked_frames", "read_plan": "read_plan"}
_BOOL_COLUMNS = {"expert_unlock": "expert_unlock", "keep": "keep"}
#: keys the reader adds to ``Log.meta``; they describe the row, they are not stored in it
_DERIVED = ("drive", "status", "end_reason", "ended", "address_map_file")


def apply_meta(conn: sqlite3.Connection, drive_id: int, items: Mapping[str, Any]) -> None:
    """Merge header facts into a drive's row. Call inside a transaction."""
    sets: dict[str, Any] = {}
    rest: dict[str, Any] = {}
    for key, value in items.items():
        if key in _TEXT_COLUMNS:
            sets[_TEXT_COLUMNS[key]] = "" if value is None else str(value)
        elif key in _REAL_COLUMNS:
            sets[_REAL_COLUMNS[key]] = None if value is None else float(value)
        elif key in _JSON_COLUMNS:
            sets[_JSON_COLUMNS[key]] = json.dumps(value, ensure_ascii=False, default=str)
        elif key in _BOOL_COLUMNS:
            sets[_BOOL_COLUMNS[key]] = 1 if value else 0
        elif key == "tags":
            tags = value if isinstance(value, (list, tuple)) else str(value or "").split()
            sets["tags"] = json.dumps([str(t) for t in tags], ensure_ascii=False)
        elif key == "address_map_text":
            text = str(value or "")
            if text:
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                conn.execute(
                    "INSERT OR IGNORE INTO address_maps(sha256, version, tag, hash, file, text) VALUES(?,?,?,?,?,?)",
                    (digest, str(items.get("address_map_version", "")), str(items.get("address_map_tag", "")),
                     str(items.get("address_map_hash", "")), str(items.get("address_map_file", "")), text))
                sets["map_id"] = conn.execute("SELECT id FROM address_maps WHERE sha256=?", (digest,)).fetchone()[0]
        elif key not in _DERIVED:
            rest[key] = value
    if "address_map_file" in items and "address_map_text" not in items:
        rest["address_map_file"] = items["address_map_file"]
    if rest:
        old = conn.execute("SELECT meta FROM drives WHERE id=?", (drive_id,)).fetchone()
        merged = {**json.loads(old[0] or "{}"), **rest} if old else rest
        sets["meta"] = json.dumps(merged, ensure_ascii=False, default=str)
    if sets:
        conn.execute(f"UPDATE drives SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                     (*sets.values(), drive_id))


class _RowStream:
    """Buffer of one stream that goes to ``samples``."""

    chunked = False
    __slots__ = ("info", "sid", "ncols", "parts", "sql", "buf", "max_t", "dup", "rows")

    def __init__(self, info: StreamInfo, sid: int):
        self.info, self.sid = info, sid
        self.ncols = len(info.columns)
        self.parts = max(1, (self.ncols + SLOTS - 1) // SLOTS)
        self.sql = []
        for part in range(self.parts):
            n = min(SLOTS, self.ncols - part * SLOTS)
            names = "".join(f", v{k}" for k in range(n))
            self.sql.append("INSERT INTO samples(drive_id, stream_id, part, t, n, raw, latency_us" + names
                            + ") VALUES(?,?,?,?,?,?,?" + ",?" * n + ")")
        self.buf: list[list[tuple]] = [[] for _ in range(self.parts)]
        self.max_t = float("-inf")
        self.dup = 0
        self.rows = 0


class _ChunkStream:
    """Buffer of one high-rate stream that goes to ``chunks``."""

    chunked = True
    __slots__ = ("info", "sid", "ncols", "pack", "item", "buf", "t0", "t1", "rows", "raw_since", "blocks")

    def __init__(self, info: StreamInfo, sid: int):
        self.info, self.sid = info, sid
        self.ncols = len(info.columns)
        self.pack = struct.Struct(info.struct_format).pack
        self.item = info.dtype.itemsize
        self.buf = bytearray()
        self.t0 = self.t1 = 0.0
        self.rows = 0
        self.raw_since: float | None = None     # time of the oldest row still in a "raw" chunk
        self.blocks: list[np.ndarray] = []      # whole arrays waiting to be packed


class _Job:
    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn: Callable[[sqlite3.Connection], Any]):
        self.fn = fn
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class DriveWriter:
    """One drive being written. Made by ``Store.begin_drive``."""

    def __init__(self, store: "Store", drive_id: int, name: str):
        self.store = store
        self.id = drive_id
        self.name = name
        self.ref = DriveRef(store.path, name)
        self.rows = 0                           # rows accepted so far (all streams)
        self.closed = False
        self._streams: dict[str, _RowStream | _ChunkStream] = {}
        self._events: list[Event] = []
        self._meta: dict[str, Any] = {}
        self._defs: list[tuple[int, StreamInfo, str]] = []
        self._view_dirty = False
        self._lock = store._lock

    # -- API (any thread) --

    def stream(self, name: str) -> StreamInfo | None:
        state = self._streams.get(name)
        return state.info if state else None

    def set_meta(self, **items: Any) -> None:
        """Add or replace header facts. Values must be JSON-serialisable."""
        with self._lock:
            self._check_open()
            self._meta.update(items)

    def add_stream(self, name: str, columns: Sequence[Column], *, source: str = "", raw_len: int = 0,
                   attrs: Mapping[str, Any] | None = None, storage: str | None = None) -> str:
        """Define a stream and return its name. Defining the same stream twice is fine.

        ``storage``: "rows" (one ``samples`` row per row: K-line reads, math
        channels) or "chunks" (packed blocks: the black box). The default is
        chunks for ``source="blackbox"``, rows for everything else.
        """
        info = StreamInfo(name, tuple(columns), source, raw_len, dict(attrs or {}))
        with self._lock:
            self._check_open()
            old = self._streams.get(name)
            if old is not None:
                if old.info.dtype != info.dtype:
                    raise ValueError(f"stream {name!r} already exists with other columns")
                return name
            sid = len(self._streams) + 1
            kind = storage or db.storage_for(info)
            if kind not in ("rows", "chunks"):
                raise ValueError(f"stream {name!r}: storage must be 'rows' or 'chunks'")
            self._streams[name] = _ChunkStream(info, sid) if kind == "chunks" else _RowStream(info, sid)
            self._defs.append((sid, info, kind))
            self._view_dirty = True
        return name

    def append(self, stream: str, t: float, values: Sequence[float | None], raw: bytes = b"",
               latency: float | None = None) -> None:
        """Add one row. ``None`` (and NaN) is stored as "no value". ``latency``
        is the estimated age of a K-line reply, in seconds."""
        state = self._streams[stream]
        if len(raw) != state.info.raw_len:
            raise ValueError(f"stream {stream!r} takes {state.info.raw_len} raw bytes, got {len(raw)}")
        if isinstance(state, _ChunkStream):
            try:
                row = state.pack(t, *values)
            except (struct.error, TypeError):
                if len(values) != state.ncols:
                    raise ValueError(f"stream {stream!r} takes {state.ncols} values, "
                                     f"got {len(values)}") from None
                row = state.pack(t, *[np.nan if v is None else v for v in values])
            if raw:
                row += bytes(raw)
            with self._lock:
                self._check_open()
                if not state.buf:
                    state.t0 = t
                state.t1 = t
                state.buf += row
                state.rows += 1
                self.rows += 1
            return
        if len(values) != state.ncols:
            raise ValueError(f"stream {stream!r} takes {state.ncols} values, got {len(values)}")
        for v in values:
            if type(v) not in _PLAIN:           # a numpy scalar would be stored as a BLOB
                values = [v if type(v) in _PLAIN else float(v) for v in values]     # type: ignore[arg-type]
                break
        blob = bytes(raw) if raw else None
        micros = None if latency is None else int(latency * 1e6)
        with self._lock:
            self._check_open()
            if t > state.max_t:
                state.max_t, n = t, 0
            else:                               # equal or out-of-order time: keep the key unique
                state.dup += 1
                n = state.dup
            if state.parts == 1:
                state.buf[0].append((self.id, state.sid, 0, t, n, blob, micros, *values))
            else:
                for part in range(state.parts):
                    state.buf[part].append((self.id, state.sid, part, t, n, blob if part == 0 else None,
                                            micros if part == 0 else None,
                                            *values[part * SLOTS:(part + 1) * SLOTS]))
            state.rows += 1
            self.rows += 1

    def append_array(self, stream: str, rows: np.ndarray, latency: np.ndarray | None = None) -> None:
        """Add many rows at once: a structured array in the stream's own dtype."""
        state = self._streams[stream]
        if len(rows) == 0:
            return
        if rows.dtype != state.info.dtype:
            rows = rows.astype(state.info.dtype)
        if isinstance(state, _ChunkStream):
            if len(rows) > 1 and np.any(np.diff(rows["t"]) < 0):
                rows = rows[np.argsort(rows["t"], kind="stable")]
            blocks = [rows[i:i + BLOCK_ROWS].copy() for i in range(0, len(rows), BLOCK_ROWS)]
            with self._lock:
                self._check_open()
                state.blocks += blocks
                state.rows += len(rows)
                self.rows += len(rows)
            return
        t = rows["t"].astype(np.float64)
        times = t.tolist()
        if t[0] > state.max_t and (len(t) == 1 or bool(np.all(np.diff(t) > 0))):
            ties = [0] * len(times)
            top, dup = times[-1], state.dup
        else:
            ties, top, dup = [], state.max_t, state.dup
            for value in times:
                if value > top:
                    top = value
                    ties.append(0)
                else:
                    dup += 1
                    ties.append(dup)
        blobs: list[bytes | None]
        if state.info.raw_len:
            width = state.info.raw_len
            packed = np.ascontiguousarray(rows["raw"]).tobytes()
            blobs = [packed[i * width:(i + 1) * width] for i in range(len(times))]
        else:
            blobs = [None] * len(times)
        micros: list[int | None] = [None] * len(times)
        if latency is not None and len(latency) == len(times):
            micros = [None if x != x else int(x * 1e6) for x in np.asarray(latency, dtype=np.float64).tolist()]
        names = state.info.column_names
        columns = [rows[name].astype(np.float64).tolist() for name in names]
        did, sid = self.id, state.sid
        built = []
        for part in range(state.parts):
            mine = columns[part * SLOTS:(part + 1) * SLOTS]
            n = len(times)
            head = (blobs, micros) if part == 0 else ([None] * n, [None] * n)
            built.append(list(zip([did] * n, [sid] * n, [part] * n, times, ties, *head, *mine)))
        with self._lock:
            self._check_open()
            for part, tuples in enumerate(built):
                state.buf[part] += tuples
            state.max_t, state.dup = top, dup
            state.rows += len(times)
            self.rows += len(times)

    def append_columns(self, stream: str, t: np.ndarray, columns: Mapping[str, np.ndarray],
                       raw: np.ndarray | None = None) -> None:
        """Add many rows from separate arrays. Missing columns are filled with NaN/0."""
        info = self._streams[stream].info
        rows = np.zeros(len(t), dtype=info.dtype)
        rows["t"] = t
        for col in info.columns:
            if col.name in columns:
                rows[col.name] = columns[col.name]
            elif col.dtype[0] == "f":
                rows[col.name] = np.nan
        if info.raw_len and raw is not None:
            rows["raw"] = raw
        self.append_array(stream, rows)

    def event(self, t: float, kind: str, text: str = "", data: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            self._check_open()
            self._events.append(Event(float(t), kind, text, dict(data or {})))

    def capture(self, kind: str, t_trigger: float, t_start: float, t_end: float, *, rule: str = "",
                reason: str = "", label: str = "", message: str = "", severity: str = "",
                wall: datetime | None = None, complete: bool = True, fired: Sequence[Any] = (),
                meta: Mapping[str, Any] | None = None, wait: bool = True,
                on_saved: Callable[[DriveRef, int], None] | None = None) -> tuple[DriveRef, int] | None:
        """Record a capture: the window [t_start, t_end] of this drive and what
        fired. No data is copied. Returns (ref, rows in the window), or None
        with ``wait=False`` (``on_saved`` is then called on the store's thread)."""
        wall = wall or datetime.now()

        def job(conn: sqlite3.Connection) -> tuple[DriveRef, int]:
            self.store._write(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                name = db.unique_name(conn, "captures", f"{kind}_{wall:%Y%m%d_%H%M%S}")
                rows = _count_rows(conn, self.id, t_start, t_end)
                conn.execute(
                    "INSERT INTO captures(drive_id, name, kind, rule, reason, label, message, severity, "
                    "t_trigger, t_start, t_end, wall_unix, wall_iso, complete, rows, fired, meta) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.id, name, kind, rule, reason, label, message, severity, t_trigger, t_start, t_end,
                     wall.timestamp(), wall.isoformat(timespec="milliseconds"), 1 if complete else 0, rows,
                     json.dumps(list(fired), default=str), json.dumps(dict(meta or {}), ensure_ascii=False,
                                                                     default=str)))
                conn.execute("UPDATE drives SET capture_count = capture_count + 1, "
                             "stall_count = stall_count + ? WHERE id=?", (1 if kind == "stall" else 0, self.id))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            ref = DriveRef(self.store.path, self.name, name)
            if on_saved is not None:
                on_saved(ref, rows)
            return ref, rows
        return self.store._call(job, wait=wait)

    def end(self, status: str = "complete", reason: str = "stopped") -> None:
        """Flush, pack the black box tail, set the end time and the status."""
        with self._lock:
            if self.closed:
                return
            self.closed = True

        def job(conn: sqlite3.Connection) -> None:
            try:
                self.store._write(conn)
                db.finish_drive(conn, self.id, status, reason)
            finally:
                db.mark_active(self.store.path, self.id, False)
                with self._lock:
                    if self in self.store.drives:
                        self.store.drives.remove(self)
        self.store._call(job)

    def __enter__(self) -> "DriveWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.end()

    # -- internals --

    def _check_open(self) -> None:
        if self.closed:
            raise ValueError(f"{self.ref}: the drive is closed")

    def _has_data(self) -> bool:
        return bool(self._defs or self._events or self._meta or self._view_dirty
                    or any((s.buf or s.blocks) if isinstance(s, _ChunkStream) else any(s.buf)
                           for s in self._streams.values()))


def _count_rows(conn: sqlite3.Connection, drive_id: int, t0: float, t1: float) -> int:
    total = 0
    for sid, (info, storage) in db.stream_infos(conn, drive_id).items():
        if storage == "chunks":
            total += len(db.read_chunks(conn, drive_id, sid, info.dtype, t0, t1))
        else:
            total += int(conn.execute("SELECT count(*) FROM samples WHERE drive_id=? AND stream_id=? AND part=0 "
                                      "AND t >= ? AND t <= ?", (drive_id, sid, t0, t1)).fetchone()[0])
    return total


class _Batch:
    """Everything one flush writes, taken from the buffers under the lock."""

    def __init__(self) -> None:
        self.defs: list[tuple[int, int, StreamInfo, str]] = []
        self.meta: list[tuple[int, dict[str, Any]]] = []
        self.many: list[tuple[str, list[tuple]]] = []
        self.raw: list[tuple[DriveWriter, _ChunkStream, bytes, int, float, float]] = []
        self.blocks: list[tuple[int, _ChunkStream, np.ndarray]] = []
        self.events: list[tuple[int, Event]] = []
        self.views: list[int] = []
        self.rows = 0

    def __bool__(self) -> bool:
        return bool(self.defs or self.meta or self.many or self.raw or self.blocks or self.events or self.views)


class Store:
    """The database opened for writing. See the module text."""

    def __init__(self, path: str | os.PathLike, *, flush_interval: float = 0.5, background: bool = True,
                 fsync: bool = True, compact_s: float = 60.0, checkpoint_s: float = 10.0,
                 timeout: float = 5.0):
        self.path = Path(os.path.expanduser(os.fspath(path)))
        self.flush_interval = flush_interval
        self.compact_s = compact_s
        self.checkpoint_s = checkpoint_s if fsync else 0.0
        self.error: Exception | None = None     # last write error, if any
        self.drives: list[DriveWriter] = []
        self.closed = False
        self._lock = threading.Lock()           # guards the buffers
        self._io = threading.RLock()            # guards the connection
        #: ``timeout``: how long a commit waits for another writer (another
        #: hondalog process) before it gives up and keeps the rows for the next one
        self._conn = db.connect(self.path, write=True, create=True,
                                synchronous="NORMAL" if fsync else "OFF", timeout=timeout)
        self._retry: _Batch | None = None
        self._heartbeat = time.monotonic()
        self._checkpoint = time.monotonic()
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._run, name="hondalog-store", daemon=True)
            self._thread.start()

    # -- API (any thread) --

    def begin_drive(self, meta: Mapping[str, Any] | None = None, *, started: datetime | None = None,
                    name: str | None = None) -> DriveWriter:
        """Start a drive. Its name (``drive_2026_11_16_0712``, made unique) is
        also the name of its view. The row is in the database when this returns."""
        header = dict(meta or {})
        if started is None:
            unix = header.get("started_unix")
            if isinstance(unix, (int, float)):
                started = datetime.fromtimestamp(float(unix))
            else:
                try:                            # an imported log knows its start as text
                    started = datetime.fromisoformat(str(header.get("started", "")))
                except ValueError:
                    started = datetime.now()
        header.setdefault("started", started.isoformat(timespec="seconds"))
        header.setdefault("started_unix", started.timestamp())

        def job(conn: sqlite3.Connection) -> DriveWriter:
            self._check_open()
            conn.execute("BEGIN IMMEDIATE")
            try:
                unique = db.unique_name(conn, "drives", name or db.drive_stem(started))
                cur = conn.execute("INSERT INTO drives(name, status, writer_pid, heartbeat_at) VALUES(?,?,?,?)",
                                   (unique, "recording", os.getpid(), time.time()))
                drive_id = int(cur.lastrowid or 0)
                apply_meta(conn, drive_id, header)
                schema.make_drive_view(conn, drive_id)
                db.mark_active(self.path, drive_id, True)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            drive = DriveWriter(self, drive_id, unique)
            with self._lock:
                self.drives.append(drive)
            return drive
        try:
            return self._call(job)
        except sqlite3.Error as exc:
            raise OSError(f"{self.path}: {exc}") from None

    def flush(self, sync: bool = False) -> None:
        """Write everything buffered so far (and wait for it). ``sync=True``
        also syncs the write-ahead log to the disk."""
        def job(conn: sqlite3.Connection) -> None:
            self._write(conn)
            if sync:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        self._call(job)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """End any drive still open, write everything, close the database."""
        with self._lock:
            if self.closed:
                return
            open_drives = list(self.drives)
        for drive in open_drives:
            try:
                drive.end(reason="closed")
            except (sqlite3.Error, OSError) as exc:
                self.error = exc if isinstance(exc, OSError) else OSError(str(exc))
        with self._lock:
            self.closed = True
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            self._jobs.put(None)
            thread.join()
        with self._io:
            try:
                self._write(self._conn)
                # pages freed by packing the black box tail go back to the disk (bounded: 8 MB a time)
                self._conn.execute("PRAGMA incremental_vacuum(2000)").fetchall()
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            except sqlite3.Error as exc:
                self.error = self.error or OSError(f"{self.path}: {exc}")
            finally:
                self._conn.close()
        if self.error is not None:
            raise self.error

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the writer thread --

    def _check_open(self) -> None:
        if self.closed:
            raise ValueError(f"{self.path}: the store is closed")

    def _call(self, fn: Callable[[sqlite3.Connection], Any], wait: bool = True) -> Any:
        """Run ``fn(connection)`` on the writer thread (inline if there is none)."""
        thread = self._thread
        if thread is None or thread is threading.current_thread() or not thread.is_alive():
            with self._io:
                return fn(self._conn)
        job = _Job(fn)
        self._jobs.put(job)
        if not wait:
            return None
        job.done.wait()
        if job.error is not None:
            raise job.error
        return job.result

    def _run(self) -> None:
        due = time.monotonic() + self.flush_interval
        while True:
            try:
                job = self._jobs.get(timeout=max(0.0, due - time.monotonic()))
            except queue.Empty:
                with self._io:
                    self._write(self._conn)
                due = time.monotonic() + self.flush_interval
                continue
            if job is None:
                return
            with self._io:
                try:
                    job.result = job.fn(self._conn)
                except BaseException as exc:    # hand it to the caller, keep the thread alive
                    job.error = exc
                    if not isinstance(exc, (sqlite3.Error, OSError, ValueError)):
                        self.error = OSError(f"{self.path}: {exc!r}")
                    elif isinstance(exc, (sqlite3.Error, OSError)):
                        self.error = exc if isinstance(exc, OSError) else OSError(f"{self.path}: {exc}")
            job.done.set()
            if time.monotonic() >= due:             # jobs must not starve the regular commit
                with self._io:
                    self._write(self._conn)
                due = time.monotonic() + self.flush_interval

    def _take(self) -> _Batch:
        """Move everything buffered into a batch (under the buffer lock)."""
        batch = self._retry or _Batch()
        self._retry = None
        with self._lock:
            for drive in self.drives:
                if not drive._has_data():
                    continue
                for sid, info, kind in drive._defs:
                    batch.defs.append((drive.id, sid, info, kind))
                drive._defs = []
                if drive._meta:
                    batch.meta.append((drive.id, drive._meta))
                    drive._meta = {}
                for state in drive._streams.values():
                    if isinstance(state, _ChunkStream):
                        if state.buf:
                            n = len(state.buf) // state.item
                            batch.raw.append((drive, state, bytes(state.buf), n, state.t0, state.t1))
                            batch.rows += n
                            state.buf.clear()
                        if state.blocks:
                            batch.blocks += [(drive.id, state, block) for block in state.blocks]
                            batch.rows += sum(len(b) for b in state.blocks)
                            state.blocks = []
                    else:
                        for part, rows in enumerate(state.buf):
                            if rows:
                                batch.many.append((state.sql[part], rows))
                                batch.rows += len(rows)
                                state.buf[part] = []
                if drive._events:
                    batch.events += [(drive.id, e) for e in drive._events]
                    drive._events = []
                if drive._view_dirty:
                    batch.views.append(drive.id)
                    drive._view_dirty = False
        return batch

    def _write(self, conn: sqlite3.Connection) -> None:
        """One transaction with everything buffered. Never raises for a disk
        error: the recording goes on and ``error`` says what happened."""
        batch = self._take()
        now = time.monotonic()
        beat = now - self._heartbeat >= HEARTBEAT_S and bool(self.drives)
        if not batch and not beat:
            self._maybe_checkpoint(conn, now)
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._write_batch(conn, batch)
                if beat:
                    self._heartbeat = now
                    conn.executemany("UPDATE drives SET heartbeat_at=? WHERE id=?",
                                     [(time.time(), d.id) for d in self.drives])
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        except Exception as exc:                    # disk full, locked, stick pulled: keep polling alive
            self.error = exc if isinstance(exc, OSError) else OSError(f"{self.path}: {exc}")
            busy = isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc))
            if busy and batch.rows <= MAX_RETRY_ROWS:
                self._retry = batch                 # another writer had the file: try again next time
            return
        self._maybe_checkpoint(conn, now)

    def _maybe_checkpoint(self, conn: sqlite3.Connection, now: float) -> None:
        if self.checkpoint_s and now - self._checkpoint >= self.checkpoint_s:
            self._checkpoint = now
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            except sqlite3.Error:
                pass

    def _write_batch(self, conn: sqlite3.Connection, batch: _Batch) -> None:
        for drive_id, sid, info, kind in batch.defs:
            conn.execute("INSERT INTO streams(drive_id, id, name, source, storage, raw_len, addr, attrs) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (drive_id, sid, info.name, info.source, kind, info.raw_len,
                          info.attrs.get("addr") if isinstance(info.attrs.get("addr"), int) else None,
                          json.dumps(dict(info.attrs), ensure_ascii=False, default=str)))
            conn.executemany(
                "INSERT INTO channels(drive_id, stream_id, position, part, slot, name, units, label, kind, "
                "dtype, decimals, lo, hi, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(drive_id, sid, i, i // SLOTS, i % SLOTS, c.name, c.units, c.label, c.kind, c.dtype,
                  c.decimals, c.lo, c.hi, info.source) for i, c in enumerate(info.columns)])
        for drive_id, items in batch.meta:
            apply_meta(conn, drive_id, items)
        for sql, rows in batch.many:
            conn.executemany(sql, rows)
        for drive, state, data, n, t0, t1 in batch.raw:
            conn.execute("INSERT INTO chunks(drive_id, stream_id, t_start, t_end, n_rows, codec, data) "
                         "VALUES(?,?,?,?,?,?,?)", (drive.id, state.sid, t0, t1, n, codec.RAW, data))
            if state.raw_since is None:
                state.raw_since = t0
            if t1 - state.raw_since >= self.compact_s:
                db.compact_stream(conn, drive.id, state.sid, state.info)
                state.raw_since = None
        for drive_id, state, block in batch.blocks:
            db.write_block(conn, drive_id, state.sid, state.info, block)
        plain = [(d, e.t, e.kind, e.text, json.dumps(dict(e.data), ensure_ascii=False, default=str)
                  if e.data else None) for d, e in batch.events if e.kind != "alarm"]
        if plain:
            conn.executemany("INSERT INTO events(drive_id, t, kind, text, data) VALUES(?,?,?,?,?)", plain)
        alarms = []
        for d, e in batch.events:
            if e.kind == "alarm":
                value = e.data.get("value")
                alarms.append((d, e.t, str(e.data.get("alarm", "")), str(e.data.get("kind", "")),
                               str(e.data.get("severity", "")), str(e.data.get("channel", "") or ""),
                               float(value) if isinstance(value, (int, float)) else None,
                               1 if e.data.get("unverified") else 0, e.text,
                               json.dumps(dict(e.data), ensure_ascii=False, default=str) if e.data else None))
        if alarms:
            conn.executemany("INSERT INTO alarms(drive_id, t, name, state, severity, channel, value, "
                             "unverified, text, data) VALUES(?,?,?,?,?,?,?,?,?,?)", alarms)
        for drive_id in batch.views:
            schema.make_drive_view(conn, drive_id)


# ---- whole-drive operations -------------------------------------------------

@contextmanager
def new_drive(path: str | os.PathLike, meta: Mapping[str, Any] | None = None, *,
              status: str = "complete", name: str | None = None) -> Iterator[DriveWriter]:
    """Write one drive on the calling thread (converters, tests):

        with new_drive("hondalog.db", {"ecu_id": "..."}) as drive:
            drive.add_stream("s", [Column("a")])
            drive.append("s", 0.0, [1.0])
        drive.ref       # the drive, complete
    """
    store = Store(path, background=False)
    try:
        drive = store.begin_drive(meta, name=name)
        yield drive
        drive.end(status=status)
    finally:
        store.close()


def write_log(log: Log, path: str | os.PathLike, meta: Mapping[str, Any] | None = None, *,
              name: str | None = None, status: str = "imported") -> DriveRef:
    """Save any ``Log`` (imported, cut, re-decoded) as a new drive in the
    database ``path`` (made if it is not there). Returns the new drive."""
    header = {**log.meta, **dict(meta or {})}
    store = Store(path, background=False)
    try:
        drive = store.begin_drive(header, name=name)
        for stream in log.streams.values():
            info = stream.info
            drive.add_stream(info.name, info.columns, source=info.source, raw_len=info.raw_len,
                             attrs=info.attrs)
            data = stream.data
            drive.append_array(info.name, data, stream.latency if len(data) else None)
        for event in log.events:
            drive.event(event.t, event.kind, event.text, event.data)
        drive.end(status=status, reason="import" if status == "imported" else "stopped")
    finally:
        store.close()
    return drive.ref


def merge_window(sources: Sequence[Log], t0: float, t1: float) -> Log:
    """Rows and events between t0 and t1 from one or more logs on the same
    timebase (two drives of one recording session), as one in-memory log."""
    streams: dict[str, Stream] = {}
    events = []
    meta: dict[str, Any] = {}
    for source in sources:
        part = source.window(t0, t1)
        meta.update(part.meta)
        events.extend(part.events)
        for name, stream in part.streams.items():
            old = streams.get(name)
            if old is None or old.info.dtype != stream.info.dtype:
                streams[name] = stream
            else:
                streams[name] = Stream(stream.info, data=np.concatenate([old.data, stream.data]))
    return Log(meta, streams.values(), events)


def extract_window(sources: Sequence[str | os.PathLike | DriveRef | Log], out_path: str | os.PathLike,
                   t0: float, t1: float, meta: Mapping[str, Any] | None = None) -> tuple[DriveRef, int]:
    """Copy [t0, t1] of one or more logs into the database ``out_path`` as a
    new drive: every stream is kept, so the ECU reads and the black box rows
    of the same seconds travel together. Returns (drive, rows written)."""
    opened = [s if isinstance(s, Log) else open_log(s) for s in sources]
    try:
        cut = merge_window(opened, t0, t1)
    finally:
        for source, log in zip(sources, opened):
            if not isinstance(source, Log):
                log.close()
    return write_log(cut, out_path, meta), cut.rows


def annotate(ref: DriveRef | str | os.PathLike, **meta: Any) -> None:
    """Add or change header facts (notes, tags, keep, ...) of a drive."""
    found = db.parse_ref(ref, db.db_path())
    if found is None:
        raise ValueError(f"{ref}: not a drive in a hondalog database")
    conn = db.connect(found.db, write=True)
    try:
        row = conn.execute("SELECT id FROM drives WHERE name=?", (found.drive,)).fetchone()
        if row is None:
            raise ValueError(f"{ref}: no such drive")
        conn.execute("BEGIN IMMEDIATE")
        try:
            apply_meta(conn, int(row[0]), meta)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
