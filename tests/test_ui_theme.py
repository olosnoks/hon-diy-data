"""Design tokens: exact values from HANDOFF.md and the 7:1 contrast rule."""

import pytest

from hondalog.ui import theme
from hondalog.ui.theme import DAYLIGHT, NIGHT, TEXT_PAIRS, contrast, token_hex


def test_tokens_match_handoff():
    d, n = DAYLIGHT, NIGHT
    assert (d.bg, d.panel, d.sunk, d.fg, d.fg2) == ("#FFFFFF", "#FFFFFF", "#E9ECEF", "#000000", "#1F2428")
    assert (d.rule, d.rule2, d.accent, d.on_accent) == ("#000000", "#5A6570", "#0A3FA8", "#FFFFFF")
    assert (d.warn, d.alarm, d.ok, d.unverified) == ("#FFD000", "#B00020", "#00572D", "#7A3300")
    assert (d.series1, d.series2, d.nodata) == ("#0A3FA8", "#963200", "#CCD1D6")
    assert (n.bg, n.panel, n.sunk, n.fg, n.fg2) == ("#000000", "#0B0E11", "#1A1F25", "#FFFFFF", "#D6DCE2")
    assert (n.rule, n.rule2, n.accent, n.on_accent) == ("#8B96A1", "#8B96A1", "#6CB6FF", "#000000")
    assert (n.warn, n.alarm, n.ok, n.unverified) == ("#FFC400", "#FF7A66", "#4ADE80", "#FFB454")
    assert (n.series1, n.series2, n.nodata) == ("#6CB6FF", "#FF9D4D", "#3D454D")
    assert theme.LIVE_POINT == "#E6007A"
    assert theme.SEQ_RAMP[0] == "#00224E" and theme.SEQ_RAMP[-1] == "#FEE838"
    assert theme.DIV_RAMP == ("#053061", "#2166AC", "#F7F7F7", "#E08214", "#7F3B08")


@pytest.mark.parametrize("a,b,ratio", [
    ("#000000", "#FFFFFF", 21.00), ("#000000", "#E9ECEF", 17.71), ("#1F2428", "#FFFFFF", 15.66),
    ("#0A3FA8", "#FFFFFF", 9.17), ("#FFFFFF", "#B00020", 7.33), ("#000000", "#FFD000", 14.27),
    ("#963200", "#FFFFFF", 7.63), ("#FF7A66", "#0B0E11", 7.58), ("#6CB6FF", "#0B0E11", 9.01),
    ("#FF9D4D", "#1A1F25", 8.04),
])
def test_contrast_formula_matches_handoff_table(a, b, ratio):
    assert contrast(a, b) == pytest.approx(ratio, abs=0.02)


@pytest.mark.parametrize("name", ["daylight", "night"])
def test_every_text_pair_is_7_to_1(name):
    tokens = theme.THEMES[name]
    bad = []
    for fg, bg in TEXT_PAIRS[name]:
        ratio = contrast(token_hex(tokens, fg), token_hex(tokens, bg))
        if ratio < 7.0:
            bad.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not bad, bad


def test_non_text_rules_are_3_to_1():
    for tokens in (DAYLIGHT, NIGHT):
        for ground in (tokens.bg, tokens.panel, tokens.sunk):
            assert contrast(tokens.rule2, ground) >= 3.0


def test_series2_is_never_text_on_sunk_in_daylight():
    assert contrast(DAYLIGHT.series2, DAYLIGHT.sunk) < 7.0      # the reason for the rule
    assert ("series2", "sunk") not in TEXT_PAIRS["daylight"]


def test_ramps_are_monotonic_in_lightness():
    import numpy as np
    rgb = theme.seq_ramp(np.linspace(0, 1, 50))
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
    assert (np.diff(lum) > 0).all()
    assert theme.div_ramp(0.5) == pytest.approx([0.969, 0.969, 0.969], abs=0.01)


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
        T.set("daylight")
        T.toggle()
        assert T.night and T.fg.name().upper() == "#FFFFFF" and seen == ["night"]
        T.toggle()
        assert not T.night and T.pen("fg", 2).color().name() == "#000000"
    finally:
        T.set("daylight")
