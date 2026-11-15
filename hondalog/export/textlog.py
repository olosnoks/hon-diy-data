"""Text logs: CSV and MegaLogViewer ``.msl``, writers and one tolerant reader.

The reader takes hondalog 0.1.0 CSV logs (``#`` header lines, ``time_s``,
``wall_time``, ``link``, channels, ``marker``), MSL files (optional quoted
title lines, a tab-separated name row, a units row, ``MARK`` lines) and
plain CSV from other tools.

The MSL layout is the one TunerStudio writes as far as public examples show.
It has not been opened in MegaLogViewer from here.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..data.model import Column, Event, StreamInfo, Table
from ..data.reader import Log, Stream

TIME_NAMES = ("time_s", "time", "t", "timestamp", "time (s)", "time(s)", "seconds")
SKIP_COLUMNS = ("wall_time",)
MARKER_COLUMN = "marker"


def _number(value: float, decimals: int | None = None) -> str:
    if value != value:
        return ""
    if decimals is not None:
        return f"{value:.{decimals}f}"
    return f"{value:.6g}" if abs(value) < 1e6 else f"{value:.0f}"


def _marker_rows(t: np.ndarray, markers: Sequence[tuple[float, str]]) -> dict[int, str]:
    """Row index -> marker text: each marker lands on the first row at or after it."""
    out: dict[int, str] = {}
    if len(t) == 0:
        return out
    for when, text in markers:
        row = min(int(np.searchsorted(t, when, side="left")), len(t) - 1)
        out[row] = f"{out[row]} | {text}" if row in out else text
    return out


def write_csv(path: str | os.PathLike, table: Table, markers: Sequence[tuple[float, str]] = (),
              meta: Mapping[str, Any] | None = None, columns: Mapping[str, Column] | None = None) -> Path:
    """Write a table as CSV in the hondalog layout: ``#`` header lines, then
    ``time_s``, the channels and a ``marker`` column. Missing values are empty."""
    from .. import __version__
    path = Path(path)
    columns = columns or {}
    marks = _marker_rows(table.t, markers)
    names = table.names
    decimals = [None if n not in columns else 0 if columns[n].kind == "flag"
                else max(columns[n].decimals, 0) + 2 for n in names]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# hondalog {__version__}\n")
        for key, value in (meta or {}).items():
            text = str(value).replace("\n", " ")
            if len(text) <= 400:                # the map text and the like stay in the .hlog
                fh.write(f"# {key}: {text}\n")
        units = " ".join(f"{n}={table.units.get(n) or '-'}" for n in names)
        if "units" not in (meta or {}):
            fh.write(f"# units: {units}\n")
        writer = csv.writer(fh)
        writer.writerow(["time_s", *names, MARKER_COLUMN])
        cols = [table[n] for n in names]
        for i in range(len(table)):
            writer.writerow([f"{table.t[i]:.4f}",
                             *[_number(float(c[i]), d) for c, d in zip(cols, decimals)],
                             marks.get(i, "")])
    return path


def write_msl(path: str | os.PathLike, table: Table, markers: Sequence[tuple[float, str]] = (),
              title: str = "", fill: str = "hold", started: float | None = None) -> Path:
    """Write a MegaLogViewer text log: title lines, names, units, tab-separated rows.

    MegaLogViewer wants a number in every cell, so gaps are filled: "hold"
    repeats the last good value, "zero" writes 0. The ``link`` channel still
    shows where the ECU was silent.
    """
    from .. import __version__
    from .mlg import _fill
    path = Path(path)
    names = table.names
    marks = _marker_rows(table.t, markers)
    t = table.t - (table.t[0] if len(table) else 0.0)
    cols = [_fill(table[n], "zero" if fill == "keep" else fill) for n in names]
    when = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(started if started else time.time()))
    with open(path, "w", encoding="ascii", errors="replace", newline="\n") as fh:
        fh.write(f'"hondalog {__version__}{": " + title if title else ""}"\n')
        fh.write(f'"Capture Date: {when}"\n')
        fh.write("\t".join(["Time", *names]) + "\n")
        fh.write("\t".join(["s", *[(table.units.get(n) or "").replace("°", "deg") for n in names]]) + "\n")
        count = 0
        for i in range(len(table)):
            if i in marks:
                count += 1
                fh.write(f"MARK {count:03d} - {marks[i]}\n")
            fh.write("\t".join([f"{t[i]:.3f}", *[_number(float(c[i])) for c in cols]]) + "\n")
    return path


# ---- reading ----------------------------------------------------------------

def _to_float(cells: list[str]) -> np.ndarray:
    try:
        return np.array([c or "nan" for c in cells], dtype=np.float64)
    except ValueError:
        out = np.full(len(cells), np.nan)
        for i, cell in enumerate(cells):
            try:
                out[i] = float(cell)
            except ValueError:
                pass
        return out


def _is_number(cell: str) -> bool:
    try:
        float(cell)
        return True
    except ValueError:
        return False


def _safe_name(name: str, taken: set[str]) -> str:
    name = name.strip() or "column"
    if name in ("t", "raw"):
        name += "_"
    base, n = name, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def read_text_log(path: str | os.PathLike) -> Log:
    """Import a CSV or MSL file as a ``Log`` with one stream."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    return parse_text_log(text, path)


def parse_text_log(text: str, path: str | os.PathLike | None = None) -> Log:
    meta: dict[str, Any] = {}
    body: list[str] = []
    marks: list[tuple[int, str]] = []           # (data rows seen so far, text)
    header: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            key, sep, value = stripped[1:].partition(":")
            if sep:
                meta[key.strip()] = value.strip()
            else:
                meta.setdefault("format", stripped[1:].strip())
        elif header is None and stripped.startswith('"') and "\t" not in stripped and "," not in stripped.strip('"'):
            meta.setdefault("title", stripped.strip('"'))
        elif header is None and stripped.startswith('"') and stripped.endswith('"') and stripped.count('"') == 2:
            meta.setdefault("title", stripped.strip('"'))       # MSL title lines may hold commas
        elif header is None:
            header = line
        elif stripped.upper().startswith("MARK"):
            marks.append((len(body), re.sub(r"^MARK\s*\d*\s*-?\s*", "", stripped, flags=re.I) or stripped))
        else:
            body.append(line)
    if header is None:
        raise ValueError(f"{path or 'text log'}: no header row")
    delimiter = "\t" if "\t" in header else ";" if header.count(";") > header.count(",") else ","
    names = next(csv.reader([header], delimiter=delimiter))
    rows = list(csv.reader(io.StringIO("\n".join(body)), delimiter=delimiter))
    rows = [r for r in rows if r]
    units_row: list[str] | None = None
    if rows and not any(_is_number(c) for c in rows[0] if c.strip()):
        units_row, rows = rows[0], rows[1:]
        marks = [(max(0, n - 1), text) for n, text in marks]
    width = len(names)
    rows = [r[:width] + [""] * (width - len(r)) for r in rows]

    lower = [n.strip().lower() for n in names]
    time_i = next((lower.index(n) for n in TIME_NAMES if n in lower), None)
    marker_i = lower.index(MARKER_COLUMN) if MARKER_COLUMN in lower else None
    cells = list(zip(*rows)) if rows else [()] * width
    if time_i is not None:
        t = _to_float([c.strip() for c in cells[time_i]])
    else:
        t = np.arange(len(rows), dtype=np.float64)
        meta["time_axis"] = "row number (the file has no time column)"
    keep = ~np.isnan(t)

    units: dict[str, str] = {}
    for pair in str(meta.get("units", "")).split():
        name, sep, unit = pair.partition("=")
        if sep and unit != "-":
            units[name] = unit
    taken: set[str] = set()
    columns: list[Column] = []
    data: list[np.ndarray] = []
    for i, name in enumerate(names):
        if i in (time_i, marker_i) or lower[i] in SKIP_COLUMNS:
            continue
        values = _to_float([c.strip() for c in cells[i]])[keep] if rows else np.zeros(0)
        if rows and np.isnan(values).all() and any(c.strip() for c in cells[i]):
            continue                            # a text column: nothing to plot
        clean = _safe_name(name, taken)
        unit = units.get(name.strip(), units_row[i].strip() if units_row and i < len(units_row) else "")
        flag = lower[i] == "link"
        columns.append(Column(clean, "f8", unit, kind="flag" if flag else "value",
                              decimals=0 if flag else 2))
        data.append(values)

    info = StreamInfo("csv" if delimiter != "\t" else "msl", tuple(columns), source="import")
    out = np.zeros(int(keep.sum()), dtype=info.dtype)
    out["t"] = t[keep]
    for col, values in zip(columns, data):
        out[col.name] = values
    if len(out) > 1 and np.any(np.diff(out["t"]) < 0):
        out = out[np.argsort(out["t"], kind="stable")]

    events: list[Event] = []
    if marker_i is not None and rows:
        for when, cell in zip(t, cells[marker_i]):
            if cell and when == when:
                for part in cell.split(" | "):
                    events.append(Event(float(when), "trigger" if part.startswith("STALL") else "marker", part))
    kept_rows = np.flatnonzero(keep)
    for seen, text_ in marks:
        if len(kept_rows):
            events.append(Event(float(t[kept_rows[min(seen, len(kept_rows) - 1)]]), "marker", text_))
    if path is not None:
        meta["imported_from"] = str(path)
    return Log(meta, [Stream(info, data=out)], events, path=path)
