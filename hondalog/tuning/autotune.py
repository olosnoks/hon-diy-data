"""Fuel table correction from a wideband log (the "VE Analyze" idea).

A pure model: arrays in, tables out. Nothing here touches the ROM until
:meth:`AutoTuneResult.apply` is called on a table editor.

For each log sample the fuel error is ``measured lambda / target lambda``:
1.05 means 5% lean, so the cell needs 5% more fuel. Each sample is shared
between the four cells around its (RPM, MAP) point by bilinear weight, and a
cell's suggested correction is the weighted mean of its samples, then:

1. cells with fewer than ``min_samples`` weighted hits are dropped,
2. corrections inside ``deadband_pct`` are dropped,
3. corrections are optionally smoothed between neighbouring cells,
4. each is limited to ``max_correction_pct`` for this pass.

On the stock ECU there is no wideband; :func:`trim_ratio` turns short- and
long-term fuel trim into the same kind of ratio.

The correction multiplies the table's *real* value, which is right when that
value is proportional to fuel delivered (injector time, VE, fuel multiplier).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

import numpy as np

from .binning import bilinear, check_axis, spread
from .table import smooth_kernel

if TYPE_CHECKING:
    from .table import EditResult, Selection, TableEditor

STOICH_GASOLINE = 14.7


def afr_to_lambda(afr, stoich: float = STOICH_GASOLINE) -> np.ndarray:
    return np.asarray(afr, dtype=float) / stoich


def lambda_to_afr(lam, stoich: float = STOICH_GASOLINE) -> np.ndarray:
    return np.asarray(lam, dtype=float) * stoich


def trim_ratio(stft_pct, ltft_pct=0.0) -> np.ndarray:
    """Fuel trims (percent, positive = ECU adding fuel) -> fuel ratio needed."""
    return (1.0 + np.asarray(stft_pct, dtype=float) / 100.0) * \
           (1.0 + np.asarray(ltft_pct, dtype=float) / 100.0)


@dataclass
class AutoTuneSettings:
    min_samples: float = 10.0        # weighted hits a cell needs
    max_correction_pct: float = 10.0  # largest change per pass, either way
    deadband_pct: float = 0.5        # ignore corrections smaller than this
    smoothing: float = 0.0           # 0..1, blend corrections between tuned cells
    neighbour_blend: float = 0.0     # 0..1, share of the neighbours' correction
                                     # given to cells that lacked samples
    lambda_min: float = 0.6          # samples outside this window are sensor
    lambda_max: float = 1.4          # faults, fuel cut or misfire: dropped
    lag_s: float = 0.0               # exhaust transport + sensor delay
    weighting: str = "bilinear"      # or "nearest"

    def validate(self) -> None:
        if self.min_samples < 0 or self.max_correction_pct < 0 or self.deadband_pct < 0:
            raise ValueError("limits must not be negative")
        if not 0 <= self.smoothing <= 1 or not 0 <= self.neighbour_blend <= 1:
            raise ValueError("smoothing and neighbour_blend must be between 0 and 1")
        if self.weighting not in ("bilinear", "nearest"):
            raise ValueError("weighting must be 'bilinear' or 'nearest'")


@dataclass
class AutoTuneResult:
    x_axis: np.ndarray
    y_axis: np.ndarray
    hits: np.ndarray                 # weighted samples per cell
    lambda_mean: np.ndarray          # measured, NaN where no samples
    lambda_target: np.ndarray        # NaN where no samples
    error_pct: np.ndarray            # measured vs target, NaN where no samples
    correction_pct: np.ndarray       # what apply() will do; 0 where nothing
    eligible: np.ndarray             # bool: enough samples
    limited: np.ndarray              # bool: hit max_correction_pct
    samples_total: int = 0
    samples_used: int = 0
    settings: AutoTuneSettings = field(default_factory=AutoTuneSettings)

    @property
    def multiplier(self) -> np.ndarray:
        return 1.0 + self.correction_pct / 100.0

    @property
    def cells_to_change(self) -> int:
        return int((self.correction_pct != 0).sum())

    def overlays(self) -> dict[str, np.ndarray]:
        """The switchable per-cell overlays for the fuel table view."""
        return {"lambda": self.lambda_mean, "target": self.lambda_target,
                "error_pct": self.error_pct, "hits": self.hits,
                "correction_pct": self.correction_pct}

    def apply(self, editor: "TableEditor", selection: "Selection" = None) -> "EditResult":
        """Scale the fuel table by the suggested corrections, as one undo step.

        Only cells in ``selection`` with a non-zero correction change; locked
        cells are skipped by the editor.
        """
        if editor.shape != self.correction_pct.shape:
            raise ValueError(
                f"the result is {self.correction_pct.shape}, the table is {editor.shape}")
        mask = editor.mask(selection) & (self.correction_pct != 0)
        return editor.scale(mask, self.multiplier, label="auto-tune correction")

    def summary(self) -> str:
        changed = self.correction_pct[self.correction_pct != 0]
        if changed.size == 0:
            return (f"{self.samples_used} of {self.samples_total} samples used; "
                    "no cell needs a change")
        return (f"{self.samples_used} of {self.samples_total} samples used; "
                f"{changed.size} cells to change, {changed.min():+.1f}% to "
                f"{changed.max():+.1f}%, {int(self.limited.sum())} at the limit")


def _target_per_sample(target, n: int, x_axis, y_axis, r0, c0, wr, wc) -> np.ndarray:
    target = np.asarray(target, dtype=float)
    if target.ndim == 0:
        return np.full(n, float(target))
    if target.shape == (n,):
        return target
    shape = (np.asarray(y_axis).size, np.asarray(x_axis).size)
    if target.shape == shape:
        r1 = np.minimum(r0 + 1, shape[0] - 1)
        c1 = np.minimum(c0 + 1, shape[1] - 1)
        return (target[r0, c0] * (1 - wr) * (1 - wc) + target[r0, c1] * (1 - wr) * wc
                + target[r1, c0] * wr * (1 - wc) + target[r1, c1] * wr * wc)
    raise ValueError("lambda_target must be a number, one value per sample, "
                     f"or a table of shape {shape}")


def analyze(x, y, x_axis, y_axis, *, lambda_measured=None, lambda_target=1.0,
            ratio=None, time=None, mask=None, locked=None,
            settings: AutoTuneSettings | None = None) -> AutoTuneResult:
    """Suggest fuel corrections.

    x, y            the table's index channels per sample (RPM, MAP)
    x_axis, y_axis  the table's breakpoints
    lambda_measured wideband lambda per sample (use :func:`afr_to_lambda` first)
    lambda_target   a number, an array per sample, or a table (rows x cols)
    ratio           instead of the two above: fuel ratio needed per sample
                    (see :func:`trim_ratio`)
    time            seconds per sample; needed only for ``settings.lag_s``
    mask            bool per sample: extra filter (warm engine, steady throttle)
    locked          bool table: cells that must not be corrected
    """
    s = settings or AutoTuneSettings()
    s.validate()
    x_axis, y_axis = check_axis(x_axis), check_axis(y_axis)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("x and y must be 1-D arrays of the same length")
    n = x.size
    shape = (y_axis.size, x_axis.size)
    if (ratio is None) == (lambda_measured is None):
        raise ValueError("give either lambda_measured or ratio")

    r0, c0, wr, wc, valid = bilinear(x_axis, y_axis, x, y)
    if s.weighting == "nearest":
        r0 = np.minimum(r0 + (wr >= 0.5), shape[0] - 1)
        c0 = np.minimum(c0 + (wc >= 0.5), shape[1] - 1)
        wr, wc = np.zeros(n), np.zeros(n)

    if ratio is not None:
        ratio_s = np.asarray(ratio, dtype=float)
        measured = ratio_s
        target_s = np.ones(n)
        in_window = np.isfinite(ratio_s) & (ratio_s > 0)
    else:
        measured = np.asarray(lambda_measured, dtype=float)
        if measured.shape != (n,):
            raise ValueError("lambda_measured must have one value per sample")
        if s.lag_s:
            if time is None:
                raise ValueError("lag_s needs the time array")
            t = np.asarray(time, dtype=float)
            # the lambda read at t+lag belongs to the fuel injected at t
            measured = np.interp(t + s.lag_s, t, measured, right=np.nan)
        target_s = _target_per_sample(lambda_target, n, x_axis, y_axis, r0, c0, wr, wc)
        in_window = (np.isfinite(measured) & np.isfinite(target_s) & (target_s > 0)
                     & (measured >= s.lambda_min) & (measured <= s.lambda_max))
        with np.errstate(all="ignore"):
            ratio_s = measured / target_s
    keep = valid & in_window
    if mask is not None:
        m = np.asarray(mask, dtype=bool)
        if m.shape != (n,):
            raise ValueError("mask must have one value per sample")
        keep &= m

    ratio_s = np.where(keep, ratio_s, 0.0)
    hits = spread(shape, r0, c0, wr, wc, keep=keep)
    with np.errstate(all="ignore"):
        mean_ratio = spread(shape, r0, c0, wr, wc, ratio_s, keep) / hits
        lam = spread(shape, r0, c0, wr, wc, np.where(keep, measured, 0.0), keep) / hits
        tgt = spread(shape, r0, c0, wr, wc, np.where(keep, target_s, 0.0), keep) / hits
    seen = hits > 0
    error_pct = np.where(seen, (mean_ratio - 1.0) * 100.0, np.nan)
    eligible = seen & (hits >= s.min_samples)
    if locked is not None:
        eligible &= ~np.asarray(locked, dtype=bool)

    correction = np.where(eligible, error_pct, 0.0)
    correction = np.where(np.abs(correction) < s.deadband_pct, 0.0, correction)
    if s.smoothing > 0 and eligible.any():
        weight = np.where(eligible, hits, 0.0)
        with np.errstate(all="ignore"):
            blurred = smooth_kernel(correction * weight) / smooth_kernel(weight)
        correction = np.where(eligible,
                              correction + s.smoothing * (blurred - correction), 0.0)
    if s.neighbour_blend > 0 and eligible.any():
        tuned = eligible.astype(float)
        with np.errstate(all="ignore"):
            around = smooth_kernel(correction * tuned) / smooth_kernel(tuned)
        fill = ~eligible & np.isfinite(around)
        if locked is not None:
            fill &= ~np.asarray(locked, dtype=bool)
        correction = np.where(fill, s.neighbour_blend * around, correction)
    limited = np.abs(correction) > s.max_correction_pct
    correction = np.clip(correction, -s.max_correction_pct, s.max_correction_pct)
    correction = np.where(np.abs(correction) < 1e-9, 0.0, correction)

    return AutoTuneResult(
        x_axis, y_axis, hits,
        np.where(seen, lam, np.nan), np.where(seen, tgt, np.nan), error_pct,
        correction, eligible, limited, n, int(keep.sum()), s)


def analyze_log(channels: Mapping[str, np.ndarray], editor: "TableEditor", *,
                x: str = "rpm", y: str = "map", lam: str | None = "lambda",
                afr: str | None = None, lambda_target=1.0, time: str | None = "time_s",
                stft: str | None = None, ltft: str | None = None, mask=None,
                settings: AutoTuneSettings | None = None) -> AutoTuneResult:
    """:func:`analyze` for a log held as ``{channel name: array}`` and a table
    editor (its axes and locked cells are used).

    Pick the error signal by naming channels: ``lam`` (lambda), ``afr``
    (converted with stoich 14.7), or ``stft`` (+ optional ``ltft``) for trims.
    """
    xa, ya = editor.axis("x"), editor.axis("y")
    if not xa.numeric or not ya.numeric:
        raise ValueError(f"{editor.title}: an axis has no numeric breakpoints")
    kwargs: dict = {"mask": mask, "locked": editor.locked, "settings": settings}
    if time and time in channels:
        kwargs["time"] = channels[time]
    if stft:
        kwargs["ratio"] = trim_ratio(channels[stft], channels[ltft] if ltft else 0.0)
    elif afr:
        kwargs["lambda_measured"] = afr_to_lambda(channels[afr])
        kwargs["lambda_target"] = lambda_target
    elif lam:
        kwargs["lambda_measured"] = channels[lam]
        kwargs["lambda_target"] = lambda_target
    else:
        raise ValueError("name a lambda, AFR or fuel trim channel")
    return analyze(channels[x], channels[y], xa.values, ya.values, **kwargs)
