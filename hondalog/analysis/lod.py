"""Level of detail for plotting: a min/max envelope per pixel column.

A plot that is 1000 pixels wide cannot show more than 1000 columns. Drawing
every sample of a long log wastes time and, worse, a naive "every Nth sample"
hides spikes. The envelope keeps the lowest and highest value that fall into
each pixel column, so a one-sample dropout is still drawn.

``envelope`` is enough for K-line logs (tens of thousands of rows). For the
black box's kHz streams build an ``LodPyramid`` once; it answers any zoom
level from pre-reduced levels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.model import FloatArray


@dataclass
class Envelope:
    """``t`` is the centre of each non-empty pixel column."""

    t: FloatArray
    lo: FloatArray
    hi: FloatArray
    reduced: bool                   # False: these are the raw samples (lo == hi)

    def polyline(self) -> tuple[FloatArray, FloatArray]:
        """x, y for one zig-zag line that covers the envelope (lo, hi, lo, hi...)."""
        if not self.reduced:
            return self.t, self.lo
        x = np.repeat(self.t, 2)
        y = np.empty(len(x))
        y[0::2] = self.lo
        y[1::2] = self.hi
        return x, y


def _reduce(lo: FloatArray, hi: FloatArray, starts: np.ndarray, total: int) -> tuple[FloatArray, FloatArray, np.ndarray]:
    """Min of ``lo`` and max of ``hi`` over the groups that begin at ``starts``.
    Returns only the non-empty groups and their indices."""
    ends = np.append(starts[1:], total)
    keep = ends > starts
    if not keep.any():
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.intp)
    first = starts[keep]
    return np.fmin.reduceat(lo, first), np.fmax.reduceat(hi, first), np.flatnonzero(keep)


def envelope(t: FloatArray, v: FloatArray, t0: float | None = None, t1: float | None = None,
             columns: int = 1000) -> Envelope:
    """Min/max of ``v`` in each of ``columns`` equal time slices of [t0, t1].

    If the range holds no more than two samples per column the raw samples
    come back unchanged. NaN samples are ignored; a column with only NaN
    stays NaN, which the plot draws as a gap.
    """
    if len(t) == 0:
        empty = np.zeros(0)
        return Envelope(empty, empty, empty, False)
    t0 = float(t[0]) if t0 is None else t0
    t1 = float(t[-1]) if t1 is None else t1
    i0 = int(np.searchsorted(t, t0, side="left"))
    i1 = int(np.searchsorted(t, t1, side="right"))
    columns = max(1, int(columns))
    if i1 - i0 <= 2 * columns or t1 <= t0:
        # one sample of context on each side so lines reach the plot edges
        a, b = max(0, i0 - 1), min(len(t), i1 + 1)
        return Envelope(t[a:b], v[a:b], v[a:b], False)
    edges = np.linspace(t0, t1, columns + 1)
    starts = np.searchsorted(t[i0:i1], edges[:-1], side="left")
    lo, hi, kept = _reduce(v[i0:i1], v[i0:i1], starts, i1 - i0)
    centres = (edges[:-1] + edges[1:]) / 2
    return Envelope(centres[kept], lo, hi, True)


class LodPyramid:
    """Pre-reduced min/max levels of one long series.

    Level k holds the min and max of blocks of ``factor**k`` samples. A query
    picks the coarsest level that still has at least ``oversample`` blocks per
    pixel column, so the work per query is bounded by the plot width and not
    by the length of the log.
    """

    def __init__(self, t: FloatArray, v: FloatArray, factor: int = 8, min_len: int = 4096):
        self.t = np.asarray(t, dtype=np.float64)
        self.v = np.asarray(v, dtype=np.float64)
        self.factor = factor
        self.levels: list[tuple[FloatArray, FloatArray, FloatArray]] = []   # (block start t, lo, hi)
        lo = hi = self.v
        tt = self.t
        while len(tt) > min_len:
            n = len(tt) // factor * factor
            head_lo = np.fmin.reduce(lo[:n].reshape(-1, factor), axis=1)
            head_hi = np.fmax.reduce(hi[:n].reshape(-1, factor), axis=1)
            starts = tt[:n:factor]
            if n < len(tt):                 # the short block at the end
                head_lo = np.append(head_lo, np.fmin.reduce(lo[n:]))
                head_hi = np.append(head_hi, np.fmax.reduce(hi[n:]))
                starts = np.append(starts, tt[n])
            lo, hi, tt = head_lo, head_hi, starts
            self.levels.append((tt, lo, hi))

    def envelope(self, t0: float | None = None, t1: float | None = None, columns: int = 1000,
                 oversample: int = 4) -> Envelope:
        if len(self.t) == 0:
            return envelope(self.t, self.v, t0, t1, columns)
        t0 = float(self.t[0]) if t0 is None else t0
        t1 = float(self.t[-1]) if t1 is None else t1
        columns = max(1, int(columns))
        need = columns * oversample
        chosen = None
        for tt, lo, hi in reversed(self.levels):
            if np.searchsorted(tt, t1, side="right") - np.searchsorted(tt, t0, side="left") >= need:
                chosen = (tt, lo, hi)
                break
        if chosen is None:
            return envelope(self.t, self.v, t0, t1, columns)
        tt, lo, hi = chosen
        # block b covers [tt[b], tt[b+1]). A column gets every block that starts
        # inside it plus the block that straddles its left edge, so a block
        # split by an edge counts in both columns: extremes are never lost,
        # at worst a column borrows a few samples from its neighbour.
        edges = np.linspace(t0, t1, columns + 1)
        first = np.searchsorted(tt, edges[:-1], side="left")       # first block starting in column
        stop = np.searchsorted(tt, edges[1:], side="left")         # one past the last one
        stop[-1] = np.searchsorted(tt, t1, side="right")
        out_lo = np.full(columns, np.nan)
        out_hi = np.full(columns, np.nan)
        has = stop > first
        if has.any():
            idx = first[has]
            # reduceat over consecutive non-empty ranges; trailing blocks past the
            # last range are cut off by appending its stop as a sentinel
            tail = int(stop[has][-1])
            seg_lo = np.fmin.reduceat(lo[:tail], idx)
            seg_hi = np.fmax.reduceat(hi[:tail], idx)
            out_lo[has], out_hi[has] = seg_lo, seg_hi
        straddle = first - 1                                       # block holding the left edge
        ok = (straddle >= 0) & (straddle < len(tt))
        ok &= np.where(ok, tt[np.clip(straddle, 0, len(tt) - 1)] < edges[:-1], False)
        # only if that block still has samples at or after the edge: true unless it is the last
        # block and the series ended before the edge
        ok &= (straddle < len(tt) - 1) | (self.t[-1] >= edges[:-1])
        s = straddle[ok]
        out_lo[ok] = np.fmin(out_lo[ok], lo[s])
        out_hi[ok] = np.fmax(out_hi[ok], hi[s])
        keep = has | ok
        centres = (edges[:-1] + edges[1:]) / 2
        return Envelope(centres[keep], out_lo[keep], out_hi[keep], True)


def decimate_points(n: int, limit: int) -> np.ndarray:
    """Indices of at most ``limit`` evenly spread points out of ``n`` (for scatter plots)."""
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).astype(np.intp))
