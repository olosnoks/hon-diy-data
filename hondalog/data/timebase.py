"""Mapping a second clock (the black box) onto the session timebase.

The host sends its clock to the box about once a second (W2-07). Each such
exchange gives a pair (session time, box time). ``ClockSync`` fits offset and
drift to the recent pairs so box timestamps can be written into the log on
the same timebase as the K-line reads.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .model import FloatArray


class ClockSync:
    def __init__(self, window: int = 120):
        self._pairs: deque[tuple[float, float]] = deque(maxlen=window)
        self.offset = 0.0           # session = box * rate + offset
        self.rate = 1.0

    @property
    def synced(self) -> bool:
        return bool(self._pairs)

    def add(self, session_t: float, box_t: float) -> None:
        """Record that box time ``box_t`` was read at session time ``session_t``."""
        self._pairs.append((float(session_t), float(box_t)))
        box = np.array([p[1] for p in self._pairs])
        host = np.array([p[0] for p in self._pairs])
        if len(box) >= 8 and box[-1] - box[0] >= 5.0:
            # centre first: box times can be large and the fit is of a tiny slope error
            b0, h0 = box.mean(), host.mean()
            rate = float(np.polyfit(box - b0, host - h0, 1)[0])
            if 0.99 < rate < 1.01:          # crystals drift by ppm, not percent
                self.rate = rate
                self.offset = float(h0 - rate * b0)
                return
        self.rate = 1.0
        self.offset = float(np.median(host - box))

    def to_session(self, box_t: float | FloatArray) -> float | FloatArray:
        return box_t * self.rate + self.offset

    def residual_ms(self) -> float:
        """Worst disagreement between the fit and the stored pairs, in ms."""
        if not self._pairs:
            return 0.0
        return max(abs(h - (b * self.rate + self.offset)) for h, b in self._pairs) * 1000.0
