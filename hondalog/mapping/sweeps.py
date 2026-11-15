"""Record memory-scanner sweeps without any UI.

A ``SweepRecording`` is the raw material for the correlation finder and for
verification evidence: every sweep's bytes with the time each chunk was read.
``SweepRecorder`` fills one through the poller's ``submit`` (so it shares the
line with normal polling); ``record_link`` does the same on a bare ``Link``.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from ..addressmap import AddressMap
from ..protocol import ProtocolError

UNREAD = -1


@dataclass
class Mark:
    """A labelled stretch of time: a session phase, or "A/C on"."""
    label: str
    t0: float
    t1: float | None = None

    def contains(self, t: Any) -> Any:
        end = np.inf if self.t1 is None else self.t1
        return (t >= self.t0) & (t <= end)


@dataclass
class SweepRecording:
    """N sweeps of the 256-byte address space.

    mem    (N, 256) int16, -1 where a byte was not read
    times  (N, 256) float seconds: when the chunk holding each byte arrived
    """
    mem: np.ndarray = field(default_factory=lambda: np.empty((0, 256), dtype=np.int16))
    times: np.ndarray = field(default_factory=lambda: np.empty((0, 256), dtype=float))
    marks: list[Mark] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    _rows: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list, repr=False)

    def __len__(self) -> int:
        return len(self.mem) + len(self._rows)

    def append(self, mem: np.ndarray, times: np.ndarray) -> None:
        self._rows.append((np.asarray(mem, dtype=np.int16).copy(),
                           np.asarray(times, dtype=float).copy()))

    def _flush(self) -> None:
        if self._rows:
            self.mem = np.vstack([self.mem, *[m[None, :] for m, _ in self._rows]])
            self.times = np.vstack([self.times, *[t[None, :] for _, t in self._rows]])
            self._rows.clear()

    @property
    def data(self) -> tuple[np.ndarray, np.ndarray]:
        """(mem, times) with everything appended so far."""
        self._flush()
        return self.mem, self.times

    @property
    def t(self) -> np.ndarray:
        """One time per sweep: when its last chunk arrived."""
        _, times = self.data
        return times.max(axis=1) if len(times) else np.empty(0)

    def read_addresses(self) -> np.ndarray:
        """Addresses that were read at least once."""
        mem, _ = self.data
        return np.flatnonzero((mem >= 0).any(axis=0))

    def mem_at(self, i: int) -> dict[int, int]:
        """Sweep ``i`` as {address: byte}, the form AddressMap.decode takes."""
        mem, _ = self.data
        row = mem[i]
        return {int(a): int(row[a]) for a in np.flatnonzero(row >= 0)}

    def decode(self, amap: AddressMap) -> dict[str, np.ndarray]:
        """Decode every sweep with a map: {"t": ..., channel: ...}, NaN = no value.
        Each channel also gets "<name>:t", the time its own bytes were read."""
        mem, times = self.data
        out: dict[str, np.ndarray] = {"t": self.t}
        rows = [amap.decode(self.mem_at(i)) for i in range(len(mem))]
        for ch in amap.channels:
            out[ch.name] = np.array([np.nan if r[ch.name] is None else r[ch.name] for r in rows],
                                    dtype=float)
            out[ch.name + ":t"] = times[:, ch.addr] if len(times) else np.empty(0)
        return out

    def window(self, t0: float, t1: float) -> "SweepRecording":
        """The sweeps that finished between t0 and t1."""
        mem, times = self.data
        keep = (self.t >= t0) & (self.t <= t1)
        return SweepRecording(mem[keep], times[keep], list(self.marks), dict(self.meta))

    def marked(self, label: str) -> "SweepRecording":
        """The sweeps inside every mark with this label."""
        mem, times = self.data
        keep = np.zeros(len(mem), dtype=bool)
        for mark in self.marks:
            if mark.label == label:
                keep |= mark.contains(self.t)
        return SweepRecording(mem[keep], times[keep], list(self.marks), dict(self.meta))

    def mark_mask(self, label: str) -> np.ndarray:
        """(N, 256) bool: which readings were taken inside marks with this label."""
        _, times = self.data
        mask = np.zeros(times.shape, dtype=bool)
        for mark in self.marks:
            if mark.label == label:
                mask |= mark.contains(times)
        return mask

    # -- files --

    def save(self, path: str | Path) -> Path:
        """Write a compressed .npz (bytes, times, marks and meta in one file)."""
        path = Path(path).with_suffix(".npz")
        mem, times = self.data
        info = {"marks": [asdict(m) for m in self.marks], "meta": self.meta}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            np.savez_compressed(fh, mem=mem, times=times, info=np.array(json.dumps(info)))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "SweepRecording":
        with np.load(Path(path), allow_pickle=False) as doc:
            info = json.loads(str(doc["info"]))
            return cls(doc["mem"].astype(np.int16), doc["times"].astype(float),
                       [Mark(**m) for m in info.get("marks", [])], info.get("meta", {}))

    @classmethod
    def from_scan_csv(cls, path: str | Path) -> "SweepRecording":
        """Read the .csv the Raw tools memory scanner exports (one row per sweep)."""
        rec = cls(meta={"source": str(path)})
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header or header[0] != "wall_time" or len(header) != 257:
                raise ValueError(f"{path}: not a hondalog memory scan export")
            t0: datetime | None = None
            for row in reader:
                if len(row) != 257:
                    continue
                wall = datetime.fromisoformat(row[0])
                t0 = t0 or wall
                mem = np.array([UNREAD if c == "" else int(c) for c in row[1:]], dtype=np.int16)
                rec.append(mem, np.full(256, (wall - t0).total_seconds()))
        return rec


def chunks(lo: int = 0x00, hi: int = 0xFF, size: int = 16) -> list[tuple[int, int]]:
    """(address, length) reads covering lo..hi inclusive."""
    lo, hi = sorted((lo & 0xFF, hi & 0xFF))
    size = max(1, size)
    return [(a, min(size, hi + 1 - a)) for a in range(lo, hi + 1, size)]


class SweepRecorder:
    """Sweeps a range of addresses over and over through ``poller.submit``.

        rec = SweepRecorder(poller.submit, lo=0x00, hi=0x3F)
        rec.start()
        ...  call rec.pump() from a timer or a loop ...
        rec.stop()
        recording = rec.recording

    Each chunk is stamped on the poller thread as its reply arrives, so the
    times do not depend on how often ``pump`` runs.
    """

    def __init__(self, submit: Callable[[Callable[[Any], Any]], Any], lo: int = 0x00,
                 hi: int = 0xFF, chunk: int = 16,
                 clock: Callable[[], float] = time.monotonic, t0: float | None = None):
        self.submit = submit
        self.plan = chunks(lo, hi, chunk)
        self.clock = clock
        self.t0 = clock() if t0 is None else t0
        self.recording = SweepRecording(meta={"lo": lo, "hi": hi, "chunk": chunk})
        self.active = False
        self.error = ""
        self.failed_reads = 0
        self._pending: list[tuple[Any, int, int]] = []
        self._mem = np.full(256, UNREAD, dtype=np.int16)
        self._times = np.zeros(256)
        self._open: Mark | None = None

    def now(self) -> float:
        return self.clock() - self.t0

    def start(self) -> None:
        if not self.active:
            self.active = True
            self.error = ""
            self._queue()

    def stop(self) -> None:
        self.active = False
        self._pending.clear()
        self.end_mark()

    def _queue(self) -> None:
        self._mem[:] = UNREAD
        for addr, length in self.plan:
            self._pending.append((self.submit(self._job(addr, length)), addr, length))

    def _job(self, addr: int, length: int) -> Callable[[Any], tuple[bytes, float]]:
        clock, t0 = self.clock, self.t0

        def read(link: Any) -> tuple[bytes, float]:
            data = link.read_mem(addr, length)
            return data, clock() - t0           # stamped as the reply arrives
        return read

    def pump(self) -> int:
        """Collect finished reads. Returns how many sweeps were completed."""
        done = 0
        while self._pending and self._pending[0][0].done():
            fut, addr, length = self._pending.pop(0)
            try:
                data, stamp = fut.result()
            except ProtocolError:
                self.failed_reads += 1
                self._times[addr:addr + length] = self.now()
            except Exception as exc:                    # port gone, poller stopped
                self.error = str(exc) or type(exc).__name__
                self.stop()
                return done
            else:
                self._mem[addr:addr + length] = np.frombuffer(data, dtype=np.uint8)
                self._times[addr:addr + length] = stamp
            if not self._pending:
                if (self._mem >= 0).any():
                    self.recording.append(self._mem, self._times)
                    done += 1
                if self.active:
                    self._queue()
        return done

    def run(self, sweeps: int | None = None, seconds: float | None = None,
            poll_s: float = 0.005) -> SweepRecording:
        """Blocking helper for scripts and tests: record until a count or a time."""
        if sweeps is None and seconds is None:
            raise ValueError("give sweeps or seconds")
        self.start()
        deadline = None if seconds is None else self.clock() + seconds
        got = 0
        while self.active:
            got += self.pump()
            if (sweeps is not None and got >= sweeps) or \
                    (deadline is not None and self.clock() >= deadline):
                break
            time.sleep(poll_s)
        self.stop()
        return self.recording

    # -- marks --

    def begin_mark(self, label: str) -> Mark:
        """Start a labelled stretch (ends the previous one)."""
        self.end_mark()
        self._open = Mark(label, self.now())
        self.recording.marks.append(self._open)
        return self._open

    def end_mark(self) -> None:
        if self._open is not None:
            self._open.t1 = self.now()
            self._open = None


def record_link(link: Any, sweeps: int, lo: int = 0x00, hi: int = 0xFF, chunk: int = 16,
                clock: Callable[[], float] = time.monotonic,
                between: Callable[[int], None] | None = None) -> SweepRecording:
    """Record sweeps straight from a ``transport.Link`` (no poller thread).
    ``between(i)`` runs after sweep i, e.g. to move a simulator along."""
    rec = SweepRecording(meta={"lo": lo, "hi": hi, "chunk": chunk})
    t0 = clock()
    plan = chunks(lo, hi, chunk)
    for i in range(sweeps):
        mem = np.full(256, UNREAD, dtype=np.int16)
        times = np.zeros(256)
        for addr, length in plan:
            try:
                data = link.read_mem(addr, length)
            except ProtocolError:
                times[addr:addr + length] = clock() - t0
                continue
            mem[addr:addr + length] = np.frombuffer(data, dtype=np.uint8)
            times[addr:addr + length] = clock() - t0
        rec.append(mem, times)
        if between is not None:
            between(i)
    return rec


def from_rows(rows: Sequence[Mapping[int, int] | Sequence[int]], t: Sequence[float]) -> SweepRecording:
    """Build a recording from plain data: one {addr: byte} or 256-list per sweep."""
    rec = SweepRecording()
    for row, ti in zip(rows, t):
        mem = np.full(256, UNREAD, dtype=np.int16)
        if isinstance(row, Mapping):
            for addr, value in row.items():
                mem[addr] = value
        else:
            mem[:len(row)] = row
        rec.append(mem, np.full(256, float(ti)))
    return rec
