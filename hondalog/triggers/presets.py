"""Built-in trigger rules, and loading user rules from a TOML file.

The two stall rules reproduce hondalog 0.1.0's stall catcher exactly and are
on by default. The others are off until the channels they use have been
verified on the car: with a guessed address map a wrong battery or coolant
byte would fill the log folder with false captures.

Channel names are the default address map's.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Iterable

from .engine import TriggerRule


def stall_rules(rpm_channel: str = "rpm", pre_s: float = 30.0, post_s: float = 10.0,
                rpm_run: float = 400.0, rpm_zero: float = 50.0,
                link_down_s: float = 3.0, review_s: float = 60.0,
                vss_channel: str = "vss", moving_kmh: float = 5.0) -> list[TriggerRule]:
    """The stall catcher as two rules.

    Both arm once RPM has been above ``rpm_run``.
      * "stall":      RPM reads below ``rpm_zero`` (ECU still answering)
      * "link_loss":  no valid reply for ``link_down_s`` (ECU lost power, cable out)

    The link-loss rule is reviewed when the ECU answers again (for up to
    ``review_s``): if the very first RPM reading after the silence is above
    ``rpm_run`` and close to the last one before it (below 1.25 x that + 100
    rpm), the engine never stopped. A dead ECU stops the engine; an ECU that
    comes back after a power loss or a key cycle answers at key-on with RPM 0,
    or, if the engine was restarted before the link came back (OBD2-only mode
    needs a slow 5-baud init), in an after-start flare well above the idle it
    had (2026-11-25 18:17: 670 before, 1,446 after). The capture is then filed
    as kind "dropout" (label "LINK DROPOUT"), not as a stall, so it stays out
    of the stall list but its data is kept.

    It is also a dropout when the car was moving before the silence (speed
    above ``moving_kmh``) and is still moving when the ECU answers, with the
    engine above ``rpm_run``: then RPM follows the road, not the idle, and the
    "close to before" test fails on an ordinary throttle change (2026-11-26
    23:16: 8 s silent while accelerating 41 -> 59 km/h, back at 2,887 rpm against
    2,066 before, filed as a stall). A stall while moving that the driver
    restarts while still rolling would also be filed as a dropout; the capture
    is kept either way. With no speed channel (never read before the
    silence) this test is false and the rpm test decides; ``vss_channel=""``
    leaves it out altogether.

    ``link_down_s`` was 1.0 s until 2026-11-26. The stock P2P on the KKL cable
    has short K-line dropouts with the engine running (96 in one 28-minute
    idle, up to 1.5 s; one of 2.5 s on 2026-11-25), and one of them was filed
    as a stall. A real power loss keeps the ECU silent until the restart, so
    3 s costs nothing; the window still starts ``pre_s`` before the silence.
    """
    arm = f"{rpm_channel} > {rpm_run:g}"
    return [
        TriggerRule("stall", when=f"{rpm_channel} < {rpm_zero:g}", arm_when=arm,
                    pre_s=pre_s, post_s=post_s, label="STALL (rpm 0)",
                    message="rpm dropped to 0", kind="stall", reason="rpm", group="stall"),
        TriggerRule("link_loss", when="link == 0", arm_when=arm, hold_s=link_down_s,
                    pre_s=pre_s + link_down_s, post_s=post_s, label="STALL (link lost)",
                    message="link lost (no reply from ECU)", kind="stall", reason="link",
                    group="stall", review_s=review_s, cancel_kind="dropout",
                    cancel_when=(f"{arm} and ({rpm_channel} < before_{rpm_channel} * 1.25 + 100"
                                 + (f" or (before_{vss_channel} > {moving_kmh:g}"
                                    f" and {vss_channel} > {moving_kmh:g})" if vss_channel else "")
                                 + ")"),
                    cancel_label="LINK DROPOUT (engine running)",
                    cancel_message="ECU silent, engine still running when it answered again"),
    ]


def extra_rules() -> list[TriggerRule]:
    """Rules from the feature list that ship disabled (see the module text)."""
    return [
        TriggerRule("low_battery", when="batt < 11.0", rearm_when="batt > 12.0", hold_s=0.5,
                    pre_s=30, post_s=10, label="LOW BATTERY", severity="warn",
                    message="battery below 11.0 V for 0.5 s", kind="lowbatt", enabled=False),
        TriggerRule("lean_spike", when="o2 < 0.1 and tps > 50 and rpm > 2500", hold_s=0.3,
                    pre_s=10, post_s=5, label="LEAN", severity="critical",
                    message="O2 lean under load for 0.3 s", kind="lean", enabled=False),
        TriggerRule("overtemp", when="ect > 105", rearm_when="ect < 100", hold_s=2.0,
                    pre_s=60, post_s=10, label="OVERTEMP", severity="critical",
                    message="coolant above 105 °C for 2 s", kind="overtemp", enabled=False),
    ]


def default_rules(**stall_options: Any) -> list[TriggerRule]:
    return stall_rules(**stall_options) + extra_rules()


MANUAL = TriggerRule("manual", when="0", pre_s=30, post_s=10, label="CAPTURE",
                     message="capture requested", kind="capture")


def external_rule(why: str, rules: Iterable[TriggerRule] = ()) -> TriggerRule:
    """The one-off rule for a trigger that came from outside (the black box).

    It is a stall capture like the two built-in rules and uses their window,
    so the file looks the same whichever side saw the engine die first.
    """
    stall = next((r for r in rules if r.kind == "stall"), None)
    pre_s, post_s = (stall.pre_s, stall.post_s) if stall else (30.0, 10.0)
    return TriggerRule("external", when="0", pre_s=pre_s, post_s=post_s, label=f"STALL ({why})",
                       message=why, kind="stall", reason="external", group="stall")


def alarm_rule(name: str, message: str = "", severity: str = "critical",
               rules: Iterable[TriggerRule] = ()) -> TriggerRule:
    """The one-off rule for an alarm whose actions include "capture"
    (``hondalog.alarms``). The alarm engine has already done the deciding
    (limits, hold time, hysteresis, latching); this only says what to save.
    A trigger rule of the same name, if there is one, sets the window."""
    own = next((r for r in rules if r.name == name), None)
    pre_s, post_s = (own.pre_s, own.post_s) if own else (30.0, 10.0)
    return TriggerRule(f"alarm_{name}", when="0", pre_s=pre_s, post_s=post_s,
                       label=f"ALARM {name}", message=message or f"alarm {name}",
                       kind="alarm", reason="alarm", severity=severity)


def load_rules(path: str | Path) -> list[TriggerRule]:
    """Read rules from TOML::

        [[trigger]]
        name = "overtemp"
        when = "ect > 105"
        hold_s = 2
        rearm_when = "ect < 100"
        actions = ["capture", "mark", "notify"]

    Raises ValueError (or ExprError) with the rule's name on a bad entry.
    """
    with open(path, "rb") as fh:
        doc = tomllib.load(fh)
    rules = []
    for entry in doc.get("trigger", []):
        try:
            rules.append(TriggerRule.from_dict(entry))
        except TypeError as exc:
            raise ValueError(f"{path}: trigger {entry.get('name', '?')!r}: {exc}") from None
    return rules


def merge_rules(base: Iterable[TriggerRule], override: Iterable[TriggerRule]) -> list[TriggerRule]:
    """``base`` with same-named rules replaced by, and new rules added from, ``override``."""
    merged = {r.name: r for r in base}
    merged.update({r.name: r for r in override})
    return list(merged.values())
