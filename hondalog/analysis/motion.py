"""How the car moved: gear, longitudinal and lateral acceleration, the g-g
trail, a modelled road grade and the fuel flow. Pure functions on numpy
arrays; no Qt, no database.

The methods and constants are those of docs/analysis/2026-11-26
(vehicle-dynamics.md, drive-timeline.md):

- **Gear** from the coupling test: the engine is coupled to the wheels in
  gear N when rpm / vss is within ±5 % of that gear's ratio (133.6 / 76.8 /
  49.7 / 36.0 / 27.7 rpm per ECU km/h) at 8 km/h or more, held for 0.5 s.
  Anything else moving is the clutch slipping or the gearbox in neutral.
- **Speed**: true speed = 1.011 × vss.
- **Longitudinal acceleration** from vss, the derivative of a local
  quadratic fit over 1.5 s (what a Savitzky-Golay 15-point filter does at
  10 Hz), because vss only counts whole km/h.
- **Lateral acceleration** from the GPS: heading rate × speed. The GPS speed
  and heading arrive about 1.7 s after the matching ECU reading, so their
  times are moved 1.7 s earlier.
- **Grade** is modelled, never measured (no altitude is logged): sinθ =
  (k·F_engine − m_eff·a − F_road) / (m·g), only while the engine is coupled
  (or fully decoupled) with the brake off. Treat it as an estimate.
- **Fuel flow** from the injector pulse: 4 injectors × 240 cc/min × (pulse −
  1.0 ms dead time) × rpm / 120. Both injector numbers are unverified.

    car = Car()                         # 1,195 kg, 0.287 m tyre, η 0.90, road load 179 + 0.384 v²
    t = grid(t_rpm[0], t_rpm[-1])        # 10 Hz
    rpm, vss = resample(t_rpm, rpm, t), resample(t_vss, vss, t)
    g = gear(rpm, vss)                   # 0 = not coupled
    a = longitudinal_accel(vss, 0.1)     # m/s²
    tl, lat = lateral_accel(t_gps, heading, gps_kmh)    # m/s², times already moved by -1.7 s
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

G = 9.81
#: rpm per (ECU) km/h in each gear, measured (drive-timeline.md)
GEAR_RPM_PER_KMH = {1: 133.6, 2: 76.8, 3: 49.7, 4: 36.0, 5: 27.7}
#: true speed = VSS_SCALE × the ECU's vss
VSS_SCALE = 1.011
#: the GPS speed and heading come this much later than the ECU's reading
GPS_LAG_S = 1.7
GEAR_TOLERANCE = 0.05
GEAR_MIN_KMH = 8.0
GEAR_HOLD_S = 0.5
ACCEL_WINDOW_S = 1.5


@dataclass(frozen=True)
class Car:
    """The car and the assumptions behind every force (vehicle-dynamics.md §7).
    Change any of them with ``car.with_(mass_kg=1250)``."""

    mass_kg: float = 1195.0             # car 1,100 + driver 85 + kit 10 (assumed)
    tyre_m: float = 0.287               # 185/65R14 rolling radius (assumed)
    eta: float = 0.90                   # drivetrain efficiency (assumed)
    road_a: float = 179.0               # N, rolling + constant drag (fitted ±15)
    road_b: float = 0.384               # N per (m/s)², aero (fitted ±0.02)
    wheels_j: float = 2.6               # kg m², four wheels and tyres (assumed)
    engine_j: float = 0.13              # kg m², crank + flywheel + clutch (assumed)
    vss_scale: float = VSS_SCALE
    gears: dict = field(default_factory=lambda: dict(GEAR_RPM_PER_KMH))
    inj_cc_min: float = 240.0           # per injector (unverified)
    inj_dead_ms: float = 1.0            # (unverified)
    cylinders: int = 4
    fuel_g_cc: float = 0.745
    displacement_l: float = 1.590

    def with_(self, **changes) -> "Car":
        return replace(self, **changes)

    def wheel_rpm_per_kmh(self) -> float:
        """Wheel revolutions per minute per ECU km/h."""
        return (1000.0 / 60.0) / (2.0 * np.pi * self.tyre_m) / self.vss_scale

    def ratio(self, gear) -> np.ndarray | float:
        """Engine revolutions per wheel revolution in ``gear`` (NaN for 0)."""
        g = np.asarray(gear)
        table = np.array([np.nan] + [self.gears.get(k, np.nan) for k in range(1, 6)])
        out = table[np.clip(g.astype(int), 0, 5)] / self.wheel_rpm_per_kmh()
        return float(out) if out.ndim == 0 else out

    def m_eff(self, gear) -> np.ndarray | float:
        """Translational mass with the wheels' inertia and, in gear, the
        engine's reflected through the gear (kg)."""
        g = np.asarray(gear)
        m = self.mass_kg + self.wheels_j / self.tyre_m ** 2
        ratio = np.nan_to_num(np.asarray(self.ratio(g), dtype=float))
        out = m + np.where(g > 0, self.engine_j * ratio ** 2 / self.tyre_m ** 2, 0.0)
        return float(out) if np.ndim(out) == 0 else out

    def road_load(self, v_ms) -> np.ndarray | float:
        """Rolling + aero drag at true speed ``v_ms`` (N)."""
        v = np.asarray(v_ms, dtype=float)
        out = self.road_a * (v > 0.3) + self.road_b * v * v
        return float(out) if out.ndim == 0 else out


# ---- time grid -------------------------------------------------------------

def grid(t0: float, t1: float, hz: float = 10.0) -> np.ndarray:
    """Evenly spaced times from t0 to t1."""
    if not (np.isfinite(t0) and np.isfinite(t1)) or t1 <= t0:
        return np.zeros(0)
    return t0 + np.arange(int(np.floor((t1 - t0) * hz)) + 1) / hz


def resample(t, v, at, max_gap: float = 1.5) -> np.ndarray:
    """Linear interpolation of (t, v) at the times ``at``; NaN outside the
    samples and inside any gap between two samples longer than ``max_gap``."""
    t, v, at = (np.asarray(x, dtype=float) for x in (t, v, at))
    out = np.full(len(at), np.nan)
    ok = np.isfinite(t) & np.isfinite(v)
    t, v = t[ok], v[ok]
    if len(t) == 0 or len(at) == 0:
        return out
    if len(t) == 1:
        out[np.abs(at - t[0]) <= max_gap / 2] = v[0]
        return out
    out = np.interp(at, t, v, left=np.nan, right=np.nan)
    i = np.clip(np.searchsorted(t, at, side="right"), 1, len(t) - 1)
    out[(t[i] - t[i - 1]) > max_gap] = np.nan
    return out


def hold(t, v, at, max_age: float = 1.5) -> np.ndarray:
    """The last sample at or before each time of ``at`` (flags, slow bytes)."""
    t, v, at = (np.asarray(x, dtype=float) for x in (t, v, at))
    out = np.full(len(at), np.nan)
    if len(t) == 0 or len(at) == 0:
        return out
    i = np.searchsorted(t, at, side="right") - 1
    ok = i >= 0
    idx = np.clip(i, 0, len(t) - 1)
    ok &= (at - t[idx]) <= max_age
    out[ok] = v[idx[ok]]
    return out


def derivative(v, dt: float, window_s: float = ACCEL_WINDOW_S) -> np.ndarray:
    """d v / d t from a local quadratic (= linear, at the centre) fit over
    ``window_s``: Savitzky-Golay, deriv=1, without scipy. NaN in, NaN out
    (gaps are not bridged)."""
    v = np.asarray(v, dtype=float)
    half = max(1, int(round(window_s / dt / 2)))
    if len(v) < 2 * half + 1:
        return np.full(len(v), np.nan)
    k = np.arange(-half, half + 1, dtype=float)
    kernel = k / (np.sum(k * k) * dt)
    pad = np.concatenate([np.full(half, v[0]), v, np.full(half, v[-1])])
    out = np.convolve(pad, kernel[::-1], mode="valid")
    # where the window ran over the ends, fall back to one-sided differences
    out[:half] = np.nan
    out[-half:] = np.nan
    if len(v) > 1:
        edge = np.gradient(v, dt)
        out[:half], out[-half:] = edge[:half], edge[-half:]
    return out


def smooth(v, n: int) -> np.ndarray:
    """Centred moving average over n samples, ignoring NaN."""
    v = np.asarray(v, dtype=float)
    if n <= 1 or len(v) == 0:
        return v.copy()
    ok = np.isfinite(v)
    kernel = np.ones(n)
    s = np.convolve(np.where(ok, v, 0.0), kernel, mode="same")
    c = np.convolve(ok.astype(float), kernel, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = s / c
    out[c < max(1, n // 3)] = np.nan
    return out


# ---- gear ------------------------------------------------------------------

def speed_ms(vss, car: Car = Car()) -> np.ndarray:
    """True speed in m/s from the ECU's km/h."""
    return np.asarray(vss, dtype=float) * car.vss_scale / 3.6


def gear_raw(rpm, vss, car: Car = Car(), tol: float = GEAR_TOLERANCE,
             min_kmh: float = GEAR_MIN_KMH) -> np.ndarray:
    """Per sample: the gear whose rpm / vss ratio is within ``tol`` (widened
    by half a km/h of vss resolution: whole km/h at 5 km/h is ±10 %), 0 if none."""
    rpm, vss = np.asarray(rpm, dtype=float), np.asarray(vss, dtype=float)
    out = np.zeros(len(rpm), dtype=int)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = rpm / np.maximum(vss, 1e-3)
        moving = vss >= min_kmh
        for k, g in car.gears.items():
            out[moving & (np.abs(ratio / g - 1.0) < tol + 0.5 / np.maximum(vss, 1.0))] = k
    return out


def gear(rpm, vss, dt: float = 0.1, car: Car = Car(), hold_s: float = GEAR_HOLD_S,
         tol: float = GEAR_TOLERANCE, min_kmh: float = GEAR_MIN_KMH) -> np.ndarray:
    """The coupling test on an even grid: the gear, where the engine has
    been coupled in it for ``hold_s`` around the sample; 0 elsewhere
    (stopped, clutch slipping, neutral, changing gear)."""
    raw = gear_raw(rpm, vss, car, tol, min_kmh)
    half = max(0, int(round(hold_s / dt / 2)))
    if half == 0 or len(raw) == 0:
        return raw
    out = raw.copy()
    n = len(raw)
    pad = np.concatenate([np.full(half, -1), raw, np.full(half, -1)])
    for s in range(-half, half + 1):
        if s:
            out[pad[half + s: half + s + n] != raw] = 0
    return out


def decoupled(rpm, vss, car: Car = Car(), min_kmh: float = 15.0) -> np.ndarray:
    """Moving with the engine turning well below even top gear's line:
    clutch in or neutral (no engine force at the wheels)."""
    rpm, vss = np.asarray(rpm, dtype=float), np.asarray(vss, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = rpm / np.maximum(vss, 1e-3)
    return (vss >= min_kmh) & (ratio < 0.85 * min(car.gears.values())) & np.isfinite(rpm)


def gear_name(g) -> str:
    g = int(g) if g is not None and np.isfinite(g) else 0
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(g, f"{g}th") if g > 0 else "—"


# ---- accelerations ---------------------------------------------------------

def longitudinal_accel(vss, dt: float = 0.1, car: Car = Car(),
                       window_s: float = ACCEL_WINDOW_S) -> np.ndarray:
    """m/s² from the ECU's vss on an even grid (gaps stay NaN)."""
    return derivative(speed_ms(vss, car), dt, window_s)


def accel_from_rpm(rpm, g, dt: float = 0.1, car: Car = Car(), window_s: float = 1.0) -> np.ndarray:
    """m/s² from rpm while coupled in gear ``g`` (finer than vss: rpm reads
    0.25 rpm steps). NaN where not in gear."""
    rpm = np.asarray(rpm, dtype=float)
    g = np.asarray(g, dtype=int)
    per = np.array([np.nan] + [car.gears.get(k, np.nan) for k in range(1, 6)])[np.clip(g, 0, 5)]
    v = rpm / per * car.vss_scale / 3.6
    a = derivative(v, dt, window_s)
    a[g == 0] = np.nan
    return a


def heading_rate(t, heading_deg, window_s: float = 2.0) -> np.ndarray:
    """deg/s from a compass heading (unwrapped across 0/360), irregular times."""
    t = np.asarray(t, dtype=float)
    h = np.asarray(heading_deg, dtype=float)
    out = np.full(len(t), np.nan)
    ok = np.isfinite(t) & np.isfinite(h)
    if ok.sum() < 3:
        return out
    tt = t[ok]
    hh = np.degrees(np.unwrap(np.radians(h[ok])))
    rate = np.full(len(tt), np.nan)
    half = window_s / 2
    lo = np.searchsorted(tt, tt - half, side="left")
    hi = np.searchsorted(tt, tt + half, side="right") - 1
    span = tt[hi] - tt[lo]
    good = span > 0.3
    rate[good] = (hh[hi[good]] - hh[lo[good]]) / span[good]
    out[np.flatnonzero(ok)] = rate
    return out


def lateral_accel(t_gps, heading_deg, speed_kmh, lag_s: float = GPS_LAG_S,
                  window_s: float = 2.0, min_kmh: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """(times moved ``lag_s`` earlier, m/s²) from the GPS heading rate × GPS
    speed. Positive = turning right (clockwise heading). NaN below
    ``min_kmh``, where the heading is noise."""
    t = np.asarray(t_gps, dtype=float)
    v = np.asarray(speed_kmh, dtype=float) / 3.6
    rate = np.radians(heading_rate(t, heading_deg, window_s))
    a = rate * v
    a[~(np.asarray(speed_kmh, dtype=float) >= min_kmh)] = np.nan
    return t - lag_s, a


def gg_trail(t, lat_g, long_g, now: float, trail_s: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """(lateral g, longitudinal g) of the last ``trail_s`` up to ``now``,
    oldest first, finite points only (for the g-g plot)."""
    t = np.asarray(t, dtype=float)
    i0, i1 = np.searchsorted(t, now - trail_s, side="left"), np.searchsorted(t, now, side="right")
    x = np.asarray(lat_g, dtype=float)[i0:i1]
    y = np.asarray(long_g, dtype=float)[i0:i1]
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


# ---- grade (modelled) ------------------------------------------------------

def grade_estimate(accel, v_ms, g, engine_force_n, brake=None, car: Car = Car(), k: float = 0.91,
                   dt: float = 0.1, smooth_s: float = 5.0, clip_pct: float = 10.0) -> np.ndarray:
    """Road grade in % (positive uphill), **modelled**: whatever the engine
    model's force (scaled by the route fit's k = 0.91) does not explain of
    the acceleration and the road load. Only while coupled in gear or fully
    decoupled with the brake off; elsewhere NaN. Smoothed over ``smooth_s``
    because a single reading is mostly driveline shuffle."""
    a = np.asarray(accel, dtype=float)
    v = np.asarray(v_ms, dtype=float)
    g = np.asarray(g, dtype=int)
    f_engine = np.nan_to_num(np.asarray(engine_force_n, dtype=float)) * k
    m_eff = np.asarray(car.m_eff(g), dtype=float)
    with np.errstate(invalid="ignore"):
        sin = (f_engine - m_eff * a - car.road_load(v)) / (car.mass_kg * G)
    valid = np.isfinite(a) & np.isfinite(v) & (v > 2.0)
    if brake is not None:
        valid &= ~(np.nan_to_num(np.asarray(brake, dtype=float)) > 0.5)
    sin[~valid] = np.nan
    pct = np.clip(100.0 * np.tan(np.arcsin(np.clip(sin, -0.3, 0.3))), -clip_pct, clip_pct)
    return smooth(pct, max(1, int(round(smooth_s / dt))))


# ---- fuel ------------------------------------------------------------------

def fuel_flow(inj_ms, rpm, car: Car = Car()) -> tuple[np.ndarray, np.ndarray]:
    """(g/s, L/h) from the injector pulse: every injector opens once per
    cycle (two revolutions) for pulse − dead time. 0 in fuel cut."""
    inj = np.asarray(inj_ms, dtype=float)
    rpm = np.asarray(rpm, dtype=float)
    open_ms = np.clip(inj - car.inj_dead_ms, 0.0, None)
    open_ms = np.where(inj <= 0.05, 0.0, open_ms)
    cc_s = car.cylinders * (car.inj_cc_min / 60000.0) * open_ms * rpm / 120.0
    return cc_s * car.fuel_g_cc, cc_s * 3.6
