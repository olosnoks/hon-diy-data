"""The table editing model: what a spreadsheet-style grid needs, without Qt.

A :class:`TableEditor` belongs to a :class:`~hondalog.tuning.session.RomSession`.
It reads the ROM on every call, so it is always current after undo/redo, and
writes through ``session.apply`` so every operation is one undo step.

Views: "real" (engineering units, via the definition's equation) and "raw"
(the stored integers). Operations take a *selection*:

    None                  the whole table
    (r0, c0, r1, c1)      a rectangle, both corners included
    [(r, c), ...]         single cells
    bool array            shape (rows, cols)

Rows are Y, columns are X; row 0 is the first row stored in the ROM.
Cells that were not selected (or are locked) keep their exact bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence, Union, cast

import numpy as np

from .definition import Axis, Storage, Table
from .mathexpr import Conversion, MathError

if TYPE_CHECKING:
    from .session import RomSession

Selection = Union[None, tuple[int, int, int, int], Sequence[tuple[int, int]], np.ndarray]


class TableError(Exception):
    pass


@dataclass
class AxisValues:
    """One axis as the grid header needs it."""

    values: np.ndarray           # numbers (breakpoints, or 0..n-1 when there are none)
    labels: list[str]            # text for each position
    units: str
    source: str                  # "rom", "labels", "link" or "index"
    editable: bool               # True when the breakpoints live in the ROM
    numeric: bool                # False when ``values`` is only an index

    def __len__(self) -> int:
        return len(self.labels)


@dataclass
class EditResult:
    changed: int                 # cells whose stored value changed
    clipped: int = 0             # cells that hit the storage limit
    skipped_locked: int = 0
    truncated: bool = False      # a paste ran off the edge of the table

    def __bool__(self) -> bool:
        return self.changed > 0


def format_number(value: float, decimals: int | None) -> str:
    if decimals is None:
        text = f"{value:.6g}"
        return text
    return f"{value:.{max(0, decimals)}f}"


class TableEditor:
    def __init__(self, session: "RomSession", table: Table):
        self.session = session
        self.table = table
        self.base = session.definition.base_offset
        storage = table.z.storage
        assert storage is not None
        self.storage: Storage = storage
        self.rows, self.cols = table.rows, table.cols
        self._row_idx, self._col_idx = np.meshgrid(
            np.arange(self.rows), np.arange(self.cols), indexing="ij")
        self.math_error: str | None = None
        try:
            self._conversion(table.z)
        except MathError as exc:
            self.math_error = str(exc)       # real view unavailable; raw still works

    # -- basics --

    @property
    def id(self) -> str:
        return self.table.id

    @property
    def title(self) -> str:
        return self.table.title

    @property
    def units(self) -> str:
        return self.table.z.units

    @property
    def decimals(self) -> int | None:
        return self.table.z.decimals

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    @property
    def real_available(self) -> bool:
        return self.math_error is None

    def _conversion(self, axis: Axis) -> Conversion:
        return axis.conversion(self.session.variables(axis.vars))

    def _check_view(self, view: str) -> None:
        if view not in ("real", "raw"):
            raise TableError(f"view must be 'real' or 'raw', not {view!r}")
        if view == "real" and self.math_error:
            raise TableError(f"{self.title}: {self.math_error}")

    def raw(self, data: bytes | bytearray | None = None) -> np.ndarray:
        return self.storage.read(self.session.data if data is None else data, self.base)

    def values(self, view: str = "real", data: bytes | bytearray | None = None) -> np.ndarray:
        """The cells, shape (rows, cols). ``data`` reads another image instead."""
        self._check_view(view)
        raw = self.raw(data)
        if view == "raw":
            return raw
        return self._conversion(self.table.z).to_real(raw, self._row_idx, self._col_idx)

    def original_values(self, view: str = "real") -> np.ndarray:
        return self.values(view, self.session.original)

    def changed_mask(self) -> np.ndarray:
        """True for cells that differ from the ROM as it was loaded."""
        return self.raw() != self.raw(self.session.original)

    def limits(self, view: str = "real") -> tuple[float, float]:
        """Smallest and largest value a cell can hold."""
        conv = self._conversion(self.table.z)
        if view == "raw" or self.math_error:
            return (float(conv.lo), float(conv.hi))
        ends = conv.to_real(np.array([conv.lo, conv.hi], dtype=float))
        return (float(np.nanmin(ends)), float(np.nanmax(ends)))

    def format(self, value: float, view: str = "real") -> str:
        if view == "raw" and not self.storage.is_float:
            return str(int(value))
        return format_number(float(value), self.decimals)

    # -- axes --

    def axis(self, which: str) -> AxisValues:
        """Column ("x") or row ("y") header values."""
        axis, n = (self.table.x, self.cols) if which == "x" else (self.table.y, self.rows)
        if which not in ("x", "y"):
            raise TableError("axis must be 'x' or 'y'")
        index = np.arange(n, dtype=float)
        if axis.storage is not None and axis.storage.count == n:
            raw = axis.storage.read(self.session.data, self.base).ravel()
            try:
                values = self._conversion(axis).to_real(raw)
            except MathError:
                values = raw.astype(float)
            labels = [format_number(v, axis.decimals) for v in values]
            return AxisValues(values, labels, axis.units, "rom", True, True)
        if axis.link:
            linked = self._linked_axis(axis.link, n)
            if linked is not None:
                labels = [format_number(v, axis.decimals) for v in linked]
                return AxisValues(linked, labels, axis.units, "link", False, True)
        if axis.labels and len(axis.labels) == n and any(l.strip() for l in axis.labels):
            try:
                values = np.array([float(l) for l in axis.labels])
                return AxisValues(values, list(axis.labels), axis.units, "labels", False, True)
            except ValueError:
                return AxisValues(index, list(axis.labels), axis.units, "labels", False, False)
        return AxisValues(index, [str(i) for i in range(n)], axis.units, "index", False, False)

    def _linked_axis(self, link: str, n: int) -> np.ndarray | None:
        try:
            other = self.session.definition.table(link)
            values = self.session.table(other.id).values("real").ravel()
        except (KeyError, TableError, MathError):
            return None
        return values.astype(float) if values.size == n else None

    def set_axis_value(self, which: str, index: int, value: float,
                       view: str = "real") -> EditResult:
        """Change one breakpoint (only for axes stored in the ROM)."""
        axis = self.table.x if which == "x" else self.table.y
        if axis.storage is None:
            raise TableError(f"the {which} axis of {self.title} is not stored in the ROM")
        st = axis.storage
        raw = st.read(self.session.data, self.base)
        if not 0 <= index < st.count:
            raise TableError("axis index out of range")
        clipped = 0
        if view == "raw":
            new = value
        else:
            got, clip = self._conversion(axis).to_raw(np.array([value], dtype=float))
            new, clipped = got[0], int(clip[0])
        new_raw = raw.copy()
        new_raw.ravel()[index] = new if st.is_float else int(round(float(new)))
        try:
            changes = st.changes(self.session.data, new_raw, self.base)
        except Exception as exc:                 # noqa: BLE001 - range error text
            raise TableError(str(exc)) from exc
        done = self.session.apply(f"{self.title}: {which} axis", changes)
        return EditResult(int(done), clipped)

    # -- selections and locks --

    def mask(self, selection: Selection = None) -> np.ndarray:
        """Normalise any selection form to a bool array."""
        out = np.zeros(self.shape, dtype=bool)
        if selection is None:
            out[:] = True
        elif isinstance(selection, np.ndarray):
            if selection.shape != self.shape:
                raise TableError(f"selection mask must have shape {self.shape}")
            out = selection.astype(bool)
        elif (isinstance(selection, tuple) and len(selection) == 4
              and all(isinstance(v, (int, np.integer)) for v in selection)):
            r0, c0, r1, c1 = (int(v) for v in cast(tuple[int, int, int, int], selection))
            r0, r1 = sorted((r0, r1))
            c0, c1 = sorted((c0, c1))
            if r0 < 0 or c0 < 0 or r1 >= self.rows or c1 >= self.cols:
                raise TableError("selection is outside the table")
            out[r0:r1 + 1, c0:c1 + 1] = True
        else:
            for r, c in cast(Sequence[tuple[int, int]], selection):
                if not (0 <= r < self.rows and 0 <= c < self.cols):
                    raise TableError("selection is outside the table")
                out[r, c] = True
        return out

    @property
    def locked(self) -> np.ndarray:
        return self.session.locks.get(self.id, np.zeros(self.shape, dtype=bool))

    def lock(self, selection: Selection = None) -> None:
        """Protect cells from every edit until unlocked."""
        self.session.locks[self.id] = self.locked | self.mask(selection)

    def unlock(self, selection: Selection = None) -> None:
        self.session.locks[self.id] = self.locked & ~self.mask(selection)

    # -- the write path --

    def _write(self, new: np.ndarray, mask: np.ndarray, view: str, label: str,
               truncated: bool = False) -> EditResult:
        self._check_view(view)
        if np.any(~np.isfinite(np.asarray(new, dtype=float)[mask])):
            raise TableError("cannot store a value that is not a number")
        locked = self.locked & mask
        mask = mask & ~self.locked
        raw_now = self.raw()
        if not mask.any():
            return EditResult(0, 0, int(locked.sum()), truncated)
        target = np.where(mask, new, 0.0)
        if view == "raw":
            conv = self._conversion(self.table.z) if not self.math_error else \
                Conversion("X", bits=self.storage.bits, signed=self.storage.signed,
                           is_float=self.storage.is_float)
            if self.storage.is_float:
                converted, clipped = target.astype(float), np.zeros(self.shape, bool)
            else:
                rounded = np.sign(target) * np.floor(np.abs(target) + 0.5)
                clipped = (rounded < conv.lo) | (rounded > conv.hi)
                converted = np.clip(rounded, conv.lo, conv.hi).astype(np.int64)
        else:
            converted, clipped = self._conversion(self.table.z).to_raw(
                target, self._row_idx, self._col_idx)
        raw_new = np.where(mask, converted, raw_now)
        if not self.storage.is_float:
            raw_new = raw_new.astype(np.int64)
        changes = self.storage.changes(self.session.data, raw_new, self.base)
        self.session.apply(f"{self.title}: {label}", changes)
        return EditResult(int(((raw_new != raw_now) & mask).sum()),
                          int((clipped & mask).sum()), int(locked.sum()), truncated)

    # -- cell operations --

    def set_cell(self, row: int, col: int, value: float, view: str = "real") -> EditResult:
        return self.set_value([(row, col)], value, view)

    def set_value(self, selection: Selection, value: float, view: str = "real") -> EditResult:
        mask = self.mask(selection)
        return self._write(np.full(self.shape, float(value)), mask, view, "set")

    def set_values(self, values: np.ndarray, selection: Selection = None,
                   view: str = "real", label: str = "set") -> EditResult:
        """Write an array the size of the table; only selected cells are used."""
        values = np.asarray(values, dtype=float)
        if values.shape != self.shape:
            raise TableError(f"values must have shape {self.shape}")
        return self._write(values, self.mask(selection), view, label)

    def add(self, selection: Selection, amount: float, view: str = "real") -> EditResult:
        return self._write(self.values(view) + amount, self.mask(selection), view,
                           f"add {amount:g}")

    def scale(self, selection: Selection, factor: float | np.ndarray,
              view: str = "real", label: str | None = None) -> EditResult:
        """Multiply. ``factor`` may be one number or an array the table's shape."""
        factor_arr = np.asarray(factor, dtype=float)
        text = label or (f"multiply by {float(factor_arr):g}" if factor_arr.ndim == 0
                         else "multiply")
        return self._write(self.values(view) * factor_arr, self.mask(selection), view, text)

    def percent(self, selection: Selection, percent: float, view: str = "real") -> EditResult:
        """Change by a percentage: +5 makes every selected cell 5% bigger."""
        return self._write(self.values(view) * (1.0 + percent / 100.0),
                           self.mask(selection), view, f"{percent:+g}%")

    def interpolate(self, selection: Selection, direction: str = "both",
                    view: str = "real", by_axis: bool = True) -> EditResult:
        """Fill the inside of the selection from its edges.

        direction: "horizontal" (along each row, between the row's first and
        last selected cell), "vertical" (along each column), or "both"
        (bilinear from the four corners of the selection's bounding box).
        ``by_axis`` spaces the fill by the axis breakpoints instead of by
        cell count, when the axis has usable numbers.
        """
        mask = self.mask(selection)
        if not mask.any():
            return EditResult(0)
        values = self.values(view).astype(float)
        out = values.copy()
        xs = self._positions("x", by_axis)
        ys = self._positions("y", by_axis)
        if direction == "horizontal":
            for r in np.nonzero(mask.any(axis=1))[0]:
                cols = np.nonzero(mask[r])[0]
                a, b = cols[0], cols[-1]
                if b > a:
                    out[r, cols] = np.interp(xs[cols], [xs[a], xs[b]],
                                             [values[r, a], values[r, b]])
        elif direction == "vertical":
            for c in np.nonzero(mask.any(axis=0))[0]:
                rws = np.nonzero(mask[:, c])[0]
                a, b = rws[0], rws[-1]
                if b > a:
                    out[rws, c] = np.interp(ys[rws], [ys[a], ys[b]],
                                            [values[a, c], values[b, c]])
        elif direction == "both":
            rws, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
            r0, r1, c0, c1 = rws[0], rws[-1], cols[0], cols[-1]
            tx = np.zeros(self.cols) if c1 == c0 else (xs - xs[c0]) / (xs[c1] - xs[c0])
            ty = np.zeros(self.rows) if r1 == r0 else (ys - ys[r0]) / (ys[r1] - ys[r0])
            tx, ty = tx[None, :], ty[:, None]
            out = (values[r0, c0] * (1 - tx) * (1 - ty) + values[r0, c1] * tx * (1 - ty)
                   + values[r1, c0] * (1 - tx) * ty + values[r1, c1] * tx * ty)
        else:
            raise TableError("direction must be 'horizontal', 'vertical' or 'both'")
        return self._write(out, mask, view, f"interpolate {direction}")

    def _positions(self, which: str, by_axis: bool) -> np.ndarray:
        n = self.cols if which == "x" else self.rows
        if by_axis:
            axis = self.axis(which)
            v = axis.values.astype(float)
            if axis.numeric and n > 1 and (np.all(np.diff(v) > 0) or np.all(np.diff(v) < 0)):
                return v if v[-1] > v[0] else -v
        return np.arange(n, dtype=float)

    def smooth(self, selection: Selection = None, strength: float = 0.5,
               passes: int = 1, view: str = "real") -> EditResult:
        """Blend each selected cell toward the weighted average of its 3x3
        neighbourhood. strength 0 leaves it alone, 1 replaces it."""
        if not 0.0 <= strength <= 1.0:
            raise TableError("smoothing strength must be between 0 and 1")
        mask = self.mask(selection)
        values = self.values(view).astype(float)
        out = values.copy()
        for _ in range(max(1, int(passes))):
            out = np.where(mask, out + strength * (smooth_kernel(out) - out), out)
        return self._write(out, mask, view, f"smooth {strength:g}")

    # -- clipboard --

    def copy(self, selection: Selection = None, view: str = "real",
             with_axes: bool = False) -> str:
        """Tab-separated text of the selection's bounding box, as spreadsheets
        expect. Cells inside the box that are not selected are left empty."""
        mask = self.mask(selection)
        if not mask.any():
            return ""
        rws, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
        r0, r1, c0, c1 = rws[0], rws[-1], cols[0], cols[-1]
        values = self.values(view)
        lines = []
        if with_axes:
            lines.append("\t".join([""] + self.axis("x").labels[c0:c1 + 1]))
        ylabels = self.axis("y").labels
        for r in range(r0, r1 + 1):
            cells = [self.format(values[r, c], view) if mask[r, c] else ""
                     for c in range(c0, c1 + 1)]
            lines.append("\t".join(([ylabels[r]] if with_axes else []) + cells))
        return "\n".join(lines) + "\n"

    def paste(self, text: str, at: tuple[int, int] | Selection = (0, 0),
              view: str = "real") -> EditResult:
        """Paste spreadsheet text. ``at`` is the top-left cell, or a selection:
        its top-left is used, and a single pasted value fills all of it."""
        grid = parse_clipboard(text)
        if not grid:
            return EditResult(0)
        if isinstance(at, tuple) and len(at) == 2:
            mask_at = self.mask([cast(tuple[int, int], at)])
        else:
            mask_at = self.mask(cast(Selection, at))
        if not mask_at.any():
            return EditResult(0)
        if len(grid) == 1 and len(grid[0]) == 1 and grid[0][0] is not None:
            return self._write(np.full(self.shape, grid[0][0]), mask_at, view, "paste")
        r0 = int(np.nonzero(mask_at.any(axis=1))[0][0])
        c0 = int(np.nonzero(mask_at.any(axis=0))[0][0])
        new = np.zeros(self.shape)
        mask = np.zeros(self.shape, dtype=bool)
        truncated = False
        for dr, line in enumerate(grid):
            for dc, value in enumerate(line):
                r, c = r0 + dr, c0 + dc
                if r >= self.rows or c >= self.cols:
                    truncated = truncated or value is not None
                    continue
                if value is not None:
                    new[r, c], mask[r, c] = value, True
        return self._write(new, mask, view, "paste", truncated)

    # -- history passthrough --

    def undo(self) -> str | None:
        return self.session.undo()

    def redo(self) -> str | None:
        return self.session.redo()

    def revert(self, selection: Selection = None) -> EditResult:
        """Put selected cells back to the values in the loaded ROM."""
        mask = self.mask(selection)
        return self._write(self.raw(self.session.original).astype(float), mask, "raw",
                           "revert")


def smooth_kernel(values: np.ndarray) -> np.ndarray:
    """3x3 weighted average (1-2-1 in each direction), edges renormalised."""
    k = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]])
    rows, cols = values.shape
    padded = np.pad(values, 1, mode="constant")
    weight = np.pad(np.ones_like(values), 1, mode="constant")
    total = np.zeros_like(values, dtype=float)
    wsum = np.zeros_like(values, dtype=float)
    for dr in range(3):
        for dc in range(3):
            total += k[dr, dc] * padded[dr:dr + rows, dc:dc + cols]
            wsum += k[dr, dc] * weight[dr:dr + rows, dc:dc + cols]
    return total / wsum


_NUMBER = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def parse_clipboard(text: str) -> list[list[float | None]]:
    """Spreadsheet text -> rows of numbers (None for empty cells).

    Tabs separate cells; without tabs, semicolons, then commas or spaces do.
    A decimal comma is accepted when the separator is not a comma.
    """
    lines = [l for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    out: list[list[float | None]] = []
    for line in lines:
        if "\t" in line:
            cells, decimal_comma = line.split("\t"), True
        elif ";" in line:
            cells, decimal_comma = line.split(";"), True
        elif "," in line and " " not in line.strip():
            cells, decimal_comma = line.split(","), False
        else:
            cells, decimal_comma = line.split(), True
        row: list[float | None] = []
        for cell in cells:
            cell = cell.strip()
            if not cell:
                row.append(None)
                continue
            if decimal_comma and "," in cell and "." not in cell:
                cell = cell.replace(",", ".")
            if not _NUMBER.match(cell):
                raise TableError(f"cannot paste {cell!r}: not a number")
            row.append(float(cell))
        out.append(row)
    return out
