"""Colours and the dark palette."""

from PySide6.QtGui import QColor, QPalette

BG = "#15171b"
PANEL = "#20242a"
PANEL_HI = "#2b3038"
TEXT = "#e8eaed"
DIM = "#8e96a3"
ACCENT = "#4fa3ff"
GOOD = "#3ccf6e"
WARN = "#ffb000"
BAD = "#ff4d4d"

# One colour per plot row, repeated if there are more rows.
SERIES = ["#4fa3ff", "#3ccf6e", "#ffb000", "#ff6b6b", "#c58bff", "#35d0c8",
          "#ff9d4d", "#d0d35a"]

MONO = "DejaVu Sans Mono, Liberation Mono, monospace"


def apply(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor("#101216"))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL_HI))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(PANEL_HI))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#000000"))
    pal.setColor(QPalette.PlaceholderText, QColor(DIM))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#606672"))
    app.setPalette(pal)
    app.setStyleSheet(
        "QToolTip { border: 1px solid #555; padding: 3px; }"
        "QTabBar::tab { padding: 6px 18px; }"
        "QPushButton { padding: 5px 12px; }"
    )
