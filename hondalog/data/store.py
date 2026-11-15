"""Whole-file operations on native logs: save, cut out a window, repair, annotate."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .container import LogWriter
from .reader import Log, Stream, read_hlog


def write_log(log: Log, path: str | os.PathLike, meta: Mapping[str, Any] | None = None) -> Path:
    """Save any ``Log`` (imported, cut, re-decoded) as a native file."""
    path = Path(path)
    with LogWriter(path, {**log.meta, **dict(meta or {})}, background=False) as writer:
        for stream in log.streams.values():
            info = stream.info
            writer.add_stream(info.name, info.columns, source=info.source,
                              raw_len=info.raw_len, attrs=info.attrs)
            writer.append_array(info.name, stream.data)
        for event in log.events:
            writer.event(event.t, event.kind, event.text, event.data)
    return path


def merge_window(sources: Sequence[Log], t0: float, t1: float) -> Log:
    """Rows and events between t0 and t1 from one or more logs on the same
    timebase (the parts of a rotated recording), as one in-memory log."""
    streams: dict[str, Stream] = {}
    events = []
    meta: dict[str, Any] = {}
    for source in sources:
        part = source.window(t0, t1)
        meta.update(part.meta)
        events.extend(part.events)
        for name, stream in part.streams.items():
            old = streams.get(name)
            if old is None or old.info.dtype != stream.info.dtype:
                streams[name] = stream
            else:
                streams[name] = Stream(stream.info, data=np.concatenate([old.data, stream.data]))
    return Log(meta, streams.values(), events)


def extract_window(sources: Sequence[str | os.PathLike | Log], out_path: str | os.PathLike,
                   t0: float, t1: float, meta: Mapping[str, Any] | None = None) -> tuple[Path, int]:
    """Cut [t0, t1] out of one or more logs into a new file. Every stream is
    kept, so an ECU capture and the black box frames of the same seconds end
    up in one bundle. Returns (path, rows written)."""
    opened = [s if isinstance(s, Log) else read_hlog(s) for s in sources]
    try:
        cut = merge_window(opened, t0, t1)
    finally:
        for source, log in zip(sources, opened):
            if not isinstance(source, Log):
                log.close()
    return write_log(cut, out_path, meta), cut.rows


def repair(path: str | os.PathLike) -> dict[str, Any]:
    """Finish a log that was cut off: drop the torn tail, write the index.

    Safe to run on a good file (it only appends a fresh index).
    """
    before = read_hlog(path)
    info = {"was_indexed": before.indexed, "junk_bytes": before.junk_bytes, "rows": before.rows}
    before.close()
    LogWriter(path, append=True, background=False).close()
    return info


def annotate(path: str | os.PathLike, **meta: Any) -> None:
    """Add or change metadata (notes, tags, ...) on an existing log.

    Append-only like everything else: the new values go into a new chunk at
    the end and win over the old ones when the file is read.
    """
    with LogWriter(path, append=True, background=False) as writer:
        writer.set_meta(**meta)
