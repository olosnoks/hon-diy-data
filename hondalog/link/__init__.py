"""ECU link internals: clocks, adaptive timing, read planner, scheduler.

``transport.py`` (bytes on the wire and the request/reply ``Link``) and
``poller.py`` (the worker thread) are built from these parts.
"""

from .clock import REAL, Clock, VirtualClock
from .planner import Block, CostModel, Span, bus_load, plan_blocks, predict_rates, spans_from_channels
from .scheduler import ChannelRate, Scheduler, rate_class_of, target_hz
from .timing import AdaptiveTiming, LinkStats

__all__ = [
    "REAL", "Clock", "VirtualClock",
    "Block", "CostModel", "Span", "bus_load", "plan_blocks", "predict_rates",
    "spans_from_channels",
    "ChannelRate", "Scheduler", "rate_class_of", "target_hz",
    "AdaptiveTiming", "LinkStats",
]
