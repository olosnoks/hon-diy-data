"""Cursor readouts: the value at a cursor and statistics between two cursors."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.model import Series

NAN = float("nan")


@dataclass
class SpanStats:
    """One channel between cursor A and cursor B."""

    name: str
    units: str
    a: float                # value at cursor A
    b: float                # value at cursor B
    delta: float            # b - a
    min: float
    max: float
    mean: float             # time-weighted, so uneven sampling does not skew it
    n: int                  # samples between the cursors
    rate: float             # delta per second


def value_at(series: Series, t: float, how: str = "hold") -> float:
    """The channel's value at time t: the last sample at or before t ("hold"),
    the nearest sample ("nearest") or a straight line between neighbours ("linear")."""
    n = len(series)
    if n == 0:
        return NAN
    i = int(np.searchsorted(series.t, t, side="right")) - 1
    if how == "nearest":
        j = min(max(i, 0), n - 1)
        if j + 1 < n and abs(series.t[j + 1] - t) < abs(series.t[j] - t):
            j += 1
        return float(series.v[j])
    if i < 0:
        return NAN
    if how == "linear" and i + 1 < n:
        t0, t1 = series.t[i], series.t[i + 1]
        if t1 > t0:
            return float(series.v[i] + (series.v[i + 1] - series.v[i]) * (t - t0) / (t1 - t0))
    return float(series.v[i])


def span_stats(series: Series, ta: float, tb: float, full: bool = True) -> SpanStats:
    """Statistics of one channel between two cursor times (in either order).

    ``full=False`` gives only the two values, their difference and the rate
    (min, max and mean are NaN, n is 0): two lookups, whatever lies between.
    A readout that follows a dragged cursor over an hour of 1 kHz data wants
    that; a table of statistics asks for the rest once the cursor rests."""
    lo, hi = min(ta, tb), max(ta, tb)
    a, b = value_at(series, ta), value_at(series, tb)
    if not full:
        span = tb - ta
        return SpanStats(series.name, series.units, a, b, b - a, NAN, NAN, NAN, 0,
                         (b - a) / span if span else NAN)
    i0 = int(np.searchsorted(series.t, lo, side="left"))
    i1 = int(np.searchsorted(series.t, hi, side="right"))
    v = series.v[i0:i1]
    t = series.t[i0:i1]
    ok = ~np.isnan(v)
    n = int(ok.sum())
    if n == 0:
        vmin = vmax = mean = NAN
    else:
        vmin, vmax = float(v[ok].min()), float(v[ok].max())
        mean = float(v[ok].mean())
        if n > 1:
            # each sample counts for the time until the next one (zero-order hold)
            dt = np.diff(np.append(t, hi))
            weight = np.where(ok, dt, 0.0)
            if weight.sum() > 0:
                mean = float(np.sum(np.where(ok, v, 0.0) * weight) / weight.sum())
    delta = b - a
    span = tb - ta
    return SpanStats(series.name, series.units, a, b, delta, vmin, vmax, mean, n,
                     delta / span if span else NAN)


def step(series: Series, t: float, direction: int) -> float:
    """Time of the next (+1) or previous (-1) sample of this channel from t."""
    n = len(series)
    if n == 0:
        return t
    if direction > 0:
        i = int(np.searchsorted(series.t, t, side="right"))
    else:
        i = int(np.searchsorted(series.t, t, side="left")) - 1
    return float(series.t[min(max(i, 0), n - 1)])
