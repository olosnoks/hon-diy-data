"""Plain data types shared by the log reader, the analysis code and the exporters.

Nothing in here does I/O. A log is a set of *streams*. A stream is a table
with its own time column: one K-line read block, the black box's rail frames,
the link statistics. Streams run at very different rates and share one
timebase: seconds since the session started (monotonic clock).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

#: numpy type string -> struct code. Everything in a file is little-endian.
DTYPES: dict[str, str] = {"f4": "f", "f8": "d", "u1": "B", "i1": "b", "u2": "H",
                          "i2": "h", "u4": "I", "i4": "i", "u8": "Q", "i8": "q"}

#: event kinds that the replay view shows as a marker line
MARKER_KINDS = ("marker", "trigger", "alarm")


@dataclass(frozen=True)
class Column:
    """One decoded channel in a stream."""

    name: str
    dtype: str = "f4"
    units: str = ""
    label: str = ""
    kind: str = "value"             # "value" or "flag"
    decimals: int = 2
    lo: float | None = None         # display range, if the source knows it
    hi: float | None = None

    def __post_init__(self) -> None:
        if self.dtype not in DTYPES:
            raise ValueError(f"column {self.name!r}: unsupported dtype {self.dtype!r}")
        if not self.name or self.name in ("t", "raw"):
            raise ValueError(f"bad column name {self.name!r}")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "dtype": self.dtype}
        for key in ("units", "label"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.kind != "value":
            out["kind"] = self.kind
        if self.decimals != 2:
            out["decimals"] = self.decimals
        if self.lo is not None:
            out["lo"] = self.lo
        if self.hi is not None:
            out["hi"] = self.hi
        return out

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Column":
        known = {k: doc[k] for k in ("name", "dtype", "units", "label", "kind",
                                     "decimals", "lo", "hi") if k in doc}
        return cls(**known)


@dataclass(frozen=True)
class StreamInfo:
    """Definition of a stream: its columns and where the data came from."""

    name: str
    columns: tuple[Column, ...]
    source: str = ""                # "ecu", "blackbox", "math", "import", ...
    raw_len: int = 0                # raw bytes kept per row (0 = none)
    attrs: Mapping[str, Any] = field(default_factory=dict)   # e.g. addr and length

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"stream {self.name!r}: duplicate column names")
        if not 0 <= self.raw_len <= 65535:
            raise ValueError(f"stream {self.name!r}: raw_len out of range")

    @property
    def dtype(self) -> np.dtype:
        """Packed little-endian row layout: t, the columns, then the raw bytes."""
        fields: list[tuple] = [("t", "<f8")]
        fields += [(c.name, "<" + c.dtype if c.dtype[1] != "1" else c.dtype) for c in self.columns]
        if self.raw_len:
            fields.append(("raw", "u1", (self.raw_len,)))
        return np.dtype(fields)

    @property
    def struct_format(self) -> str:
        return "<d" + "".join(DTYPES[c.dtype] for c in self.columns)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source, "raw_len": self.raw_len,
                "attrs": dict(self.attrs), "columns": [c.to_json() for c in self.columns]}

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "StreamInfo":
        return cls(name=str(doc["name"]),
                   columns=tuple(Column.from_json(c) for c in doc.get("columns", [])),
                   source=str(doc.get("source", "")), raw_len=int(doc.get("raw_len", 0)),
                   attrs=dict(doc.get("attrs", {})))


@dataclass(frozen=True)
class Event:
    """Something that happened at one moment: a marker, a trigger, an alarm, a note."""

    t: float
    kind: str
    text: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": self.t, "kind": self.kind}
        if self.text:
            out["text"] = self.text
        if self.data:
            out["data"] = dict(self.data)
        return out

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Event":
        return cls(float(doc["t"]), str(doc.get("kind", "marker")), str(doc.get("text", "")),
                   dict(doc.get("data", {})))


@dataclass
class Series:
    """One channel over time, at the rate it was actually read."""

    name: str
    t: FloatArray
    v: FloatArray
    units: str = ""

    def __len__(self) -> int:
        return len(self.t)

    @property
    def rate_hz(self) -> float:
        """Median sample rate; 0 for fewer than two samples."""
        if len(self.t) < 2:
            return 0.0
        dt = float(np.median(np.diff(self.t)))
        return 1.0 / dt if dt > 0 else 0.0


@dataclass
class Table:
    """Several channels on one shared time grid. NaN marks "no value here".

    This is what histograms, filters, scatter plots and the row-based
    exporters work on.
    """

    t: FloatArray
    columns: dict[str, FloatArray]
    units: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.t)

    def __getitem__(self, name: str) -> FloatArray:
        return self.t if name == "t" else self.columns[name]

    def __contains__(self, name: object) -> bool:
        return name == "t" or name in self.columns

    def __iter__(self) -> Iterator[str]:
        return iter(self.columns)

    @property
    def names(self) -> list[str]:
        return list(self.columns)

    def env(self) -> dict[str, FloatArray]:
        """Names for the expression evaluator: every column plus ``t``."""
        return {**self.columns, "t": self.t}

    def select(self, mask: BoolArray) -> "Table":
        """Rows where ``mask`` is true."""
        return Table(self.t[mask], {k: v[mask] for k, v in self.columns.items()}, dict(self.units))
