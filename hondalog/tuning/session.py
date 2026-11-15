"""An editing session on one ROM: working copy, undo history, snapshots.

Safety rules built in here:

* The file that was opened is never written. ``save`` refuses its path.
* The first time a file is opened, an exact copy goes to
  ``<store>/backups/`` and is made read-only.
* Every ``save`` also drops a timestamped snapshot in ``<store>/snapshots/``.
* Every change, from any editor, goes through :meth:`RomSession.apply`, so
  there is one unlimited undo history for the whole ROM.

``<store>`` is ``~/hondalog/roms`` (or ``$HONDALOG_ROM_DIR``).
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

import numpy as np

from .definition import Definition, Flag, MathVar, Patch, Scalar
from .mathexpr import MathError
from .rom import (BurnImage, ChecksumReport, RomError, RomImage, build_burn_image,
                  checksum_fix, checksum_report, sha256_hex, write_atomic)

if TYPE_CHECKING:
    from .diff import RomDiff
    from .table import TableEditor

ChangeListener = Callable[[list[tuple[int, int]], str], None]


def rom_dir() -> Path:
    return Path(os.environ.get("HONDALOG_ROM_DIR") or os.path.expanduser("~/hondalog/roms"))


@dataclass
class Change:
    offset: int
    old: bytes
    new: bytes


@dataclass
class Edit:
    label: str
    changes: list[Change] = field(default_factory=list)

    @property
    def byte_count(self) -> int:
        return sum(len(c.new) for c in self.changes)

    def ranges(self) -> list[tuple[int, int]]:
        return [(c.offset, c.offset + len(c.new)) for c in self.changes]


@dataclass
class SaveResult:
    path: Path
    snapshot: Path | None
    sha256: str
    checksum: ChecksumReport
    checksum_fixed: bool


class RomSession:
    def __init__(self, rom: RomImage, definition: Definition, *,
                 store_dir: str | Path | None = None, name: str | None = None):
        if rom.size != definition.rom_size:
            raise RomError(
                f"the ROM is {rom.size} bytes but the definition "
                f"{definition.title!r} is for {definition.rom_size} bytes")
        self.rom = rom
        self.definition = definition
        self.store_dir = Path(store_dir) if store_dir else rom_dir()
        self.name = name or (rom.path.stem if rom.path else "rom")
        self.source_path = rom.path
        self.original = bytes(rom.data)          # as loaded; never changes
        self.data = bytearray(rom.data)          # the working copy
        self._saved = bytes(rom.data)
        self._save_path: Path | None = None
        self._undo: list[Edit] = []
        self._redo: list[Edit] = []
        self._group: Edit | None = None
        self._group_depth = 0
        self._listeners: list[ChangeListener] = []
        self._editors: dict[str, "TableEditor"] = {}
        self.locks: dict[str, np.ndarray] = {}   # table id -> bool mask of locked cells
        self.backup_path: Path | None = None
        self.warnings: list[str] = list(rom.warnings) + rom.check_signatures(definition)

    # -- opening --

    @classmethod
    def open(cls, path: str | Path, definition: Definition, *,
             half: str | int | None = None, store_dir: str | Path | None = None,
             backup: bool = True) -> "RomSession":
        rom = RomImage.load(path, definition.rom_size, half)
        session = cls(rom, definition, store_dir=store_dir)
        if backup:
            session.backup_path = session._backup_original()
        return session

    def _backup_original(self) -> Path | None:
        if self.source_path is None:
            return None
        blob = self.source_path.read_bytes()
        target = (self.store_dir / "backups"
                  / f"{sha256_hex(blob)[:12]}_{self.source_path.name}")
        if not target.exists():
            write_atomic(target, blob)
            target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return target

    # -- the one way bytes change --

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        """Call ``listener(ranges, reason)`` after every change. Returns an
        unsubscribe function. ``reason`` is "edit", "undo" or "redo"."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self, ranges: list[tuple[int, int]], reason: str) -> None:
        for listener in list(self._listeners):
            listener(ranges, reason)

    def apply(self, label: str, changes: list[tuple[int, bytes]]) -> bool:
        """Write bytes with undo. Returns False when nothing actually changed."""
        real: list[Change] = []
        for offset, blob in changes:
            if offset < 0 or offset + len(blob) > len(self.data):
                raise RomError(f"write at 0x{offset:X} is outside the ROM")
            old = bytes(self.data[offset:offset + len(blob)])
            if old != blob:
                real.append(Change(offset, old, bytes(blob)))
        if not real:
            return False
        for c in real:
            self.data[c.offset:c.offset + len(c.new)] = c.new
        if self._group is not None:
            self._group.changes.extend(real)
        else:
            self._undo.append(Edit(label, real))
            self._redo.clear()
        self._notify([(c.offset, c.offset + len(c.new)) for c in real], "edit")
        return True

    @contextmanager
    def group(self, label: str) -> Iterator[None]:
        """Make several ``apply`` calls undo as one step."""
        if self._group is None:
            self._group = Edit(label)
        self._group_depth += 1
        try:
            yield
        finally:
            self._group_depth -= 1
            if self._group_depth == 0:
                edit, self._group = self._group, None
                if edit.changes:
                    self._undo.append(edit)
                    self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def history(self) -> list[str]:
        """Labels of the undoable steps, oldest first."""
        return [e.label for e in self._undo]

    @property
    def redo_history(self) -> list[str]:
        return [e.label for e in reversed(self._redo)]

    def undo(self) -> str | None:
        if not self._undo:
            return None
        edit = self._undo.pop()
        for c in reversed(edit.changes):
            self.data[c.offset:c.offset + len(c.old)] = c.old
        self._redo.append(edit)
        self._notify(edit.ranges(), "undo")
        return edit.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        edit = self._redo.pop()
        for c in edit.changes:
            self.data[c.offset:c.offset + len(c.new)] = c.new
        self._undo.append(edit)
        self._notify(edit.ranges(), "redo")
        return edit.label

    # -- state --

    @property
    def dirty(self) -> bool:
        """Changed since the last save (or since load)."""
        return bytes(self.data) != self._saved

    @property
    def modified(self) -> bool:
        """Different from the ROM that was opened."""
        return bytes(self.data) != self.original

    def changed_bytes(self) -> np.ndarray:
        """Bool array: True where the working copy differs from the loaded ROM."""
        return (np.frombuffer(bytes(self.data), dtype=np.uint8)
                != np.frombuffer(self.original, dtype=np.uint8))

    def revert_all(self) -> bool:
        return self.apply("Revert to loaded ROM", self._runs_to(self.original))

    def revert_item(self, key: str) -> bool:
        item = self.definition.item(key)
        base = self.definition.base_offset
        changes = [(s + base, self.original[s + base:e + base]) for s, e in item.ranges()]
        return self.apply(f"Revert {item.title}", changes)

    # -- items --

    def variables(self, vars_: list[MathVar]) -> dict[str, float]:
        """Values for the extra variables of an equation, read from this ROM."""
        out: dict[str, float] = {}
        for v in vars_:
            if v.kind == "address" and v.storage is not None:
                out[v.name] = float(v.storage.read(self.data, self.definition.base_offset)[0, 0])
            elif v.kind == "link":
                try:
                    out[v.name] = self.scalar(v.link)
                except (KeyError, MathError) as exc:
                    raise MathError(f"cannot resolve linked variable {v.name}: {exc}") from exc
        return out

    def table(self, key: str) -> "TableEditor":
        from .table import TableEditor
        table = self.definition.table(key)
        if table.id not in self._editors:
            self._editors[table.id] = TableEditor(self, table)
        return self._editors[table.id]

    def scalar(self, key: str, view: str = "real") -> float:
        s = self.definition.scalar(key)
        raw = s.storage.read(self.data, self.definition.base_offset)
        if view == "raw":
            return float(raw[0, 0])
        return float(s.conversion(self.variables(s.vars)).to_real(raw)[0, 0])

    def set_scalar(self, key: str, value: float, view: str = "real") -> bool:
        s: Scalar = self.definition.scalar(key)
        if view == "raw":
            raw = np.array([[value]])
        else:
            raw, _ = s.conversion(self.variables(s.vars)).to_raw(np.array([[value]]))
        if not s.storage.is_float:
            raw = np.rint(raw).astype(np.int64)
        changes = s.storage.changes(self.data, raw, self.definition.base_offset)
        return self.apply(f"Set {s.title}", changes)

    def flag(self, key: str) -> bool:
        f: Flag = self.definition.flag(key)
        raw = int(f.storage.read(self.data, self.definition.base_offset)[0, 0])
        return (raw & f.mask) == f.mask

    def set_flag(self, key: str, on: bool) -> bool:
        f: Flag = self.definition.flag(key)
        raw = int(f.storage.read(self.data, self.definition.base_offset)[0, 0])
        if f.storage.signed and raw < 0:
            raw += 1 << f.storage.bits
        new = (raw | f.mask) if on else (raw & ~f.mask)
        if f.storage.signed and new >= 1 << (f.storage.bits - 1):
            new -= 1 << f.storage.bits
        changes = f.storage.changes(self.data, np.array([[new]]),
                                    self.definition.base_offset)
        return self.apply(f"{'Set' if on else 'Clear'} {f.title}", changes)

    def patch_state(self, key: str) -> str:
        """"applied", "removed" (base bytes present), "partial" or "unknown"."""
        p: Patch = self.definition.patch(key)
        base = self.definition.base_offset
        applied = removed = 0
        for e in p.entries:
            got = bytes(self.data[e.address + base:e.address + base + len(e.data)])
            if got == e.data:
                applied += 1
            elif e.base is not None and got == e.base:
                removed += 1
        n = len(p.entries)
        if n and applied == n:
            return "applied"
        if n and removed == n:
            return "removed"
        return "partial" if applied or removed else "unknown"

    def apply_patch(self, key: str, force: bool = False) -> bool:
        """Write a patch. Unless ``force``, every entry that names its base
        bytes must find them (or already be applied)."""
        p: Patch = self.definition.patch(key)
        base = self.definition.base_offset
        changes = []
        for e in p.entries:
            start = e.address + base
            got = bytes(self.data[start:start + len(e.data)])
            if not force and e.base is not None and got not in (e.base, e.data):
                raise RomError(
                    f"patch {p.title!r}: bytes at 0x{e.address:X} are not what the "
                    "patch expects; this ROM may be a different base")
            changes.append((start, e.data))
        return self.apply(f"Apply patch {p.title}", changes)

    def remove_patch(self, key: str) -> bool:
        p: Patch = self.definition.patch(key)
        base = self.definition.base_offset
        if any(e.base is None for e in p.entries):
            raise RomError(f"patch {p.title!r} does not record the original bytes; "
                           "use undo or revert instead")
        return self.apply(f"Remove patch {p.title}",
                          [(e.address + base, e.base) for e in p.entries])  # type: ignore[misc]

    # -- checksum, diff, burn image --

    def checksum(self) -> ChecksumReport:
        return checksum_report(self.data, self.definition.checksum)

    def fix_checksum(self) -> bool:
        return self.apply("Fix checksum", checksum_fix(self.data, self.definition.checksum))

    def diff(self) -> "RomDiff":
        """What changed since the ROM was loaded."""
        from .diff import diff_roms
        return diff_roms(self.original, bytes(self.data), self.definition)

    def burn_image(self, **options) -> BurnImage:
        """Chip-ready image of the working copy; raises BurnRefused if unsafe."""
        return build_burn_image(self.data, self.definition,
                                logical_size=self.definition.rom_size, **options)

    # -- saving --

    def default_save_path(self) -> Path:
        if self.source_path is None:
            return self.store_dir / f"{self.name}.tuned.bin"
        stem = self.source_path.stem
        if not stem.endswith(".tuned"):
            stem += ".tuned"
        return self.source_path.with_name(stem + ".bin")

    def save(self, path: str | Path | None = None, *, container: str = "plain",
             fix_checksum: bool = True, snapshot: bool = True) -> SaveResult:
        """Write the working copy. Never onto the file that was opened."""
        target = Path(path) if path else self._save_path or self.default_save_path()
        if self.source_path is not None and target.exists() \
                and os.path.samefile(target, self.source_path):
            raise RomError(
                f"{target.name} is the original ROM; hondalog never overwrites it. "
                "Save under another name.")
        fixed = fix_checksum and self.fix_checksum()
        report = self.checksum()
        rom = RomImage(bytearray(self.data), self.rom.layout)
        write_atomic(target, rom.container_bytes(container))
        self._save_path = target
        self._saved = bytes(self.data)
        snap = self.snapshot() if snapshot else None
        return SaveResult(target, snap, sha256_hex(self.data), report, bool(fixed))

    def snapshot_dir(self) -> Path:
        return self.store_dir / "snapshots" / self.name

    def snapshot(self, note: str = "") -> Path:
        """Store the working copy (logical image) as a timestamped snapshot."""
        digest = sha256_hex(self.data)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        tag = "".join(c if c.isalnum() else "-" for c in note)[:24].strip("-")
        name = f"{stamp}_{digest[:8]}" + (f"_{tag}" if tag else "") + ".bin"
        path = self.snapshot_dir() / name
        write_atomic(path, bytes(self.data))
        return path

    def snapshots(self) -> list[Path]:
        """Snapshots for this ROM name, oldest first."""
        folder = self.snapshot_dir()
        return sorted(folder.glob("*.bin")) if folder.is_dir() else []

    def restore(self, source: str | Path | bytes, label: str = "") -> bool:
        """Load a snapshot, the backup, or any same-size image into the working
        copy as one undoable step."""
        if isinstance(source, (bytes, bytearray)):
            blob, name = bytes(source), "image"
        else:
            image = RomImage.load(source, self.definition.rom_size)
            blob, name = bytes(image.data), Path(source).name
        if len(blob) != len(self.data):
            raise RomError("the image to restore is a different size")
        return self.apply(label or f"Restore {name}", self._runs_to(blob))

    def _runs_to(self, blob: bytes) -> list[tuple[int, bytes]]:
        """Byte writes that turn the working copy into ``blob``."""
        from .diff import byte_runs
        return [(s, blob[s:e]) for s, e in byte_runs(bytes(self.data), blob)]
