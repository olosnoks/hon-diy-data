"""The drive picker: what Replay's Open button shows instead of a file dialog.

Every drive in the database, newest first, each followed by its captures
(stalls and the like), then black box capture files. Type to search names,
notes, tags and markers; Enter or a double click opens. "Import file…" reads
a CSV / MSL / MLG log into the database as a new drive and opens it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import adapters as ad
from .theme import T

IMPORT_FILTER = "Logs (*.csv *.msl *.mlg *.bbx *.db);;All files (*)"


def _length(seconds: float) -> str:
    if seconds <= 0:
        return ""
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"


class DrivePicker(QDialog):
    """Modal list of drives. ``chosen`` is what ``LogReader.read`` takes, or None."""

    def __init__(self, logs: ad.LogReader, parent=None):
        super().__init__(parent)
        self.logs = logs
        self.chosen = None
        self.error = ""
        self.setWindowTitle("Open a drive")
        self.resize(860, 520)
        t = T.tokens
        self.setStyleSheet(
            f"QDialog {{ background: {t.bg}; }} QLabel {{ color: {t.fg}; }}"
            f"QLineEdit {{ border: 2px solid {t.rule}; padding: 6px 10px; background: {t.bg}; color: {t.fg}; }}"
            f"QLineEdit:focus {{ border: 3px solid {t.accent}; padding: 5px 9px; }}"
            f"QTreeWidget {{ border: 2px solid {t.rule}; background: {t.bg}; color: {t.fg};"
            f" alternate-background-color: {t.sunk}; }}"
            f"QTreeWidget::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}"
            f"QHeaderView::section {{ background: {t.sunk}; color: {t.fg}; border: 0;"
            f" border-bottom: 2px solid {t.rule}; padding: 4px 8px; }}"
            f"QPushButton {{ border: 2px solid {t.rule}; background: {t.bg}; color: {t.fg}; padding: 8px 18px; }}"
            f"QPushButton:default {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}"
            f"QPushButton:disabled {{ color: {t.rule2}; border-color: {t.rule2}; }}")

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search drives, notes, markers")
        self.search.setFont(T.font(15, 500))
        self.search.textChanged.connect(self._fill)
        self.tree = QTreeWidget(self)
        self.tree.setFont(T.font(14, 500))
        self.tree.setHeaderLabels(["Drive", "Started", "Length", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 180)
        self.tree.setColumnWidth(2, 80)
        self.tree.itemDoubleClicked.connect(lambda *_: self._accept())
        self.tree.currentItemChanged.connect(lambda *_: self._enable())
        self.empty = QLabel("", self)
        self.empty.setFont(T.font(14, 500))
        self.empty.setWordWrap(True)

        self.open_btn = QPushButton("Open", self)
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._accept)
        self.import_btn = QPushButton("Import file…", self)
        self.import_btn.setAutoDefault(False)
        self.import_btn.clicked.connect(self._import)
        cancel = QPushButton("Cancel", self)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        for button in (self.open_btn, self.import_btn, cancel):
            button.setFont(T.font(14, 700))

        buttons = QHBoxLayout()
        buttons.addWidget(self.import_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.open_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty)
        layout.addLayout(buttons)

        self.rows = list(logs.drives())
        self._fill()
        self.search.setFocus()

    def _fill(self) -> None:
        words = self.search.text().lower().split()
        self.tree.clear()
        for row in self.rows:
            if words and not all(w in row.text for w in words):
                continue
            nested = row.kind not in ("drive", "file")
            what = row.notes
            if row.kind == "drive":
                flags = [f"{row.stalls} stall{'s' if row.stalls != 1 else ''}"] if row.stalls else []
                flags += [row.status] if row.status in ("crashed", "recording") else []
                what = " · ".join(flags + ([row.notes] if row.notes else []))
            item = QTreeWidgetItem([("    " if nested else "") + row.name, row.started,
                                    _length(row.duration_s), what])
            item.setData(0, Qt.UserRole, row)
            if row.kind == "stall":
                item.setForeground(0, T.color("alarm"))
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self.empty.setText("")
        elif self.rows:
            self.empty.setText("Nothing matches.")
        else:
            self.empty.setText("No drives recorded yet. Record one (R on the Drive screen), "
                               "or import a CSV, MSL or MLG log.")
        self._enable()

    def _enable(self) -> None:
        self.open_btn.setEnabled(self.tree.currentItem() is not None)

    def _accept(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        self.chosen = item.data(0, Qt.UserRole).ref
        self.accept()

    def import_path(self, path) -> bool:
        """Import ``path`` and choose the new drive. False (and ``error``) if it cannot be read."""
        try:
            self.chosen = self.logs.import_file(path)
        except (OSError, ValueError, KeyError) as exc:
            self.error = f"Cannot import {path}: {exc}"
            self.empty.setText(self.error)
            return False
        self.accept()
        return True

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import a log file", "", IMPORT_FILTER)
        if path:
            self.import_path(path)

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Down, Qt.Key_Up) and self.search.hasFocus():
            self.tree.setFocus()
            self.tree.keyPressEvent(ev)
            return
        super().keyPressEvent(ev)
