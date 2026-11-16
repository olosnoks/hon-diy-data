"""Display units: converted for the screen only, US by default, remembered."""

import numpy as np
import pytest

from conftest import spin
from hondalog.data import open_log
from hondalog.export import export_log
from hondalog.ui import main_window as mw
from hondalog.ui.backends import LimitAlarms, order_of_loss
from hondalog.ui import adapters as ad
from hondalog.ui.bus import ChannelBus, ChannelSpec
from hondalog.ui.units import METRIC, US, U
from hondalog.units import UnitPrefs


def tile(shell, cid):
    return next(t for t in shell.sensors.tiles if t.state.spec.id == cid)


def test_nothing_is_converted_until_the_app_loads_its_settings():
    ect = ChannelSpec("ect", "Coolant", "°C", 0, -40, 140, warn_hi=100, alarm_hi=108)
    assert U.prefs is None and U.spec(ect) is ect and U.with_unit(ect, 88.0) == "88 °C"
    assert U.convert_text("Coolant 110 °C") == "Coolant 110 °C" and U.limit(ect, 108) == "108 °C"


def test_us_units_convert_what_is_shown_and_keep_map_in_kpa():
    U.preset("US")
    ect = ChannelSpec("ect", "Coolant", "°C", 0, -40, 140, warn_hi=100, alarm_hi=108)
    shown = U.spec(ect)
    assert (shown.units, shown.lo, shown.hi) == ("°F", -40.0, 284.0)
    assert (shown.warn_hi, shown.alarm_hi) == (212.0, pytest.approx(226.4))
    assert U.with_unit(ect, 88.0) == "190 °F" and U.value(ect, 100.0) == 212.0
    assert U.limit(ect, 108) == "226.4 °F" and U.spec(ect) is shown          # cached per spec
    vss = ChannelSpec("vss", "Speed", "km/h", 0, 0, 200)
    assert U.with_unit(vss, 100.0) == "62 mph" and U.spec(vss).hi == pytest.approx(124.27, abs=0.01)
    kpa = ChannelSpec("map", "MAP", "kPa", 0, 0, 110)
    assert U.spec(kpa) is kpa and U.with_unit(kpa, 32.0) == "32 kPa"         # the tuning convention stays
    for spec in (ChannelSpec("batt", "Battery", "V", 1, 8, 16), ChannelSpec("o2", "O2", "V", 2, 0, 1),
                 ChannelSpec("rpm", "RPM", "rpm", 0, 0, 8000), ChannelSpec("vtec", "VTEC", kind="flag"),
                 ChannelSpec("afr_est", "AFR", "AFR", 1, 10, 20)):
        assert U.spec(spec) is spec                                          # volts, rpm, flags, AFR: as they are
    flow = ChannelSpec("fuel_flow", "Fuel flow", "L/h", 1, 0, 40)
    assert U.with_unit(flow, 10.0) == "2.64 gal/h"                           # a smaller unit gets a decimal more
    assert U.preset_name() == "US" and U.as_dict() == US

    # pressure is the user's to change: MAP in psi gets the decimal it needs
    U.set_unit("pressure", "psi")
    assert U.with_unit(kpa, 101.0) == "14.6 psi" and U.preset_name() == ""
    U.preset("Metric")
    assert U.as_dict() == METRIC and U.with_unit(ect, 88.0) == "88 °C" and U.with_unit(kpa, 101.0) == "101 kPa"


def test_free_text_from_alarm_rules_is_converted():
    U.preset("US")
    assert U.convert_text("Coolant 110 °C") == "Coolant 230 °F"
    assert U.convert_text("Coolant 104.5 °C rising 2.0 °C/s") == "Coolant 220.1 °F rising 3.6 °F/s"
    assert U.convert_text("Speed 100 km/h at 3000 rpm, 13.8 V, MAP 32 kPa") == \
        "Speed 62 mph at 3000 rpm, 13.8 V, MAP 32 kPa"
    assert U.convert_text("IAT −5 °C") == "IAT 23 °F" and U.convert_text("RPM 800 falling 900 rpm/s") == \
        "RPM 800 falling 900 rpm/s"


def test_the_app_starts_in_us_units_and_remembers_a_change(shell, app, settings):
    """The owner's default: mph and °F on the tiles, MAP in kPa, volts as
    they are. The bus keeps the map's units."""
    assert settings.value("units/speed") is None and U.preset_name() == "US"
    bus = shell.session.bus
    vss, ect, kpa, batt = (tile(shell, c) for c in ("vss", "ect", "map", "batt"))
    assert (vss._units, ect._units, kpa._units, batt._units) == ("mph", "°F", "kPa", "V")
    assert bus.channel("ect").spec.units == "°C" and bus.channel("vss").spec.units == "km/h"
    c = bus.channel("ect").value
    assert ect._text == f"{c * 1.8 + 32:.0f}" and float(ect._max) >= float(ect._text) >= float(ect._min)
    assert vss._text == f"{bus.channel('vss').value * 0.6213712:.0f}"
    assert kpa._text == f"{bus.channel('map').value:.0f}"
    gauge = ect._frac                       # the gauge is a fraction of the range: the same in any unit

    shell.set_units("temperature", "°C")
    spin(app, shell)
    assert ect._units == "°C" and ect._text == f"{c:.0f}" and ect._frac == gauge and vss._units == "mph"
    assert settings.value("units/temperature") == "°C" and settings.value("units/speed") == "mph"

    # the Settings menu: presets and one submenu per quantity, the current choice ticked
    from PySide6.QtWidgets import QMenu
    menu = shell.settings_menu(QMenu(shell))
    units = menu.actions()[0].menu()
    titles = [a.text() for a in units.actions() if a.text()]
    assert menu.actions()[0].text() == "Units" and titles[0].startswith("US") and titles[1].startswith("Metric")
    assert "Speed: mph" in titles and "Temperature: °C" in titles and "Pressure (MAP, baro): kPa" in titles
    assert not any(a.isChecked() for a in units.actions()[:2])               # a mix: neither preset
    speed = next(a.menu() for a in units.actions() if a.text() == "Speed: mph")
    assert [(a.text(), a.isChecked()) for a in speed.actions()] == [("km/h", False), ("mph", True)]
    speed.actions()[0].trigger()
    spin(app, shell)
    assert vss._units == "km/h" and settings.value("units/speed") == "km/h" and U.as_dict()["speed"] == "km/h"

    # the drive view and a second start use what was saved
    shell.set_units(preset="US")
    shell.show_screen(mw.DRIVE)
    spin(app, shell)
    drive = {t.state.spec.id: t for t in shell.drive.tiles}
    assert drive["vss"]._units == "mph" and drive["ect"]._units == "°F"
    U.set(None)
    U.load(settings)
    assert U.as_dict() == US
    settings.setValue("units/speed", "furlongs")                             # rubbish in the file: the default
    settings.setValue("units/temperature", "kPa")
    U.load(settings)
    assert U.as_dict() == US


def test_alarm_texts_follow_the_units_and_the_log_marker_does_not(app):
    U.preset("US")
    bus = ChannelBus()
    bus.register(ChannelSpec("ect", "Coolant", "°C", 0, -40, 140, warn_hi=100, alarm_hi=108))
    bus.register(ChannelSpec("vss", "Speed", "km/h", 0, 0, 200, warn_hi=130))
    bus.register(ChannelSpec("batt", "Battery", "V", 1, 8, 16, warn_lo=12.8, alarm_lo=11.5))
    alarms = LimitAlarms()
    bus.publish({"ect": 110.0, "vss": 140.0, "batt": 12.1}, t=1.0)
    started = alarms.evaluate(bus, 1.0)
    by_id = {e.channel_id: e for e in alarms.active()}
    assert [e.channel_id for e in started] == ["ect"]
    assert (by_id["ect"].value_text, by_id["ect"].limit_text) == ("230 °F", "above 226.4 °F")
    assert by_id["ect"].log_text == "above 108 °C"                           # what a marker in the log says
    assert (by_id["vss"].value_text, by_id["vss"].limit_text) == ("87 mph", "above 80.8 mph")
    assert (by_id["batt"].value_text, by_id["batt"].limit_text) == ("12.1 V", "below 12.8 V")
    rows = {r.name: (r.warn, r.alarm) for r in alarms.limits()}
    assert rows == {"Coolant °F": (">212", ">226.4"), "Speed mph": (">80.78", "—"),
                    "Battery": ("<12.8", "<11.5")}
    U.preset("Metric")
    assert {r.name: r.warn for r in alarms.limits()}["Coolant"] == ">100"


def test_wired_alarm_rules_are_shown_in_display_units(app, tmp_path):
    """W3's rules write their own message with the map's units in it: the
    screen converts it, the log keeps the rule's words."""
    import types
    from hondalog.ui import wired
    U.preset("US")
    ecu = wired.LinkSource(out_dir=tmp_path)
    alarms = wired.PipelineAlarms(ecu)
    bus = ChannelBus()
    for spec in ecu.specs():
        bus.register(spec)
    rule = next(r for r in alarms.engine.rules if r.channel == "ect")
    ecu.frames.append(types.SimpleNamespace(events=[types.SimpleNamespace(
        name=rule.name, kind="raised", channel="ect", value=110.0, severity=rule.severity,
        message="Coolant 110 °C", unverified=False)]))
    alarms.evaluate(bus, 2.0)
    event = alarms.active()[0]
    assert (event.value_text, event.limit_text, event.log_text) == ("230 °F", "Coolant 230 °F", "Coolant 110 °C")
    row = next(r for r in alarms.limits() if r.name.endswith("°F"))
    high = rule.high * 1.8 + 32
    assert f">{round(high, 2):g}" in (row.warn + row.alarm)


def test_order_of_loss_and_replay_readouts_use_display_units(shell, app):
    t = np.linspace(-30.0, 5.0, 351)
    ect = np.where(t < -4.0, 90.0, 60.0)
    view = ad.LogView(None, "x", trigger_t=0.0)
    view.specs["ect"] = ChannelSpec("ect", "Coolant", "°C", 0, -40, 140)
    view.t["ect"], view.data["ect"] = t, ect
    row = order_of_loss(view).loss[0]
    assert "fell below" in row.what and "°F" in row.what and "(was 194)" in row.what

    view.specs["map"] = ChannelSpec("map", "MAP", "kPa", 0, 0, 110)
    view.t["map"], view.data["map"] = t, np.full_like(t, 32.0)
    shell.show_screen(mw.REPLAY)
    shell.replay.show_view(view)
    lane = next(lane for lane in shell.replay.block.lanes if lane.cid == "map")
    shell.replay.block.set_cursor(-10.0, force=True)
    assert lane.label == "MAP kPa" and lane.value == "32"


def test_exports_keep_the_maps_units(synth_path, tmp_path):
    """Display units never reach the data: an export made while the screen
    shows °F and mph has °C and km/h columns, labelled as such."""
    U.preset("US")
    with open_log(synth_path) as log:
        assert log.units("ect") == "°C" and log.units("map") == "kPa"
        ect = log.series("ect").v[:50].copy()
        out = export_log(log, tmp_path / "a.csv", channels=["rpm", "ect", "iat", "map"], t1=30.0)
    text = "\n".join(line for line in out.read_text().splitlines() if line.startswith("#"))
    assert "ect=°C" in text and "map=kPa" in text and "°F" not in text and "psi" not in text
    with open_log(out) as back:
        assert back.units("ect") == "°C"                                    # and the numbers are °C, not °F
        assert abs(float(np.nanmedian(back.series("ect").v)) - float(np.median(ect))) < 3.0
