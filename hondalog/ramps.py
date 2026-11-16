"""The colour ramps of the design (docs/design/HANDOFF.md section 2), without Qt,
and how a channel's values are spread over them.

``ui/theme.py`` paints with these; the KML export colours a track with them,
so a track looks the same in Google Earth as on the Map screen.
"""

from __future__ import annotations

import numpy as np

#: sequential: values, samples (cividis-like, colour-blind safe, monotonic lightness)
SEQ_RAMP = ("#00224E", "#35456C", "#666970", "#948E77", "#C8B866", "#FEE838")
#: diverging: fuel trim (blue = rich / ECU pulling fuel, orange = lean / ECU adding)
DIV_RAMP = ("#053061", "#2166AC", "#F7F7F7", "#E08214", "#7F3B08")


def stops(hexes) -> np.ndarray:
    return np.array([[int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)] for h in hexes], dtype=np.float32)


_SEQ = stops(SEQ_RAMP)
_DIV = stops(DIV_RAMP)


def ramp(table: np.ndarray, t) -> np.ndarray:
    """Piecewise-linear ramp. t in 0..1 (any shape) -> rgb array (..., 3)."""
    t = np.clip(np.asarray(t, dtype=np.float32), 0.0, 1.0) * (len(table) - 1)
    i = np.minimum(t.astype(np.int32), len(table) - 2)
    f = (t - i)[..., None]
    return table[i] * (1.0 - f) + table[i + 1] * f


def seq(t) -> np.ndarray:
    return ramp(_SEQ, t)


def div(t) -> np.ndarray:
    return ramp(_DIV, t)


def is_diverging(channel: str) -> bool:
    """Channels whose zero is the middle of the scale: fuel trims."""
    name = channel.lower()
    return "trim" in name or name in ("stft", "ltft")


def value_range(channel: str, values: np.ndarray, lo: float | None = None,
                hi: float | None = None) -> tuple[float, float, bool]:
    """(lo, hi, diverging) to colour ``values`` with: the 2nd to 98th
    percentile (one wild sample does not wash the rest out), symmetric
    around zero for a trim, at least a little wide."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    diverging = is_diverging(channel)
    if lo is None or hi is None:
        if len(v):
            a, b = (float(x) for x in np.percentile(v, (2.0, 98.0)))
        else:
            a, b = 0.0, 1.0
        lo = a if lo is None else lo
        hi = b if hi is None else hi
    if diverging:
        m = max(abs(lo), abs(hi), 1.0)
        lo, hi = -m, m
    if not hi > lo:
        hi = lo + max(1.0, abs(lo) * 0.1)
    if channel.lower() in ("gps_speed", "vss", "speed") and lo > 0:
        lo = 0.0                            # speed starts at standing still
    return float(lo), float(hi), diverging


def bins(values: np.ndarray, lo: float, hi: float, n: int) -> np.ndarray:
    """Values -> bin numbers 0..n-1; NaN -> -1."""
    v = np.asarray(values, dtype=np.float64)
    out = np.full(len(v), -1, dtype=np.int32)
    ok = np.isfinite(v)
    out[ok] = np.clip(((v[ok] - lo) / (hi - lo) * n).astype(np.int64), 0, n - 1)
    return out


def bin_colours(n: int, diverging: bool) -> np.ndarray:
    """(n, 3) rgb 0..1, the centre colour of each bin."""
    t = (np.arange(n) + 0.5) / n
    return div(t) if diverging else seq(t)


__all__ = ["DIV_RAMP", "SEQ_RAMP", "bin_colours", "bins", "div", "is_diverging", "ramp", "seq", "stops",
           "value_range"]
