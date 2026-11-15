"""The native log file (``.hlog``): an append-only chunk container.

Layout (all little-endian)::

    file header   16 bytes   "HONDALOG", u16 version, u16 header size, u32 reserved
    chunk*                   16-byte chunk header + payload
    trailer       24 bytes   "HLOGTAIL", u64 offset of the index chunk, u32 crc, "END\\n"

    chunk header  4s magic F0 "HLC" | u8 type | u8 flags | u16 stream id
                  | u32 payload length | u32 crc32(first 12 header bytes + payload)

Chunk types:

    META    JSON object, merged into the log's metadata in file order
    STREAM  JSON stream definition (name, columns, raw_len, attrs); the
            chunk's stream id is the id that DATA chunks refer to
    DATA    packed rows of one stream: f8 time, the columns, then raw bytes
    EVENT   JSON list of events (markers, triggers, alarms, notes)
    INDEX   table of every chunk before it; written on a clean close

Nothing is ever rewritten. A crash leaves a file without index and trailer,
possibly with a torn last chunk: the reader scans the chunk headers instead
(cheap: one header per stream per flush) and drops the torn tail. With the
default settings a flush and fdatasync happen every 0.5 s on a background
thread, so a power cut loses at most that plus the sync time, and the thread
that calls ``append`` never waits for the disk.
"""

from __future__ import annotations

import json
import mmap
import os
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

import numpy as np

from .model import Column, Event, StreamInfo

FILE_MAGIC = b"HONDALOG"
FORMAT_VERSION = 1
FILE_HEADER = struct.Struct("<8sHHI")
CHUNK_MAGIC = b"\xf0HLC"
CHUNK_HEADER = struct.Struct("<4sBBHII")
TRAILER_MAGIC = b"HLOGTAIL"
TRAILER_END = b"END\n"
TRAILER = struct.Struct("<8sQI4s")

META, STREAM, DATA, EVENT, INDEX = 1, 2, 3, 4, 5
CHUNK_NAMES = {META: "meta", STREAM: "stream", DATA: "data", EVENT: "event", INDEX: "index"}

#: one row per chunk; this is both the in-memory chunk table and the INDEX payload
INDEX_DTYPE = np.dtype([("offset", "<u8"), ("type", "u1"), ("stream", "<u2"),
                        ("length", "<u4"), ("rows", "<u4"), ("t0", "<f8"), ("t1", "<f8")])

MAX_CHUNK = 64 * 1024 * 1024        # sanity limit when scanning a damaged file
EXTENSION = ".hlog"


class LogFormatError(Exception):
    """The file is not a hondalog log, or is damaged beyond the header."""


def _crc(header12: bytes, payload: bytes | memoryview) -> int:
    return zlib.crc32(payload, zlib.crc32(header12)) & 0xFFFFFFFF


def pack_chunk(ctype: int, stream: int, payload: bytes) -> bytes:
    head = struct.pack("<4sBBHI", CHUNK_MAGIC, ctype, 0, stream, len(payload))
    return head + struct.pack("<I", _crc(head, payload)) + payload


def pack_trailer(index_offset: int) -> bytes:
    head = struct.pack("<8sQ", TRAILER_MAGIC, index_offset)
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF) + TRAILER_END


def is_hlog(path: str | os.PathLike) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(FILE_MAGIC)) == FILE_MAGIC
    except OSError:
        return False


# ---- reading the chunk table ------------------------------------------------

@dataclass
class ChunkTable:
    """Where every chunk is. Built from the index if there is one, else by scanning."""

    entries: np.ndarray             # INDEX_DTYPE
    end: int                        # offset just past the last good chunk
    indexed: bool                   # True: taken from a valid index + trailer
    junk: int                       # bytes that were skipped or cut off (0 for a clean file)


def _read_trailer(buf: Any, size: int) -> int | None:
    """Offset of the index chunk if the file ends with a valid trailer."""
    if size < FILE_HEADER.size + CHUNK_HEADER.size + TRAILER.size:
        return None
    magic, offset, crc, end = TRAILER.unpack_from(buf, size - TRAILER.size)
    if magic != TRAILER_MAGIC or end != TRAILER_END:
        return None
    if zlib.crc32(bytes(buf[size - TRAILER.size:size - 8])) & 0xFFFFFFFF != crc:
        return None
    return int(offset) if FILE_HEADER.size <= offset < size - TRAILER.size else None


def _chunk_at(buf: Any, pos: int, size: int, check_crc: bool = True) -> tuple[int, int, int] | None:
    """(type, stream, payload length) of a valid chunk at ``pos``, else None."""
    if pos + CHUNK_HEADER.size > size:
        return None
    magic, ctype, _flags, stream, length, crc = CHUNK_HEADER.unpack_from(buf, pos)
    if magic != CHUNK_MAGIC or ctype not in CHUNK_NAMES or length > MAX_CHUNK:
        return None
    start = pos + CHUNK_HEADER.size
    if start + length > size:
        return None
    if check_crc and _crc(bytes(buf[pos:pos + 12]), buf[start:start + length]) != crc:
        return None
    return ctype, stream, length


def read_chunk_table(buf: Any, size: int) -> ChunkTable:
    """Find every chunk in ``buf`` (an mmap or bytes of the whole file)."""
    if size < FILE_HEADER.size:
        raise LogFormatError("file too short for a hondalog header")
    magic, version, header_size, _ = FILE_HEADER.unpack_from(buf, 0)
    if magic != FILE_MAGIC:
        raise LogFormatError("not a hondalog log (bad magic)")
    if version > FORMAT_VERSION:
        raise LogFormatError(f"log format version {version} is newer than this program")

    index_at = _read_trailer(buf, size)
    if index_at is not None:
        found = _chunk_at(buf, index_at, size)
        if found is not None and found[0] == INDEX and found[2] % INDEX_DTYPE.itemsize == 0:
            start = index_at + CHUNK_HEADER.size
            entries = np.frombuffer(buf, INDEX_DTYPE, found[2] // INDEX_DTYPE.itemsize, start).copy()
            return ChunkTable(entries, index_at, True, 0)
    return scan_chunks(buf, size, header_size)


def scan_chunks(buf: Any, size: int, start: int = FILE_HEADER.size) -> ChunkTable:
    """Walk the chunk headers. Skips damage by searching for the next chunk magic."""
    rows: list[tuple] = []
    row_size: dict[int, int] = {}
    pos, end, junk = start, start, 0
    while pos < size:
        found = _chunk_at(buf, pos, size)
        if found is None:
            if bytes(buf[pos:pos + 8]) == TRAILER_MAGIC and pos + TRAILER.size <= size:
                pos += TRAILER.size             # a trailer that was later appended to
                end = pos
                continue
            nxt = buf.find(CHUNK_MAGIC, pos + 1)
            if nxt < 0:
                junk += size - pos
                break
            junk += nxt - pos
            pos = nxt
            continue
        ctype, stream, length = found
        body = pos + CHUNK_HEADER.size
        nrows, t0, t1 = 0, np.nan, np.nan
        if ctype == STREAM:
            try:
                info = StreamInfo.from_json(json.loads(bytes(buf[body:body + length])))
                row_size[stream] = info.dtype.itemsize
            except (ValueError, KeyError, TypeError):
                pass
        elif ctype == DATA:
            item = row_size.get(stream, 0)
            if item and length >= item:
                nrows = length // item
                t0 = struct.unpack_from("<d", buf, body)[0]
                t1 = struct.unpack_from("<d", buf, body + (nrows - 1) * item)[0]
        if ctype != INDEX:
            rows.append((pos, ctype, stream, length, nrows, t0, t1))
        pos = body + length
        end = pos
    return ChunkTable(np.array(rows, dtype=INDEX_DTYPE), end, False, junk)


# ---- writing ----------------------------------------------------------------

class _StreamState:
    __slots__ = ("info", "sid", "pack", "buf", "rows", "t0", "t1", "item", "ncols")

    def __init__(self, info: StreamInfo, sid: int):
        self.info, self.sid = info, sid
        self.pack = struct.Struct(info.struct_format).pack
        self.item = info.dtype.itemsize
        self.ncols = len(info.columns)
        self.buf = bytearray()
        self.rows = 0
        self.t0 = self.t1 = 0.0


class LogWriter:
    """Appends streams, events and metadata to a ``.hlog`` file.

    ``append`` and ``event`` only touch memory, so they are safe to call from
    the poller thread. The data reaches the disk every ``flush_interval``
    seconds on a background thread (``background=False``: only on ``flush``
    and ``close``, which is what tests and offline converters want).

    ``append=True`` continues an existing file, also one that was cut off by a
    crash: the torn tail is dropped and new chunks follow the last good one.
    """

    def __init__(self, path: str | os.PathLike, meta: Mapping[str, Any] | None = None, *,
                 flush_interval: float = 0.5, fsync: bool = True, background: bool = True,
                 append: bool = False):
        self.path = Path(path)
        self.fsync = fsync
        self.flush_interval = flush_interval
        self.error: Exception | None = None     # last background write error, if any
        self.rows = 0                            # rows accepted so far (all streams)
        self._lock = threading.Lock()            # guards the buffers
        self._io = threading.Lock()              # guards the file
        self._streams: dict[str, _StreamState] = {}
        self._by_id: dict[int, _StreamState] = {}
        self._pending: list[tuple[int, int, bytes, int, float, float]] = []
        self._events: list[Event] = []
        self._table: list[tuple] = []
        self._closed = False
        self._fh: BinaryIO
        self._pos = 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if append and self.path.exists() and self.path.stat().st_size >= FILE_HEADER.size:
            self._open_existing()
        else:
            self._fh = open(self.path, "wb")
            self._fh.write(FILE_HEADER.pack(FILE_MAGIC, FORMAT_VERSION, FILE_HEADER.size, 0))
            self._pos = FILE_HEADER.size
        if meta:
            self.set_meta(**dict(meta))
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._run, name="hondalog-logwriter", daemon=True)
            self._thread.start()

    def _open_existing(self) -> None:
        with open(self.path, "rb") as fh:
            data = fh.read()
        table = read_chunk_table(data, len(data))
        if table.indexed:                        # keep the old index chunk, it is harmless
            table = scan_chunks(data, len(data))
        for row in table.entries:
            if row["type"] == STREAM:
                body = int(row["offset"]) + CHUNK_HEADER.size
                info = StreamInfo.from_json(json.loads(data[body:body + int(row["length"])]))
                state = _StreamState(info, int(row["stream"]))
                self._streams[info.name] = state
                self._by_id[state.sid] = state
        self._table = [tuple(r) for r in table.entries.tolist()]
        self._fh = open(self.path, "r+b")
        self._fh.truncate(table.end)
        self._fh.seek(table.end)
        self._pos = table.end

    # -- API (any thread) --

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def bytes_written(self) -> int:
        return self._pos

    def stream(self, name: str) -> StreamInfo | None:
        state = self._streams.get(name)
        return state.info if state else None

    def set_meta(self, **items: Any) -> None:
        """Add or replace metadata keys. Values must be JSON-serialisable."""
        payload = json.dumps(items, ensure_ascii=False, default=str).encode("utf-8")
        with self._lock:
            self._check_open()
            self._pending.append((META, 0, payload, 0, np.nan, np.nan))

    def add_stream(self, name: str, columns: Sequence[Column], *, source: str = "",
                   raw_len: int = 0, attrs: Mapping[str, Any] | None = None) -> str:
        """Define a stream and return its name. Defining the same stream twice is fine."""
        info = StreamInfo(name, tuple(columns), source, raw_len, dict(attrs or {}))
        with self._lock:
            self._check_open()
            old = self._streams.get(name)
            if old is not None:
                if old.info.dtype != info.dtype:
                    raise ValueError(f"stream {name!r} already exists with other columns")
                return name
            sid = len(self._by_id) + 1
            if sid > 0xFFFF:
                raise ValueError("too many streams")
            state = _StreamState(info, sid)
            self._streams[name] = state
            self._by_id[sid] = state
            payload = json.dumps(info.to_json(), ensure_ascii=False).encode("utf-8")
            self._pending.append((STREAM, sid, payload, 0, np.nan, np.nan))
        return name

    def append(self, stream: str, t: float, values: Sequence[float | None],
               raw: bytes = b"") -> None:
        """Add one row. ``None`` in a float column is stored as NaN."""
        state = self._streams[stream]
        try:
            row = state.pack(t, *values)
        except (struct.error, TypeError):
            if len(values) != state.ncols:
                raise ValueError(f"stream {stream!r} takes {state.ncols} values, "
                                 f"got {len(values)}") from None
            row = state.pack(t, *[np.nan if v is None else v for v in values])
        if state.info.raw_len:
            if len(raw) != state.info.raw_len:
                raise ValueError(f"stream {stream!r} takes {state.info.raw_len} raw bytes, "
                                 f"got {len(raw)}")
            row += bytes(raw)
        with self._lock:
            self._check_open()
            if not state.buf:
                state.t0 = t
            state.t1 = t
            state.buf += row
            state.rows += 1
            self.rows += 1

    def append_array(self, stream: str, rows: np.ndarray) -> None:
        """Add many rows at once: a structured array in the stream's own dtype."""
        state = self._streams[stream]
        if len(rows) == 0:
            return
        if rows.dtype != state.info.dtype:
            rows = rows.astype(state.info.dtype)
        data = rows.tobytes()
        t0, t1 = float(rows["t"][0]), float(rows["t"][-1])
        with self._lock:
            self._check_open()
            if not state.buf:
                state.t0 = t0
            state.t1 = t1
            state.buf += data
            state.rows += len(rows)
            self.rows += len(rows)

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

    def flush(self, sync: bool = False) -> None:
        """Write everything buffered so far. ``sync=True`` also waits for the disk."""
        self._flush(sync)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """Flush, write the index and trailer, close the file."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        try:
            self._flush(False, final=True)
        finally:
            with self._io:
                self._fh.close()
        if self.error is not None:
            raise self.error

    def __enter__(self) -> "LogWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals --

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError(f"{self.path}: log is closed")

    def _run(self) -> None:
        while not self._wake.wait(self.flush_interval):
            self._flush(self.fsync)

    def _take(self) -> list[tuple[int, int, bytes, int, float, float]]:
        """Move everything buffered into a list of chunks (under the buffer lock)."""
        with self._lock:
            out, self._pending = self._pending, []
            for state in self._by_id.values():
                if state.buf:
                    out.append((DATA, state.sid, bytes(state.buf), len(state.buf) // state.item,
                                state.t0, state.t1))
                    state.buf.clear()
            if self._events:
                events, self._events = self._events, []
                payload = json.dumps([e.to_json() for e in events], ensure_ascii=False,
                                     default=str).encode("utf-8")
                out.append((EVENT, 0, payload, len(events), events[0].t, events[-1].t))
        return out

    def _flush(self, sync: bool, final: bool = False) -> None:
        with self._io:
            if self._fh.closed:
                return
            chunks = self._take()
            if not chunks and not final:
                return
            known = len(self._table)
            try:
                parts = []
                pos = self._pos
                for ctype, sid, payload, nrows, t0, t1 in chunks:
                    blob = pack_chunk(ctype, sid, payload)
                    self._table.append((pos, ctype, sid, len(payload), nrows, t0, t1))
                    parts.append(blob)
                    pos += len(blob)
                if final:
                    index = np.array(self._table, dtype=INDEX_DTYPE).tobytes()
                    parts.append(pack_chunk(INDEX, 0, index))
                    parts.append(pack_trailer(pos))
                    pos += CHUNK_HEADER.size + len(index) + TRAILER.size
                self._fh.write(b"".join(parts))
                self._fh.flush()
                self._pos = pos
                if sync or (final and self.fsync):
                    os.fdatasync(self._fh.fileno())
            except OSError as exc:              # disk full, stick pulled: keep polling alive
                del self._table[known:]
                self.error = exc


def open_mmap(path: str | os.PathLike) -> tuple[Any, int]:
    """Map a file read-only. Returns (buffer, size); an empty file gives (b"", 0)."""
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size == 0:
            return b"", 0
        return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ), size
