"""Session: the non-widget half of the app.

Owns the ChannelBus and one object per adapter protocol, and turns what they
report into a short list of events for the shell (alarm started, stall
caught, message). The screens read the bus and the adapters through this
object and never import a backend module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import adapters as ad
from .bus import ChannelBus

# Row-major order of the ECU grid (HANDOFF 5.1). Anything else a source
# delivers (math channels, new map entries) is appended.
ECU_ORDER = ("rpm", "vss", "map", "tps", "inj", "ign", "o2", "stft", "ltft", "ect", "iat",
             "baro", "batt", "alt_fr", "eld", "iacv", "knock", "ign_limit", "closed_loop",
             "vtec", "ac_sw", "fan", "fuel_pump", "mil")
DRIVE_DEFAULT = ("rpm", "vss", "ect", "batt", "fpv", "stft")


@dataclass
class UiEvent:
    kind: str               # "alarm", "stall", "message"
    text: str = ""
    payload: object = None


class Session:
    def __init__(self, ecu, blackbox, alarms, recording, logs, obsmap, mapper, tables,
                 demo: bool = False, auto_record: bool = True, extra_sources=()):
        self.bus = ChannelBus()
        self.ecu: ad.ChannelSource = ecu
        self.blackbox: ad.ChannelSource = blackbox
        #: more ChannelSources (math channels): started and stopped with the rest
        self.extra_sources: list[ad.ChannelSource] = list(extra_sources)
        self.alarms: ad.AlarmSource = alarms
        self.recording: ad.Recording = recording
        self.logs: ad.LogReader = logs
        self.obsmap: ad.ObservedMap = obsmap
        self.mapper: ad.MapperSession = mapper
        self.tables: ad.TableModel = tables
        self.demo = demo
        self.auto_record = auto_record
        self.ecu_status = ad.SourceStatus()
        self.bb_status = ad.SourceStatus()
        self.stall = ad.StallStatus()
        self.banner: ad.StallCapture | None = None      # stall waiting to be dismissed
        self.banner_cause = ""
        self._status_t = -1.0
        self._was_ok = False
        self._started = False

    # ---- lifecycle ----

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.ecu.start(self.bus)
            self.blackbox.start(self.bus)
            for source in self.extra_sources:
                source.start(self.bus)

    def close(self) -> None:
        self.recording.stop()
        for source in self.extra_sources:
            source.stop()
        self.blackbox.stop()
        self.ecu.stop()

    # ---- connection ----

    @property
    def port(self) -> str:
        return getattr(self.ecu, "port", "")

    @property
    def connected(self) -> bool:
        return self.ecu_status.state != ad.OFF

    def ports(self):
        return self.ecu.ports() if isinstance(self.ecu, ad.Connectable) else []

    def connect(self, port: str) -> None:
        if isinstance(self.ecu, ad.Connectable):
            self._was_ok = False
            self.ecu.connect(port)
            self._status_t = -1.0

    def disconnect(self) -> None:
        self.recording.stop()
        if isinstance(self.ecu, ad.Connectable):
            self.ecu.disconnect()
        self._status_t = -1.0

    # ---- actions ----

    def mark(self) -> str:
        n = self.recording.mark()
        if not n:
            return "Not recording: marker ignored"
        for src in (self.ecu, self.blackbox):
            note = getattr(src, "note_mark", None)
            if note:
                note()
        return f"Marker {n} dropped"

    def toggle_recording(self) -> str:
        if self.recording.active:
            path = self.recording.path
            self.recording.stop()
            return f"Saved {path}" if path else "Recording stopped"
        problem = self.recording.start()
        return problem or (f"Recording to {self.recording.path}" if self.recording.path
                           else "Recording")

    def acknowledge(self) -> None:
        self.alarms.acknowledge()

    def dismiss_banner(self) -> None:
        self.banner = None

    # ---- layout helpers ----

    def ordered(self, source: str) -> list:
        states = self.bus.channels(source)
        if source != "ECU":
            return states
        rank = {cid: i for i, cid in enumerate(ECU_ORDER)}
        return sorted(states, key=lambda s: rank.get(s.spec.id, len(rank)))

    def counts(self) -> tuple[int, int]:
        chans = [s for s in self.bus.channels() if s.spec.mapped]
        return len(chans), sum(1 for s in chans if not s.spec.verified)

    def map_name(self) -> str:
        amap = getattr(self.ecu, "amap", None)
        return amap.version.split(" ")[0] if amap is not None else "demo"

    def stall_paths(self, limit: int = 3) -> list:
        """(name, rpm array, kPa array) for the last 30 s of recent stall files."""
        if self.demo:
            from .backends import demo_stall_paths
            return demo_stall_paths()
        out = []
        for path in self.logs.recent(stalls_only=True):
            try:
                view = self.logs.read(path)
            except (OSError, ValueError):
                continue
            if "rpm" not in view.data or "map" not in view.data or view.trigger_t is None:
                continue
            t = view.t["rpm"]
            keep = (t >= -30.0) & (t <= 0.0)
            # RPM and MAP are read in different blocks, each at its own times:
            # take the MAP value that was current at each RPM read
            tm = view.t["map"]
            if not len(tm):
                continue
            at = np.clip(np.searchsorted(tm, t[keep], side="right") - 1, 0, len(tm) - 1)
            out.append((path.name, view.data["rpm"][keep], view.data["map"][at]))
            if len(out) >= limit:
                break
        return out

    # ---- once per UI frame ----

    def pump(self, now: float) -> list[UiEvent]:
        events: list[UiEvent] = []
        bus = self.bus
        bus.tick(now)
        # W3's alarm rules say themselves whether they want a marker (and the
        # live path drops it); the plain limit checker gets one from here
        auto_mark = not getattr(self.alarms, "marks_itself", False)
        for alarm in self.alarms.evaluate(bus, now):
            # the marker goes into the log: in the map's own units, whatever the screen shows
            n = (self.recording.mark(f"ALARM {alarm.source} {alarm.label} "
                                     f"{alarm.log_text or alarm.limit_text}")
                 if auto_mark or alarm.source == "BB" else 0)
            events.append(UiEvent("alarm", f"{alarm.source} {alarm.label} {alarm.value_text}"
                                           f" — {alarm.limit_text}"
                                           + (f" (auto-mark {n})" if n else ""), alarm))
        self.obsmap.update(bus)
        self.mapper.poll()
        if now - self._status_t >= 0.25:
            self._status_t = now
            self.ecu_status = self.ecu.status()
            self.bb_status = self.blackbox.status()
            self.stall = self.recording.stall()
            if self.banner is not None and self.stall.state != "saving":
                self.stall.state = "triggered"
            ok = self.ecu_status.state == ad.OK
            if ok and not self._was_ok and self.auto_record and not self.recording.active:
                problem = self.recording.start()
                events.append(UiEvent("message", problem or
                                      f"Recording to {self.recording.path}"))
            self._was_ok = self._was_ok or ok
            poller = getattr(self.ecu, "poller", None)
            if poller is not None and not poller.is_alive() and poller.ready.is_set():
                error = poller.last_error
                self.disconnect()
                events.append(UiEvent("message", f"Link stopped: {error}"))
            for cap in self.recording.poll():
                cause = {"rpm": "RPM dropped to 0.", "link": "ECU went silent."}.get(
                    cap.reason) or (cap.message[:1].upper() + cap.message[1:] + "."
                                    if cap.message else "Capture saved.")
                try:
                    summary = self.logs.analyze(self.logs.read(cap.path)).summary
                except (OSError, ValueError):
                    summary = ""
                self.banner = cap
                self.banner_cause = " ".join(x for x in (cause, summary,
                                                         f"Saved {getattr(cap.path, 'name', cap.path)}") if x)
                events.append(UiEvent("stall", self.banner_cause, cap))
        return events


def build_session(demo: bool = False, log_dir=None, writable_tables: bool = False,
                  demo_clock=None, rom=None, definition=None, blackbox_port=None) -> Session:
    """Wire one object per adapter role.

    Real use binds the tracks' backends (``wired.py``): the W1 link through
    ``hondalog.live.LiveSession``, W2's black box reader, W3's math channels,
    alarms and mapping tools, W4's recorder, trigger engine, log reader and
    histogram, W5's ROM tables. ``demo=True`` keeps the simulated car from
    ``backends.py``: nothing touches a port or writes a log.

    ``rom`` / ``definition`` open a ROM image for the Tune screen; without
    them (or if they cannot be read) the sample tables stay.
    """
    from . import backends as be

    if demo:
        ecu = be.DemoSource("ECU", demo_clock)
        blackbox = be.DemoSource("BB", demo_clock)
        recording = be.NullRecording(on_mark=None)
        obsmap = be.HistogramMap()
        obsmap.load_demo()
        session = Session(ecu, blackbox, be.LimitAlarms(), recording, None, obsmap,
                          be.DemoMapper(ecu), be.DemoTableModel(writable=True), demo=True)
        session.logs = be.CsvLogReader(_lookup(session), log_dir)
        return session

    from . import wired

    ecu = wired.LinkSource(out_dir=log_dir)
    tables = wired.RomTableModel(be.DemoTableModel(writable=writable_tables))
    if rom:
        tables.open(rom, definition)
    session = Session(ecu, wired.BlackboxSource(ecu, blackbox_port), wired.PipelineAlarms(ecu),
                      wired.LiveRecording(ecu, log_dir), None, wired.LogHistogramMap(tables),
                      wired.MappingSession(ecu), tables, extra_sources=[wired.MathSource(ecu)])
    session.logs = wired.DriveReader(_lookup(session), log_dir)
    return session


def _lookup(session: Session):
    def lookup(cid):
        state = session.bus.channel(cid)
        return state.spec if state is not None else None
    return lookup
