"""Shared painting helpers: the shape vocabulary of the design.

State is always carried by shape first and colour second (a TN panel shifts
colour with viewing angle), so every state icon is a distinct outline.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPixmap,
                           QPolygonF)

from .theme import T


def _path(points, close=True) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(*points[0])
    for pt in points[1:]:
        path.lineTo(*pt)
    if close:
        path.closeSubpath()
    return path


# All on a 16 x 16 grid, as in the mockup SVGs.
TRIANGLE = _path([(8, 1), (15.5, 15), (0.5, 15)])
OCTAGON = _path([(5, 0.5), (11, 0.5), (15.5, 5), (15.5, 11), (11, 15.5), (5, 15.5),
                 (0.5, 11), (0.5, 5)])
CHECK = _path([(3, 8.5), (6.5, 12), (13, 4.5)], close=False)
CROSS = QPainterPath()
CROSS.moveTo(2, 2), CROSS.lineTo(14, 14), CROSS.moveTo(14, 2), CROSS.lineTo(2, 14)
DOWN = _path([(0, 0), (8, 0), (4, 7)])
UP = _path([(0, 7), (8, 7), (4, 0)])
PLAY = _path([(3, 1.5), (3, 14.5), (14, 8)])
PREV = QPainterPath()
PREV.addRect(2, 2, 3, 12)
PREV.addPath(_path([(14, 2), (14, 14), (6, 8)]))
NEXT = QPainterPath()
NEXT.addRect(11, 2, 3, 12)
NEXT.addPath(_path([(2, 2), (2, 14), (10, 8)]))
PAUSE = QPainterPath()
PAUSE.addRect(3, 2, 4, 12)
PAUSE.addRect(9, 2, 4, 12)
DIAMOND = _path([(8, 0), (16, 8), (8, 16), (0, 8)])

_metrics: dict = {}
_hatch: dict = {}


def fm(font) -> QFontMetricsF:
    """Cached font metrics (fonts from T.font are themselves cached)."""
    key = font.key()
    m = _metrics.get(key)
    if m is None:
        m = _metrics[key] = QFontMetricsF(font)
    return m


def text_w(font, text: str) -> float:
    return fm(font).horizontalAdvance(text)


def elide(font, text: str, width: float) -> str:
    return fm(font).elidedText(text, Qt.ElideRight, width)


def shape(p: QPainter, path: QPainterPath, x: float, y: float, size: float, brush) -> None:
    """Fill one of the 16-grid shapes at (x, y), scaled to size."""
    p.save()
    p.translate(x, y)
    p.scale(size / 16.0, size / 16.0)
    p.fillPath(path, brush)
    p.restore()


def stroke(p: QPainter, path: QPainterPath, x: float, y: float, size: float,
           color: QColor, width: float) -> None:
    p.save()
    p.translate(x, y)
    p.scale(size / 16.0, size / 16.0)
    p.setPen(T.cpen(color, width * 16.0 / size, Qt.FlatCap))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def chip_icon(p: QPainter, kind: str, x: float, y: float, color: QColor,
              blink_on: bool = True, size: float = 14.0) -> None:
    """ok / off / half / ring / rec / x, 14 px. Shape first, colour second."""
    p.setRenderHint(QPainter.Antialiasing, True)
    r = QRectF(x, y, size, size)
    if kind == "ok":
        p.setPen(Qt.NoPen)
        p.setBrush(T.ok)
        p.drawEllipse(r)
    elif kind == "rec":
        if blink_on:
            p.setPen(Qt.NoPen)
            p.setBrush(T.alarm)
            p.drawEllipse(r)
        else:                               # off phase keeps the outline
            p.setPen(T.cpen(T.alarm, 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1.25, 1.25, -1.25, -1.25))
    elif kind == "dot":                     # filled circle in the current colour
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(r)
    elif kind == "off":
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(T.cpen(color, 3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x + 1.5, y + 1.5, size - 3, size - 3))
    elif kind == "ring":
        p.setPen(T.cpen(color, 4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r.adjusted(2, 2, -2, -2))
    elif kind == "half":
        p.setPen(T.cpen(color, 2.5))
        p.setBrush(Qt.NoBrush)
        inner = r.adjusted(1.25, 1.25, -1.25, -1.25)
        p.drawEllipse(inner)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPie(inner, 90 * 16, 180 * 16)
    elif kind == "x":
        stroke(p, CROSS, x, y, size, color, 3)
    elif kind == "tri":
        shape(p, TRIANGLE, x, y, size, color)
    elif kind == "oct":
        shape(p, OCTAGON, x, y, size, color)
    p.setRenderHint(QPainter.Antialiasing, False)


def marker(p: QPainter, x: float, y: float, verified: bool, ink: QColor | None = None,
           ground: QColor | None = None) -> None:
    """The 18 x 18 verified / unverified box. ink + ground override the colours
    inside a warn band or an alarm tile."""
    if verified:
        p.fillRect(QRectF(x, y, 18, 18), ink if ink is not None else T.ok)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(T.cpen(ground if ground is not None else T.panel, 2.5, Qt.FlatCap))
        p.setBrush(Qt.NoBrush)
        p.save()
        p.translate(x + 2, y + 2)
        p.scale(14 / 16.0, 14 / 16.0)
        p.drawPath(CHECK)
        p.restore()
        p.setRenderHint(QPainter.Antialiasing, False)
    else:
        colour = ink if ink is not None else T.unverified
        p.setPen(T.cpen(colour, 2, Qt.FlatCap, dash=(2.0, 1.5)))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x + 1, y + 1, 16, 16))
        p.setPen(colour)
        p.setFont(T.font(13, 800))
        p.drawText(QRectF(x, y, 18, 18), Qt.AlignCenter, "?")


def hatch_brush() -> QBrush:
    """45 degree hatch, 8 px stripes of sunk on panel: stale / no data."""
    brush = _hatch.get(T.name)
    if brush is None:
        size = 22                           # 16 px period along the diagonal
        pm = QPixmap(size, size)
        pm.fill(T.panel)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.setPen(Qt.NoPen)
        qp.setBrush(T.sunk)
        for k in (-1, 0, 1):
            o = k * size
            qp.drawPolygon(QPolygonF([QPointF(o, size), QPointF(o + size / 2, size),
                                      QPointF(o + size * 1.5, 0), QPointF(o + size, 0)]))
        qp.end()
        brush = _hatch[T.name] = QBrush(pm)
    return brush


def keycap_w(text: str) -> float:
    return text_w(T.font(12, 700, mono=True), text) + 10


def keycap(p: QPainter, x: float, cy: float, text: str, color: QColor) -> float:
    """Mono 12/700 in a 2 px box, vertically centred on cy. Returns its width."""
    font = T.font(12, 700, mono=True)
    w = keycap_w(text)
    p.setPen(T.cpen(color, 2))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(x + 1, cy - 9, w - 2, 18))
    p.setFont(font)
    p.drawText(QRectF(x, cy - 10, w, 20), Qt.AlignCenter, text)
    return w


def caption(p: QPainter, x: float, y: float, text: str, color: QColor | None = None,
            width: float = 0.0) -> None:
    """12/800 caps, +0.06em, fg2. (x, y) is the top-left of a 16 px line."""
    font = T.font(12, 800, spacing=0.06)
    p.setFont(font)
    p.setPen(color if color is not None else T.fg2)
    text = text.upper()
    if width:
        text = elide(font, text, width)
    p.drawText(QPointF(x, y + 12), text)


def frame(p: QPainter, rect: QRectF, width: int = 2, color: QColor | None = None,
          fill: QColor | None = None) -> None:
    """A filled box with a crisp inner border of the given width."""
    if fill is not None:
        p.fillRect(rect, fill)
    p.setPen(T.cpen(color if color is not None else T.rule, width))
    p.setBrush(Qt.NoBrush)
    h = width / 2.0
    p.drawRect(rect.adjusted(h, h, -h, -h))


def track(p: QPainter, rect: QRectF, frac: float, fill: QColor | None = None,
          outline: QColor | None = None, ground: QColor | None = None) -> None:
    """A linear bar: sunk track, 2 px outline, solid fill (accent unless told) from the left."""
    p.fillRect(rect, ground if ground is not None else T.sunk)
    if frac > 0:
        p.fillRect(QRectF(rect.x(), rect.y(), rect.width() * min(1.0, frac), rect.height()),
                   fill if fill is not None else T.accent)
    p.setPen(T.cpen(outline if outline is not None else T.rule2, 2))
    p.setBrush(Qt.NoBrush)
    p.drawRect(rect.adjusted(1, 1, -1, -1))


def wrap(font, text: str, width: float) -> list[str]:
    """Greedy word wrap for the few places that draw a sentence."""
    m = fm(font)
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and m.horizontalAdvance(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def paragraph(p: QPainter, x: float, y: float, width: float, text: str, font,
              color: QColor, line_h: float, max_lines: int = 99) -> float:
    """Draw wrapped text; returns the y below it. Lines are cached per call
    site by the caller when the text is static."""
    p.setFont(font)
    p.setPen(color)
    lines = wrap(font, text, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = elide(font, lines[-1] + " …", width)
    m = fm(font)
    for line in lines:
        p.drawText(QPointF(x, y + (line_h + m.ascent() - m.descent()) / 2.0), line)
        y += line_h
    return y


def selected_row(p: QPainter, rect: QRectF) -> None:
    """The selected row of a list: sunk ground and a 5 px accent bar on the
    left, as the Events artboard (and the web app) draw it. Never a light fill."""
    p.fillRect(rect, T.sunk)
    p.fillRect(QRectF(rect.x(), rect.y(), 5, rect.height()), T.accent)
