"""Comparing two logs: line them up in time, overlay, difference, histogram delta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..data.model import FloatArray, Series
from ..data.reader import Log, align
from .filters import FilterSet
from .histogram import Bins, TableHistogram

NAN = float("nan")


def first_crossing(series: Series, level: float, rising: bool = True, after: float | None = None) -> float | None:
    """Time at which the channel first crosses ``level`` (interpolated), or None."""
    t, v = series.t, series.v
    if after is not None:
        keep = t >= after
        t, v = t[keep], v[keep]
    if len(t) < 2:
        return None
    above = v >= level
    change = (~above[:-1] & above[1:]) if rising else (above[:-1] & ~above[1:])
    hits = np.flatnonzero(change)
    if len(hits) == 0:
        return None
    i = int(hits[0])
    span = v[i + 1] - v[i]
    frac = (level - v[i]) / span if span else 0.0
    return float(t[i] + (t[i + 1] - t[i]) * frac)


def event_time(log: Log, how: str = "start", *, channel: str = "rpm", level: float = 0.0,
               rising: bool = True, text: str = "") -> float | None:
    """A reference moment in a log.

    how = "start"     first sample
          "marker"    first marker (or the first whose text contains ``text``)
          "trigger"   first trigger event (a stall capture's stall moment)
          "crossing"  first time ``channel`` crosses ``level``
    """
    if how == "start":
        return log.t0
    if how in ("marker", "trigger"):
        kinds = ("marker",) if how == "marker" else ("trigger",)
        for event in log.events_of(*kinds):
            if text.lower() in event.text.lower():
                return event.t
        if how == "trigger" and "stall_time_s" in log.meta:
            return float(log.meta["stall_time_s"])
        return None
    if how == "crossing":
        return first_crossing(log.series(channel), level, rising)
    raise ValueError(f"unknown alignment {how!r}")


@dataclass
class Overlay:
    """Channel values of both logs on log A's time axis."""

    name: str
    t: FloatArray
    a: FloatArray
    b: FloatArray           # log B, shifted and resampled onto t

    @property
    def diff(self) -> FloatArray:
        return self.a - self.b

    def summary(self) -> dict[str, float]:
        d = self.diff
        d = d[~np.isnan(d)]
        if len(d) == 0:
            return {"n": 0, "mean": NAN, "rms": NAN, "max_abs": NAN}
        return {"n": float(len(d)), "mean": float(d.mean()),
                "rms": float(np.sqrt(np.mean(d * d))), "max_abs": float(np.abs(d).max())}


class Comparison:
    """Log B laid over log A. ``offset`` is added to B's times."""

    def __init__(self, a: Log, b: Log, offset: float = 0.0):
        self.a, self.b = a, b
        self.offset = offset

    def align(self, how: str = "start", **options: Any) -> float:
        """Set ``offset`` so the chosen event happens at the same time in both
        logs. Returns the offset; raises ValueError if a log lacks the event."""
        ta, tb = event_time(self.a, how, **options), event_time(self.b, how, **options)
        if ta is None or tb is None:
            which = ("neither log has" if ta is None and tb is None
                     else "the first log has" if ta is None else "the second log has")
            raise ValueError(f"{which} no '{how}' event to align on")
        self.offset = ta - tb
        return self.offset

    def common_channels(self) -> list[str]:
        theirs = set(self.b.channels)
        return [n for n in self.a.channels if n in theirs]

    def shifted(self, name: str) -> Series:
        """Log B's channel with the offset applied, for drawing on A's axis."""
        s = self.b.series(name)
        return Series(name, s.t + self.offset, s.v, s.units)

    def overlay(self, name: str, how: str = "linear") -> Overlay:
        a = self.a.series(name)
        return Overlay(name, a.t, a.v, align(self.shifted(name), a.t, how))

    def histogram_delta(self, x: str, y: str, z: str, x_bins: Bins, y_bins: Bins,
                        stat: str = "mean", min_hits: int = 1,
                        filters: FilterSet | None = None) -> tuple[FloatArray, TableHistogram, TableHistogram]:
        """Per-cell A minus B, plus both histograms. ``filters`` is a FilterSet."""
        hists = []
        for log in (self.a, self.b):
            names = {x, y, z} | (filters.channels() if filters is not None else set())
            table = log.table(sorted(names))
            mask = filters.mask(table) if filters is not None else None
            hists.append(TableHistogram.from_table(table, x, y, z, x_bins, y_bins, mask))
        return hists[0].delta(hists[1], stat, min_hits), hists[0], hists[1]
