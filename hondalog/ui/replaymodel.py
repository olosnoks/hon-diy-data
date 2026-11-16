"""What the Replay screen asks of ``hondalog.analysis.ReplaySession``, in the
screen's own terms.

A ``LogView`` names channels by the UI's ids and counts time from the stall
trigger (or the start of the file); the session works on the log's channel
names and session seconds. ``ReplayModel`` is the thin layer between: every
time that goes in or comes out is view time, every channel a view id, every
value in the view's unit (the black box tach is pulses per second on the
screen and rpm in the log).

    model = ReplayModel(view)
    model.set_window(-30, 10); model.zoom(0.5, around=-2.1); model.pan(0.25)
    x, y, reduced = model.trace("rpm", columns=814)      # min/max envelope of the window
    model.set_cursor(-2.1); model.set_cursor(0.0, "b")
    model.readouts(["rpm", "fpv"])["fpv"].stats.delta     # A to B
    model.jump(+1)                                         # next marker, trigger, alarm, loss row
    model.set_overlay(other_view, "trigger")               # a second drive laid over this one

A view that did not come from the drive database (the demo capture, a .bbx
file) has no session of its own: one is made from its arrays, so the screen
has a single code path.

No Qt in here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analysis import ReplaySession
from ..analysis.cursors import SpanStats
from ..data import Column, Event, Log, Stream, StreamInfo
from . import adapters as ad

MIN_WINDOW_S = 0.25
#: how a second drive can be laid over the first: (key, menu text)
ALIGNMENTS = (("start", "the first sample"), ("marker", "the first marker"),
              ("trigger", "the stall (trigger)"), ("crossing", "RPM rising through 400"))
#: the same in one or two words, for a status line
ALIGN_SHORT = {"start": "start", "marker": "marker", "trigger": "stall", "crossing": "rpm 400"}
_ALIGN_OPTIONS = {"crossing": {"channel": "rpm", "level": 400.0, "rising": True}}


@dataclass
class LaneReadout:
    value: float                    # at cursor A, in the view's unit
    stats: SpanStats | None         # A to B when B is set: a, b, delta, min, max, mean, rate


def session_from_view(view: ad.LogView) -> ReplaySession:
    """A replay session on a view's own arrays (one stream per channel, each
    at its own timestamps; markers and the trigger as events)."""
    streams = []
    for cid, t in view.t.items():
        spec = view.specs.get(cid)
        info = StreamInfo(cid, (Column(cid, "f8", spec.units if spec else ""),),
                          source="blackbox" if spec is not None and spec.source == "BB" else "ecu")
        data = np.zeros(len(t), info.dtype)
        data["t"], data[cid] = t, view.data[cid]
        streams.append(Stream(info, data=data))
    events = [Event(float(t), "marker", text) for t, text in view.markers]
    if view.trigger_t is not None:
        events.append(Event(float(view.trigger_t), "trigger", "STALL"))
    return ReplaySession(Log(dict(view.meta), streams, events))


class ReplayModel:
    def __init__(self, view: ad.LogView):
        self.view = view
        session = view.replay
        if session is None:
            session, self.origin, self.names = session_from_view(view), 0.0, {}
        else:
            self.origin, self.names = float(view.origin), dict(view.names)
        self.session: ReplaySession = session       # type: ignore[assignment]
        lo, hi = view.span()
        log = self.session.log
        if log.rows:                                # cursors are clipped to the log: so is the span
            lo, hi = max(lo, log.t0 - self.origin), min(hi, log.t1 - self.origin)
        if not hi > lo:
            lo, hi = view.span()
        self.span: tuple[float, float] = (lo, hi)
        self.overlay_name = ""
        self.align = ""
        self.set_window(lo, hi)
        self.session.set_cursor(lo + self.origin)

    # ---- names ----

    def _name(self, cid: str) -> tuple[str, float]:
        return self.names.get(cid, (cid, 1.0))

    def has(self, cid: str) -> bool:
        return self._name(cid)[0] in self.session.log.channels

    # ---- window ----

    @property
    def window(self) -> tuple[float, float]:
        t0, t1 = self.session.view
        return t0 - self.origin, t1 - self.origin

    def set_window(self, t0: float, t1: float) -> tuple[float, float]:
        """Show [t0, t1], kept inside the log and no narrower than MIN_WINDOW_S."""
        lo, hi = self.span
        width = min(max(t1 - t0, MIN_WINDOW_S), hi - lo)
        t0 = min(max(lo, t0), hi - width)
        self.session.set_view(t0 + self.origin, t0 + width + self.origin)
        return self.window

    def zoom(self, factor: float, around: float | None = None) -> tuple[float, float]:
        """factor < 1 zooms in; the time ``around`` stays where it is on screen."""
        t0, t1 = self.window
        lo, hi = self.span
        # stop at the narrowest and the widest window with ``around`` still in its place
        factor = min(max(factor, MIN_WINDOW_S / (t1 - t0)), (hi - lo) / (t1 - t0))
        self.session.zoom(factor, None if around is None else around + self.origin)
        return self.set_window(*self.window)

    def pan(self, fraction: float) -> tuple[float, float]:
        """Move the window by a fraction of its width (negative: earlier)."""
        t0, t1 = self.window
        shift = (t1 - t0) * fraction
        return self.set_window(t0 + shift, t1 + shift)

    def show_all(self) -> tuple[float, float]:
        return self.set_window(*self.span)

    def reveal(self, t: float) -> bool:
        """Scroll so that ``t`` is in the window. True if the window moved."""
        t0, t1 = self.window
        if t0 <= t <= t1:
            return False
        width = t1 - t0
        self.set_window(t - width / 2, t + width / 2)
        return True

    # ---- drawing ----

    def _line(self, env, scale: float) -> tuple[np.ndarray, np.ndarray, bool]:
        x, y = env.polyline()
        return x - self.origin, y * scale, env.reduced

    def trace(self, cid: str, columns: int) -> tuple[np.ndarray, np.ndarray, bool]:
        """(x, y, reduced) for one lane in the current window: the raw samples
        when they fit, else one min/max pair per pixel column, so a dropout
        one sample long is still drawn."""
        name, scale = self._name(cid)
        return self._line(self.session.trace(name, columns), scale)

    def overlay_trace(self, cid: str, columns: int):
        """The same channel of the second drive on this time axis, or None."""
        name, scale = self._name(cid)
        env = self.session.overlay_trace(name, columns)
        return None if env is None or not len(env.t) else self._line(env, scale)

    # ---- cursors ----

    @property
    def cursor_a(self) -> float:
        return self.session.cursor_a - self.origin

    @property
    def cursor_b(self) -> float | None:
        b = self.session.cursor_b
        return None if b is None else b - self.origin

    def set_cursor(self, t: float, which: str = "a") -> float:
        self.session.set_cursor(t + self.origin, which)
        return self.cursor_a if which == "a" else self.cursor_b    # type: ignore[return-value]

    def clear_cursor_b(self) -> None:
        self.session.clear_cursor_b()

    @property
    def delta(self) -> float | None:
        """B minus A in seconds, None without a second cursor."""
        return self.session.cursor_delta

    def step(self, direction: int, cid: str | None = None) -> float:
        """Cursor A to the next or previous sample (of one lane, or of anything)."""
        name = self._name(cid)[0] if cid else None
        return self.session.step(direction, name) - self.origin

    def readouts(self, cids, full: bool = True) -> dict[str, LaneReadout]:
        """Per lane: the value at cursor A, and A-to-B statistics when B is
        set. ``full=False`` leaves out min, max and mean: what a readout that
        follows the cursor needs, at the cost of two lookups per lane."""
        known = [cid for cid in cids if self.has(cid)]
        rows = self.session.readouts([self._name(cid)[0] for cid in known], full)
        out = {}
        for cid, row in zip(known, rows):
            scale = self._name(cid)[1]
            stats = row.stats
            if stats is not None and scale != 1.0:
                stats = SpanStats(stats.name, stats.units, stats.a * scale, stats.b * scale,
                                  stats.delta * scale, stats.min * scale, stats.max * scale,
                                  stats.mean * scale, stats.n, stats.rate * scale)
            out[cid] = LaneReadout(row.value * scale, stats)
        return out

    # ---- events ----

    def add_event(self, t: float, kind: str, text: str = "") -> None:
        self.session.add_event(t + self.origin, kind, text)

    def events(self) -> list[tuple[float, str, str]]:
        """(t, kind, text) of everything worth jumping to, in time order."""
        return [(e.t - self.origin, e.kind, e.text or e.kind) for e in self.session.event_list()]

    def jump(self, direction: int) -> float | None:
        """Cursor A to the next (+1) or previous (-1) event; the window follows.
        None when there is none that way."""
        target = self.session.jump_event(direction)
        if target is None:
            return None
        self.set_window(*self.window)       # jump_event recentres without minding the ends
        return target - self.origin

    # ---- a second drive ----

    def set_overlay(self, other: "ReplayModel | ad.LogView | None", align: str = "start") -> str:
        """Lay another drive over this one, lined up on ``align`` (one of
        ALIGNMENTS). Returns what it was really aligned on: "start" when a
        drive has no such moment. None removes the overlay."""
        if other is None:
            self.session.set_overlay(None)
            self.overlay_name = self.align = ""
            return ""
        model = other if isinstance(other, ReplayModel) else ReplayModel(other)
        self.session.set_overlay(model.session.log, "start")
        self.overlay_name = model.view.title
        return self.align_overlay(align)

    def align_overlay(self, align: str) -> str:
        if self.session.overlay is None:
            return ""
        try:
            self.session.align_overlay(align, **_ALIGN_OPTIONS.get(align, {}))
            self.align = align
        except (ValueError, KeyError):
            self.session.align_overlay("start")
            self.align = "start"
        return self.align

    @property
    def overlay_offset(self) -> float:
        """Seconds added to the second drive's times."""
        comparison = self.session.overlay
        return comparison.offset if comparison is not None else 0.0

    def nudge_overlay(self, seconds: float) -> float:
        """Slide the second drive by hand. Returns the new offset."""
        return self.session.shift_overlay(seconds)
