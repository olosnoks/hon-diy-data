"""Demo data and the small stand-ins the screens were built against.

Real use is wired in ``wired.py`` (the W1-W5 backends). What stays here:

    DemoSource, DemoMapper, DemoTableModel, NullRecording, demo_*   ``--demo``
    PollerSource, PollerMapper      the bases ``wired.LinkSource`` and
                                    ``wired.MappingSession`` build on
    DemoGps                         the handheld's GPS in the demo: the Pearl, MS loop
    LimitAlarms                     plain limits (demo; the black box rails)
    HistogramMap, CsvLogReader      demo mode
    order_of_loss                   the forensics reading for any LogView
    spec_from_channel, BLACKBOX_SPECS, PLACEHOLDERS, DEFAULT_LIMITS

No widgets in here. See docs/ui-adapters.md.
"""

from __future__ import annotations

import collections

import math
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import addressmap, protocol
from .. import poller as pl
from ..logger import log_dir, read_log
from ..protocol import ProtocolError
from ..transport import BUILTIN_SIM, BUILTIN_SIM_ISO, SIM_SYMLINK, list_ports, make_transport
from . import adapters as ad
from .bus import ChannelBus, ChannelSpec
from .units import U

# ---- display details the 0.1.0 address map does not carry -------------------

# Short labels so they fit a 163 px tile. Applied only to the shipped labels.
_SHORT = {"Alternator load": "Alt load", "Electrical load": "Elec load",
          "Ignition limit": "Ign limit", "Ignition": "Ign BTDC"}
_UNITS = {"° BTDC": "°"}
_SIGNED = {"stft", "ltft"}

# HANDOFF section 8 placeholder limits, used until addressmap.toml carries
# warn_lo / warn_hi / alarm_lo / alarm_hi itself (W3).
DEFAULT_LIMITS = {
    "rpm": dict(warn_hi=6500, alarm_hi=6900),
    "stft": dict(warn_lo=-15, warn_hi=15),
    "ltft": dict(warn_lo=-12, warn_hi=12),
    "ect": dict(warn_hi=100, alarm_hi=108),
    "iat": dict(warn_hi=45),
    "batt": dict(warn_lo=12.8, alarm_lo=11.5, alarm_hi=15.2),
    "knock": dict(warn_hi=2),
    "mil": dict(alarm_hi=0.5),
    "bbatt": dict(alarm_lo=11), "ecub": dict(alarm_lo=11),
    "fpv": dict(alarm_lo=11), "ig1": dict(alarm_lo=11),
}

# Slots the design shows that the 0.1.0 map has no address for.
PLACEHOLDERS = (ChannelSpec("fan", "Rad fan", kind="flag", mapped=False),
                ChannelSpec("fuel_pump", "Fuel pump", kind="flag", mapped=False))

# The black-box channel list is assumed from the brief (HANDOFF section 8).
BLACKBOX_SPECS = (
    ChannelSpec("bbatt", "Battery", "V", 2, 8, 16, source="BB", verified=True),
    ChannelSpec("ecub", "ECU +B", "V", 2, 8, 16, source="BB", verified=True),
    ChannelSpec("fpv", "FP feed", "V", 2, 0, 16, source="BB", verified=True),
    ChannelSpec("ig1", "IG1", "V", 2, 8, 16, source="BB", verified=True),
    ChannelSpec("tach", "Tach", "Hz", 0, 0, 250, source="BB", verified=True),
    ChannelSpec("igt", "Ign trigger", "Hz", 0, 0, 250, source="BB", verified=True),
    ChannelSpec("injp", "Inj pulse", "ms", 2, 0, 16, source="BB", verified=True),
    ChannelSpec("bbmark", "Marker", kind="flag", source="BB", verified=True),
)

# The handheld's GPS (the "gps" stream's columns, hondalog.geo.gps_columns),
# as the live bus carries it for the Map screen. Source "GPS".
GPS_SPECS = (
    ChannelSpec("gps_lat", "Latitude", "°", 6, -90, 90, source="GPS", verified=True),
    ChannelSpec("gps_lon", "Longitude", "°", 6, -180, 180, source="GPS", verified=True),
    ChannelSpec("gps_speed", "GPS speed", "km/h", 1, 0, 200, source="GPS", verified=True),
    ChannelSpec("gps_heading", "Heading", "°", 0, 0, 360, source="GPS", verified=True),
    ChannelSpec("gps_sats", "Satellites", "", 0, 0, 16, source="GPS", verified=True),
    ChannelSpec("gps_fix", "GPS fix", kind="flag", source="GPS", verified=True),
)
GPS_IDS = tuple(s.id for s in GPS_SPECS)
#: a fix comes once a second: after this long without one the position is stale
GPS_TIMEOUT_S = 3.5


def with_limits(spec: ChannelSpec) -> ChannelSpec:
    limits = DEFAULT_LIMITS.get(spec.id)
    return replace(spec, **limits) if limits else spec


def spec_from_channel(ch) -> ChannelSpec:
    """hondalog.addressmap.Channel -> ChannelSpec."""
    spec = ChannelSpec(
        id=ch.name, label=_SHORT.get(ch.label, ch.label), units=_UNITS.get(ch.units, ch.units),
        decimals=ch.decimals, lo=ch.min, hi=ch.max, kind=ch.kind, source="ECU",
        verified=ch.verified, signed=ch.name in _SIGNED)
    own = {k: getattr(ch, k) for k in ("warn_lo", "warn_hi", "alarm_lo", "alarm_hi")
           if getattr(ch, k, None) is not None}       # map v2 fields, when they exist
    return replace(spec, **own) if own else with_limits(spec)


def obd2_specs(amap) -> list[ChannelSpec]:
    """Tiles for the map's OBD2-only channels (``[[obd2]]`` with gauge = true).
    Empty in Honda mode: nothing there can read them."""
    return [spec_from_channel(ch) for ch in getattr(amap, "obd2", ()) if ch.gauge]


def is_sim_port(port: str) -> bool:
    return port in (BUILTIN_SIM, BUILTIN_SIM_ISO, SIM_SYMLINK)


# ---- ECU over K-line --------------------------------------------------------

class PollerSource:
    """ChannelSource + Connectable on top of hondalog.poller.Poller."""

    name = "ECU"

    def __init__(self, load_map=None):
        self._load_map = load_map or self._default_map
        self.map_problem = ""
        self.amap = self._load_map()
        self.poller: pl.Poller | None = None
        self.port = ""
        self.bus: ChannelBus | None = None
        self.sink = None                    # callable(Sample), on the poller thread
        self.wire_clock = time.monotonic    # bench hook: when the last byte arrived
        self._verified: set[str] = set()
        self._silent_since: float | None = None

    def _default_map(self):
        try:
            self.map_problem = ""
            return addressmap.load_user_map()
        except (addressmap.MapError, OSError) as exc:
            self.map_problem = str(exc)
            return addressmap.load_default()

    def specs(self):
        out = []
        for ch in self.amap.channels:
            spec = spec_from_channel(ch)
            if ch.name in self._verified:
                spec.verified = True
            out.append(spec)
        out += obd2_specs(self.amap)
        have = {s.id for s in out}
        return out + [p for p in PLACEHOLDERS if p.id not in have]

    def start(self, bus: ChannelBus) -> None:
        self.bus = bus
        for spec in self.specs():
            bus.register(spec)

    def stop(self) -> None:
        self.disconnect()

    # -- Connectable --

    def ports(self):
        return [(p.device, p.label) for p in list_ports()]

    def connect(self, port: str) -> None:
        if self.poller is not None or not port:
            return
        self.amap = self._load_map()
        if self.bus is not None:
            self.start(self.bus)            # pick up an edited map
        self.port = port
        self._silent_since = None
        self.poller = pl.Poller(make_transport(port, self.amap.framing), self.amap,
                                on_sample=self._on_sample)
        self.poller.port_name = port
        self.poller.start()

    def disconnect(self) -> None:
        poller, self.poller = self.poller, None
        if poller is not None:
            poller.stop()

    def set_verified(self, channel_id: str, verified: bool) -> str:
        (self._verified.add if verified else self._verified.discard)(channel_id)
        if self.bus is not None:
            self.start(self.bus)
        return (f"{channel_id}: verified for this session. To keep it, set "
                f"status = \"verified\" in {addressmap.user_map_path()}")

    # -- poller thread --

    def _on_sample(self, sample) -> None:
        sink = self.sink
        if sink is not None:
            sink(sample)
        bus, poller = self.bus, self.poller
        if sample.link and bus is not None and poller is not None:
            bus.publish(sample.values, t=bus.bus_time(poller.t0 + sample.t),
                        wire=self.wire_clock(), source="ECU")

    # -- status --

    def status(self) -> ad.SourceStatus:
        p = self.poller
        if p is None:
            return ad.SourceStatus(ad.OFF, detail="no port")
        sim = is_sim_port(self.port)
        if p.state == pl.OK:
            self._silent_since = None
            if self.bus is not None and p.hz > 0:
                self.bus.set_timeout("ECU", max(0.5, 3.0 / p.hz))
            return ad.SourceStatus(ad.OK, p.hz, p.errors, simulated=sim, queue=len(p.samples),
                                   link="OBD2" if getattr(p, "mode", "") == pl.OBD2 else "")
        if p.state == pl.CONNECTING:
            return ad.SourceStatus(ad.CONNECTING, errors=p.errors, detail="waking K-line",
                                   simulated=sim)
        if self._silent_since is None:
            self._silent_since = time.monotonic()
        silent = time.monotonic() - self._silent_since
        if p.state == pl.NO_REPLY:
            return ad.SourceStatus(ad.SILENT, errors=p.errors, silent_s=silent, simulated=sim,
                                   detail=p.last_error)
        return ad.SourceStatus(ad.LOST, errors=p.errors, silent_s=silent,
                               detail=p.last_error, simulated=sim)

    @property
    def ecu_id_text(self) -> str:
        return self.poller.ecu_id_text if self.poller is not None else ""


# ---- a ticking source (demo data) ------------------------------------------

class _Ticker:
    """A daemon thread that calls self._step(bus_time) at a fixed rate."""

    hz = 10.0

    def __init__(self):
        self.bus: ChannelBus | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, bus: ChannelBus) -> None:
        self.bus = bus
        for spec in self.specs():
            bus.register(spec)
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name=f"hondalog-{self.name}")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(1.0)

    def _run(self) -> None:
        period = 1.0 / self.hz
        nxt = time.monotonic()
        while not self._stop.is_set():
            self._step(self.bus.now())
            nxt += period
            delay = nxt - time.monotonic()
            if delay < -1.0:
                nxt = time.monotonic()
            elif delay > 0:
                self._stop.wait(delay)


# ---- demo data (the mockup's simulator, docs/design/mockup-src/shared.js) ----

DEMO_ECU = (
    ChannelSpec("rpm", "RPM", "rpm", 0, 0, 8000, verified=True),
    ChannelSpec("vss", "Speed", "km/h", 0, 0, 200),
    ChannelSpec("map", "MAP", "kPa", 0, 0, 110),
    ChannelSpec("tps", "Throttle", "%", 0, 0, 100, verified=True),
    ChannelSpec("inj", "Injector", "ms", 2, 0, 16),
    ChannelSpec("ign", "Ign BTDC", "°", 1, -10, 50),
    ChannelSpec("o2", "O2", "V", 2, 0, 1.2),
    ChannelSpec("stft", "Short trim", "%", 1, -25, 25, signed=True),
    ChannelSpec("ltft", "Long trim", "%", 1, -25, 25, signed=True),
    ChannelSpec("ect", "Coolant", "°C", 0, -20, 130),
    ChannelSpec("iat", "Intake air", "°C", 0, -20, 80),
    ChannelSpec("baro", "Baro", "kPa", 0, 60, 110),
    ChannelSpec("batt", "Battery", "V", 1, 8, 16, verified=True),
    ChannelSpec("alt_fr", "Alt load", "%", 0, 0, 100),
    ChannelSpec("eld", "Elec load", "A", 0, 0, 100),
    ChannelSpec("iacv", "Idle valve", "%", 0, 0, 100),
    ChannelSpec("knock", "Knock", "V", 2, 0, 5),
    ChannelSpec("ign_limit", "Ign limit", "°", 1, 0, 20),
    ChannelSpec("closed_loop", "Closed loop", kind="flag"),
    ChannelSpec("vtec", "VTEC", kind="flag"),
    ChannelSpec("ac_sw", "A/C", kind="flag"),
    ChannelSpec("fan", "Rad fan", kind="flag"),
    ChannelSpec("fuel_pump", "Fuel pump", kind="flag"),
    ChannelSpec("mil", "MIL", kind="flag"),
)


def demo_values(t: float, mark_t: float = -1e9) -> dict:
    """hlSim from the mockups: a car driving up and down through the gears."""
    def n(k, f):
        return 0.6 * math.sin(t * f + k * 12.9898) + 0.4 * math.sin(t * f * 2.71 + k * 4.1)
    u = 0.5 + 0.5 * math.sin(t * 0.21)
    rpm = 1400 + 2600 * u + n(1, 3.1) * 18
    inj = 2.2 + 4.6 * u + n(4, 2.2) * 0.04
    dip = 29 < t % 34 < 31.5
    return {
        "rpm": rpm, "vss": 18 + 62 * u + n(2, 0.9) * 0.4,
        "map": 30 + 55 * u ** 1.2 + n(3, 2.3) * 0.8, "tps": 8 + 30 * u + n(5, 1.7) * 0.5,
        "inj": inj, "ign": 34 - 16 * u + n(6, 2.9) * 0.4,
        "o2": 0.46 + 0.36 * math.tanh(3 * math.sin(t * 5.2)), "stft": n(7, 1.3) * 4.2,
        "ltft": 2.3 + n(8, 0.05) * 0.2, "ect": 88 + n(9, 0.07) * 1.2,
        "iat": 46.4 + n(10, 0.05) * 0.5, "baro": 101.0, "batt": 14.1 + n(11, 1.9) * 0.06,
        "alt_fr": 42 + 20 * u + n(12, 1.1) * 2, "eld": 24 + n(13, 0.8) * 3,
        "iacv": 31 + 6 * (1 - u) + n(14, 2.4) * 0.6, "knock": 0.08 + abs(n(15, 4.4)) * 0.05,
        "ign_limit": 0.0, "closed_loop": 1.0, "vtec": 1.0 if rpm > 5500 else 0.0,
        "ac_sw": 1.0 if t % 46 > 30 else 0.0, "fan": 1.0 if t % 58 > 41 else 0.0,
        "fuel_pump": 1.0, "mil": 0.0,
        "bbatt": 14.05 + n(16, 1.9) * 0.05, "ecub": 13.92 + n(17, 2.1) * 0.04,
        "fpv": 9.8 + n(18, 9) * 0.3 if dip else 13.62 + n(18, 2.6) * 0.05,
        "ig1": 13.84 + n(19, 2.2) * 0.04, "tach": rpm / 30, "igt": rpm / 30,
        "injp": inj + 0.06, "bbmark": 1.0 if 0 <= t - mark_t < 1 else 0.0,
    }


class DemoSource(_Ticker):
    """The mockup's simulated car, for looking at the UI without a cable."""

    def __init__(self, name: str = "ECU", clock=None):
        super().__init__()
        self.name = name
        self.hz = 10.0 if name == "ECU" else 50.0
        self._specs = [with_limits(replace(s)) for s in (DEMO_ECU if name == "ECU"
                                                         else BLACKBOX_SPECS)]
        self._ids = [s.id for s in self._specs]
        self.port = "demo"
        self.mark_t = -1e9
        self.clock = clock              # tests / screenshots: fixed simulated time
        self.amap = None
        self.poller = None

    def specs(self):
        return self._specs

    def start(self, bus: ChannelBus) -> None:
        super().start(bus)
        bus.set_timeout(self.name, 1.0)

    def status(self) -> ad.SourceStatus:
        return ad.SourceStatus(ad.OK, self.hz, simulated=True)

    def _step(self, t: float) -> None:
        values = demo_values(self.clock() if self.clock else t, self.mark_t)
        self.bus.publish({k: values[k] for k in self._ids}, t=t, source=self.name)

    def prefill(self, bus: ChannelBus, seconds: float = 60.0) -> None:
        """Fill history and sparklines as if the car had been driving a while."""
        t1 = self.clock() if self.clock else bus.now()
        steps = int(seconds * 10)
        cols = {k: np.empty(steps, dtype=np.float32) for k in self._ids}
        tt = np.empty(steps)
        for i in range(steps):
            dt = (steps - 1 - i) * 0.1
            tt[i] = bus.now() - dt
            values = demo_values(t1 - dt)
            for k in self._ids:
                cols[k][i] = values[k]
        bus.publish_block(tt, cols, source=self.name)
        for k in self._ids:
            state = bus.channel(k)
            state.spark[:] = cols[k][::5][-len(state.spark):]
            state.spark_gen += 1
            if state.spec.is_flag:
                state.changes = int(np.count_nonzero(np.diff(cols[k])))

    # Connectable, so the connection menu behaves
    def ports(self):
        return [("demo", "Demo data")]

    def connect(self, port: str) -> None: ...

    def disconnect(self) -> None: ...

    def set_verified(self, channel_id: str, verified: bool) -> str:
        for spec in self._specs:
            if spec.id == channel_id:
                spec.verified = verified
        return f"{channel_id}: demo channel marked {'verified' if verified else 'unverified'}"

    def note_mark(self) -> None:
        self.mark_t = self.clock() if self.clock else self.bus.now()


# ---- alarms (stopgap limit checker) -----------------------------------------

@dataclass
class _Level:
    level: str = ""
    event: ad.AlarmEvent | None = None
    ok_since: float | None = None


class LimitAlarms:
    """warn_lo/hi and alarm_lo/hi from the ChannelSpec. An alarm latches until
    the value has been back in range for 2 s."""

    LATCH_S = 2.0

    def __init__(self):
        self._levels: dict[str, _Level] = {}
        self._watch: list = []
        self._version = -1
        self._history: list[ad.AlarmEvent] = []
        self.revision = 0

    @staticmethod
    def _check(spec: ChannelSpec, v: float, shown: bool = True):
        """(level, text). ``shown``: the limit in the display unit; False: in
        the channel's own unit (what goes into the log)."""
        def text(word: str, bound: float) -> str:
            return f"{word} {U.limit(spec, bound)}" if shown else f"{word} {bound:g} {spec.units}".strip()
        if spec.alarm_lo is not None and v < spec.alarm_lo:
            return "alarm", text("below", spec.alarm_lo)
        if spec.alarm_hi is not None and v > spec.alarm_hi:
            if spec.is_flag:
                return "alarm", "is ON"
            return "alarm", text("above", spec.alarm_hi)
        if spec.warn_lo is not None and v < spec.warn_lo:
            return "warn", text("below", spec.warn_lo)
        if spec.warn_hi is not None and v > spec.warn_hi:
            return "warn", text("above", spec.warn_hi)
        return "", ""

    def evaluate(self, bus: ChannelBus, now: float):
        if bus.version != self._version:
            self._version = bus.version
            self._watch = [s for s in bus.channels() if any(
                getattr(s.spec, k) is not None
                for k in ("warn_lo", "warn_hi", "alarm_lo", "alarm_hi"))]
        started = []
        for state in self._watch:
            spec = state.spec
            rec = self._levels.get(spec.id)
            if rec is None:
                rec = self._levels[spec.id] = _Level()
            level, limit = ("", "") if state.stale(now) else self._check(spec, state.value)
            if rec.level == "alarm" and level != "alarm":
                if rec.ok_since is None:
                    rec.ok_since = now
                if now - rec.ok_since < self.LATCH_S:
                    continue                    # still latched
            if level == rec.level:
                rec.ok_since = None
                continue
            if rec.event is not None:
                rec.event.t_end = now
                self._history.insert(0, rec.event)
                del self._history[50:]
            rec.level, rec.ok_since, rec.event = level, None, None
            if level:
                rec.event = ad.AlarmEvent(
                    spec.id, "BLACK BOX" if spec.source == "BB" else spec.source,
                    spec.label.upper(), level, U.with_unit(spec, state.value), limit, now,
                    log_text=self._check(spec, state.value, shown=False)[1])
                if level == "alarm":
                    started.append(rec.event)
            self.revision += 1
        return started

    def state(self, channel_id: str) -> str:
        rec = self._levels.get(channel_id)
        return rec.level if rec else ""

    def unacked(self, channel_id: str) -> bool:
        rec = self._levels.get(channel_id)
        return bool(rec and rec.level == "alarm" and rec.event and not rec.event.acked)

    def active(self):
        events = [r.event for r in self._levels.values() if r.event is not None]
        events.sort(key=lambda e: (e.severity != "alarm", -e.t_start))
        return events

    def history(self):
        return self._history

    def acknowledge(self) -> None:
        for rec in self._levels.values():
            if rec.event is not None and not rec.event.acked:
                rec.event.acked = True
                self.revision += 1

    def limits(self):
        rows = []
        for state in self._watch:
            s = state.spec
            if s.is_flag:
                continue
            shown = U.spec(s)                   # the limits in the display unit

            def lim(lo, hi):
                parts = [f"<{round(lo, 2):g}" if lo is not None else "",
                         f">{round(hi, 2):g}" if hi is not None else ""]
                return " ".join(x for x in parts if x).replace("-", "\u2212")
            warn, alarm = lim(shown.warn_lo, shown.warn_hi), lim(shown.alarm_lo, shown.alarm_hi)
            unit = f" {shown.units}" if shown.units != s.units else ""
            rows.append(ad.LimitRow(("BB " if s.source == "BB" else "") + s.label + unit,
                                    warn or "—", alarm or "—"))
        return rows


# ---- recording --------------------------------------------------------------

class NullRecording:
    """Demo mode: marks are counted, nothing is written."""

    active = True
    path = None

    def __init__(self, on_mark=None):
        self.marks = 0
        self._t0 = time.monotonic()
        self._last: float | None = None
        self._on_mark = on_mark

    def start(self) -> str:
        self.active = True
        self._t0 = time.monotonic()
        return ""

    def stop(self) -> None:
        self.active = False

    def mark(self, text=None) -> int:
        self.marks += 1
        self._last = time.monotonic()
        if self._on_mark:
            self._on_mark()
        return self.marks

    def elapsed_s(self) -> float:
        return time.monotonic() - self._t0 if self.active else 0.0

    def last_mark_age_s(self):
        return None if self._last is None else time.monotonic() - self._last

    def stall(self) -> ad.StallStatus:
        if not self.active:
            return ad.StallStatus()
        return ad.StallStatus("armed", min(30.0, self.elapsed_s()))

    def poll(self):
        return []


# ---- logs -------------------------------------------------------------------

class CsvLogReader:
    def __init__(self, spec_lookup=None, directory=None):
        self._lookup = spec_lookup or (lambda cid: None)
        self._dir = directory

    def read(self, path) -> ad.LogView:
        log = read_log(path)
        view = ad.LogView(Path(path), title=Path(path).name, meta=log.meta)
        trigger = None
        try:
            trigger = float(log.meta["stall_time_s"])
        except (KeyError, ValueError):
            for t, text in log.markers:
                if "STALL" in text:
                    trigger = t
                    break
        origin = trigger if trigger is not None else (float(log.t[0]) if len(log.t) else 0.0)
        t = log.t - origin
        for name in log.names:
            if name == "link":
                continue
            spec = self._lookup(name) or ChannelSpec(name, name)
            data = log.data[name]
            if not spec.mapped or not np.isfinite(data).any():
                continue
            view.specs[name], view.t[name], view.data[name] = spec, t, data
        for mt, text in log.markers:
            for part in text.split(" | "):
                if part and "STALL" not in part:
                    view.markers.append((mt - origin, part))
        view.trigger_t = 0.0 if trigger is not None else None
        return view

    def analyze(self, view: ad.LogView) -> ad.Forensics:
        return order_of_loss(view)

    def recent(self, stalls_only: bool = False):
        folder = Path(self._dir) if self._dir else log_dir()
        try:
            files = [p for p in folder.glob("*.csv")
                     if not stalls_only or p.name.startswith("stall_")]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def drives(self):
        return [ad.DriveRow(p, p.name, "file", text=p.name.lower()) for p in self.recent()]

    def import_file(self, path):
        return Path(path)                   # the demo has no database: the file is opened as it is

    def delete(self, ref, allow_keep: bool = False) -> str:
        raise ValueError("the demo has no database: there is nothing to delete")

    def set_keep(self, ref, keep: bool) -> None:
        raise ValueError("the demo has no database")


def order_of_loss(view: ad.LogView, pre_s: float = 30.0, hold_s: float = 0.2) -> ad.Forensics:
    """For each channel, the first time it leaves its normal band in the pre_s
    before the trigger and stays out for hold_s. The band is what the channel
    did in the early part of the window, widened by 8 % of its range."""
    if view.trigger_t is None:
        return ad.Forensics(reading="This file has no stall trigger, so there is no "
                                    "order of loss to work out.")
    t0 = view.trigger_t
    rows, held = [], []
    for cid, tt in view.t.items():
        spec, v = view.specs[cid], view.data[cid]
        if "mark" in cid:                       # the driver's marker is not a signal
            continue
        if cid.startswith("gps_"):              # where the car was is not a signal either
            continue
        rel = tt - t0
        start = max(-pre_s, float(rel[0])) if len(rel) else -pre_s
        split = start + (0.0 - start) * (2.0 / 3.0)
        base = v[(rel >= start) & (rel < split)]
        base = base[np.isfinite(base)]
        if len(base) < 3:
            continue
        margin = 0.0 if spec.is_flag else 0.08 * (spec.hi - spec.lo)
        lo, hi = float(base.min()) - margin, float(base.max()) + margin
        late = np.flatnonzero((rel >= split) & (rel < -0.15))    # at the trigger = effect
        out = np.isfinite(v) & ((v < lo) | (v > hi))
        hit = None
        for i in late:
            if not out[i]:
                continue
            j = int(np.searchsorted(rel, rel[i] + hold_s, side="right"))
            if out[i:max(j, i + 1)].all():
                hit = i
                break
        tag = f"{spec.label.upper()} · {'black box' if spec.source == 'BB' else 'ECU'}"
        if hit is None:
            held.append(spec.label)
            continue
        was = U.text(spec, float(np.median(base)))
        if spec.is_flag:
            what = "dropped to OFF" if v[hit] < 0.5 else "went ON"
        elif v[hit] < lo:
            what = f"fell below {U.with_unit(spec, lo)} (was {was})"
        else:
            what = f"rose above {U.with_unit(spec, hi)} (was {was})"
        rows.append(ad.LossRow(float(rel[hit]), cid, tag, what))
    rows.sort(key=lambda r: r.t)
    out = ad.Forensics(loss=rows + [ad.LossRow(0.0, "", "STALL", "trigger")])
    if held:
        names = ", ".join(held[:-1]) + (" and " if len(held) > 1 else "") + held[-1]
        out.held = f"{names} stayed in {'their' if len(held) > 1 else 'its'} normal range."
    else:
        out.held = "Nothing: every recorded signal left its normal range."
    if rows:
        first = rows[0]
        name = first.signal.split(" · ")[0]
        out.summary = f"{name} fell first, {abs(first.t):.1f} s earlier."
        out.reading = (f"{name} left its normal range {abs(first.t):.1f} s before the "
                       "engine died, ahead of everything else recorded. Start there.")
    else:
        out.reading = ("No recorded signal left its normal range before the trigger. "
                       "The cause is something this log does not carry.")
    return out


def demo_stall_view() -> ad.LogView:
    """The simulated capture from the mockups: fuel-pump feed dies 2.1 s early."""
    t = -30.0 + np.arange(401) * 0.1

    def n(a, f):
        return np.sin(t * f + a * 7.3) * 0.6 + np.sin(t * f * 2.3 + a) * 0.4
    blip = np.exp(-((t + 17) / 1.6) ** 2)
    sag = np.where(t < -0.9, 1.0, np.where(t < 0, -t / 0.9, 0.0))
    run = t < 0
    off = 0.3 * np.exp(-np.maximum(0, t) / 2)
    rpm = (790 + 1900 * blip + n(1, 3) * 14) * sag ** 0.7
    cols = {
        "rpm": rpm,
        "map": np.where(run, 33 + 40 * blip + n(2, 2) * 0.8 + (1 - sag) * 60, 101.0),
        "tps": 1 + 34 * blip,
        "o2": np.where(t < -1.3, 0.46 + 0.36 * np.tanh(3 * np.sin(t * 5.2)), 0.05),
        "inj": np.where(run, (2.4 + 3 * blip) * np.where(t > -1.3, 1.22, 1.0), 0.0),
        "bbatt": np.where(run, 14.05 + n(3, 2) * 0.04, 12.4 + off),
        "ecub": np.where(run, 13.92, 12.3 + off) + n(4, 2) * 0.03,
        "fpv": np.where(t < -2.1, 13.62 + n(5, 2.6) * 0.05,
                        np.maximum(0.2, 13.6 * np.exp(-(t + 2.1) / 0.06))),
        "ig1": np.where(run, 13.84, 12.25 + off) + n(6, 2) * 0.03,
        "tach": rpm / 30,
        "fuel_pump": (t < 2).astype(float),
        "closed_loop": (t < -1.0).astype(float),
        "bbmark": (((t >= -22) & (t < -21.5)) | ((t >= -9) & (t < -8.5))).astype(float),
    }
    specs = {s.id: with_limits(replace(s)) for s in (*DEMO_ECU, *BLACKBOX_SPECS)}
    specs["rpm"] = replace(specs["rpm"], hi=3000)
    specs["tach"] = replace(specs["tach"], hi=100)
    specs["inj"] = replace(specs["inj"], hi=8)
    view = ad.LogView(Path("stall_20261115_140211.csv"), title="stall_20261115_140211.csv",
                      note="simulated capture", trigger_t=0.0,
                      markers=[(-22.0, "MARK 1"), (-9.0, "MARK 2")])
    for cid, col in cols.items():
        view.specs[cid], view.t[cid], view.data[cid] = specs[cid], t, col.astype(np.float32)
    # the handheld's GPS: on the rough stretch of the Pearl loop, rolling to a halt at the stall
    gt, lat, lon, kmh, heading = demo_stall_track()
    for spec in GPS_SPECS:
        view.specs[spec.id], view.t[spec.id] = spec, gt
    view.data.update({"gps_lat": lat, "gps_lon": lon, "gps_speed": kmh, "gps_heading": heading,
                      "gps_sats": np.full(len(gt), 9.0), "gps_fix": np.ones(len(gt))})
    return view


def demo_stall_track():
    """(t, lat, lon, km/h, heading) of the demo capture, 1 Hz, -30 s to +10 s:
    on the rough stretch of the Pearl, MS loop, stopping after the stall."""
    from ..geo import route
    loop = route.PEARL_LOOP
    a, b = loop.stretches["rough stretch"]
    driver = route.Driver(loop, seed=5, start_m=a + (b - a) * 0.55 - 420.0, noise_m=1.0)
    driver.v = 15.0
    t = np.arange(-30.0, 10.5, 1.0)
    rows = [driver.step(0.0 if i == 0 else 1.0, running=tt < 0.0) for i, tt in enumerate(t)]
    out = np.array(rows)
    return t, out[:, 0], out[:, 1], out[:, 2], out[:, 3]


def demo_stall_pins():
    """Stall pins for the demo's All stalls view (no database): three on the
    rough stretch of the Pearl loop, one on the US 80-like road."""
    from ..geo import route
    from ..geo.stalls import StallPin
    loop = route.PEARL_LOOP
    out = []
    for i, (where, frac, day, reason) in enumerate((("rough stretch", 0.55, 15, "rpm"), ("rough stretch", 0.4, 9, "rpm"),
                                                    ("us 80", 0.3, 4, "link"), ("rough stretch", 0.7, 1, "rpm"))):
        lat, lon = loop.point(where, frac)
        wall = datetime(2026, 11, day, 7, 12 + i, 3 * i)
        out.append(StallPin(i + 1, f"stall_{wall:%Y%m%d_%H%M%S}", "stall", reason,
                            "rpm dropped to 0" if reason == "rpm" else "ECU went silent", f"drive_{wall:%Y_%m_%d_%H%M}",
                            wall.isoformat(), wall.timestamp(), 600.0 + 90 * i, lat, lon, 4.0 + 3 * i, 52.0 + 4 * i,
                            None, 0.3))
    return out


class DemoGps(_Ticker):
    """The handheld's GPS in the demo: once a second, the car drives the
    Pearl, MS loop (``hondalog.geo.route``) at the demo's speed. Source "GPS"."""

    name = "GPS"
    hz = 1.0

    def __init__(self, clock=None, seed: int = 4):
        super().__init__()
        from ..geo import route
        self.clock = clock
        self.driver = route.Driver(route.PEARL_LOOP, seed=seed, start_m=4200.0, noise_m=1.0)
        self._t: float | None = None

    def specs(self):
        return GPS_SPECS

    def start(self, bus: ChannelBus) -> None:
        super().start(bus)
        bus.set_timeout(self.name, GPS_TIMEOUT_S)

    def status(self) -> ad.SourceStatus:
        return ad.SourceStatus(ad.OK, self.hz, simulated=True)

    def _fix(self, dt: float) -> dict:
        lat, lon, kmh, heading = self.driver.step(dt)
        return {"gps_lat": lat, "gps_lon": lon, "gps_speed": kmh, "gps_heading": heading,
                "gps_sats": 9.0, "gps_fix": 1.0}

    def _step(self, t: float) -> None:
        now = self.clock() if self.clock else t
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        if dt or self.clock is None:
            self.bus.publish(self._fix(dt or 1.0), t=t, source=self.name)

    def prefill(self, bus: ChannelBus, seconds: float = 300.0) -> None:
        """A trail, as if the car had been driving for a while."""
        steps = int(seconds)
        cols = {k: np.empty(steps, dtype=np.float32) for k in GPS_IDS}
        tt = bus.now() - (steps - 1 - np.arange(steps)) * 1.0
        for i in range(steps):
            for k, v in self._fix(1.0).items():
                cols[k][i] = v
        # the bus keeps history as float32: under a metre at Pearl's latitude, fine for a trail
        bus.publish_block(tt, cols, source=self.name)


# ---- observed map (stopgap histogram) ---------------------------------------

class HistogramMap:
    KEYS = ("inj", "ign", "trim")

    def __init__(self, nx: int = 20, ny: int = 10):
        self.rpm_axis = np.round((500 + np.arange(nx) * 7000 / (nx - 1)) / 50) * 50
        self.map_axis = np.round(17 + np.arange(ny) * 84 / (ny - 1))
        self._hits = np.zeros((ny, nx), dtype=np.int32)
        self._sums = {k: np.zeros((ny, nx)) for k in self.KEYS}
        self._gen = -1
        self.revision = 0

    def cell(self, rpm: float, kpa: float) -> tuple[int, int]:
        i = int(np.abs(self.rpm_axis - rpm).argmin())
        j = int(np.abs(self.map_axis - kpa).argmin())
        return i, j

    def update(self, bus: ChannelBus) -> None:
        rpm = bus.channel("rpm")
        if rpm is None or rpm.gen == self._gen:
            return
        self._gen = rpm.gen
        kpa = bus.channel("map")
        if kpa is None or rpm.value != rpm.value or kpa.value != kpa.value or rpm.value < 300:
            return
        i, j = self.cell(rpm.value, kpa.value)

        def val(cid):
            st = bus.channel(cid)
            return 0.0 if st is None or st.value != st.value else st.value
        self._hits[j, i] += 1
        self._sums["inj"][j, i] += val("inj")
        self._sums["ign"][j, i] += val("ign")
        self._sums["trim"][j, i] += val("stft") + val("ltft")
        self.revision += 1

    def hits(self) -> np.ndarray:
        return self._hits

    def mean(self, key: str) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(self._hits > 0, self._sums[key] / self._hits, np.nan)

    def clear(self) -> None:
        self._hits[:] = 0
        for s in self._sums.values():
            s[:] = 0
        self.revision += 1

    def load_demo(self) -> None:
        """The mockup's sample surface, so demo mode has something to show."""
        fuel, ign, trim, hits = demo_tables(len(self.rpm_axis), len(self.map_axis))
        self._hits[:] = hits
        for key, table in (("inj", fuel), ("ign", ign), ("trim", trim)):
            self._sums[key][:] = table * hits
        self.revision += 1


def demo_tables(nx: int = 20, ny: int = 10):
    """(fuel, ign, trim, hits) as (ny, nx) arrays: hl3dData in shared.js."""
    r = np.linspace(0, 1, nx)[None, :]
    ld = np.linspace(0, 1, ny)[:, None]
    ve = 0.72 + 0.28 * np.exp(-((r - 0.62) / 0.33) ** 2)
    fuel = 1.1 + 7.4 * ld ** 1.15 * ve + 0.5 * r
    ign = 10 + 30 * r ** 0.55 - 17 * ld ** 1.4 + np.where(ld < 0.2, 4, 0)
    trim = 7 * np.sin(r * 5.1 + 1) * np.cos(ld * 3.7) + 2 * np.sin(r * 13 + ld * 7)
    h = (900 * np.exp(-(((r - 0.04) / 0.07) ** 2 + ((ld - 0.2) / 0.14) ** 2))
         + 420 * np.exp(-(((r - 0.14 - 0.42 * ld) / 0.1) ** 2 + ((ld - 0.45) / 0.34) ** 2)))
    hits = np.where(h < 5, 0, np.round(h)).astype(np.int32)
    return fuel, ign, trim, hits


def demo_stall_paths():
    """Three sample approaches for the Stall paths view (mockup hl3dBuild)."""
    out = []
    q = np.arange(40)
    s = q / 39.0
    for p in range(3):
        rpm = (3300 - p * 350 - (2700 - p * 350) * s ** 0.8
               - np.where(s > 0.85, (s - 0.85) * 2600, 0) + 60 * np.sin(q * 0.9 + p))
        kpa = (62 - 34 * np.sin(np.minimum(1, s * 1.5) * np.pi / 2)
               + np.where(s > 0.72, (s - 0.72) * 235, 0) + p * 2.5)
        out.append((f"stall {p + 1}", np.maximum(500, rpm), np.minimum(101, kpa)))
    return out


# ---- raw tools / mapper -----------------------------------------------------

OBD2_ONLY = ("OBD2 only: this ECU answers standard OBD2 (ISO 9141-2), not Honda reads, so the "
             "sweep and the console are off. Live values, codes and readiness still work.")

class PollerMapper:
    """Memory sweep and hex console through Poller.submit, plus a small guided
    wizard (stopgap for W3's): baseline, three timed presses, Pearson ranking."""

    BASELINE_S = 5.0
    PHASE_S = 3.0               # press 3 s, release 3 s, three times
    HIST = 120

    def __init__(self, ecu):
        self.ecu = ecu
        self.cur = np.full(256, -1, dtype=np.int16)
        self.fresh = np.zeros(256, dtype=bool)
        self.changes = np.zeros(256, dtype=np.int32)
        self.minv = np.full(256, 255, dtype=np.int16)
        self.maxv = np.zeros(256, dtype=np.int16)
        self.sweeps = 0
        self.sweep_s = 0.0
        self.sweeping = False
        self.revision = 0
        self._hist = np.full((self.HIST, 256), -1, dtype=np.int16)
        self._sweep_t: collections.deque = collections.deque(maxlen=self.HIST)     # when each row was finished
        self.map_revision = 0           # bumps when mapped() would answer differently
        self._pending: list = []
        self._left = 0
        self._t0 = 0.0
        self._console = ""
        self._poller = None
        self._obd2 = False
        # wizard
        self._step = 0
        self._target = 0
        self._phase_t0 = 0.0
        self._noise = np.zeros(256, dtype=bool)
        self._base_changes = None
        self._trace: list = []          # (t, 256 values) during the stimulus
        self._cands: list[ad.Candidate] = []
        self._picked: int | None = None

    # -- sweep --

    def available(self) -> bool:
        return getattr(self.ecu, "poller", None) is not None and not self.obd2_only()

    def obd2_only(self) -> bool:
        """The link runs standard OBD2 PIDs: raw Honda reads are refused."""
        return getattr(getattr(self.ecu, "poller", None), "mode", "") == pl.OBD2

    def set_sweeping(self, on: bool) -> None:
        self.sweeping = bool(on) and self.available()
        if on and self.obd2_only():
            self._console = OBD2_ONLY
        self.revision += 1
        if self.sweeping and not self._left:
            self._start_sweep()

    def _start_sweep(self) -> None:
        poller = self.ecu.poller
        if poller is None:
            self.sweeping = False
            return
        self._poller = poller
        self._t0 = time.monotonic()
        self.fresh[:] = False
        self._left = 16
        for addr in range(0, 256, 16):
            fut = poller.submit(lambda link, a=addr: link.read_mem(a, 16))
            self._pending.append((fut, "scan", addr))

    def poll(self) -> None:
        obd2 = self.obd2_only()
        if obd2 != self._obd2:
            self._obd2 = obd2
            if obd2:                        # the link went OBD2-only: Honda jobs would only fail
                self._pending.clear()
                self._left = 0
                self.sweeping = False
                self._console = OBD2_ONLY
            self.revision += 1
        if self.ecu.poller is not self._poller and self._pending:
            self._pending.clear()           # link went away under us
            self._left = 0
            self.sweeping = False
            self.revision += 1
        while self._pending and self._pending[0][0].done():
            fut, kind, info = self._pending.pop(0)
            if kind == "scan":
                self._chunk(info, fut)
            else:
                self._console_done(info, fut)
        self._wizard_tick()

    def _chunk(self, addr: int, fut) -> None:
        try:
            data = fut.result()
        except ProtocolError:
            pass
        except Exception as exc:
            self.sweeping, self._left = False, 0
            self._console = f"sweep stopped: {exc}"
            self.revision += 1
            return
        else:
            new = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
            sl = slice(addr, addr + len(new))
            old = self.cur[sl]
            changed = (old >= 0) & (old != new)
            self.fresh[sl] = changed
            self.changes[sl] += changed
            self.minv[sl] = np.minimum(self.minv[sl], new)
            self.maxv[sl] = np.maximum(self.maxv[sl], new)
            self.cur[sl] = new
        self._left -= 1
        if self._left <= 0:
            self.sweeps += 1
            self.sweep_s = time.monotonic() - self._t0
            self._hist[:-1] = self._hist[1:]
            self._hist[-1] = self.cur
            self._sweep_t.append(time.monotonic())
            if self._step == 2:
                self._trace.append((time.monotonic(), self.cur.copy()))
            self.revision += 1
            if self.sweeping:
                self._start_sweep()

    def clear_changes(self) -> None:
        self.changes[:] = 0
        self.fresh[:] = False
        known = self.cur >= 0
        self.minv[:], self.maxv[:] = 255, 0
        self.minv[known] = self.maxv[known] = self.cur[known]
        self._hist[:] = -1
        self._sweep_t.clear()
        self.sweeps = 0
        self.revision += 1

    # -- the correlation finder on the sweep history ("Rank addresses") --

    SHAPES = (("shape:rise", "Rises steadily"), ("shape:fall", "Falls steadily"))
    MIN_SWEEPS = 5

    def sweep_recording(self):
        """The grid's sweep history as a W3 ``SweepRecording`` (oldest first)."""
        from ..mapping.sweeps import from_rows
        rows = [row for row in self._hist if (row >= 0).any()]
        return from_rows(rows, list(self._sweep_t)[-len(rows):] if rows else [])

    def _reference_channels(self) -> list:
        """(name, label, verified) of the channels that can serve as a reference."""
        amap = getattr(self.ecu, "amap", None)
        if amap is None:
            return []
        return [(c.name, c.label, bool(c.verified)) for c in amap.channels if c.kind == "value"]

    def rank_references(self) -> list[ad.RankReference]:
        """What addresses can be ranked against: the map's channels (proven
        ones first: a reference is only as good as its own address) and the
        two shapes the user can make by hand."""
        chans = sorted(self._reference_channels(), key=lambda c: not c[2])
        return ([ad.RankReference(name, label, "channel", verified) for name, label, verified in chans]
                + [ad.RankReference(key, label, "shape") for key, label in self.SHAPES])

    def _rank_channel(self, rec, name: str, top: int) -> list:
        from ..mapping import correlate
        return correlate.rank_against_channel(rec, self.ecu.amap, name, top=top)

    def rank(self, reference: str, top: int = 8) -> list[ad.RankRow]:
        """W3's correlation finder on the sweeps so far. ``reference`` is a
        channel name (which other addresses moved with it?) or "shape:rise" /
        "shape:fall" (which addresses moved steadily one way since the changes
        were cleared?). Best first; when the list is empty ``console()`` says why."""
        from ..mapping import correlate
        rec = self.sweep_recording()
        if len(rec) < self.MIN_SWEEPS:
            self._console = (f"{len(rec)} sweeps so far: sweep for a few seconds while the "
                             "signal changes, then rank")
            self.revision += 1
            return []
        try:
            if reference.startswith("shape:"):
                t = rec.t
                # rho comes back positive when the raw value moves the way the shape says
                matches = correlate.rank_shape(rec, reference.split(":", 1)[1], float(t[0]), float(t[-1]),
                                               top=top)
            else:
                matches = self._rank_channel(rec, reference, top)
        except (addressmap.MapError, KeyError, ValueError) as exc:
            self._console = f"!! {exc.args[0] if exc.args else exc}"
            self.revision += 1
            return []
        rows = []
        for m in matches:
            c = m.candidate
            info = self.mapped(c.addr)
            span = range(c.addr, min(256, c.addr + c.width))
            shape = reference.startswith("shape:")   # a fit to a made-up ramp is no formula
            rows.append(ad.RankRow(
                c.addr, c.width, c.endian, c.inverse, c.label, float(m.score),
                float(m.rho if shape else m.r), "" if shape else m.formula,
                int(min(self.minv[a] for a in span)), int(max(self.maxv[a] for a in span)),
                info[0] if info else "", "; ".join(m.notes)))
        self._console = ("  ·  ".join(f"{r.label} {r.score:.2f}" for r in rows[:4])
                         or "nothing moved with it in these sweeps")
        self.revision += 1
        return rows

    def rank_against(self, channel: str, top: int = 5) -> list[ad.Candidate]:
        """The short form: (address, score, range) of what follows a channel."""
        return [ad.Candidate(r.addr, r.score, r.lo, r.hi) for r in self.rank(channel, top)]

    def assign_targets(self) -> list[tuple[str, str]]:
        """(channel name, text) of the channels a ranked address can be given to."""
        amap = getattr(self.ecu, "amap", None)
        if amap is None:
            return []
        return [(c.name, f"{c.label} ({c.name})  now 0x{c.addr:02X}" + ("" if not c.verified else "  verified"))
                for c in amap.channels]

    def assign(self, row: ad.RankRow, channel: str) -> str:
        """Give the ranked address to a channel of the map. Here: not possible
        (no map file behind this session). Returns a line for the user."""
        return "This session has no address map file to write to"

    def history(self, addr: int) -> np.ndarray:
        col = self._hist[:, addr]
        return col[col >= 0]

    def mapped(self, addr: int):
        amap = getattr(self.ecu, "amap", None)
        if amap is None:
            return None
        for ch in amap.channels:
            if ch.addr <= addr < ch.addr + ch.length:
                return ch.name, ch.verified
        return None

    # -- console --

    def send(self, data: bytes, add_checksum: bool) -> None:
        poller = self.ecu.poller if self.available() else None
        if poller is None or not data:
            self._console = OBD2_ONLY if self.obd2_only() else "not connected"
            self.revision += 1
            return
        if add_checksum:
            data = protocol.with_checksum(data)
        self._poller = poller
        fut = poller.submit(lambda link, d=data: link.raw(d))
        self._pending.append((fut, "console", (data, time.monotonic())))

    def _console_done(self, info, fut) -> None:
        data, t0 = info
        ms = (time.monotonic() - t0) * 1000
        try:
            echo, rest = fut.result()
        except Exception as exc:
            self._console = f"!! {exc}"
            self.revision += 1
            return
        framing = self.ecu.amap.framing if self.ecu.amap else protocol.DEFAULT
        if not rest:
            note = "no reply" + ("" if echo else ", no echo: check cable and 12 V")
        elif protocol.find_any_frame(rest, framing):
            note = "checksum OK"
        else:
            note = "no valid frame"
        self._console = (f"{protocol.fmt_hex(rest) or '(nothing)'}  ·  {note} · "
                         f"{ms:.0f} ms")
        self.revision += 1

    def console(self) -> str:
        return self._console

    def export(self, path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# hondalog memory sweep, {datetime.now():%Y-%m-%d %H:%M:%S}, "
                     f"{self.sweeps} sweeps\n# addr value min max changes\n")
            for a in range(256):
                if self.cur[a] >= 0:
                    fh.write(f"0x{a:02X} {int(self.cur[a]):02X} {int(self.minv[a])} "
                             f"{int(self.maxv[a])} {int(self.changes[a])}\n")

    # -- wizard --

    def _targets(self) -> list:
        amap = getattr(self.ecu, "amap", None)
        if amap is None:
            return []
        return [c for c in amap.channels if not c.verified and c.kind == "value"]

    def wizard_pick_target(self, index: int) -> None:
        if self._step == 0 and 0 <= index < len(self._targets()):
            self._target = index
            self.revision += 1

    def _target_name(self) -> str:
        targets = self._targets()
        if not targets:
            return ""
        ch = targets[min(self._target, len(targets) - 1)]
        return f"{ch.label} ({ch.name})"

    def wizard(self) -> ad.WizardView:
        w = ad.WizardView(step=self._step, target=self._target_name(), can_back=self._step > 0)
        now = time.monotonic()
        if self._step == 0:
            w.title = "Pick what you are looking for"
            w.body = ("One channel at a time. Throttle is a good first one: you control it "
                      "and nothing else moves with it. Click a row, then Next.")
            w.column = "CHANNEL"
            w.targets = [f"{c.label} ({c.name})  now 0x{c.addr:02X}" for c in self._targets()]
            w.target_index = self._target
            w.primary = "Next" if self.available() else "Connect first"
        elif self._step == 1:
            w.title = "Hold still for 5 seconds"
            w.body = ("Foot off everything. Bytes that move now are noise and are ruled out.")
            w.busy = min(1.0, (now - self._phase_t0) / self.BASELINE_S)
            w.primary = "Waiting…"
        elif self._step == 2:
            el = now - self._phase_t0
            phase = int(el / self.PHASE_S)
            press = phase % 2 == 0
            w.title = (f"PRESS and hold  ({phase // 2 + 1} of 3)" if press
                       else f"RELEASE  ({phase // 2 + 1} of 3)")
            w.body = ("Press slowly to the floor when it says PRESS, let go when it says "
                      "RELEASE. Key on, engine off is fine.")
            w.busy = min(1.0, el / (6 * self.PHASE_S))
            w.primary = "Recording…"
        elif self._step == 3:
            w.title = ("Pick the byte that follows your foot" if self._cands
                       else "Nothing followed the stimulus")
            w.body = ("Ranked by how closely each byte followed the three presses. Click a "
                      "row to inspect it in the grid." if self._cands else
                      "No byte moved with the presses. Check the sweep is running, then "
                      "try again.")
            w.candidates = self._cands
            sel = self._picked if self._picked is not None else (
                self._cands[0].addr if self._cands else None)
            w.primary = f"Use 0x{sel:02X}" if sel is not None else "Try again"
        else:
            a = self._picked or 0
            w.title = "Confirm the scaling"
            w.body = (f"0x{a:02X} read {int(self.minv[a])} at rest and {int(self.maxv[a])} "
                      f"pressed. Put addr = 0x{a:02X} in this channel's entry in "
                      "addressmap.toml, fix the formula, set status = \"verified\", "
                      "then reconnect.")
            w.primary = "Done"
        return w

    def wizard_next(self, selected_addr: int | None = None) -> None:
        if self._step == 0:
            if not self.available():
                return
            self.set_sweeping(True)
            self._base_changes = self.changes.copy()
            self._step, self._phase_t0 = 1, time.monotonic()
        elif self._step == 3:
            if not self._cands:
                return self.wizard_retry()
            valid = {c.addr for c in self._cands}
            self._picked = selected_addr if selected_addr in valid else self._cands[0].addr
            self._step = 4
        elif self._step == 4:
            self._step = 0
        self.revision += 1

    def wizard_back(self) -> None:
        if self._step in (1, 2):
            self._step = 0
        elif self._step > 0:
            self._step -= 1 if self._step == 4 else 3
        self._step = max(0, self._step)
        self.revision += 1

    def wizard_retry(self) -> None:
        if self._step > 0 and self.available():
            self._step = 0
            self.wizard_next()

    def _wizard_tick(self) -> None:
        now = time.monotonic()
        if self._step == 1 and now - self._phase_t0 >= self.BASELINE_S:
            self._noise = (self.changes - self._base_changes) > 0
            self._trace = []
            self._step, self._phase_t0 = 2, now
        elif self._step == 2 and now - self._phase_t0 >= 6 * self.PHASE_S:
            self._cands = rank_candidates(self._trace, self._phase_t0, self.PHASE_S,
                                          self._noise)
            self._picked = None
            self._step = 3


def rank_candidates(trace, t0: float, phase_s: float, noise, top: int = 4):
    """Pearson correlation of every byte with the expected press/release shape."""
    if len(trace) < 4:
        return []
    t = np.array([row[0] for row in trace]) - t0
    mem = np.array([row[1] for row in trace], dtype=np.float64)       # (n, 256)
    expect = ((t / phase_s).astype(int) % 2 == 0).astype(np.float64)
    e = expect - expect.mean()
    if not e.any():
        return []
    m = mem - mem.mean(axis=0)
    denom = np.sqrt((m ** 2).sum(axis=0) * (e ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(denom > 0, (m * e[:, None]).sum(axis=0) / denom, 0.0)
    r = np.abs(r)
    r[np.asarray(noise, dtype=bool)] = 0.0
    r[(mem < 0).any(axis=0)] = 0.0
    order = [int(a) for a in np.argsort(-r)[:top] if r[a] > 0.2]
    return [ad.Candidate(a, float(r[a]), int(mem[:, a].min()), int(mem[:, a].max()))
            for a in order]


class DemoMapper(PollerMapper):
    """Demo mode: a made-up RAM that follows the demo car (hlMem in the mockups)."""

    def __init__(self, ecu):
        super().__init__(ecu)
        i = np.arange(256)
        self._ram = np.where((i * 73 + 41) % 251 > 196, (i * 37 + 11) % 256, 0).astype(np.int16)
        self.demo_map = {0x00: ("rpm", True), 0x01: ("rpm", True), 0x02: ("vss", False),
                         0x10: ("ect", False), 0x11: ("iat", False), 0x12: ("map", False),
                         0x14: ("tps", True), 0x15: ("o2", False), 0x17: ("batt", True),
                         0x20: ("stft", False), 0x22: ("ltft", False), 0x24: ("inj", False),
                         0x25: ("inj", False), 0x26: ("ign", False), 0x28: ("iacv", False)}
        self._next = 0.0

    def available(self) -> bool:
        return True

    def mapped(self, addr: int):
        return self.demo_map.get(addr)

    def _targets(self) -> list:
        from types import SimpleNamespace as Ch
        return [Ch(label="Speed", name="vss", addr=0x02), Ch(label="MAP", name="map", addr=0x12),
                Ch(label="O2", name="o2", addr=0x15), Ch(label="Coolant", name="ect", addr=0x10)]

    # the finder in the demo: its made-up map is the dictionary above
    _DEMO_LABELS = {"rpm": "RPM", "vss": "Speed", "ect": "Coolant", "iat": "Intake air", "map": "MAP",
                    "tps": "Throttle", "o2": "O2", "batt": "Battery", "stft": "Short trim",
                    "ltft": "Long trim", "inj": "Injector", "ign": "Ignition", "iacv": "Idle valve"}

    def _channel_bytes(self, name: str) -> list[int]:
        return sorted(a for a, (n, _) in self.demo_map.items() if n == name)

    def _reference_channels(self) -> list:
        names = dict.fromkeys(n for n, _ in self.demo_map.values())
        return [(n, self._DEMO_LABELS.get(n, n), all(v for m, v in self.demo_map.values() if m == n))
                for n in names]

    def _rank_channel(self, rec, name: str, top: int) -> list:
        from ..mapping import correlate
        addrs = self._channel_bytes(name)
        if not addrs:
            raise KeyError(f"no channel named {name!r}")
        mem, times = rec.data
        value = sum(mem[:, a].astype(float) * 256 ** (len(addrs) - 1 - k) for k, a in enumerate(addrs))
        return correlate.rank(rec, times[:, addrs[0]], value, top=top, exclude=addrs)

    def assign_targets(self) -> list[tuple[str, str]]:
        out = []
        for name, label, verified in self._reference_channels():
            addr = self._channel_bytes(name)[0]
            out.append((name, f"{label} ({name})  now 0x{addr:02X}" + ("  verified" if verified else "")))
        return out

    def assign(self, row: ad.RankRow, channel: str) -> str:
        if not self._channel_bytes(channel):
            return f"no channel named {channel!r}"
        self.demo_map = {a: v for a, v in self.demo_map.items() if v[0] != channel}
        for k in range(row.width):
            self.demo_map[row.addr + k] = (channel, False)
        self.map_revision += 1
        self.revision += 1
        return f"{channel} now reads {row.label} (demo map, unverified)"

    def _start_sweep(self) -> None:
        self._t0 = time.monotonic()
        self._next = self._t0 + 0.3
        self._left = 1

    def poll(self) -> None:
        if self.sweeping and self._left and time.monotonic() >= self._next:
            clock = getattr(self.ecu, "clock", None)
            t = clock() if clock else self.ecu.bus.now()
            self.fill(t + 0.9 * self.sweeps)
        self._wizard_tick()

    def fill(self, t: float) -> None:
        """One synthetic sweep at demo time t."""
        c = demo_values(t)
        ram = self._ram
        rw, iw = int(c["rpm"]), int(c["inj"] * 250)
        for addr, x in ((0, rw >> 8), (1, rw & 255), (2, c["vss"]), (0x10, 200 - c["ect"]),
                        (0x11, 200 - c["iat"] * 1.4), (0x12, c["map"] * 2.2), (0x13, 222),
                        (0x14, c["tps"] * 2.55), (0x15, c["o2"] * 51), (0x17, c["batt"] * 10),
                        (0x18, c["alt_fr"] * 2.55), (0x19, c["eld"] * 2),
                        (0x20, 128 + c["stft"] * 2), (0x22, 128 + c["ltft"] * 2),
                        (0x24, iw >> 8), (0x25, iw & 255), (0x26, 40 + c["ign"] * 2),
                        (0x28, c["iacv"] * 2.55), (0x30, int(t * 4) & 255),
                        (0x44, int(t) & 255), (0x5A, 25 + c["tps"] * 2.3),
                        (0x76, 14 + c["tps"] * 0.4)):
            ram[addr] = max(0, min(255, int(round(x))))
        self._left = 16
        for addr in range(0, 256, 16):
            done = Future()
            done.set_result(bytes(int(b) for b in ram[addr:addr + 16]))
            self._chunk(addr, done)

    def send(self, data: bytes, add_checksum: bool) -> None:
        self._console = "demo mode: nothing to send to"
        self.revision += 1


# ---- tuning tables (stopgap sample tables) ----------------------------------

class DemoTableModel:
    """Sample 20 x 10 tables in memory. Read-only unless writable=True."""

    def __init__(self, writable: bool = False):
        self.rpm_axis = np.round((500 + np.arange(20) * 7000 / 19) / 50) * 50
        self.map_axis = np.round(17 + np.arange(10) * 84 / 9)
        fuel, ign, _, _ = demo_tables(20, 10)
        fuel, ign = fuel.T.copy(), ign.T.copy()             # (n_rpm, n_map)
        k = np.arange(20)[:, None] / 20.0
        self._tables = {"fl": fuel, "fh": fuel * (1.02 + 0.1 * k), "il": ign, "ih": ign - 2.5}
        self._base = {k: v.copy() for k, v in self._tables.items()}
        self._info = [
            ad.TableInfo("fl", "Fuel · low cam", "ms", "INJ ms", 2, 0.05, 0, 10),
            ad.TableInfo("fh", "Fuel · VTEC", "ms", "INJ ms", 2, 0.05, 0, 10),
            ad.TableInfo("il", "Ignition · low cam", "°BTDC", "IGN °BTDC", 1, 0.5, -5, 45),
            ad.TableInfo("ih", "Ignition · VTEC", "°BTDC", "IGN °BTDC", 1, 0.5, -5, 45),
        ]
        self.writable = writable
        self.read_only_reason = ("" if writable else
                                 "Sample tables, read-only. The stock P2P cannot be "
                                 "written: this screen unlocks with the chipped P28.")
        self.revision = 0
        self._undo: list = []

    def tables(self):
        return self._info

    def values(self, table_id: str) -> np.ndarray:
        return self._tables[table_id]

    def base(self, table_id: str) -> np.ndarray:
        return self._base[table_id]

    def _edit(self, table_id: str) -> np.ndarray | None:
        if not self.writable:
            return None
        self._undo.append((table_id, self._tables[table_id].copy()))
        del self._undo[:-200]
        self.revision += 1
        return self._tables[table_id]

    def adjust(self, table_id, cells, steps) -> None:
        table = self._edit(table_id)
        if table is None:
            return
        step = next(i.step for i in self._info if i.id == table_id)
        for r, c in cells:
            table[r, c] += steps * step

    @staticmethod
    def _box(cells):
        rows = [r for r, _ in cells]
        cols = [c for _, c in cells]
        return min(rows), max(rows), min(cols), max(cols)

    def interpolate(self, table_id, cells) -> None:
        if len(cells) < 3:
            return
        table = self._edit(table_id)
        if table is None:
            return
        r0, r1, c0, c1 = self._box(cells)
        for r in range(r0, r1 + 1):
            a = (r - r0) / (r1 - r0) if r1 > r0 else 0.0
            for c in range(c0, c1 + 1):
                b = (c - c0) / (c1 - c0) if c1 > c0 else 0.0
                table[r, c] = ((table[r0, c0] * (1 - b) + table[r0, c1] * b) * (1 - a)
                               + (table[r1, c0] * (1 - b) + table[r1, c1] * b) * a)

    def smooth(self, table_id, cells) -> None:
        table = self._edit(table_id)
        if table is None:
            return
        src = table.copy()
        nr, nc = src.shape
        for r, c in cells:
            block = src[max(0, r - 1):min(nr, r + 2), max(0, c - 1):min(nc, c + 2)]
            table[r, c] = 0.5 * src[r, c] + 0.5 * block.mean()

    def undo(self) -> bool:
        if not self._undo:
            return False
        table_id, old = self._undo.pop()
        self._tables[table_id][:] = old
        self.revision += 1
        return True

    def save(self, path) -> None:
        """Stopgap dump (float32, table after table). Not a ROM image."""
        with open(path, "wb") as fh:
            for info in self._info:
                fh.write(self._tables[info.id].astype("<f4").tobytes())

    def can_write_chip(self) -> bool:
        return False
