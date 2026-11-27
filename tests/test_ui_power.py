"""F5 Power offscreen: pulls found in the demo drive, the curve, the result,
the inputs, compare / baseline / export, and the Time in cell tab."""

import csv

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from hondalog.ui import adapters as ad
from hondalog.ui.screens import power as P


def press(screen, k):
    return screen.key(QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))


# ---- pure parts ----

def test_reference_is_the_published_d16y8():
    assert P.ref_torque(5500) == pytest.approx(145.0)
    assert float(np.max(P.REF_TORQUE)) == 145.0
    i = int(np.argmax(P.REF_HP))
    assert P.REF_RPM[i] == 6600 and P.REF_HP[i] == pytest.approx(127.0, abs=0.5)


def test_curve_results_come_in_any_shape():
    rpm = np.array([3000.0, 4000.0, 5000.0])
    tq = np.array([120.0, 126.0, 122.0])
    hp = tq * rpm * P.HP_PER_NM_RPM
    c = P._norm_curve((rpm, tq, hp, 0.1), "x")
    assert c.band_pct == pytest.approx(10.0) and np.allclose(c.hi, tq * 1.1)

    class Result:
        pass
    r = Result()
    r.rpm, r.torque, r.hp, r.lo, r.hi = rpm, tq, hp, tq - 5, tq + 5
    c = P._norm_curve(r, "y")
    assert np.allclose(c.lo, tq - 5)
    cells = dict((a, (b, n)) for a, b, n in c.results(False))
    assert cells["Peak torque"][0] == "126 N·m" and "4,000" in cells["Peak torque"][1]
    assert cells["Uncertainty"][1] == "grade unknown"
    p = P._norm_pull({"t0": 10.0, "t1": 14.6, "gear": 2, "quality": "FULL", "rpm_lo": 2770, "rpm_hi": 5411,
                      "tps": 84, "map_kpa": 98}, None)
    assert p.gear == "2nd" and p.quality == "full" and "4.6 s" in p.detail and "TPS 84 %" in p.detail


def test_d3_analysis_finds_the_demo_pulls():
    view = P.demo_drive()
    pulls = P.detect_pulls(view)
    assert P.analysis_name() == "hondalog.analysis.power"
    full = [p for p in pulls if p.quality == "full"]
    assert len(full) >= 2 and all(p.wall.startswith("22:5") for p in pulls)
    c = P.make_curve(view, max(full, key=lambda p: p.rpm_hi - p.rpm_lo), P.Inputs())
    assert len(c.rpm) > 5
    assert np.all(np.abs(c.torque / (0.9 * P.ref_torque(c.rpm)) - 1) < 0.06)
    heavier = P.make_curve(view, full[-1], P.Inputs(mass_kg=1400))
    assert np.nanmean(heavier.torque) > np.nanmean(c.torque)


def test_demo_drive_pulls_and_fallback_curve():
    view = P.demo_drive()
    pulls = P.fallback_detect(view)
    assert [p.quality for p in pulls] == ["short", "full", "part", "part", "full"]
    full = pulls[1]
    assert full.gear == "2nd" and full.rpm_lo < 2900 and full.rpm_hi > 5200
    c = P.fallback_curve(view, full, P.Inputs())
    assert not c.empty
    # the demo car is driven by 90 % of the reference torque: the dyno finds it within 6 %
    err = c.torque / (0.9 * P.ref_torque(c.rpm)) - 1
    assert np.all(np.abs(err) < 0.06)
    heavier = P.fallback_curve(view, full, P.Inputs(mass_kg=1400))
    assert np.nanmean(heavier.torque) > np.nanmean(c.torque)


def test_cell_stats_count_timing_pulled():
    view = P.demo_drive()
    rpm_axis = np.arange(500, 7600, 500.0)
    map_axis = np.arange(15, 106, 10.0)
    hits, mean, retard, cid = P.cell_stats(view, rpm_axis, map_axis)
    assert cid == "ign_corr" and hits.sum() == len(view.t["ign_corr"])
    assert retard.sum() > 0 and np.nanmax(mean) > 4
    assert P.cell_stats(ad.LogView(None), rpm_axis, map_axis)[3] == ""


# ---- the screen ----

@pytest.fixture
def power(app, settings):
    from hondalog.ui.session import build_session
    session = build_session(demo=True, demo_clock=lambda: 100.0)
    scr = P.PowerScreen(session, settings)
    scr.resize(1366, 648)
    scr.show()
    app.processEvents()
    yield scr
    scr.close()


def test_power_screen_opens_on_the_full_throttle_pull(power, app):
    assert len(power.pulls) >= 4
    sel = power.selected()                           # the longest full-throttle pull: 3rd gear, 2,000 -> 6,000+
    assert sel.quality == "full" and sel.gear == "3rd" and sel.rpm_hi - sel.rpm_lo > 3500
    assert [p.quality for p in power.pulls].count("full") >= 2
    assert power.result.title.startswith("RESULT · ") and power.result.title.endswith("PULL")
    names = [c[0] for c in power.result.cells]
    assert names == ["Peak torque", "Power", "vs D16Y8", "Uncertainty"]
    assert power.curve_plot.maxline.isVisible() and power.curve_plot.maxline.value() > 5000
    assert power.export_btn.isEnabled() and power.compare_btn.isEnabled()
    # every widget inside the window: nothing off the right or the bottom
    for w in (power.result, power.inputs_box, power.export_btn, power.stack, power.pull_list):
        g = w.geometry()
        assert g.right() <= power.width() and g.bottom() <= power.height(), w
    power.grab()


def test_power_inputs_compare_baseline_export(power, app, settings, tmp_path):
    before = float(np.nanmax(power.curve.torque))
    power.fields["mass_kg"].setValue(1400)
    app.processEvents()
    assert float(np.nanmax(power.curve.torque)) > before
    assert float(settings.value("power/mass_kg")) == 1400
    power.set_compare(2)
    assert power.compare is not None and power.pull_list.compare == 2
    assert any("compare" in text for _, text, _ in power.legend.items)
    power.set_compare(-1)
    assert power.compare is None
    power.save_baseline()
    assert power.baseline is not None and "pull" in power.baseline.label
    assert P.Curve.from_json(str(settings.value("power/baseline"))) is not None
    out = tmp_path / "curve.csv"
    n = power.export_csv(out)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert n > 10 and rows[0][:3] == ["rpm", "torque_nm", "hp"] and len(rows) == n + 1
    sel = power.sel
    press(power, Qt.Key_Up)
    assert power.sel == sel - 1
    power.select(0)
    assert power.sel == 0 and power.result.cells[0][0] == "Peak torque"


def test_power_tabs_and_time_in_cell(power, app):
    from hondalog.ui.screens.map3d import Map3DScreen
    assert isinstance(power.map3d, Map3DScreen)
    assert press(power, Qt.Key_Tab) and power.page == "trace"
    power.show_page("map3d")                         # the shell's name for the tab
    assert power.page == "cells" and power.stack.currentWidget() is power.cells
    assert not power.result.isVisible()              # Time in cell takes the result column's room
    power.refresh(100.0)
    power.cells.set_mode("retard")
    assert power.cells.grid.isVisible() and not power.map3d.isVisible()
    assert np.nansum(power.cells.grid.values) > 0
    power.cells.set_mode("ign")
    assert "1/4°" in power.cells.grid.note
    power.cells.set_mode("map3d")
    assert power.map3d.isVisible()
    power.show_page("curve")
    assert power.result.isVisible()
    power.grab()
