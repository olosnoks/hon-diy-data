"""Small dashboard widgets and the live-data history buffer."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from . import theme


class GaugeTile(QWidget):
    """One channel as a big number with a range bar underneath."""

    def __init__(self, channel, color=theme.ACCENT, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.color = QColor(color)
        self._text = "--"
        self._frac = 0.0
        self.setMinimumSize(150, 62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        note = "" if channel.verified else "\nUNVERIFIED address/formula"
        self.setToolTip(f"{channel.label}: address 0x{channel.addr:02X}, {channel.length} byte(s)\n"
                        f"formula: {channel.formula}{note}")

    def set_value(self, value) -> None:
        ch = self.channel
        text = ch.format(value)
        if value is None or value != value:
            frac = 0.0
        else:
            span = (ch.max - ch.min) or 1.0
            frac = min(1.0, max(0.0, (value - ch.min) / span))
        if text != self._text or abs(frac - self._frac) > 0.004:
            self._text, self._frac = text, frac
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PANEL))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 6, 6)

        small = QFont(self.font())
        small.setPointSizeF(9.5)
        p.setFont(small)
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(10, 5, w - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, self.channel.label)
        p.drawText(QRectF(10, 5, w - 20, 18), Qt.AlignRight | Qt.AlignVCenter, self.channel.units)
        if not self.channel.verified:
            fm = p.fontMetrics()
            p.setPen(QColor(theme.WARN))
            p.drawText(QRectF(14 + fm.horizontalAdvance(self.channel.label), 5, 20, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, "?")

        big = QFont(self.font())
        big.setBold(True)
        big.setPixelSize(int(max(20, min(h - 36, w / 4.2))))
        p.setFont(big)
        p.setPen(QColor(theme.TEXT if self._text != "--" else theme.DIM))
        p.drawText(QRectF(10, 22, w - 20, h - 34), Qt.AlignLeft | Qt.AlignVCenter, self._text)

        bar = QRectF(10, h - 10, w - 20, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PANEL_HI))
        p.drawRoundedRect(bar, 2, 2)
        if self._frac > 0:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * self._frac, 4), 2, 2)
        p.end()


class Lamp(QLabel):
    """On/off flag."""

    def __init__(self, channel, parent=None):
        super().__init__(channel.label + ("" if channel.verified else " ?"), parent)
        self.channel = channel
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(26)
        self.setToolTip(f"address 0x{channel.addr:02X}, {channel.formula}"
                        + ("" if channel.verified else "\nUNVERIFIED"))
        self._state = None
        self.set_value(None)

    def set_value(self, value) -> None:
        state = None if value is None or value != value else bool(value)
        if state == self._state and self.styleSheet():
            return
        self._state = state
        if state:
            css = f"background:{theme.GOOD}; color:#000; font-weight:bold;"
        else:
            css = f"background:{theme.PANEL}; color:{theme.DIM if state is None else theme.TEXT};"
        self.setStyleSheet(f"QLabel {{ {css} border-radius:5px; padding:2px 6px; }}")


class History:
    """Fixed-size rolling store of samples as contiguous numpy arrays."""

    def __init__(self, names, capacity: int = 20000):
        self.cap = capacity
        self.n = 0
        self.t = np.full(2 * capacity, np.nan)
        self.data = {name: np.full(2 * capacity, np.nan) for name in names}

    def append(self, t: float, values: dict) -> None:
        if self.n == 2 * self.cap:              # slide the newer half down
            self.t[:self.cap] = self.t[self.cap:]
            for arr in self.data.values():
                arr[:self.cap] = arr[self.cap:]
            self.n = self.cap
        i = self.n
        self.t[i] = t
        for name, arr in self.data.items():
            v = values.get(name)
            arr[i] = np.nan if v is None else v
        self.n = i + 1

    def window(self, since: float):
        """(start_index, time_array) for samples at or after ``since``."""
        t = self.t[:self.n]
        i = int(np.searchsorted(t, since))
        return i, t[i:]

    def series(self, name: str, start: int):
        return self.data[name][start:self.n]
