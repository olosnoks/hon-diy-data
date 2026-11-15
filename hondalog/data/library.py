"""The session library: a searchable list of every log and capture in a folder.

Opening each file for the list would be slow with hundreds of logs, so the
summaries are cached in ``.library.json`` in the folder, keyed by file size
and modification time. The cache is only a cache: delete it at will.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .container import EXTENSION
from .reader import Log, open_log
from .store import annotate

CACHE_NAME = ".library.json"
CACHE_VERSION = 2
SUFFIXES = (EXTENSION, ".csv", ".msl", ".mlg")
THUMB_POINTS = 64


@dataclass
class LogEntry:
    path: str
    name: str
    kind: str                           # "log", or the capture prefix: "stall", ...
    size: int
    mtime: float
    started: str = ""
    duration: float = 0.0
    rows: int = 0
    ecu_id: str = ""
    map_version: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    trigger: str = ""
    thumb_lo: list[float] = field(default_factory=list)     # RPM envelope, for a sparkline
    thumb_hi: list[float] = field(default_factory=list)
    error: str = ""

    def matches(self, query: str) -> bool:
        """Every word of ``query`` appears somewhere in the name, notes, tags or markers."""
        hay = " ".join([self.name, self.notes, self.trigger, self.ecu_id, self.map_version,
                        *self.tags, *self.markers]).lower()
        return all(word in hay for word in query.lower().split())


def summarise(path: Path, thumb_channel: str = "rpm") -> LogEntry:
    stat = path.stat()
    kind = path.name.split("_", 1)[0] if "_" in path.name else "log"
    entry = LogEntry(str(path), path.name, kind, stat.st_size, stat.st_mtime)
    try:
        log = open_log(path)
    except Exception as exc:            # a broken file must not break the list
        entry.error = str(exc)
        return entry
    with log:
        _fill(entry, log, thumb_channel)
    return entry


def _fill(entry: LogEntry, log: Log, thumb_channel: str) -> None:
    from ..analysis.lod import envelope
    meta = log.meta
    entry.started = str(meta.get("started", ""))
    entry.duration = round(log.duration, 3)
    entry.rows = log.rows
    entry.ecu_id = str(meta.get("ecu_id", ""))
    entry.map_version = str(meta.get("address_map_version", ""))
    entry.notes = str(meta.get("notes", ""))
    tags = meta.get("tags", [])
    entry.tags = [str(t) for t in tags] if isinstance(tags, list) else str(tags).split()
    entry.markers = [text for _, text in log.markers]
    entry.trigger = str(meta.get("trigger_message", meta.get("stall_trigger", "")))
    if thumb_channel in log.channels:
        series = log.series(thumb_channel)
        env = envelope(series.t, series.v, columns=THUMB_POINTS)
        lo, hi = np.nan_to_num(env.lo), np.nan_to_num(env.hi)
        if not env.reduced and len(lo) > THUMB_POINTS:
            pick = np.linspace(0, len(lo) - 1, THUMB_POINTS).astype(int)
            lo, hi = lo[pick], hi[pick]
        entry.thumb_lo = [round(float(x), 1) for x in lo]
        entry.thumb_hi = [round(float(x), 1) for x in hi]


class Library:
    """All logs in one folder."""

    def __init__(self, folder: str | os.PathLike, thumb_channel: str = "rpm"):
        self.folder = Path(folder)
        self.thumb_channel = thumb_channel
        self.entries: list[LogEntry] = []

    def refresh(self) -> list[LogEntry]:
        """Rescan the folder. Only new and changed files are opened."""
        cache = self._load_cache()
        entries = []
        if self.folder.is_dir():
            for path in sorted(self.folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                    continue
                stat = path.stat()
                old = cache.get(path.name)
                if old is not None and old.size == stat.st_size and old.mtime == stat.st_mtime:
                    old.path = str(path)
                    entries.append(old)
                else:
                    entries.append(summarise(path, self.thumb_channel))
        entries.sort(key=lambda e: e.mtime, reverse=True)
        self.entries = entries
        self._save_cache()
        return entries

    def search(self, query: str = "", kinds: Sequence[str] = (), tag: str = "") -> list[LogEntry]:
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

    def set_notes(self, entry: LogEntry | str | os.PathLike, notes: str | None = None,
                  tags: Sequence[str] | None = None, keep: bool | None = None) -> None:
        """Write notes, tags or the keep flag into a native log and refresh its entry."""
        path = Path(entry.path if isinstance(entry, LogEntry) else entry)
        if path.suffix != EXTENSION:
            raise ValueError("notes can only be stored in native .hlog files; import the log first")
        changes: dict[str, Any] = {}
        if notes is not None:
            changes["notes"] = notes
        if tags is not None:
            changes["tags"] = list(tags)
        if keep is not None:
            changes["keep"] = keep
        if changes:
            annotate(path, **changes)
        self.refresh()

    def _load_cache(self) -> dict[str, LogEntry]:
        try:
            with open(self.folder / CACHE_NAME, encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("version") != CACHE_VERSION:
                return {}
            return {e["name"]: LogEntry(**e) for e in doc["entries"]}
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def _save_cache(self) -> None:
        try:
            tmp = self.folder / (CACHE_NAME + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": CACHE_VERSION, "entries": [asdict(e) for e in self.entries]}, fh)
            os.replace(tmp, self.folder / CACHE_NAME)
        except OSError:
            pass                        # read-only folder: the list still works, just slower
