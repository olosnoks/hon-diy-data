"""The drive library: a searchable list of every drive and capture in the database.

The list comes straight from SQL (``drives``, ``captures``, ``events``); the
RPM thumbnail of a drive is made once, when the drive ends, and kept in its
row. Nothing has to be opened to show the list.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import db
from .db import DriveRef
from .store import annotate


@dataclass
class DriveEntry:
    ref: DriveRef
    name: str
    kind: str                           # "drive", or the capture's kind: "stall", "capture", ...
    drive_id: int
    status: str = ""                    # recording, complete, crashed, imported
    started: str = ""
    started_unix: float = 0.0
    duration: float = 0.0
    rows: int = 0
    size: int = 0                       # estimate in bytes (0 for a capture: it holds no data of its own)
    ecu_id: str = ""
    map_version: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    keep: bool = False
    markers: list[str] = field(default_factory=list)
    trigger: str = ""
    stalls: int = 0
    captures: int = 0
    thumb_lo: list[float] = field(default_factory=list)     # RPM envelope, for a sparkline
    thumb_hi: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def path(self) -> DriveRef:
        return self.ref

    def matches(self, query: str) -> bool:
        """Every word of ``query`` appears somewhere in the name, notes, tags or markers."""
        hay = " ".join([self.name, self.kind, self.status, self.notes, self.trigger, self.ecu_id,
                        self.map_version, *self.tags, *self.markers]).lower()
        return all(word in hay for word in query.lower().split())


LogEntry = DriveEntry       # the name it had when logs were files

_MARKS = ("SELECT drive_id, t, text, kind FROM events WHERE kind IN ('marker', 'trigger') "
          "UNION ALL SELECT drive_id, t, text, 'alarm' FROM alarms ORDER BY 1, 2")


def list_entries(conn: sqlite3.Connection, path: Path) -> list[DriveEntry]:
    """Every drive and capture, newest first."""
    marks: dict[int, list[tuple[float, str]]] = {}
    for drive_id, t, text, kind in conn.execute(_MARKS):
        marks.setdefault(int(drive_id), []).append((float(t), text or kind))
    entries: list[DriveEntry] = []
    names: dict[int, str] = {}
    for (drive_id, name, status, started, started_at, duration, rows, size, ecu_id, map_version, notes,
         tags, keep, stalls, captures, thumb) in conn.execute(
            "SELECT id, name, status, started_iso, started_at, duration_s, rows, size_bytes, ecu_id, "
            "map_version, notes, tags, keep, stall_count, capture_count, thumb FROM drives"):
        names[int(drive_id)] = name
        entry = DriveEntry(DriveRef(path, name), name, "drive", int(drive_id), status, started or "",
                           float(started_at or 0.0), round(float(duration or 0.0), 3), int(rows), int(size),
                           ecu_id, map_version, notes, _tags(tags), bool(keep),
                           [text for _, text in marks.get(int(drive_id), [])], stalls=int(stalls),
                           captures=int(captures))
        if thumb:
            doc = json.loads(thumb)
            entry.thumb_lo, entry.thumb_hi = doc.get("lo", []), doc.get("hi", [])
        entries.append(entry)
    by_id = {e.drive_id: e for e in entries}
    for drive_id, name, kind, message, t0, t1, wall, wall_unix, rows in conn.execute(
            "SELECT drive_id, name, kind, message, t_start, t_end, wall_iso, wall_unix, rows FROM captures"):
        owner = by_id.get(int(drive_id))
        if owner is None:
            continue
        entries.append(DriveEntry(
            DriveRef(path, owner.name, name), name, kind, int(drive_id), owner.status, wall or "",
            float(wall_unix or 0.0), round(float(t1) - float(t0), 3), int(rows), 0, owner.ecu_id,
            owner.map_version, owner.notes, list(owner.tags), owner.keep,
            [text for t, text in marks.get(int(drive_id), []) if t0 <= t <= t1], message))
    entries.sort(key=lambda e: (e.started_unix, e.kind != "drive", e.name), reverse=True)
    return entries


def _tags(text: Any) -> list[str]:
    try:
        tags = json.loads(text or "[]")
    except ValueError:
        return str(text).split()
    return [str(t) for t in tags] if isinstance(tags, list) else str(tags).split()


class Library:
    """All drives and captures in one database (a file, or the folder it is in)."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = db.db_path(path)
        self.entries: list[DriveEntry] = []

    @property
    def folder(self) -> Path:
        return self.path.parent

    def refresh(self) -> list[DriveEntry]:
        """Read the list again. A missing database is an empty library."""
        try:
            conn = db.connect(self.path)
        except FileNotFoundError:
            self.entries = []
            return self.entries
        try:
            self.entries = list_entries(conn, self.path)
        finally:
            conn.close()
        return self.entries

    def search(self, query: str = "", kinds: Sequence[str] = (), tag: str = "") -> list[DriveEntry]:
        """Entries whose text holds every word of ``query``, newest first."""
        out = []
        for entry in self.entries:
            if kinds and entry.kind not in kinds:
                continue
            if tag and tag not in entry.tags:
                continue
            if query and not entry.matches(query):
                continue
            out.append(entry)
        return out

    def drives(self) -> list[DriveEntry]:
        return [e for e in self.entries if e.kind == "drive"]

    def get(self, name: str) -> DriveEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def set_notes(self, entry: DriveEntry | DriveRef | str, notes: str | None = None,
                  tags: Sequence[str] | None = None, keep: bool | None = None) -> None:
        """Write notes, tags or the keep flag into a drive's row and refresh the list.
        Notes on a capture are notes on its drive."""
        ref = entry.ref if isinstance(entry, DriveEntry) else db.parse_ref(entry, self.path)
        if ref is None:
            raise ValueError(f"{entry}: not a drive in {self.path}; import the file first")
        changes: dict[str, Any] = {}
        if notes is not None:
            changes["notes"] = notes
        if tags is not None:
            changes["tags"] = list(tags)
        if keep is not None:
            changes["keep"] = keep
        if changes:
            annotate(ref, **changes)
        self.refresh()
