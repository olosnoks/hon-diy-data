"""Scatter plot data: X against Y, optional colour channel, fit line, brushing."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.model import BoolArray, FloatArray, Table
from .lod import decimate_points


@dataclass
class Fit:
    """A polynomial y = c[0] + c[1] x + c[2] x^2 ... through the points."""

    coeffs: tuple[float, ...]
    r2: float
    n: int

    def __call__(self, x: FloatArray) -> FloatArray:
        return np.asarray(np.polynomial.polynomial.polyval(x, self.coeffs), dtype=np.float64)

    def describe(self, decimals: int = 4) -> str:
        terms = []
        for power, c in enumerate(self.coeffs):
            term = f"{c:.{decimals}g}" + ("" if power == 0 else " x" if power == 1 else f" x^{power}")
            terms.append(term)
        return "y = " + " + ".join(terms).replace("+ -", "- ") + f"   (R² = {self.r2:.3f}, n = {self.n})"


class ScatterModel:
    """Points for a scatter plot. Rows with NaN in X or Y, or rejected by
    ``mask``, are left out. ``rows`` maps each point back to its table row, so a
    selection can be highlighted in the time view."""

    def __init__(self, table: Table, x: str, y: str, color: str | None = None,
                 mask: BoolArray | None = None):
        self.x_name, self.y_name, self.color_name = x, y, color
        xs, ys = table[x], table[y]
        ok = ~np.isnan(xs) & ~np.isnan(ys)
        if mask is not None:
            ok &= mask
        self.rows = np.flatnonzero(ok)
        self.x: FloatArray = xs[self.rows]
        self.y: FloatArray = ys[self.rows]
        self.t: FloatArray = table.t[self.rows]
        self.color: FloatArray | None = table[color][self.rows] if color else None

    def __len__(self) -> int:
        return len(self.rows)

    def points(self, limit: int = 20000) -> tuple[FloatArray, FloatArray, FloatArray | None, np.ndarray]:
        """x, y, colour and point indices, thinned evenly to at most ``limit`` points."""
        pick = decimate_points(len(self), limit)
        color = self.color[pick] if self.color is not None else None
        return self.x[pick], self.y[pick], color, pick

    def fit(self, degree: int = 1) -> Fit | None:
        """Least-squares polynomial. None if there are too few distinct points."""
        if len(self) <= degree or np.ptp(self.x) == 0:
            return None
        coeffs = np.polynomial.polynomial.polyfit(self.x, self.y, degree)
        resid = self.y - np.polynomial.polynomial.polyval(self.x, coeffs)
        spread = float(np.sum((self.y - self.y.mean()) ** 2))
        r2 = 1.0 - float(np.sum(resid ** 2)) / spread if spread > 0 else 1.0
        return Fit(tuple(float(c) for c in coeffs), r2, len(self))

    def brush(self, x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
        """Point indices inside a rectangle dragged on the plot."""
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)
        return np.flatnonzero((self.x >= lo_x) & (self.x <= hi_x) & (self.y >= lo_y) & (self.y <= hi_y))

    def brush_times(self, x0: float, x1: float, y0: float, y1: float) -> FloatArray:
        """Timestamps of the brushed points, for highlighting in the time view."""
        return self.t[self.brush(x0, x1, y0, y1)]

    def brush_spans(self, x0: float, x1: float, y0: float, y1: float,
                    gap: float = 1.0) -> list[tuple[float, float]]:
        """The brushed points as time spans; points closer than ``gap`` seconds join up."""
        times = self.brush_times(x0, x1, y0, y1)
        if len(times) == 0:
            return []
        breaks = np.flatnonzero(np.diff(times) > gap)
        starts = np.concatenate([[0], breaks + 1])
        ends = np.concatenate([breaks, [len(times) - 1]])
        return [(float(times[a]), float(times[b])) for a, b in zip(starts, ends)]
