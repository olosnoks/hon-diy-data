"""The drive picker: what Replay's Open button shows instead of a file dialog.

Every drive in the database, newest first, each followed by its captures
(stalls and the like), then black box capture files. Type to search names,
notes, tags and markers; Enter or a double click opens. "Import file…" reads
a CSV / MSL / MLG log into the database as a new drive and opens it.
"Delete…" (or the Delete key) removes the selected drive with everything in
it, or the selected capture's entry, after a question; a drive marked keep
asks a second time. "Keep" sets or clears that mark.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

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
        self.message = ""                   # the result of the last delete, shown under the list
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
            f"QPushButton:checked {{ background: {t.fg}; color: {t.bg}; }}"
            f"QPushButton:disabled {{ color: {t.rule2}; border-color: {t.rule2}; }}")

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search drives, notes, markers")
        self.search.setFont(T.font(15, 500))
        self.search.textChanged.connect(lambda _text: self._fill())
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
        self.delete_btn = QPushButton("Delete…", self)
        self.delete_btn.setAutoDefault(False)
        self.delete_btn.setToolTip("Delete the selected drive and everything recorded in it, "
                                   "or the selected capture's entry (Del)")
        self.delete_btn.clicked.connect(lambda: self.delete_current())
        self.keep_btn = QPushButton("Keep", self)
        self.keep_btn.setAutoDefault(False)
        self.keep_btn.setCheckable(True)
        self.keep_btn.setToolTip("Keep this drive: it is never pruned, and deleting it asks twice")
        self.keep_btn.clicked.connect(lambda on: self.set_keep(on))
        #: asks the user; a test replaces it. (title, text) -> bool
        self.confirm = self._confirm_box
        cancel = QPushButton("Cancel", self)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        for button in (self.open_btn, self.import_btn, self.delete_btn, self.keep_btn, cancel):
            button.setFont(T.font(14, 700))

        buttons = QHBoxLayout()
        buttons.addWidget(self.import_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.keep_btn)
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

    def _fill(self, select: str = "") -> None:
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
                flags += ["keep"] if row.keep else []
                what = " · ".join(flags + ([row.notes] if row.notes else []))
            item = QTreeWidgetItem([("    " if nested else "") + row.name, row.started,
                                    _length(row.duration_s), what])
            item.setData(0, Qt.UserRole, row)
            if row.kind == "stall":
                item.setForeground(0, T.color("alarm"))
            self.tree.addTopLevelItem(item)
            if select and row.name == select:
                self.tree.setCurrentItem(item)
        if self.tree.topLevelItemCount():
            if self.tree.currentItem() is None:
                self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self.empty.setText(self.message)
            self.message = ""
        elif self.rows:
            self.empty.setText("Nothing matches.")
        else:
            self.empty.setText("No drives recorded yet. Record one (R on the Drive screen), "
                               "or import a CSV, MSL or MLG log.")
        self._enable()

    def current_row(self) -> ad.DriveRow | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _enable(self) -> None:
        row = self.current_row()
        self.open_btn.setEnabled(row is not None)
        in_db = row is not None and row.kind != "file"
        self.delete_btn.setEnabled(in_db and row.status != "recording")
        self.keep_btn.setEnabled(in_db and row.kind == "drive")
        self.keep_btn.setChecked(bool(in_db and row.keep))

    # ---- delete / keep ----

    def _confirm_box(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Cancel, self)
        yes = box.addButton("Delete", QMessageBox.DestructiveRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is yes

    def delete_question(self, row: ad.DriveRow) -> str:
        """What the first question says: exactly what goes."""
        if row.kind != "drive":
            return (f"Delete the {row.kind} capture {row.name}?\n\nOnly the capture's entry goes. "
                    "The drive it was taken from, and all its data, stay.")
        parts = [row.started, _length(row.duration_s)]
        if row.captures:
            parts.append(f"{row.captures} capture{'s' if row.captures != 1 else ''}"
                         + (f", {row.stalls} of them stall{'s' if row.stalls != 1 else ''}"
                            if row.stalls else ""))
        text = (f"Delete {row.name}?\n\n{' · '.join(p for p in parts if p)}\n\n"
                "Everything recorded in this drive is removed from the database"
                + (", its captures included" if row.captures else "") + ". This cannot be undone.")
        if row.notes:
            text += f"\n\nNotes: {row.notes[:120]}"
        return text

    def delete_current(self) -> bool:
        """Delete the selected drive or capture after asking (twice for a
        drive marked keep). True if it was deleted."""
        row = self.current_row()
        if row is None or row.kind == "file":
            return False
        if not self.confirm("Delete", self.delete_question(row)):
            return False
        keep = row.kind == "drive" and row.keep
        if keep and not self.confirm("Marked keep", f"{row.name} is marked keep.\n\n"
                                     "You marked this drive so that it would not be deleted. "
                                     "Delete it anyway?"):
            return False
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        try:
            self.message = self.logs.delete(row.ref, allow_keep=keep)
        except ValueError as exc:
            self.error = str(exc)
            self.empty.setText(self.error)
            return False
        self.rows = list(self.logs.drives())
        self._fill()
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(min(index, self.tree.topLevelItemCount() - 1)))
        return True

    def set_keep(self, keep: bool) -> None:
        row = self.current_row()
        if row is None or row.kind != "drive":
            return
        try:
            self.logs.set_keep(row.ref, keep)
        except ValueError as exc:
            self.error = str(exc)
            self.empty.setText(self.error)
            return
        self.rows = list(self.logs.drives())
        self._fill(select=row.name)

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
        if ev.key() == Qt.Key_Delete and not self.search.hasFocus() and self.delete_btn.isEnabled():
            self.delete_current()
            return
        if ev.key() in (Qt.Key_Down, Qt.Key_Up) and self.search.hasFocus():
            self.tree.setFocus()
            self.tree.keyPressEvent(ev)
            return
        super().keyPressEvent(ev)
