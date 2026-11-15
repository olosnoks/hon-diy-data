"""The correlation finder: which address behaves like this signal?

Every address is tried as an unsigned byte and as a 16-bit value in both byte
orders (and optionally as 1/x, for ECUs that store a period). Each candidate is
compared with a reference (a verified channel, a typed-in reading, a Track B
channel, ISO PID values, or an expected shape such as "rises steadily") and the
list comes back ranked, with a least-squares formula suggestion.

Also here: ``find_value`` (which address reads 3000 rpm right now, given a
formula?) and ``changed_bits`` (which bits flipped only while the A/C was on?).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from ..addressmap import AddressMap, MapError, compile_formula
from .sweeps import SweepRecording

MIN_SAMPLES = 5


@dataclass(frozen=True)
class Candidate:
    addr: int
    width: int = 1                 # bytes: 1 or 2
    endian: str = "big"            # for width 2
    inverse: bool = False          # compared as 1/x

    @property
    def label(self) -> str:
        kind = "u8" if self.width == 1 else f"u16{'be' if self.endian == 'big' else 'le'}"
        return f"0x{self.addr:02X} {kind}" + (" 1/x" if self.inverse else "")

    def channel_fields(self) -> dict[str, Any]:
        """addr/length/endian in the form mapedit.update_channel takes."""
        return {"addr": self.addr, "length": self.width, "endian": self.endian}


@dataclass
class Match:
    candidate: Candidate
    score: float                   # 0..1, higher is better
    r: float                       # Pearson correlation with the reference
    rho: float                     # Spearman (rank) correlation
    slope: float = float("nan")    # reference ~= slope * x + offset  (x or 1/x)
    offset: float = float("nan")
    rmse: float = float("nan")     # of that fit, in reference units
    n: int = 0                     # samples used
    span: float = 0.0              # raw range seen (max - min)
    notes: list[str] = field(default_factory=list)

    @property
    def formula(self) -> str:
        """The least-squares fit as map formula text."""
        if self.slope != self.slope:
            return ""
        if self.candidate.inverse:
            return f"{self.slope:.7g} / x + {self.offset:.6g}".replace("+ -", "- ")
        return f"x * {self.slope:.7g} + {self.offset:.6g}".replace("+ -", "- ")

    def __str__(self) -> str:
        fit = f"  fit: {self.formula} (rmse {self.rmse:.3g})" if self.formula else ""
        note = f"  [{'; '.join(self.notes)}]" if self.notes else ""
        return (f"{self.candidate.label:14} score {self.score:.3f}  r {self.r:+.3f}  "
                f"rho {self.rho:+.3f}  n {self.n}{fit}{note}")


# ---- candidate series -------------------------------------------------------

def candidates(rec: SweepRecording, widths: Sequence[int] = (1, 2),
               inverse: bool = False) -> tuple[list[Candidate], np.ndarray, np.ndarray]:
    """All candidates with their raw series.

    Returns (candidates, values (N, C) float with NaN for unread, times (N, C)).
    A 16-bit value takes the read time of its first byte.
    """
    mem, times = rec.data
    raw = np.where(mem >= 0, mem, np.nan).astype(float)
    cands: list[Candidate] = []
    cols: list[np.ndarray] = []
    tcols: list[np.ndarray] = []
    read = set(rec.read_addresses().tolist())
    for addr in sorted(read):
        if 1 in widths:
            cands.append(Candidate(addr))
            cols.append(raw[:, addr])
            tcols.append(times[:, addr])
        if 2 in widths and addr + 1 in read:
            for endian in ("big", "little"):
                hi, lo = (addr, addr + 1) if endian == "big" else (addr + 1, addr)
                cands.append(Candidate(addr, 2, endian))
                cols.append(raw[:, hi] * 256 + raw[:, lo])
                tcols.append(times[:, addr])
    if not cands:
        return [], np.empty((len(mem), 0)), np.empty((len(mem), 0))
    values, tmat = np.column_stack(cols), np.column_stack(tcols)
    if inverse:
        inv = [Candidate(c.addr, c.width, c.endian, True) for c in cands]
        with np.errstate(divide="ignore", invalid="ignore"):
            flipped = np.where(values > 0, 1.0 / values, np.nan)
        cands, values, tmat = cands + inv, np.hstack([values, flipped]), np.hstack([tmat, tmat])
    return cands, values, tmat


def _ranks(col: np.ndarray) -> np.ndarray:
    """Average ranks (ties share a rank), as Spearman needs."""
    _, inverse, counts = np.unique(col, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return ((ends - counts + 1 + ends) / 2.0)[inverse]


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    xd, yd = x - x.mean(), y - y.mean()
    den = float(np.sqrt((xd * xd).sum() * (yd * yd).sum()))
    return float((xd * yd).sum() / den) if den > 0 else 0.0


# ---- ranking ----------------------------------------------------------------

def rank(rec: SweepRecording, ref_t: Any, ref_v: Any, *, widths: Sequence[int] = (1, 2),
         inverse: bool = False, method: str = "both", top: int = 20,
         min_span: float = 3.0, exclude: Iterable[int] = ()) -> list[Match]:
    """Rank every candidate address against a reference signal.

    ref_t, ref_v  the reference: times in the recording's clock and values.
                  It is interpolated onto each candidate's own read times, so
                  the reference may be sampled faster or slower than the sweeps.
    method        "pearson" (straight-line relation), "spearman" (any steadily
                  rising or falling relation: thermistors, expected shapes) or
                  "both" (the better of the two decides the score).
    inverse       also try 1/x (OBD1 RPM is a period).
    min_span      candidates whose raw value moved less than this are scored
                  down: two counts of jitter correlate with anything.
    exclude       addresses to leave out (the reference's own bytes).
    """
    ref_t = np.asarray(ref_t, dtype=float)
    ref_v = np.asarray(ref_v, dtype=float)
    ok = np.isfinite(ref_t) & np.isfinite(ref_v)
    ref_t, ref_v = ref_t[ok], ref_v[ok]
    if len(ref_t) < 2:
        return []
    order = np.argsort(ref_t, kind="stable")
    ref_t, ref_v = ref_t[order], ref_v[order]
    cands, values, tmat = candidates(rec, widths, inverse)
    skip = set(exclude)
    out: list[Match] = []
    by_key: dict[tuple[int, int, str, bool], Match] = {}
    for j, cand in enumerate(cands):
        if cand.addr in skip or (cand.width == 2 and cand.addr + 1 in skip):
            continue
        x, t = values[:, j], tmat[:, j]
        keep = np.isfinite(x) & (t >= ref_t[0]) & (t <= ref_t[-1])
        if keep.sum() < MIN_SAMPLES:
            continue
        x, y = x[keep], np.interp(t[keep], ref_t, ref_v)
        span = float(x.max() - x.min())
        if span == 0 or float(y.max() - y.min()) == 0:
            continue
        r = _pearson(x, y)
        rho = _pearson(_ranks(x), _ranks(y))
        strength = {"pearson": abs(r), "spearman": abs(rho)}.get(method, max(abs(r), abs(rho)))
        raw_span = span if not cand.inverse else float(np.ptp(1.0 / x))
        weight = min(1.0, raw_span / min_span) if min_span > 0 else 1.0
        slope, offset = np.polyfit(x, y, 1)
        rmse = float(np.sqrt(np.mean((slope * x + offset - y) ** 2)))
        match = Match(cand, strength * weight, r, rho, float(slope), float(offset), rmse,
                      int(keep.sum()), raw_span)
        if weight < 1.0:
            match.notes.append(f"barely moves ({raw_span:g} counts)")
        out.append(match)
        by_key[(cand.addr, cand.width, cand.endian, cand.inverse)] = match
    _prefer_simple(out, by_key)
    out.sort(key=lambda m: (-round(m.score, 6), -round(abs(m.r), 6), m.candidate.width,
                            m.candidate.addr))
    return out[:top] if top else out


def _prefer_simple(matches: list[Match],
                   by_key: dict[tuple[int, int, str, bool], Match]) -> None:
    """A 16-bit candidate must earn its second byte: unless it explains clearly
    more than its own high byte alone does, the single byte is listed first."""
    for m in matches:
        c = m.candidate
        if c.width != 2:
            continue
        high_addr = c.addr if c.endian == "big" else c.addr + 1
        high = by_key.get((high_addr, 1, "big", c.inverse))
        if high is None:
            low = by_key.get((c.addr + 1 if c.endian == "big" else c.addr, 1, "big", c.inverse))
            if low is not None and low.score >= m.score - 1e-9:
                m.notes.append(f"high byte never changes: same as u8 at 0x{low.candidate.addr:02X}")
                m.score *= 0.99
            continue
        unexplained16 = 1.0 - min(1.0, m.score) ** 2
        unexplained8 = 1.0 - min(1.0, high.score) ** 2
        if unexplained16 >= 0.5 * unexplained8:
            m.notes.append(f"no better than u8 at 0x{high_addr:02X}")
            m.score = min(m.score, high.score) * 0.995


def rank_against_channel(rec: SweepRecording, amap: AddressMap, name: str,
                         **kwargs: Any) -> list[Match]:
    """Rank against a channel decoded from the same sweeps (ideally a verified
    one). The channel's own bytes are left out of the result."""
    ch = amap.channel(name)
    if ch is None:
        raise MapError(f"no channel named {name!r}")
    decoded = rec.decode(amap)
    kwargs.setdefault("exclude", range(ch.addr, ch.addr + ch.length))
    return rank(rec, decoded[name + ":t"], decoded[name], **kwargs)


def rank_shape(rec: SweepRecording, shape: str, t0: float, t1: float,
               **kwargs: Any) -> list[Match]:
    """Rank against an expected behaviour between t0 and t1 instead of a signal.

    shape  "rise" or "fall": the real quantity changes steadily in one
           direction (pedal pressed slowly, engine warming up). Only the order
           matters, so the scale and any curve in the sensor do not. The raw
           byte may run the other way (a thermistor's count falls as it warms),
           so both directions are listed; ``rho`` is positive when the raw
           value moves the way the shape says.
    """
    if shape not in ("rise", "fall"):
        raise ValueError("shape must be 'rise' or 'fall'")
    ref_t = np.array([t0, t1], dtype=float)
    ref_v = np.array([0.0, 1.0]) if shape == "rise" else np.array([1.0, 0.0])
    kwargs.setdefault("method", "spearman")
    return rank(rec, ref_t, ref_v, **kwargs)


def rank_steps(rec: SweepRecording, label: str, **kwargs: Any) -> list[Match]:
    """Rank against "different inside the marks with this label than outside"."""
    t = rec.t
    if not len(t):
        return []
    mem, times = rec.data
    grid = np.unique(times[mem >= 0])
    inside = np.zeros(len(grid))
    for mark in rec.marks:
        if mark.label == label:
            inside[mark.contains(grid)] = 1.0
    return rank(rec, grid, inside, **kwargs)


# ---- typed targets ----------------------------------------------------------

@dataclass
class ValueMatch:
    candidate: Candidate
    error: float                   # median |decoded - target|
    decoded: float                 # median decoded value
    spread: float                  # how much the decoded value moved meanwhile
    n: int

    def __str__(self) -> str:
        return (f"{self.candidate.label:14} reads {self.decoded:.4g} "
                f"(off by {self.error:.3g}, moves {self.spread:.3g}, n {self.n})")


def find_value(rec: SweepRecording, target: float, formula: str = "x", tol: float = 0.0,
               rel_tol: float = 0.05, widths: Sequence[int] = (1, 2), top: int = 20) -> list[ValueMatch]:
    """Which addresses read ``target`` when decoded with ``formula``?

    For a typed-in reading (tach says 3000, meter says 12.4 V) or an ISO PID
    value taken under the same steady conditions. Use it on a recording, or a
    window of one, during which the real value was not changing.
    """
    try:
        fn: Callable[[int, Sequence[int]], Any] = compile_formula(formula)
    except MapError:
        raise
    limit = max(tol, rel_tol * abs(target))
    cands, values, _ = candidates(rec, widths)
    out: list[ValueMatch] = []
    for j, cand in enumerate(cands):
        x = values[:, j]
        x = x[np.isfinite(x)]
        if len(x) < 1:
            continue
        decoded = np.array([_decode(fn, int(v), cand) for v in x], dtype=float)
        decoded = decoded[np.isfinite(decoded)]
        if not len(decoded):
            continue
        mid = float(np.median(decoded))
        err = float(np.median(np.abs(decoded - target)))
        if err <= limit:
            out.append(ValueMatch(cand, err, mid, float(np.ptp(decoded)), len(decoded)))
    out.sort(key=lambda m: (m.error + m.spread, m.candidate.width, m.candidate.addr))
    return out[:top] if top else out


def _decode(fn: Callable[[int, Sequence[int]], Any], x: int, cand: Candidate) -> float:
    raw = x.to_bytes(cand.width, "big")           # x is already assembled in the right order
    try:
        return float(fn(x, raw))
    except Exception:
        return float("nan")


# ---- bits -------------------------------------------------------------------

@dataclass
class BitChange:
    addr: int
    bit: int
    inside: int                    # the bit's usual value during the action
    outside: int                   # and the rest of the time
    purity: float                  # 0..1: share of readings that fit that story
    n_inside: int
    n_outside: int

    @property
    def formula(self) -> str:
        return f"bit(x, {self.bit})" if self.inside else f"1 - bit(x, {self.bit})"

    def __str__(self) -> str:
        return (f"0x{self.addr:02X} bit {self.bit}: {self.outside} -> {self.inside} during the "
                f"action ({self.purity:.0%} of {self.n_inside}+{self.n_outside} readings)")


def bit_history(rec: SweepRecording, addr: int) -> tuple[np.ndarray, np.ndarray]:
    """(times, (n, 8) array of 0/1) for one byte; column 0 is bit 0. Unread
    sweeps are dropped. This is what the bit-field explorer's strips show."""
    mem, times = rec.data
    col = mem[:, addr]
    keep = col >= 0
    values = col[keep].astype(np.uint8)
    bits = (values[:, None] >> np.arange(8)[None, :]) & 1
    return times[keep, addr], bits


def changed_bits(rec: SweepRecording, label: str, min_purity: float = 0.9, guard_s: float = 0.0,
                 min_each: int = 2) -> list[BitChange]:
    """The "what changed" filter: bits that are one way inside the marks named
    ``label`` (A/C on, brake pressed) and the other way the rest of the time.

    guard_s   ignore readings this close to a mark's start or end, where the
              switch and the sweep race each other.
    """
    mem, times = rec.data
    if not len(mem):
        return []
    inside = rec.mark_mask(label)
    near = np.zeros(times.shape, dtype=bool)
    if guard_s > 0:
        for mark in rec.marks:
            if mark.label == label:
                near |= np.abs(times - mark.t0) < guard_s
                if mark.t1 is not None:
                    near |= np.abs(times - mark.t1) < guard_s
    out: list[BitChange] = []
    for addr in rec.read_addresses().tolist():
        col = mem[:, addr]
        ok = (col >= 0) & ~near[:, addr]
        a, b = col[ok & inside[:, addr]], col[ok & ~inside[:, addr]]
        if len(a) < min_each or len(b) < min_each:
            continue
        for bit in range(8):
            in_ones = float(((a >> bit) & 1).mean())
            out_ones = float(((b >> bit) & 1).mean())
            in_val, out_val = int(in_ones >= 0.5), int(out_ones >= 0.5)
            if in_val == out_val:
                continue
            fit_in = in_ones if in_val else 1 - in_ones
            fit_out = out_ones if out_val else 1 - out_ones
            purity = (fit_in * len(a) + fit_out * len(b)) / (len(a) + len(b))
            if min(fit_in, fit_out) >= min_purity:
                out.append(BitChange(addr, bit, in_val, out_val, purity, len(a), len(b)))
    out.sort(key=lambda c: (-c.purity, c.addr, c.bit))
    return out


def changed_bytes(rec: SweepRecording, label: str) -> list[int]:
    """Addresses whose values inside the marked stretches never occur outside."""
    mem, _ = rec.data
    inside = rec.mark_mask(label)
    out = []
    for addr in rec.read_addresses().tolist():
        col = mem[:, addr]
        a, b = col[(col >= 0) & inside[:, addr]], col[(col >= 0) & ~inside[:, addr]]
        if len(a) and len(b) and not set(a.tolist()) & set(b.tolist()):
            out.append(addr)
    return out


def flag_channel_table(name: str, change: BitChange, label: str = "") -> dict[str, Any]:
    """A [[channel]] table that promotes a found bit to a flag channel
    (pass it to ``MapFile.add_item("channel", ...)``)."""
    return {"name": name, "label": label or name, "addr": change.addr, "length": 1,
            "formula": change.formula, "kind": "flag", "status": "unverified",
            "source": "memory scanner: bit that follows the marked action"}
