"""A plausible drive for the simulators and the test fixtures: a loop of
streets in Pearl, Mississippi, driven with stops, corners and speed limits.

The loop is drawn from street-like legs (east along an Old Brandon Road-like
street, south past a rough stretch with a railroad crossing, west along a
Government Street / US 80-like road, north back to the start). The corners
are only roughly where the real streets are: it is a test track, not
navigation data.

    driver = Driver(PEARL_LOOP, seed=3)
    for _ in range(600):
        fix = driver.step(1.0)          # lat, lon, speed_kmh, heading
    t, lat, lon, kmh, heading = drive(PEARL_LOOP, 3600, hz=10)

No Qt in here.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from . import proj

#: the corner the offsets below are measured from (south-west Pearl)
ORIGIN = (32.2690, -90.1335)


@dataclass(frozen=True)
class Leg:
    east_m: float               # where this leg ends, metres from ORIGIN
    north_m: float
    limit_kmh: float            # speed limit on the way there
    stop: bool = False          # a stop sign or a red light at its end
    name: str = ""


@dataclass
class Route:
    name: str
    legs: tuple[Leg, ...]
    origin: tuple[float, float] = ORIGIN
    #: named stretches, (start m, end m) along the loop
    stretches: dict[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lat0, lon0 = self.origin
        pts = [proj.offset(lat0, lon0, leg.east_m, leg.north_m) for leg in self.legs]
        self.lat = np.array([p[0] for p in pts] + [pts[0][0]])
        self.lon = np.array([p[1] for p in pts] + [pts[0][1]])
        seg = proj.haversine_m(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:])
        self.cum = np.concatenate(([0.0], np.cumsum(seg)))
        self.length_m = float(self.cum[-1])
        self.heading = proj.bearing_deg(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:])
        # leg k runs from vertex k to vertex k+1 with the limit of leg k+1 (the leg that ends there)
        n = len(self.legs)
        self.limits = np.array([self.legs[(k + 1) % n].limit_kmh for k in range(n)]) / 3.6
        self.stops = np.array([self.cum[(k + 1)] for k in range(n) if self.legs[(k + 1) % n].stop])
        turn = np.abs((np.diff(np.append(self.heading, self.heading[0])) + 180.0) % 360.0 - 180.0)
        # a 90 degree corner is taken at about 22 km/h, a gentle bend barely slows
        self.corner_v = np.where(turn > 25.0, (22.0 + 40.0 * np.clip((90.0 - turn) / 65.0, 0, 1)) / 3.6, np.inf)

    def at(self, s: float) -> tuple[float, float, float, float]:
        """(lat, lon, heading, speed limit m/s) at ``s`` metres along the loop."""
        s = s % self.length_m
        k = int(np.searchsorted(self.cum, s, side="right")) - 1
        k = min(max(k, 0), len(self.cum) - 2)
        f = (s - self.cum[k]) / max(1e-9, self.cum[k + 1] - self.cum[k])
        return (float(self.lat[k] + f * (self.lat[k + 1] - self.lat[k])),
                float(self.lon[k] + f * (self.lon[k + 1] - self.lon[k])),
                float(self.heading[k]), float(self.limits[k]))

    def allowed(self, s: float, brake: float) -> float:
        """The fastest the car may go at ``s`` and still make the corners ahead."""
        s = s % self.length_m
        _, _, _, limit = self.at(s)
        v = limit
        for k in range(1, len(self.cum) - 1):
            ahead = (self.cum[k] - s) % self.length_m
            if ahead < 300.0 and np.isfinite(self.corner_v[k - 1]):
                v = min(v, math.sqrt(self.corner_v[k - 1] ** 2 + 2.0 * brake * ahead))
        return v

    def point(self, name: str, frac: float = 0.5) -> tuple[float, float]:
        """(lat, lon) a fraction of the way along a named stretch."""
        a, b = self.stretches[name]
        lat, lon, _, _ = self.at(a + (b - a) * frac)
        return lat, lon


# Pearl, MS. East along the north street, a light at the far end; south on
# the east road past the rough stretch (patched asphalt and a railroad
# crossing); west along the US 80-like road with two lights; north home.
PEARL_LOOP = Route("Pearl, MS loop", (
    Leg(0.0, 0.0, 48.0, True, "start"),
    Leg(420.0, 1180.0, 48.0, True, "up to the north street"),
    Leg(1200.0, 1260.0, 56.0, False, "Old Brandon Rd-like street"),
    Leg(2150.0, 1320.0, 56.0, False),
    Leg(3050.0, 1280.0, 56.0, True, "light at the east road"),
    Leg(3130.0, 600.0, 72.0, False, "east road, south"),
    Leg(3170.0, -150.0, 64.0, False, "rough stretch and railroad crossing"),
    Leg(3210.0, -900.0, 72.0, True, "light at US 80"),
    Leg(2100.0, -960.0, 72.0, False, "US 80-like road, west"),
    Leg(1050.0, -1000.0, 72.0, True, "light"),
    Leg(150.0, -1020.0, 64.0, False),
    Leg(-80.0, -520.0, 56.0, True, "stop sign, north"),
))
_s_rough = float(PEARL_LOOP.cum[5]) + 250.0
PEARL_LOOP.stretches.update({
    "rough stretch": (_s_rough, _s_rough + 520.0),
    "north street": (float(PEARL_LOOP.cum[2]), float(PEARL_LOOP.cum[4])),
    "us 80": (float(PEARL_LOOP.cum[8]), float(PEARL_LOOP.cum[10])),
})


class Driver:
    """Drives a route: accelerates to the limit, brakes for corners and
    stops, waits at lights. ``step(dt, running)``: with ``running`` False
    (the engine has died) the car rolls to a halt where it is."""

    ACCEL = 1.6                 # m/s^2
    BRAKE = 2.2

    def __init__(self, route: Route = PEARL_LOOP, seed: int = 1, start_m: float = 0.0, noise_m: float = 1.5):
        self.route = route
        self.rng = random.Random(seed)
        self.s = start_m            # metres driven, can exceed one lap
        self.v = 0.0
        self.wait = 0.0
        self.noise_m = noise_m
        self.cruise = 0.92          # how close to the limit this driver goes
        self._next_stop = self._find_stop(self.s + 3.0)

    def _find_stop(self, s: float) -> float:
        stops = self.route.stops
        if not len(stops):
            return math.inf
        lap, pos = divmod(s, self.route.length_m)
        later = stops[stops >= pos]
        return lap * self.route.length_m + (float(later[0]) if len(later) else self.route.length_m + float(stops[0]))

    def step(self, dt: float, running: bool = True) -> tuple[float, float, float, float]:
        """Advance ``dt`` seconds. Returns (lat, lon, speed km/h, heading)."""
        rem = dt
        while rem > 1e-9:
            h = min(0.2, rem)
            rem -= h
            if not running:
                self.v = max(0.0, self.v - self.BRAKE * 1.3 * h)
            elif self.wait > 0:
                self.wait -= h
                self.v = 0.0
            else:
                if self.rng.random() < 0.002 * h:
                    self.cruise = self.rng.uniform(0.82, 1.02)      # traffic, the driver's mood
                target = self.route.allowed(self.s, self.BRAKE) * self.cruise
                to_stop = self._next_stop - self.s
                target = min(target, math.sqrt(2.0 * self.BRAKE * max(0.0, to_stop - 1.0)))
                if self.v < target:
                    self.v = min(target, self.v + self.ACCEL * h)
                else:
                    self.v = max(target, self.v - self.BRAKE * 1.5 * h)
                if to_stop < 2.0 and self.v < 0.6:
                    self.v = 0.0
                    self.wait = self.rng.uniform(4.0, 28.0)
                    self._next_stop = self._find_stop(self._next_stop + 3.0)
            self.s += self.v * h
        lat, lon, heading, _ = self.route.at(self.s)
        if self.noise_m:
            lat, lon = proj.offset(lat, lon, self.rng.gauss(0, self.noise_m), self.rng.gauss(0, self.noise_m))
        return lat, lon, self.v * 3.6, heading


def drive(route: Route = PEARL_LOOP, duration_s: float = 3600.0, hz: float = 1.0, seed: int = 1,
          start_m: float = 0.0, stops_at: dict[float, float] | None = None):
    """Arrays (t, lat, lon, speed km/h, heading) of ``duration_s`` of driving
    at ``hz`` fixes a second. ``stops_at``: {time: seconds} the engine is dead
    (a stall): the car rolls to a halt and waits."""
    driver = Driver(route, seed, start_m)
    n = int(duration_s * hz)
    t = np.arange(n) / hz
    out = np.zeros((n, 4))
    dead = sorted((stops_at or {}).items())
    for i in range(n):
        running = not any(a <= t[i] < a + d for a, d in dead)
        out[i] = driver.step(1.0 / hz if i else 0.0, running)
    return t, out[:, 0], out[:, 1], out[:, 2], out[:, 3]


def time_at(route: Route, target_s: float, seed: int = 1, hz: float = 1.0, start_m: float = 0.0,
            limit_s: float = 7200.0) -> float:
    """How long the same driver takes to reach ``target_s`` metres (for
    placing a stall on a given stretch of a fixture drive)."""
    driver = Driver(route, seed, start_m)
    t = 0.0
    while driver.s < target_s and t < limit_s:
        driver.step(1.0 / hz if t else 0.0)
        t += 1.0 / hz
    return t


__all__ = ["Driver", "Leg", "ORIGIN", "PEARL_LOOP", "Route", "drive", "time_at"]
