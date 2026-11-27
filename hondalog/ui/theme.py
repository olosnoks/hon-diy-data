"""Design tokens and the runtime theme.

Two themes, same layout: Dracula (default) and Darker (deeper grounds, the
same inks). Never a white or light surface: the owner can't use white
screens. The values are those of ``web/src/ui/theme.ts`` (and the CYD's
firmware palette), so the desktop, the web app and the handheld look alike.
Widgets never hold colours of their own; they paint from the single ``T``
object and repaint when ``T.changed`` fires. F9 toggles.

The token tables and ``contrast`` are plain Python so the tests can check
them without a QApplication.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
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
MIN_TEXT_CONTRAST = 4.5     # every text pair in TEXT_PAIRS (WCAG AA; the web app's rule)


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    panel: str
    sunk: str
    fg: str
    fg2: str
    rule: str               # 2 px borders (Dracula "comment")
    rule2: str              # track outlines, dividers that must be seen (non-text)
    line: str               # quiet dividers: between tabs, tile edges (non-text)
    accent: str
    on_accent: str
    warn: str               # fill
    on_warn: str
    alarm: str              # fill; also text on bg and panel (never on sunk)
    on_alarm: str
    ok: str
    unverified: str
    series1: str            # ECU traces
    series2: str            # second source / pink
    series3: str            # third stall path / cyan
    series4: str
    series5: str
    nodata: str             # 3D cell without samples
    heat: tuple             # table heat stops, dark -> bright
    on_heat: str            # text on the bright end of the heat (the dark end takes fg: heat_ink)


DRACULA = Tokens(
    name="dracula", bg="#282A36", panel="#21222C", sunk="#343746",
    fg="#F8F8F2", fg2="#C5C8D6", rule="#6272A4", rule2="#6272A4", line="#44475A",
    accent="#BD93F9", on_accent="#282A36", warn="#F1FA8C", on_warn="#282A36",
    alarm="#FF5555", on_alarm="#282A36", ok="#50FA7B", unverified="#FFB86C",
    series1="#BD93F9", series2="#FF79C6", series3="#8BE9FD", series4="#50FA7B", series5="#FFB86C",
    nodata="#44475A",
    heat=("#21222C", "#44475A", "#6272A4", "#BD93F9", "#FFB86C", "#F1FA8C"), on_heat="#282A36")

DARKER = replace(
    DRACULA, name="darker", bg="#191A21", panel="#121318", sunk="#21222C",
    on_accent="#191A21", on_warn="#191A21", on_alarm="#191A21",
    heat=("#121318", "#44475A", "#6272A4", "#BD93F9", "#FFB86C", "#F1FA8C"), on_heat="#191A21")

THEMES = {"dracula": DRACULA, "darker": DARKER}
DEFAULT = "dracula"
#: names stored by older versions (QSettings "theme", --theme) and what they are now
ALIASES = {"daylight": "dracula", "night": "darker", "day": "dracula", "dark": "darker"}
# old names for code that still imports them: both are dark now
DAYLIGHT, NIGHT = DRACULA, DARKER

LIVE_POINT = "#FF79C6"
# SEQ_RAMP and DIV_RAMP live in hondalog.ramps (no Qt): the KML export colours a track with them

_TEXT = (
    ("fg", "bg"), ("fg", "panel"), ("fg", "sunk"),
    ("fg2", "bg"), ("fg2", "panel"), ("fg2", "sunk"),
    ("bg", "fg"),                                   # inverted tags
    ("accent", "bg"), ("accent", "panel"), ("accent", "sunk"),
    ("on_accent", "accent"),                        # the current tab, segments, primary buttons
    ("on_warn", "warn"), ("on_alarm", "alarm"),
    ("alarm", "bg"), ("alarm", "panel"),            # alarm colour as text (never on sunk: 3.75)
    ("panel", "ok"),                                # check mark in the verified box
    ("ok", "bg"), ("ok", "panel"), ("ok", "sunk"),
    ("warn", "bg"), ("warn", "panel"),
    ("unverified", "bg"), ("unverified", "panel"), ("unverified", "sunk"),
    ("series1", "bg"), ("series1", "panel"),
    ("series2", "bg"), ("series2", "panel"), ("series2", "sunk"),
    ("series3", "bg"), ("series3", "panel"), ("series3", "sunk"),
    ("on_heat", "heat_hot"), ("fg", "heat_cold"),
)
# Every (text token, ground token) pair the UI draws text with. The test
# suite asserts each is MIN_TEXT_CONTRAST or better in its theme. Add a pair
# here before drawing text in a new combination.
TEXT_PAIRS = {"dracula": _TEXT, "darker": _TEXT}


def resolve(name) -> str:
    """A theme name as stored or typed (old names too) -> "dracula" | "darker"."""
    key = str(name or "").strip().lower()
    key = ALIASES.get(key, key)
    return key if key in THEMES else DEFAULT


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
        self.tokens = DRACULA
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
    def darker(self) -> bool:
        return self.tokens is DARKER

    @property
    def night(self) -> bool:
        """Old name of ``darker`` (screens written for Daylight / Night)."""
        return self.darker

    @property
    def label(self) -> str:
        return "DARKER" if self.darker else "DRACULA"

    def set(self, name: str) -> None:
        tokens = THEMES[resolve(name)]
        if tokens is self.tokens:
            return
        self.tokens = tokens
        self._load_colours()
        self.changed.emit()

    def toggle(self) -> None:
        self.set("dracula" if self.darker else "darker")

    def _load_colours(self) -> None:
        self._pens.clear()
        self._brushes.clear()
        for f in fields(Tokens):
            value = getattr(self.tokens, f.name)
            if isinstance(value, str) and value.startswith("#"):
                setattr(self, f.name, QColor(value))

    def color(self, name: str) -> QColor:
        return getattr(self, name)

    def heat_ink(self, ground: QColor) -> QColor:
        """Text colour on a heat cell: fg on the dark end, on_heat on the bright end."""
        lum = 0.2126 * ground.redF() + 0.7152 * ground.greenF() + 0.0722 * ground.blueF()
        return self.on_heat if lum > 0.45 else self.fg

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


def palette():
    """A QPalette of the current tokens for the stock Qt widgets (menus,
    dialogs, message boxes, scroll bars, tooltips, combo pop-ups): every role
    set, so Fusion never falls back to its light greys."""
    from PySide6.QtGui import QPalette
    pal = QPalette()
    t = T
    roles = ((QPalette.Window, t.panel), (QPalette.Base, t.bg), (QPalette.AlternateBase, t.sunk),
             (QPalette.Button, t.sunk), (QPalette.WindowText, t.fg), (QPalette.Text, t.fg),
             (QPalette.ButtonText, t.fg), (QPalette.BrightText, t.fg),
             (QPalette.ToolTipBase, t.sunk), (QPalette.ToolTipText, t.fg),
             (QPalette.Highlight, t.accent), (QPalette.HighlightedText, t.on_accent),
             (QPalette.PlaceholderText, t.fg2), (QPalette.Link, t.accent),
             (QPalette.LinkVisited, t.series2),
             # the bevel shades Fusion draws scroll bars, frames and splitters with
             (QPalette.Light, t.line), (QPalette.Midlight, t.sunk), (QPalette.Mid, t.line),
             (QPalette.Dark, t.panel), (QPalette.Shadow, t.panel))
    for role, colour in roles:
        pal.setColor(role, colour)
    if hasattr(QPalette, "Accent"):                     # Qt 6.6+
        pal.setColor(QPalette.Accent, t.accent)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, t.fg2)
    pal.setColor(QPalette.Disabled, QPalette.Base, t.panel)
    pal.setColor(QPalette.Disabled, QPalette.Button, t.panel)
    return pal


def stylesheet() -> str:
    """The few stock widgets a palette does not reach (tooltips, menus, scroll
    bars). Screens still paint themselves; this only keeps Qt's light defaults out."""
    t = T.tokens
    return (
        f"QToolTip {{ background: {t.sunk}; color: {t.fg}; border: 2px solid {t.rule}; padding: 4px; }}"
        f"QMenu {{ background: {t.panel}; color: {t.fg}; border: 2px solid {t.rule}; }}"
        f"QMenu::item {{ padding: 6px 24px 6px 16px; }}"
        f"QMenu::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}"
        f"QMenu::item:disabled {{ color: {t.fg2}; }}"
        f"QMenu::separator {{ height: 2px; background: {t.line}; margin: 4px 0; }}"
        f"QScrollBar:vertical {{ background: {t.sunk}; width: 14px; margin: 0; }}"
        f"QScrollBar:horizontal {{ background: {t.sunk}; height: 14px; margin: 0; }}"
        f"QScrollBar::handle {{ background: {t.rule}; min-height: 30px; min-width: 30px; }}"
        f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}"
        f"QScrollBar::add-page, QScrollBar::sub-page {{ background: {t.sunk}; }}"
        f"QMessageBox, QInputDialog, QFileDialog {{ background: {t.panel}; color: {t.fg}; }}"
    )


def apply(app) -> None:
    """Application-wide defaults: fonts, and the dark palette for stock widgets."""
    T.load_fonts()
    app.setFont(T.font(14, 600))
    apply_palette(app)


def apply_palette(app) -> None:
    """Palette, tooltip palette and the stock-widget style sheet of the current theme."""
    from PySide6.QtWidgets import QToolTip
    pal = palette()
    app.setPalette(pal)
    QToolTip.setPalette(pal)
    app.setStyleSheet(stylesheet())
