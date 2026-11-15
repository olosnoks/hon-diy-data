"""Cross-check Honda RAM channels against standard OBD2 PIDs (ISO 9141-2).

The two protocols cannot share the K-line, so this works on snapshots: read
the PIDs, switch to the Honda protocol, read the same channels with the engine
in the same steady state, and add the pair. Do it at two or more different
operating points (cold and warm, idle and 3000 rpm) and a scaled channel gets
a slope and offset suggestion by least squares.

No I/O in here. The ISO link belongs to the protocol layer; this module only
knows which PID matches which channel and how a PID's data bytes decode
(SAE J1979 mode 01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import numpy as np

AGREE, SCALED, NO_MATCH, NO_DATA = "agree", "scaled", "no match", "no data"


@dataclass(frozen=True)
class PidSpec:
    channel: str
    pid: int
    name: str
    nbytes: int
    decode: Callable[[Sequence[int]], float]
    units: str
    tol: float                 # absolute tolerance for "agree"
    rel_tol: float = 0.0       # or this share of the PID value, whichever is larger
    note: str = ""


PIDS: tuple[PidSpec, ...] = (
    PidSpec("rpm", 0x0C, "Engine RPM", 2, lambda d: (d[0] * 256 + d[1]) / 4.0, "rpm", 50, 0.03),
    PidSpec("ect", 0x05, "Coolant temperature", 1, lambda d: d[0] - 40.0, "°C", 3),
    PidSpec("iat", 0x0F, "Intake air temperature", 1, lambda d: d[0] - 40.0, "°C", 3),
    PidSpec("map", 0x0B, "Manifold absolute pressure", 1, lambda d: float(d[0]), "kPa", 3),
    PidSpec("tps", 0x11, "Throttle position", 1, lambda d: d[0] * 100.0 / 255.0, "%", 3,
            note="the PID is absolute position (about 10% with the pedal released); "
                 "a relative channel shows as scaled"),
    PidSpec("vss", 0x0D, "Vehicle speed", 1, lambda d: float(d[0]), "km/h", 3),
    PidSpec("stft", 0x06, "Short term fuel trim", 1, lambda d: (d[0] - 128) * 100.0 / 128.0,
            "%", 3),
    PidSpec("ltft", 0x07, "Long term fuel trim", 1, lambda d: (d[0] - 128) * 100.0 / 128.0,
            "%", 3),
    PidSpec("o2", 0x14, "O2 sensor 1 voltage", 2, lambda d: d[0] / 200.0, "V", 0.15,
            note="switches several times a second in closed loop: compare averages"),
    PidSpec("ign", 0x0E, "Timing advance", 1, lambda d: d[0] / 2.0 - 64.0, "° BTDC", 2),
)
BY_CHANNEL = {p.channel: p for p in PIDS}
BY_PID = {p.pid: p for p in PIDS}


def decode_pid(pid: int, data: Sequence[int]) -> float:
    """Mode 01 data bytes (after the PID byte) -> engineering value."""
    spec = BY_PID.get(pid)
    if spec is None:
        raise KeyError(f"PID 0x{pid:02X} has no matching channel")
    if len(data) < spec.nbytes:
        raise ValueError(f"PID 0x{pid:02X} needs {spec.nbytes} data bytes, got {len(data)}")
    return spec.decode(data)


def pid_values(raw: Mapping[int, Sequence[int]]) -> dict[str, float]:
    """{pid: data bytes} from an ISO session -> {channel name: value}. PIDs
    without a matching channel, or with too few bytes, are left out."""
    out: dict[str, float] = {}
    for pid, data in raw.items():
        spec = BY_PID.get(pid)
        if spec is not None and len(data) >= spec.nbytes:
            out[spec.channel] = spec.decode(data)
    return out


@dataclass
class ChannelCheck:
    channel: str
    verdict: str                   # agree | scaled | no match | no data
    n: int = 0
    max_error: float = float("nan")     # worst |honda - pid| over the snapshots
    slope: float = float("nan")         # pid ~= slope * honda + offset
    offset: float = float("nan")
    rmse: float = float("nan")
    formula: str = ""              # suggested replacement formula (for "scaled")
    detail: str = ""
    pairs: list[tuple[float, float]] = field(default_factory=list)      # (honda, pid)

    def __str__(self) -> str:
        text = f"{self.channel:8} {self.verdict:9} {self.detail}"
        return text + (f"\n{'':19}try formula: {self.formula}" if self.formula else "")


class CrossCheck:
    """Collects (Honda, PID) snapshot pairs and reports per channel.

        cc = CrossCheck({"rpm": "x / 4", ...})        # current formulas, for suggestions
        cc.add_snapshot(honda_values, pid_values, "warm idle")
        cc.add_snapshot(honda_values_2, pid_values_2, "3000 rpm")
        for line in cc.report(): print(line)
    """

    def __init__(self, formulas: Mapping[str, str] | None = None):
        self.formulas = dict(formulas or {})
        self.snapshots: list[tuple[str, dict[str, float], dict[str, float]]] = []

    def add_snapshot(self, honda: Mapping[str, float | None], pids: Mapping[str, float],
                     label: str = "") -> None:
        """One operating point. ``honda`` holds decoded channel values (use a
        mean over a few seconds), ``pids`` holds values by channel name
        (see ``pid_values``)."""
        h = {k: float(v) for k, v in honda.items() if v is not None and v == v}
        p = {k: float(v) for k, v in pids.items() if k in BY_CHANNEL and v == v}
        self.snapshots.append((label or f"snapshot {len(self.snapshots) + 1}", h, p))

    def report(self, channels: Sequence[str] | None = None) -> list[ChannelCheck]:
        names = list(channels) if channels is not None else [p.channel for p in PIDS]
        return [self._check(name) for name in names]

    def _check(self, name: str) -> ChannelCheck:
        spec = BY_CHANNEL.get(name)
        if spec is None:
            return ChannelCheck(name, NO_DATA, detail="no standard PID carries this")
        pairs = [(h[name], p[name]) for _, h, p in self.snapshots if name in h and name in p]
        if not pairs:
            return ChannelCheck(name, NO_DATA, detail="no snapshot has both values")
        hv = np.array([a for a, _ in pairs])
        pv = np.array([b for _, b in pairs])
        tol = np.maximum(spec.tol, spec.rel_tol * np.abs(pv))
        err = np.abs(hv - pv)
        out = ChannelCheck(name, NO_MATCH, len(pairs), float(err.max()), pairs=pairs)
        shown = ", ".join(f"{a:.4g} vs PID {b:.4g}" for a, b in pairs[:4])
        if bool((err <= tol).all()):
            out.verdict = AGREE
            out.detail = f"{shown} {spec.units} (within {spec.tol:g})"
            return out
        note = f" ({spec.note})" if spec.note else ""
        if len(pairs) >= 2 and float(np.ptp(hv)) > 0 and float(np.ptp(pv)) > 2 * spec.tol:
            slope, offset = np.polyfit(hv, pv, 1)
            fit_err = np.abs(slope * hv + offset - pv)
            out.slope, out.offset = float(slope), float(offset)
            out.rmse = float(np.sqrt(np.mean(fit_err ** 2)))
            if bool((fit_err <= tol).all()) and (len(pairs) > 2 or abs(slope) > 1e-9):
                out.verdict = SCALED
                out.formula = self._suggest(name, out.slope, out.offset)
                sure = "" if len(pairs) > 2 else "; only two points, so this fits by construction"
                out.detail = (f"{shown} {spec.units}: PID = {slope:.4g} * channel "
                              f"{offset:+.4g}{sure}{note}")
                return out
            out.detail = f"{shown} {spec.units}: no straight line fits (rmse {out.rmse:.3g}){note}"
            return out
        if len(pairs) == 1 and hv[0] != 0 and pv[0] != 0:
            out.detail = (f"{shown} {spec.units}: differ by a factor of {pv[0] / hv[0]:.4g}. "
                          f"One point cannot tell scale from offset: add a snapshot at a "
                          f"different operating point{note}")
            return out
        out.detail = (f"{shown} {spec.units}: differ, and the snapshots are too alike to fit a "
                      f"scale: add one at a different operating point{note}")
        return out

    def _suggest(self, name: str, slope: float, offset: float) -> str:
        base = self.formulas.get(name, "x").strip()
        inner = base if base == "x" else f"({base})"
        text = f"{inner} * {slope:.6g}"
        if abs(offset) > 1e-9:
            text += f" {'+' if offset >= 0 else '-'} {abs(offset):.6g}"
        return text


def steady_mean(samples: Sequence[Mapping[str, float | None]],
                names: Sequence[str] | None = None) -> dict[str, float]:
    """Mean of each channel over a list of decoded samples (missing values skipped)."""
    keys = names if names is not None else sorted({k for s in samples for k in s})
    out: dict[str, float] = {}
    for key in keys:
        vals = [s[key] for s in samples if s.get(key) is not None]
        if vals:
            out[key] = float(np.mean(vals))         # type: ignore[arg-type]
    return out
