"""``hondalog-handheld sync``: the handheld's SD card over its USB-C serial
link, without taking the card out (two-way firmware only).

The files are copied into a mirror of the card on the laptop
(``~/hondalog/handheld/<device>/DRIVES``, ``CAPTURES``, ``HANDHELD.MAP``) and
the mirror goes through the same importer as a card in a reader
(``importer.import_card``), so what is already in the database is skipped.

Resumable at every level: a file that is already in the mirror whole is not
asked for again, a file that was cut off half-way (``.part``) carries on
from where it stopped, and every chunk carries its own CRC32.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..data import db
from . import serial_proto as P
from .importer import ImportResult, import_card
from .serial_link import CydBusy, CydClient, CydError, FileEntry, UsedLength


@dataclass
class SyncReport:
    mirror: Path
    device: str = ""
    fetched: list[str] = field(default_factory=list)        # copied now (whole, or the rest of a .part)
    present: list[str] = field(default_factory=list)        # already in the mirror
    open: list[str] = field(default_factory=list)           # still being written: not copied
    imported: list[ImportResult] = field(default_factory=list)
    bytes: int = 0


def mirror_for(database: str | Path | None, device: str) -> Path:
    """Where a handheld's files are kept: next to the database."""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in device) or "handheld"
    return db.db_path(database).parent / "handheld" / safe


def sync(port: Any = "cyd:", database: str | Path | None = None, mirror: str | Path | None = None, *,
         captures: bool = True, end_drive: bool = False, do_import: bool = True,
         progress: Callable[[str, int, int], None] | None = None, client: CydClient | None = None) -> SyncReport:
    """Copy the handheld's drive (and capture) files, then import them.

    ``end_drive``: end the drive being recorded first (a new one starts at
    once while the ECU answers), so it can be copied too.
    ``progress(name, done, total)`` is called as bytes arrive.
    """
    own = client is None
    cyd = client or CydClient(port)
    try:
        hello = cyd.hello or cyd.open()
        cyd.need_two_way()
        if not hello.caps & P.CAP_FILES:
            raise CydError("sync: " + ("no SD card in the handheld" if not hello.flags & 8
                                       else "this firmware cannot send files"))
        if end_drive:
            cyd.drive(False)
            cyd.drive(True)
        folder = Path(mirror) if mirror else mirror_for(database, hello.device)
        report = SyncReport(folder, hello.device)
        kinds = [P.F_DRIVE] + ([P.F_CAPTURE] if captures else [])
        manifest = _Manifest(folder)
        # the map the drives were recorded with, so the importer finds it by its hash
        for entry in cyd.list_files(P.F_MAP):
            _fetch(cyd, entry, folder / entry.name, report, progress)
        for kind in kinds:
            for entry in cyd.list_files(kind):
                if entry.open:
                    report.open.append(entry.name)
                    continue
                _fetch(cyd, entry, folder / entry.name, report, progress, manifest=manifest)
    finally:
        if own:
            cyd.close()
    if do_import:
        report.imported = import_card(folder, database)
    return report


class _Manifest:
    """SYNC.json: name -> the file's length on the card when it was copied."""

    def __init__(self, folder: Path):
        self.path = folder / "SYNC.json"
        try:
            self.sizes: dict[str, int] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.sizes = {}

    def have(self, entry: FileEntry, dest: Path) -> bool:
        if not dest.is_file():
            return False
        return dest.stat().st_size == entry.size or self.sizes.get(entry.name) == entry.size

    def done(self, entry: FileEntry) -> None:
        self.sizes[entry.name] = entry.size
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sizes, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _fetch(cyd: CydClient, entry: FileEntry, dest: Path, report: SyncReport,
           progress: Callable[[str, int, int], None] | None, manifest: _Manifest | None = None) -> None:
    if entry.kind == P.F_MAP and dest.is_file() and dest.stat().st_size == entry.size:
        # the same map? Its header holds the hash, the text's sha256 and the CRC of the rest
        head, _ = cyd.read_chunk(entry.kind, entry.file_no, 0, 64)
        if dest.read_bytes()[:64] == head:
            report.present.append(entry.name)
            return
    if manifest is not None and manifest.have(entry, dest):
        report.present.append(entry.name)
        return
    part = dest.with_name(dest.name + ".part")
    had = part.stat().st_size if part.is_file() else 0
    cut = UsedLength() if entry.kind in (P.F_DRIVE, P.F_CAPTURE) else None
    try:
        cyd.fetch(entry, dest, (lambda done, total: progress(entry.name, done, total)) if progress else None,
                  cut=cut)
    except CydBusy:
        report.open.append(entry.name)
        return
    report.fetched.append(entry.name)
    report.bytes += max(0, dest.stat().st_size - had)
    if manifest is not None:
        manifest.done(entry)


__all__ = ["SyncReport", "mirror_for", "sync"]
