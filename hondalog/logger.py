"""Recording: the running native log, the triggers and the captures they save.

One ``Recorder`` is one recording session. It is fed on the poller thread
and writes a ``.hlog`` file (see ``hondalog/data/container.py``) through a
writer that never makes the caller wait for the disk. Triggers (the stall
rules by default) are evaluated on every feed; when a capture window closes
it is cut out of the running log into its own file, with every stream that
was being recorded at the time.

How it is fed (``hondalog.live.LiveSession`` does this wiring):

    feed_read(t, addr, data)     one K-line read with its own timestamp
                                 (``Poller.on_block``); the raw bytes are
                                 stored next to the decoded values
    feed_link(t, up)             the ECU stopped or started answering
    feed_values(stream, t, {})   anything else on the session timebase:
                                 math channels, black box frames
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
from .data.container import EXTENSION, LogWriter
from .data.model import Column
from .data.reader import align, open_log
from .data.retention import RetentionPolicy
from .data.store import extract_window
from .triggers import MANUAL, Capture, Fired, TriggerEngine, TriggerRule, default_rules, external_rule

LINK = "link"
ECU_STREAM = "ecu"
LINK_STREAM = "ecu/link"


def log_dir() -> Path:
    return Path(os.environ.get("HONDALOG_DIR") or os.path.expanduser("~/hondalog/logs"))


@dataclass
class CaptureEvent:
    """A capture file was written. ``reason`` is the rule's short code:
    "rpm" and "link" for the two stall rules."""

    path: Path
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


def _unique(path: Path) -> Path:
    n, out = 2, path
    while out.exists():
        out = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return out


class Recorder:
    """One recording session: the native log, the trigger engine, the captures.

    ``feed*`` are called on the poller thread; ``mark``, ``note``, ``trigger``
    and ``stop`` on the UI thread. Finished captures appear on ``events``;
    every rule firing appears at once on ``notices`` (for a banner or a beep
    before the capture's post-window is over).

    ``stall_opts`` (pre_s, post_s, rpm_run, rpm_zero, link_down_s,
    rpm_channel) tune the built-in stall rules; pass ``rules`` to replace the
    whole rule list.
    """

    def __init__(self, amap: Any, ecu_id: str = "", port: str = "", out_dir: Path | None = None, *,
                 rules: Sequence[TriggerRule] | None = None, rotate_s: float | None = 3600.0,
                 rotate_mb: float | None = 64.0, retention: RetentionPolicy | None = None,
                 flush_interval: float = 0.5, fsync: bool = True, sync_captures: bool = False,
                 epoch: datetime | None = None, meta: Mapping[str, Any] | None = None,
                 **stall_opts: Any):
        self.amap = amap
        self.out_dir = Path(out_dir) if out_dir else log_dir()
        self.started = datetime.now()
        #: called with every Fired, on the feeding thread (LiveSession tells the black box)
        self.on_fired: Callable[[Fired], None] | None = None
        self.rotate_s, self.rotate_mb = rotate_s, rotate_mb
        self.retention = retention
        self.sync_captures = sync_captures
        self._writer_opts: dict[str, Any] = {"flush_interval": flush_interval, "fsync": fsync}
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

        self.paths: list[Path] = []
        self._part = 0
        self._part_t0: float | None = None
        self._log = self._open_part()
        self._engine = TriggerEngine(rule_list)
        self._lock = threading.Lock()
        self._pending_markers: list[str] = []
        self._workers: list[threading.Thread] = []
        self._epoch: datetime | None = epoch    # wall time of session time 0
        self._last_t = 0.0
        self._t_first: float | None = None      # session time of the first thing recorded
        self._link: bool | None = None
        self.events: queue.Queue[CaptureEvent] = queue.Queue()
        self.notices: queue.Queue[Fired] = queue.Queue()
        self.markers = 0
        self.rows = 0               # sweeps or reads fed so far
        self.active = True

    # -- properties --

    @property
    def path(self) -> Path:
        """The file being written now (the last part of a rotated recording)."""
        return self.paths[-1]

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
        if self._t_first is not None:
            out.buffer_s = max(0.0, min(out.pre_s, self._last_t - self._t_first))
        capture = engine.capture
        if capture is not None:
            out.capturing = True
            out.capture_ends_t = capture.t_end
        return out

    @property
    def write_error(self) -> Exception | None:
        """The last disk error, if the log could not be written."""
        return self._log.error

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
                self._log.event(self._last_t, "note", text)

    def set_meta(self, **items: Any) -> None:
        """Add facts to the log header: DTCs, a freeze frame, expert-unlock, notes, tags."""
        with self._lock:
            if self.active:
                self.header.update(items)
                self._log.set_meta(**items)

    def event(self, kind: str, text: str = "", data: Mapping[str, Any] | None = None,
              t: float | None = None) -> None:
        """Record an event of any kind ("alarm", "dtc", "link", ...)."""
        with self._lock:
            if self.active:
                self._log.event(self._last_t if t is None else t, kind, text, data)

    def trigger(self, rule: TriggerRule | str = MANUAL, t: float | None = None) -> None:
        """Start a capture by hand, from an alarm, or from a black box trigger."""
        with self._lock:
            if self.active:
                self._handle_fired([self._engine.fire(rule, self._last_t if t is None else t)])

    def external_trigger(self, why: str, t: float | None = None) -> None:
        """Another source (the black box) reported a trigger: save a capture
        around now. It goes through the same engine as the rules, so a stall
        the ECU log also sees within the window lands in the same file."""
        with self._lock:
            if self.active:
                rule = external_rule(why, self._engine.rules)
                self._handle_fired([self._engine.fire(rule, self._last_t if t is None else t)])

    def stop(self) -> None:
        with self._lock:
            if not self.active:
                return
            self.active = False
            capture = self._engine.finish()
            try:
                self._log.close()
            except OSError:
                pass
            if capture is not None:
                self._save_capture(capture, list(self.paths[-2:]))
        for worker in self._workers:
            worker.join()
        if self.retention is not None:
            self.retention.apply(self.out_dir)

    # -- poller thread --

    def feed(self, sample: Any) -> None:
        """One row of decoded values for every channel (anything with ``t``,
        ``link`` and ``values``). No raw bytes: such a log cannot be decoded
        again. The live path uses ``feed_read`` instead."""
        with self._lock:
            if not self.active:
                return
            self._begin(sample.t, getattr(sample, "wall", None))
            self._after(sample.t, self._sweep(sample))

    def feed_read(self, t: float, addr: int, data: bytes) -> None:
        """One K-line read: ``data`` is what the ECU returned for ``addr``."""
        with self._lock:
            if not self.active:
                return
            self._begin(t, None)
            changed = self._read(t, addr, bytes(data))
            changed.update(self._set_link(t, True))
            self._after(t, changed)

    def feed_link(self, t: float, up: bool) -> None:
        """The link went up or down (no reply, port lost)."""
        with self._lock:
            if not self.active:
                return
            self._begin(t, None)
            self._after(t, self._set_link(t, up))

    def feed_values(self, stream: str, t: float, values: Mapping[str, float | None], *,
                    source: str = "", columns: Sequence[Column] | None = None,
                    evaluate: bool = True) -> None:
        """One row of any other stream on the session timebase. The stream's
        columns are fixed by the first call (``columns``, or the keys of
        ``values`` as 32-bit floats)."""
        with self._lock:
            if not self.active:
                return
            self._begin(t, None)
            names = self._custom.get(stream)
            if names is None:
                cols = list(columns) if columns is not None else [Column(n) for n in values]
                self._log.add_stream(stream, cols, source=source or stream.split("/")[0])
                names = self._custom[stream] = [c.name for c in cols]
            self._log.append(stream, t, [values.get(n) for n in names])
            if evaluate:
                self._after(t, values)

    # -- internals (lock held) --

    def _open_part(self) -> LogWriter:
        self._part += 1
        stem = f"log_{self.started:%Y%m%d_%H%M%S}"
        if self._part > 1:
            stem += f"_p{self._part:03d}"
        path = _unique(self.out_dir / (stem + EXTENSION))
        meta = dict(self.header, part=self._part)
        if self.paths:
            meta["previous_part"] = self.paths[-1].name
        writer = LogWriter(path, meta, **self._writer_opts)
        self.paths.append(path)
        self._part_t0 = None
        self._custom = {}
        return writer

    def _begin(self, t: float, wall: datetime | None) -> None:
        if self._epoch is None:
            self._epoch = (wall or datetime.now()) - timedelta(seconds=t)
            # wall time of session time 0: what lines this log up with a
            # black box capture, or with another log of the same drive
            self.header["epoch_unix"] = self._epoch.timestamp()
            self._log.set_meta(epoch_unix=self.header["epoch_unix"])
        if self._t_first is None:
            self._t_first = t
        if self._part_t0 is None:
            self._part_t0 = t
        elif not self._engine.capturing and (
                (self.rotate_s and t - self._part_t0 >= self.rotate_s)
                or (self.rotate_mb and self._log.bytes_written >= self.rotate_mb * 1e6)):
            self._rotate()
            self._part_t0 = t
        if self._pending_markers:
            for text in self._pending_markers:
                self._log.event(t, "marker", text)
            self._pending_markers.clear()
        self._last_t = t
        self.rows += 1

    def _rotate(self) -> None:
        old = self._log
        old.flush()                     # rows reach the file now; the index follows below
        self._log = self._open_part()
        worker = threading.Thread(target=old.close, name="hondalog-rotate", daemon=True)
        worker.start()
        self._workers.append(worker)

    def _sweep(self, sample: Any) -> dict[str, Any]:
        log = self._log
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

    def _read(self, t: float, addr: int, data: bytes) -> dict[str, Any]:
        key = (addr, len(data))
        channels = self._block_channels.get(key)
        if channels is None:
            channels = self._block_channels[key] = [
                c for c in self.amap.channels if addr <= c.addr and c.addr + c.length <= addr + len(data)]
        name = f"ecu/{addr:02X}+{len(data)}"
        log = self._log
        if log.stream(name) is None:
            log.add_stream(name, [column_for(c) for c in channels], source="ecu", raw_len=len(data),
                           attrs={"addr": addr, "length": len(data), "mode": "read"})
        values = [c.convert(data[c.addr - addr:c.addr - addr + c.length]) for c in channels]
        log.append(name, t, values, data)
        return dict(zip((c.name for c in channels), values))

    def _set_link(self, t: float, up: bool) -> dict[str, Any]:
        """Log a link change. Returns what the trigger engine has to hear."""
        log = self._log
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
            paths = list(self.paths[-2:])
            if self.sync_captures:
                self._save_capture(done, paths)
            else:
                worker = threading.Thread(target=self._save_capture, args=(done, paths),
                                          name="hondalog-capture", daemon=True)
                worker.start()
                self._workers = [w for w in self._workers if w.is_alive()] + [worker]

    def _handle_fired(self, fired: Sequence[Fired]) -> None:
        for event in fired:
            rule = event.rule
            if self.on_fired is not None:
                try:
                    self.on_fired(event)
                except Exception:           # a listener must not stop the recording
                    pass
            if "mark" in rule.actions:
                self._log.event(event.t, "trigger", rule.label,
                                {"rule": rule.name, "reason": rule.reason, "message": rule.message,
                                 "severity": rule.severity})
            if "notify" in rule.actions:
                self.notices.put(event)

    def _wall(self, t: float) -> datetime:
        return (self._epoch or self.started) + timedelta(seconds=t)

    def _save_capture(self, capture: Capture, sources: Sequence[Path]) -> None:
        """Cut the capture window out of the running log (worker thread)."""
        first = capture.first
        rule = first.rule
        wall = self._wall(first.t)
        meta = {
            "capture_of": sources[-1].name,
            "trigger": rule.name,
            "trigger_reason": rule.reason,
            "trigger_message": rule.message,
            "trigger_time_s": f"{first.t:.3f}",
            "trigger_wall_time": wall.isoformat(timespec="milliseconds"),
            "triggers_fired": [{"rule": f.rule.name, "t": round(f.t, 3)} for f in capture.fired],
            "window": f"{rule.pre_s:g} s before, {rule.post_s:g} s after",
            "window_complete": capture.complete,
            # the 0.1.0 names, which the replay tab and old scripts look for
            "stall_trigger": rule.message,
            "stall_time_s": f"{first.t:.3f}",
            "stall_wall_time": wall.isoformat(timespec="milliseconds"),
        }
        try:
            if not self._log.closed:
                self._log.flush()
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = _unique(self.out_dir / f"{rule.kind}_{wall:%Y%m%d_%H%M%S}{EXTENSION}")
            _, rows = extract_window([p for p in sources if p.exists()], path,
                                     capture.t_start, capture.t_end, meta)
        except (OSError, ValueError) as exc:
            self._log.error = exc
            return
        self.events.put(CaptureEvent(path, rule.reason, first.t, rows, rule.name, rule.label, rule.message))


# ---- reading (the shape the 0.1.0 replay tab uses) --------------------------

@dataclass
class LogData:
    """A log flattened onto one time grid: what the replay tab draws."""

    path: Path
    meta: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    t: Any = None                                   # numpy array of seconds
    data: dict = field(default_factory=dict)        # name -> numpy array (NaN = missing)
    markers: list = field(default_factory=list)     # (time_s, text)


def read_log(path: str | os.PathLike) -> LogData:
    """Open any supported log (.hlog, old .csv, .msl, .mlg) as one flat table.

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
        return LogData(Path(path), meta, list(table.names), table.t, columns, list(log.markers))
