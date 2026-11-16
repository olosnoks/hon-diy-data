"""Display units. Channels and logs keep their base unit; this converts for
the screen only (°C/°F, kPa/psi/inHg, km/h/mph, lambda/AFR ...).

    prefs = UnitPrefs(temperature="°F", pressure="psi")
    value, unit = prefs.convert(88.0, "°C")        # (190.4, "°F")
    prefs.format(88.0, "°C", decimals=0)           # "190 °F"
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Mapping

STOICH_PETROL = 14.7

# quantity -> {unit: (scale, offset)} with  display = base * scale + offset,
# where "base" is the first unit listed for the quantity.
_TABLE: dict[str, dict[str, tuple[float, float]]] = {
    "temperature": {"°C": (1.0, 0.0), "°F": (1.8, 32.0), "K": (1.0, 273.15)},
    "pressure": {"kPa": (1.0, 0.0), "psi": (0.1450377, 0.0), "inHg": (0.2952998, 0.0),
                 "bar": (0.01, 0.0), "mmHg": (7.500617, 0.0)},
    "speed": {"km/h": (1.0, 0.0), "mph": (0.6213712, 0.0)},
    "mixture": {"lambda": (1.0, 0.0), "AFR": (STOICH_PETROL, 0.0)},
    "volume_flow": {"L/h": (1.0, 0.0), "gal/h": (0.2641721, 0.0)},
    "distance": {"km": (1.0, 0.0), "mi": (0.6213712, 0.0)},
}
_ALIASES = {"c": "°C", "degc": "°C", "°c": "°C", "f": "°F", "degf": "°F", "°f": "°F",
            "kpa": "kPa", "psi": "psi", "inhg": "inHg", "bar": "bar", "mmhg": "mmHg",
            "km/h": "km/h", "kph": "km/h", "kmh": "km/h", "mph": "mph", "lambda": "lambda",
            "λ": "lambda", "afr": "AFR", "l/h": "L/h", "gal/h": "gal/h", "km": "km", "mi": "mi",
            "k": "K"}
_QUANTITY = {unit: q for q, units in _TABLE.items() for unit in units}


def canonical(unit: str) -> str | None:
    """The table's spelling of a unit, or None if it is not one we convert."""
    return _ALIASES.get(unit.strip().casefold().replace(" ", ""))


def quantity(unit: str) -> str | None:
    """'temperature', 'pressure' ... or None for units that are shown as-is."""
    name = canonical(unit)
    return _QUANTITY.get(name) if name else None


def choices(unit: str) -> list[str]:
    """Display units on offer for a base unit ([] when it cannot be converted)."""
    q = quantity(unit)
    return list(_TABLE[q]) if q else []


def convert(value: float | None, unit: str, to: str) -> float | None:
    """Convert between two units of the same quantity. Raises ValueError otherwise."""
    src, dst = canonical(unit), canonical(to)
    if src is None or dst is None or _QUANTITY[src] != _QUANTITY[dst]:
        raise ValueError(f"cannot convert {unit!r} to {to!r}")
    if value is None:
        return None
    table = _TABLE[_QUANTITY[src]]
    scale, offset = table[src]
    base = (value - offset) / scale
    scale, offset = table[dst]
    return base * scale + offset


def factors(unit: str, to: str) -> tuple[float, float]:
    """(scale, offset) with  value in ``to`` = value in ``unit`` * scale + offset."""
    src, dst = canonical(unit), canonical(to)
    if src is None or dst is None or _QUANTITY[src] != _QUANTITY[dst]:
        raise ValueError(f"cannot convert {unit!r} to {to!r}")
    table = _TABLE[_QUANTITY[src]]
    (s0, o0), (s1, o1) = table[src], table[dst]
    scale = s1 / s0
    return scale, o1 - o0 * scale


def convert_span(span: float, unit: str, to: str) -> float:
    """Convert a difference (5 °C wide = 9 °F wide): scale only, no offset."""
    src, dst = canonical(unit), canonical(to)
    if src is None or dst is None or _QUANTITY[src] != _QUANTITY[dst]:
        raise ValueError(f"cannot convert {unit!r} to {to!r}")
    table = _TABLE[_QUANTITY[src]]
    return span / table[src][0] * table[dst][0]


@dataclass
class UnitPrefs:
    """The user's display unit per quantity. Defaults are the map's base units."""
    temperature: str = "°C"
    pressure: str = "kPa"
    speed: str = "km/h"
    mixture: str = "lambda"
    volume_flow: str = "L/h"
    distance: str = "km"

    def __post_init__(self) -> None:
        for f in fields(self):
            unit = canonical(getattr(self, f.name))
            if unit is None or _QUANTITY[unit] != f.name:
                raise ValueError(f"{getattr(self, f.name)!r} is not a unit of {f.name}")
            setattr(self, f.name, unit)

    @classmethod
    def from_mapping(cls, table: Mapping[str, str]) -> "UnitPrefs":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in table.items() if k in known})

    @classmethod
    def us(cls) -> "UnitPrefs":
        return cls(temperature="°F", pressure="psi", speed="mph", mixture="AFR",
                   volume_flow="gal/h", distance="mi")

    def display_unit(self, base_unit: str) -> str:
        """The unit to show for a channel whose base unit is ``base_unit``."""
        q = quantity(base_unit)
        return getattr(self, q) if q else base_unit

    def convert(self, value: float | None, base_unit: str) -> tuple[float | None, str]:
        """(display value, display unit). Unknown units pass through unchanged."""
        q = quantity(base_unit)
        if q is None:
            return value, base_unit
        target = getattr(self, q)
        return convert(value, base_unit, target), target

    def format(self, value: float | None, base_unit: str, decimals: int = 0) -> str:
        shown, unit = self.convert(value, base_unit)
        if shown is None or shown != shown:
            return "--"
        return f"{shown:.{decimals}f} {unit}".rstrip()
