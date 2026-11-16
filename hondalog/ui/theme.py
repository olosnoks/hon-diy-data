"""Design tokens and the runtime theme (docs/design/HANDOFF.md section 2).

Two themes, same layout: Daylight (black on white, default) and Night (white
on true black). Widgets never hold colours of their own; they paint from the
single ``T`` object and repaint when ``T.changed`` fires.

The token tables and ``contrast`` are plain Python so the tests can check
them without a QApplication.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPen

from ..ramps import DIV_RAMP, SEQ_RAMP, ramp
from ..ramps import stops as _stops

FONT_DIR = Path(__file__).parent.parent / "data" / "fonts"
SANS_FALLBACK = ("Inter", "DejaVu Sans", "Liberation Sans")
MONO_FALLBACK = ("JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono")
MIN_TEXT_PX = 12            # nothing smaller than this is ever drawn
MIN_WEIGHT = 500            # thin strokes vanish in glare


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    panel: str
    sunk: str
    fg: str
    fg2: str
    rule: str               # 2 px borders
    rule2: str              # track outlines, dividers (non-text)
    accent: str
    on_accent: str
    warn: str               # fill
    on_warn: str
    alarm: str              # fill; in Night also used as text on panel
    on_alarm: str
    ok: str
    unverified: str
    series1: str            # ECU traces
    series2: str            # black-box traces
    series3: str            # third stall path
    nodata: str             # 3D cell without samples
    heat: tuple             # table heat stops, cold -> hot
    on_heat: str            # text on the table heat


DAYLIGHT = Tokens(
    name="daylight", bg="#FFFFFF", panel="#FFFFFF", sunk="#E9ECEF",
    fg="#000000", fg2="#1F2428", rule="#000000", rule2="#5A6570",
    accent="#0A3FA8", on_accent="#FFFFFF", warn="#FFD000", on_warn="#000000",
    alarm="#B00020", on_alarm="#FFFFFF", ok="#00572D", unverified="#7A3300",
    series1="#0A3FA8", series2="#963200", series3="#6A1B9A", nodata="#CCD1D6",
    heat=("#FFFFFF", "#FFD000", "#FF9600"), on_heat="#000000")

NIGHT = Tokens(
    name="night", bg="#000000", panel="#0B0E11", sunk="#1A1F25",
    fg="#FFFFFF", fg2="#D6DCE2", rule="#8B96A1", rule2="#8B96A1",
    accent="#6CB6FF", on_accent="#000000", warn="#FFC400", on_warn="#000000",
    alarm="#FF7A66", on_alarm="#000000", ok="#4ADE80", unverified="#FFB454",
    series1="#6CB6FF", series2="#FF9D4D", series3="#D2A8FF", nodata="#3D454D",
    heat=("#0B0E11", "#0A3FA8"), on_heat="#FFFFFF")

THEMES = {"daylight": DAYLIGHT, "night": NIGHT}

LIVE_POINT = "#E6007A"
# SEQ_RAMP and DIV_RAMP live in hondalog.ramps (no Qt): the KML export colours a track with them

# Every (text token, ground token) pair the UI draws text with. The test
# suite asserts each is 7:1 or better in its theme. Add a pair here before
# drawing text in a new combination.
TEXT_PAIRS = {
    "daylight": (
        ("fg", "bg"), ("fg", "panel"), ("fg", "sunk"),
        ("fg2", "bg"), ("fg2", "panel"), ("fg2", "sunk"),
        ("bg", "fg"),                               # inverted tabs, segments, tags
        ("accent", "bg"), ("accent", "panel"), ("accent", "sunk"),
        ("on_accent", "accent"),
        ("on_warn", "warn"), ("on_alarm", "alarm"),
        ("panel", "ok"),                            # check mark in the verified box
        ("ok", "bg"), ("ok", "panel"),
        ("unverified", "bg"), ("unverified", "panel"), ("unverified", "sunk"),
        ("series1", "bg"), ("series1", "panel"),
        ("series2", "bg"), ("series2", "panel"),    # never on sunk (6.43)
        ("on_heat", "heat_hot"), ("on_heat", "heat_cold"),
    ),
    "night": (
        ("fg", "bg"), ("fg", "panel"), ("fg", "sunk"),
        ("fg2", "bg"), ("fg2", "panel"), ("fg2", "sunk"),
        ("bg", "fg"),
        ("accent", "bg"), ("accent", "panel"), ("accent", "sunk"),
        ("on_accent", "accent"),
        ("on_warn", "warn"), ("on_alarm", "alarm"),
        ("alarm", "panel"),                         # alarm colour as text
        ("panel", "ok"),
        ("ok", "bg"), ("ok", "panel"),
        ("unverified", "bg"), ("unverified", "panel"), ("unverified", "sunk"),
        ("series1", "bg"), ("series1", "panel"),
        ("series2", "bg"), ("series2", "panel"), ("series2", "sunk"),
        ("on_heat", "heat_hot"), ("on_heat", "heat_cold"),
    ),
}


def token_hex(tokens: Tokens, name: str) -> str:
    if name == "heat_hot":
        return tokens.heat[-1]
    if name == "heat_cold":
        return tokens.heat[0]
    return getattr(tokens, name)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio of two #RRGGBB colours."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---- colour ramps (numpy, used by the 3D scene, the heat map, the table) ----

_SEQ = _stops(SEQ_RAMP)
_DIV = _stops(DIV_RAMP)


def seq_ramp(t) -> np.ndarray:
    return ramp(_SEQ, t)


def div_ramp(t) -> np.ndarray:
    return ramp(_DIV, t)


def heat_ramp(tokens: Tokens, t) -> np.ndarray:
    return ramp(_stops(tokens.heat), t)


def rgb_of(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ---- the runtime theme ------------------------------------------------------

class Theme(QObject):
    """Current tokens as QColors, plus caches of pens, brushes and fonts.

    Paint code asks for ``T.pen("fg", 2)`` or ``T.font(40, 800)``: nothing is
    constructed inside a paintEvent after the first frame.
    """

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.tokens = DAYLIGHT
        self._pens: dict = {}
        self._brushes: dict = {}
        self._fonts: dict = {}
        self._fonts_loaded = False
        self.sans = SANS_FALLBACK[0]
        self.mono = MONO_FALLBACK[0]
        self.live = QColor(LIVE_POINT)
        self._load_colours()

    # -- tokens --

    @property
    def name(self) -> str:
        return self.tokens.name

    @property
    def night(self) -> bool:
        return self.tokens is NIGHT

    def set(self, name: str) -> None:
        tokens = THEMES.get(name, DAYLIGHT)
        if tokens is self.tokens:
            return
        self.tokens = tokens
        self._load_colours()
        self.changed.emit()

    def toggle(self) -> None:
        self.set("daylight" if self.night else "night")

    def _load_colours(self) -> None:
        self._pens.clear()
        self._brushes.clear()
        for f in fields(Tokens):
            value = getattr(self.tokens, f.name)
            if isinstance(value, str) and value.startswith("#"):
                setattr(self, f.name, QColor(value))

    def color(self, name: str) -> QColor:
        return getattr(self, name)

    def pen(self, name: str, width: float = 2.0, style=Qt.SolidLine,
            cap=Qt.SquareCap, join=Qt.MiterJoin) -> QPen:
        key = (name, width, style, cap, join)
        pen = self._pens.get(key)
        if pen is None:
            pen = QPen(getattr(self, name), width, style, cap, join)
            if style == Qt.DashLine:
                pen.setDashPattern([3.0, 2.0])
            self._pens[key] = pen
        return pen

    def cpen(self, color: QColor, width: float = 2.0, cap=Qt.SquareCap, join=Qt.MiterJoin,
             dash: tuple | None = None) -> QPen:
        """Cached pen for an arbitrary colour (ink that depends on tile state)."""
        key = (color.rgba(), width, cap, join, dash)
        pen = self._pens.get(key)
        if pen is None:
            pen = QPen(color, width, Qt.SolidLine, cap, join)
            if dash:
                pen.setStyle(Qt.CustomDashLine)
                pen.setDashPattern(list(dash))
            self._pens[key] = pen
        return pen

    def brush(self, name: str) -> QBrush:
        brush = self._brushes.get(name)
        if brush is None:
            brush = self._brushes[name] = QBrush(getattr(self, name))
        return brush

    # -- fonts --

    def load_fonts(self) -> None:
        """Register the bundled fonts. Needs a QGuiApplication."""
        if self._fonts_loaded:
            return
        self._fonts_loaded = True
        if FONT_DIR.is_dir():
            for path in sorted(FONT_DIR.iterdir()):
                if path.suffix.lower() in (".otf", ".ttf"):
                    QFontDatabase.addApplicationFont(str(path))
        have = set(QFontDatabase.families())
        self.sans = next((f for f in SANS_FALLBACK if f in have), SANS_FALLBACK[1])
        self.mono = next((f for f in MONO_FALLBACK if f in have), MONO_FALLBACK[1])
        self._fonts.clear()

    def font(self, px: int, weight: int = 700, mono: bool = False,
             spacing: float = 0.0) -> QFont:
        """Cached font. px in pixels, weight 500-800, spacing in em."""
        key = (px, weight, mono, spacing)
        font = self._fonts.get(key)
        if font is None:
            self.load_fonts()
            font = QFont(self.mono if mono else self.sans)
            font.setPixelSize(max(MIN_TEXT_PX, int(px)))
            font.setWeight(QFont.Weight(max(MIN_WEIGHT, int(weight))))
            font.setStyleStrategy(QFont.PreferAntialias)
            font.setHintingPreference(QFont.PreferVerticalHinting)
            if spacing:
                font.setLetterSpacing(QFont.PercentageSpacing, 100.0 + spacing * 100.0)
            if hasattr(QFont, "Tag") and hasattr(font, "setFeature"):
                try:                            # Qt 6.7+
                    if mono:                    # no "<-" or ">=" ligatures in hex and limits
                        font.setFeature(QFont.Tag("calt"), 0)
                        font.setFeature(QFont.Tag("liga"), 0)
                    else:                       # tabular figures
                        font.setFeature(QFont.Tag("tnum"), 1)
                except Exception:
                    pass
            self._fonts[key] = font
        return font


T = Theme()


def apply(app) -> None:
    """Application-wide defaults. Styling stays in paint code, not stylesheets."""
    T.load_fonts()
    app.setFont(T.font(14, 600))
