"""Exports to other tools' formats, and importers for the same.

    export_log(log, "run.mlg")              # by file extension
    export_log(log, "run.csv", channels=["rpm", "map"], filters=filter_set)

Formats: ``.mlg`` (MegaLogViewer binary, v2 by default), ``.msl``
(MegaLogViewer text), ``.csv``, ``.npz`` (NumPy), ``.db`` (the drive, or a
cut of it, copied into another hondalog database: all streams and raw bytes
kept; the file is made if it is not there).

    export_capture("hondalog.db#stall_20261116_071455", "stall.mlg")

Parquet is left out on purpose: python3-pyarrow is not packaged for Debian
trixie (only for forky and sid), and the app installs from apt.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..analysis.filters import FilterSet
from ..data.model import Column, Table
from ..data.db import DriveRef
from ..data.reader import Log, open_log
from ..data.store import merge_window, write_log
from . import mlg, textlog

FORMATS = {".mlg": "MegaLogViewer binary (MLG v2)", ".msl": "MegaLogViewer text",
           ".csv": "CSV", ".npz": "NumPy arrays", ".db": "hondalog database (SQLite)"}


def export_table(log: Log, channels: Sequence[str] | None = None, *, hz: float | None = None,
                 base: str | None = None, filters: FilterSet | None = None, t0: float | None = None,
                 t1: float | None = None) -> tuple[Table, list[tuple[float, str]]]:
    """The rows an export writes: channels on one grid, filtered, cut to [t0, t1].

    ``filters`` is an ``analysis.FilterSet``. Channels a filter needs are
    looked up even when they are not exported.
    """
    names = list(channels) if channels is not None else log.channels
    wanted = list(names)
    if filters is not None:
        wanted += [n for n in sorted(filters.channels()) if n not in wanted and n in log.channels]
    table = log.table(wanted, hz=hz, base=base)
    mask = np.ones(len(table), dtype=bool)
    if filters is not None:
        mask &= filters.mask(table)
    if t0 is not None:
        mask &= table.t >= t0
    if t1 is not None:
        mask &= table.t <= t1
    out = Table(table.t[mask], {n: table[n][mask] for n in names}, {n: table.units.get(n, "") for n in names})
    lo = -np.inf if t0 is None else t0
    hi = np.inf if t1 is None else t1
    return out, [(t, text) for t, text in log.markers if lo <= t <= hi]


def export_log(log: Log, path: str | os.PathLike, fmt: str | None = None, *,
               channels: Sequence[str] | None = None, hz: float | None = None,
               base: str | None = None, filters: FilterSet | None = None, t0: float | None = None,
               t1: float | None = None, mlg_version: int = 2, compact: bool = False) -> Path:
    """Write ``log`` in another format. The format comes from ``fmt`` or the extension."""
    path = Path(path)
    ext = "." + fmt.lstrip(".").lower() if fmt else path.suffix.lower()
    if ext not in FORMATS:
        raise ValueError(f"unknown export format {ext!r}; choose from {', '.join(FORMATS)}")
    if ext == ".db":
        lo = log.t0 if t0 is None else t0
        hi = log.t1 if t1 is None else t1
        write_log(merge_window([log], lo, hi), path, {"exported_from": str(log.path or "")})
        return path
    table, markers = export_table(log, channels, hz=hz, base=base, filters=filters, t0=t0, t1=t1)
    columns: dict[str, Column] = {n: c for n in table.names if (c := log.column(n)) is not None}
    small_meta = {k: v for k, v in log.meta.items() if k != "address_map_text"}
    if ext == ".mlg":
        info = "; ".join(f"{k}={small_meta[k]}" for k in ("ecu", "ecu_id", "address_map_version", "started")
                         if k in small_meta)
        return mlg.write_mlg(path, table, markers, version=mlg_version, columns=columns, info=info,
                             timestamp=small_meta.get("started_unix"), compact=compact)
    if ext == ".msl":
        return textlog.write_msl(path, table, markers, title=str(small_meta.get("ecu", "")),
                                 started=small_meta.get("started_unix"))
    if ext == ".csv":
        return textlog.write_csv(path, table, markers, small_meta, columns)
    arrays: dict[str, Any] = {f"ch_{n}": table[n] for n in table.names}
    np.savez_compressed(path, t=table.t, names=np.array(table.names),
                        units=np.array([table.units.get(n, "") for n in table.names]),
                        marker_t=np.array([m[0] for m in markers]),
                        marker_text=np.array([m[1] for m in markers]), **arrays)
    return path


def export_capture(capture: DriveRef | str | os.PathLike, path: str | os.PathLike, fmt: str | None = None,
                   **options: Any) -> Path:
    """Write one capture (a stall, a manual capture) to its own file. A
    capture is a time range of a drive; this is how it leaves the database:
    ``.mlg`` / ``.msl`` / ``.csv`` / ``.npz`` for other tools, ``.db`` for a
    small hondalog database that holds just that window (to send to someone).
    ``options`` are those of ``export_log``."""
    with open_log(capture) as log:
        return export_log(log, path, fmt, **options)


__all__ = ["FORMATS", "export_capture", "export_log", "export_table", "mlg", "textlog"]
