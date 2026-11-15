"""Live cursor and trail on a table. Data only; the UI draws it.

Feed it the two channel values that index the table (RPM and MAP for the fuel
and ignition tables) each time the logger has a new sample:

    tracer = TableTracer.for_table(session.table("fuel"))
    pos = tracer.update(t, rpm, map_kpa)
    pos.cells      # [(row, col, weight), ...]  up to four, weights sum to 1
    tracer.heat()  # rows x cols, 0..1, where the engine has just been

It works for any pair of axes, so a "virtual table" on the stock ECU (your own
RPM and MAP bins, no ROM) is the same class.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .binning import AxisError, check_axis, fractional_index

if TYPE_CHECKING:
    from .table import TableEditor


@dataclass
class CursorPosition:
    x: float
    y: float
    col: float                   # fractional column index
    row: float                   # fractional row index
    cells: list[tuple[int, int, float]] = field(default_factory=list)
    nearest: tuple[int, int] = (0, 0)
    clamped_x: bool = False      # x was off the end of the axis
    clamped_y: bool = False
    valid: bool = True

    @property
    def clamped(self) -> bool:
        return self.clamped_x or self.clamped_y


INVALID = CursorPosition(float("nan"), float("nan"), float("nan"), float("nan"),
                         valid=False)


class TableTracer:
    def __init__(self, x_axis, y_axis, trail_seconds: float = 5.0,
                 max_points: int = 2000):
        self.x_axis = check_axis(x_axis)
        self.y_axis = check_axis(y_axis)
        self.shape = (self.y_axis.size, self.x_axis.size)
        self.trail_seconds = float(trail_seconds)
        self._trail: deque[tuple[float, float, float]] = deque(maxlen=max_points)
        self.cursor: CursorPosition = INVALID
        self.hits = np.zeros(self.shape)         # weighted sample count per cell
        self.dwell = np.zeros(self.shape)        # seconds spent per cell
        self._last_t: float | None = None

    @classmethod
    def for_table(cls, editor: "TableEditor", **options) -> "TableTracer":
        """Tracer on a table's own axes. Fails if an axis has no numbers."""
        x, y = editor.axis("x"), editor.axis("y")
        if not x.numeric or not y.numeric:
            raise AxisError(f"{editor.title}: an axis has no numeric breakpoints")
        return cls(x.values, y.values, **options)

    def locate(self, x: float, y: float) -> CursorPosition:
        """Where (x, y) falls, without recording anything."""
        if x is None or y is None or not (np.isfinite(x) and np.isfinite(y)):
            return INVALID
        col_idx, cx = fractional_index(self.x_axis, float(x))
        row_idx, cy = fractional_index(self.y_axis, float(y))
        col, row = float(col_idx), float(row_idx)
        rows, cols = self.shape
        c0 = min(int(np.floor(col)), max(cols - 2, 0))
        r0 = min(int(np.floor(row)), max(rows - 2, 0))
        wc, wr = col - c0, row - r0
        cells: dict[tuple[int, int], float] = {}
        for r, c, w in ((r0, c0, (1 - wr) * (1 - wc)), (r0, c0 + 1, (1 - wr) * wc),
                        (r0 + 1, c0, wr * (1 - wc)), (r0 + 1, c0 + 1, wr * wc)):
            if w > 0 and r < rows and c < cols:
                cells[(r, c)] = cells.get((r, c), 0.0) + w
        ordered = sorted(((r, c, w) for (r, c), w in cells.items()),
                         key=lambda item: -item[2])
        nearest = (min(int(np.floor(row + 0.5)), rows - 1),
                   min(int(np.floor(col + 0.5)), cols - 1))
        return CursorPosition(float(x), float(y), col, row, ordered, nearest,
                              bool(cx), bool(cy))

    def update(self, t: float, x: float | None, y: float | None) -> CursorPosition:
        """Record a sample at time ``t`` (seconds) and return the cursor."""
        pos = self.locate(x, y)                  # type: ignore[arg-type]
        self.cursor = pos
        if not pos.valid:
            self._last_t = None                  # a gap: do not count dwell across it
            return pos
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 1.0))
        self._last_t = t
        for r, c, w in pos.cells:
            self.hits[r, c] += w
            self.dwell[r, c] += w * dt
        self._trail.append((t, pos.col, pos.row))
        self._expire(t)
        return pos

    def _expire(self, now: float) -> None:
        while self._trail and now - self._trail[0][0] > self.trail_seconds:
            self._trail.popleft()

    def trail(self, now: float | None = None) -> np.ndarray:
        """Recent positions, oldest first: columns (col, row, age_s, alpha).

        ``alpha`` fades linearly from 1 (now) to 0 (``trail_seconds`` old).
        """
        if not self._trail:
            return np.zeros((0, 4))
        pts = np.array(self._trail)
        now = pts[-1, 0] if now is None else now
        age = now - pts[:, 0]
        keep = (age >= 0) & (age <= self.trail_seconds)
        alpha = 1.0 - age / self.trail_seconds if self.trail_seconds > 0 else np.ones_like(age)
        return np.column_stack((pts[:, 1], pts[:, 2], age, alpha))[keep]

    def heat(self, now: float | None = None) -> np.ndarray:
        """Fading per-cell intensity 0..1: the brightest recent visit to each
        cell, scaled by how much of the sample fell in that cell."""
        out = np.zeros(self.shape)
        rows, cols = self.shape
        for col, row, _, alpha in self.trail(now):
            c0 = min(int(np.floor(col)), max(cols - 2, 0))
            r0 = min(int(np.floor(row)), max(rows - 2, 0))
            wc, wr = col - c0, row - r0
            for r, c, w in ((r0, c0, (1 - wr) * (1 - wc)), (r0, c0 + 1, (1 - wr) * wc),
                            (r0 + 1, c0, wr * (1 - wc)), (r0 + 1, c0 + 1, wr * wc)):
                if w > 0 and r < rows and c < cols:
                    out[r, c] = max(out[r, c], w * alpha)
        return out

    def reset(self) -> None:
        """Forget the trail and the hit counts."""
        self._trail.clear()
        self.hits[:] = 0
        self.dwell[:] = 0
        self.cursor = INVALID
        self._last_t = None
