"""Open a black box capture for replay, together with the ECU log that was
running at the time.

``open_event`` returns a ``hondalog.logger.LogData``, the same object replay
gets from an ordinary log, so nothing downstream needs to know the
difference. ``logger.read_log`` hands ``.bbx`` files here.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np

from ..data.container import EXTENSION
from ..data.reader import open_log
from ..logger import LogData, log_dir, read_log
from . import format as F
from .align import Alignment, estimate
from .diagnose import diagnose
from .eventfile import BlackboxEvent, read_event


def _wall_span(path: Path):
    """(first, last) wall time of an ECU log (.hlog or 0.1.0 CSV) as unix
    seconds, or None."""
    if path.suffix.lower() == EXTENSION:
        try:
            with open_log(path) as log:
                epoch = log.meta.get("epoch_unix")
                if not isinstance(epoch, (int, float)) or log.rows == 0:
                    return None
                return (epoch + log.t0, epoch + log.t1)
        except (OSError, ValueError):
            return None
    first = last = None
    wi = None
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192).decode("utf-8", "replace").splitlines()
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in head:
        if line.startswith("#"):
            continue
        cells = line.split(",")
        if wi is None:
            if "wall_time" not in cells:
                return None
            wi = cells.index("wall_time")
            continue
        try:
            first = datetime.fromisoformat(cells[wi]).timestamp()
            break
        except (ValueError, IndexError):
            return None
    for line in reversed(tail):
        cells = line.split(",")
        try:
            last = datetime.fromisoformat(cells[wi]).timestamp()
            break
        except (ValueError, IndexError, TypeError):
            continue
    return (first, last) if first is not None and last is not None else None


def find_ecu_log(ev: BlackboxEvent, directory=None) -> Path | None:
    """The ECU log that was recording when the capture triggered. Needs the
    capture's clock to have been set. Prefers the full ``log_*`` recording
    over the ``stall_*`` capture cut from it, and a native log over a CSV."""
    wall = ev.trigger_wall
    if wall is None:
        return None
    at = wall.timestamp()
    best = None
    folder = Path(directory or log_dir())
    for path in sorted([*folder.glob("*" + EXTENSION), *folder.glob("*.csv")]):
        span = _wall_span(path)
        if span is None or not span[0] - 1.0 <= at <= span[1] + 15.0:
            continue
        rank = (0 if path.name.startswith("log_") else 1,
                0 if path.suffix.lower() == EXTENSION else 1, -(span[1] - span[0]))
        if best is None or rank < best[0]:
            best = (rank, path)
    return best[1] if best else None


def merge(ev: BlackboxEvent, ecu_log: LogData | None = None,
          alignment: Alignment | None = None) -> LogData:
    """Build the LogData for replay.

    The time axis is the capture's 1 kHz grid. With an aligned ECU log the
    times are ECU-log seconds and the ECU channels are added, each holding
    its last value between polls; without one, times are seconds relative to
    the trigger (negative = before).
    """
    if not len(ev.t_us):
        raise ValueError("the capture has no samples")
    aligned = ecu_log is not None and alignment is not None and alignment.ok
    if aligned:
        t = alignment.to_ecu_s(ev.t_us)
        shift = lambda us: us / 1e6 + alignment.offset_s          # noqa: E731
    else:
        t = ev.seconds()
        shift = lambda us: (us - ev.trigger_t_us) / 1e6           # noqa: E731

    log = LogData(ev.path or Path("blackbox"))
    log.t = t
    if aligned:
        log.meta.update(ecu_log.meta)
        idx = np.searchsorted(ecu_log.t, t, side="right") - 1
        inside = (idx >= 0) & (t <= ecu_log.t[-1] + 1.0)
        for name in ecu_log.names:
            col = ecu_log.data[name][np.clip(idx, 0, len(ecu_log.t) - 1)].astype(float)
            col[~inside] = np.nan
            log.data[name] = col
            log.names.append(name)
        log.markers += [(mt, text) for mt, text in ecu_log.markers if t[0] <= mt <= t[-1]]
    for name, col in ev.channels().items():
        log.data[name] = col
        log.names.append(name)
    for r in ev.events:
        when = float(shift(r.t_us))
        if t[0] <= when <= t[-1]:
            tag = "BB TRIGGER" if r.is_trigger and r.t_us == ev.trigger_t_us and r.code == ev.reason else "BB"
            log.markers.append((when, f"{tag}: {r.text}"))
    log.markers.sort(key=lambda m: m[0])

    verdict = diagnose(ev)
    log.meta.update({
        "stall_trigger": f"black box: {ev.reason_text}",
        "stall_time_s": f"{float(shift(ev.trigger_t_us)):.3f}",
        "bb_event": str(ev.event_no),
        "bb_device": f"{ev.device} fw {ev.fw}",
        "bb_verdict": verdict.title,
        "bb_alignment": (f"{alignment.method}, about ±{alignment.error_s * 1000:.0f} ms"
                         if aligned else "no ECU log aligned"),
    })
    if ev.trigger_wall:
        log.meta["stall_wall_time"] = ev.trigger_wall.isoformat(timespec="milliseconds")
    if aligned:
        log.meta["bb_ecu_log"] = str(ecu_log.path)
    notes = list(ev.warnings) + (alignment.notes if alignment else [])
    if notes:
        log.meta["bb_notes"] = "; ".join(notes)
    log.blackbox = ev               # extras for a UI that wants them
    log.alignment = alignment
    log.verdict = verdict
    return log


def open_event(path, ecu_log="auto", directory=None) -> LogData:
    """Read a ``.bbx`` capture and merge the matching ECU log into it.

    ``ecu_log``: ``"auto"`` looks in ``directory`` (default: the log folder)
    for the log that was recording at the time; a path uses that file;
    ``None`` opens the capture alone.

    The result also has ``.blackbox`` (the ``BlackboxEvent``), ``.alignment``
    and ``.verdict`` (``diagnose.Verdict``).
    """
    ev = read_event(path)
    ecu_path = find_ecu_log(ev, directory) if ecu_log == "auto" else ecu_log
    ecu = alignment = None
    if ecu_path:
        try:
            ecu = read_log(ecu_path)
            alignment = estimate(ecu, ev)
        except (OSError, ValueError):
            ecu = alignment = None
    return merge(ev, ecu, alignment)


def bundle(event_path, ecu_log_path=None, out_dir=None) -> Path:
    """Copy a capture and its ECU log into one folder with a ``bundle.json``
    that records how they line up. Returns the folder."""
    ev = read_event(event_path)
    ecu_path = Path(ecu_log_path) if ecu_log_path else find_ecu_log(ev)
    stamp = ev.trigger_wall.strftime("%Y%m%d_%H%M%S") if ev.trigger_wall else f"ev{ev.event_no:06d}"
    folder = Path(out_dir or log_dir()) / f"event_{stamp}_{ev.reason_name}"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(event_path, folder / Path(event_path).name)
    alignment = None
    if ecu_path:
        shutil.copy2(ecu_path, folder / ecu_path.name)
        alignment = estimate(read_log(ecu_path), ev)
    verdict = diagnose(ev)
    (folder / "bundle.json").write_text(json.dumps({
        "blackbox": Path(event_path).name,
        "ecu_log": ecu_path.name if ecu_path else None,
        "reason": ev.reason_name,
        "trigger_wall_time": ev.trigger_wall.isoformat(timespec="milliseconds") if ev.trigger_wall else None,
        "offset_s": alignment.offset_s if alignment else None,
        "alignment": alignment.method if alignment else "none",
        "verdict": verdict.cause,
        "verdict_text": verdict.text(),
    }, indent=2))
    return folder
