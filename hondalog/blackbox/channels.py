"""The black box channels, described the same way ECU channels are
(``hondalog.addressmap.Channel``), so gauges, plots and replay need no
special case. Names all start with ``bb_``."""

from __future__ import annotations

from ..addressmap import Channel


def _ch(name, label, units="", lo=0.0, hi=1.0, decimals=0, kind="value", plot=False, gauge=False):
    return Channel(name, 0, label=label, units=units, min=lo, max=hi, decimals=decimals,
                   kind=kind, plot=plot, gauge=gauge, status="verified")


BB_CHANNELS = [
    _ch("bb_vbat", "BB battery", "V", 0, 18, 2, plot=True, gauge=True),
    _ch("bb_ecub", "BB ECU +B", "V", 0, 18, 2, plot=True, gauge=True),
    _ch("bb_pump", "BB fuel pump feed", "V", 0, 18, 2, plot=True, gauge=True),
    _ch("bb_ig1", "BB IG1", "V", 0, 18, 2, plot=True, gauge=True),
    _ch("bb_alt", "BB alternator", "V", 0, 18, 2),
    _ch("bb_vbat_min", "BB battery min", "V", 0, 18, 1),
    _ch("bb_vbat_max", "BB battery max", "V", 0, 18, 1),
    _ch("bb_ecub_min", "BB ECU +B min", "V", 0, 18, 1),
    _ch("bb_ecub_max", "BB ECU +B max", "V", 0, 18, 1),
    _ch("bb_pump_min", "BB pump feed min", "V", 0, 18, 1),
    _ch("bb_pump_max", "BB pump feed max", "V", 0, 18, 1),
    _ch("bb_ig1_min", "BB IG1 min", "V", 0, 18, 1),
    _ch("bb_ig1_max", "BB IG1 max", "V", 0, 18, 1),
    _ch("bb_rpm_tach", "BB RPM (tach)", "rpm", 0, 8000, 0, plot=True, gauge=True),
    _ch("bb_rpm_ign", "BB RPM (ign trigger)", "rpm", 0, 8000, 0, plot=True),
    _ch("bb_inj_pw", "BB injector pulse", "ms", 0, 20, 2, plot=True),
    _ch("bb_inj_duty", "BB injector duty", "%", 0, 100, 1),
    _ch("bb_tach", "BB tach level", kind="flag"),
    _ch("bb_ign", "BB ign trigger level", kind="flag"),
    _ch("bb_inj", "BB injector level", kind="flag"),
    _ch("bb_button", "BB button", kind="flag"),
    _ch("bb_ig1_on", "BB IG1 on", kind="flag"),
    _ch("bb_running", "BB engine running", kind="flag"),
]
BB_NAMES = [c.name for c in BB_CHANNELS]
_BY_NAME = {c.name: c for c in BB_CHANNELS}

# what the live stream carries (the event file has all of BB_CHANNELS)
LIVE_NAMES = ["bb_vbat", "bb_ecub", "bb_pump", "bb_ig1", "bb_alt", "bb_vbat_min", "bb_ecub_min",
              "bb_rpm_tach", "bb_rpm_ign", "bb_inj_pw", "bb_inj_duty", "bb_button", "bb_ig1_on",
              "bb_running"]


def bb_channel(name: str) -> Channel | None:
    """Channel metadata for a ``bb_*`` name, or None."""
    return _BY_NAME.get(name)
