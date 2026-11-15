"""Keeping the database from filling the disk.

``RetentionPolicy.plan`` lists the drives it would delete; ``apply`` deletes
them and gives the freed pages back to the disk (incremental vacuum; pass
``full_vacuum=True`` to rewrite the file compactly as well). Nothing is
deleted unless a limit is set. A drive with a capture in it (a stall) is
kept by default, and so is any drive with ``keep`` set or with notes or
tags: somebody cared about those. A drive that is being recorded is never
touched.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import db
from .db import DriveRef
from .library import DriveEntry, list_entries


def is_protected(entry: DriveEntry) -> bool:
    """True if the drive's own row says to keep it."""
    return bool(entry.keep or entry.notes or entry.tags)


@dataclass
class RetentionPolicy:
    max_age_days: float | None = None       # delete drives older than this
    max_total_mb: float | None = None       # then delete oldest until the database fits
    max_drives: int | None = None           # and until at most this many drives remain
    keep_captures: bool = True              # never delete a drive that has a capture (a stall) in it
    keep_newest: int = 1                    # always keep this many of the newest drives
    full_vacuum: bool = False               # rewrite the whole file after deleting (slow on a big one)

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.max_age_days, self.max_total_mb, self.max_drives))

    def plan(self, path: str | os.PathLike | None = None, now: float | None = None) -> list[DriveRef]:
        """Drives this policy would delete, oldest first."""
        target = db.db_path(path)
        if not self.active or not target.is_file():
            return []
        now = time.time() if now is None else now
        conn = db.connect(target)
        try:
            drives = [e for e in list_entries(conn, target) if e.kind == "drive"]
            pages, free, page_size = (int(conn.execute(f"PRAGMA {name}").fetchone()[0])
                                      for name in ("page_count", "freelist_count", "page_size"))
        finally:
            conn.close()
        drives.sort(key=lambda e: (e.started_unix, e.drive_id))
        candidates = [e for e in drives if e.status != "recording"]
        if self.keep_newest > 0:
            spared = {e.name for e in drives[-self.keep_newest:]}
            candidates = [e for e in candidates if e.name not in spared]
        candidates = [e for e in candidates if not is_protected(e)
                      and not (self.keep_captures and e.captures)]

        doomed: list[DriveEntry] = []
        if self.max_age_days is not None:
            limit = now - self.max_age_days * 86400.0
            doomed += [e for e in candidates if e.started_unix < limit]
        remaining = [e for e in candidates if e not in doomed]
        if self.max_drives is not None:
            left = len(drives) - len(doomed)
            while remaining and left > self.max_drives:
                doomed.append(remaining.pop(0))
                left -= 1
        if self.max_total_mb is not None:
            # a drive's size in its row is an estimate: scale them so they add up to the file
            used = float((pages - free) * page_size)
            scale = used / (sum(e.size for e in drives) or 1.0)
            total = used - scale * sum(e.size for e in doomed)
            while remaining and total > self.max_total_mb * 1e6:
                victim = remaining.pop(0)
                doomed.append(victim)
                total -= scale * victim.size
        doomed.sort(key=lambda e: (e.started_unix, e.drive_id))
        return [e.ref for e in doomed]

    def apply(self, path: str | os.PathLike | None = None, now: float | None = None) -> list[DriveRef]:
        """Delete what ``plan`` lists. Returns the drives that were removed."""
        target = db.db_path(path)
        doomed = self.plan(target, now)
        if not doomed:
            return []
        conn = db.connect(target, write=True)
        try:
            gone = set(db.delete_drives(conn, [ref.drive for ref in doomed]))
        finally:
            conn.close()
        try:
            db.vacuum(target, full=self.full_vacuum)
        except OSError:
            pass                            # busy: the pages are free and get reused anyway
        return [ref for ref in doomed if ref.drive in gone]
