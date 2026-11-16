"""The adapters bound to the real backends (W1-W5).

``backends.py`` keeps the demo data and the small stand-ins the screens were
built against. This module is what ``build_session()`` wires for real use:

    LinkSource      ECU: hondalog.live.LiveSession over the W1 poller
                    (per-read publishing, per-channel rates, link counters,
                    expert unlock, first contact, verified marks in the map)
    MathSource      W3 math channels as the "MATH" source
    BlackboxSource  W2 BlackboxReader on the poller's time axis
    PipelineAlarms  W3 alarm engine (plus plain limits for the black box rails)
    LiveRecording   W4 Recorder + trigger engine through the LiveSession
    DriveReader     W4 open_log / ReplaySession on the drive database, .bbx through W2 open_event
    LogHistogramMap W4 TableHistogram, on the ROM table's axes when one is open
    MappingSession  W3 VerificationSession / correlate; the console goes
                    through W1's read-only guard
    RomTableModel   W5 RomSession / TableEditor; the demo tables stay the
                    fallback when no ROM or definition is loaded

No widgets in here. See docs/ui-adapters.md.
"""

from __future__ import annotations

import collections
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .. import addressmap
from .. import poller as pl
from ..alarms import CRITICAL
from ..alarms.engine import RAISED
from ..analysis import Bins, ReplaySession, TableHistogram, table_bins
from ..blackbox import BlackboxReader, bb_channel
from ..blackbox import list_ports as bb_list_ports
from ..blackbox.sim import BBSIM_PREFIX
from ..data import DriveRef, db_path, files_dir, open_log, parse_ref, write_log
from ..data.library import Library
from ..live import BLACKBOX_STREAM, LiveSession
from ..logger import LINK, log_dir
from ..mapping import correlate, mapedit
from ..mapping.verify import FAIL, PASS, PLAUSIBLE, VerificationSession, scripts_for
from ..transport import CommandBlocked
from . import adapters as ad
from . import backends as be
from .bus import ChannelBus, ChannelSpec

# ---- names ------------------------------------------------------------------

#: W2's channel name -> the UI's id, and how to turn the number into the UI's unit.
#: Tach and ignition trigger are shown as pulse frequency: a four-cylinder
#: gives two pulses per revolution, so Hz = rpm / 30.
BB_TO_UI: dict[str, tuple[str, float]] = {
    "bb_vbat": ("bbatt", 1.0), "bb_ecub": ("ecub", 1.0), "bb_pump": ("fpv", 1.0),
    "bb_ig1": ("ig1", 1.0), "bb_rpm_tach": ("tach", 1 / 30.0), "bb_rpm_ign": ("igt", 1 / 30.0),
    "bb_inj_pw": ("injp", 1.0), "bb_button": ("bbmark", 1.0),
}


def _alarm_limits(amap: Any) -> dict[str, dict[str, float]]:
    """warn/alarm bands per channel from the map's [[alarm]] rules (low/high)."""
    out: dict[str, dict[str, float]] = {}
    for rule in getattr(amap, "alarms", ()):
        if not rule.enabled or not rule.channel:
            continue
        prefix = "alarm" if rule.severity == CRITICAL else "warn"
        slot = out.setdefault(rule.channel, {})
        if rule.low is not None:
            slot[f"{prefix}_lo"] = rule.low
        if rule.high is not None:
            slot[f"{prefix}_hi"] = rule.high
    return out


# ---- ECU --------------------------------------------------------------------

class LinkSource(be.PollerSource):
    """ChannelSource + Connectable on the one live path (``hondalog.live``).

    Every block read is published as it arrives, so each channel moves at its
    own rate (fast / medium / slow) and carries its own age.
    """

    def __init__(self, load_map=None, out_dir=None):
        super().__init__(load_map)
        self.out_dir = out_dir
        self.live = LiveSession(self.amap, out_dir=out_dir)
        self.frames: collections.deque = collections.deque(maxlen=4000)   # for PipelineAlarms
        self.listeners: list = []           # objects with on_connect(source) / on_disconnect()
        self.first_contact_report = None
        self._fc_thread: threading.Thread | None = None
        self._fc_progress = ""

    # -- specs --

    def specs(self):
        limits = _alarm_limits(self.amap)
        out = []
        for ch in self.amap.channels:
            spec = be.spec_from_channel(ch)
            own = limits.get(ch.name)
            if own:                         # the map's own alarm rules beat the placeholders
                for key in ("warn_lo", "warn_hi", "alarm_lo", "alarm_hi"):
                    setattr(spec, key, own.get(key))
            out.append(spec)
        have = {s.id for s in out}
        # Rad fan / Fuel pump: real tiles as soon as the map has channels with
        # these names, placeholders (hatched, "not mapped") until then.
        return out + [p for p in be.PLACEHOLDERS if p.id not in have]

    def math_specs(self) -> list[ChannelSpec]:
        limits = _alarm_limits(self.amap)
        out = []
        for ch in self.live.pipeline.math.channels:
            spec = ChannelSpec(ch.name, ch.label, ch.units, ch.decimals, ch.min, ch.max, ch.kind,
                               source="MATH", verified=self.amap.is_verified(ch.name))
            for key, value in limits.get(ch.name, {}).items():
                setattr(spec, key, value)
            out.append(spec)
        return out

    # -- Connectable --

    def reload_map(self) -> None:
        """Read the map file again (after an edit or a verified mark)."""
        self.amap = self._load_map()
        if self.poller is None:
            self.live = LiveSession(self.amap, out_dir=self.out_dir)
        else:                               # connected: new labels and marks now, decoding on reconnect
            self.live.amap = self.amap
        if self.bus is not None:
            self.start(self.bus)
        for listener in self.listeners:
            hook = getattr(listener, "on_map", None)
            if hook:
                hook(self)

    def connect(self, port: str) -> None:
        if self.poller is not None or not port:
            return
        self.amap = self._load_map()
        self.live = LiveSession(self.amap, out_dir=self.out_dir)
        self.live.on_read = self._on_read
        self.live.on_frame = self._on_frame
        self.frames.clear()
        if self.bus is not None:
            self.start(self.bus)            # pick up an edited map
        self.port = port
        self._silent_since = None
        self.poller = self.live.connect(port)
        for listener in self.listeners:
            hook = getattr(listener, "on_map", None)
            if hook:
                hook(self)
            listener.on_connect(self)

    def disconnect(self) -> None:
        poller, self.poller = self.poller, None
        if poller is None:
            return
        for listener in self.listeners:
            listener.on_disconnect()
        self.live.close()

    def set_verified(self, channel_id: str, verified: bool) -> str:
        """Write the mark to the address map file, with a note of who said so."""
        path = Path(self.amap.path) if self.amap.path else None
        if self.amap.channel(channel_id) is None:
            return f"{channel_id} is worked out from other channels; verify those"
        if path is None or path == addressmap.DEFAULT_MAP:
            return "The shipped map is read-only: connect once so your own copy exists"
        evidence = addressmap.Evidence(method="manual", result="pass" if verified else "",
                                       summary="marked in the app by the user")
        try:
            mapedit.mark_channel(path, channel_id, evidence,
                                 addressmap.VERIFIED if verified else addressmap.UNVERIFIED,
                                 self.amap.variant or None)
        except (addressmap.MapError, OSError) as exc:
            return f"Could not write {path.name}: {exc}"
        self.reload_map()
        return f"{channel_id}: {'verified' if verified else 'unverified'}, saved in {path}"

    # -- W1 extras the screens reach through getattr --

    def channel_rates(self) -> dict:
        """name -> ChannelRate (class, target Hz, achieved Hz) from the scheduler."""
        return self.poller.channel_rates() if self.poller is not None else {}

    def link_text(self) -> str:
        """One line of link health for the info bar."""
        p = self.poller
        if p is None or p.state != pl.OK:
            return ""
        rates = p.class_rates()
        parts = [f"{name} {got:.0f}/{target:.0f} Hz" for name, (target, got) in rates.items()]
        s = p.stats
        parts.append(f"wire {100 * p.bus_load:.0f} %")
        parts.append(f"{s.timeouts} t/o {s.checksum_failures} cks {s.retries} retry")
        if not p.link.read_only:
            parts.append("EXPERT UNLOCK")
        return " · ".join(parts)

    @property
    def expert_unlock(self) -> bool:
        return self.poller is not None and not self.poller.link.read_only

    def set_expert_unlock(self, on: bool) -> str:
        """The hex console's tick box: let frames that are not reads onto the wire."""
        if self.poller is None:
            return "Not connected"
        self.poller.set_expert_unlock(bool(on))
        if self.live.recorder is not None:
            self.live.recorder.event("link", f"expert unlock {'ON' if on else 'off'}")
        return ("Expert unlock ON: the console sends anything. Every such frame is "
                "written to the log header." if on else "Read-only again")

    def first_contact_running(self) -> bool:
        return self._fc_thread is not None and self._fc_thread.is_alive()

    def first_contact(self, port: str, done=None) -> str:
        """Run hondalog-firstcontact on ``port`` in the background (the port
        must be free: disconnect first). Returns '' or why it did not start."""
        if self.poller is not None:
            return "Disconnect first: first contact needs the port to itself"
        if self.first_contact_running():
            return "First contact is already running"
        from ..diag import firstcontact

        def work() -> None:
            report = firstcontact.run(port, self.amap, progress=self._fc_note)
            path = Path(firstcontact.default_report_path(report.started))
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(report.to_markdown(), encoding="utf-8")
                if report.trace:
                    path.with_suffix(".hex.log").write_text(report.trace, encoding="utf-8")
                report.path = path          # type: ignore[attr-defined]
            except OSError:
                report.path = None          # type: ignore[attr-defined]
            self.first_contact_report = report
            self._fc_progress = f"first contact: {report.verdict}"
            if done is not None:
                done(report)

        self.first_contact_report = None
        self._fc_progress = "first contact: starting"
        self._fc_thread = threading.Thread(target=work, name="hondalog-firstcontact", daemon=True)
        self._fc_thread.start()
        return ""

    def _fc_note(self, text: str) -> None:
        self._fc_progress = f"first contact: {text}"

    def first_contact_progress(self) -> str:
        return self._fc_progress

    # -- poller thread --

    def _on_read(self, read) -> None:
        bus = self.bus
        if bus is not None:
            # t: when the ECU sampled it, on the bus clock. wire: when the last
            # reply byte arrived, which the link measures itself (BlockRead.mono).
            bus.publish(read.values, t=bus.bus_time(read.mono - read.latency_s),
                        wire=read.mono, source="ECU")

    def _on_frame(self, frame) -> None:
        bus, poller = self.bus, self.poller
        if frame.link and bus is not None and poller is not None:
            names = self.live.pipeline.math.names
            if names:
                bus.publish({n: frame.values.get(n) for n in names},
                            t=bus.bus_time(poller.t0 + frame.t), source="MATH")
        if frame.events:
            self.frames.append(frame)
        sink = self.sink
        if sink is not None and frame.sample is not None:
            sink(frame.sample)

    def status(self) -> ad.SourceStatus:
        st = super().status()
        if st.state == ad.OK:
            st.queue = len(self.live.frames)
            self.live.frames.clear()        # nobody drains them here: the bus is the UI's queue
            if self.bus is not None:
                self.bus.set_timeout("ECU", 3.5)        # slow channels are read once a second
                self.bus.set_timeout("MATH", 1.0)
        elif st.state == ad.OFF and self.first_contact_running():
            st.detail = self._fc_progress
        return st


class MathSource:
    """W3 math channels. The link publishes them (they are worked out on the
    poller thread, once per sample); this object only declares them."""

    name = "MATH"

    def __init__(self, ecu: LinkSource):
        self.ecu = ecu
        self.bus: ChannelBus | None = None
        ecu.listeners.append(self)

    def specs(self):
        return self.ecu.math_specs()

    def start(self, bus: ChannelBus) -> None:
        self.bus = bus
        bus.unregister_source("MATH")
        for spec in self.specs():
            bus.register(spec)

    def stop(self) -> None:
        pass

    def status(self) -> ad.SourceStatus:
        st = self.ecu.status()
        return ad.SourceStatus(st.state if st.state == ad.OK else ad.OFF, st.rate_hz,
                               simulated=st.simulated)

    def on_map(self, _ecu) -> None:
        if self.bus is not None:
            self.start(self.bus)

    def on_connect(self, _ecu) -> None: ...

    def on_disconnect(self) -> None: ...


# ---- black box --------------------------------------------------------------

class BlackboxSource:
    """W2's BlackboxReader, started next to the ECU link on the same t0.

    With a simulated ECU the built-in black box simulator is used; otherwise
    the udev name ``/dev/hondalog-bb`` (or ``port=``). No box: the chip says
    "not found" and the tiles stay hatched.
    """

    name = "BB"

    def __init__(self, ecu: LinkSource, port: str | None = None):
        self.ecu = ecu
        self.port = port
        self.bus: ChannelBus | None = None
        self.reader: BlackboxReader | None = None
        self.simulated = False
        self._detail = "not found"
        ecu.listeners.append(self)

    def specs(self):
        return [be.with_limits(s) for s in be.BLACKBOX_SPECS]

    def start(self, bus: ChannelBus) -> None:
        self.bus = bus
        for spec in self.specs():
            bus.register(spec)

    def stop(self) -> None:
        self.on_disconnect()

    def _pick_port(self) -> str | None:
        if self.port in ("off", "none"):
            return None
        if self.port:
            return self.port
        if be.is_sim_port(self.ecu.port) or self.ecu.port.startswith("sim:"):
            return BBSIM_PREFIX
        real = [dev for dev, _ in bb_list_ports() if not dev.startswith(BBSIM_PREFIX)
                and "bbsim" not in dev]
        return real[0] if real else None

    def on_map(self, _ecu) -> None: ...

    def on_connect(self, ecu: LinkSource) -> None:
        port = self._pick_port()
        if port is None:
            self._detail = "not found"
            return
        self.simulated = port.startswith(BBSIM_PREFIX) or "bbsim" in port
        ecu.live.on_blackbox = self._on_sample
        target: Any = port
        if port == BBSIM_PREFIX:
            # next to a simulated ECU: a simulated box in a car that is already running
            from ..blackbox import Car, SimDevice, SimPort
            # one ADC reading per millisecond instead of five: this box only feeds
            # the screen, and the full model cost more CPU than the UI next to it
            target = SimPort(SimDevice(Car(None, key_on_at=0.0, crank_at=0.02, start_at=0.1, sub=1)))
        try:
            self.reader = ecu.live.open_blackbox(target)
        except Exception as exc:            # a missing box must never stop the ECU link
            self.reader = None
            self._detail = str(exc)[:40]

    def on_disconnect(self) -> None:
        reader, self.reader = self.reader, None
        if reader is not None:
            reader.stop()

    def note_mark(self) -> None:
        """The marker goes to the box through LiveSession.mark (same number)."""

    def _on_sample(self, sample) -> None:
        bus, reader = self.bus, self.reader
        if bus is None or reader is None or not sample.link:
            return
        values = {}
        for name, (cid, scale) in BB_TO_UI.items():
            value = sample.values.get(name)
            if value is not None:
                values[cid] = value * scale
        bus.publish(values, t=bus.bus_time(reader.t0 + sample.t), source="BB")

    def status(self) -> ad.SourceStatus:
        reader = self.reader
        if reader is None:
            return ad.SourceStatus(ad.OFF, detail=self._detail)
        if reader.state == pl.OK:
            if self.bus is not None:
                self.bus.set_timeout("BB", 0.5)
            return ad.SourceStatus(ad.OK, reader.hz, reader.errors, simulated=self.simulated)
        if reader.state in (pl.NO_REPLY, pl.PORT_LOST):
            return ad.SourceStatus(ad.SILENT if reader.state == pl.NO_REPLY else ad.LOST,
                                   errors=reader.errors, detail=reader.last_error,
                                   simulated=self.simulated)
        return ad.SourceStatus(ad.CONNECTING, detail="connecting", simulated=self.simulated)


# ---- alarms -----------------------------------------------------------------

class PipelineAlarms:
    """AlarmSource on W3's alarm engine (``LivePipeline.alarms``).

    The engine runs on the poller thread once per sample; this object reads
    its state and turns the events the link collected into the UI's events.
    Channels the engine does not know (the black box rails) keep the plain
    limit check on their ChannelSpec.
    """

    marks_itself = True         # LiveSession drops the marker for "marker" alarms

    def __init__(self, ecu: LinkSource):
        self.ecu = ecu
        self._extra = be.LimitAlarms()
        self._events: dict[str, ad.AlarmEvent] = {}
        self._history: list[ad.AlarmEvent] = []
        self._rev = 0

    @property
    def engine(self):
        return self.ecu.live.pipeline.alarms

    @property
    def revision(self) -> int:
        return self._rev + self._extra.revision

    def _own(self, state) -> bool:
        return state.spec.source in ("ECU", "MATH")

    def _event(self, bus: ChannelBus, ev, now: float) -> ad.AlarmEvent:
        state = bus.channel(ev.channel) if ev.channel else None
        spec = state.spec if state is not None else None
        rule = next((r for r in self.engine.rules if r.name == ev.name), None)
        label = (spec.label if spec is not None else (rule.label if rule else ev.name)).upper()
        value = (f"{spec.format(ev.value)} {spec.units}".strip()
                 if spec is not None and ev.value is not None else "")
        return ad.AlarmEvent(ev.channel or ev.name, spec.source if spec is not None else "ECU",
                             label, "alarm" if ev.severity == CRITICAL else "warn", value,
                             ev.message + (" (unverified channel)" if ev.unverified else ""), now)

    def evaluate(self, bus: ChannelBus, now: float):
        started = []
        frames = self.ecu.frames
        while frames:
            for ev in frames.popleft().events:
                old = self._events.pop(ev.name, None)
                if ev.kind == RAISED:
                    event = self._events[ev.name] = self._event(bus, ev, now)
                    if event.severity == "alarm":
                        started.append(event)
                elif old is not None:
                    if ev.kind == "acknowledged" and self.engine.status(ev.name).active:
                        old.acked = True
                        self._events[ev.name] = old
                    else:
                        old.t_end = now
                        self._history.insert(0, old)
                        del self._history[50:]
                self._rev += 1
        started += self._extra.evaluate(_Filtered(bus, self._own), now)
        return started

    def state(self, channel_id: str) -> str:
        try:
            severity = self.engine.severity_for(channel_id)
        except KeyError:
            severity = None
        if severity is not None:
            return "alarm" if severity == CRITICAL else "warn"
        return self._extra.state(channel_id)

    def unacked(self, channel_id: str) -> bool:
        if any(e.channel_id == channel_id and e.severity == "alarm" and not e.acked
               for e in self._events.values()):
            return True
        return self._extra.unacked(channel_id)

    def active(self):
        events = list(self._events.values()) + list(self._extra.active())
        events.sort(key=lambda e: (e.severity != "alarm", -e.t_start))
        return events

    def history(self):
        return sorted(self._history + list(self._extra.history()), key=lambda e: -e.t_start)

    def acknowledge(self) -> None:
        self.ecu.live.acknowledge()         # under the pipeline's lock; logged when recording
        for event in self._events.values():
            if not event.acked:
                event.acked = True
                self._rev += 1
        self._extra.acknowledge()

    def limits(self):
        rows = []
        for rule in self.engine.rules:
            def lim(lo, hi, rule=rule):
                parts = [f"<{lo:g}" if lo is not None else "", f">{hi:g}" if hi is not None else ""]
                if rule.rate_low is not None or rule.rate_high is not None:
                    parts.append("rate")
                if rule.when:
                    parts.append("rule")
                return " ".join(x for x in parts if x).replace("-", "−") or "—"
            text = lim(rule.low, rule.high)
            critical = rule.severity == CRITICAL
            rows.append(ad.LimitRow(rule.label[:1].upper() + rule.label[1:],
                                    "—" if critical else text, text if critical else "—"))
        return rows + list(self._extra.limits())


class _Filtered:
    """A view of the bus without the channels the alarm engine already covers."""

    def __init__(self, bus: ChannelBus, skip):
        self._bus, self._skip = bus, skip

    @property
    def version(self):
        return self._bus.version

    def channels(self, source=None):
        return [s for s in self._bus.channels(source) if not self._skip(s)]


# ---- recording --------------------------------------------------------------

class LiveRecording:
    """Recording on the LiveSession: a drive in the database with raw bytes,
    math channels, the black box stream, alarms and the trigger engine.
    ``path`` is the drive (a ``DriveRef``); a capture is a ``DriveRef`` too."""

    def __init__(self, ecu: LinkSource, out_dir=None):
        self.ecu = ecu
        self.out_dir = out_dir
        self.rec = None
        self.path: DriveRef | None = None
        self.marks = 0
        self._t0 = 0.0
        self._last_mark: float | None = None
        self._count = 0
        self._last_file = ""
        self._pending: list[ad.StallCapture] = []
        ecu.listeners.append(self)

    @property
    def active(self) -> bool:
        return self.rec is not None

    def on_map(self, _ecu) -> None: ...

    def on_connect(self, _ecu) -> None: ...

    def on_disconnect(self) -> None:
        self.stop()                         # before the link closes: the header gets its counters

    def start(self) -> str:
        if self.rec is not None:
            return ""
        if self.ecu.poller is None:
            return "Not connected: nothing to record"
        try:
            self.rec = self.ecu.live.start_recording(self.out_dir)
        except OSError as exc:
            return f"Cannot write the database: {exc}"
        self.path, self.marks, self._t0 = self.rec.path, 0, time.monotonic()
        self._last_mark = None
        return ""

    def stop(self) -> None:
        rec, self.rec = self.rec, None
        if rec is None:
            return
        self.ecu.live.stop_recording()
        self.path = rec.path
        self._harvest(rec)

    def _harvest(self, rec) -> None:
        while not rec.events.empty():
            ev = rec.events.get_nowait()
            self._count += 1
            self._last_file = ev.path.name
            epoch = rec.header.get("epoch_unix")
            wall = datetime.fromtimestamp(epoch + ev.t) if epoch else datetime.now()
            self._pending.append(ad.StallCapture(ev.path, ev.reason, wall, ev.t, ev.message))

    def mark(self, text: str | None = None) -> int:
        if self.rec is None:
            return 0
        self.marks = self.ecu.live.mark(text)
        self._last_mark = time.monotonic()
        return self.marks

    def elapsed_s(self) -> float:
        return time.monotonic() - self._t0 if self.rec is not None else 0.0

    def last_mark_age_s(self):
        return None if self._last_mark is None else time.monotonic() - self._last_mark

    def stall(self) -> ad.StallStatus:
        rec = self.rec
        st = ad.StallStatus(count=self._count, last_file=self._last_file)
        if rec is None:
            return st
        status = rec.status()               # public: no private fields
        st.pre_s, st.post_s = status.pre_s, status.post_s
        st.buffer_s = status.buffer_s
        if status.capturing:
            st.state = "saving"
            st.post_left_s = max(0.0, status.capture_ends_t - self.ecu.live.now())
        else:
            st.state = "armed" if status.armed else "standby"
        return st

    def poll(self):
        if self.rec is not None:
            self._harvest(self.rec)
        out, self._pending = self._pending, []
        return out


# ---- logs -------------------------------------------------------------------

class DriveReader:
    """LogReader on W4's reader (``open_log``: a drive or capture in the
    database; old .csv, .msl, .mlg files) and W2's ``open_event`` for black
    box captures (.bbx, merged with the drive that was being recorded).

    Every channel keeps its own time base. ``view.replay`` is W4's
    ``ReplaySession`` for the same drive (envelopes, two cursors, readouts).
    ``directory`` is the folder that holds ``hondalog.db``.
    """

    def __init__(self, spec_lookup=None, directory=None):
        self._lookup = spec_lookup or (lambda cid: None)
        self._dir = directory

    @property
    def database(self) -> Path:
        return db_path(self._dir)

    def _spec(self, name: str, column=None, source: str = "ECU") -> tuple[str, ChannelSpec, float]:
        """(ui id, spec, scale) for a logged channel name."""
        cid, scale = BB_TO_UI.get(name, (name, 1.0))
        spec = self._lookup(cid)
        if spec is None:
            ch = bb_channel(name)
            if ch is not None:
                spec = ChannelSpec(cid, ch.label, ch.units, ch.decimals, ch.min, ch.max, ch.kind,
                                   source="BB", verified=True)
            elif column is not None:
                hi = column.hi if column.hi is not None and column.hi > (column.lo or 0) else 1.0
                spec = ChannelSpec(cid, column.label or name, column.units, column.decimals or 0,
                                   column.lo or 0.0, hi, column.kind or "value", source=source)
            else:
                spec = ChannelSpec(cid, name)
        return cid, spec, scale

    def read(self, path) -> ad.LogView:
        ref = path if isinstance(path, DriveRef) else parse_ref(path, self.database)
        if ref is None:
            path = Path(path)
            if path.suffix.lower() == ".bbx":
                return self._read_bbx(path)
            if not path.exists() and path.parent == Path("."):
                raise ValueError(f"no drive or capture {path.name!r} in {self.database}")
        else:
            path = ref
        try:
            log = open_log(path)
        except KeyError as exc:
            raise ValueError(str(exc.args[0] if exc.args else exc)) from None
        with log:
            log.load_all()
            if not log.channels or log.rows == 0:
                raise ValueError(f"{path.name}: no data")
            meta = {k: v for k, v in log.meta.items() if k != "address_map_text"}
            view = ad.LogView(path, title=path.name, meta=meta)
            if log.status == "crashed":
                view.note = "the recorder died during this drive: it ends at the last commit"
            trigger = None
            for key in ("trigger_time_s", "stall_time_s"):
                try:
                    trigger = float(log.meta[key])
                    break
                except (KeyError, TypeError, ValueError):
                    continue
            origin = trigger if trigger is not None else log.t0
            for name in log.channels:
                if name == LINK:
                    continue
                series = log.series(name)
                if not len(series) or not np.isfinite(series.v).any():
                    continue
                stream = log.streams_with(name)[0]
                source = {"blackbox": "BB", "math": "MATH"}.get(stream.info.source, "ECU")
                cid, spec, scale = self._spec(name, log.column(name), source)
                if not spec.mapped or cid in view.data:
                    continue
                view.specs[cid] = spec
                view.t[cid] = np.asarray(series.t, dtype=float) - origin
                view.data[cid] = np.asarray(series.v, dtype=float) * scale
            for mt, text in log.markers:
                view.markers.append((mt - origin, text))
            view.trigger_t = 0.0 if trigger is not None else None
            if BLACKBOX_STREAM in log.streams and log.meta.get("blackbox_clock_rtt_us") is not None:
                # both sources were stamped on one clock; half the sync round trip is the bound
                view.align_residual_ms = float(log.meta["blackbox_clock_rtt_us"]) / 2000.0
            view.replay = ReplaySession(log)        # type: ignore[attr-defined]
            view.origin = origin                    # type: ignore[attr-defined]
        return view

    def _read_bbx(self, path: Path) -> ad.LogView:
        from ..blackbox import open_event
        log = open_event(path, directory=self._dir)
        view = ad.LogView(path, title=path.name, meta=dict(log.meta))
        try:
            origin = float(log.meta["stall_time_s"])
        except (KeyError, ValueError):
            origin = float(log.t[0])
        t = np.asarray(log.t, dtype=float) - origin
        for name in log.names:
            if name == LINK:
                continue
            data = np.asarray(log.data[name], dtype=float)
            if not np.isfinite(data).any():
                continue
            cid, spec, scale = self._spec(name)
            if cid in view.data:
                continue
            view.specs[cid], view.t[cid], view.data[cid] = spec, t, data * scale
        for mt, text in log.markers:
            if not text.startswith("BB"):
                view.markers.append((mt - origin, text))
        view.trigger_t = 0.0
        alignment = getattr(log, "alignment", None)
        if alignment is not None and alignment.ok and alignment.error_s is not None:
            view.align_residual_ms = alignment.error_s * 1000.0
            view.note = f"ECU log merged ({alignment.method})"
        else:
            view.note = "black box only: no ECU log aligned"
        view.verdict = getattr(log, "verdict", None)    # type: ignore[attr-defined]
        return view

    def analyze(self, view: ad.LogView) -> ad.Forensics:
        out = be.order_of_loss(view)
        verdict = getattr(view, "verdict", None)
        if verdict is not None:             # the box's own 1 kHz diagnosis leads
            out.reading = f"{verdict.title}. {out.reading}".strip()
            out.summary = verdict.title + "."
        return out

    def _entries(self):
        try:
            return Library(self.database).refresh()
        except (OSError, ValueError):
            return []

    def _bbx(self) -> list[tuple[float, Path]]:
        try:
            return [(p.stat().st_mtime, p) for p in files_dir(self._dir).glob("*.bbx")]
        except OSError:
            return []

    def recent(self, stalls_only: bool = False):
        """Drives, captures and black box files, newest first. A capture
        counts from its trigger, so it comes before the drive it is in."""
        entries = self._entries()
        if stalls_only:
            return [e.ref for e in entries if e.kind == "stall"]
        found = [(e.started_unix, 1 if e.kind == "drive" else 2, e.ref) for e in entries]
        found += [(mtime, 0, path) for mtime, path in self._bbx()]
        return [ref for _, _, ref in sorted(found, key=lambda x: x[:2], reverse=True)]

    def drives(self):
        """The drive picker's rows: every drive, each followed by its captures, then .bbx files."""
        entries = self._entries()
        rows = []
        for drive in (e for e in entries if e.kind == "drive"):
            mine = [drive] + sorted((e for e in entries if e.kind != "drive" and e.drive_id == drive.drive_id),
                                    key=lambda e: e.started_unix)
            for e in mine:
                text = " ".join([e.name, e.kind, e.status, e.notes, e.trigger, *e.tags, *e.markers]).lower()
                rows.append(ad.DriveRow(e.ref, e.name, e.kind, e.started[:19].replace("T", " "), e.duration,
                                        e.status, e.stalls, e.trigger if e.kind != "drive" else e.notes, text,
                                        keep=e.keep, captures=e.captures))
        for mtime, path in sorted(self._bbx(), reverse=True):
            stamp = datetime.fromtimestamp(mtime).isoformat(" ", "seconds")
            rows.append(ad.DriveRow(path, path.name, "file", stamp, text=path.name.lower()))
        return rows

    def import_file(self, path):
        """A CSV / MSL / MLG file becomes a new drive in the database."""
        path = Path(path)
        if path.suffix.lower() == ".bbx":
            return path                     # black box captures are opened where they lie
        with open_log(path) as log:
            if log.ref is not None:
                return log.ref              # already a drive (another database): open it there
            return write_log(log, self.database, {"imported_from": str(path)})


    def delete(self, ref, allow_keep: bool = False) -> str:
        """One drive (all of it) or one capture (its entry), through the same
        code as ``hondalog-log rm``: refused while it is being recorded, a
        keep drive only with ``allow_keep``, freed pages vacuumed."""
        from ..data import retention
        if not isinstance(ref, DriveRef):
            raise ValueError(f"{getattr(ref, 'name', ref)} is a file, not a drive: delete it in the file manager")
        before = self.database.stat().st_size if self.database.exists() else 0
        try:
            entry = retention.delete(ref, allow_keep=allow_keep)
        except OSError as exc:
            raise ValueError(f"could not delete {ref.name}: {exc}") from None
        if entry.kind != "drive":
            return f"Deleted capture {entry.name}; {ref.drive} is untouched"
        freed = max(0, before - (self.database.stat().st_size if self.database.exists() else 0))
        return f"Deleted {entry.name}" + (f", {freed / 1e6:.1f} MB freed" if freed else "")

    def set_keep(self, ref, keep: bool) -> None:
        if not isinstance(ref, DriveRef):
            raise ValueError("only a drive can be marked keep")
        try:
            Library(ref.db).set_notes(ref.drive_ref, keep=keep)
        except OSError as exc:
            raise ValueError(str(exc)) from None


# ---- observed map -----------------------------------------------------------

class LogHistogramMap:
    """ObservedMap on W4's ``TableHistogram``.

    Live samples (one per new RPM reading) are kept and binned on the tune
    table's own axes when a ROM is open (``RomTableModel``), on the default
    20 x 10 grid otherwise. The histogram is rebuilt at most twice a second.
    """

    KEYS = ("inj", "ign", "trim")
    MAX_ROWS = 400_000          # about six hours at 17 Hz
    REBUILD_S = 0.5

    def __init__(self, tables=None):
        self._tables = tables
        self._tables_rev = -1
        self.rpm_axis = np.round((500 + np.arange(20) * 7000 / 19) / 50) * 50
        self.map_axis = np.round(17 + np.arange(10) * 84 / 9)
        self._rows = np.zeros((1024, 5))            # rpm, map, inj, ign, trim
        self._n = 0
        self._gen = -1
        self._dirty = False
        self._built_at = 0.0
        self._hist: dict[str, TableHistogram] = {}
        self._hits = np.zeros((len(self.map_axis), len(self.rpm_axis)), dtype=np.int32)
        self.revision = 0
        self._adopt_axes()

    def _adopt_axes(self) -> None:
        tables = self._tables
        if tables is None or tables.revision == self._tables_rev and self._tables_rev != -1:
            return
        self._tables_rev = tables.revision
        rpm, kpa = np.asarray(tables.rpm_axis, float), np.asarray(tables.map_axis, float)
        if not (np.array_equal(rpm, self.rpm_axis) and np.array_equal(kpa, self.map_axis)):
            self.rpm_axis, self.map_axis = rpm, kpa
            self._dirty = True
            self._build()

    def bins(self) -> tuple[Bins, Bins]:
        editor = getattr(self._tables, "editor", None)
        if editor is not None:
            try:
                return table_bins(editor)   # W5's axes straight from the ROM
            except ValueError:
                pass
        return Bins.from_breakpoints(self.rpm_axis.tolist()), Bins.from_breakpoints(self.map_axis.tolist())

    def cell(self, rpm: float, kpa: float) -> tuple[int, int]:
        return int(np.abs(self.rpm_axis - rpm).argmin()), int(np.abs(self.map_axis - kpa).argmin())

    def update(self, bus: ChannelBus) -> None:
        self._adopt_axes()
        rpm = bus.channel("rpm")
        if rpm is not None and rpm.gen != self._gen:
            self._gen = rpm.gen
            kpa = bus.channel("map")
            if kpa is not None and rpm.value == rpm.value and kpa.value == kpa.value \
                    and rpm.value >= 300:
                def val(cid):
                    st = bus.channel(cid)
                    return 0.0 if st is None or st.value != st.value else st.value
                if self._n == len(self._rows):
                    if self._n >= self.MAX_ROWS:
                        self._rows[:-1] = self._rows[1:]
                        self._n -= 1
                    else:
                        self._rows = np.vstack([self._rows, np.zeros_like(self._rows)])
                self._rows[self._n] = (rpm.value, kpa.value, val("inj"), val("ign"),
                                       val("stft") + val("ltft"))
                self._n += 1
                self._dirty = True
        if self._dirty and time.monotonic() - self._built_at >= self.REBUILD_S:
            self._build()

    def add_table(self, table) -> None:
        """Fold a finished log in (``Log.table(["rpm", "map", "inj", "ign", "stft", "ltft"])``)."""
        n = len(table)
        rows = np.zeros((n, 5))
        rows[:, 0], rows[:, 1] = table["rpm"], table["map"]
        for k, name in ((2, "inj"), (3, "ign")):
            rows[:, k] = table[name] if name in table else 0.0
        trim = sum(np.nan_to_num(table[name]) for name in ("stft", "ltft") if name in table)
        rows[:, 4] = trim
        keep = np.isfinite(rows[:, 0]) & np.isfinite(rows[:, 1]) & (rows[:, 0] >= 300)
        self._rows = np.vstack([self._rows[:self._n], rows[keep]])
        self._n = len(self._rows)
        self._dirty = True
        self._build()

    def _build(self) -> None:
        rows = self._rows[:self._n]
        x_bins, y_bins = self.bins()
        self._hist = {key: TableHistogram(rows[:, 0], rows[:, 1], rows[:, 2 + k], x_bins, y_bins)
                      for k, key in enumerate(self.KEYS)}
        self._hits = self._hist["inj"].count.astype(np.int32)
        self._dirty = False
        self._built_at = time.monotonic()
        self.revision += 1

    def hits(self) -> np.ndarray:
        return self._hits

    def mean(self, key: str) -> np.ndarray:
        hist = self._hist.get(key)
        if hist is None:
            return np.full(self._hits.shape, np.nan)
        return hist.stat("mean")

    def clear(self) -> None:
        self._n = 0
        self._dirty = True
        self._build()


# ---- raw tools / mapper -----------------------------------------------------

class MappingSession(be.PollerMapper):
    """MapperSession on W3's mapping tools.

    The sweep grid and the console stay as they were (reads through
    ``Poller.submit``); the console's frames pass W1's read-only guard, and
    the wizard is a W3 ``VerificationSession``: a guided script per channel,
    a verdict computed from the recorded data, candidates from the
    correlation finder when the address looks wrong, and the result written
    to the address map with its evidence.
    """

    def __init__(self, ecu: LinkSource):
        super().__init__(ecu)
        self._session: VerificationSession | None = None
        self._verdict = None
        self._matches: dict[int, Any] = {}
        self._message = ""
        self._channel = ""
        self._sweep_t: collections.deque = collections.deque(maxlen=self.HIST)

    # -- the correlation finder on the sweep history --

    def sweep_recording(self):
        """The grid's sweep history as a W3 ``SweepRecording`` (oldest first)."""
        from ..mapping.sweeps import from_rows
        rows = [row for row in self._hist if (row >= 0).any()]
        return from_rows(rows, list(self._sweep_t)[-len(rows):] if rows else [])

    def rank_against(self, channel: str, top: int = 5) -> list[ad.Candidate]:
        """Which other bytes follow a channel (ideally a verified one) in the
        sweeps so far: W3's correlation finder. Needs a few sweeps in which
        the channel moved."""
        rec = self.sweep_recording()
        if len(rec) < 5:
            self._console = "sweep continuously for a few seconds first, then rank"
            self.revision += 1
            return []
        try:
            matches = correlate.rank_against_channel(rec, self.ecu.amap, channel, top=top)
        except addressmap.MapError as exc:
            self._console = f"!! {exc}"
            self.revision += 1
            return []
        self._console = "  ·  ".join(f"{m.candidate.label} {m.score:.2f}" for m in matches) \
            or f"nothing follows {channel}"
        self.revision += 1
        return [ad.Candidate(m.candidate.addr, float(m.score), int(self.minv[m.candidate.addr]),
                             int(self.maxv[m.candidate.addr])) for m in matches]

    # -- console: W1's guard --

    def set_expert(self, on: bool) -> str:
        message = self.ecu.set_expert_unlock(on)
        self._console = message
        self.revision += 1
        return message

    @property
    def expert(self) -> bool:
        return self.ecu.expert_unlock

    def _console_done(self, info, fut) -> None:
        try:
            exc = fut.exception()
        except Exception:
            exc = None
        if isinstance(exc, CommandBlocked):
            self._console = (f"!! blocked: {exc}. Only read requests go out; tick "
                             "\"Expert\" to send anything else.")
            self.revision += 1
            return
        super()._console_done(info, fut)

    # -- sweeps feed the verification session --

    def _chunk(self, addr: int, fut) -> None:
        before = self.sweeps
        super()._chunk(addr, fut)
        if self.sweeps == before:
            return
        self._sweep_t.append(time.monotonic())
        session = self._session
        if session is not None and session.state == "running":
            session.add_sweep(self.cur)

    # -- wizard --

    def _script(self):
        targets = self._targets()
        if not targets:
            return None, None
        ch = targets[min(self._target, len(targets) - 1)]
        scripts = scripts_for(ch.name)
        return ch, (scripts[0] if scripts else None)

    def wizard(self) -> ad.WizardView:
        w = ad.WizardView(step=self._step, target=self._target_name(), can_back=self._step > 0)
        session = self._session
        if self._step == 0:
            ch, script = self._script()
            w.title = "Pick the channel to prove"
            w.body = ("One channel at a time. Each has a short guided check; a pass is "
                      "written to the address map with what was measured."
                      + (f"  {self._message}" if self._message else ""))
            w.column = "CHANNEL"
            w.targets = [f"{c.label} ({c.name})  now 0x{c.addr:02X}"
                         + ("" if scripts_for(c.name) else "  (no script)")
                         for c in self._targets()]
            w.target_index = self._target
            w.primary = ("Connect first" if not self.available()
                         else "Next" if script is not None else "No script")
        elif self._step in (1, 2) and session is not None and session.phase is not None:
            phase = session.phase
            index = session.script.phases.index(phase)
            w.title = f"{phase.title}  ({index + 1} of {len(session.script.phases)})"
            w.body = phase.instruction
            w.busy = min(1.0, session.phase_elapsed / max(phase.min_s, 0.1))
            last = index == len(session.script.phases) - 1
            w.primary = ("Recording…" if not session.can_advance
                         else "Finish" if last else "Next step")
        elif self._step == 3:
            verdict = self._verdict
            status = verdict.status if verdict is not None else "skip"
            w.title = f"{self._channel}: {status.upper()}"
            w.candidates = [ad.Candidate(addr, score, lo, hi)
                            for addr, (score, lo, hi, _) in self._matches.items()]
            w.column = "FITS THE CHECK"
            if status == PASS:
                w.body = f"{verdict.summary}"
                w.primary = "Mark verified"
            elif status == PLAUSIBLE:
                w.body = (f"{verdict.summary}. That only shows the value is sane, not that "
                          "the address is right.")
                w.primary = "Accept anyway"
            elif w.candidates:
                w.body = (f"{verdict.summary if verdict else ''} These addresses behaved the way "
                          "the check expects. Click one to inspect it.").strip()
                sel = self._picked if self._picked is not None else w.candidates[0].addr
                w.primary = f"Use 0x{sel:02X}"
            else:
                w.body = ((verdict.summary + ". ") if verdict is not None and verdict.summary
                          else "") + "Nothing in the sweep fits either. Try again."
                w.primary = "Try again"
        else:
            w.title = "Done"
            w.body = self._message
            w.primary = "Done"
        return w

    def wizard_next(self, selected_addr: int | None = None) -> None:
        if self._step == 0:
            ch, script = self._script()
            if not self.available() or ch is None or script is None:
                return
            self._channel, self._message = ch.name, ""
            self._session = VerificationSession(script, self.ecu.amap)
            self._session.start()
            self.set_sweeping(True)
            self._step = 1
        elif self._step in (1, 2):
            session = self._session
            if session is None or not session.can_advance:
                return
            if session.next_phase() is None:
                self._finish()
            else:
                self._step = 2
        elif self._step == 3:
            self._confirm(selected_addr)
        else:
            self._step, self._session = 0, None
        self.revision += 1

    def _finish(self) -> None:
        session = self._session
        assert session is not None
        report = session.finish()
        self._verdict = report.verdicts.get(self._channel)
        self._matches = {}
        self._picked = None
        if self._verdict is None or self._verdict.status not in (PASS, PLAUSIBLE):
            for found in session.suggest(self._channel):
                if isinstance(found, correlate.BitChange):
                    addr, score, fields = found.addr, found.purity, {"addr": found.addr,
                                                                     "formula": found.formula}
                elif isinstance(found, correlate.ValueMatch):
                    addr = found.candidate.addr
                    score = 1.0 / (1.0 + abs(found.error))
                    fields = found.candidate.channel_fields()
                else:
                    addr, score, fields = (found.candidate.addr, found.score,
                                           found.candidate.channel_fields())
                if addr not in self._matches:
                    self._matches[addr] = (float(score), int(self.minv[addr]),
                                           int(self.maxv[addr]), fields)
        self._step = 3

    def _confirm(self, selected_addr: int | None) -> None:
        session, verdict = self._session, self._verdict
        path = Path(self.ecu.amap.path) if self.ecu.amap.path else None
        status = verdict.status if verdict is not None else "skip"
        if session is None or path is None or path == addressmap.DEFAULT_MAP:
            self._message = "The shipped map is read-only; your copy is made on first connect."
            self._step = 4
            return
        try:
            if status in (PASS, PLAUSIBLE):
                session.confirm(self._channel, path, force=status == PLAUSIBLE,
                                evidence_dir=log_dir())
                self._message = (f"{self._channel} is marked verified in {path.name}, with the "
                                 "measurements as evidence.")
            elif self._matches:
                addr = selected_addr if selected_addr in self._matches else next(iter(self._matches))
                self._picked = addr
                mapedit.update_channel(path, self._channel, self.ecu.amap.variant or None,
                                       **self._matches[addr][3])
                if status == FAIL:
                    session.reject(self._channel, path, note=f"moved to 0x{addr:02X}",
                                   evidence_dir=log_dir())
                self._message = (f"{self._channel} now reads 0x{addr:02X} in {path.name} "
                                 "(still unverified). Reconnect, then run the check again.")
            else:
                return self.wizard_retry()
        except (addressmap.MapError, OSError) as exc:
            self._message = f"Not written: {exc}"
        else:
            self.ecu.reload_map()
        self._step = 4

    def clear_changes(self) -> None:
        super().clear_changes()
        self._sweep_t.clear()

    def wizard_back(self) -> None:
        self._step, self._session = 0, None
        self.revision += 1

    def wizard_retry(self) -> None:
        if self.available():
            self._step, self._session = 0, None
            self.wizard_next()

    def _wizard_tick(self) -> None:
        if self._step in (1, 2) and self._session is not None and not self.available():
            self._step, self._session = 0, None
            self._message = "The link went away; the check was stopped."
            self.revision += 1


# ---- tuning tables ----------------------------------------------------------

class RomTableModel:
    """TableModel on W5's ``RomSession`` / ``TableEditor``.

    Until ``open(rom, definition)`` succeeds it behaves exactly like the demo
    tables it wraps (read-only samples), so the Tune and Map 3D screens always
    have something to show. With a ROM open: the RPM x load tables of the
    definition that share the first table's axes, edits through the session's
    undo stack, ``save`` writes a ROM image with the checksum fixed.
    """

    def __init__(self, fallback=None, store_dir=None):
        self._fallback = fallback or be.DemoTableModel()
        self.store_dir = store_dir
        self.session = None
        self.editor = None                  # the first table's editor: axes for the histogram
        self._editors: dict[str, Any] = {}
        self._info: list[ad.TableInfo] = []
        self._base: dict[str, np.ndarray] = {}
        self._cache: dict[str, tuple[int, np.ndarray]] = {}
        self._revision = 0
        self.problem = ""

    # -- loading --

    def open(self, rom_path, definition_path=None) -> str:
        """Open a ROM with its definition (.toml or .xdf). Returns '' or the
        reason it could not be used (the demo tables then stay)."""
        from .. import tuning
        from ..tuning.demo import demo_definition
        try:
            definition = (tuning.load_definition(definition_path) if definition_path
                          else demo_definition())
            session = tuning.RomSession.open(rom_path, definition, store_dir=self.store_dir)
        except Exception as exc:
            self.problem = f"{Path(rom_path).name}: {exc}"
            return self.problem
        return self.use(session)

    def use(self, session) -> str:
        editors, info, first = {}, [], None
        for table in session.definition.tables:
            try:
                editor = session.table(table.id)
                xa, ya = editor.axis("x"), editor.axis("y")
            except Exception:
                continue
            if editor.rows < 2 or editor.cols < 2 or not (xa.numeric and ya.numeric):
                continue                    # curves and label-only tables have no RPM x load grid
            if first is None:
                first = (editor, xa.values, ya.values)
            elif not (np.array_equal(xa.values, first[1]) and np.array_equal(ya.values, first[2])):
                continue
            lo, hi = editor.limits("real")
            decimals = editor.decimals if editor.decimals is not None else 2
            editors[table.id] = editor
            info.append(ad.TableInfo(table.id, editor.title, editor.units,
                                     f"{editor.title.split()[0].upper()} {editor.units}".strip(),
                                     decimals, 10.0 ** -decimals * (5 if decimals else 1),
                                     float(lo), float(hi)))
        if first is None:
            self.problem = "the definition has no RPM x load table with numeric axes"
            return self.problem
        self.session, self.editor = session, first[0]
        self._editors, self._info = editors, info
        self._base = {tid: e.original_values("real").T.copy() for tid, e in editors.items()}
        self._cache.clear()
        self.problem = ""
        self._revision += 1
        session.on_change(self._changed)
        return ""

    def _changed(self, *_args) -> None:
        self._cache.clear()
        self._revision += 1

    # -- TableModel --

    @property
    def loaded(self) -> bool:
        return self.session is not None

    @property
    def revision(self) -> int:
        return self._revision + self._fallback.revision

    @property
    def rpm_axis(self) -> np.ndarray:
        return self.editor.axis("x").values if self.loaded else self._fallback.rpm_axis

    @property
    def map_axis(self) -> np.ndarray:
        return self.editor.axis("y").values if self.loaded else self._fallback.map_axis

    @property
    def writable(self) -> bool:
        return True if self.loaded else self._fallback.writable

    @property
    def read_only_reason(self) -> str:
        if self.loaded:
            name = self.session.rom.path.name if self.session.rom.path else "ROM"
            report = self.session.checksum()
            return f"{name}: {self.session.definition.title or 'definition'}; checksum {'OK' if report.ok else 'will be fixed on save'}."
        return (self.problem + " " if self.problem else "") + self._fallback.read_only_reason

    def tables(self):
        return self._info if self.loaded else self._fallback.tables()

    def values(self, table_id: str) -> np.ndarray:
        if not self.loaded:
            return self._fallback.values(table_id)
        hit = self._cache.get(table_id)
        if hit is None:
            # the ROM is (load rows, rpm columns); the UI wants (n_rpm, n_map)
            hit = self._cache[table_id] = (self._revision,
                                           self._editors[table_id].values("real").T.copy())
        return hit[1]

    def base(self, table_id: str) -> np.ndarray:
        return self._base[table_id] if self.loaded else self._fallback.base(table_id)

    def _mask(self, table_id: str, cells) -> np.ndarray:
        editor = self._editors[table_id]
        mask = np.zeros(editor.shape, dtype=bool)
        for rpm_i, map_i in cells:
            mask[map_i, rpm_i] = True
        return mask

    def adjust(self, table_id, cells, steps) -> None:
        if not self.loaded:
            return self._fallback.adjust(table_id, cells, steps)
        step = next(i.step for i in self._info if i.id == table_id)
        self._editors[table_id].add(self._mask(table_id, cells), steps * step)

    def interpolate(self, table_id, cells) -> None:
        if not self.loaded:
            return self._fallback.interpolate(table_id, cells)
        if len(cells) >= 3:
            self._editors[table_id].interpolate(self._mask(table_id, cells))

    def smooth(self, table_id, cells) -> None:
        if not self.loaded:
            return self._fallback.smooth(table_id, cells)
        self._editors[table_id].smooth(self._mask(table_id, cells))

    def undo(self) -> bool:
        if not self.loaded:
            return self._fallback.undo()
        return self.session.undo() is not None

    def save(self, path) -> None:
        if not self.loaded:
            return self._fallback.save(path)
        self.session.save(path)

    def can_write_chip(self) -> bool:
        if not self.loaded:
            return False
        from ..tuning import find_minipro
        return find_minipro() is not None
