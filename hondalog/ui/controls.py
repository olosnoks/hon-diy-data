"""Buttons, segmented controls and cards, painted from the theme tokens.

44 px tall, 2 px borders, square corners, no stylesheets. Nothing here takes
keyboard focus: every action has a key, handled in one place (the shell).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractButton, QLineEdit, QSizePolicy, QWidget

from . import paint
from .theme import T


class Button(QAbstractButton):
    """kind: 'normal' or 'primary'. A checked button is drawn inverted."""

    def __init__(self, text: str = "", key: str = "", kind: str = "normal",
                 icon=None, height: int = 44, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.key = key
        self.kind = kind
        self.icon_path = icon           # a paint.* path, or a chip icon name
        self.px = 14
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_text(self, text: str) -> None:
        if text != self.text():
            self.setText(text)
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        w = 28.0
        if self.text():
            w += paint.text_w(T.font(self.px, 700), self.text())
        if self.icon_path is not None:
            w += 16 + (8 if self.text() else 0)
        if self.key:
            w += paint.keycap_w(self.key) + 8
        return QSize(max(44, int(w + 0.99)), self.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        if not self.isEnabled():
            p.fillRect(r, T.sunk)
            p.setPen(T.cpen(T.fg2, 2, dash=(3.0, 2.0)))
            p.drawRect(r.adjusted(1, 1, -1, -1))
            ink = T.fg2
        else:
            if self.kind == "primary":
                fill, ink, edge = T.accent, T.on_accent, T.accent
            elif self.isChecked() or self.isDown():
                fill, ink, edge = T.accent, T.on_accent, T.accent
            else:
                fill, ink, edge = T.panel, T.fg, T.rule
            paint.frame(p, r, 2, edge, fill)
        font = T.font(self.px, 700)
        tw = paint.text_w(font, self.text()) if self.text() else 0.0
        iw = 16.0 if self.icon_path is not None else 0.0
        kw = paint.keycap_w(self.key) if self.key else 0.0
        total = tw + iw + kw + (8 if tw and iw else 0) + (8 if kw and (tw or iw) else 0)
        x = (r.width() - total) / 2.0
        cy = r.height() / 2.0
        if iw:
            if isinstance(self.icon_path, str):
                paint.chip_icon(p, self.icon_path, x + 1, cy - 7, ink)
            else:
                p.setRenderHint(QPainter.Antialiasing, True)
                paint.shape(p, self.icon_path, x, cy - 8, 16, ink)
                p.setRenderHint(QPainter.Antialiasing, False)
            x += iw + 8
        if tw:
            p.setFont(font)
            p.setPen(ink)
            p.drawText(QRectF(x, 0, tw + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       self.text())
            x += tw + 8
        if kw:
            paint.keycap(p, x, cy, self.key, ink)
        p.end()


class Segment(QWidget):
    """A row of joined buttons. One is selected (inverted) unless momentary."""

    picked = Signal(str)

    def __init__(self, options, current: str | None = None, momentary: bool = False,
                 parent=None):
        super().__init__(parent)
        self.options = list(options)        # (id, label)
        self.current = current if current is not None else (
            None if momentary else self.options[0][0])
        self.momentary = momentary
        self._down: str | None = None
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)

    def set_options(self, options, current: str | None = None) -> None:
        self.options = list(options)
        if current is not None:
            self.current = current
        self.update()

    def set_current(self, option_id: str | None) -> None:
        if option_id != self.current:
            self.current = option_id
            self.update()

    def cycle(self) -> None:
        ids = [o[0] for o in self.options]
        nxt = ids[(ids.index(self.current) + 1) % len(ids)] if self.current in ids else ids[0]
        self.set_current(nxt)
        self.picked.emit(nxt)

    def _cell(self, i: int) -> QRectF:
        n = len(self.options)
        w = (self.width() + 2.0 * (n - 1)) / n
        x0 = round(i * (w - 2))
        x1 = self.width() if i == n - 1 else round(i * (w - 2) + w)
        return QRectF(x0, 0, x1 - x0, self.height())

    def _at(self, x: float) -> str | None:
        for i, (oid, _) in enumerate(self.options):
            if self._cell(i).contains(x, 1):
                return oid
        return None

    def mousePressEvent(self, event) -> None:
        self._down = self._at(event.position().x())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        hit, self._down = self._at(event.position().x()), None
        if hit is not None:
            if not self.momentary:
                self.current = hit
            self.picked.emit(hit)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        font = T.font(14, 700)
        on_cell = None
        for i, (oid, label) in enumerate(self.options):
            r = self._cell(i)
            on = oid == self.current or oid == self._down
            if on:
                on_cell = (r, label)
                continue
            paint.frame(p, r, 2, T.rule, T.panel)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(r, Qt.AlignCenter, paint.elide(font, label, r.width() - 10))
        if on_cell is not None:             # drawn last: its border wins the overlap
            r, label = on_cell
            p.fillRect(r, T.accent)
            p.setFont(font)
            p.setPen(T.on_accent)
            p.drawText(r, Qt.AlignCenter, paint.elide(font, label, r.width() - 10))
        p.end()


class Card(QWidget):
    """Panel box with a 2 px border (4 px for the one emphasised box per
    screen) and a caption. Subclasses override paint_body(p, x, y, w, h)."""

    PAD_X, PAD_Y = 10, 8

    def __init__(self, title: str = "", border: int = 2, parent=None):
        super().__init__(parent)
        self.title = title
        self.border = border

    def body_rect(self) -> QRectF:
        top = self.PAD_Y + self.border - 2 + (20 if self.title else 0)
        pad = self.PAD_X + self.border - 2
        return QRectF(pad, top, self.width() - 2 * pad, self.height() - top - self.PAD_Y)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint.frame(p, QRectF(self.rect()), self.border, T.rule, self.ground())
        if self.title:
            pad = self.PAD_X + self.border - 2
            paint.caption(p, pad, self.PAD_Y + self.border - 2, self.title, self.ink2(),
                          self.width() - 2 * pad)
        b = self.body_rect()
        self.paint_body(p, b.x(), b.y(), b.width(), b.height())
        p.end()

    def ground(self):
        return T.panel

    def ink2(self):
        return T.fg2

    def paint_body(self, p: QPainter, x: float, y: float, w: float, h: float) -> None:
        pass


class TextCard(Card):
    """A card holding one wrapped sentence."""

    def __init__(self, title: str, border: int = 2, px: int = 14, weight: int = 600,
                 parent=None):
        super().__init__(title, border, parent)
        self.px, self.weight = px, weight
        self._text = ""
        self._lines: list[str] = []
        self._lines_w = -1.0

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._lines_w = -1.0
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        font = T.font(self.px, self.weight)
        if self._lines_w != w:
            self._lines_w = w
            self._lines = paint.wrap(font, self._text, w)
        line_h = round(self.px * 1.4)
        lines = self._lines[:max(1, int(h // line_h))]
        p.setFont(font)
        p.setPen(T.fg)
        m = paint.fm(font)
        for i, line in enumerate(lines):
            if i == len(lines) - 1 and len(lines) < len(self._lines):
                line = paint.elide(font, line + " …", w)
            p.drawText(QPointF(x, y + i * line_h + (line_h + m.ascent() - m.descent()) / 2),
                       line)


class HexEdit(QLineEdit):
    """The only text field in the app. Styled to the tokens; this is the one
    place a stylesheet is used besides nothing else."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setFrame(False)
        self.restyle()

    def restyle(self) -> None:
        self.setFont(T.font(15, 700, mono=True))
        t = T.tokens
        self.setStyleSheet(
            f"QLineEdit {{ border: 2px solid {t.rule}; border-radius: 0; padding: 0 10px;"
            f" background: {t.bg}; color: {t.fg}; selection-background-color: {t.accent};"
            f" selection-color: {t.on_accent}; }}"
            f"QLineEdit:focus {{ border: 3px solid {t.accent}; padding: 0 9px; }}")
