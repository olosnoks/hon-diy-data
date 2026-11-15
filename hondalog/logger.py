"""CSV logging, the stall catcher, and reading logs back for replay.

File format (logs and stall captures are the same):

    # hondalog 0.1.0
    # key: value                 <- header lines, one per fact
    time_s,wall_time,link,rpm,...,marker
    12.345,2026-11-16T08:01:02.123,1,752,...,
"""

from __future__ import annotations

import collections
import csv
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .poller import Sample

FIXED_COLS = ("time_s", "wall_time", "link")
MARKER_COL = "marker"


def log_dir() -> Path:
    return Path(os.environ.get("HONDALOG_DIR") or os.path.expanduser("~/hondalog/logs"))


def _row(sample: Sample, names, marker: str) -> list:
    row = [f"{sample.t:.3f}", sample.wall.isoformat(timespec="milliseconds"),
           1 if sample.link else 0]
    for name in names:
        value = sample.values.get(name)
        row.append("" if value is None else f"{value:.4g}" if abs(value) < 1e5 else f"{value:.0f}")
    row.append(marker)
    return row


def write_csv(path: Path, names, header: dict, rows) -> None:
    """Write a whole file at once. rows = iterable of (Sample, marker)."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        _write_header(fh, names, header)
        w = csv.writer(fh)
        for sample, marker in rows:
            w.writerow(_row(sample, names, marker))


def _write_header(fh, names, header: dict) -> None:
    fh.write(f"# hondalog {__version__}\n")
    for key, value in header.items():
        fh.write(f"# {key}: {str(value).replace(chr(10), ' ')}\n")
    csv.writer(fh).writerow([*FIXED_COLS, *names, MARKER_COL])


class CsvLogger:
    """Appends one row per sample. Line-buffered: a crash loses one row at most."""

    def __init__(self, path: Path, names, header: dict):
        self.path = Path(path)
        self.names = list(names)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w", newline="", encoding="utf-8", buffering=1)
        _write_header(self._fh, self.names, header)
        self._csv = csv.writer(self._fh)
        self.rows = 0

    def write(self, sample: Sample, marker: str = "") -> None:
        self._csv.writerow(_row(sample, self.names, marker))
        self.rows += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


@dataclass
class StallEvent:
    path: Path
    reason: str             # "rpm" or "link"
    t: float                # sample time of the trigger
    rows: int


class StallCatcher:
    """Keeps the last ``pre_s`` seconds; on a stall saves them plus ``post_s``.

    Triggers once the engine has been seen running (RPM above ``rpm_run``):
      * "rpm":  RPM reads below ``rpm_zero``
      * "link": no valid reply for ``link_down_s`` (ECU lost power, cable out)
    It re-arms when RPM goes above ``rpm_run`` again.
    """

    def __init__(self, out_dir: Path, names, header: dict, rpm_channel: str = "rpm",
                 pre_s: float = 30.0, post_s: float = 10.0, rpm_run: float = 400.0,
                 rpm_zero: float = 50.0, link_down_s: float = 1.0):
        self.out_dir = Path(out_dir)
        self.names = list(names)
        self.header = dict(header)
        self.rpm_channel = rpm_channel
        self.pre_s, self.post_s = pre_s, post_s
        self.rpm_run, self.rpm_zero, self.link_down_s = rpm_run, rpm_zero, link_down_s
        self._ring: collections.deque = collections.deque()
        self._armed = False
        self._link_lost_at: float | None = None
        self._capture: list | None = None       # rows being collected after a trigger
        self._trigger: tuple[str, float, datetime] | None = None

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    def feed(self, sample: Sample, marker: str = "") -> tuple[str | None, StallEvent | None]:
        """Returns (trigger_reason_or_None, finished_capture_or_None)."""
        if self._capture is not None:
            self._capture.append((sample, marker))
            if sample.t - self._trigger[1] >= self.post_s:
                return None, self.finish()
            return None, None

        self._ring.append((sample, marker))
        while self._ring and sample.t - self._ring[0][0].t > self.pre_s:
            self._ring.popleft()

        reason = None
        rpm = sample.values.get(self.rpm_channel) if sample.link else None
        if sample.link:
            self._link_lost_at = None
            if rpm is not None and rpm > self.rpm_run:
                self._armed = True
            elif rpm is not None and rpm < self.rpm_zero and self._armed:
                reason = "rpm"
        else:
            if self._link_lost_at is None:
                self._link_lost_at = sample.t
            if self._armed and sample.t - self._link_lost_at >= self.link_down_s:
                reason = "link"
        if reason:
            self._armed = False
            self._trigger = (reason, sample.t, sample.wall)
            self._capture = list(self._ring)
            self._ring.clear()
        return reason, None

    def finish(self) -> StallEvent | None:
        """Write the capture now (also called when recording stops early)."""
        if self._capture is None:
            return None
        reason, t, wall = self._trigger
        rows, self._capture, self._trigger = self._capture, None, None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"stall_{wall:%Y%m%d_%H%M%S}.csv"
        header = dict(self.header)
        header["stall_trigger"] = ("rpm dropped to 0" if reason == "rpm"
                                   else "link lost (no reply from ECU)")
        header["stall_time_s"] = f"{t:.3f}"
        header["stall_wall_time"] = wall.isoformat(timespec="milliseconds")
        header["window"] = f"{self.pre_s:g} s before, {self.post_s:g} s after"
        write_csv(path, self.names, header, rows)
        return StallEvent(path, reason, t, len(rows))


class Recorder:
    """One recording session: the running CSV log plus the stall catcher.

    ``feed`` is called on the poller thread, ``mark``/``stop`` on the UI
    thread. Finished stall captures appear on ``events``.
    """

    def __init__(self, amap, ecu_id: str = "", port: str = "", out_dir: Path | None = None,
                 **stall_opts):
        self.out_dir = Path(out_dir) if out_dir else log_dir()
        self.started = datetime.now()
        self.header = {
            "started": self.started.isoformat(timespec="seconds"),
            "ecu": amap.ecu,
            "ecu_id": ecu_id or "(not read)",
            "port": port,
            "address_map_version": amap.version,
            "address_map_file": amap.path,
            "unverified_channels": " ".join(c.name for c in amap.channels if not c.verified) or "none",
            "units": " ".join(f"{c.name}={c.units or '-'}" for c in amap.channels),
        }
        names = amap.names
        self.path = self.out_dir / f"log_{self.started:%Y%m%d_%H%M%S}.csv"
        self._log = CsvLogger(self.path, names, self.header)
        self._stall = StallCatcher(self.out_dir, names, self.header, **stall_opts)
        self._lock = threading.Lock()
        self._pending_marker = ""
        self.events: queue.Queue = queue.Queue()
        self.markers = 0
        self.active = True

    @property
    def rows(self) -> int:
        return self._log.rows

    def mark(self, text: str = "MARK") -> None:
        """Tag the next row. Called on a keypress."""
        with self._lock:
            self._pending_marker = (self._pending_marker + " | " + text
                                    if self._pending_marker else text)
            self.markers += 1

    def feed(self, sample: Sample) -> None:
        with self._lock:
            if not self.active:
                return
            marker, self._pending_marker = self._pending_marker, ""
            reason, event = self._stall.feed(sample, marker)
            if reason:
                # put the tag on the trigger row in both files
                tag = "STALL (rpm 0)" if reason == "rpm" else "STALL (link lost)"
                marker = f"{marker} | {tag}" if marker else tag
                self._stall._capture[-1] = (sample, marker)
            self._log.write(sample, marker)
            if event:
                self.events.put(event)

    def stop(self) -> None:
        with self._lock:
            if not self.active:
                return
            self.active = False
            event = self._stall.finish()
            if event:
                self.events.put(event)
            self._log.close()


# ---- reading ----------------------------------------------------------------

@dataclass
class LogData:
    path: Path
    meta: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    t: object = None                    # numpy array of seconds
    data: dict = field(default_factory=dict)        # name -> numpy array (NaN = missing)
    markers: list = field(default_factory=list)     # (time_s, text)


def read_log(path) -> LogData:
    import numpy as np

    log = LogData(Path(path))
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        body = []
        for line in fh:
            if line.startswith("#"):
                key, sep, value = line[1:].partition(":")
                if sep:
                    log.meta[key.strip()] = value.strip()
                else:
                    log.meta.setdefault("format", line[1:].strip())
            elif line.strip():
                body.append(line)
    rows = list(csv.reader(body))
    if not rows:
        raise ValueError(f"{path}: no data")
    cols = rows[0]
    if "time_s" not in cols:
        raise ValueError(f"{path}: not a hondalog CSV (no time_s column)")
    ti = cols.index("time_s")
    mi = cols.index(MARKER_COL) if MARKER_COL in cols else None
    skip = {"time_s", "wall_time", MARKER_COL}
    log.names = [c for c in cols if c not in skip]
    idx = {name: cols.index(name) for name in log.names}

    def num(row, i):
        try:
            return float(row[i])
        except (ValueError, IndexError):
            return float("nan")

    rows = [r for r in rows[1:] if r and num(r, ti) == num(r, ti)]
    log.t = np.array([num(r, ti) for r in rows], dtype=float)
    for name, i in idx.items():
        log.data[name] = np.array([num(r, i) for r in rows], dtype=float)
    if mi is not None:
        log.markers = [(num(r, ti), r[mi]) for r in rows if len(r) > mi and r[mi]]
    return log
