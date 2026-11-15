"""Keeping the log folder from filling the disk.

``RetentionPolicy.plan`` lists what it would delete; ``apply`` deletes it.
Nothing is deleted unless a limit is set, captures (stall files and the
like) are kept by default, and so is any log whose metadata has ``keep``
set or carries notes or tags: somebody cared about those.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .container import EXTENSION, LogFormatError
from .reader import read_hlog

LOG_PREFIX = "log_"
LOG_SUFFIXES = (EXTENSION, ".csv")


def is_session_log(path: Path) -> bool:
    """A continuous recording (as opposed to a capture cut out of one)."""
    return path.name.startswith(LOG_PREFIX) and path.suffix in LOG_SUFFIXES


def is_protected(path: Path) -> bool:
    """True if the log's own metadata says to keep it."""
    if path.suffix != EXTENSION:
        return False
    try:
        with read_hlog(path) as log:
            meta = log.meta
    except (OSError, LogFormatError, ValueError):
        return True                     # unreadable: do not make it worse
    return bool(meta.get("keep") or meta.get("notes") or meta.get("tags"))


@dataclass
class RetentionPolicy:
    max_age_days: float | None = None       # delete session logs older than this
    max_total_mb: float | None = None       # then delete oldest until the folder fits
    max_files: int | None = None            # and until at most this many session logs remain
    keep_captures: bool = True              # never delete stall_*/capture files
    keep_newest: int = 1                    # always keep this many of the newest session logs

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.max_age_days, self.max_total_mb, self.max_files))

    def plan(self, folder: str | os.PathLike, now: float | None = None) -> list[Path]:
        """Files this policy would delete, oldest first."""
        folder = Path(folder)
        if not self.active or not folder.is_dir():
            return []
        now = time.time() if now is None else now
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix in LOG_SUFFIXES]
        stats = {p: p.stat() for p in files}
        candidates = sorted((p for p in files if is_session_log(p) or not self.keep_captures),
                            key=lambda p: stats[p].st_mtime)
        if self.keep_newest > 0:
            sessions = [p for p in candidates if is_session_log(p)]
            spared = set(sessions[-self.keep_newest:])
            candidates = [p for p in candidates if p not in spared]
        candidates = [p for p in candidates if not is_protected(p)]

        doomed: list[Path] = []
        if self.max_age_days is not None:
            limit = now - self.max_age_days * 86400.0
            doomed += [p for p in candidates if stats[p].st_mtime < limit]
        remaining = [p for p in candidates if p not in doomed]
        if self.max_files is not None:
            sessions_left = sum(1 for p in files if is_session_log(p) and p not in doomed)
            while remaining and sessions_left > self.max_files:
                victim = remaining.pop(0)
                doomed.append(victim)
                sessions_left -= is_session_log(victim)
        if self.max_total_mb is not None:
            total = sum(stats[p].st_size for p in files if p not in doomed)
            while remaining and total > self.max_total_mb * 1e6:
                victim = remaining.pop(0)
                doomed.append(victim)
                total -= stats[victim].st_size
        return sorted(doomed, key=lambda p: stats[p].st_mtime)

    def apply(self, folder: str | os.PathLike, now: float | None = None) -> list[Path]:
        """Delete what ``plan`` lists. Returns the files that were removed."""
        removed = []
        for path in self.plan(folder, now):
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
        return removed
