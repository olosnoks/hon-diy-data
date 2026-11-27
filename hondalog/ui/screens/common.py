"""Small pieces the Power, Bytes and Setup screens share: the compact tab row
of the design ("Curve | Pull trace | Time in cell"), and one style sheet that
gives stock Qt form widgets (text fields, tables, spin boxes, combo boxes) the
theme tokens. Colours come only from ``T``; nothing here holds one of its own.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .. import paint
from ..theme import T

TAB_H = 34


class TabStrip(QWidget):
    """A row of tabs: the current one filled with the accent, the others
    outlined with a quiet line. ``picked(id)`` when one is clicked."""

    picked = Signal(str)

    def __init__(self, options, current: str | None = None, parent=None):
        super().__init__(parent)
        self.options = list(options)            # (id, label)
        self.current = current if current is not None else self.options[0][0]
        self.setFixedHeight(TAB_H)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def ids(self) -> list[str]:
        return [o[0] for o in self.options]

    def set_current(self, option_id: str) -> None:
        if option_id != self.current and option_id in self.ids():
            self.current = option_id
            self.update()

    def pick(self, option_id: str) -> None:
        if option_id in self.ids():
            self.set_current(option_id)
            self.picked.emit(option_id)

    def step(self, delta: int) -> None:
        ids = self.ids()
        i = ids.index(self.current) if self.current in ids else 0
        self.pick(ids[(i + delta) % len(ids)])

    def _cells(self) -> list[QRectF]:
        font = T.font(14, 700)
        x, out = 0.0, []
        for _, label in self.options:
            w = paint.text_w(font, label) + 28
            out.append(QRectF(x, 0, w, TAB_H))
            x += w + 6
        return out

    def sizeHint(self) -> QSize:
        cells = self._cells()
        return QSize(int(cells[-1].right()) + 2 if cells else 100, TAB_H)

    def mousePressEvent(self, event) -> None:
        for (oid, _), r in zip(self.options, self._cells()):
            if r.contains(event.position()):
                self.pick(oid)
                return

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        font = T.font(14, 700)
        p.setFont(font)
        for (oid, label), r in zip(self.options, self._cells()):
            if oid == self.current:
                p.fillRect(r, T.accent)
                p.setPen(T.on_accent)
            else:
                paint.frame(p, r, 2, T.line, T.bg)
                p.setPen(T.fg)
            p.drawText(r, Qt.AlignCenter, label)
        p.end()


def form_style(root: str) -> str:
    """Style sheet for the stock form widgets under the widget named ``root``."""
    t = T.tokens
    return f"""
    QWidget#{root} {{ background: {t.bg}; color: {t.fg}; }}
    QWidget#{root} QLabel {{ color: {t.fg}; background: transparent; }}
    QWidget#{root} QLabel[dim="true"] {{ color: {t.fg2}; }}
    QWidget#{root} QLabel[head="true"] {{ font-weight: 800; letter-spacing: 1px; }}
    QWidget#{root} QFrame[box="true"] {{ border: 2px solid {t.rule}; background: {t.panel}; }}
    QWidget#{root} QLineEdit, QWidget#{root} QPlainTextEdit, QWidget#{root} QTextEdit,
    QWidget#{root} QComboBox, QWidget#{root} QSpinBox, QWidget#{root} QDoubleSpinBox,
    QWidget#{root} QTableWidget, QWidget#{root} QListWidget {{
        background: {t.bg}; color: {t.fg}; border: 2px solid {t.rule}; border-radius: 0; padding: 3px 6px;
        selection-background-color: {t.accent}; selection-color: {t.on_accent};
        gridline-color: {t.line}; alternate-background-color: {t.panel}; }}
    QWidget#{root} QLineEdit:focus, QWidget#{root} QPlainTextEdit:focus, QWidget#{root} QComboBox:focus,
    QWidget#{root} QSpinBox:focus, QWidget#{root} QDoubleSpinBox:focus {{ border: 2px solid {t.accent}; }}
    QWidget#{root} QLineEdit:disabled, QWidget#{root} QSpinBox:disabled,
    QWidget#{root} QDoubleSpinBox:disabled {{ color: {t.fg2}; background: {t.panel}; }}
    QWidget#{root} QHeaderView::section {{ background: {t.sunk}; color: {t.fg}; border: 0;
        border-right: 1px solid {t.line}; border-bottom: 2px solid {t.rule}; padding: 4px 6px; font-weight: 700; }}
    QWidget#{root} QTableWidget::item:selected, QWidget#{root} QListWidget::item:selected {{
        background: {t.accent}; color: {t.on_accent}; }}
    QWidget#{root} QTableCornerButton::section {{ background: {t.sunk}; border: 0; }}
    QWidget#{root} QComboBox QAbstractItemView {{ background: {t.bg}; color: {t.fg};
        selection-background-color: {t.accent}; selection-color: {t.on_accent}; border: 2px solid {t.rule}; }}
    QWidget#{root} QCheckBox, QWidget#{root} QRadioButton {{ color: {t.fg}; spacing: 8px; }}
    QWidget#{root} QCheckBox::indicator, QWidget#{root} QRadioButton::indicator {{
        width: 18px; height: 18px; border: 2px solid {t.rule}; background: {t.bg}; }}
    QWidget#{root} QCheckBox::indicator:checked, QWidget#{root} QRadioButton::indicator:checked {{
        background: {t.accent}; }}
    QWidget#{root} QScrollArea {{ border: 0; background: {t.bg}; }}
    QWidget#{root} QScrollArea > QWidget > QWidget {{ background: {t.bg}; }}
    """


def label(text: str = "", dim: bool = False, head: bool = False, mono: bool = False,
          wrap: bool = False, px: int = 14) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("dim", dim)
    lab.setProperty("head", head)
    lab.setFont(T.font(13 if head else px, 800 if head else (600 if dim else 700), mono=mono))
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def restyle(widget: QWidget, root: str) -> None:
    """Apply the form style again (theme change) and re-polish property-styled labels."""
    widget.setStyleSheet(form_style(root))
    for lab in widget.findChildren(QLabel):
        lab.style().unpolish(lab)
        lab.style().polish(lab)
