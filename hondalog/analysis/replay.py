"""The replay view's brain, without any Qt: what to draw and what the cursors read.

The UI asks for an envelope per plotted channel for the visible time range
and its pixel width, moves two cursors, and shows the readout rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..data.model import Event, Series
from ..data.reader import Log
from . import cursors
from .compare import Comparison
from .lod import Envelope, LodPyramid, envelope

PYRAMID_FROM = 200_000          # series longer than this get a pyramid


@dataclass
class Readout:
    name: str
    units: str
    value: float                # at cursor A
    stats: cursors.SpanStats | None     # between A and B, when B is set


class ReplaySession:
    def __init__(self, log: Log):
        self.log = log
        self.view: tuple[float, float] = (log.t0, max(log.t1, log.t0 + 1e-3))
        self.cursor_a: float = log.t0
        self.cursor_b: float | None = None
        self.overlay: Comparison | None = None
        #: events the viewer worked out itself (the order-of-loss rows of a
        #: stall): listed and jumped to together with the log's own
        self.bookmarks: list[Event] = []
        self._pyramids: dict[tuple[int, str], LodPyramid] = {}

    # -- view --

    def set_view(self, t0: float, t1: float) -> None:
        if t1 > t0:
            self.view = (t0, t1)

    def show_all(self) -> None:
        self.view = (self.log.t0, max(self.log.t1, self.log.t0 + 1e-3))

    def zoom(self, factor: float, around: float | None = None) -> None:
        """factor < 1 zooms in. The time ``around`` stays where it is on screen."""
        t0, t1 = self.view
        around = (t0 + t1) / 2 if around is None else around
        self.set_view(around - (around - t0) * factor, around + (t1 - around) * factor)

    def pan(self, fraction: float) -> None:
        t0, t1 = self.view
        shift = (t1 - t0) * fraction
        self.view = (t0 + shift, t1 + shift)

    # -- drawing --

    def _envelope(self, series: Series, columns: int, key: int) -> Envelope:
        if len(series) >= PYRAMID_FROM:
            pyramid = self._pyramids.get((key, series.name))
            if pyramid is None:
                pyramid = self._pyramids[(key, series.name)] = LodPyramid(series.t, series.v)
            return pyramid.envelope(self.view[0], self.view[1], columns)
        return envelope(series.t, series.v, self.view[0], self.view[1], columns)

    def trace(self, name: str, columns: int = 1000) -> Envelope:
        """What to draw for one channel in the current view."""
        return self._envelope(self.log.series(name), columns, 0)

    def overlay_trace(self, name: str, columns: int = 1000) -> Envelope | None:
        """The same channel from the comparison log, shifted onto this time axis."""
        if self.overlay is None or name not in self.overlay.b.channels:
            return None
        return self._envelope(self.overlay.shifted(name), columns, 1)

    def set_overlay(self, other: Log | None, align: str = "start", **options: Any) -> float:
        """Lay a second log over this one. Returns the time offset that was applied."""
        self._pyramids = {k: v for k, v in self._pyramids.items() if k[0] == 0}
        if other is None:
            self.overlay = None
            return 0.0
        self.overlay = Comparison(self.log, other)
        try:
            return self.overlay.align(align, **options)
        except ValueError:
            return self.overlay.align("start")

    def align_overlay(self, align: str = "start", **options: Any) -> float:
        """Line the overlay up on another moment. Returns the offset; raises
        ValueError (and changes nothing) if a log has no such moment."""
        if self.overlay is None:
            return 0.0
        offset = self.overlay.align(align, **options)
        self._pyramids = {k: v for k, v in self._pyramids.items() if k[0] == 0}
        return offset

    def shift_overlay(self, seconds: float) -> float:
        """Slide the overlay by hand. Returns the new offset."""
        if self.overlay is None:
            return 0.0
        self.overlay.offset += seconds
        self._pyramids = {k: v for k, v in self._pyramids.items() if k[0] == 0}
        return self.overlay.offset

    # -- cursors --

    def set_cursor(self, t: float, which: str = "a") -> None:
        t = float(np.clip(t, self.log.t0, self.log.t1))
        if which == "a":
            self.cursor_a = t
        else:
            self.cursor_b = t

    def clear_cursor_b(self) -> None:
        self.cursor_b = None

    def step(self, direction: int, channel: str | None = None, which: str = "a") -> float:
        """Move a cursor one sample. Without ``channel`` it steps to the nearest
        sample of any stream, so no read is skipped."""
        now = self.cursor_a if which == "a" or self.cursor_b is None else self.cursor_b
        if channel is not None:
            target = cursors.step(self.log.series(channel), now, direction)
        else:
            best: float | None = None
            for stream in self.log.streams.values():
                if not stream.rows:
                    continue
                t = stream.t
                i = int(np.searchsorted(t, now, "right")) if direction > 0 else int(np.searchsorted(t, now, "left")) - 1
                if 0 <= i < len(t):
                    cand = float(t[i])
                    if best is None or (cand < best if direction > 0 else cand > best):
                        best = cand
            target = now if best is None else best
        self.set_cursor(target, which)
        return target

    def readouts(self, names: Sequence[str] | None = None, full: bool = True) -> list[Readout]:
        """One row per channel: value at cursor A, and A-to-B statistics if B
        is set (``full=False``: only the values at A and B and their
        difference, see ``cursors.span_stats``)."""
        rows = []
        for name in names if names is not None else self.log.channels:
            series = self.log.series(name)
            stats = (cursors.span_stats(series, self.cursor_a, self.cursor_b, full)
                     if self.cursor_b is not None else None)
            rows.append(Readout(name, series.units, cursors.value_at(series, self.cursor_a), stats))
        return rows

    @property
    def cursor_delta(self) -> float | None:
        return None if self.cursor_b is None else self.cursor_b - self.cursor_a

    # -- events --

    def add_event(self, t: float, kind: str, text: str = "") -> Event:
        """Add a bookmark: a moment that is not in the log but is worth
        jumping to. It appears in ``event_list`` and ``jump_event``."""
        event = Event(float(t), kind, text)
        self.bookmarks.append(event)
        return event

    def clear_events(self, kind: str | None = None) -> None:
        """Forget the bookmarks (of one kind, or all)."""
        self.bookmarks = [e for e in self.bookmarks if kind is not None and e.kind != kind]

    def event_list(self, *kinds: str) -> list[Event]:
        """Markers, triggers and alarms in time order, for the jump list,
        with the bookmarks among them. ``kinds`` narrows both."""
        events = self.log.events_of(*(kinds or ("marker", "trigger", "alarm")))
        events = events + [e for e in self.bookmarks if not kinds or e.kind in kinds]
        return sorted(events, key=lambda e: e.t)

    def jump_event(self, direction: int, *kinds: str) -> float | None:
        """Move cursor A to the next or previous event. None if there is none."""
        times = [e.t for e in self.event_list(*kinds)]
        if direction > 0:
            later = [t for t in times if t > self.cursor_a + 1e-6]
            target = later[0] if later else None
        else:
            earlier = [t for t in times if t < self.cursor_a - 1e-6]
            target = earlier[-1] if earlier else None
        if target is not None:
            self.set_cursor(target)
            span = self.view[1] - self.view[0]
            if not self.view[0] <= target <= self.view[1]:
                self.view = (target - span / 2, target + span / 2)
        return target
