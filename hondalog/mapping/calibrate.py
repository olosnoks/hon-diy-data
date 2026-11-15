"""Calibration helpers: fit a formula from (raw byte, reference value) pairs.

    pairs = Collector()
    pairs.add(raw=146, reference=13.97)      # meter reading typed in, live
    ...
    fit = pairs.fit()                        # picks a straight line or a curve
    fit.formula                              # "x * 0.0956938 + 0.0004"
    fit.rmse, fit.max_error                  # the residual, in reference units

The residual is measured by running the written formula text through the real
evaluator, so what is reported is what the map will do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..addressmap import compile_formula


@dataclass
class Fit:
    kind: str                      # "linear" | "poly" | "thermistor"
    formula: str                   # map formula text
    coeffs: tuple[float, ...]
    n: int
    rmse: float
    max_error: float
    r2: float
    residuals: list[float] = field(default_factory=list)       # formula(raw) - reference
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        warn = f"  ({'; '.join(self.warnings)})" if self.warnings else ""
        return (f"{self.formula}   rmse {self.rmse:.3g}, worst {self.max_error:.3g}, "
                f"n {self.n}{warn}")


def _clean(raw: Sequence[float], ref: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.asarray(raw, dtype=float), np.asarray(ref, dtype=float)
    if x.shape != y.shape:
        raise ValueError("raw and reference must have the same length")
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def _measure(kind: str, formula: str, coeffs: Sequence[float], x: np.ndarray,
             y: np.ndarray) -> Fit:
    fn = compile_formula(formula)
    got = np.array([float(fn(int(v) if float(v).is_integer() else v, [])) for v in x])  # type: ignore[arg-type]
    res = got - y
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((res ** 2).sum()) / ss_tot if ss_tot > 0 else float("nan")
    return Fit(kind, formula, tuple(float(c) for c in coeffs), len(x),
               float(np.sqrt(np.mean(res ** 2))), float(np.abs(res).max()), r2, res.tolist())


def _signed(value: float) -> str:
    return f"{'+' if value >= 0 else '-'} {abs(value):.7g}"


def fit_linear(raw: Sequence[float], ref: Sequence[float]) -> Fit:
    """Least-squares straight line: ``x * a + b``."""
    x, y = _clean(raw, ref)
    if len(x) < 2 or float(np.ptp(x)) == 0:
        raise ValueError("a line needs at least two points with different raw values")
    a, b = np.polyfit(x, y, 1)
    fit = _measure("linear", f"x * {a:.7g} {_signed(float(b))}", (b, a), x, y)
    if len(x) == 2:
        fit.warnings.append("two points: the line fits them exactly, a third would test it")
    return fit


def fit_poly(raw: Sequence[float], ref: Sequence[float], degree: int = 3) -> Fit:
    """Least-squares polynomial: ``poly(x, c0, c1, ...)``."""
    x, y = _clean(raw, ref)
    if not 1 <= degree <= 6:
        raise ValueError("degree must be 1 to 6")
    if len(np.unique(x)) <= degree:
        raise ValueError(f"degree {degree} needs at least {degree + 1} different raw values")
    scale = max(1.0, float(np.abs(x).max()))
    c = np.polyfit(x / scale, y, degree)[::-1] / scale ** np.arange(degree + 1)
    formula = "poly(x, " + ", ".join(f"{v:.10g}" for v in c) + ")"
    fit = _measure("poly", formula, c, x, y)
    if len(np.unique(x)) == degree + 1:
        fit.warnings.append("as many points as coefficients: it fits them exactly")
    lo, hi = float(x.min()), float(x.max())
    if hi - lo < 100:
        fit.warnings.append(f"only covers raw {lo:g}..{hi:g}; outside that the curve is a guess")
    return fit


def fit_thermistor(points: Sequence[tuple[float, float]], full: float = 255.0) -> Fit:
    """Steinhart-Hart from three (raw, °C) points, e.g. ice water, room, hot.

    Assumes the usual Honda arrangement: an NTC thermistor to ground with a
    pull-up, read by an 8-bit ADC (``full`` = 255). Gives
    ``thermistor(x, a, b, c)`` for the map.
    """
    if len(points) != 3:
        raise ValueError("give exactly three (raw, °C) points")
    x = np.array([p[0] for p in points], dtype=float)
    temp = np.array([p[1] for p in points], dtype=float)
    if len(np.unique(x)) != 3 or bool(((x <= 0) | (x >= full)).any()):
        raise ValueError(f"the three raw values must differ and lie between 0 and {full:g}")
    ln_r = np.log(x / (full - x))
    matrix = np.column_stack([np.ones(3), ln_r, ln_r ** 3])
    a, b, c = np.linalg.solve(matrix, 1.0 / (temp + 273.15))
    args = f"x, {a:.10g}, {b:.10g}, {c:.10g}" + ("" if full == 255.0 else f", {full:g}")
    fit = _measure("thermistor", f"thermistor({args})", (a, b, c), x, temp)
    order = np.argsort(x)
    if not (bool((np.diff(temp[order]) < 0).all()) or bool((np.diff(temp[order]) > 0).all())):
        fit.warnings.append("temperature does not move one way with the raw value")
    span = float(np.ptp(temp))
    if span < 40:
        fit.warnings.append(f"points only span {span:.0f} °C; spread them out (0, 25, 90 °C)")
    return fit


def fit_best(raw: Sequence[float], ref: Sequence[float], max_degree: int = 3,
             good_enough: float = 0.0) -> Fit:
    """The simplest formula that fits: a line unless a curve is clearly better.

    A higher degree is taken only when it cuts the rmse by more than half and
    there are at least three points per coefficient. ``good_enough`` is an rmse
    at which to stop looking (the reference's own reading error).
    """
    x, _ = _clean(raw, ref)
    best = fit_linear(raw, ref)
    for degree in range(2, max_degree + 1):
        if best.rmse <= good_enough or len(np.unique(x)) < 3 * (degree + 1):
            break
        trial = fit_poly(raw, ref, degree)
        if trial.rmse < 0.5 * best.rmse:
            best = trial
    return best


class Collector:
    """Pairs gathered live: the raw value now, and what the reference says."""

    def __init__(self) -> None:
        self.raw: list[float] = []
        self.reference: list[float] = []

    def add(self, raw: float, reference: float) -> None:
        self.raw.append(float(raw))
        self.reference.append(float(reference))

    def __len__(self) -> int:
        return len(self.raw)

    def clear(self) -> None:
        self.raw.clear()
        self.reference.clear()

    def fit(self, kind: str = "best", degree: int = 3) -> Fit:
        if kind == "linear":
            return fit_linear(self.raw, self.reference)
        if kind == "poly":
            return fit_poly(self.raw, self.reference, degree)
        if kind == "thermistor":
            return fit_thermistor(list(zip(self.raw, self.reference)))
        return fit_best(self.raw, self.reference, degree)
