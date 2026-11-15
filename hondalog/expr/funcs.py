"""The function whitelist: everything an expression is allowed to call.

Two kinds:

* pure functions (``abs``, ``poly``, ``table`` ...): a scalar implementation
  and a numpy one for whole-log evaluation;
* stateful helpers (``avg``, ``slope``, ``smooth`` ...): one small state object
  per call site, fed once per sample with ``update(t, x)``.

Nothing here does I/O and nothing reaches Python builtins by name.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

# ---- pure, scalar -----------------------------------------------------------


def _bit(x: float, n: float) -> int:
    n = int(n)
    if not 0 <= n < 64:
        raise ValueError("bit index out of range")
    return (int(x) >> n) & 1


def _signed(x: float, bits: float = 8) -> int:
    xi, nb = int(x), int(bits)
    if not 1 <= nb <= 64:
        raise ValueError("bits out of range")
    xi &= (1 << nb) - 1
    return xi - (1 << nb) if xi & (1 << (nb - 1)) else xi


def _poly(x: float, *coeffs: float) -> float:
    """c0 + c1*x + c2*x^2 ... (Horner)."""
    acc = 0.0
    for c in reversed(coeffs):
        acc = acc * x + c
    return acc


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _table(x: float, *pairs: float) -> float:
    """Piecewise-linear lookup: table(x, x0, y0, x1, y1, ...), ends held."""
    n = len(pairs) // 2
    if x <= pairs[0]:
        return pairs[1]
    for i in range(1, n):
        x1 = pairs[2 * i]
        if x <= x1:
            x0, y0, y1 = pairs[2 * i - 2], pairs[2 * i - 1], pairs[2 * i + 1]
            return y1 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pairs[-1]


def _thermistor(x: float, a: float, b: float, c: float, full: float = 255.0) -> float:
    """Steinhart-Hart on an ADC count: degrees C from a pulled-up NTC divider.

    ``r = x / (full - x)`` is the thermistor's resistance as a fraction of the
    pull-up; ``1/T = a + b*ln(r) + c*ln(r)^3`` with T in kelvin.
    """
    ln_r = math.log(x / (full - x))
    return 1.0 / (a + b * ln_r + c * ln_r ** 3) - 273.15


def _between(x: float, lo: float, hi: float) -> bool:
    return lo <= x <= hi


def _round(x: float, digits: float = 0) -> float:
    return round(x, int(digits)) if digits else float(round(x))


# ---- pure, numpy ------------------------------------------------------------

Arr = Any        # np.ndarray or a Python number; numpy broadcasts either


def _finite(a: Arr) -> Arr:
    """Turn inf into NaN: a math error is 'invalid', it must not travel on."""
    return np.where(np.isfinite(a), a, np.nan)


def _to_int(a: Arr) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(a, dtype=float)
    bad = ~np.isfinite(arr) | (np.abs(arr) >= 2.0 ** 62)
    return np.where(bad, 0, arr).astype(np.int64), bad


def _v_bit(x: Arr, n: Arr) -> Arr:
    xi, bad = _to_int(x)
    ni, bad_n = _to_int(n)
    bad = bad | bad_n | (ni < 0) | (ni >= 64)
    out = ((xi >> np.where(bad, 0, ni)) & 1).astype(float)
    return np.where(bad, np.nan, out)


def _v_signed(x: Arr, bits: Arr = 8) -> Arr:
    nb = int(bits)
    if not 1 <= nb <= 64:
        return np.full(np.shape(x), np.nan)
    xi, bad = _to_int(x)
    if nb < 64:
        xi = xi & ((1 << nb) - 1)
        xi = np.where(xi & (1 << (nb - 1)), xi - (1 << nb), xi)
    return np.where(bad, np.nan, xi.astype(float))


def _v_poly(x: Arr, *coeffs: Arr) -> Arr:
    acc: Arr = 0.0
    for c in reversed(coeffs):
        acc = acc * x + c
    return acc


def _v_table(x: Arr, *pairs: Arr) -> Arr:
    xs = np.array([float(p) for p in pairs[0::2]])
    ys = np.array([float(p) for p in pairs[1::2]])
    return np.interp(x, xs, ys)


def _v_thermistor(x: Arr, a: Arr, b: Arr, c: Arr, full: Arr = 255.0) -> Arr:
    ln_r = np.log(x / (full - x))
    return 1.0 / (a + b * ln_r + c * ln_r ** 3) - 273.15


def _v_round(x: Arr, digits: Arr = 0) -> Arr:
    return np.round(x, int(digits))


def _v_pow(a: Arr, b: Arr) -> Arr:
    """numpy says nan ** 0 == 1; here a missing input stays missing."""
    out = np.power(np.asarray(a, dtype=float), b)
    return np.where(np.isnan(a) | np.isnan(b), np.nan, out)


def _v_minmax(fn: Callable[..., Any]) -> Callable[..., Arr]:
    def run(*args: Arr) -> Arr:
        acc = args[0]
        nan = np.isnan(acc)
        for a in args[1:]:
            nan = nan | np.isnan(a)
            acc = fn(acc, a)
        return np.where(nan, np.nan, acc)
    return run


@dataclass(frozen=True)
class Pure:
    scalar: Callable[..., Any]
    vector: Callable[..., Arr]
    min_args: int
    max_args: int | None        # None = any number
    doc: str
    const_from: int | None = None        # arguments from this position on must be constants
    pairs: bool = False                  # table(): the constants are ascending x, y pairs


PURE: dict[str, Pure] = {
    "abs": Pure(abs, np.abs, 1, 1, "abs(x)"),
    "min": Pure(min, _v_minmax(np.minimum), 2, None, "min(a, b, ...) smallest argument"),
    "max": Pure(max, _v_minmax(np.maximum), 2, None, "max(a, b, ...) largest argument"),
    "round": Pure(_round, _v_round, 1, 2, "round(x[, digits])", const_from=1),
    "int": Pure(int, np.trunc, 1, 1, "int(x) drops the fraction"),
    "float": Pure(float, lambda x: x * 1.0, 1, 1, "float(x)"),
    "floor": Pure(math.floor, np.floor, 1, 1, "floor(x)"),
    "ceil": Pure(math.ceil, np.ceil, 1, 1, "ceil(x)"),
    "sqrt": Pure(math.sqrt, np.sqrt, 1, 1, "sqrt(x)"),
    "exp": Pure(math.exp, np.exp, 1, 1, "exp(x)"),
    "ln": Pure(math.log, np.log, 1, 1, "ln(x) natural logarithm"),
    "log": Pure(math.log, np.log, 1, 1, "log(x) natural logarithm"),
    "log10": Pure(math.log10, np.log10, 1, 1, "log10(x)"),
    "pow": Pure(math.pow, _v_pow, 2, 2, "pow(x, y)"),
    "sin": Pure(math.sin, np.sin, 1, 1, "sin(x), radians"),
    "cos": Pure(math.cos, np.cos, 1, 1, "cos(x), radians"),
    "sign": Pure(_sign, np.sign, 1, 1, "sign(x): -1, 0 or 1"),
    "clamp": Pure(_clamp, lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi), 3, 3,
                  "clamp(x, lo, hi)"),
    "between": Pure(_between,
                    lambda x, lo, hi: np.where(np.isnan(x) | np.isnan(lo) | np.isnan(hi),
                                               np.nan, (x >= lo) & (x <= hi)),
                    3, 3, "between(x, lo, hi): 1 when lo <= x <= hi"),
    "bit": Pure(_bit, _v_bit, 2, 2, "bit(x, n): bit n of x, 0 = least significant"),
    "signed": Pure(_signed, _v_signed, 1, 2, "signed(x[, bits]) two's complement",
                   const_from=1),
    "poly": Pure(_poly, _v_poly, 2, None, "poly(x, c0, c1, ...) = c0 + c1*x + c2*x^2 ..."),
    "table": Pure(_table, _v_table, 5, None,
                  "table(x, x0, y0, x1, y1, ...) straight lines between points",
                  const_from=1, pairs=True),
    "thermistor": Pure(_thermistor, _v_thermistor, 4, 5,
                       "thermistor(x, a, b, c[, full]) Steinhart-Hart on an ADC count"),
}


# ---- stateful ---------------------------------------------------------------

_RESYNC = 1024       # recompute running sums this often so rounding cannot build up


class State:
    """One call site's memory. ``update`` returns None for 'not enough data'."""

    def update(self, t: float, x: float) -> float | None:        # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:                                     # pragma: no cover
        raise NotImplementedError


class _Window(State):
    """Samples from the last ``ms`` milliseconds as (t, x) pairs."""

    def __init__(self, ms: float):
        if not ms > 0:
            raise ValueError("the time window must be more than 0 ms")
        self.w = ms / 1000.0
        self.q: deque[tuple[float, float]] = deque()
        self.count = 0

    def reset(self) -> None:
        self.q.clear()
        self.count = 0
        self._clear()

    def _clear(self) -> None:
        pass

    def _push(self, t: float, x: float) -> bool:
        """Append; returns True when running sums must be rebuilt from scratch."""
        q = self.q
        if q and t < q[-1][0]:          # time went backwards: a seek or a new log
            self.reset()
        q.append((t, x))
        self.count += 1
        return self.count % _RESYNC == 0


class Avg(_Window):
    """avg(x, ms): mean of the samples in the window."""

    def __init__(self, ms: float):
        super().__init__(ms)
        self.s = 0.0

    def _clear(self) -> None:
        self.s = 0.0

    def update(self, t: float, x: float) -> float:
        resync = self._push(t, x)
        q = self.q
        self.s += x
        lim = t - self.w
        while q[0][0] < lim:
            self.s -= q.popleft()[1]
        if resync or len(q) == 1:
            self.s = math.fsum(v for _, v in q)
        return self.s / len(q)


class Stdev(_Window):
    """stdev(x, ms): population standard deviation over the window."""

    def update(self, t: float, x: float) -> float:
        self._push(t, x)
        q = self.q
        lim = t - self.w
        while q[0][0] < lim:
            q.popleft()
        n = len(q)
        mean = math.fsum(v for _, v in q) / n
        return math.sqrt(math.fsum((v - mean) ** 2 for _, v in q) / n)


class Slope(_Window):
    """slope(x, ms): least-squares slope over the window, in units per second."""

    def __init__(self, ms: float):
        super().__init__(ms)
        self._clear()

    def _clear(self) -> None:
        self.t0 = 0.0
        self.st = self.sx = self.stt = self.stx = 0.0

    def _rebuild(self) -> None:
        q = self.q
        self.t0 = q[0][0]
        self.st = math.fsum(t - self.t0 for t, _ in q)
        self.sx = math.fsum(x for _, x in q)
        self.stt = math.fsum((t - self.t0) ** 2 for t, _ in q)
        self.stx = math.fsum((t - self.t0) * x for t, x in q)

    def update(self, t: float, x: float) -> float | None:
        resync = self._push(t, x)
        q = self.q
        if len(q) == 1:
            self._rebuild()
            return None
        r = t - self.t0
        self.st += r
        self.sx += x
        self.stt += r * r
        self.stx += r * x
        lim = t - self.w
        while q[0][0] < lim:
            ot, ox = q.popleft()
            r = ot - self.t0
            self.st -= r
            self.sx -= ox
            self.stt -= r * r
            self.stx -= r * ox
        n = len(q)
        if resync or n <= 2:
            self._rebuild()
        if n < 2:
            return None
        den = n * self.stt - self.st * self.st
        if den <= 1e-12:
            return None
        return (n * self.stx - self.st * self.sx) / den


class _Extreme(_Window):
    """Sliding minimum or maximum with a monotonic queue: O(1) per sample."""

    sign = 1.0

    def update(self, t: float, x: float) -> float:
        q = self.q
        if q and t < q[-1][0]:
            q.clear()
        key = x * self.sign
        while q and q[-1][1] * self.sign >= key:
            q.pop()
        q.append((t, x))
        lim = t - self.w
        while q[0][0] < lim:
            q.popleft()
        return q[0][1]


class MinW(_Extreme):
    """minw(x, ms): smallest value in the window."""
    sign = 1.0


class MaxW(_Extreme):
    """maxw(x, ms): largest value in the window."""
    sign = -1.0


class Smooth(State):
    """smooth(x, n): mean of the last n samples."""

    def __init__(self, n: float):
        if int(n) < 1 or int(n) > 100000:
            raise ValueError("the sample count must be 1 to 100000")
        self.q: deque[float] = deque(maxlen=int(n))
        self.s = 0.0
        self.count = 0

    def reset(self) -> None:
        self.q.clear()
        self.s = 0.0
        self.count = 0

    def update(self, t: float, x: float) -> float:
        q = self.q
        if len(q) == q.maxlen:
            self.s -= q[0]
        q.append(x)
        self.s += x
        self.count += 1
        if self.count % _RESYNC == 0:
            self.s = math.fsum(q)
        return self.s / len(q)


class Median(State):
    """median(x, n): median of the last n samples (kills single-sample spikes)."""

    def __init__(self, n: float):
        if int(n) < 1 or int(n) > 1001:
            raise ValueError("the sample count must be 1 to 1001")
        self.q: deque[float] = deque(maxlen=int(n))

    def reset(self) -> None:
        self.q.clear()

    def update(self, t: float, x: float) -> float:
        self.q.append(x)
        s = sorted(self.q)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])


class Ema(State):
    """ema(x, ms): first-order low-pass with time constant ms."""

    def __init__(self, ms: float):
        if not ms > 0:
            raise ValueError("the time constant must be more than 0 ms")
        self.tau = ms / 1000.0
        self.y: float | None = None
        self.t = 0.0

    def reset(self) -> None:
        self.y = None

    def update(self, t: float, x: float) -> float:
        if self.y is None or t < self.t:
            self.y = x
        else:
            self.y += (x - self.y) * (1.0 - math.exp(-(t - self.t) / self.tau))
        self.t = t
        return self.y


class Lag(State):
    """prev(x[, n]) and [x-n]: the value n valid samples ago."""

    def __init__(self, n: float = 1):
        if int(n) < 1 or int(n) > 10000:
            raise ValueError("can look back 1 to 10000 samples")
        self.q: deque[float] = deque(maxlen=int(n) + 1)

    def reset(self) -> None:
        self.q.clear()

    def update(self, t: float, x: float) -> float | None:
        q = self.q
        q.append(x)
        return q[0] if len(q) == q.maxlen else None


class Delta(State):
    """delta(x): change since the previous sample."""

    def __init__(self) -> None:
        self.last: float | None = None

    def reset(self) -> None:
        self.last = None

    def update(self, t: float, x: float) -> float | None:
        last, self.last = self.last, x
        return None if last is None else x - last


class Rate(State):
    """rate(x): change per second since the previous sample."""

    def __init__(self) -> None:
        self.last: tuple[float, float] | None = None

    def reset(self) -> None:
        self.last = None

    def update(self, t: float, x: float) -> float | None:
        last, self.last = self.last, (t, x)
        if last is None or t <= last[0]:
            return None
        return (x - last[1]) / (t - last[0])


class Integ(State):
    """integ(x): running integral of x over time in seconds (trapezoid)."""

    def __init__(self) -> None:
        self.last: tuple[float, float] | None = None
        self.acc = 0.0

    def reset(self) -> None:
        self.last = None
        self.acc = 0.0

    def update(self, t: float, x: float) -> float:
        last = self.last
        if last is not None and t < last[0]:
            self.acc, last = 0.0, None
        if last is not None:
            self.acc += 0.5 * (x + last[1]) * (t - last[0])
        self.last = (t, x)
        return self.acc


class Held(State):
    """held(cond, ms): 1 once cond has been true for ms without a break."""

    def __init__(self, ms: float):
        if ms < 0:
            raise ValueError("the hold time cannot be negative")
        self.w = ms / 1000.0
        self.since: float | None = None

    def reset(self) -> None:
        self.since = None

    def update(self, t: float, x: float) -> float:
        if not x:
            self.since = None
            return 0.0
        if self.since is None or t < self.since:
            self.since = t
        return 1.0 if t - self.since >= self.w else 0.0


@dataclass(frozen=True)
class Stateful:
    factory: Callable[..., State]
    params: int                # constant parameters after x: avg(x, ms) has 1
    optional: int              # how many of those may be left out
    doc: str


STATEFUL: dict[str, Stateful] = {
    "avg": Stateful(Avg, 1, 0, "avg(x, ms) mean over the last ms milliseconds"),
    "slope": Stateful(Slope, 1, 0, "slope(x, ms) rate of change per second over the last ms"),
    "minw": Stateful(MinW, 1, 0, "minw(x, ms) smallest value in the last ms"),
    "maxw": Stateful(MaxW, 1, 0, "maxw(x, ms) largest value in the last ms"),
    "stdev": Stateful(Stdev, 1, 0, "stdev(x, ms) standard deviation over the last ms"),
    "smooth": Stateful(Smooth, 1, 0, "smooth(x, n) mean of the last n samples"),
    "median": Stateful(Median, 1, 0, "median(x, n) median of the last n samples"),
    "ema": Stateful(Ema, 1, 0, "ema(x, ms) low-pass filter, time constant ms"),
    "prev": Stateful(Lag, 1, 1, "prev(x[, n]) value n samples ago, same as [x-n]"),
    "delta": Stateful(Delta, 0, 0, "delta(x) change since the previous sample"),
    "rate": Stateful(Rate, 0, 0, "rate(x) change per second since the previous sample"),
    "integ": Stateful(Integ, 0, 0, "integ(x) running integral over seconds"),
    "held": Stateful(Held, 1, 0, "held(cond, ms) 1 once cond has been true for ms"),
}

# Handled by the compilers themselves (they need to catch 'invalid').
SPECIAL: dict[str, str] = {
    "valid": "valid(x) 1 when x has a value right now, else 0",
    "coalesce": "coalesce(a, b, ...) first argument that has a value",
    "time": "time() seconds since the start of the session or log",
}


def function_help() -> list[str]:
    """One line per callable, for the UI's expression editor."""
    lines = [p.doc for p in PURE.values()] + [s.doc for s in STATEFUL.values()]
    return sorted(lines + list(SPECIAL.values()))


def run_state(state: State, t: Sequence[float], x: np.ndarray) -> np.ndarray:
    """Feed a whole column through a state object. NaN in = skipped, NaN out."""
    out = np.full(len(x), np.nan)
    update = state.update
    for i, (ti, xi) in enumerate(zip(t, x.tolist())):
        if xi == xi:
            r = update(ti, xi)
            if r is not None:
                out[i] = r
    return out
