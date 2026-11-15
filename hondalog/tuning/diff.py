"""Compare ROMs: by raw bytes, and by what the definition says the bytes mean."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from .definition import Definition, Flag, Item, Patch, Scalar, Table
from .mathexpr import MathError
from .rom import RomError


def byte_runs(a: bytes | bytearray, b: bytes | bytearray,
              join_gap: int = 0) -> list[tuple[int, int]]:
    """Half-open (start, end) ranges where the images differ.

    Runs separated by ``join_gap`` or fewer equal bytes are merged.
    """
    if len(a) != len(b):
        raise RomError(f"cannot compare images of {len(a)} and {len(b)} bytes")
    diff = np.frombuffer(bytes(a), dtype=np.uint8) != np.frombuffer(bytes(b), dtype=np.uint8)
    idx = np.nonzero(diff)[0]
    if idx.size == 0:
        return []
    breaks = np.nonzero(np.diff(idx) > 1 + join_gap)[0]
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]])) + 1
    return [(int(s), int(e)) for s, e in zip(starts, ends)]


@dataclass
class RegionDiff:
    """Changed bytes that belong to one defined item, or to none."""

    item_id: str | None          # None = undefined region
    title: str
    kind: str                    # table, scalar, flag, undefined
    runs: list[tuple[int, int]]
    bytes_changed: int

    @property
    def start(self) -> int:
        return self.runs[0][0]

    @property
    def end(self) -> int:
        return self.runs[-1][1]


@dataclass
class TableDiff:
    id: str
    title: str
    units: str
    a: np.ndarray                # values in ROM A (real units, or raw if no equation)
    b: np.ndarray
    delta: np.ndarray            # b - a
    percent: np.ndarray          # 100 * (b - a) / a; NaN where a is 0 and b is not
    changed: np.ndarray          # bool, by stored value
    view: str                    # "real" or "raw"
    axes_changed: bool = False

    @property
    def cells_changed(self) -> int:
        return int(self.changed.sum())

    @property
    def max_abs_delta(self) -> float:
        return float(np.abs(self.delta[self.changed]).max()) if self.changed.any() else 0.0

    @property
    def max_abs_percent(self) -> float:
        p = np.abs(self.percent[self.changed])
        p = p[np.isfinite(p)]
        return float(p.max()) if p.size else 0.0

    @property
    def mean_delta(self) -> float:
        return float(self.delta[self.changed].mean()) if self.changed.any() else 0.0

    def summary(self) -> str:
        return (f"{self.title}: {self.cells_changed} of {self.changed.size} cells, "
                f"max {self.max_abs_delta:.4g} {self.units} "
                f"({self.max_abs_percent:.1f}%), mean {self.mean_delta:+.4g}"
                + (", axis changed" if self.axes_changed else ""))


@dataclass
class ScalarDiff:
    id: str
    title: str
    units: str
    a: float
    b: float

    @property
    def delta(self) -> float:
        return self.b - self.a

    @property
    def percent(self) -> float:
        return 100.0 * (self.b - self.a) / self.a if self.a else float("nan")


@dataclass
class FlagDiff:
    id: str
    title: str
    a: bool
    b: bool


@dataclass
class RomDiff:
    size: int
    bytes_changed: int
    runs: list[tuple[int, int]]
    regions: list[RegionDiff] = field(default_factory=list)
    tables: list[TableDiff] = field(default_factory=list)
    scalars: list[ScalarDiff] = field(default_factory=list)
    flags: list[FlagDiff] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return self.bytes_changed == 0

    @property
    def undefined(self) -> list[RegionDiff]:
        return [r for r in self.regions if r.item_id is None]

    @property
    def undefined_bytes(self) -> int:
        return sum(r.bytes_changed for r in self.undefined)

    def table(self, key: str) -> TableDiff | None:
        return next((t for t in self.tables if key in (t.id, t.title)), None)

    def summary(self) -> list[str]:
        if self.identical:
            return ["The ROMs are identical."]
        lines = [f"{self.bytes_changed} bytes differ in {len(self.runs)} places."]
        lines += [t.summary() for t in self.tables]
        lines += [f"{s.title}: {s.a:g} -> {s.b:g} {s.units}".rstrip() for s in self.scalars]
        lines += [f"{f.title}: {'on' if f.a else 'off'} -> {'on' if f.b else 'off'}"
                  for f in self.flags]
        if self.undefined:
            lines.append(f"{self.undefined_bytes} changed bytes in "
                         f"{len(self.undefined)} undefined regions "
                         f"(first at 0x{self.undefined[0].start:04X}).")
        return lines


def _real_or_raw(item_axis, storage, data: bytes, base: int, variables) -> tuple[np.ndarray, str]:
    raw = storage.read(data, base)
    try:
        conv = item_axis.conversion(variables)
        r, c = np.meshgrid(np.arange(storage.rows), np.arange(storage.cols), indexing="ij")
        return conv.to_real(raw, r, c), "real"
    except MathError:
        return raw.astype(float), "raw"


def _variables(vars_, data: bytes, definition: Definition) -> dict[str, float]:
    out: dict[str, float] = {}
    for v in vars_:
        if v.kind == "address" and v.storage is not None:
            out[v.name] = float(v.storage.read(data, definition.base_offset)[0, 0])
        elif v.kind == "link":
            try:
                s = definition.scalar(v.link)
                raw = s.storage.read(data, definition.base_offset)
                out[v.name] = float(s.conversion(_variables(s.vars, data, definition))
                                    .to_real(raw)[0, 0])
            except (KeyError, MathError):
                pass                             # conversion() then fails -> raw view
    return out


def diff_roms(a: bytes | bytearray, b: bytes | bytearray,
              definition: Definition | None = None) -> RomDiff:
    """Compare two logical images. With a definition, changed bytes are grouped
    by the item that owns them and tables are compared cell by cell."""
    a, b = bytes(a), bytes(b)
    runs = byte_runs(a, b)
    diff_mask = np.frombuffer(a, dtype=np.uint8) != np.frombuffer(b, dtype=np.uint8)
    out = RomDiff(len(a), int(diff_mask.sum()), runs)
    if not runs:
        return out
    if definition is None:
        out.regions = [RegionDiff(None, "undefined", "undefined", [r], r[1] - r[0])
                       for r in runs]
        return out
    base = definition.base_offset
    owned = np.zeros(len(a), dtype=bool)
    for item in definition.items():
        if isinstance(item, Patch):
            continue
        item_mask = np.zeros(len(a), dtype=bool)
        for start, end in item.ranges():
            item_mask[max(0, start + base):max(0, end + base)] = True
        hit = item_mask & diff_mask & ~owned
        if (item_mask & diff_mask).any():
            _describe(out, item, a, b, definition)
        if hit.any():
            out.regions.append(RegionDiff(item.id, item.title, item.kind,
                                          _mask_runs(hit), int(hit.sum())))
        owned |= item_mask
    rest = diff_mask & ~owned
    for run in _mask_runs(rest):
        out.regions.append(RegionDiff(None, "undefined", "undefined", [run],
                                      run[1] - run[0]))
    out.regions.sort(key=lambda r: r.start)
    return out


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    idx = np.nonzero(mask)[0]
    if idx.size == 0:
        return []
    breaks = np.nonzero(np.diff(idx) > 1)[0]
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]])) + 1
    return [(int(s), int(e)) for s, e in zip(starts, ends)]


def _describe(out: RomDiff, item: Item, a: bytes, b: bytes, d: Definition) -> None:
    base = d.base_offset
    if isinstance(item, Table):
        st = item.z.storage
        assert st is not None
        va, view_a = _real_or_raw(item.z, st, a, base, _variables(item.z.vars, a, d))
        vb, view_b = _real_or_raw(item.z, st, b, base, _variables(item.z.vars, b, d))
        if view_a != view_b:
            va, vb = st.read(a, base).astype(float), st.read(b, base).astype(float)
        changed = st.read(a, base) != st.read(b, base)
        delta = vb - va
        with np.errstate(all="ignore"):
            percent = np.where(va != 0, 100.0 * delta / va,
                               np.where(delta == 0, 0.0, np.nan))
        axes_changed = any(
            ax.storage is not None
            and not np.array_equal(ax.storage.read(a, base), ax.storage.read(b, base))
            for ax in (item.x, item.y))
        out.tables.append(TableDiff(item.id, item.title, item.z.units, va, vb, delta,
                                    percent, changed,
                                    view_a if view_a == view_b else "raw", axes_changed))
    elif isinstance(item, Scalar):
        def value(data: bytes) -> float:
            raw = item.storage.read(data, base)
            try:
                return float(item.conversion(_variables(item.vars, data, d)).to_real(raw)[0, 0])
            except MathError:
                return float(raw[0, 0])
        out.scalars.append(ScalarDiff(item.id, item.title, item.units, value(a), value(b)))
    elif isinstance(item, Flag):
        def state(data: bytes) -> bool:
            raw = int(item.storage.read(data, base)[0, 0]) & ((1 << item.storage.bits) - 1)
            return (raw & item.mask) == item.mask
        sa, sb = state(a), state(b)
        if sa != sb:
            out.flags.append(FlagDiff(item.id, item.title, sa, sb))


def compare_many(base: bytes | bytearray, others: Mapping[str, bytes | bytearray],
                 definition: Definition | None = None) -> dict[str, RomDiff]:
    """Compare one ROM against several (TunerPro allows four; no limit here)."""
    return {name: diff_roms(base, other, definition) for name, other in others.items()}


def table_stack(base: bytes | bytearray, others: Mapping[str, bytes | bytearray],
                definition: Definition, table_key: str) -> dict[str, np.ndarray]:
    """One table's values in the base ROM ("base") and in each other ROM,
    for a side-by-side or overlay view."""
    table = definition.table(table_key)
    st = table.z.storage
    assert st is not None
    out = {}
    for name, data in {"base": base, **others}.items():
        data = bytes(data)
        out[name], _ = _real_or_raw(table.z, st, data, definition.base_offset,
                                    _variables(table.z.vars, data, definition))
    return out
