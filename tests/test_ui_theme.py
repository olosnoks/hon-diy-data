"""Design tokens: Dracula and Darker (the web app's theme.ts), no light
surfaces, the contrast rule, and the F9 toggle."""

import pytest

from hondalog.ui import theme
from hondalog.ui.theme import DARKER, DRACULA, MIN_TEXT_CONTRAST, TEXT_PAIRS, contrast, luminance, token_hex


def test_tokens_match_the_guide():
    d, k = DRACULA, DARKER
    assert (d.bg, d.panel, d.sunk, d.line, d.rule) == ("#282A36", "#21222C", "#343746", "#44475A", "#6272A4")
    assert (d.fg, d.fg2, d.accent, d.on_accent) == ("#F8F8F2", "#C5C8D6", "#BD93F9", "#282A36")
    assert (d.warn, d.alarm, d.ok, d.unverified) == ("#F1FA8C", "#FF5555", "#50FA7B", "#FFB86C")
    assert (d.series1, d.series2, d.series3, d.series4, d.series5) == (
        "#BD93F9", "#FF79C6", "#8BE9FD", "#50FA7B", "#FFB86C")
    assert theme.LIVE_POINT == "#FF79C6"
    assert (k.bg, k.panel, k.sunk) == ("#191A21", "#121318", "#21222C")
    for name in ("fg", "fg2", "rule", "line", "accent", "warn", "alarm", "ok", "unverified",
                 "series1", "series2", "series3", "series4", "series5"):
        assert getattr(k, name) == getattr(d, name), name           # the rest the same
    assert k.on_accent == k.on_warn == k.on_alarm == "#191A21"       # as web/src/ui/theme.ts
    assert set(theme.THEMES) == {"dracula", "darker"}


@pytest.mark.parametrize("tokens", [DRACULA, DARKER], ids=["dracula", "darker"])
def test_no_light_surfaces(tokens):
    """Grounds, fills and ramps stay dark: nothing is white or light grey."""
    for name in ("bg", "panel", "sunk", "line", "rule", "nodata"):
        assert luminance(getattr(tokens, name)) < 0.2, name
    assert luminance(tokens.heat[0]) < 0.02                         # the heat starts dark
    assert "#FFFFFF" not in [getattr(tokens, f) for f in ("bg", "panel", "sunk", "fg", "fg2")]
    assert theme.DIV_RAMP[len(theme.DIV_RAMP) // 2] == "#6272A4"    # no white middle any more


def test_old_names_resolve():
    assert theme.resolve("daylight") == "dracula" and theme.resolve("night") == "darker"
    assert theme.resolve("Darker") == "darker" and theme.resolve("") == "dracula"
    assert theme.resolve("solarized") == "dracula"
    assert theme.DAYLIGHT is DRACULA and theme.NIGHT is DARKER      # old imports keep working


@pytest.mark.parametrize("a,b,ratio", [
    ("#000000", "#FFFFFF", 21.00), ("#F8F8F2", "#282A36", 13.36), ("#C5C8D6", "#343746", 7.08),
    ("#BD93F9", "#282A36", 5.90), ("#FF5555", "#282A36", 4.53), ("#F8F8F2", "#191A21", 16.27),
])
def test_contrast_formula(a, b, ratio):
    assert contrast(a, b) == pytest.approx(ratio, abs=0.02)


@pytest.mark.parametrize("name", ["dracula", "darker"])
def test_every_text_pair_is_readable(name):
    tokens = theme.THEMES[name]
    bad = []
    for fg, bg in TEXT_PAIRS[name]:
        ratio = contrast(token_hex(tokens, fg), token_hex(tokens, bg))
        if ratio < MIN_TEXT_CONTRAST:
            bad.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not bad, bad


def test_borders_are_3_to_1():
    for tokens in (DRACULA, DARKER):
        for ground in (tokens.bg, tokens.panel):
            assert contrast(tokens.rule, ground) >= 3.0
            assert contrast(tokens.rule2, ground) >= 3.0


def test_alarm_is_never_text_on_sunk():
    assert contrast(DRACULA.alarm, DRACULA.sunk) < MIN_TEXT_CONTRAST       # the reason for the rule
    assert ("alarm", "sunk") not in TEXT_PAIRS["dracula"]


def test_ramps_are_monotonic_in_lightness():
    import numpy as np
    for rgb in (theme.seq_ramp(np.linspace(0, 1, 50)), theme.heat_ramp(DRACULA, np.linspace(0, 1, 50)),
                theme.heat_ramp(DARKER, np.linspace(0, 1, 50))):
        lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
        assert (np.diff(lum) > 0).all()
    assert theme.div_ramp(0.5) == pytest.approx(theme.rgb_of("#6272A4"), abs=0.01)


def test_heat_ink_flips_from_light_to_dark(app):
    from PySide6.QtGui import QColor
    T = theme.T
    T.set("dracula")
    assert T.heat_ink(QColor(DRACULA.heat[0])) == T.fg
    assert T.heat_ink(QColor(DRACULA.heat[-1])) == T.on_heat
    for stop in DRACULA.heat:                                       # the better of the two inks, always
        ink = T.heat_ink(QColor(stop)).name().upper()
        other = DRACULA.fg if ink == DRACULA.on_heat else DRACULA.on_heat
        assert contrast(ink, stop) >= contrast(other, stop)


def test_fonts_bundled_and_never_small_or_thin(app):
    T = theme.T
    T.load_fonts()
    assert T.sans == "Inter" and T.mono == "JetBrains Mono"
    f = T.font(8, 300)
    assert f.pixelSize() >= 12 and f.weight() >= 500
    assert T.font(40, 800) is T.font(40, 800)                   # cached, not rebuilt


def test_toggle_switches_tokens_and_signals(app):
    T = theme.T
    seen = []
    T.changed.connect(lambda: seen.append(T.name))
    try:
        T.set("dracula")
        T.toggle()
        assert T.darker and T.night and T.label == "DARKER" and seen == ["darker"]
        assert T.bg.name().upper() == "#191A21" and T.fg.name().upper() == "#F8F8F2"
        T.toggle()
        assert not T.darker and T.label == "DRACULA" and T.pen("bg", 2).color().name().upper() == "#282A36"
        T.set("night")                                          # an old name
        assert T.name == "darker"
    finally:
        T.set("dracula")


def test_palette_has_no_light_roles(app):
    from PySide6.QtGui import QPalette
    T = theme.T
    for name in ("dracula", "darker"):
        T.set(name)
        pal = theme.palette()
        for role in (QPalette.Window, QPalette.Base, QPalette.AlternateBase, QPalette.Button,
                     QPalette.ToolTipBase, QPalette.Light, QPalette.Midlight, QPalette.Mid):
            assert pal.color(role).lightness() < 120, (name, role)
        assert "#FFFFFF" not in theme.stylesheet().upper()
    T.set("dracula")


def test_theme_flag_takes_the_new_names_and_the_old_ones():
    from hondalog.__main__ import build_parser
    ap = build_parser()
    for value, expect in (("dracula", "dracula"), ("darker", "darker"), ("daylight", "dracula"), ("night", "darker")):
        assert theme.resolve(ap.parse_args(["--theme", value]).theme) == expect
    with pytest.raises(SystemExit):
        ap.parse_args(["--theme", "solarized"])
    assert ap.parse_args([]).bb is None                         # no --bb: the ESP32 black box stays hidden
