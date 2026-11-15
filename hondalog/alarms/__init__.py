"""Alarms: limits, rate-of-change and combined conditions on any channel.

    from hondalog.alarms import AlarmEngine, AlarmRule

    engine = AlarmEngine([AlarmRule("ect_high", channel="ect", high=105, hold_s=2,
                                    severity="critical")], names=["ect"])
    for event in engine.update({"ect": 106.0}, t):
        ...   # event.kind is "raised", "cleared" or "acknowledged"

The engine has no Qt and no I/O. The UI reads ``active()`` / ``top()`` /
``severity_for(channel)``; the logger and the trigger engine take the events
returned by ``update`` (or ``subscribe`` a callback) and act on
``event.actions``.
"""

from .engine import (ACTIONS, CRITICAL, WARN, AlarmEngine, AlarmError, AlarmEvent, AlarmRule,
                     AlarmStatus)

__all__ = ["ACTIONS", "CRITICAL", "WARN", "AlarmEngine", "AlarmError", "AlarmEvent",
           "AlarmRule", "AlarmStatus"]
