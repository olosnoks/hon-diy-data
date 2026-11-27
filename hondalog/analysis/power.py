"""Engine torque and power from how hard the car accelerated, the pulls they
come from, a reference curve and an airflow cross-check. Pure functions; no
Qt, no database. The Power screen (F5) and Replay's readouts use them.

Method (vehicle-dynamics.md §1.3): in a gear, with the engine coupled,

    crank torque = [m_eff·a + F_road(v) + m·g·sinθ] · r / (overall ratio · η)

with m = 1,195 kg, r = 0.287 m, η = 0.90, F_road = 179 + 0.384·v² N, m_eff
adding the wheels' and the engine's inertia, and the grade θ taken as 0 unless
given. Every one of those is an assumption (``motion.Car``, editable); the
uncertainty band moves grade ±2 %, mass ±50 kg and η over 0.86-0.92 and adds
them in quadrature (about ±12 % on the one WOT pull of 2026-11-26).

A **pull** is a stretch in one gear with the throttle at or above 50 %
(``min_tps``) and the rpm rising, for at least 1.5 s (``min_s``); it is "full
throttle" (``Pull.wot``) when the throttle was at or above 80 % for 1.0 s of it,
else "part throttle". The run ends where the throttle lifts or the rpm stops
rising, and the acceleration is a local fit that only uses samples inside the
run, so nothing from after the lift leaks in and the top of the pull is kept.
1st gear counts from 5 km/h when the coupling test says the clutch is in.

The **reference curve** is the D16Y8's published rating (127 hp at 6,600 rpm,
145 N·m at 5,500 rpm) with a shape drawn through those two points: it is
**approximate** between and around them, and says nothing about this engine.

The **airflow cross-check** is speed-density with an assumed VE curve and the
rule of thumb of 1.25-1.35 crank hp per g/s of air: a second, independent-ish
estimate of the power at the top of a pull.

    pulls = find_pulls(t, rpm, vss, tps, map_kpa)
    best = max(pulls, key=lambda p: p.peak_power_hp)
    best.curve                      # [(rpm bin centre, torque, hp, lo, hi), ...] per 250 rpm
    reference_torque(5500)          # 145.0 (approximate)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .motion import (G, Car, accel_from_rpm, gear, gear_name, grid, longitudinal_accel, resample,
                     speed_ms, smooth)

HP_W = 745.7
MIN_TPS = 50.0                  # listed from here (the web app uses the same rule)
MIN_PULL_S = 1.5
WOT_TPS = 80.0                  # "full throttle" when at least this ...
WOT_S = 1.0                     # ... for at least this long within the pull
MIN_KMH = 5.0                   # 1st-gear launches count once the coupling test holds
DROP_TAIL_S = 0.0               # the run already ends at the lift; nothing more is cut
ACCEL_WINDOW_S = 1.5
GRADE_UNCERTAINTY_PCT = 2.0
MASS_UNCERTAINTY_KG = 50.0
ETA_RANGE = (0.86, 0.92)


def hp(torque_nm, rpm):
    """Mechanical horsepower from N·m and rpm."""
    return np.asarray(torque_nm, dtype=float) * np.asarray(rpm, dtype=float) * 2 * np.pi / 60.0 / HP_W


def torque_of_hp(power_hp, rpm):
    return np.asarray(power_hp, dtype=float) * HP_W * 60.0 / (2 * np.pi * np.asarray(rpm, dtype=float))


# ---- the reference: D16Y8 rating (approximate shape) -----------------------

@dataclass(frozen=True)
class ReferenceCurve:
    name: str
    rpm: tuple
    torque_nm: tuple
    peak_torque: tuple          # (N·m, rpm), published
    peak_power: tuple           # (hp, rpm), published
    approximate: bool = True
    note: str = ""

    def torque(self, rpm):
        return np.interp(np.asarray(rpm, dtype=float), self.rpm, self.torque_nm, left=np.nan, right=np.nan)

    def power_hp(self, rpm):
        return hp(self.torque(rpm), rpm)


_T6600 = float(torque_of_hp(127.0, 6600.0))             # 137.0 N·m
D16Y8 = ReferenceCurve(
    name="D16Y8 rated (approximate)",
    rpm=(1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6600, 7200),
    torque_nm=(102, 110, 117, 122, 126, 129, 132, 134, 137, 145, 143, _T6600, 124),
    peak_torque=(145.0, 5500.0), peak_power=(127.0, 6600.0),
    note="published 127 hp at 6,600 and 145 N·m at 5,500; the shape between is drawn, not measured")


def reference_torque(rpm, curve: ReferenceCurve = D16Y8):
    return curve.torque(rpm)


def reference_power_hp(rpm, curve: ReferenceCurve = D16Y8):
    return curve.power_hp(rpm)


# ---- torque from acceleration ----------------------------------------------

def wheel_force(accel, v_ms, g, car: Car = Car(), grade_pct=0.0):
    """Force the tyres put on the road (N): inertia + road load + climbing."""
    a = np.asarray(accel, dtype=float)
    v = np.asarray(v_ms, dtype=float)
    sin = np.sin(np.arctan(np.asarray(grade_pct, dtype=float) / 100.0))
    return np.asarray(car.m_eff(g), dtype=float) * a + car.road_load(v) + car.mass_kg * G * sin


def crank_torque(accel, v_ms, g, car: Car = Car(), grade_pct=0.0):
    """Engine torque (N·m) that the acceleration implies; NaN out of gear."""
    g = np.asarray(g, dtype=int)
    ratio = np.asarray(car.ratio(g), dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = wheel_force(accel, v_ms, g, car, grade_pct) * car.tyre_m / (ratio * car.eta)
    return np.where(g > 0, out, np.nan)


def torque_band(accel, v_ms, g, car: Car = Car(), grade_pct=0.0,
                d_grade: float = GRADE_UNCERTAINTY_PCT, d_mass: float = MASS_UNCERTAINTY_KG,
                eta_range: tuple = ETA_RANGE):
    """(lo, hi) torque: grade ± d_grade, mass ± d_mass, η over eta_range,
    each as a relative change, added in quadrature."""
    mid = crank_torque(accel, v_ms, g, car, grade_pct)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel_g = (crank_torque(accel, v_ms, g, car, np.asarray(grade_pct) + d_grade) - mid) / mid
        rel_m = (crank_torque(accel, v_ms, g, car.with_(mass_kg=car.mass_kg + d_mass), grade_pct) - mid) / mid
        rel_e = (eta_range[1] - eta_range[0]) / 2 / car.eta
    rel = np.sqrt(rel_g ** 2 + rel_m ** 2 + rel_e ** 2)
    return mid * (1 - rel), mid * (1 + rel)


def relative_uncertainty(car: Car = Car(), accel: float = 2.2, v_ms: float = 15.0, g: int = 2) -> float:
    lo, hi = torque_band(accel, v_ms, g, car)
    mid = float(crank_torque(accel, v_ms, g, car))
    return float((hi - lo) / 2 / mid)


# ---- pulls -----------------------------------------------------------------

@dataclass
class Pull:
    t0: float
    t1: float
    gear: int
    rpm0: float
    rpm1: float
    tps_mean: float
    tps_min: float
    kmh0: float
    kmh1: float
    t: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    rpm: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    accel: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    torque: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    power: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    lo: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    hi: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    map_kpa: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    curve: list = field(default_factory=list)       # (rpm, torque, hp, lo, hi, n) per bin
    wot_s: float = 0.0                              # seconds at or above WOT_TPS

    @property
    def key(self) -> str:
        """Stable id for selecting this pull (Replay's Open in Power -> show_pull)."""
        return f"pull@{self.t0:.1f}"

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def wot(self) -> bool:
        return self.wot_s >= WOT_S

    @property
    def peak_torque(self) -> tuple[float, float]:
        if not len(self.torque) or not np.isfinite(self.torque).any():
            return float("nan"), float("nan")
        i = int(np.nanargmax(self.torque))
        return float(self.torque[i]), float(self.rpm[i])

    @property
    def peak_power_hp(self) -> float:
        return float(np.nanmax(self.power)) if len(self.power) and np.isfinite(self.power).any() else float("nan")

    @property
    def label(self) -> str:
        throttle = "full throttle" if self.wot else f"part throttle {self.tps_mean:.0f} %"
        return f"{gear_name(self.gear)} gear · {self.rpm0:,.0f} → {self.rpm1:,.0f} rpm · {throttle}"

    def at(self, t: float) -> tuple[float, float, float] | None:
        """(rpm, torque, hp) at time t inside the pull."""
        if not len(self.t) or not self.t[0] <= t <= self.t[-1]:
            return None
        i = int(np.clip(np.searchsorted(self.t, t), 0, len(self.t) - 1))
        return float(self.rpm[i]), float(self.torque[i]), float(self.power[i])

    def ratio_to(self, curve: ReferenceCurve = D16Y8) -> float:
        """Mean of measured / reference torque over the pull (NaN outside the curve)."""
        ref = curve.torque(self.rpm)
        with np.errstate(invalid="ignore", divide="ignore"):
            r = self.torque / ref
        return float(np.nanmean(r)) if np.isfinite(r).any() else float("nan")


def pull_curve(rpm, torque, lo, hi, bin_rpm: float = 250.0) -> list:
    """Bin the samples of a pull by rpm: (centre, torque, hp, lo, hi, n)."""
    rpm, torque, lo, hi = (np.asarray(x, dtype=float) for x in (rpm, torque, lo, hi))
    ok = np.isfinite(rpm) & np.isfinite(torque)
    out = []
    if not ok.any():
        return out
    edges = np.arange(np.floor(rpm[ok].min() / bin_rpm) * bin_rpm, rpm[ok].max() + bin_rpm, bin_rpm)
    for a, b in zip(edges[:-1], edges[1:]):
        m = ok & (rpm >= a) & (rpm < b)
        if m.sum() >= 2:
            c = float((a + b) / 2)
            tq = float(np.mean(torque[m]))
            out.append((c, tq, float(hp(tq, c)), float(np.mean(lo[m])), float(np.mean(hi[m])), int(m.sum())))
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of every run of True."""
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _slope_inside(v, dt: float, window_s: float) -> np.ndarray:
    """d v / d t from a local linear fit whose window is clipped to the
    array: centred inside, one-sided at the ends (no data from outside)."""
    v = np.asarray(v, dtype=float)
    n = len(v)
    h = max(1, int(round(window_s / dt / 2)))
    out = np.full(n, np.nan)
    x = np.arange(n) * dt
    for i in range(n):
        a, b = max(0, i - h), min(n, i + h + 1)
        if b - a >= 3:
            xx, yy = x[a:b], v[a:b]
            ok = np.isfinite(yy)
            if ok.sum() >= 3:
                xm = xx[ok] - xx[ok].mean()
                out[i] = float(np.sum(xm * (yy[ok] - yy[ok].mean())) / np.sum(xm * xm))
    return out


def find_pulls(t, rpm, vss, tps, map_kpa=None, *, t_vss=None, t_tps=None, t_map=None,
               car: Car = Car(), min_tps: float = MIN_TPS, min_s: float = MIN_PULL_S,
               wot_tps: float = WOT_TPS, wot_s: float = WOT_S, min_kmh: float = MIN_KMH,
               hz: float = 10.0, drop_tail_s: float = DROP_TAIL_S, grade_pct: float = 0.0,
               bin_rpm: float = 250.0, window_s: float = ACCEL_WINDOW_S) -> list[Pull]:
    """Every pull in a drive. ``t`` is rpm's time base; the other channels
    may have their own (``t_vss`` etc.), they are resampled to a 10 Hz grid.
    Acceleration comes from rpm in the held gear (finer than vss)."""
    t = np.asarray(t, dtype=float)
    if len(t) < 3:
        return []
    tg = grid(float(np.nanmin(t)), float(np.nanmax(t)), hz)
    dt = 1.0 / hz
    r = resample(t, rpm, tg)
    v = resample(t if t_vss is None else t_vss, vss, tg)
    p = resample(t if t_tps is None else t_tps, tps, tg)
    mp = resample(t if t_map is None else t_map, map_kpa, tg) if map_kpa is not None else np.full(len(tg), np.nan)
    g = gear(r, v, dt, car, min_kmh=min_kmh)
    drpm = np.gradient(smooth(r, 3), dt)
    ok = (g > 0) & (p >= min_tps) & (drpm > 0) & np.isfinite(r)
    pulls = []
    for i0, i1 in _runs(ok):
        # one gear for the whole run
        for j0, j1 in _runs(np.r_[g[i0:i1] == g[i0]]):
            s, e = i0 + j0, i0 + j1
            e = max(s, e - int(round(drop_tail_s * hz)))
            if (e - s) * dt < min_s:
                continue
            gg = int(g[s])
            vv = r[s:e] / car.gears[gg] * car.vss_scale / 3.6
            acc = _slope_inside(vv, dt, window_s)
            tq = crank_torque(acc, vv, gg, car, grade_pct)
            lo, hi = torque_band(acc, vv, gg, car, grade_pct)
            pulls.append(Pull(
                t0=float(tg[s]), t1=float(tg[e - 1]), gear=gg, rpm0=float(r[s]), rpm1=float(r[e - 1]),
                tps_mean=float(np.nanmean(p[s:e])), tps_min=float(np.nanmin(p[s:e])),
                kmh0=float(v[s]), kmh1=float(v[e - 1]), t=tg[s:e], rpm=r[s:e], accel=acc,
                torque=tq, power=hp(tq, r[s:e]), lo=lo, hi=hi, map_kpa=mp[s:e],
                curve=pull_curve(r[s:e], tq, lo, hi, bin_rpm),
                wot_s=float(np.sum(p[s:e] >= wot_tps) * dt)))
    return pulls


def power_now(accel, vss_kmh, g, rpm, car: Car = Car(), grade_pct: float = 0.0) -> tuple[float, float]:
    """(torque N·m, hp) right now from the acceleration in gear; NaN when
    not coupled or not pulling (a negative torque is engine braking: NaN)."""
    if not g or not np.isfinite(accel) or not np.isfinite(vss_kmh) or not np.isfinite(rpm):
        return float("nan"), float("nan")
    tq = float(crank_torque(accel, speed_ms(vss_kmh, car), int(g), car, grade_pct))
    if not np.isfinite(tq) or tq <= 0:
        return float("nan"), float("nan")
    return tq, float(hp(tq, rpm))


# ---- engine model (assumed) and the airflow cross-check --------------------

R_AIR = 287.05
LHV = 43.0e6
STOICH = 14.7
ETA_IG = 0.42


def ve_assumed(rpm, map_kpa):
    """Assumed speed-density VE (MAP referenced), vehicle-dynamics.md §1.1."""
    r = np.asarray(rpm, dtype=float) / 1000.0
    ve_wot = np.interp(r, [0.7, 1.5, 2.5, 3.5, 4.6, 5.5, 6.2, 7.0], [0.62, 0.74, 0.83, 0.88, 0.90, 0.89, 0.86, 0.80])
    m = np.clip(np.asarray(map_kpa, dtype=float), 15, 101)
    return ve_wot * (0.80 + 0.20 * (m - 15) / 86)


def airflow_gs(rpm, map_kpa, iat_c=25.0, car: Car = Car()):
    """Air mass flow (g/s), speed-density with the assumed VE."""
    rpm = np.asarray(rpm, dtype=float)
    iat = np.where(np.isfinite(np.asarray(iat_c, dtype=float)), iat_c, 25.0)
    per_cycle = ve_assumed(rpm, map_kpa) * np.asarray(map_kpa, dtype=float) * 1e3 * car.displacement_l * 1e-3 \
        / (R_AIR * (iat + 273.15))
    return per_cycle * rpm / 120.0 * 1e3


def airflow_hp(air_gs) -> tuple:
    """(lo, hi) crank hp the airflow supports: 1.25-1.35 hp per g/s."""
    a = np.asarray(air_gs, dtype=float)
    return a * 1.25, a * 1.35


def model_torque(rpm, map_kpa, iat_c=25.0, car: Car = Car(), baro: float = 100.25, fuel_on=True):
    """Willans-line brake torque (N·m) of the engine model, calibrated on a
    rating, not on this engine: used only for the modelled grade."""
    rpm = np.asarray(rpm, dtype=float)
    mp = np.asarray(map_kpa, dtype=float)
    vd = car.displacement_l * 1e-3
    iat = np.where(np.isfinite(np.asarray(iat_c, dtype=float)), iat_c, 25.0)
    m_air = ve_assumed(rpm, mp) * mp * 1e3 * vd / (R_AIR * (iat + 273.15))
    fuel = m_air / STOICH * np.where(fuel_on, 1.0, 0.0)
    imep = ETA_IG * fuel * LHV / vd / 1e3
    n = rpm / 1000.0
    fmep = 60 + 12 * n + 2.6 * n ** 2
    flow = rpm / 6000 * mp / 100
    pmep = baro + 8 * flow ** 2 - mp
    return (imep - fmep - pmep) * 1e3 * vd / (4 * np.pi)


def engine_force(rpm, map_kpa, g, iat_c=25.0, car: Car = Car(), fuel_on=True):
    """The engine model's force at the tyres (N) in gear ``g``; 0 decoupled."""
    g = np.asarray(g, dtype=int)
    ratio = np.nan_to_num(np.asarray(car.ratio(g), dtype=float))
    return np.where(g > 0, model_torque(rpm, map_kpa, iat_c, car, fuel_on=fuel_on) * ratio * car.eta / car.tyre_m, 0.0)


@dataclass
class AirflowCheck:
    rpm: float
    air_gs: float
    hp_lo: float
    hp_hi: float
    measured_hp: float
    agrees: bool


def airflow_check(pull: Pull, iat_c: float = 25.0, car: Car = Car()) -> AirflowCheck | None:
    """At the top of a pull: the hp its airflow supports against the hp the
    acceleration measured. They agree when the measured hp is within the
    airflow band ± the pull's own uncertainty."""
    if not len(pull.rpm) or not np.isfinite(pull.map_kpa).any():
        return None
    i = int(np.nanargmax(np.where(np.isfinite(pull.map_kpa), pull.rpm, np.nan)))
    air = float(airflow_gs(pull.rpm[i], pull.map_kpa[i], iat_c, car))
    lo, hi = airflow_hp(air)
    meas = float(pull.power[i])
    rel = float((pull.hi[i] - pull.lo[i]) / 2 / pull.torque[i]) if pull.torque[i] else 0.12
    agrees = bool(lo * (1 - rel) <= meas <= hi * (1 + rel))
    return AirflowCheck(float(pull.rpm[i]), air, float(lo), float(hi), meas, agrees)
