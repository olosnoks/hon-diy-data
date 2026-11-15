import pytest

from hondalog import units as u


def test_conversions():
    assert u.convert(100, "°C", "°F") == pytest.approx(212)
    assert u.convert(32, "degF", "C") == pytest.approx(0)
    assert u.convert(0, "°C", "K") == pytest.approx(273.15)
    assert u.convert(101.325, "kPa", "psi") == pytest.approx(14.696, abs=1e-3)
    assert u.convert(101.325, "kpa", "inHg") == pytest.approx(29.92, abs=1e-2)
    assert u.convert(14.696, "psi", "bar") == pytest.approx(1.01325, abs=1e-4)
    assert u.convert(100, "km/h", "mph") == pytest.approx(62.137, abs=1e-3)
    assert u.convert(60, "mph", "kph") == pytest.approx(96.56, abs=1e-2)
    assert u.convert(1.0, "lambda", "AFR") == pytest.approx(14.7)
    assert u.convert(None, "°C", "°F") is None
    assert u.convert_span(5, "°C", "°F") == pytest.approx(9)
    for bad in [("°C", "kPa"), ("rpm", "rpm"), ("V", "°C")]:
        with pytest.raises(ValueError):
            u.convert(1, *bad)
        with pytest.raises(ValueError):
            u.convert_span(1, *bad)


def test_round_trip_every_pair():
    for q, table in u._TABLE.items():
        for a in table:
            for b in table:
                assert u.convert(u.convert(37.5, a, b), b, a) == pytest.approx(37.5), (q, a, b)


def test_prefs():
    p = u.UnitPrefs(temperature="f", pressure="PSI", speed="mph", mixture="afr")
    assert p.temperature == "°F" and p.display_unit("kPa") == "psi"
    assert p.convert(88.0, "°C") == (pytest.approx(190.4), "°F")
    assert p.convert(13.9, "V") == (13.9, "V")          # no conversion known: shown as is
    assert p.format(88.0, "°C") == "190 °F" and p.format(None, "°C") == "--"
    assert p.format(3.14159, "", 2) == "3.14"
    assert u.UnitPrefs().format(88.0, "°C", 1) == "88.0 °C"
    assert u.UnitPrefs.us().speed == "mph"
    assert u.UnitPrefs.from_mapping({"pressure": "inHg", "junk": "x"}).pressure == "inHg"
    assert u.choices("°C") == ["°C", "°F", "K"] and u.choices("rpm") == []
    assert u.quantity("km/h") == "speed" and u.quantity("%") is None
    with pytest.raises(ValueError):
        u.UnitPrefs(temperature="psi")


def test_default_map_base_units_are_understood():
    from hondalog import addressmap
    amap = addressmap.load_default()
    assert u.quantity(amap.channel("ect").units) == "temperature"
    assert u.quantity(amap.channel("map").units) == "pressure"
    assert u.quantity(amap.channel("vss").units) == "speed"
    assert u.quantity(amap.math_channel("afr_est").units) == "mixture"
