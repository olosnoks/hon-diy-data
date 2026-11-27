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

Calculated channels (``hondalog.analysis.derived``: lateral g, gear, power,
grade, ...) join the log's as lanes and readouts with ``set_derived``; the
events of ``hondalog.analysis.events`` become the moments [ and ] step
through with ``set_events``. ``demo_drive_view`` is a simulated drive (no real
place: the track is laid out around an empty spot of ocean) for ``--demo``.

No Qt in here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from ..analysis import ReplaySession
from ..analysis import derived as _derived
from ..analysis import events as _events
from ..analysis.cursors import SpanStats
from ..analysis.lod import envelope
from ..data import Column, Event, Log, Stream, StreamInfo
from . import adapters as ad
from .bus import ChannelSpec

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
        #: calculated channels: view id -> (t in view seconds, values)
        self.derived: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        #: the drive's events (hondalog.analysis.events.DriveEvent), times in view seconds
        self.drive_events: list = []
        self.set_window(lo, hi)
        self.session.set_cursor(lo + self.origin)

    # ---- names ----

    def _name(self, cid: str) -> tuple[str, float]:
        return self.names.get(cid, (cid, 1.0))

    def has(self, cid: str) -> bool:
        return cid in self.derived or self._name(cid)[0] in self.session.log.channels

    # ---- calculated channels ----

    def set_derived(self, cid: str, t, v) -> None:
        """A calculated channel (view time, view units) that lanes and readouts can show."""
        t, v = np.asarray(t, dtype=float), np.asarray(v, dtype=float)
        if len(t):
            self.derived[cid] = (t, v)
        else:
            self.derived.pop(cid, None)

    def derived_at(self, cid: str, t: float | None = None) -> float:
        t = self.cursor_a if t is None else t
        pair = self.derived.get(cid)
        if pair is None or not len(pair[0]):
            return float("nan")
        tt, vv = pair
        if not tt[0] - 0.2 <= t <= tt[-1] + 0.2:
            return float("nan")
        i = int(np.clip(np.searchsorted(tt, t, side="right") - 1, 0, len(tt) - 1))
        return float(vv[i])

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
        if cid in self.derived:
            t, v = self.derived[cid]
            t0, t1 = self.window
            env = envelope(t, v, t0, t1, columns)
            x, y = env.polyline()
            return x, y, env.reduced
        name, scale = self._name(cid)
        return self._line(self.session.trace(name, columns), scale)

    def overlay_trace(self, cid: str, columns: int):
        """The same channel of the second drive on this time axis, or None."""
        if cid in self.derived:
            return None
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
        out = {}
        for cid in cids:
            if cid in self.derived:
                a = self.derived_at(cid)
                b = self.cursor_b
                stats = None
                if b is not None:
                    vb = self.derived_at(cid, b)
                    stats = SpanStats(cid, "", a, vb, vb - a, min(a, vb), max(a, vb), (a + vb) / 2, 2, 0.0)
                out[cid] = LaneReadout(a, stats)
        known = [cid for cid in cids if cid not in self.derived and self.has(cid)]
        rows = self.session.readouts([self._name(cid)[0] for cid in known], full)
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

    def set_events(self, found) -> None:
        """The drive's events (view time). Once set, they and a capture's
        trigger (and the order of loss, if added) are the moments [ and ]
        step through, instead of every marker, trigger and alarm in the log."""
        self.drive_events = sorted(found, key=lambda e: e.t)
        self.session.clear_events("event")
        for e in self.drive_events:
            self.session.add_event(e.t + self.origin, "event", f"{e.label} {e.summary}")

    def jump(self, direction: int) -> float | None:
        """Cursor A to the next (+1) or previous (-1) event; the window follows.
        None when there is none that way."""
        kinds = ("event", "trigger", "loss") if self.drive_events else ()
        target = self.session.jump_event(direction, *kinds)
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


# ---- the analysis on a view -------------------------------------------------

def analysis_channels(view: ad.LogView) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """The view's channels by their log names and in the log's units (what
    ``hondalog.analysis`` expects), on view time."""
    out = {}
    for cid, t in view.t.items():
        name, scale = view.names.get(cid, (cid, 1.0))
        v = np.asarray(view.data[cid], dtype=float)
        out[name] = (np.asarray(t, dtype=float), v / scale if scale else v)
    return out


def view_epoch(view: ad.LogView) -> float | None:
    """Wall-clock (unix) time of the view's zero, when the log says."""
    try:
        return float(view.meta["epoch_unix"]) + float(view.origin)
    except (KeyError, TypeError, ValueError):
        return None


def analyse(view: ad.LogView, car=None):
    """(derived channels, events) of a view, both on view time. A capture's
    window ends where it was cut, not at a key-off: no end silence there."""
    from ..analysis.motion import Car
    channels = analysis_channels(view)
    car = car or Car()
    _lo, hi = view.span()
    whole = view.trigger_t is None
    found = _events.detect(channels, view.markers, t_end=hi if whole else None, car=car)
    return _derived.derive(channels, car), found


# ---- a simulated drive for --demo ------------------------------------------

#: the simulated track is laid out around this spot (open ocean: no real road)
DEMO_ORIGIN = (30.0, -40.0)


def _demo_specs() -> dict[str, ChannelSpec]:
    rows = [
        ("rpm", "RPM", "rpm", 0, 0, 7000), ("vss", "Speed", "km/h", 0, 0, 160), ("map", "MAP", "kPa", 0, 15, 105),
        ("tps", "Throttle", "%", 0, 0, 100), ("inj", "Injector", "ms", 2, 0, 16),
        ("ign", "Timing", "° BTDC", 1, -10, 50), ("idle_target", "Idle target", "rpm", 0, 500, 1500),
        ("iac_cmd", "IAC cmd", "", 0, 0, 255), ("iacv", "Idle valve", "%", 0, 0, 100),
        ("ect", "Coolant", "°C", 0, -20, 120), ("iat", "Intake air", "°C", 0, -20, 80),
        ("stft", "Short trim", "%", 1, -25, 25), ("ltft", "Long trim", "%", 1, -25, 25),
        ("o2", "O2 front", "V", 2, 0, 1), ("batt", "Battery", "V", 1, 8, 16), ("eld", "Elec load", "A", 0, 0, 80),
        ("alt_fr", "Alt field", "%", 0, 0, 100), ("ign_limit", "Ign corr 0x27", "", 0, 0, 40),
        ("knock", "Knock level", "", 0, 0, 12),
    ]
    specs = {cid: ChannelSpec(cid, label, units, dec, lo, hi, verified=True) for cid, label, units, dec, lo, hi in rows}
    for cid, label in (("brake", "Brake"), ("vtec", "VTEC"), ("closed_loop", "Closed loop"), ("ac_sw", "A/C"),
                       ("fuel_pump", "Pump")):
        specs[cid] = ChannelSpec(cid, label, kind="flag", verified=True)
    for cid, label, units, dec, lo, hi in (("gps_lat", "Latitude", "°", 6, -90, 90),
                                           ("gps_lon", "Longitude", "°", 6, -180, 180),
                                           ("gps_speed", "GPS speed", "km/h", 1, 0, 200),
                                           ("gps_heading", "Heading", "°", 0, 0, 360),
                                           ("gps_sats", "Satellites", "", 0, 0, 16)):
        specs[cid] = ChannelSpec(cid, label, units, dec, lo, hi, source="GPS", verified=True)
    return specs


def demo_drive_view() -> ad.LogView:
    """About three minutes of a simulated drive: a launch that nearly stalls
    (throttle 0.6 s late), a full-throttle pull in 2nd with VTEC and a knock
    retard, corners, a link dropout while moving, a hard stop, a mark and the
    key-off. Plausible, not measured."""
    rng = np.random.default_rng(7)
    dt = 0.1
    t = np.round(np.arange(0.0, 195.0, dt), 3)
    n = len(t)
    kmh = np.interp(t, [0, 22.4, 26, 31, 33, 41.5, 44, 60, 90, 110, 130, 150, 160, 163, 166, 172, 195],
                    [0, 0, 12, 24, 22, 80, 76, 62, 64, 92, 95, 90, 60, 20, 0, 0, 0])
    gear_at = np.select([t < 22.4, t < 31, t < 33, t < 41.8, t < 44, t < 100, t < 104, t < 150, t < 156, t < 160],
                        [0, 1, 0, 2, 0, 3, 0, 5, 0, 3], 0)
    gear_at = np.where((t >= 44) & (t < 100) & (kmh > 63), 4, gear_at)
    per = np.array([0, 133.6, 76.8, 49.7, 36.0, 27.7])
    vss = np.round(kmh / 1.011)
    rpm = np.where(gear_at > 0, vss * per[gear_at], 860.0)
    for a, b, nxt in ((31, 33, 1600), (41.8, 44, 2600), (100, 104, 2400), (150, 156, 1700), (160, 166, 900)):
        m = (t >= a) & (t < b)
        start = rpm[max(0, int(np.searchsorted(t, a)) - 1)]
        rpm[m] = np.linspace(start, nxt, int(m.sum()))
    m = (t >= 20.0) & (t < 23.0)                         # the clutch bites, throttle closed: 470 rpm
    rpm[m] = 860 - 390 * np.sin(np.pi * np.clip((t[m] - 20.0) / 2.6, 0, 1))
    m = (t >= 23.0) & (t < 26.0)
    rpm[m] = np.maximum(rpm[m], 1500)
    rpm += rng.normal(0, 6, n)
    tps = np.full(n, 7.8)
    tps[(t >= 20.6) & (t < 31)] = 22.0                   # 0.6 s after the fall began
    pull = (t >= 33) & (t < 41.5)
    tps[pull] = np.linspace(70, 95, int(pull.sum()))
    cruise = (t >= 44) & (t < 150)
    tps[cruise] = 18 + 6 * np.sin(t[cruise] / 7)
    tps[(t >= 104) & (t < 112)] = 45
    map_ = np.clip(22 + 0.82 * tps + rng.normal(0, 0.6, n), 18, 99)
    map_[(t >= 150) & (t < 168)] = 22
    inj = np.where(map_ > 25, 1.2 + map_ * 0.09, 2.6) + rng.normal(0, 0.03, n)
    inj[(t >= 150) & (t < 160)] = 0.0                    # fuel cut on the overrun
    brake = ((t >= 150) & (t < 168)).astype(float)
    rate = np.zeros(n)
    for a, b, r in ((46, 52, 9.0), (64, 71, -12.0), (84, 92, 11.0), (120, 126, 6.0), (138, 141, -15.0)):
        rate[(t >= a) & (t < b)] = r
    heading = (40.0 + np.cumsum(rate) * dt) % 360
    v_ms = kmh / 3.6
    east = np.cumsum(v_ms * np.sin(np.radians(heading))) * dt
    north = np.cumsum(v_ms * np.cos(np.radians(heading))) * dt
    lat0, lon0 = DEMO_ORIGIN
    lat = lat0 + north / 111_320.0
    lon = lon0 + east / (111_320.0 * np.cos(np.radians(lat0)))
    specs = _demo_specs()
    view = ad.LogView(Path("demo_drive"), title="demo_drive · simulated", note="simulated drive",
                      meta={"epoch_unix": datetime(2026, 11, 26, 23, 0, 0).timestamp()},
                      markers=[(176.0, "MARK 1")])

    def put(cid, tt, vv):
        view.specs[cid] = specs[cid]
        view.t[cid] = np.asarray(tt, dtype=float)
        view.data[cid] = np.asarray(vv, dtype=np.float32)

    # the ECU's fast block every 0.1 s; silent 72-78 s (a dropout while moving) and after 186 s (key-off)
    ecu = ~((t > 72.0) & (t < 78.0)) & (t < 186.0)
    for cid, vv in (("rpm", rpm), ("vss", vss), ("map", map_), ("tps", tps), ("inj", inj),
                    ("ign", 12 + rpm / 300 - map_ * 0.05), ("stft", rng.normal(2, 2, n)),
                    ("ltft", np.full(n, 6.2)), ("o2", 0.45 + 0.4 * np.sign(np.sin(t * 2.7)))):
        put(cid, t[ecu], vv[ecu])
    slow = ecu & (np.round(t * 10) % 10 == 0)             # the slow block once a second
    ts = t[slow]
    idle_target = np.where(t < 120, 864.0, 840.0)
    iac = 90 + 8 * np.sin(t / 9) + np.where((t >= 21) & (t < 24), 14, 0)
    ign_limit = np.where(map_ > 44, np.minimum(4 + 1.3 * (map_ - 41), 28), 4.0)
    ign_limit[(t >= 37.0) & (t < 39.0)] -= 9             # timing pulled under load
    vtec = ((t >= 40.0) & (t < 41.5)).astype(float)
    for cid, vv in (("idle_target", idle_target), ("iac_cmd", iac), ("iacv", (iac - 70) * 0.9),
                    ("ect", np.minimum(70 + t / 30, 82)), ("iat", np.full(n, 24.0)),
                    ("batt", 14.2 + rng.normal(0, 0.03, n)), ("eld", 18 + 6 * (np.sin(t / 13) > 0.6)),
                    ("alt_fr", np.full(n, 38.0)), ("ign_limit", np.round(ign_limit)),
                    ("knock", np.round(1 + 0.104 * (rpm / 1000) ** 2.9)), ("brake", brake), ("vtec", vtec),
                    ("closed_loop", ((tps < 50) & (inj > 0)).astype(float)), ("ac_sw", np.zeros(n)),
                    ("fuel_pump", np.ones(n))):
        put(cid, ts, vv[slow])
    # the handheld's GPS once a second; its speed and heading 1.7 s late
    gps = np.round(t * 10) % 10 == 0
    gt = t[gps]
    late = np.clip(gt - 1.7, 0, None)
    put("gps_lat", gt, lat[gps])
    put("gps_lon", gt, lon[gps])
    put("gps_speed", gt, np.interp(late, t, kmh))
    put("gps_heading", gt, np.interp(late, t, np.degrees(np.unwrap(np.radians(heading)))) % 360)
    put("gps_sats", gt, np.full(len(gt), 11.0))
    return view
