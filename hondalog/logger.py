"""Recording: the drive being written to the database, the triggers and the
captures they save.

One ``Recorder`` is one recording session. It is fed on the poller thread
and writes into ``hondalog.db`` (see ``hondalog/data/store.py``) through a
store that never makes the caller wait for the disk. Triggers (the stall
rules by default) are evaluated on every feed; when a capture window closes
it is entered in the ``captures`` table as a time range of the drive: no
data is copied, and every stream that was being recorded is in it.

A session is one or more *drives*. The first starts with the recorder. A
drive ends when the recorder stops, or when nothing has shown the car to be
switched on for ``drive_gap_s`` seconds (key off). "Switched on" is the ECU
answering, or another source saying so: the black box feeds its frames with
``alive=True`` while it sees the key on (IG1, ECU +B or the engine turning).
So a drive stays open, and the black box frames keep being recorded, while
the key is on and the ECU is dead: the stall this tool exists to catch. The
next sign of life starts a new drive on the same clock, whether it is the
ECU answering or the box seeing the key come on with the ECU still silent.
Between drives (key off) nothing is recorded.

Why one drive and not a second, "black box only" drive for the silent
stretch: the ECU dying, the dead time and the restart are one event. In one
drive they are on one time axis, in one capture window and on one Replay
screen; split at the 120 s mark they would be two or three drives cut at a
moment that means nothing. And tying the drive to key-on (not to "the box is
plugged in") keeps a laptop left in the parked car from recording all night.

How it is fed (``hondalog.live.LiveSession`` does this wiring):

    feed_read(t, addr, data)     one K-line read with its own timestamp
                                 (``Poller.on_block``); the raw bytes are
                                 stored next to the decoded values
    feed_link(t, up)             the ECU stopped or started answering
    feed_values(stream, t, {})   anything else on the session timebase:
                                 math channels, black box frames (``alive=``
                                 says whether that source sees the key on)
    feed(sample)                 one whole row of decoded values, for sources
                                 that have no single reads (imports, tests)

Old CSV logs still open: ``read_log`` takes any format ``open_log`` knows.
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from . import __version__
from .data import db
from .data.db import DriveRef
from .data.model import Column
from .data.reader import align, open_log
from .data.retention import RetentionPolicy
from .data.store import DriveWriter, Store
from .triggers import MANUAL, Capture, Fired, TriggerEngine, TriggerRule, default_rules, external_rule

LINK = "link"
ECU_STREAM = "ecu"
LINK_STREAM = "ecu/link"


def log_dir() -> Path:
    """The folder for loose files: exports, reports, fetched black box
    captures. The drives themselves are in the database (``db.db_path()``)."""
    return db.files_dir()


@dataclass
class CaptureEvent:
    """A capture was saved. ``path`` names it in the database (``open_log``
    takes it; ``path.name`` is like ``stall_20261116_071455``). ``reason`` is
    the rule's short code: "rpm" and "link" for the two stall rules."""

    path: DriveRef
    reason: str
    t: float                # session time of the (first) trigger
    rows: int
    rule: str = ""
    label: str = ""
    message: str = ""


StallEvent = CaptureEvent       # the 0.1.0 name


@dataclass
class TriggerStatus:
    """What the stall catcher is doing right now, for a status chip."""

    armed: bool = False             # a stall rule is armed (the engine has been seen running)
    capturing: bool = False         # a capture window is open
    pre_s: float = 30.0
    post_s: float = 10.0
    buffer_s: float = 0.0           # seconds of log a trigger could reach back over now
    capture_ends_t: float = 0.0     # session time at which the open window closes
    fired: int = 0                  # rule firings so far


def column_for(ch: Any) -> Column:
    """An address map channel, math channel or black box channel as a log column."""
    return Column(ch.name, "f4", ch.units, ch.label if ch.label != ch.name else "",
                  ch.kind, ch.decimals, float(ch.min), float(ch.max))


def obd2_stream(amap: Any, pid: int, pv: Any) -> tuple[str, Column, dict[str, Any]]:
    """Stream name, column and attrs for one Mode 01 PID (``pv`` is its decoded
    ``hondalog.obd2.PidValue``). A PID that is the same quantity as a map
    channel gets that channel's name, with the PID's own (standard) units."""
    from .obd2 import PID_CHANNEL
    ch = amap.channel(PID_CHANNEL.get(pid, ""))
    if ch is None:
        column = Column(f"pid_{pid:02x}", "f4", pv.units, pv.name)
    else:
        column = Column(ch.name, "f4", pv.units or ch.units, ch.label if ch.label != ch.name else "",
                        ch.kind, ch.decimals, float(ch.min), float(ch.max))
    return f"obd2/{pid:02X}", column, {"mode": 1, "pid": pid, "name": pv.name}


class Recorder:
    """One recording session: the drives, the trigger engine, the captures.

    ``feed*`` are called on the poller thread; ``mark``, ``note``, ``trigger``
    and ``stop`` on the UI thread. Finished captures appear on ``events``;
    every rule firing appears at once on ``notices`` (for a banner or a beep
    before the capture's post-window is over).

    ``out_dir`` is the folder that holds ``hondalog.db`` (``db`` names the
    file itself). ``stall_opts`` (pre_s, post_s, rpm_run, rpm_zero,
    link_down_s, rpm_channel) tune the built-in stall rules; pass ``rules``
    to replace the whole rule list.
    """

    def __init__(self, amap: Any, ecu_id: str = "", port: str = "", out_dir: str | os.PathLike | None = None, *,
                 db_file: str | os.PathLike | None = None, rules: Sequence[TriggerRule] | None = None,
                 drive_gap_s: float | None = 120.0, retention: RetentionPolicy | None = None,
                 flush_interval: float = 0.5, fsync: bool = True, sync_captures: bool = False,
                 epoch: datetime | None = None, meta: Mapping[str, Any] | None = None,
                 **stall_opts: Any):
        self.amap = amap
        self.db = Path(db_file) if db_file else db.db_path(out_dir)
        self.out_dir = self.db.parent
        self.started = datetime.now()
        #: called with every Fired, on the feeding thread (LiveSession tells the black box)
        self.on_fired: Callable[[Fired], None] | None = None
        self.drive_gap_s = drive_gap_s
        self.retention = retention
        self.sync_captures = sync_captures
        rule_list = list(rules) if rules is not None else default_rules(**stall_opts)
        try:
            map_text = Path(amap.path).read_text(encoding="utf-8") if amap.path else ""
        except OSError:
            map_text = ""
        self.header: dict[str, Any] = {
            "format": f"hondalog {__version__}",
            "started": self.started.isoformat(timespec="seconds"),
            "started_unix": self.started.timestamp(),
            "timebase": "seconds since connect (monotonic)",
            "ecu": amap.ecu,
            "ecu_id": ecu_id or "(not read)",
            "port": port,
            "address_map_version": amap.version,
            # version plus the hash of everything that decides a decoded value
            "address_map_tag": getattr(amap, "version_tag", amap.version),
            "address_map_hash": getattr(amap, "version_hash", ""),
            "ecu_variant": getattr(amap, "variant", ""),
            "address_map_file": amap.path,
            "address_map_text": map_text,
            "unverified_channels": " ".join(c.name for c in amap.channels if not c.verified) or "none",
            "units": " ".join(f"{c.name}={c.units or '-'}" for c in amap.channels),
            "triggers": [r.to_dict() for r in rule_list],
        }
        self.header.update(meta or {})
        if epoch is not None:
            self.header["epoch_unix"] = epoch.timestamp()
        self._names: list[str] = list(amap.names)
        self._unknown = {name: None for name in self._names}
        self._block_channels: dict[tuple[int, int], list[Any]] = {}
        self._custom: dict[str, list[str]] = {}

        #: every drive of this session, oldest first
        self.drives: list[DriveRef] = []
        self._store = Store(self.db, flush_interval=flush_interval, fsync=fsync)
        self._log: DriveWriter | None = None
        self._open_drive(self.started)
        self._engine = TriggerEngine(rule_list)
        self._lock = threading.Lock()
        self._pending_markers: list[str] = []
        self._epoch: datetime | None = epoch    # wall time of session time 0
        self._last_t = 0.0
        self._t_first: float | None = None      # session time of the first thing recorded
        self._drive_t0: float | None = None     # ... of the first thing in the current drive
        self._down_since: float | None = None   # no sign of life since then: ECU silent, key not seen on
        self._key_off_seen = False              # ... and another source saw the key off meanwhile
        self._link: bool | None = None
        self.events: queue.Queue[CaptureEvent] = queue.Queue()
        self.notices: queue.Queue[Fired] = queue.Queue()
        self.markers = 0
        self.rows = 0               # sweeps or reads fed so far
        self.active = True

    # -- properties --

    @property
    def path(self) -> DriveRef:
        """The drive being written now (the last one of the session)."""
        return self.drives[-1]

    drive = path

    @property
    def parked(self) -> bool:
        """Between drives: for ``drive_gap_s`` the ECU has been silent and
        nothing else has shown the key to be on."""
        return self._log is None

    @property
    def rules(self) -> list[TriggerRule]:
        return self._engine.rules

    @property
    def capturing(self) -> bool:
        return self._engine.capturing

    def status(self) -> TriggerStatus:
        """The trigger engine's state (safe from any thread; a snapshot)."""
        engine = self._engine
        stall = [r for r in engine.rules if r.enabled and r.kind == "stall"] or \
                [r for r in engine.rules if r.enabled]
        out = TriggerStatus()
        if stall:
            out.pre_s, out.post_s = stall[0].pre_s, stall[0].post_s
            out.armed = any(engine.armed(r.name) for r in stall)
        out.fired = sum(engine.fired_count(r.name) for r in engine.rules)
        if self._drive_t0 is not None and self._log is not None:
            out.buffer_s = max(0.0, min(out.pre_s, self._last_t - self._drive_t0))
        capture = engine.capture
        if capture is not None:
            out.capturing = True
            out.capture_ends_t = capture.t_end
        return out

    @property
    def write_error(self) -> Exception | None:
        """The last database error, if the log could not be written."""
        return self._store.error

    # -- UI thread --

    def mark(self, text: str = "MARK") -> None:
        """Drop a marker. It gets the time of the next thing fed."""
        with self._lock:
            self._pending_markers.append(text)
            self.markers += 1

    def note(self, text: str) -> None:
        """A free-text note at the current time (shown in the event list, not as a marker)."""
        with self._lock:
            if self.active:
                self._drive().event(self._last_t, "note", text)

    def set_meta(self, **items: Any) -> None:
        """Add facts to the drive's header: DTCs, a freeze frame, expert-unlock, notes, tags."""
        with self._lock:
            if self.active:
                self.header.update(items)
                if self._log is not None:
                    self._log.set_meta(**items)

    def event(self, kind: str, text: str = "", data: Mapping[str, Any] | None = None,
              t: float | None = None) -> None:
        """Record an event of any kind ("alarm", "dtc", "link", ...)."""
        with self._lock:
            if self.active:
                self._drive().event(self._last_t if t is None else t, kind, text, data)

    def trigger(self, rule: TriggerRule | str = MANUAL, t: float | None = None) -> None:
        """Start a capture by hand, from an alarm, or from a black box trigger."""
        with self._lock:
            if self.active:
                self._handle_fired([self._engine.fire(rule, self._last_t if t is None else t)])

    def external_trigger(self, why: str, t: float | None = None) -> None:
        """Another source (the black box) reported a trigger: save a capture
        around now. It goes through the same engine as the rules, so a stall
        the ECU log also sees within the window lands in the same capture."""
        with self._lock:
            if self.active:
                rule = external_rule(why, self._engine.rules)
                self._handle_fired([self._engine.fire(rule, self._last_t if t is None else t)])

    def flush(self) -> None:
        """Commit what has been fed so far (a reader sees it afterwards)."""
        self._store.flush()

    def stop(self) -> None:
        with self._lock:
            if not self.active:
                return
            self.active = False
            capture = self._engine.finish()
            if capture is not None:
                self._save_capture(capture, wait=True)
            try:
                self._end_drive("stopped")
                self._store.close()
            except OSError:
                pass
        if self.retention is not None:
            self.retention.apply(self.db)

    # -- poller thread --

    def feed(self, sample: Any) -> None:
        """One row of decoded values for every channel (anything with ``t``,
        ``link`` and ``values``). No raw bytes: such a log cannot be decoded
        again. The live path uses ``feed_read`` instead."""
        with self._lock:
            if not self.active or not self._begin(sample.t, getattr(sample, "wall", None), bool(sample.link)):
                return
            self._after(sample.t, self._sweep(sample))

    def feed_read(self, t: float, addr: int, data: bytes, latency: float | None = None) -> None:
        """One K-line read: ``data`` is what the ECU returned for ``addr``.
        ``latency`` is the estimated age of the reply in seconds, if known."""
        with self._lock:
            if not self.active:
                return
            self._begin(t, None, True)
            changed = self._read(t, addr, bytes(data), latency)
            changed.update(self._set_link(t, True))
            self._after(t, changed)

    def feed_pid(self, t: float, pid: int, data: bytes) -> None:
        """One OBD2 Mode 01 reply (OBD2-only live mode): ``data`` is the PID's
        data bytes. Stored raw in an ``obd2/XX`` stream, as the handheld's
        OBD2 drives are imported."""
        from .obd2 import decode_pid
        pv = decode_pid(pid, data)
        with self._lock:
            if not self.active:
                return
            self._begin(t, None, True)
            log = self._drive()
            name, column, attrs = obd2_stream(self.amap, pid, pv)
            if log.stream(name) is None:
                log.add_stream(name, [column], source="obd2", raw_len=len(data), attrs=attrs)
            try:
                log.append(name, t, [pv.value], bytes(data))
            except ValueError:      # another length than the first reply: keep the stream consistent
                pass
            changed: dict[str, Any] = {column.name: pv.value}
            changed.update(self._set_link(t, True))
            self._after(t, changed)

    def feed_link(self, t: float, up: bool) -> None:
        """The link went up or down (no reply, port lost)."""
        with self._lock:
            if not self.active or not self._begin(t, None, up):
                return
            self._after(t, self._set_link(t, up))

    def feed_values(self, stream: str, t: float, values: Mapping[str, float | None], *,
                    source: str = "", columns: Sequence[Column] | None = None,
                    evaluate: bool = True, alive: bool | None = None) -> None:
        """One row of any other stream on the session timebase. The stream's
        columns are fixed by the first call (``columns``, or the keys of
        ``values`` as 32-bit floats).

        ``alive``: this source can tell whether the car is switched on, and
        says it is (True) or is not (False). While it is, the drive stays
        open although the ECU is silent, and a new drive starts if there is
        none. None (the default): the source knows nothing about that; its
        rows are recorded while a drive is open and dropped between drives."""
        with self._lock:
            if not self.active or not self._begin(t, None, None, alive):
                return
            log = self._drive()
            names = self._custom.get(stream)
            if names is None:
                cols = list(columns) if columns is not None else [Column(n) for n in values]
                log.add_stream(stream, cols, source=source or stream.split("/")[0])
                names = self._custom[stream] = [c.name for c in cols]
            log.append(stream, t, [values.get(n) for n in names])
            if evaluate:
                self._after(t, values)

    # -- internals (lock held) --

    def _open_drive(self, started: datetime) -> DriveWriter:
        meta = dict(self.header, drive_of_session=len(self.drives) + 1)
        meta["started"] = started.isoformat(timespec="seconds")
        meta["started_unix"] = started.timestamp()
        if self.drives:
            meta["previous_drive"] = self.drives[-1].name
        self._log = self._store.begin_drive(meta, started=started)
        self.drives.append(self._log.ref)
        self._custom = {}
        self._drive_t0 = None
        self._link = None
        return self._log

    def _drive(self) -> DriveWriter:
        """The drive being written; starts one if the recorder was parked."""
        if self._log is None:
            self._open_drive(self._wall(self._last_t))
        assert self._log is not None
        return self._log

    def _end_drive(self, reason: str) -> None:
        log, self._log = self._log, None
        if log is not None:
            log.end(reason=reason)

    def _begin(self, t: float, wall: datetime | None, link: bool | None,
               alive: bool | None = None) -> bool:
        """Bookkeeping before a row is stored. ``link``: this feed shows the
        ECU answering (True), silent (False), or says nothing about it (None).
        ``alive``: another source sees the key on (True), off (False), or
        cannot tell (None). Returns False when the recorder is between
        drives and the row is dropped."""
        if self._epoch is None:
            self._epoch = (wall or datetime.now()) - timedelta(seconds=t)
            # wall time of session time 0: what lines this log up with a
            # black box capture, or with another drive of the same session
            self.header["epoch_unix"] = self._epoch.timestamp()
            if self._log is not None:
                self._log.set_meta(epoch_unix=self.header["epoch_unix"])
        if link or alive:
            self._down_since = None                 # a sign of life: the ECU, or the key seen on
            self._key_off_seen = False
        else:
            if alive is False:
                self._key_off_seen = True
            if self._down_since is None and ((link is False and self._link is not None) or alive is False):
                self._down_since = t
        if self._log is not None and self._down_since is not None and self.drive_gap_s is not None \
                and t - self._down_since >= self.drive_gap_s and not self._engine.capturing:
            # this drive is over. "key_off" when a second witness saw the key off;
            # "link_lost" when all that is known is that the ECU stopped answering
            self._end_drive("key_off" if self._key_off_seen else "link_lost")
        if self._log is None:
            if not (link or alive):
                self._last_t = t
                return False
            log = self._open_drive(self._wall(t))
            if not link:                            # the key is on and the ECU has not answered
                log.set_meta(started_by="key on seen by another source, ECU silent")
        if self._t_first is None:
            self._t_first = t
        if self._drive_t0 is None:
            self._drive_t0 = t
        if self._pending_markers:
            for text in self._pending_markers:
                self._drive().event(t, "marker", text)
            self._pending_markers.clear()
        self._last_t = t
        self.rows += 1
        return True

    def _sweep(self, sample: Any) -> dict[str, Any]:
        log = self._drive()
        if log.stream(ECU_STREAM) is None:
            cols = [Column(LINK, "u1", kind="flag", decimals=0)]
            cols += [column_for(c) for c in self.amap.channels]
            log.add_stream(ECU_STREAM, cols, source="ecu", attrs={"mode": "sweep"})
        values = sample.values
        log.append(ECU_STREAM, sample.t, [1 if sample.link else 0, *[values.get(n) for n in self._names]])
        self._link = bool(sample.link)
        if sample.link:
            return {**values, LINK: 1}
        return {**self._unknown, LINK: 0}

    def _read(self, t: float, addr: int, data: bytes, latency: float | None) -> dict[str, Any]:
        key = (addr, len(data))
        channels = self._block_channels.get(key)
        if channels is None:
            channels = self._block_channels[key] = [
                c for c in self.amap.channels if addr <= c.addr and c.addr + c.length <= addr + len(data)]
        name = f"ecu/{addr:02X}+{len(data)}"
        log = self._drive()
        if log.stream(name) is None:
            log.add_stream(name, [column_for(c) for c in channels], source="ecu", raw_len=len(data),
                           attrs={"addr": addr, "length": len(data), "mode": "read"})
        values = [c.convert(data[c.addr - addr:c.addr - addr + c.length]) for c in channels]
        log.append(name, t, values, data, latency)
        return dict(zip((c.name for c in channels), values))

    def _set_link(self, t: float, up: bool) -> dict[str, Any]:
        """Log a link change. Returns what the trigger engine has to hear."""
        log = self._drive()
        if log.stream(LINK_STREAM) is None:
            log.add_stream(LINK_STREAM, [Column(LINK, "u1", kind="flag", decimals=0)], source="ecu")
            self._link = None
        if up != self._link or not up:
            log.append(LINK_STREAM, t, [1 if up else 0])
        changed = up != self._link
        self._link = up
        if up:
            return {LINK: 1}
        # every value is unknown from here on; say so once, not on every probe
        return {**self._unknown, LINK: 0} if changed else {LINK: 0}

    def _after(self, t: float, values: Mapping[str, Any] | None = None) -> None:
        fired, done = self._engine.feed(t, values)
        self._handle_fired(fired)
        if done is not None:
            self._save_capture(done, wait=self.sync_captures)

    def _handle_fired(self, fired: Sequence[Fired]) -> None:
        for event in fired:
            rule = event.rule
            if self.on_fired is not None:
                try:
                    self.on_fired(event)
                except Exception:           # a listener must not stop the recording
                    pass
            if "mark" in rule.actions:
                self._drive().event(event.t, "trigger", rule.label,
                                    {"rule": rule.name, "reason": rule.reason, "message": rule.message,
                                     "severity": rule.severity})
            if "notify" in rule.actions:
                self.notices.put(event)

    def _wall(self, t: float) -> datetime:
        return (self._epoch or self.started) + timedelta(seconds=t)

    def _save_capture(self, capture: Capture, wait: bool) -> None:
        """Enter the capture window in the database. With ``wait`` it is
        there when this returns; otherwise the store's thread does it."""
        first = capture.first
        rule = first.rule
        wall = self._wall(first.t)
        log = self._drive()
        t_start = capture.t_start if self._drive_t0 is None else max(capture.t_start, self._drive_t0)
        fired = [{"rule": f.rule.name, "t": round(f.t, 3)} for f in capture.fired]
        meta: dict[str, Any] = {
            "trigger": rule.name,
            "trigger_reason": rule.reason,
            "trigger_message": rule.message,
            "trigger_time_s": f"{first.t:.3f}",
            "trigger_wall_time": wall.isoformat(timespec="milliseconds"),
            "triggers_fired": fired,
            "window": f"{rule.pre_s:g} s before, {rule.post_s:g} s after",
            "window_complete": capture.complete,
            # the 0.1.0 names, which the replay tab and old scripts look for
            "stall_trigger": rule.message,
            "stall_time_s": f"{first.t:.3f}",
            "stall_wall_time": wall.isoformat(timespec="milliseconds"),
        }

        def saved(ref: DriveRef, rows: int) -> None:
            self.events.put(CaptureEvent(ref, rule.reason, first.t, rows, rule.name, rule.label, rule.message))
        try:
            log.capture(rule.kind, first.t, t_start, capture.t_end, rule=rule.name, reason=rule.reason,
                        label=rule.label, message=rule.message, severity=rule.severity, wall=wall,
                        complete=capture.complete, fired=fired, meta=meta, wait=wait,
                        on_saved=saved)
        except (OSError, ValueError) as exc:
            self._store.error = exc
        except Exception as exc:                # sqlite3.Error: the capture is lost, the recording goes on
            self._store.error = OSError(str(exc))


# ---- reading (the shape the 0.1.0 replay tab uses) --------------------------

@dataclass
class LogData:
    """A log flattened onto one time grid: what the replay tab draws."""

    path: Any                                       # a DriveRef, or the Path of a file
    meta: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    t: Any = None                                   # numpy array of seconds
    data: dict = field(default_factory=dict)        # name -> numpy array (NaN = missing)
    markers: list = field(default_factory=list)     # (time_s, text)


def read_log(path: str | os.PathLike | DriveRef) -> LogData:
    """Open any supported log (a drive or capture in the database, an old
    .csv, .msl, .mlg) as one flat table.

    Channels read at different rates are put on the grid of the densest one.
    New code should use ``hondalog.data.open_log`` and keep the real rates.
    A black box capture (``.bbx``) is opened through
    ``hondalog.blackbox.open_event``, merged with its ECU log.
    """
    if str(path).lower().endswith(".bbx"):
        from .blackbox.importer import open_event
        return open_event(path)
    with open_log(path) as log:
        if not log.channels or log.rows == 0:
            raise ValueError(f"{path}: no data")
        names = log.channels
        grid = max((log.series(n) for n in names), key=len).t
        link = log.series(LINK) if LINK in names else None
        if link is not None and len(link) and len(link) < len(grid):
            # per-read log: the link is its own stream, written when it changes and
            # while it is down. Keep those moments on the grid (the ECU channels
            # stop there) and hold the state between them.
            grid = np.union1d(grid, link.t)
        table = log.table(names, t=grid)
        columns = dict(table.columns)
        if link is not None and len(link):
            columns[LINK] = align(link, table.t, "hold", float("inf"))
        meta = {k: v if isinstance(v, str) else str(v) for k, v in log.meta.items()
                if k != "address_map_text"}
        where = path if isinstance(path, DriveRef) else Path(path)
        return LogData(where, meta, list(table.names), table.t, columns, list(log.markers))
