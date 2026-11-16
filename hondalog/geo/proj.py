"""Web Mercator, the projection of every slippy-map tile server, and distances.

Positions are kept as "unit" coordinates: x and y in 0..1 over the whole
world (x east from 180° W, y south from 85.05° N). Pixel coordinates at zoom
``z`` are unit coordinates times ``TILE * 2**z``; tile (x, y) at zoom ``z``
covers unit [x, x+1) / 2**z by [y, y+1) / 2**z.

No Qt in here.
"""

from __future__ import annotations

import math

import numpy as np

TILE = 256
MAX_LAT = 85.0511287798066
EARTH_R = 6378137.0                 # WGS 84 equatorial radius: what Web Mercator uses
MEAN_R = 6371008.8                  # for great-circle distances
MIN_ZOOM, MAX_ZOOM = 2, 19


def to_unit(lon, lat):
    """(lon, lat) in degrees -> (x, y) in 0..1. Scalars or arrays."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.clip(np.asarray(lat, dtype=np.float64), -MAX_LAT, MAX_LAT)
    x = (lon + 180.0) / 360.0
    s = np.sin(np.radians(lat))
    y = 0.5 - np.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)
    return x, y


def from_unit(x, y):
    """(x, y) in 0..1 -> (lon, lat) in degrees."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    lon = x * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * y))))
    return lon, lat


def world_px(zoom: float) -> float:
    """Width of the whole world in pixels at ``zoom``."""
    return TILE * 2.0 ** zoom


def metres_per_px(lat: float, zoom: float) -> float:
    """Ground metres one screen pixel covers at latitude ``lat``."""
    return math.cos(math.radians(lat)) * 2.0 * math.pi * EARTH_R / world_px(zoom)


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres. Scalars or arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2.0 * MEAN_R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial course from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return np.degrees(np.arctan2(y, x)) % 360.0


def offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """The point ``east_m`` / ``north_m`` metres away (flat-earth, fine for a town)."""
    dlat = north_m / MEAN_R
    dlon = east_m / (MEAN_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


_STEPS = (1, 2, 5)


def scale_bar(m_per_px: float, max_px: float, miles: bool = False) -> tuple[float, str]:
    """The longest round length that fits in ``max_px``: (pixels, label).

    Metric: 1, 2, 5 x 10^n metres (km from 1 km). Imperial: feet below a
    quarter mile (1, 2, 5 x 10^n ft), then 0.25, 0.5, 1, 2, 5... miles."""
    if m_per_px <= 0 or max_px <= 0:
        return 0.0, ""
    limit = m_per_px * max_px
    if miles:
        ft = 0.3048
        mile = 1609.344
        best = (0.0, "")
        for value in (0.25, 0.5) + tuple(k * 10 ** e for e in range(0, 4) for k in _STEPS):
            if value * mile <= limit:
                best = (value * mile / m_per_px, f"{value:g} mi")
        if best[1]:
            return best
        for e in range(0, 5):
            for k in _STEPS:
                value = k * 10 ** e
                if value * ft <= limit:
                    best = (value * ft / m_per_px, f"{value:g} ft")
        return best
    best = (0.0, "")
    for e in range(0, 7):
        for k in _STEPS:
            value = k * 10 ** e
            if value <= limit:
                best = (value / m_per_px, f"{value / 1000:g} km" if value >= 1000 else f"{value:g} m")
    return best


def fit_zoom(x0: float, y0: float, x1: float, y1: float, width_px: float, height_px: float,
             margin_px: float = 40.0, lo: int = MIN_ZOOM, hi: int = MAX_ZOOM) -> int:
    """The largest whole zoom at which the unit box fits the viewport."""
    w = max(x1 - x0, 1e-12)
    h = max(y1 - y0, 1e-12)
    room_w = max(1.0, width_px - 2 * margin_px)
    room_h = max(1.0, height_px - 2 * margin_px)
    z = math.floor(math.log2(min(room_w / (w * TILE), room_h / (h * TILE))))
    return int(min(hi, max(lo, z)))


__all__ = ["EARTH_R", "MAX_LAT", "MAX_ZOOM", "MIN_ZOOM", "TILE", "bearing_deg", "fit_zoom", "from_unit",
           "haversine_m", "metres_per_px", "offset", "scale_bar", "to_unit", "world_px"]
