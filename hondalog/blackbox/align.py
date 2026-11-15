"""Put a black box capture and an ECU (K-line) log on one time axis.

The box counts microseconds since it booted; the ECU log counts seconds since
the app connected. ``estimate`` finds ``offset_s`` such that::

    ecu_time_s = box_time_s + offset_s

Three methods, best first:

``wall``     The laptop set the box's clock over USB (``BlackboxReader`` does
             this once a second from an NTP-style exchange, good to about a
             millisecond). The event file carries that clock and the ECU log
             carries wall time per row. Error: a few ms.
``markers``  A marker pressed in the app goes to both logs with the same
             number. The ECU log tags the *next* row, so each marker is late
             by up to one poll (about 100 ms); half a poll is subtracted.
             Error: about half a poll.
``rpm``      Cross-correlate the box's tach RPM with the ECU's RPM. Works with
             no clock and no markers, as long as RPM moved. Error: about one
             poll.

Note on what "ECU time" means: an ECU row is stamped when its sweep
*finished*; a value in it was read up to one sweep earlier.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..data.db import DriveRef, is_database, parse_ref
from ..data.reader import open_log
from . import format as F
from .eventfile import BlackboxEvent


@dataclass
class Alignment:
    offset_s: float | None          # None = could not align
    method: str = "none"            # "wall", "markers", "rpm", "none"
    error_s: float | None = None    # rough one-sigma of the estimate
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.offset_s is not None

    def to_ecu_s(self, box_us):
        """Box microseconds -> ECU log seconds."""
        return np.asarray(box_us) / 1e6 + self.offset_s


def ecu_wall_origin(path) -> float | None:
    """Unix time of session time 0 in an ECU log.

    A drive in the database carries it in its row (``epoch_unix``, written by
    the recorder). A 0.1.0 CSV has a wall_time column: the median over all
    rows is used, so one odd row does not matter."""
    ref = path if isinstance(path, DriveRef) else parse_ref(path)
    if ref is not None or is_database(path):
        with open_log(ref if ref is not None else path) as log:
            epoch = log.meta.get("epoch_unix")
        return float(epoch) if isinstance(epoch, (int, float)) else None
    origins = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = csv.reader(line for line in fh if not line.startswith("#"))
        cols = next(rows, None)
        if not cols or "time_s" not in cols or "wall_time" not in cols:
            return None
        ti, wi = cols.index("time_s"), cols.index("wall_time")
        for row in rows:
            try:
                origins.append(datetime.fromisoformat(row[wi]).timestamp() - float(row[ti]))
            except (ValueError, IndexError):
                continue
    return float(np.median(origins)) if origins else None


def _marker_offsets(ecu_log, ev: BlackboxEvent) -> list[float]:
    box = {}
    for r in ev.events:
        if r.code == F.EV_HOST_MARK:
            box.setdefault(r.value, r.t_us / 1e6)
    out = []
    for t, text in ecu_log.markers:
        for m in re.finditer(r"MARK (\d+)", text):
            n = int(m.group(1))
            if n in box:
                out.append(t - box[n])
    return out


def _rpm_offset(ecu_log, ev: BlackboxEvent, rpm_channel: str, step_s: float = 0.02):
    """Slide the box's RPM trace along the ECU's and take the best fit.

    The two need not cover the same stretch: every lag where at least half of
    the capture overlaps the ECU log is tried, and the mean squared difference
    over the overlap is minimised."""
    if rpm_channel not in ecu_log.data or len(ev.t_us) < 100 or len(ecu_log.t) < 10:
        return None
    box_t = ev.t_us / 1e6
    box = np.interp(np.arange(box_t[0], box_t[-1], step_s), box_t,
                    ev.samples["rpm_tach"].astype(float))
    ok = np.isfinite(ecu_log.data[rpm_channel])
    if ok.sum() < 10:
        return None
    grid = np.arange(ecu_log.t[0], ecu_log.t[-1], step_s)
    ecu = np.interp(grid, ecu_log.t[ok], ecu_log.data[rpm_channel][ok])
    n = len(box)
    if len(ecu) < 20 or box.std() < 20 or ecu.std() < 20:
        return None                             # flat traces say nothing
    pad = n // 2
    e = np.concatenate([np.zeros(pad), ecu, np.zeros(pad)])
    m = np.concatenate([np.zeros(pad), np.ones(len(ecu)), np.zeros(pad)])
    if len(e) < n:
        return None
    ones = np.ones(n)
    count = np.correlate(m, ones, mode="valid")
    err = (np.correlate(e * e, ones, mode="valid") - 2 * np.correlate(e, box, mode="valid")
           + np.correlate(m, box * box, mode="valid")) / np.maximum(count, 1)
    err[count < n / 2] = np.inf
    k = int(np.argmin(err))
    if not np.isfinite(err[k]):
        return None
    frac = 0.0
    if 0 < k < len(err) - 1 and np.isfinite(err[k - 1]) and np.isfinite(err[k + 1]):
        a, b, c = err[k - 1], err[k], err[k + 1]            # parabola through the minimum
        denom = a - 2 * b + c
        frac = 0.5 * (a - c) / denom if denom > 0 else 0.0
    return grid[0] + (k - pad + frac) * step_s - box_t[0]


def estimate(ecu_log, ev: BlackboxEvent, rpm_channel: str = "rpm") -> Alignment:
    """Find the time offset between an ECU log (``logger.LogData``, read from
    a file so ``ecu_log.path`` is set) and a capture."""
    notes = []
    poll_s = float(np.median(np.diff(ecu_log.t))) if len(ecu_log.t) > 2 else 0.1

    wall = None
    if ev.time_valid and ecu_log.path:
        origin = ecu_wall_origin(ecu_log.path)
        if origin is not None:
            wall = ev.unix_us_at_t0 / 1e6 - origin
    marks = _marker_offsets(ecu_log, ev)
    mark = float(np.median(marks)) - poll_s / 2 if marks else None

    if wall is not None:
        if mark is not None and abs(mark - wall) > max(0.25, 2 * poll_s):
            notes.append(f"markers disagree with the clock by {mark - wall:+.2f} s; "
                         "the laptop clock may have been changed during the drive")
        return Alignment(wall, "wall", 0.005, notes)
    if mark is not None:
        return Alignment(mark, "markers", poll_s / 2, notes)
    rpm = _rpm_offset(ecu_log, ev, rpm_channel)
    if rpm is not None:
        notes.append("aligned by matching RPM; set the clock or press a marker for a tighter fit")
        return Alignment(rpm - poll_s / 2, "rpm", poll_s, notes)
    notes.append("no clock, no shared markers and no usable RPM: the logs are not aligned")
    return Alignment(None, "none", None, notes)
