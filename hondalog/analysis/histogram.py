"""Table histogram: bin two channels (say RPM and MAP) and summarise a third per cell.

This is the view tuners live in: "what was the average short-term trim in the
1500 rpm / 40 kPa cell, and how many samples is that based on".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..data.model import BoolArray, FloatArray, Table

STATS = ("mean", "min", "max", "count", "std", "sum")


@dataclass(frozen=True)
class Bins:
    """Cell boundaries along one axis. ``centres`` label the cells."""

    edges: tuple[float, ...]
    centres: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.centres)

    @classmethod
    def from_edges(cls, edges: Sequence[float]) -> "Bins":
        e = [float(x) for x in edges]
        if len(e) < 2 or any(b <= a for a, b in zip(e, e[1:])):
            raise ValueError("bin edges must be at least two rising numbers")
        return cls(tuple(e), tuple((a + b) / 2 for a, b in zip(e, e[1:])))

    @classmethod
    def from_breakpoints(cls, points: Sequence[float], clamp: bool = True) -> "Bins":
        """Cells centred on table breakpoints (an ECU table's RPM or MAP axis).

        Boundaries are halfway between neighbours. With ``clamp`` the first
        and last cell also take everything beyond them, the way an ECU clamps
        its table lookup; without it the outer cells are half a step wide.
        """
        p = [float(x) for x in points]
        if len(p) < 2 or any(b <= a for a, b in zip(p, p[1:])):
            raise ValueError("breakpoints must be at least two rising numbers")
        mids = [(a + b) / 2 for a, b in zip(p, p[1:])]
        if clamp:
            edges = [-np.inf, *mids, np.inf]
        else:
            edges = [p[0] - (mids[0] - p[0]), *mids, p[-1] + (p[-1] - mids[-1])]
        return cls(tuple(edges), tuple(p))

    @classmethod
    def linear(cls, lo: float, hi: float, n: int) -> "Bins":
        return cls.from_edges(np.linspace(lo, hi, n + 1).tolist())

    def index(self, values: FloatArray) -> np.ndarray:
        """Cell number per value; -1 for NaN and values outside every cell."""
        edges = np.asarray(self.edges)
        idx = np.searchsorted(edges, values, side="right") - 1
        idx[values == edges[-1]] = len(self) - 1        # the top edge belongs to the last cell
        bad = np.isnan(values) | (idx < 0) | (idx >= len(self))
        idx[bad] = -1
        return idx


#: Generic axes. They are *not* read from a ROM: pass the real fuel table's
#: axes to ``Bins.from_breakpoints`` once W5 can read them.
RPM_500 = Bins.from_breakpoints(list(range(500, 7501, 500)))
RPM_250 = Bins.from_breakpoints(list(range(500, 7501, 250)))
MAP_KPA_10 = Bins.from_breakpoints(list(range(20, 101, 10)))
MAP_KPA_5 = Bins.from_breakpoints(list(range(15, 106, 5)))
TPS_10 = Bins.from_breakpoints(list(range(0, 101, 10)))
PRESETS = {"rpm 500": RPM_500, "rpm 250": RPM_250, "map 10 kPa": MAP_KPA_10,
           "map 5 kPa": MAP_KPA_5, "tps 10 %": TPS_10}


class TableHistogram:
    """Per-cell statistics of ``z`` binned by ``x`` and ``y``.

    Grids have shape (len(y_bins), len(x_bins)): row = Y cell, column = X cell.
    ``rows`` are the indices into the source arrays of every sample that went
    into a cell, so the UI can list them and jump to their time.
    """

    def __init__(self, x: FloatArray, y: FloatArray, z: FloatArray, x_bins: Bins, y_bins: Bins,
                 mask: BoolArray | None = None, t: FloatArray | None = None):
        self.x_bins, self.y_bins = x_bins, y_bins
        self.t = t
        nx, ny = len(x_bins), len(y_bins)
        ix, iy = x_bins.index(np.asarray(x, float)), y_bins.index(np.asarray(y, float))
        z = np.asarray(z, dtype=np.float64)
        ok = (ix >= 0) & (iy >= 0) & ~np.isnan(z)
        if mask is not None:
            ok &= mask
        rows = np.flatnonzero(ok)
        cell = iy[rows] * nx + ix[rows]
        order = np.argsort(cell, kind="stable")         # stable: rows stay in time order
        self._rows = rows[order]
        cells = cell[order]
        zs = z[self._rows]
        ncell = nx * ny
        self._start = np.searchsorted(cells, np.arange(ncell), side="left")
        self._end = np.searchsorted(cells, np.arange(ncell), side="right")
        count = (self._end - self._start).astype(np.float64)
        filled = count > 0
        total = np.zeros(ncell)
        lo = np.full(ncell, np.nan)
        hi = np.full(ncell, np.nan)
        sq = np.zeros(ncell)
        if len(zs):
            starts = self._start[filled]
            total[filled] = np.add.reduceat(zs, starts)
            lo[filled] = np.minimum.reduceat(zs, starts)
            hi[filled] = np.maximum.reduceat(zs, starts)
            sq[filled] = np.add.reduceat(zs * zs, starts)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(filled, total / count, np.nan)
            var = np.where(filled, sq / count - mean * mean, np.nan)
        shape = (ny, nx)
        self.count = count.reshape(shape)
        self.sum = np.where(filled, total, np.nan).reshape(shape)
        self.mean = mean.reshape(shape)
        self.min = lo.reshape(shape)
        self.max = hi.reshape(shape)
        self.std = np.sqrt(np.clip(var, 0, None)).reshape(shape)
        self.samples = int(len(zs))

    @classmethod
    def from_table(cls, table: Table, x: str, y: str, z: str, x_bins: Bins, y_bins: Bins,
                   mask: BoolArray | None = None) -> "TableHistogram":
        return cls(table[x], table[y], table[z], x_bins, y_bins, mask, table.t)

    def stat(self, name: str = "mean", min_hits: int = 1) -> FloatArray:
        """One statistic as a grid. Cells with fewer than ``min_hits`` samples are NaN."""
        if name not in STATS:
            raise ValueError(f"unknown statistic {name!r}; choose from {', '.join(STATS)}")
        grid = getattr(self, name)
        if name == "count":
            return np.where(self.count >= min_hits, self.count, 0.0)
        return np.where(self.count >= max(1, min_hits), grid, np.nan)

    def rows(self, ix: int, iy: int) -> np.ndarray:
        """Source row numbers of the samples in cell (ix, iy), in time order."""
        cell = iy * len(self.x_bins) + ix
        return self._rows[self._start[cell]:self._end[cell]]

    def times(self, ix: int, iy: int) -> FloatArray:
        """Timestamps of the samples in a cell (needs ``t``)."""
        if self.t is None:
            raise ValueError("histogram was built without a time column")
        return self.t[self.rows(ix, iy)]

    def delta(self, other: "TableHistogram", stat: str = "mean", min_hits: int = 1) -> FloatArray:
        """This minus ``other`` per cell (log A minus log B, or lambda minus target).
        NaN where either side has too few samples."""
        if (self.x_bins, self.y_bins) != (other.x_bins, other.y_bins):
            raise ValueError("histograms must use the same bins to be compared")
        return self.stat(stat, min_hits) - other.stat(stat, min_hits)

    def color_range(self, stat: str = "mean", min_hits: int = 1) -> tuple[float, float]:
        """(low, high) of the visible cells, for scaling the colour map."""
        grid = self.stat(stat, min_hits)
        if stat == "count":
            return 0.0, float(grid.max()) if grid.size else 0.0
        if np.all(np.isnan(grid)):
            return 0.0, 0.0
        return float(np.nanmin(grid)), float(np.nanmax(grid))
