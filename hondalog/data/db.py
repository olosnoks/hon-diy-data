"""Opening the database, naming drives, and the housekeeping every opener shares:
crash recovery, finishing a drive, compacting high-rate streams, deleting, vacuum.

    conn = connect(path)                    # read; migrates and recovers if needed
    conn = connect(path, write=True, create=True)
    ref = DriveRef(path, "drive_2026_11_16_0712")          # a drive
    ref = DriveRef(path, "drive_2026_11_16_0712", "stall_20261116_071455")   # a capture in it
    parse_ref("~/hondalog/hondalog.db#stall_20261116_071455")

The default database is ``~/hondalog/hondalog.db``. ``HONDALOG_DB`` names
another file; ``HONDALOG_DIR`` names one folder for the database and for the
loose files (exports, reports, black box captures), which otherwise go to
``~/hondalog/logs``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from . import codec, schema
from .model import Column, StreamInfo
from .schema import SLOTS, LogFormatError

DB_NAME = "hondalog.db"
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
#: a drive still marked "recording" whose writer has not been heard from for this long is dead
HEARTBEAT_STALE_S = 30.0
THUMB_POINTS = 64
#: rough bytes per ``samples`` row, for the size estimate retention works with
ROW_BYTES = 56

_active: set[tuple[str, int]] = set()          # (database, drive id) being written by this process
_active_lock = threading.Lock()


def files_dir(folder: str | os.PathLike | None = None) -> Path:
    """Where loose files live (exports, reports, fetched black box captures):
    ``folder`` if given (the folder of a database file), else
    ``$HONDALOG_DIR``, else ``~/hondalog/logs``."""
    if folder is not None:
        path = Path(folder)
        return path.parent if path.suffix.lower() in DB_SUFFIXES or path.is_file() else path
    env = os.environ.get("HONDALOG_DIR")
    return Path(env) if env else Path(os.path.expanduser("~/hondalog/logs"))


def db_path(folder: str | os.PathLike | None = None) -> Path:
    """The database file: ``hondalog.db`` in ``folder`` if given (a file path
    is taken as it is), else ``$HONDALOG_DB``, else ``hondalog.db`` in
    ``$HONDALOG_DIR``, else ``~/hondalog/hondalog.db``."""
    if folder is not None:
        path = Path(folder)
        return path if path.suffix.lower() in DB_SUFFIXES or path.is_file() else path / DB_NAME
    env = os.environ.get("HONDALOG_DB")
    if env:
        return Path(env)
    env = os.environ.get("HONDALOG_DIR")
    return Path(env) / DB_NAME if env else Path(os.path.expanduser("~/hondalog")) / DB_NAME


def is_database(path: str | os.PathLike) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(schema.SQLITE_MAGIC)) == schema.SQLITE_MAGIC
    except OSError:
        return False


# ---- references -------------------------------------------------------------

@dataclass(frozen=True)
class DriveRef:
    """One drive in one database, or one capture (a time range of a drive).

    This is what the recorder hands out where it used to hand out a file
    path: ``open_log`` takes it, ``str()`` gives ``database#name``, and
    ``name`` reads like the old file stem (``stall_20261116_071455``).
    """

    db: Path
    drive: str
    capture: str = ""

    @property
    def name(self) -> str:
        return self.capture or self.drive

    @property
    def stem(self) -> str:
        return self.name

    @property
    def suffix(self) -> str:
        return ""

    @property
    def parent(self) -> Path:
        return self.db.parent

    @property
    def drive_ref(self) -> "DriveRef":
        """The whole drive this capture was taken from (or the ref itself)."""
        return DriveRef(self.db, self.drive) if self.capture else self

    def exists(self) -> bool:
        try:
            conn = connect(self.db)
        except (OSError, ValueError):
            return False
        try:
            return find(conn, self.name) is not None
        finally:
            conn.close()

    def __str__(self) -> str:
        return f"{self.db}#{self.name}"


def parse_ref(text: str | os.PathLike | DriveRef, default_db: str | os.PathLike | None = None) -> DriveRef | None:
    """``database#name`` (or a bare drive or capture name, looked up in
    ``default_db``) as a ``DriveRef``. None if it does not name one."""
    if isinstance(text, DriveRef):
        return text
    spec = os.fspath(text)
    if "#" in spec:
        head, name = spec.rsplit("#", 1)
        target = Path(os.path.expanduser(head)) if head else db_path(default_db)
    else:
        if os.sep in spec or Path(spec).exists():
            return None
        target, name = db_path(default_db), spec
    if not is_database(target):
        return None
    try:
        conn = connect(target)
    except (OSError, ValueError):
        return None
    try:
        return find(conn, name, target)
    finally:
        conn.close()


def find(conn: sqlite3.Connection, name: str | int, db: Path | None = None) -> DriveRef | None:
    """A drive name, drive id, capture name, or "last" / "" for the newest drive."""
    path = db if db is not None else database_file(conn)
    key = str(name).strip()
    if key in ("", "last", "latest"):
        row = conn.execute("SELECT name FROM drives ORDER BY coalesce(started_at, 0) DESC, id DESC LIMIT 1").fetchone()
        return DriveRef(path, row[0]) if row else None
    row = conn.execute("SELECT name FROM drives WHERE name=?", (key,)).fetchone()
    if row is None and key.isdigit():
        row = conn.execute("SELECT name FROM drives WHERE id=?", (int(key),)).fetchone()
    if row is not None:
        return DriveRef(path, row[0])
    row = conn.execute("SELECT d.name, c.name FROM captures c JOIN drives d ON d.id = c.drive_id "
                       "WHERE c.name=?", (key,)).fetchone()
    return DriveRef(path, row[0], row[1]) if row else None


def database_file(conn: sqlite3.Connection) -> Path:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file)
    return Path("")


# ---- connections ------------------------------------------------------------

def connect(path: str | os.PathLike, *, write: bool = False, create: bool = False,
            synchronous: str = "NORMAL", timeout: float = 5.0) -> sqlite3.Connection:
    """Open the database: WAL, the newest schema, dead drives marked.

    A connection opened without ``write`` refuses writes (``query_only``),
    but opening may still migrate the schema and flag a drive whose recorder
    died, so the first open after a crash already shows it as "crashed".
    Raises OSError if the file cannot be opened, LogFormatError if it is not
    a hondalog database.
    """
    path = Path(path)
    exists = path.exists() and path.stat().st_size > 0
    if not exists and not create:
        raise FileNotFoundError(f"{path}: no such database")
    if exists and not is_database(path):
        raise LogFormatError(f"{path}: not a hondalog database")
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None, check_same_thread=False)
    except sqlite3.Error as exc:
        raise OSError(f"{path}: {exc}") from None
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        if not exists:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")        # before the first table
            conn.execute(f"PRAGMA application_id = {schema.APPLICATION_ID}")
        else:
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            if app_id != schema.APPLICATION_ID:
                raise LogFormatError(f"{path}: an SQLite file, but not a hondalog database")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA synchronous = {synchronous}")
        schema.migrate(conn)
        if conn.execute("SELECT 1 FROM drives WHERE status='recording' LIMIT 1").fetchone():
            recover(conn)
        if not write:
            conn.execute("PRAGMA query_only = ON")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise OSError(f"{path}: {exc}") from None
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise LogFormatError(f"{path}: {exc}") from None
    except BaseException:
        conn.close()
        raise
    return conn


def mark_active(path: Path, drive_id: int, active: bool) -> None:
    key = (str(Path(path).resolve()), drive_id)
    with _active_lock:
        if active:
            _active.add(key)
        else:
            _active.discard(key)


def _writer_alive(path: Path, drive_id: int, pid: int | None, heartbeat: float | None, now: float) -> bool:
    if pid is None:
        return False
    if pid == os.getpid():
        with _active_lock:
            return (str(path.resolve()), drive_id) in _active
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass                                    # it exists but is not ours
    return heartbeat is not None and now - heartbeat < HEARTBEAT_STALE_S


def recover(conn: sqlite3.Connection, now: float | None = None) -> list[int]:
    """Close every drive whose recorder is gone: status "crashed", an end
    time from the last row that reached the disk. Returns their ids."""
    now = time.time() if now is None else now
    path = database_file(conn)
    done = []
    rows = conn.execute("SELECT id, writer_pid, heartbeat_at FROM drives WHERE status='recording'").fetchall()
    for drive_id, pid, heartbeat in rows:
        if _writer_alive(path, int(drive_id), pid, heartbeat, now):
            continue
        try:
            finish_drive(conn, int(drive_id), "crashed", "crash")
        except sqlite3.OperationalError:
            continue                            # locked or read-only: the next opener does it
        done.append(int(drive_id))
    return done


# ---- stream definitions -----------------------------------------------------

def storage_for(info: StreamInfo) -> str:
    """"chunks" for high-rate streams (the black box), "rows" for the rest."""
    wanted = info.attrs.get("storage") if info.attrs else None
    if wanted in ("rows", "chunks"):
        return str(wanted)
    return "chunks" if info.source == "blackbox" else "rows"


def stream_infos(conn: sqlite3.Connection, drive_id: int) -> dict[int, tuple[StreamInfo, str]]:
    """stream id -> (definition, storage) for one drive."""
    columns: dict[int, list[Column]] = {}
    for sid, name, units, label, kind, dtype, decimals, lo, hi in conn.execute(
            "SELECT stream_id, name, units, label, kind, dtype, decimals, lo, hi FROM channels "
            "WHERE drive_id=? ORDER BY stream_id, position", (drive_id,)):
        columns.setdefault(int(sid), []).append(Column(name, dtype, units, label, kind, int(decimals), lo, hi))
    out: dict[int, tuple[StreamInfo, str]] = {}
    for sid, name, source, storage, raw_len, attrs in conn.execute(
            "SELECT id, name, source, storage, raw_len, attrs FROM streams WHERE drive_id=? ORDER BY id",
            (drive_id,)):
        info = StreamInfo(name, tuple(columns.get(int(sid), ())), source, int(raw_len), json.loads(attrs or "{}"))
        out[int(sid)] = (info, storage)
    return out


# ---- chunked streams --------------------------------------------------------

def read_chunks(conn: sqlite3.Connection, drive_id: int, stream_id: int, dtype: np.dtype,
                t0: float | None = None, t1: float | None = None) -> np.ndarray:
    """Every row of a chunked stream (or those with t0 <= t <= t1), in time order."""
    sql = "SELECT codec, data, n_rows FROM chunks WHERE drive_id=? AND stream_id=?"
    args: list[Any] = [drive_id, stream_id]
    if t0 is not None:
        sql += " AND t_end >= ?"
        args.append(t0)
    if t1 is not None:
        sql += " AND t_start <= ?"
        args.append(t1)
    parts = [codec.decode(name, data, dtype, int(n)) for name, data, n in
             conn.execute(sql + " ORDER BY t_start, id", args)]
    if not parts:
        return np.zeros(0, dtype)
    rows = parts[0] if len(parts) == 1 else np.concatenate(parts)
    if len(rows) > 1 and np.any(np.diff(rows["t"]) < 0):
        rows = rows[np.argsort(rows["t"], kind="stable")]
    if t0 is not None or t1 is not None:
        lo = 0 if t0 is None else int(np.searchsorted(rows["t"], t0, side="left"))
        hi = len(rows) if t1 is None else int(np.searchsorted(rows["t"], t1, side="right"))
        rows = rows[lo:hi]
    return rows


def second_stats(rows: np.ndarray, info: StreamInfo) -> list[tuple[int, float, int, float, float, float]]:
    """(position, second, n, min, mean, max) for every channel and every
    whole second that has a finite value. ``rows`` must be sorted by time."""
    out: list[tuple[int, float, int, float, float, float]] = []
    if len(rows) == 0:
        return out
    second = np.floor(rows["t"])
    starts = np.flatnonzero(np.diff(second, prepend=second[0] - 1))
    seconds = second[starts].tolist()
    for position, col in enumerate(info.columns):
        values = rows[col.name].astype(np.float64)
        ok = np.isfinite(values)
        count = np.add.reduceat(ok.astype(np.int64), starts)
        total = np.add.reduceat(np.where(ok, values, 0.0), starts)
        lo = np.minimum.reduceat(np.where(ok, values, np.inf), starts)
        hi = np.maximum.reduceat(np.where(ok, values, -np.inf), starts)
        for i in np.flatnonzero(count).tolist():
            n = int(count[i])
            out.append((position, seconds[i], n, float(lo[i]), float(total[i] / n), float(hi[i])))
    return out


STATS_UPSERT = ("INSERT INTO chunk_stats(drive_id, stream_id, position, t, n, lo, mean, hi) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(drive_id, stream_id, position, t) DO UPDATE SET "
                "lo = min(lo, excluded.lo), hi = max(hi, excluded.hi), "
                "mean = (mean * n + excluded.mean * excluded.n) / (n + excluded.n), n = n + excluded.n")


def write_block(conn: sqlite3.Connection, drive_id: int, stream_id: int, info: StreamInfo,
                rows: np.ndarray) -> int:
    """Store sorted rows as one packed chunk plus their one-second statistics.
    Returns the size of the BLOB. Call inside a transaction."""
    if len(rows) == 0:
        return 0
    blob = codec.encode(rows, codec.PACKED)
    conn.execute("INSERT INTO chunks(drive_id, stream_id, t_start, t_end, n_rows, codec, data) "
                 "VALUES(?,?,?,?,?,?,?)", (drive_id, stream_id, float(rows["t"][0]), float(rows["t"][-1]),
                                           len(rows), codec.PACKED, blob))
    conn.executemany(STATS_UPSERT, [(drive_id, stream_id, *row) for row in second_stats(rows, info)])
    return len(blob)


def compact_stream(conn: sqlite3.Connection, drive_id: int, stream_id: int, info: StreamInfo) -> int:
    """Turn the stream's ``raw`` chunks into one packed block. Returns the
    rows that were packed. Call inside a transaction."""
    found = conn.execute("SELECT id, data, n_rows FROM chunks WHERE drive_id=? AND stream_id=? AND codec=? "
                         "ORDER BY t_start, id", (drive_id, stream_id, codec.RAW)).fetchall()
    if not found:
        return 0
    dtype = info.dtype
    parts = [codec.decode(codec.RAW, data, dtype, int(n)) for _id, data, n in found]
    rows = parts[0] if len(parts) == 1 else np.concatenate(parts)
    if len(rows) > 1 and np.any(np.diff(rows["t"]) < 0):
        rows = rows[np.argsort(rows["t"], kind="stable")]
    conn.executemany("DELETE FROM chunks WHERE id=?", [(row[0],) for row in found])
    write_block(conn, drive_id, stream_id, info, rows)
    return len(rows)


# ---- finishing a drive ------------------------------------------------------

def _thumb(conn: sqlite3.Connection, drive_id: int, channel: str = "rpm") -> str | None:
    """The RPM envelope for a sparkline, as JSON."""
    row = conn.execute(
        "SELECT c.stream_id, c.part, c.slot FROM channels c JOIN streams s ON s.drive_id = c.drive_id "
        "AND s.id = c.stream_id WHERE c.drive_id=? AND c.name=? AND s.storage='rows' "
        "ORDER BY s.rows DESC LIMIT 1", (drive_id, channel)).fetchone()
    if row is None:
        return None
    data = conn.execute(f"SELECT t, v{int(row[2])} FROM samples WHERE drive_id=? AND stream_id=? AND part=?",
                        (drive_id, row[0], row[1])).fetchall()
    if not data:
        return None
    from ..analysis.lod import envelope            # late: analysis imports this package
    arr = np.array(data, dtype=np.float64)
    env = envelope(arr[:, 0], arr[:, 1], columns=THUMB_POINTS)
    lo, hi = np.nan_to_num(env.lo), np.nan_to_num(env.hi)
    if not env.reduced and len(lo) > THUMB_POINTS:
        pick = np.linspace(0, len(lo) - 1, THUMB_POINTS).astype(int)
        lo, hi = lo[pick], hi[pick]
    return json.dumps({"lo": [round(float(x), 1) for x in lo], "hi": [round(float(x), 1) for x in hi]})


def finish_drive(conn: sqlite3.Connection, drive_id: int, status: str, reason: str = "") -> None:
    """Everything that is done once per drive, at its end (or when a crashed
    drive is found): pack the black box tail, count rows per stream, set the
    end time, make the thumbnail and the view. One transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        infos = stream_infos(conn, drive_id)
        total, size = 0, 0
        first: float | None = None
        last: float | None = None
        for sid, (info, storage) in infos.items():
            if storage == "chunks":
                compact_stream(conn, drive_id, sid, info)
                rows, lo, hi, blob = conn.execute(
                    "SELECT coalesce(sum(n_rows), 0), min(t_start), max(t_end), coalesce(sum(length(data)), 0) "
                    "FROM chunks WHERE drive_id=? AND stream_id=?", (drive_id, sid)).fetchone()
                size += int(blob) + ROW_BYTES * conn.execute(
                    "SELECT count(*) FROM chunk_stats WHERE drive_id=? AND stream_id=?", (drive_id, sid)).fetchone()[0]
            else:
                rows, lo, hi = conn.execute(
                    "SELECT count(*), min(t), max(t) FROM samples WHERE drive_id=? AND stream_id=? AND part=0",
                    (drive_id, sid)).fetchone()
                parts = (len(info.columns) + SLOTS - 1) // SLOTS or 1
                size += int(rows) * parts * (ROW_BYTES + info.raw_len)
            conn.execute("UPDATE streams SET rows=?, t_first=?, t_last=? WHERE drive_id=? AND id=?",
                         (rows, lo, hi, drive_id, sid))
            total += int(rows)
            if rows:
                first = lo if first is None else min(first, lo)
                last = hi if last is None else max(last, hi)
        lo, hi = conn.execute("SELECT min(t), max(t) FROM events WHERE drive_id=?", (drive_id,)).fetchone()
        if first is None and lo is not None:
            first, last = lo, hi
        epoch, started = conn.execute("SELECT epoch_unix, started_at FROM drives WHERE id=?", (drive_id,)).fetchone()
        ended = None
        if last is not None and epoch is not None:
            ended = float(epoch) + float(last)
        elif started is not None:
            ended = float(started) + (float(last) - float(first) if last is not None and first is not None else 0.0)
        duration = float(last) - float(first) if last is not None and first is not None else 0.0
        conn.execute(
            "UPDATE drives SET status=?, end_reason=?, t_first=?, t_last=?, rows=?, size_bytes=?, duration_s=?, "
            "ended_at=?, ended_iso=?, thumb=?, writer_pid=NULL WHERE id=?",
            (status, reason, first, last, total, size, duration, ended,
             datetime.fromtimestamp(ended).isoformat(timespec="seconds") if ended is not None else None,
             _thumb(conn, drive_id), drive_id))
        schema.make_drive_view(conn, drive_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# ---- names ------------------------------------------------------------------

def safe_name(text: str, fallback: str = "drive") -> str:
    """Letters, digits and underscores only, starting with a letter: usable
    as an SQL identifier without quoting, and in a ``database#name`` reference."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not name or not name[0].isalpha():
        name = f"{fallback}_{name}" if name else fallback
    return name


def unique_name(conn: sqlite3.Connection, table: str, stem: str) -> str:
    """``stem``, or ``stem_2``, ``stem_3`` ... : a name not yet in ``table``
    (and, for a drive, not the name of a table or shared view either: the
    drive's view is called the same)."""
    stem = safe_name(stem, "drive" if table == "drives" else "capture")
    taken: set[str] = set()
    if table == "drives":
        taken = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type != 'view'")}
        taken |= set(schema.SHARED_VIEWS)
    name, k = stem, 2
    while name in taken or conn.execute(f"SELECT 1 FROM {table} WHERE name=?", (name,)).fetchone():
        name = f"{stem}_{k}"
        k += 1
    return name


def drive_stem(started: datetime) -> str:
    return f"drive_{started:%Y_%m_%d_%H%M}"


# ---- deleting and vacuum ----------------------------------------------------

def delete_drives(conn: sqlite3.Connection, names: Iterable[str]) -> list[str]:
    """Remove drives and everything recorded in them. Frees the pages inside
    the file; ``vacuum`` hands them back to the disk."""
    removed = []
    for name in names:
        row = conn.execute("SELECT id FROM drives WHERE name=?", (name,)).fetchone()
        if row is None:
            continue
        drive_id = int(row[0])
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table in ("samples", "chunks", "chunk_stats", "events", "alarms", "captures",
                          "channels", "streams"):
                conn.execute(f"DELETE FROM {table} WHERE drive_id=?", (drive_id,))
            conn.execute("DELETE FROM drives WHERE id=?", (drive_id,))
            conn.execute("DELETE FROM address_maps WHERE id NOT IN "
                         "(SELECT map_id FROM drives WHERE map_id IS NOT NULL)")
            schema.drop_drive_view(conn, name)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        removed.append(name)
    return removed


def file_size(path: str | os.PathLike) -> int:
    """Bytes on disk: the database plus its write-ahead log."""
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += os.stat(str(path) + suffix).st_size
        except OSError:
            pass
    return total


def vacuum(path: str | os.PathLike, full: bool = False) -> dict[str, Any]:
    """Give free pages back to the disk. The default is the incremental
    vacuum (quick, safe while a recording is running); ``full`` rewrites the
    whole file compactly and needs the database to itself."""
    before = file_size(path)
    conn = connect(path, write=True)
    try:
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        ok = conn.execute("PRAGMA quick_check").fetchone()[0]
        if full:
            conn.execute("VACUUM")
        else:
            conn.execute("PRAGMA incremental_vacuum").fetchall()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    except sqlite3.OperationalError as exc:
        raise OSError(f"{path}: {exc}") from None
    finally:
        conn.close()
    return {"before": before, "after": file_size(path), "free_pages": free, "check": ok, "full": full}


def query(path: str | os.PathLike, sql: str, args: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
    """Run one read-only statement. Returns (column names, rows)."""
    conn = connect(path)
    try:
        cur = conn.execute(sql, tuple(args))
        names = [d[0] for d in cur.description or ()]
        return names, cur.fetchall()
    finally:
        conn.close()
