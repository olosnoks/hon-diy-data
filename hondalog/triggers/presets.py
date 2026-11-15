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
                link_down_s: float = 1.0) -> list[TriggerRule]:
    """The 0.1.0 stall catcher as two rules.

    Both arm once RPM has been above ``rpm_run``.
      * "stall":      RPM reads below ``rpm_zero`` (ECU still answering)
      * "link_loss":  no valid reply for ``link_down_s`` (ECU lost power, cable out)
    """
    arm = f"{rpm_channel} > {rpm_run:g}"
    return [
        TriggerRule("stall", when=f"{rpm_channel} < {rpm_zero:g}", arm_when=arm,
                    pre_s=pre_s, post_s=post_s, label="STALL (rpm 0)",
                    message="rpm dropped to 0", kind="stall", reason="rpm", group="stall"),
        TriggerRule("link_loss", when="link == 0", arm_when=arm, hold_s=link_down_s,
                    pre_s=pre_s, post_s=post_s, label="STALL (link lost)",
                    message="link lost (no reply from ECU)", kind="stall", reason="link",
                    group="stall"),
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
