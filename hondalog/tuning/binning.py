"""Where a pair of channel values (say RPM and MAP) lands on a table's axes.

Shared by the live cursor, the auto-tune model and the knock map. All of it
is numpy on arrays, so a one-hour log bins in milliseconds.
"""

from __future__ import annotations

import numpy as np


class AxisError(Exception):
    pass


def check_axis(axis) -> np.ndarray:
    """Axis breakpoints as floats; must be strictly rising or strictly falling."""
    axis = np.asarray(axis, dtype=float).ravel()
    if axis.size == 0 or not np.all(np.isfinite(axis)):
        raise AxisError("axis is empty or has non-numeric breakpoints")
    if axis.size > 1:
        d = np.diff(axis)
        if not (np.all(d > 0) or np.all(d < 0)):
            raise AxisError("axis breakpoints must rise or fall steadily")
    return axis


def fractional_index(axis: np.ndarray, values) -> tuple[np.ndarray, np.ndarray]:
    """Position of each value along the axis as a fractional index 0..n-1.

    Returns (index, clamped). Values outside the axis are held at the end and
    flagged; NaN stays NaN.
    """
    axis = check_axis(axis)
    values = np.asarray(values, dtype=float)
    n = axis.size
    if n == 1:
        return np.where(np.isnan(values), np.nan, 0.0), values != axis[0]
    if axis[-1] > axis[0]:
        idx = np.interp(values, axis, np.arange(n, dtype=float))
        lo, hi = axis[0], axis[-1]
    else:
        idx = np.interp(values, axis[::-1], np.arange(n - 1, -1, -1, dtype=float))
        lo, hi = axis[-1], axis[0]
    idx = np.where(np.isnan(values), np.nan, idx)
    return idx, (values < lo) | (values > hi)


def bilinear(x_axis, y_axis, x, y):
    """Four surrounding cells and their weights for every sample.

    Returns (r0, c0, wr, wc, valid): the top-left cell indices and the
    fractions toward row r0+1 and column c0+1. The four weights are
    (1-wr)(1-wc), (1-wr)wc, wr(1-wc), wr*wc. ``valid`` is False for NaN input.
    """
    col, _ = fractional_index(np.asarray(x_axis), x)
    row, _ = fractional_index(np.asarray(y_axis), y)
    valid = np.isfinite(col) & np.isfinite(row)
    col = np.where(valid, col, 0.0)
    row = np.where(valid, row, 0.0)
    nx, ny = np.asarray(x_axis).size, np.asarray(y_axis).size
    c0 = np.clip(np.floor(col), 0, max(nx - 2, 0)).astype(int)
    r0 = np.clip(np.floor(row), 0, max(ny - 2, 0)).astype(int)
    return r0, c0, row - r0, col - c0, valid


def nearest_cell(x_axis, y_axis, x, y):
    """(row, col, valid) of the closest cell for every sample."""
    col, _ = fractional_index(np.asarray(x_axis), x)
    row, _ = fractional_index(np.asarray(y_axis), y)
    valid = np.isfinite(col) & np.isfinite(row)
    r = np.where(valid, np.floor(row + 0.5), 0).astype(int)
    c = np.where(valid, np.floor(col + 0.5), 0).astype(int)
    return r, c, valid


def spread(shape: tuple[int, int], r0, c0, wr, wc, values=None, keep=None) -> np.ndarray:
    """Sum ``values`` (default 1) into a table, each sample shared between its
    four cells by the bilinear weights."""
    rows, cols = shape
    out = np.zeros(shape, dtype=float)
    keep = np.ones(np.shape(r0), dtype=bool) if keep is None else np.asarray(keep, bool)
    v = np.ones(np.shape(r0), dtype=float) if values is None else np.asarray(values, float)
    r1 = np.minimum(r0 + 1, rows - 1)
    c1 = np.minimum(c0 + 1, cols - 1)
    for rr, cc, w in ((r0, c0, (1 - wr) * (1 - wc)), (r0, c1, (1 - wr) * wc),
                      (r1, c0, wr * (1 - wc)), (r1, c1, wr * wc)):
        np.add.at(out, (rr[keep], cc[keep]), (w * v)[keep])
    return out


def cell_stat(x, y, z, x_axis, y_axis, stat: str = "mean", keep=None) -> np.ndarray:
    """A table of one statistic of ``z`` per nearest cell.

    stat: count, sum, mean, min, max. Cells with no samples are NaN (0 for
    count and sum).
    """
    r, c, valid = nearest_cell(x_axis, y_axis, x, y)
    z = np.asarray(z, dtype=float)
    keep_mask = valid & np.isfinite(z)
    if keep is not None:
        keep_mask &= np.asarray(keep, dtype=bool)
    shape = (np.asarray(y_axis).size, np.asarray(x_axis).size)
    r, c, z = r[keep_mask], c[keep_mask], z[keep_mask]
    count = np.zeros(shape)
    np.add.at(count, (r, c), 1.0)
    if stat == "count":
        return count
    total = np.zeros(shape)
    np.add.at(total, (r, c), z)
    if stat == "sum":
        return total
    if stat == "mean":
        with np.errstate(all="ignore"):
            return np.where(count > 0, total / count, np.nan)
    if stat in ("min", "max"):
        out = np.full(shape, np.inf if stat == "min" else -np.inf)
        (np.minimum if stat == "min" else np.maximum).at(out, (r, c), z)
        return np.where(count > 0, out, np.nan)
    raise ValueError(f"unknown statistic {stat!r}")


def knock_map(x, y, knock, x_axis, y_axis, mode: str = "counter", keep=None) -> np.ndarray:
    """Knock events per cell, for laying over the ignition table.

    mode "counter": ``knock`` is a running count; each increase is an event
                    (a drop is treated as the counter restarting).
         "flag":    each sample where ``knock`` is non-zero after a zero.
         "level":   every sample where ``knock`` is non-zero counts.
    """
    knock = np.nan_to_num(np.asarray(knock, dtype=float))
    if mode == "counter":
        events = np.concatenate(([0.0], np.clip(np.diff(knock), 0.0, None)))
    elif mode == "flag":
        on = knock != 0
        events = (on & ~np.concatenate(([False], on[:-1]))).astype(float)
    elif mode == "level":
        events = (knock != 0).astype(float)
    else:
        raise ValueError(f"unknown knock mode {mode!r}")
    return cell_stat(x, y, events, x_axis, y_axis, "sum", keep)
