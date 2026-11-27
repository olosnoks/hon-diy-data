"""Everything Replay calculates from a drive, on one 10 Hz grid: gear,
longitudinal and lateral acceleration (and g), the modelled grade, fuel flow,
torque and power from the acceleration, and rpm minus the idle target.
``motion`` and ``power`` hold the methods; this module only runs them over a
drive's channels. No Qt.

    d = derive({"rpm": (t, v), "vss": (t, v), "gps_heading": (t, v), ...})
    d.at(812.4)["gear"]                 # 2
    d.series("lat_g")                   # (t, v): a lane Replay can draw
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from . import motion, power
from .motion import G, Car

#: derived channels a lane can show: id -> (label, units, decimals, lo, hi)
CHANNELS = {
    "lat_g": ("Lateral", "g", 2, -0.6, 0.6),
    "long_g": ("Accel", "g", 2, -0.6, 0.6),
    "gear": ("Gear", "", 0, 0, 5),
    "grade": ("Grade (est.)", "%", 1, -6, 6),
    "fuel_lph": ("Fuel flow", "L/h", 1, 0, 20),
    "torque": ("Torque (accel)", "N·m", 0, 0, 160),
    "power": ("Power (accel)", "hp", 0, 0, 130),
    "rpm_err": ("RPM − target", "rpm", 0, -500, 1500),
}


@dataclass
class Derived:
    t: np.ndarray
    values: dict = field(default_factory=dict)      # id -> array on t
    car: Car = field(default_factory=Car)

    def has(self, name: str) -> bool:
        v = self.values.get(name)
        return v is not None and bool(np.isfinite(v).any())

    def series(self, name: str) -> tuple[np.ndarray, np.ndarray] | None:
        v = self.values.get(name)
        return None if v is None else (self.t, v)

    def at(self, t: float) -> dict[str, float]:
        """Every derived value at time ``t`` (nearest grid point; NaN off the grid)."""
        out = {k: float("nan") for k in self.values}
        if not len(self.t) or not (self.t[0] - 0.1 <= t <= self.t[-1] + 0.1):
            return out
        i = int(np.clip(np.round((t - self.t[0]) * 10.0), 0, len(self.t) - 1))
        return {k: float(v[i]) for k, v in self.values.items()}


def _pair(channels: Mapping, name: str):
    pair = channels.get(name)
    if pair is None:
        return None
    t, v = (np.asarray(x, dtype=float) for x in pair)
    return (t, v) if len(t) else None


def derive(channels: Mapping, car: Car = Car(), hz: float = 10.0, gps_lag_s: float = motion.GPS_LAG_S) -> Derived:
    rpm = _pair(channels, "rpm")
    base = rpm or _pair(channels, "vss") or _pair(channels, "gps_speed")
    if base is None:
        return Derived(np.zeros(0), {}, car)
    t = motion.grid(float(np.nanmin(base[0])), float(np.nanmax(base[0])), hz)
    dt = 1.0 / hz
    n = len(t)
    nan = np.full(n, np.nan)

    def on_grid(name, how="lin", gap=1.5):
        p = _pair(channels, name)
        if p is None:
            return nan.copy()
        return motion.resample(p[0], p[1], t, gap) if how == "lin" else motion.hold(p[0], p[1], t, gap)

    r = on_grid("rpm")
    vss = on_grid("vss")
    tps = on_grid("tps")
    mp = on_grid("map")
    inj = on_grid("inj")
    iat = on_grid("iat", "hold", 5.0)
    brake = on_grid("brake", "hold", 2.0)
    target = on_grid("idle_target", "hold", 3.0)
    out: dict[str, np.ndarray] = {}
    g = motion.gear(r, vss, dt, car)
    out["gear"] = np.where(np.isfinite(vss), g, np.nan).astype(float)
    a = motion.longitudinal_accel(vss, dt, car)
    out["long_g"] = a / G
    v_ms = motion.speed_ms(vss, car)
    # lateral: GPS heading rate x speed, moved 1.7 s earlier
    hd, sp = _pair(channels, "gps_heading"), _pair(channels, "gps_speed")
    if hd is not None and sp is not None and len(hd[0]) == len(sp[0]):
        tl, lat = motion.lateral_accel(hd[0], hd[1], sp[1], gps_lag_s)
        ok = np.isfinite(lat)
        out["lat_g"] = motion.resample(tl[ok], lat[ok], t, 3.0) / G if ok.sum() >= 2 else nan.copy()
    elif hd is not None and sp is not None:
        spd = motion.resample(sp[0], sp[1], hd[0], 3.0)
        tl, lat = motion.lateral_accel(hd[0], hd[1], spd, gps_lag_s)
        ok = np.isfinite(lat)
        out["lat_g"] = motion.resample(tl[ok], lat[ok], t, 3.0) / G if ok.sum() >= 2 else nan.copy()
    else:
        out["lat_g"] = nan.copy()
    _, lph = motion.fuel_flow(inj, r, car)
    out["fuel_lph"] = lph
    # torque and power from the acceleration in gear (rpm-derived, 1.5 s), while pulling
    a_rpm = motion.accel_from_rpm(r, g, dt, car, window_s=1.5)
    tq = power.crank_torque(a_rpm, v_ms, g, car)
    tq = np.where(tq > 10.0, tq, np.nan)                # coasting and engine braking: no reading
    out["torque"] = tq
    out["power"] = power.hp(tq, r)
    # grade (modelled): engine model force, coupled in gear or decoupled
    dec = motion.decoupled(r, vss, car)
    g_for = np.where(dec, 0, g)
    fuel_on = ~((inj <= 0.05) & (r > 1000))
    force = power.engine_force(r, np.nan_to_num(mp, nan=30.0), g_for, iat, car, fuel_on)
    acc = np.where((g > 0) | dec, a, np.nan)
    out["grade"] = motion.grade_estimate(acc, v_ms, g_for, force, brake, car, dt=dt)
    out["rpm_err"] = r - target
    return Derived(t, out, car)
