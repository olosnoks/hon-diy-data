"""The CYD black box (a page of F8 Setup; F10 before the redesign): manage the CYD handheld over its
USB-C cable, from inside the app. On screen it is the "CYD black box": the CYD is the black box.

One Connect button for the whole tab (the port stays open while you move between the sub-views):
Status (ECU link, GPS, SD card, drive, clock), SD card (browse, download, upload, delete),
Config (HANDHELD.CFG as a form or as text), Map (upload the offline road map, with resume),
Console (the handheld's log, send a line, record to a file) and Install firmware (esptool, with a
chip check). The serial work runs on ``HlWorker``'s thread: nothing here waits for the port.

Widgets are stock Qt styled from the theme tokens (2 px square borders, the same colours), so the tab
looks like the rest of the app in Dracula and Darker.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QScrollArea, QSizePolicy, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ...handheld import cfgtext, flasher
from ...handheld import hl_proto as H
from ...handheld.hl_client import MapFolderError, map_folder_files
from ...handheld.mapfile import DEFAULT_CFG
from ..controls import Button, Segment
from ..handheld_worker import HlWorker
from ..theme import T

VIEWS = (("status", "Status"), ("files", "SD card"), ("config", "Config"), ("map", "Map"),
         ("console", "Console"), ("install", "Install firmware"))
KNOWN_KEYS = {e.key for e in cfgtext.parse(DEFAULT_CFG)}


# ---- pure helpers (tested without a window) --------------------------------------------------

def fmt_bytes(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


def fmt_dur(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def progress_text(done: int, total: int, elapsed: float) -> tuple[float, str]:
    """(percent, 'x.x of y.y MB · elapsed · ~eta left') for a transfer."""
    pct = 100.0 * done / total if total else 100.0
    text = f"{done / 1048576:.1f} of {total / 1048576:.1f} MB · {fmt_dur(elapsed)}"
    if done and done < total and elapsed > 2:
        text += f" · ~{fmt_dur(elapsed * (total - done) / done)} left"
    return pct, text


def status_rows(st: dict[str, Any]) -> list[tuple[str, str, str, bool | None]]:
    """The HL STAT snapshot as (section, label, value, good). ``good`` None = neutral. The marks (OK / NO / --)
    carry the meaning, the colour only backs them up."""
    link, ecu, gps, sd, drive, tm, ser = (st.get(k, {}) for k in ("link", "ecu", "gps", "sd", "drive", "time", "serial"))
    fix = bool(gps.get("fix"))
    src = {"gps": "GPS", "host": "set from this PC", "none": "not set"}.get(tm.get("source", "none"), "?")
    ms = tm.get("unix_ms", 0)
    clock = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ms / 1000)) if ms else "not known"
    return [
        ("ECU link", "link", f"{'OK' if link.get('state') == 2 else 'NO'}  {link.get('name', '?')}", link.get("state") == 2),
        ("ECU link", "read rate", f"{link.get('hz', 0):.1f} Hz fast cycle", None),
        ("ECU link", "reads / errors", f"{link.get('reads', 0)} / {link.get('errors', 0)}", None),
        ("ECU link", "ECU id", ecu.get("id") or "not read yet", None),
        ("GPS", "fix", f"{'OK' if fix else 'NO'}  {'fix' if fix else 'no fix'}, {gps.get('sats', 0)} satellites", fix),
        ("GPS", "position", f"{gps.get('lat', 0):.5f}, {gps.get('lon', 0):.5f}" if fix else "--", None),
        ("GPS", "speed / course", f"{gps.get('speed_kmh', 0):.0f} km/h, {gps.get('course', 0):.0f} deg" if fix else "--", None),
        ("GPS", "sentences good / bad", f"{gps.get('nmea_good', 0)} / {gps.get('nmea_bad', 0)}", None),
        ("SD card", "card", f"{'OK' if sd.get('ok') else 'NO'}  {'present' if sd.get('ok') else 'missing'}", bool(sd.get("ok"))),
        ("SD card", "free", f"{sd.get('free_mb', 0)} MB" if sd.get("ok") else "--", None),
        ("Drive", "recording", (f"OK  D{drive.get('no', 0):06d}, {fmt_bytes(drive.get('bytes', 0))}" if drive.get("recording")
                                else "--  not recording"), True if drive.get("recording") else None),
        ("Drive", "markers / stalls", f"{drive.get('markers', 0)} / {drive.get('stalls', 0)}", None),
        ("Drive", "stall catcher", "armed" if drive.get("armed") else "not armed", None),
        ("Clock", "time", clock, bool(ms)),
        ("Clock", "source", src, tm.get("source") == "gps"),
        ("Link to this PC", "speed", f"{ser.get('baud', 0)} baud", None),
        ("Link to this PC", "frames sent / dropped", f"{ser.get('tx_frames', 0)} / {ser.get('tx_dropped', 0)}", None),
    ]


def upload_target(local: str, selected_dir: str | None) -> tuple[str | None, str]:
    """Where a local file goes on the card: (path, why not). HANDHELD.CFG / HANDHELD.MAP at the root, anything else
    into MAP/ (the only folder the handheld lets you write to)."""
    name = Path(local).name.upper()
    if name in H.WRITE_FILES:
        return "/" + name, ""
    path = f"/MAP/{name}"
    parts = H.split_path(path)
    if parts is None:
        return None, f"{Path(local).name}: the card wants 8.3 names (letters, digits, _ and . up to 12 characters)"
    if selected_dir not in (None, "/", "/MAP"):
        return path, ""                     # the handheld only takes MAP/; the selection does not change that
    return path, ""


def style_sheet() -> str:
    t = T.tokens
    return f"""
    QWidget#hhroot, QWidget#hhpage, QFrame#hhbox {{ background: {t.bg}; color: {t.fg}; }}
    QLabel {{ color: {t.fg}; background: transparent; }}
    QLabel[dim="true"] {{ color: {t.fg2}; }}
    QLabel[big="true"] {{ font-size: 28px; font-weight: 800; }}
    QFrame#hhbox {{ border: 2px solid {t.rule}; background: {t.panel}; }}
    QLineEdit, QPlainTextEdit, QTreeWidget, QComboBox {{
        background: {t.bg}; color: {t.fg}; border: 2px solid {t.rule}; border-radius: 0; padding: 4px 8px;
        selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 3px solid {t.accent}; padding: 3px 7px; }}
    QTreeWidget {{ padding: 0; alternate-background-color: {t.sunk}; }}
    QTreeWidget::item {{ padding: 4px; }}
    QTreeWidget::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
    QHeaderView::section {{ background: {t.sunk}; color: {t.fg}; border: 0; border-right: 2px solid {t.rule};
                            border-bottom: 2px solid {t.rule}; padding: 6px 8px; font-weight: 700; }}
    QComboBox QAbstractItemView {{ background: {t.bg}; color: {t.fg}; selection-background-color: {t.accent};
                                   selection-color: {t.on_accent}; border: 2px solid {t.rule}; }}
    QProgressBar {{ border: 2px solid {t.rule}; background: {t.bg}; height: 22px; text-align: center; }}
    QProgressBar::chunk {{ background: {t.accent}; }}
    QProgressBar[kind="ok"]::chunk {{ background: {t.ok}; }}
    QProgressBar[kind="err"]::chunk {{ background: {t.alarm}; }}
    QCheckBox {{ color: {t.fg}; spacing: 8px; }}
    QCheckBox::indicator {{ width: 20px; height: 20px; border: 2px solid {t.rule}; background: {t.bg}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; }}
    QScrollArea {{ border: 0; background: {t.bg}; }}
    QScrollBar:vertical {{ background: {t.sunk}; width: 16px; }}
    QScrollBar::handle:vertical {{ background: {t.rule2}; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    """


def label(text: str = "", dim: bool = False, big: bool = False, mono: bool = False, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("dim", dim)
    lab.setProperty("big", big)
    lab.setFont(T.font(14, 700 if not dim else 600, mono=mono))
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def box() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("hhbox")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(6)
    return frame, lay


class ProgressBlock(QWidget):
    """Percentage, stage, a bar, and a detail line: MB, elapsed, ETA. No modal dialog. Hidden until used."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        top = QHBoxLayout()
        self.pct = label("", big=True)
        self.stage = label("", wrap=True)
        top.addWidget(self.pct)
        top.addWidget(self.stage, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.detail = label("", dim=True, wrap=True)
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addWidget(self.detail)
        self.hide()

    def show_state(self, stage: str, pct: float | None = None, detail: str = "", kind: str = "") -> None:
        """kind: '' working, 'ok' done, 'err' failed. The words carry it too: 'Done', 'Failed'."""
        self.show()
        self.stage.setText(stage)
        self.pct.setText("" if pct is None else f"{pct:.0f}%")
        if pct is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(100.0, pct)) * 10))
        self.detail.setText(detail)
        self.bar.setProperty("kind", kind)
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)


# ---- sub-views ---------------------------------------------------------------------------------

class _View(QWidget):
    def __init__(self, tab: "HandheldScreen"):
        super().__init__()
        self.setObjectName("hhpage")
        self.tab = tab
        self.setAttribute(Qt.WA_StyledBackground, True)

    def on_connected(self, info: dict) -> None:
        pass

    def on_disconnected(self) -> None:
        pass

    def on_shown(self) -> None:
        pass


class StatusView(_View):
    def __init__(self, tab):
        super().__init__(tab)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("hhpage")
        self.grid = QGridLayout(inner)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self.sections: dict[str, tuple[QFrame, QFormLayout]] = {}
        self.values: dict[tuple[str, str], QLabel] = {}
        self.info = label("", dim=True, wrap=True)
        outer.addWidget(self.info)
        row = QHBoxLayout()
        self.mark_btn = Button("Drop a marker")
        self.time_btn = Button("Set clock from this PC")
        self.reboot_btn = Button("Restart the CYD")
        for b in (self.mark_btn, self.time_btn, self.reboot_btn):
            row.addWidget(b)
        row.addStretch(1)
        outer.addLayout(row)
        self.mark_btn.clicked.connect(self.mark)
        self.time_btn.clicked.connect(self.set_time)
        self.reboot_btn.clicked.connect(self.reboot)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self._inflight = False
        self.last: dict | None = None
        self.show_rows(status_rows({}))
        self.sync_enabled()

    def sync_enabled(self) -> None:
        on = self.tab.connected
        for b in (self.mark_btn, self.time_btn, self.reboot_btn):
            b.setEnabled(on)

    def show_rows(self, rows) -> None:
        for section, name, value, good in rows:
            if section not in self.sections:
                frame, _ = box()
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignLeft)
                frame.layout().addWidget(label(section.upper(), dim=True))
                frame.layout().addLayout(form)
                n = len(self.sections)
                self.grid.addWidget(frame, n // 3, n % 3)
                self.sections[section] = (frame, form)
            _, form = self.sections[section]
            key = (section, name)
            if key not in self.values:
                lab = label("", mono=True, wrap=True)
                self.values[key] = lab
                form.addRow(label(name, dim=True), lab)
            lab = self.values[key]
            lab.setText(value)
            lab.setProperty("dim", good is None and value.startswith("--"))

    def on_shown(self) -> None:
        if self.tab.connected:
            self.timer.start()
            self.poll()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def on_connected(self, info) -> None:
        self.sync_enabled()
        if self.isVisible():
            self.on_shown()

    def on_disconnected(self) -> None:
        self.timer.stop()
        self.sync_enabled()
        self.info.setText("Not connected.")
        self.show_rows(status_rows({}))

    def poll(self) -> None:
        if self._inflight or not self.tab.connected or self.tab.worker.busy:
            return
        self._inflight = True
        self.tab.run(lambda c, p: c.stat(), self._got, quiet=True)

    def _got(self, st, err) -> None:
        self._inflight = False
        if err or not st:
            if err:
                self.info.setText(f"Status: {err}")
            return
        self.last = st
        self.show_rows(status_rows(st))
        hello = self.tab.info
        self.info.setText(f"{hello.get('id', '?')} · firmware {hello.get('fw', '?')} · {hello.get('variant', '?')} build · "
                          f"map #{hello.get('map', '?')} · refreshed {time.strftime('%H:%M:%S')}")

    def mark(self) -> None:
        self.tab.run(lambda c, p: c.mark(), lambda r, e: self.tab.say(e or "Marker set on the CYD"))

    def set_time(self) -> None:
        def done(r, e):
            self.tab.say(e or ("Clock set from this PC" if r == "ok" else "The CYD keeps GPS time: clock not changed"))
        self.tab.run(lambda c, p: c.set_time(), done)

    def reboot(self) -> None:
        if QMessageBox.question(self, "Restart the CYD black box", "Restart the CYD now?") != QMessageBox.Yes:
            return
        self.tab.run(lambda c, p: c.reboot(), lambda r, e: self.tab.say(e or "The CYD is restarting"))


class FilesView(_View):
    def __init__(self, tab):
        super().__init__(tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        self.refresh_btn = Button("Refresh")
        self.down_btn = Button("Download", kind="primary")
        self.up_btn = Button("Upload")
        self.del_btn = Button("Delete")
        for b in (self.refresh_btn, self.down_btn, self.up_btn, self.del_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setFont(T.font(14, 600, mono=True))
        lay.addWidget(self.tree, 1)
        self.prog = ProgressBlock()
        lay.addWidget(self.prog)
        self.hint = label("Upload writes to MAP/ and the two settings files (HANDHELD.CFG, HANDHELD.MAP); delete works in "
                          "MAP/ and DRIVES/. The drive being recorded cannot be read or deleted: end it first.", dim=True,
                          wrap=True)
        lay.addWidget(self.hint)
        self.refresh_btn.clicked.connect(self.reload)
        self.down_btn.clicked.connect(self.download)
        self.up_btn.clicked.connect(self.upload)
        self.del_btn.clicked.connect(self.delete)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self.sync_enabled)
        self.sync_enabled()

    def selected(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def sync_enabled(self) -> None:
        on = self.tab.connected
        it = self.selected()
        is_file = it is not None and it.data(0, Qt.UserRole + 1) == "file"
        self.refresh_btn.setEnabled(on)
        self.up_btn.setEnabled(on)
        self.down_btn.setEnabled(on and is_file)
        self.del_btn.setEnabled(on and is_file)

    def on_connected(self, info) -> None:
        self.sync_enabled()
        self.reload()

    def on_disconnected(self) -> None:
        self.tree.clear()
        self.sync_enabled()
        self.prog.hide()

    def reload(self) -> None:
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, ["/", "", ""])
        root.setData(0, Qt.UserRole, "/")
        root.setData(0, Qt.UserRole + 1, "dir")
        root.addChild(QTreeWidgetItem(["…"]))
        self.tree.expandItem(root)

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, Qt.UserRole + 1) != "dir" or item.data(0, Qt.UserRole + 2):
            return
        item.setData(0, Qt.UserRole + 2, True)
        path = item.data(0, Qt.UserRole)
        self.tab.run(lambda c, p: c.ls(path), lambda r, e: self._listed(item, r, e))

    def _listed(self, item: QTreeWidgetItem, result, err) -> None:
        item.takeChildren()
        if err or result is None:
            item.setData(0, Qt.UserRole + 2, False)
            self.tab.say(f"List failed: {err}")
            return
        files, dirs = result
        for d in dirs:
            child = QTreeWidgetItem(item, [Path(d).name + "/", "", ""])
            child.setData(0, Qt.UserRole, d)
            child.setData(0, Qt.UserRole + 1, "dir")
            child.addChild(QTreeWidgetItem(["…"]))
        for name, size, mtime in files:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else ""
            child = QTreeWidgetItem(item, [Path(name).name, fmt_bytes(size), when])
            child.setData(0, Qt.UserRole, name)
            child.setData(0, Qt.UserRole + 1, "file")
            child.setData(0, Qt.UserRole + 3, size)
            child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

    def _parent_dir(self) -> str:
        it = self.selected()
        if it is None:
            return "/"
        path = it.data(0, Qt.UserRole)
        return path if it.data(0, Qt.UserRole + 1) == "dir" else str(Path(path).parent)

    def download(self) -> None:
        it = self.selected()
        if it is None or it.data(0, Qt.UserRole + 1) != "file":
            return
        path, size = it.data(0, Qt.UserRole), it.data(0, Qt.UserRole + 3)
        dest, _ = QFileDialog.getSaveFileName(self, "Save as", str(Path.home() / Path(path).name))
        if not dest:
            return
        t0 = time.monotonic()

        def work(c, p):
            data = c.get(path, lambda d, t: p(d, t, path))
            Path(dest).write_bytes(data)
            return len(data)

        def prog(job, d, t, what):
            pct, text = progress_text(d, t or size, time.monotonic() - t0)
            self.prog.show_state(f"Downloading {Path(path).name}", pct, text)

        def done(r, e):
            if e:
                self.prog.show_state(f"Failed: {Path(path).name}", None, e, "err")
            else:
                self.prog.show_state(f"Done: {Path(path).name}", 100, f"{fmt_bytes(r)} saved to {dest}", "ok")
        self.tab.run(work, done, progress=prog)

    def upload(self) -> None:
        local, _ = QFileDialog.getOpenFileName(self, "Upload to the CYD")
        if not local:
            return
        target, why = upload_target(local, self._parent_dir())
        if target is None:
            self.tab.say(why)
            return
        data = Path(local).read_bytes()
        t0 = time.monotonic()

        def prog(job, d, t, what):
            pct, text = progress_text(d, t, time.monotonic() - t0)
            self.prog.show_state(f"Uploading {Path(local).name} to {target}", pct, text)

        def done(r, e):
            if e:
                self.prog.show_state(f"Failed: {target}", None, e, "err")
            else:
                self.prog.show_state(f"Done: {target}", 100, f"{fmt_bytes(len(data))}" + (f" · {r}" if r else ""), "ok")
                self.reload()
        self.tab.run(lambda c, p: c.put(target, data, lambda d, t: p(d, t, target)), done, progress=prog)

    def delete(self) -> None:
        it = self.selected()
        if it is None or it.data(0, Qt.UserRole + 1) != "file":
            return
        path = it.data(0, Qt.UserRole)
        parts = H.split_path(path)
        if parts is None or not H.can_remove(parts):
            self.tab.say("The CYD lets MAP/, DRIVES/ and the settings files be deleted, not this one.")
            return
        if QMessageBox.question(self, "Delete", f"Delete {path} from the CYD's SD card?") != QMessageBox.Yes:
            return

        def done(r, e):
            self.tab.say(e or f"{path} deleted")
            if not e:
                (it.parent() or self.tree.invisibleRootItem()).removeChild(it)
        self.tab.run(lambda c, p: c.rm(path), done)


class ConfigView(_View):
    def __init__(self, tab):
        super().__init__(tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        self.mode = Segment([("form", "Form"), ("raw", "Raw text")])
        self.read_btn = Button("Read from the CYD")
        self.write_btn = Button("Write to the CYD", kind="primary")
        self.default_btn = Button("Defaults")
        self.reboot_btn = Button("Restart the CYD")
        row.addWidget(self.mode)
        for b in (self.read_btn, self.write_btn, self.default_btn, self.reboot_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("hhpage")
        self.form = QFormLayout(inner)
        self.form.setLabelAlignment(Qt.AlignLeft)
        scroll.setWidget(inner)
        self.raw = QPlainTextEdit()
        self.raw.setFont(T.font(14, 600, mono=True))
        self.stack.addWidget(scroll)
        self.stack.addWidget(self.raw)
        lay.addWidget(self.stack, 1)
        self.note = label("Read the file from the CYD, change it, write it back. The CYD checks the file "
                          "(length and checksum) before it replaces the old one, which it keeps as HANDHELD.CBK. "
                          "Settings are used from the next power-up.", dim=True, wrap=True)
        lay.addWidget(self.note)
        self.prog = ProgressBlock()
        lay.addWidget(self.prog)
        self.edits: dict[str, QLineEdit] = {}
        self.text = DEFAULT_CFG
        self._syncing = False
        self.mode.picked.connect(self.set_mode)
        self.read_btn.clicked.connect(self.read)
        self.write_btn.clicked.connect(self.write)
        self.default_btn.clicked.connect(self.defaults)
        self.reboot_btn.clicked.connect(self.reboot)
        self.raw.textChanged.connect(self._raw_changed)
        self.set_text(DEFAULT_CFG)
        self.sync_enabled()

    def sync_enabled(self) -> None:
        on = self.tab.connected
        for b in (self.read_btn, self.write_btn, self.reboot_btn):
            b.setEnabled(on)

    def set_mode(self, which: str) -> None:
        self.stack.setCurrentIndex(0 if which == "form" else 1)
        if which == "form":
            self._rebuild_form()

    def set_text(self, text: str) -> None:
        self.text = text
        self._syncing = True
        self.raw.setPlainText(text)
        self._syncing = False
        self._rebuild_form()

    def _raw_changed(self) -> None:
        if self._syncing:
            return
        self.text = self.raw.toPlainText()
        if self.stack.currentIndex() == 0:
            self._rebuild_form()

    def _rebuild_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.edits.clear()
        for e in cfgtext.parse(self.text):
            edit = QLineEdit(e.value)
            edit.setFont(T.font(14, 700, mono=True))
            edit.setPlaceholderText("(not set: the CYD's own default)" if not e.enabled else "")
            if not e.enabled:
                edit.setText("")
            edit.setToolTip(e.comment)
            edit.editedChanged = None  # type: ignore[attr-defined]
            edit.textEdited.connect(lambda v, k=e.key, en=e.enabled: self._form_edit(k, v))
            self.edits[e.key] = edit
            self.form.addRow(label(e.key, mono=True), self._with_comment(edit, e.comment))

    @staticmethod
    def _with_comment(edit: QLineEdit, comment: str) -> QWidget:
        w = QWidget()
        w.setObjectName("hhpage")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        edit.setMinimumWidth(200)
        edit.setMaximumWidth(280)
        h.addWidget(edit)
        h.addWidget(label(comment, dim=True, wrap=True), 1)
        return w

    def _form_edit(self, key: str, value: str) -> None:
        self.text = cfgtext.set_value(self.text, key, value.strip(), enable=bool(value.strip()))
        self._syncing = True
        self.raw.setPlainText(self.text)
        self._syncing = False

    def read(self) -> None:
        t0 = time.monotonic()

        def prog(job, d, t, what):
            pct, text = progress_text(d, t, time.monotonic() - t0)
            self.prog.show_state("Reading HANDHELD.CFG", pct, text)

        def done(r, e):
            if e:
                self.prog.show_state("Failed: could not read HANDHELD.CFG", None,
                                     e + (". The card has none yet: the defaults are shown, write them to create it."
                                          if "no such file" in e else ""), "err")
                return
            self.set_text(r.decode("utf-8", "replace"))
            self.prog.show_state("Done: HANDHELD.CFG read", 100, f"{len(r)} bytes", "ok")
        self.tab.run(lambda c, p: c.cfg_get(lambda d, t: p(d, t, "cfg")), done, progress=prog)

    def write(self) -> None:
        bad = cfgtext.problems(self.text, KNOWN_KEYS)
        if bad and QMessageBox.question(self, "Write anyway?", "The CYD will skip these lines:\n\n" + "\n".join(bad[:8])
                                        + "\n\nWrite the file anyway?") != QMessageBox.Yes:
            return
        data = self.text.encode("utf-8")
        t0 = time.monotonic()

        def prog(job, d, t, what):
            pct, text = progress_text(d, t, time.monotonic() - t0)
            self.prog.show_state("Writing HANDHELD.CFG", pct, text)

        def done(r, e):
            if e:
                self.prog.show_state("Failed: HANDHELD.CFG not changed", None, e, "err")
            else:
                self.prog.show_state("Done: HANDHELD.CFG written", 100,
                                     f"{r or 'accepted'}. Restart the CYD to use it.", "ok")
        self.tab.run(lambda c, p: c.cfg_put(data, lambda d, t: p(d, t, "cfg")), done, progress=prog)

    def defaults(self) -> None:
        self.set_text(DEFAULT_CFG)

    def reboot(self) -> None:
        if QMessageBox.question(self, "Restart the CYD black box", "Restart the CYD now?") == QMessageBox.Yes:
            self.tab.run(lambda c, p: c.reboot(), lambda r, e: self.tab.say(e or "The CYD is restarting"))

    def on_connected(self, info) -> None:
        self.sync_enabled()

    def on_disconnected(self) -> None:
        self.sync_enabled()


class MapView(_View):
    def __init__(self, tab, settings: QSettings):
        super().__init__(tab)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        self.path = QLineEdit(str(settings.value("handheld/map_folder", "")))
        self.path.setPlaceholderText("the MAP folder made by hondalog-handheld map-build (or the card folder that holds it)")
        self.browse_btn = Button("Choose MAP folder")
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_btn)
        lay.addLayout(row)
        frame, flay = box()
        self.summary = label("No folder chosen.", wrap=True, mono=True)
        flay.addWidget(self.summary)
        lay.addWidget(frame)
        row = QHBoxLayout()
        self.up_btn = Button("Upload map", kind="primary")
        self.cancel_btn = Button("Cancel")
        row.addWidget(self.up_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.prog = ProgressBlock()
        lay.addWidget(self.prog)
        self.log = label("", dim=True, wrap=True)
        lay.addWidget(self.log)
        lay.addStretch(1)
        self.note = label("Sent over the fast binary link, each file is checked before it replaces the old one, and an "
                          "interrupted upload carries on from what the CYD already holds when you press Upload "
                          "again. The Map screen reads the new map the next time it opens.", dim=True, wrap=True)
        lay.addWidget(self.note)
        self.files: list[tuple[str, bytes]] = []
        self.browse_btn.clicked.connect(self.browse)
        self.path.editingFinished.connect(self.check)
        self.up_btn.clicked.connect(self.upload)
        self.cancel_btn.clicked.connect(self.tab.worker.cancel)
        self.running = False
        self.sync_enabled()
        if self.path.text():
            self.check()

    def sync_enabled(self) -> None:
        self.up_btn.setEnabled(self.tab.connected and bool(self.files) and not self.running)
        self.cancel_btn.setEnabled(self.running)

    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the MAP folder", self.path.text() or str(Path.home()))
        if d:
            self.path.setText(d)
            self.check()

    def check(self) -> None:
        self.files = []
        text = self.path.text().strip()
        if not text:
            self.summary.setText("No folder chosen.")
        else:
            try:
                self.files = map_folder_files(text)
                self.settings.setValue("handheld/map_folder", text)
                import struct
                import datetime as dt
                idx = dict(self.files)["MAP.IDX"]
                build = dt.datetime.fromtimestamp(struct.unpack_from("<q", idx, 88)[0], dt.timezone.utc).strftime("%Y-%m-%d")
                src = idx[120:216].split(b"\0")[0].decode("utf-8", "replace")
                total = sum(len(d) for _, d in self.files)
                self.summary.setText("OK  " + ", ".join(f"{n} {fmt_bytes(len(d))}" for n, d in self.files)
                                     + f"\nbuilt {build}; {src}\ntotal {fmt_bytes(total)}")
            except (MapFolderError, OSError, struct_error) as exc:
                self.summary.setText(f"NO  {exc}")
        self.sync_enabled()

    def on_connected(self, info) -> None:
        self.sync_enabled()

    def on_disconnected(self) -> None:
        self.sync_enabled()   # a running upload reports its own end (the port is released while it runs)

    def upload(self) -> None:
        if not self.files or self.running:
            return
        self.running = True
        self.sync_enabled()
        files = list(self.files)
        total = sum(len(d) for _, d in files)
        t0 = time.monotonic()
        state = {"start": None, "name": ""}
        retries: list[str] = []

        folder = self.path.text().strip()

        def prog(job, done, tot, name):
            if state["start"] is None:
                state["start"] = done                       # a resumed upload starts part-way: the ETA counts from there
                t0_local[0] = time.monotonic()
            pct, _ = progress_text(done, tot, 1)
            el = time.monotonic() - t0_local[0]
            sofar = done - state["start"]
            eta = f" · ~{fmt_dur(el * (tot - done) / sofar)} left" if sofar > 0 and el > 2 and done < tot else ""
            rate = f" · {sofar / max(el, 1e-6) / 1024:.0f} kB/s" if sofar > 0 and el > 1 else ""
            resumed = f" (resumed at {fmt_bytes(state['start'])})" if state["start"] else ""
            self.prog.show_state(f"Uploading {name}{resumed}", pct,
                                 f"{done / 1048576:.1f} of {tot / 1048576:.1f} MB · {fmt_dur(time.monotonic() - t0)}{eta}{rate}")
            if retries:
                self.log.setText(retries[-1])

        t0_local = [time.monotonic()]

        def done(r, e):
            self.running = False
            self.sync_enabled()
            if e:
                self.prog.show_state("Stopped" if e == "cancelled" else "Failed", None,
                                     e + ". Press Upload map to carry on from where it stopped.", "err")
            else:
                self.prog.show_state("Done: map uploaded", 100,
                                     f"{fmt_bytes(total)} in {fmt_dur(time.monotonic() - t0)}", "ok")
        job = self.tab.worker.map_upload(folder)
        self.tab._progress[job] = prog
        self.tab.expect(job, done)


try:
    from struct import error as struct_error
except ImportError:                                                   # pragma: no cover
    struct_error = ValueError


class ConsoleView(_View):
    MAX_LINES = 5000

    def __init__(self, tab):
        super().__init__(tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        self.rec = QCheckBox("Record to file")
        self.clear_btn = Button("Clear")
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        row.addWidget(self.rec)
        row.addWidget(self.follow)
        row.addWidget(self.clear_btn)
        self.rec_label = label("", dim=True)
        row.addWidget(self.rec_label, 1)
        lay.addLayout(row)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.MAX_LINES)
        self.view.setFont(T.font(13, 600, mono=True))
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.line = QLineEdit()
        self.line.setFont(T.font(14, 700, mono=True))
        self.line.setPlaceholderText("a line to send, e.g.  HL STAT   (it goes out as typed; replies appear above)")
        self.send_btn = Button("Send", kind="primary")
        row.addWidget(self.line, 1)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self.file = None
        self.rec.toggled.connect(self.toggle_record)
        self.clear_btn.clicked.connect(self.view.clear)
        self.send_btn.clicked.connect(self.send)
        self.line.returnPressed.connect(self.send)
        self.sync_enabled()

    def sync_enabled(self) -> None:
        self.send_btn.setEnabled(self.tab.connected)
        self.line.setEnabled(self.tab.connected)

    def add(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.view.appendPlainText(f"{stamp}  {text}")
        if self.follow.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())
        if self.file is not None:
            try:
                self.file.write(f"{stamp}  {text}\n")
                self.file.flush()
            except OSError as exc:
                self.rec.setChecked(False)
                self.tab.say(f"Recording stopped: {exc}")

    def toggle_record(self, on: bool) -> None:
        if on:
            default = str(Path.home() / f"handheld-console-{time.strftime('%Y%m%d-%H%M%S')}.log")
            name, _ = QFileDialog.getSaveFileName(self, "Record the console to", default)
            if not name:
                self.rec.blockSignals(True)
                self.rec.setChecked(False)
                self.rec.blockSignals(False)
                return
            self.start_recording(name)
        else:
            self.stop_recording()

    def start_recording(self, name: str) -> None:
        try:
            self.file = open(name, "a", encoding="utf-8")
        except OSError as exc:
            self.tab.say(f"Cannot record to {name}: {exc}")
            return
        self.file.write("".join(self.view.toPlainText().splitlines(keepends=True)[-0:]))
        self.rec_label.setText(f"recording to {name}")
        self.rec.blockSignals(True)
        self.rec.setChecked(True)
        self.rec.blockSignals(False)

    def stop_recording(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        self.rec_label.setText("")

    def send(self) -> None:
        text = self.line.text().strip()
        if not text or not self.tab.connected:
            return
        self.add("> " + text)
        self.tab.worker.send_line(text)
        self.line.clear()

    def on_connected(self, info) -> None:
        self.add(f"connected: {info.get('id', '?')} fw {info.get('fw', '?')} ({info.get('variant', '?')} build)")
        self.sync_enabled()

    def on_disconnected(self) -> None:
        self.sync_enabled()


class InstallView(_View):
    def __init__(self, tab, settings: QSettings):
        super().__init__(tab)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        self.path = QLineEdit(str(settings.value("handheld/image", "")))
        self.path.setPlaceholderText("the merged firmware image (.bin), written at 0x0")
        self.browse_btn = Button("Choose image")
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_btn)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label("Speed"))
        self.baud = QComboBox()
        for b in flasher.BAUDS:
            self.baud.addItem(f"{b} baud", b)
        self.baud.setCurrentIndex(max(0, self.baud.findData(int(settings.value("handheld/flash_baud", flasher.DEFAULT_BAUD)))))
        row.addWidget(self.baud)
        self.install_btn = Button("Install", kind="primary")
        self.cancel_btn = Button("Cancel")
        row.addWidget(self.install_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        lay.addLayout(row)
        frame, flay = box()
        self.guard = label("", wrap=True)
        flay.addWidget(self.guard)
        lay.addWidget(frame)
        self.prog = ProgressBlock()
        lay.addWidget(self.prog)
        lay.addStretch(1)
        lay.addWidget(label("The tab lets go of the serial port while the board is flashed and connects again once it has "
                            "restarted. Only an ESP32-S3 is written: another chip is refused before anything is erased. If the "
                            "board will not enter its bootloader, hold BOOT, tap RESET, release BOOT, and press Install again. "
                            "Connect first (so the tab knows the port).", dim=True, wrap=True))
        self.browse_btn.clicked.connect(self.browse)
        self.path.editingFinished.connect(self.check)
        self.install_btn.clicked.connect(self.install)
        self.cancel_btn.clicked.connect(self.tab.worker.cancel)
        self.running = False
        tab.worker.flash_progress.connect(self._progress)
        self.check()

    def sync_enabled(self) -> None:
        ok = self.tab.connected and self.image_ok and not self.running
        self.install_btn.setEnabled(ok)
        self.cancel_btn.setEnabled(self.running)

    image_ok = False

    def browse(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Choose the merged firmware image", self.path.text() or str(Path.home()),
                                              "Firmware (*.bin);;All files (*)")
        if name:
            self.path.setText(name)
            self.check()

    def check(self) -> None:
        text = self.path.text().strip()
        self.image_ok = False
        if not text:
            self.guard.setText("No image chosen.")
        else:
            try:
                data = flasher.check_image(text)
                self.image_ok = True
                self.settings.setValue("handheld/image", text)
                self.guard.setText(f"OK  {Path(text).name}: {fmt_bytes(len(data))}, merged image (partition table at 0x8000), "
                                   f"target {flasher.CHIP_NAME}")
            except flasher.FlashError as exc:
                self.guard.setText(f"NO  {exc}")
        if flasher.esptool_command() is None:
            self.guard.setText(self.guard.text() + "\nNO  the esptool package is not installed: pip install esptool")
            self.image_ok = False
        self.sync_enabled()

    def install(self) -> None:
        if not (self.tab.connected and self.image_ok) or self.running:
            return
        if QMessageBox.question(self, "Install firmware", "Write this image to the CYD now? Do not unplug it "
                                "until it is done.") != QMessageBox.Yes:
            return
        self.running = True
        self.sync_enabled()
        self.settings.setValue("handheld/flash_baud", self.baud.currentData())
        self.prog.show_state("Starting", None, "")
        job = self.tab.worker.flash(self.path.text().strip(), int(self.baud.currentData()))
        self.tab.expect(job, self._done)

    def _progress(self, p: "flasher.Progress") -> None:
        if p.stage == "Failed":
            self.prog.show_state("Failed", None, p.text, "err")
            return
        detail = ""
        if p.total:
            detail = f"{p.written / 1048576:.2f} of {p.total / 1048576:.2f} MB · {fmt_dur(p.elapsed)}"
            if p.eta:
                detail += f" · ~{fmt_dur(p.eta)} left"
            detail += " · do not unplug"
        self.prog.show_state(p.stage, p.pct, detail or p.text)

    def _done(self, result, err) -> None:
        self.running = False
        self.sync_enabled()
        if err:
            self.prog.show_state("Failed", None, err, "err")
        else:
            self.prog.show_state("Done", 100, "The board is running the new firmware and the tab is connected to it again.", "ok")

    def on_connected(self, info) -> None:
        self.sync_enabled()

    def on_disconnected(self) -> None:
        self.sync_enabled()


# ---- the tab ---------------------------------------------------------------------------------

class HandheldScreen(QWidget):
    message = Signal(str)

    def __init__(self, session, settings: QSettings | None = None, port_factory: Callable[[str], Any] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("hhroot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.session = session
        self.settings = settings if settings is not None else QSettings("hondalog", "hondalog")
        self.worker = HlWorker(port_factory, self)
        self.connected = False
        self.info: dict[str, str] = {}
        self._callbacks: dict[int, Callable[[Any, str], None]] = {}
        self._progress: dict[int, Callable[..., None]] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("hhpage")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 6)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(380)
        self.port.setFont(T.font(14, 600, mono=True))
        self.rescan_btn = Button("Rescan")
        self.connect_btn = Button("Connect", kind="primary")
        self.state_label = label("Not connected", wrap=False)
        row.addWidget(label("Port"))
        row.addWidget(self.port, 1)
        row.addWidget(self.rescan_btn)
        row.addWidget(self.connect_btn)
        row.addWidget(self.state_label, 2)
        lay.addWidget(bar)
        seg = QWidget()
        seg.setObjectName("hhpage")
        seg.setAttribute(Qt.WA_StyledBackground, True)
        srow = QHBoxLayout(seg)
        srow.setContentsMargins(12, 0, 12, 8)
        self.segment = Segment(list(VIEWS), "status")
        srow.addWidget(self.segment)
        lay.addWidget(seg)
        self.stack = QStackedWidget()
        self.status_view = StatusView(self)
        self.files_view = FilesView(self)
        self.config_view = ConfigView(self)
        self.map_view = MapView(self, self.settings)
        self.console_view = ConsoleView(self)
        self.install_view = InstallView(self, self.settings)
        self.views = [self.status_view, self.files_view, self.config_view, self.map_view, self.console_view,
                      self.install_view]
        for v in self.views:
            self.stack.addWidget(v)
        lay.addWidget(self.stack, 1)

        self.segment.picked.connect(self.show_view)
        self.rescan_btn.clicked.connect(self.rescan)
        self.connect_btn.clicked.connect(self.toggle_connection)
        w = self.worker
        w.state.connect(self._state)
        w.hello.connect(self._hello)
        w.console.connect(self.console_view.add)
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.start()
        self.rescan()
        self.retheme()

    # ---- jobs ----

    def run(self, fn, callback: Callable[[Any, str], None] | None = None, progress: Callable[..., None] | None = None,
            quiet: bool = False) -> int:
        job = self.worker.submit(fn)
        if callback:
            self._callbacks[job] = callback
        if progress:
            self._progress[job] = progress
        return job

    def expect(self, job: int, callback: Callable[[Any, str], None]) -> None:
        self._callbacks[job] = callback

    def _on_progress(self, job: int, done: int, total: int, what: str) -> None:
        cb = self._progress.get(job)
        if cb:
            cb(job, done, total, what)

    def _on_done(self, job: int, result, err: str) -> None:
        self._progress.pop(job, None)
        cb = self._callbacks.pop(job, None)
        if cb:
            cb(result, err)

    def say(self, text: str) -> None:
        self.message.emit(text)

    # ---- connection ----

    def rescan(self) -> None:
        from ...handheld.serial_link import list_ports
        current = self.port.currentText()
        self.port.clear()
        found = list_ports()
        for name, text in found:
            self.port.addItem(text, name)
        last = str(self.settings.value("handheld/port", ""))
        if current:
            self.port.setEditText(current)
        elif last:
            self.port.setEditText(last)
        elif found:
            self.port.setCurrentIndex(0)             # a CH340 (1a86:7523) if there is one
        self.state_label.setText("Not connected" if not self.connected else self.state_label.text())

    def port_name(self) -> str:
        text = self.port.currentText().strip()
        i = self.port.findText(text)
        if i >= 0 and self.port.itemData(i):
            return str(self.port.itemData(i))
        return text if text.startswith("cyd:") or not text else "cyd:" + text

    def toggle_connection(self) -> None:
        if self.connected or self.worker.connected:
            self.worker.disconnect_port()
            return
        name = self.port_name()
        if name and self.session.port and name.endswith(self.session.port.replace("cyd:", "")) and self.session.connected:
            self.say("The live connection on F1 has this port open: disconnect it there first.")
            return                          # one link to the CYD at a time
        self.settings.setValue("handheld/port", name)
        self.connect_btn.setEnabled(False)
        self.worker.connect_port(name)

    def _state(self, state: str, detail: str) -> None:
        self.connected = state == "connected"
        text = {"connected": f"Connected  {detail}", "connecting": f"Connecting  {detail}", "flashing": "Installing firmware",
                "disconnected": "Not connected" + (f": {detail}" if detail else "")}[state]
        self.state_label.setText(text)
        self.connect_btn.setEnabled(state in ("connected", "disconnected"))
        self.connect_btn.setText("Disconnect" if self.connected else "Connect")
        self.port.setEnabled(state == "disconnected")
        if state == "disconnected":
            self.info = {}
            for v in self.views:
                v.on_disconnected()
            if detail:
                self.say(f"CYD black box: {detail}")

    def _hello(self, info: dict) -> None:
        self.info = info
        for v in self.views:
            v.on_connected(info)
        if info.get("variant") == "gps-p1":
            self.say("This is the GPS-on-P1 build: it cannot listen.")

    # ---- views ----

    def show_view(self, which: str) -> None:
        index = [k for k, _ in VIEWS].index(which)
        self.segment.set_current(which)
        self.stack.setCurrentIndex(index)
        self.views[index].on_shown()

    def refresh(self, now: float) -> None:
        pass

    def retheme(self) -> None:
        self.setStyleSheet(style_sheet())
        for lab in self.findChildren(QLabel):
            lab.style().unpolish(lab)
            lab.style().polish(lab)
        self.update()

    def shutdown(self) -> None:
        self.console_view.stop_recording()
        self.worker.stop()
