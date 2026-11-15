"""One call per sample: measured values in, math channels and alarm events out.

    pipeline = LivePipeline(amap)
    frame = pipeline.feed(sample)        # a poller Sample
    frame.values                         # measured + math, by name
    frame.events                         # AlarmEvent list (raised / cleared / acknowledged)
    pipeline.alarms.top()                # what a banner should show right now

No Qt. Costs about 10 microseconds per sample for the shipped map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Collection, Mapping

from .addressmap import AddressMap, Channel
from .alarms import AlarmEngine, AlarmEvent
from .mathch import MathChannel, MathEngine


@dataclass
class LiveFrame:
    t: float
    link: bool
    values: dict[str, float | None]
    events: list[AlarmEvent] = field(default_factory=list)


class LivePipeline:
    def __init__(self, amap: AddressMap):
        self.amap = amap
        self.math: MathEngine = amap.math_engine()
        self.alarms: AlarmEngine = amap.alarm_engine()
        self.last: LiveFrame | None = None

    @property
    def channels(self) -> list[Channel | MathChannel]:
        """Everything that can be shown: measured channels, then math channels.
        Both kinds have name, label, units, min, max, decimals, kind, format()."""
        return [*self.amap.channels, *self.math.channels]

    def process(self, values: Mapping[str, float | None], t: float, link: bool = True,
                changed: Collection[str] | None = None) -> LiveFrame:
        """``changed`` names the channels that were re-read for this sample
        (tiered polling); leave it out when everything is fresh."""
        merged: dict[str, float | None] = dict(values)
        if link:
            merged.update(self.math.update(values, t, changed))
        else:                               # no reply this sweep: nothing new to work out
            merged.update(dict.fromkeys(self.math.names))
        events = self.alarms.update(merged, t)
        self.last = LiveFrame(t, link, merged, events)
        return self.last

    def feed(self, sample: Any) -> LiveFrame:
        """Take a poller ``Sample`` (anything with .t, .link and .values)."""
        return self.process(sample.values, sample.t, sample.link)

    def reset(self) -> None:
        self.math.reset()
        self.alarms.reset()
        self.last = None
