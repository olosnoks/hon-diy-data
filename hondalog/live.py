"""The one live path: readings in, math channels and alarms worked out,
everything recorded and watched by the triggers.

    Poller --on_block--> LiveSession --feed_read------------> Recorder (hondalog.db, raw bytes)
           --on_sample->   LivePipeline (math + alarms) ----> Recorder ("math" stream,
                                                              alarm events, captures)
    BlackboxReader ---->   on the poller's t0 --------------> Recorder ("blackbox/live";
                                                              key on keeps the drive open)
    CydPoller --------->   (the handheld over USB serial: the same two callbacks,
                           plus GPS fixes and the handheld's events) -> Recorder
                           frames (deque), on_frame --------> UI / CLI

``LivePipeline`` is the per-sample step on its own (no I/O, no threads):

    pipeline = LivePipeline(amap)
    frame = pipeline.feed(sample)        # a poller Sample
    frame.values                         # measured + math, by name
    frame.events                         # AlarmEvent list (raised / cleared / acknowledged)
    pipeline.alarms.top()                # what a banner should show right now

``LiveSession`` is what the app, ``hondalog-log record`` and the smoke test
all use:

    session = LiveSession(amap)
    session.connect("sim:")              # or attach(poller); "cyd:" = the handheld
    session.open_blackbox("bbsim:")      # optional
    session.start_recording()
    ...
    session.close()

No Qt. The pipeline costs about 10 microseconds per sample for the shipped map.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping

from .addressmap import AddressMap, Channel
from .alarms import AlarmEngine, AlarmEvent
from .mathch import MathChannel, MathEngine

if TYPE_CHECKING:
    from .blackbox.reader import BBEvent, BlackboxReader
    from .logger import Recorder
    from .poller import BlockRead, Poller, Sample
    from .triggers import Fired

MATH_STREAM = "math"
BLACKBOX_STREAM = "blackbox/live"
#: a rail above this is "on" for the purpose of keeping a drive open (volts)
KEY_ON_V = 6.0


def blackbox_key_on(values: Mapping[str, float | None]) -> bool:
    """True while the black box sees the car switched on: IG1 (its own
    debounced flag, or the rail itself), the ECU's supply, or the engine
    turning. The recorder keeps the drive open on this while the ECU is
    silent, so a stall with the key on and the ECU dead stays in one drive.
    With the key off (or the rail inputs not wired) it is False and the
    drive ends as it does without a black box."""
    def above(name: str, level: float) -> bool:
        value = values.get(name)
        return value is not None and value == value and value >= level
    return (above("bb_ig1_on", 0.5) or above("bb_ig1", KEY_ON_V) or above("bb_ecub", KEY_ON_V)
            or above("bb_running", 0.5) or above("bb_rpm_tach", 1.0))


@dataclass
class LiveFrame:
    t: float
    link: bool
    values: dict[str, float | None]
    events: list[AlarmEvent] = field(default_factory=list)
    #: the poller Sample this frame was made from (stamps, latency, fresh), if any
    sample: Any = None


class LivePipeline:
    def __init__(self, amap: AddressMap):
        self.amap = amap
        self.math: MathEngine = amap.math_engine()
        self.alarms: AlarmEngine = amap.alarm_engine()
        self.last: LiveFrame | None = None

    @property
    def channels(self) -> list[Channel | MathChannel]:
        """Everything that can be shown: measured channels, then math channels.
        Both kinds have name, label, units, min, max, decimals, kind, format()."""
        return [*self.amap.channels, *self.math.channels]

    def process(self, values: Mapping[str, float | None], t: float, link: bool = True,
                changed: Collection[str] | None = None) -> LiveFrame:
        """``changed`` names the channels that were re-read for this sample
        (tiered polling); leave it out when everything is fresh."""
        merged: dict[str, float | None] = dict(values)
        if link:
            merged.update(self.math.update(values, t, changed))
        else:                               # no reply this sweep: nothing new to work out
            merged.update(dict.fromkeys(self.math.names))
        events = self.alarms.update(merged, t)
        self.last = LiveFrame(t, link, merged, events)
        return self.last

    def feed(self, sample: Any) -> LiveFrame:
        """Take a poller ``Sample`` (anything with .t, .link and .values). If
        it says which channels were re-read (``fresh``), each math channel
        runs at the rate of its fastest input."""
        frame = self.process(sample.values, sample.t, sample.link,
                             getattr(sample, "fresh", None) or None)
        frame.sample = sample
        return frame

    def reset(self) -> None:
        self.math.reset()
        self.alarms.reset()
        self.last = None


class LiveSession:
    """Poller (+ black box) -> pipeline -> recorder and triggers. See the module text.

    Threads: ``_on_block`` / ``_on_sample`` run on the poller thread, the black
    box callbacks on the reader thread; everything else is for the caller's
    thread. The pipeline is guarded by one lock, so ``acknowledge`` is safe
    from a UI thread. ``frames`` is a bounded deque the UI drains; ``on_frame``
    and ``on_read`` are called on the poller thread for callers that would
    rather be pushed.
    """

    def __init__(self, amap: AddressMap, out_dir: str | Path | None = None,
                 max_frames: int = 2000, **recorder_opts: Any):
        self.amap = amap
        self.pipeline = LivePipeline(amap)
        self.out_dir = Path(out_dir) if out_dir else None
        self.recorder_opts = recorder_opts
        self.poller: Poller | None = None
        self.blackbox: BlackboxReader | None = None
        self.recorder: Recorder | None = None
        self.frames: collections.deque[LiveFrame] = collections.deque(maxlen=max_frames)
        self.on_frame: Callable[[LiveFrame], None] | None = None
        self.on_read: Callable[[BlockRead], None] | None = None
        self.on_blackbox: Callable[[Any], None] | None = None       # a reader Sample
        #: the handheld's GPS fixes (session seconds, the ``gps`` stream's columns),
        #: recording or not: the Map screen shows where the car is
        self.on_gps: Callable[[float, Mapping[str, Any]], None] | None = None
        self.last_error = ""
        self._lock = threading.RLock()
        self._bb_asked_at = float("-inf")
        self._bb_columns: list[Any] | None = None
        self._gps_columns: list[Any] | None = None
        self._cyd_marks: collections.deque[float] = collections.deque(maxlen=16)   # sent to the handheld, when

    # ---- sources ----

    @property
    def t0(self) -> float:
        """The monotonic time that session seconds count from (the poller's)."""
        return self.poller.t0 if self.poller is not None else time.monotonic()

    def now(self) -> float:
        """Session seconds, now."""
        if self.poller is not None:
            return self.poller.clock.now() - self.poller.t0
        return 0.0

    def connect(self, port: str, start: bool = True) -> Poller:
        """Open ``port`` (a device, ``sim:`` ...) with a new poller and attach it.
        ``cyd:`` ports are the handheld's stream (``hondalog.handheld.serial_link``):
        a ``CydPoller`` takes the poller's place and the laptop decodes its raw reads."""
        from .handheld.serial_link import is_cyd_port
        if is_cyd_port(port):
            from .handheld.serial_link import CydPoller
            poller: Any = CydPoller(port, self.amap)
        else:
            from .poller import Poller
            from .transport import make_transport
            poller = Poller(make_transport(port, self.amap.framing), self.amap)
        poller.port_name = port
        self.attach(poller)
        if start:
            poller.start()
        return poller

    def attach(self, poller: Poller) -> None:
        """Take over a poller's two callbacks. Call before ``poller.start()``
        or between samples; the pipeline starts from a clean state."""
        with self._lock:
            self.pipeline.reset()
            self.frames.clear()
        self.poller = poller
        poller.on_block = self._on_block
        poller.on_sample = self._on_sample
        if hasattr(poller, "on_pid"):            # OBD2-only mode: raw PID replies
            poller.on_pid = self._on_pid
        if hasattr(poller, "on_event"):          # the handheld: its GPS and its own events too
            poller.on_gps = self._on_cyd_gps
            poller.on_event = self._on_cyd_event

    def open_blackbox(self, port: Any, start: bool = True, **opts: Any) -> BlackboxReader:
        """Start a ``BlackboxReader`` on the poller's time axis and record it."""
        from .blackbox.reader import BlackboxReader
        reader = BlackboxReader(port, t0=self.t0, **opts)
        self.attach_blackbox(reader)
        if start:
            reader.start()
        return reader

    def attach_blackbox(self, reader: BlackboxReader) -> None:
        """Record a reader's frames. Its ``t0`` must be the poller's
        (``BlackboxReader(port, t0=session.t0)``) for the two to line up."""
        self.blackbox = reader
        reader.on_sample = self._on_bb_sample
        reader.on_event = self._on_bb_event

    def close(self) -> None:
        """Stop recording, the black box and the poller."""
        poller, reader = self.poller, self.blackbox
        if poller is not None:
            poller.stop()
        if reader is not None:
            reader.stop()
        self.stop_recording()
        if poller is not None:
            poller.on_block = poller.on_sample = None
        self.poller = self.blackbox = None

    # ---- recording ----

    @property
    def recording(self) -> bool:
        return self.recorder is not None

    def log_meta(self) -> dict[str, Any]:
        """What the link knows that belongs in a log header."""
        poller = self.poller
        if poller is None:
            return {}
        unlocked = [bytes(f).hex(" ") for f in poller.link.unlocked_frames]
        meta: dict[str, Any] = {
            "link": {k: round(float(v), 4) for k, v in poller.link_report().items()},
            "expert_unlock": not poller.link.read_only,
            # frames that were not plain reads and went out under the expert unlock
            "unlocked_frames": unlocked,
            "read_plan": [f"{addr:02X}+{length}" for addr, length in poller.blocks],
        }
        if getattr(poller, "source", "") == "cyd":
            meta["source"] = "cyd"
            meta["cyd"] = poller.cyd_meta()
            if poller.warning:
                meta["cyd_warning"] = poller.warning
        reader = self.blackbox
        if reader is not None and reader.hello is not None:
            meta["blackbox"] = reader.device_text
            meta["blackbox_clock_rtt_us"] = reader.clock.rtt_us
        return meta

    def start_recording(self, out_dir: str | Path | None = None, **opts: Any) -> Recorder:
        """Begin a drive in the database (``out_dir`` is the folder that holds
        ``hondalog.db``). Raises OSError if the file cannot be made."""
        from .logger import Recorder
        if self.recorder is not None:
            return self.recorder
        poller = self.poller
        epoch = None
        ecu_id = port = ""
        if poller is not None:
            ecu_id, port = poller.ecu_id_text, poller.port_name
            if not poller.clock.virtual:
                # wall time of session time 0, from the same clock the stamps use
                epoch = datetime.fromtimestamp(time.time() - (poller.clock.now() - poller.t0))
        folder = Path(out_dir) if out_dir else self.out_dir
        recorder = Recorder(self.amap, ecu_id, port, folder, epoch=epoch,
                            meta=self.log_meta(), **{**self.recorder_opts, **opts})
        recorder.on_fired = self._on_fired
        self.recorder = recorder
        return recorder

    def stop_recording(self) -> Recorder | None:
        """End the drive (its row gets the final link counters). Returns the
        recorder so the caller can read ``path``, ``rows`` and ``events``."""
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            recorder.set_meta(**self.log_meta())
            recorder.stop()
        return recorder

    def mark(self, text: str | None = None) -> int:
        """Drop a marker in the log and in the black box. Returns its number
        (0 when not recording)."""
        recorder = self.recorder
        if recorder is None:
            return 0
        n = recorder.markers + 1
        recorder.mark(text or f"MARK {n}")
        self._cyd_command("mark", text is None)
        reader = self.blackbox
        if reader is not None and text is None:
            try:
                reader.mark(n)              # same number in both files: they align on it
            except Exception as exc:
                self.last_error = f"black box mark: {exc}"
        return n

    def trigger(self, why: str = "") -> None:
        """Save a capture now (a button)."""
        from .triggers import MANUAL
        if self.recorder is not None:
            self.recorder.trigger(MANUAL)

    def acknowledge(self) -> list[AlarmEvent]:
        with self._lock:
            events = self.pipeline.alarms.acknowledge()
        self._record_alarms(events)
        self._cyd_command("ack_alarm")
        return events

    def _cyd_command(self, what: str, when: bool = True) -> None:
        """The same button on the handheld, if it is a two-way one (its SD file gets it too)."""
        poller = self.poller
        if not when or not getattr(poller, "two_way", False):
            return
        try:
            getattr(poller, what)()
            if what == "mark":
                self._cyd_marks.append(time.monotonic())
        except Exception as exc:
            self.last_error = f"handheld {what}: {exc}"

    # ---- poller thread ----

    def _on_block(self, read: BlockRead) -> None:
        recorder = self.recorder
        if recorder is not None:
            # the time the ECU sampled it: frame completion minus its estimated age
            recorder.feed_read(read.t - read.latency_s, read.addr, read.data, read.latency_s)
        if self.on_read is not None:
            self.on_read(read)

    def _on_pid(self, t: float, pid: int, data: bytes) -> None:
        recorder = self.recorder
        if recorder is not None:
            recorder.feed_pid(t, pid, data)

    def _on_sample(self, sample: Sample) -> None:
        with self._lock:
            frame = self.pipeline.feed(sample)
        recorder = self.recorder
        if recorder is not None:
            if not frame.link:
                recorder.feed_link(frame.t, False)
            elif self.pipeline.math.names:
                names = self.pipeline.math.names
                if self._math_columns is None:
                    from .logger import column_for
                    self._math_columns = [column_for(c) for c in self.pipeline.math.channels]
                recorder.feed_values(MATH_STREAM, frame.t, {n: frame.values.get(n) for n in names},
                                     source="math", columns=self._math_columns)
            self._record_alarms(frame.events)
        self.frames.append(frame)
        if self.on_frame is not None:
            self.on_frame(frame)

    _math_columns: list[Any] | None = None

    def _record_alarms(self, events: Collection[AlarmEvent]) -> None:
        """Alarm events go into the log; "marker" drops a marker, "capture"
        opens a capture window through the trigger engine."""
        recorder = self.recorder
        if recorder is None:
            return
        from .triggers import alarm_rule
        for event in events:
            recorder.event("alarm", event.marker_text,
                           {"alarm": event.name, "kind": event.kind, "severity": event.severity,
                            "value": event.value, "channel": event.channel,
                            "unverified": event.unverified}, t=event.t)
            if event.kind != "raised":
                continue
            if "marker" in event.actions:
                recorder.mark(event.marker_text)
            if "capture" in event.actions:
                recorder.trigger(alarm_rule(event.name, event.message, event.severity,
                                            recorder.rules), t=event.t)

    def _on_fired(self, fired: Fired) -> None:
        """A capture window opened on the laptop: have the box save its side too."""
        reader = self.blackbox
        if reader is None or "capture" not in fired.rule.actions or fired.rule.name == "external":
            return
        self._bb_asked_at = time.monotonic()
        try:
            reader.trigger()
        except Exception as exc:
            self.last_error = f"black box trigger: {exc}"

    # ---- the handheld (CydPoller thread) ----

    def _on_cyd_gps(self, t: float, fix: Mapping[str, Any]) -> None:
        hook = self.on_gps
        if hook is not None:
            try:
                hook(t, fix)
            except Exception as exc:            # the UI's problem must not stop the recording
                self.last_error = f"gps: {exc}"
        recorder = self.recorder
        if recorder is None:
            return
        if self._gps_columns is None:
            from .geo.track import GPS_STREAM, gps_columns
            self._gps_columns = gps_columns()
            self._gps_stream = GPS_STREAM
        recorder.feed_values(self._gps_stream, t, fix, source="gps", columns=self._gps_columns, evaluate=False)

    def _on_cyd_event(self, event: Any) -> None:
        """The handheld's own events: its button's markers, its stall trigger,
        trouble codes it read. Alarms are worked out here from the same data,
        so the handheld's alarm events are only noted."""
        recorder = self.recorder
        if recorder is None:
            return
        from .handheld import format as F
        data = {"code": event.code, "name": event.name, "src": event.src, "aux": event.aux,
                "value": event.value, "handheld_us": event.t_dev_us}
        if event.code == F.EV_MARKER:
            now = time.monotonic()
            while self._cyd_marks and now - self._cyd_marks[0] >= 3.0:
                self._cyd_marks.popleft()       # sent, never echoed
            if self._cyd_marks:
                self._cyd_marks.popleft()       # the echo of a marker sent from here
                return
            recorder.mark(f"MARK (handheld button {event.value})")
        elif event.code in (F.EV_DTC, F.EV_DTC_NONE):
            recorder.event("dtc", event.text, data, t=event.t)
        else:
            recorder.event("handheld", event.text, data, t=event.t)
        if event.is_stall:
            recorder.external_trigger(f"handheld: {event.text}", t=event.t)

    _gps_stream = "gps"

    # ---- black box thread ----

    def _on_bb_sample(self, sample: Any) -> None:
        recorder = self.recorder
        if recorder is not None and sample.link:
            if self._bb_columns is None:
                from .blackbox.channels import LIVE_NAMES, bb_channel
                from .logger import column_for
                self._bb_columns = [column_for(bb_channel(n)) for n in LIVE_NAMES]
            # alive: with the key on the drive stays open (or one starts) although
            # the ECU is silent; with the key off the frame is dropped between drives
            recorder.feed_values(BLACKBOX_STREAM, sample.t, sample.values, source="blackbox",
                                 columns=self._bb_columns, alive=blackbox_key_on(sample.values))
        if self.on_blackbox is not None:
            self.on_blackbox(sample)

    def _on_bb_event(self, event: BBEvent) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        recorder.event("blackbox", event.text, {"code": event.code, "name": event.name,
                                                "event_no": event.event_no,
                                                "box_us": event.t_dev_us}, t=event.t)
        if event.started_file and time.monotonic() - self._bb_asked_at > 2.0:
            # the box saw it first (or alone): cut the ECU log around the same moment
            recorder.external_trigger(f"black box: {event.name}", t=event.t)
