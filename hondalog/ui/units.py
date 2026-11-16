"""Display units for the UI: one ``U`` object, like the theme's ``T``.

Everything behind the UI stays in the address map's units: the bus, the
recorder, the database, the exports. Only what is drawn is converted, here,
with ``hondalog.units.UnitPrefs``:

    U.spec(spec)            the channel as the screen shows it: a ChannelSpec
                            with the display unit, range, limits and decimals
    U.value(spec, v)        a bus value in the display unit
    U.text(spec, v)         ... formatted (no unit)
    U.with_unit(spec, v)    "190 °F"
    U.limit(spec, v)        a limit, "226 °F"
    U.convert_text(text)    free text with numbers and units in it (an alarm
                            rule's own message): "Coolant 110 °C" -> "Coolant 230 °F"

``U.prefs is None`` means "as the address map has them": nothing is
converted. That is the state before the app loads its settings, so library
code and tests that never start the shell see base units.

The owner's default is ``US``: mph, °F, gal/h, miles, and AFR. Pressure
stays in kPa there, because MAP in kPa is what every Honda table, definition
and tuning guide uses; psi, inHg or bar can be picked for the MAP and baro
readouts. The axes of the Map 3D and Tune screens are the tables' own
breakpoints and are always kPa. Volts, %, ms and rpm have nothing to convert.

No Qt in here.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Callable

from ..units import UnitPrefs, canonical, choices, factors, quantity
from .bus import ChannelSpec

#: the two presets the menu offers. Both keep AFR (the map's unit for the
#: mixture channel) and kPa.
METRIC = dict(temperature="°C", pressure="kPa", speed="km/h", mixture="AFR", volume_flow="L/h",
              distance="km")
US = dict(temperature="°F", pressure="kPa", speed="mph", mixture="AFR", volume_flow="gal/h",
          distance="mi")
PRESETS = {"US": US, "Metric": METRIC}
DEFAULT_PRESET = "US"

#: (settings key and UnitPrefs field, menu title, a unit of that quantity to list the choices of)
QUANTITIES = (("speed", "Speed", "km/h"), ("temperature", "Temperature", "°C"),
              ("pressure", "Pressure (MAP, baro)", "kPa"), ("mixture", "Mixture", "AFR"),
              ("volume_flow", "Fuel flow", "L/h"), ("distance", "Distance", "km"))

_LIMIT_KEYS = ("warn_lo", "warn_hi", "alarm_lo", "alarm_hi")
# a number followed by a unit this module converts, as alarm messages write them
_IN_TEXT = re.compile(r"(?<![\w.])([-+−]?\d+(?:\.\d+)?)(\s*)(°C|°F|km/h|mph|kPa|psi|inHg|L/h|gal/h)(/s)?(?![\w/])")


class DisplayUnits:
    def __init__(self) -> None:
        self.prefs: UnitPrefs | None = None
        self.revision = 0                   # bumps when the units change: widgets redo their texts
        self._cache: dict[int, tuple[ChannelSpec, ChannelSpec, float, float]] = {}
        self._listeners: list[Callable[[], None]] = []

    # ---- choosing ----

    def set(self, prefs: UnitPrefs | None) -> None:
        if prefs != self.prefs:
            self.prefs = prefs
            self._cache.clear()
            self.revision += 1
            for listener in list(self._listeners):
                listener()

    def on_change(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def set_unit(self, field: str, unit: str) -> None:
        current = self.as_dict()
        current[field] = unit
        self.set(UnitPrefs(**current))

    def preset(self, name: str) -> None:
        self.set(UnitPrefs(**PRESETS[name]))

    def preset_name(self) -> str:
        """"US", "Metric" or "" for a mix of the two."""
        current = self.as_dict()
        return next((name for name, units in PRESETS.items() if units == current), "")

    def as_dict(self) -> dict[str, str]:
        prefs = self.prefs
        return {field: getattr(prefs, field) if prefs is not None else METRIC[field]
                for field, _, _ in QUANTITIES}

    def load(self, settings: Any) -> None:
        """From QSettings (``units/<quantity>``). Unset or unreadable values
        take the default preset: US."""
        units = dict(PRESETS[DEFAULT_PRESET])
        for field, _, base in QUANTITIES:
            stored = settings.value(f"units/{field}", "")
            name = canonical(str(stored)) if stored else None
            if name is not None and quantity(name) == quantity(base):
                units[field] = name
        self.set(UnitPrefs(**units))

    def save(self, settings: Any) -> None:
        for field, unit in self.as_dict().items():
            settings.setValue(f"units/{field}", unit)

    # ---- converting ----

    def unit(self, base_unit: str) -> str:
        """The unit shown for a channel in ``base_unit``."""
        return self.prefs.display_unit(base_unit) if self.prefs is not None else base_unit

    def _entry(self, spec: ChannelSpec) -> tuple[ChannelSpec, ChannelSpec, float, float]:
        hit = self._cache.get(id(spec))
        if hit is not None and hit[0] is spec:
            return hit
        if len(self._cache) > 4000:         # specs are replaced when the map is reloaded
            self._cache.clear()
        shown, scale, offset = spec, 1.0, 0.0
        target = self.unit(spec.units)
        if target != spec.units and not spec.is_flag and canonical(target) != canonical(spec.units):
            scale, offset = factors(spec.units, target)
            # keep about the same resolution: psi needs a decimal more than kPa
            extra = max(-2, min(2, round(-math.log10(abs(scale))))) if scale else 0
            limits = {key: getattr(spec, key) * scale + offset for key in _LIMIT_KEYS
                      if getattr(spec, key) is not None}
            shown = replace(spec, units=target, decimals=max(0, spec.decimals + extra),
                            lo=spec.lo * scale + offset, hi=spec.hi * scale + offset, **limits)
        hit = self._cache[id(spec)] = (spec, shown, scale, offset)
        return hit

    def spec(self, spec: ChannelSpec) -> ChannelSpec:
        """The channel as shown. Its ``format`` takes display values (``U.value``)."""
        return self._entry(spec)[1]

    def scale(self, spec: ChannelSpec) -> tuple[float, float]:
        """(scale, offset): display = bus value * scale + offset."""
        entry = self._entry(spec)
        return entry[2], entry[3]

    def value(self, spec: ChannelSpec, value: float | None) -> float | None:
        if value is None:
            return None
        _, _, scale, offset = self._entry(spec)
        return value * scale + offset

    def text(self, spec: ChannelSpec, value: float | None) -> str:
        _, shown, scale, offset = self._entry(spec)
        return shown.format(None if value is None else value * scale + offset)

    def with_unit(self, spec: ChannelSpec, value: float | None) -> str:
        _, shown, scale, offset = self._entry(spec)
        return f"{shown.format(None if value is None else value * scale + offset)} {shown.units}".strip()

    def limit(self, spec: ChannelSpec, value: float) -> str:
        """A limit of the channel with its unit: "226 °F", "12.8 V"."""
        _, shown, scale, offset = self._entry(spec)
        number = round(value * scale + offset, max(shown.decimals, 1) if scale != 1.0 else 6)
        return f"{number:g} {shown.units}".strip().replace("-", "−")

    def convert_text(self, text: str) -> str:
        """Numbers with a convertible unit inside free text, converted. The
        number keeps its count of decimals; "x/s" rates convert as spans."""
        if self.prefs is None or not text:
            return text

        def swap(match: re.Match) -> str:
            number, gap, unit, per_s = match.group(1), match.group(2), match.group(3), match.group(4) or ""
            target = self.unit(unit)
            if canonical(target) == canonical(unit):
                return match.group(0)
            value = float(number.replace("−", "-"))
            scale, offset = factors(unit, target)
            value = value * scale + (0.0 if per_s else offset)      # a rate of change: scale only
            decimals = len(number.split(".")[1]) if "." in number else 0
            shown = f"{value:.{decimals}f}"
            if shown.startswith("-") and "−" in number:
                shown = "−" + shown[1:]
            return f"{shown}{gap}{target}{per_s}"
        return _IN_TEXT.sub(swap, text)

    def choices(self, base_unit: str) -> list[str]:
        return choices(base_unit)


U = DisplayUnits()
