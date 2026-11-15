"""Raw tools: hex console and the memory scanner used to map unknown ECUs."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import protocol
from ..logger import log_dir
from ..protocol import ProtocolError
from . import theme

COL_NEW = QColor("#ff8c1a")       # changed in the latest sweep
COL_SEEN = QColor("#1f4f7a")      # changed at some point since "Clear changes"
COL_NONE = QColor("#101216")


class HexSpin(QSpinBox):
    """Spin box that shows and accepts two-digit hex."""

    def __init__(self, value: int, lo: int = 0, hi: int = 0xFF):
        super().__init__()
        self.setDisplayIntegerBase(16)
        self.setPrefix("0x")
        self.setRange(lo, hi)
        self.setValue(value)

    def textFromValue(self, value: int) -> str:
        return f"{value:02X}"


class RawTools(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.poller = None
        self._pending: list = []          # (future, kind, info)
        self._scan_active = False
        self._scan_continuous = False
        self._scan_left = 0
        self._sweep_t0 = 0.0
        self._sweeps = 0
        self._history: list = []          # (wall time, 256 values) per sweep
        self._reset_arrays()

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.addWidget(self._build_console(), 2)
        root.addWidget(self._build_scanner(), 3)
        self.set_poller(None)

    def _reset_arrays(self) -> None:
        self.cur = np.full(256, -1, dtype=np.int16)
        self.minv = np.full(256, 255, dtype=np.int16)
        self.maxv = np.full(256, 0, dtype=np.int16)
        self.changes = np.zeros(256, dtype=np.int32)
        self.fresh = np.zeros(256, dtype=bool)
        self.failed = np.zeros(256, dtype=bool)

    # ---- hex console ----

    def _build_console(self) -> QWidget:
        box = QGroupBox("Hex console")
        lay = QVBoxLayout(box)
        self.console = QPlainTextEdit(readOnly=True)
        self.console.setMaximumBlockCount(2000)
        self.console.setStyleSheet(f"font-family: {theme.MONO}; font-size: 10pt;")
        self.console.setPlaceholderText(
            "Type hex bytes below, e.g.  20 05 00 10\n"
            "With 'Add checksum' ticked the last byte is worked out for you.\n"
            "TX = sent, RX = what came back after the echo.")
        lay.addWidget(self.console, 1)

        row = QHBoxLayout()
        self.hex_in = QLineEdit()
        self.hex_in.setPlaceholderText("20 05 00 10")
        self.hex_in.setStyleSheet(f"font-family: {theme.MONO}; font-size: 11pt;")
        self.hex_in.returnPressed.connect(self.send_console)
        row.addWidget(self.hex_in, 1)
        self.cksum_box = QCheckBox("Add checksum")
        self.cksum_box.setChecked(True)
        row.addWidget(self.cksum_box)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_console)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Read address"))
        self.rd_addr = HexSpin(0x00)
        row.addWidget(self.rd_addr)
        row.addWidget(QLabel("bytes"))
        self.rd_len = QSpinBox()
        self.rd_len.setRange(1, 255)
        self.rd_len.setValue(16)
        row.addWidget(self.rd_len)
        self.rd_btn = QPushButton("Read")
        self.rd_btn.clicked.connect(self._quick_read)
        row.addWidget(self.rd_btn)
        row.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.console.clear)
        row.addWidget(clear)
        lay.addLayout(row)
        return box

    def _quick_read(self) -> None:
        self.hex_in.setText(f"20 05 {self.rd_addr.value():02X} {self.rd_len.value():02X}")
        self.cksum_box.setChecked(True)
        self.send_console()

    def send_console(self) -> None:
        if self.poller is None:
            return
        try:
            data = protocol.parse_hex(self.hex_in.text())
        except ValueError:
            self._log("!! not hex: " + self.hex_in.text())
            return
        if not data:
            return
        if self.cksum_box.isChecked():
            data = protocol.with_checksum(data)
        fut = self.poller.submit(lambda link, d=data: link.raw(d))
        self._pending.append((fut, "console", data))

    def _console_done(self, data: bytes, fut) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log(f"{stamp}  TX  {protocol.fmt_hex(data)}")
        try:
            echo, rest = fut.result()
        except Exception as exc:
            self._log(f"              !!  {exc}")
            return
        framing = self.poller.amap.framing if self.poller else protocol.DEFAULT
        frame = protocol.find_any_frame(rest, framing)
        if not rest:
            note = "no reply" + ("" if echo else ", and no echo: check cable and 12 V at the plug")
        elif frame:
            note = f"checksum OK, {len(frame) - 3} data bytes"
        elif protocol.verify(rest):
            note = "sums to 00 but not the usual header"
        else:
            note = "no valid frame (checksum does not add up)"
        self._log(f"              RX  {protocol.fmt_hex(rest) or '(nothing)'}")
        self._log(f"                  [{'echo ok' if echo else 'NO ECHO'}; {note}]")

    def _log(self, text: str) -> None:
        self.console.appendPlainText(text)

    # ---- memory scanner ----

    def _build_scanner(self) -> QWidget:
        box = QGroupBox("Memory scanner")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("From"))
        self.sc_from = HexSpin(0x00)
        row.addWidget(self.sc_from)
        row.addWidget(QLabel("to"))
        self.sc_to = HexSpin(0xFF)
        row.addWidget(self.sc_to)
        row.addWidget(QLabel("chunk"))
        self.sc_chunk = QSpinBox()
        self.sc_chunk.setRange(1, 16)
        self.sc_chunk.setValue(16)
        row.addWidget(self.sc_chunk)
        self.sweep_btn = QPushButton("Sweep once")
        self.sweep_btn.clicked.connect(lambda: self.start_scan(False))
        row.addWidget(self.sweep_btn)
        self.cont_btn = QPushButton("Sweep continuously")
        self.cont_btn.setCheckable(True)
        self.cont_btn.toggled.connect(self._cont_toggled)
        row.addWidget(self.cont_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.grid = QTableWidget(16, 16)
        self.grid.setStyleSheet(f"QTableWidget {{ font-family: {theme.MONO}; font-size: 10pt; }}")
        self.grid.setHorizontalHeaderLabels([f"x{c:X}" for c in range(16)])
        self.grid.setVerticalHeaderLabels([f"{r:X}x" for r in range(16)])
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self._items = []
        for addr in range(256):
            item = QTableWidgetItem("--")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QBrush(QColor(theme.DIM)))
            self.grid.setItem(addr // 16, addr % 16, item)
            self._items.append(item)
        self.grid.currentCellChanged.connect(lambda *_: self._show_detail())
        lay.addWidget(self.grid, 1)

        self.detail = QLabel("Click a byte for details.")
        self.detail.setStyleSheet(f"font-family: {theme.MONO};")
        lay.addWidget(self.detail)

        row = QHBoxLayout()
        legend = QLabel(
            f"<span style='background:{COL_NEW.name()}; color:#000'>&nbsp;changed this sweep&nbsp;</span> "
            f"<span style='background:{COL_SEEN.name()}'>&nbsp;changed earlier&nbsp;</span> "
            f"<span style='color:{theme.BAD}'>??</span> read failed")
        row.addWidget(legend)
        self.sc_status = QLabel("")
        self.sc_status.setStyleSheet(f"color:{theme.DIM};")
        row.addWidget(self.sc_status, 1, Qt.AlignRight)
        clear = QPushButton("Clear changes")
        clear.setToolTip("Forget which bytes have changed and start watching again")
        clear.clicked.connect(self.clear_changes)
        row.addWidget(clear)
        self.export_btn = QPushButton("Export…")
        self.export_btn.clicked.connect(self.export_dialog)
        row.addWidget(self.export_btn)
        lay.addLayout(row)
        return box

    def _cont_toggled(self, on: bool) -> None:
        self._scan_continuous = on
        self.cont_btn.setText("Stop sweeping" if on else "Sweep continuously")
        if on and not self._scan_active:
            self.start_scan(True)

    def start_scan(self, continuous: bool) -> None:
        if self.poller is None or self._scan_active:
            return
        lo, hi = sorted((self.sc_from.value(), self.sc_to.value()))
        chunk = self.sc_chunk.value()
        chunks = [(a, min(chunk, hi + 1 - a)) for a in range(lo, hi + 1, chunk)]
        self._scan_active = True
        self._scan_left = len(chunks)
        self._sweep_t0 = time.monotonic()
        self.fresh[:] = False
        self.failed[:] = False
        # Queue the whole sweep; the poller interleaves it with dashboard reads.
        for addr, length in chunks:
            fut = self.poller.submit(lambda link, a=addr, n=length: link.read_mem(a, n))
            self._pending.append((fut, "scan", (addr, length)))

    def _chunk_done(self, info, fut) -> None:
        addr, length = info
        if not self._scan_active:
            return
        try:
            data = fut.result()
        except ProtocolError:
            self.failed[addr:addr + length] = True
        except Exception as exc:                       # port gone
            self._scan_active = False
            self.cont_btn.setChecked(False)
            self.sc_status.setText(f"scan stopped: {exc}")
            return
        else:
            new = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
            old = self.cur[addr:addr + length]
            changed = (old >= 0) & (old != new)
            self.fresh[addr:addr + length] = changed
            self.changes[addr:addr + length] += changed
            self.minv[addr:addr + length] = np.minimum(self.minv[addr:addr + length], new)
            self.maxv[addr:addr + length] = np.maximum(self.maxv[addr:addr + length], new)
            self.cur[addr:addr + length] = new
        self._scan_left -= 1
        if self._scan_left <= 0:
            self._sweep_finished()

    def _sweep_finished(self) -> None:
        self._scan_active = False
        self._sweeps += 1
        took = time.monotonic() - self._sweep_t0
        self._history.append((datetime.now(), self.cur.copy()))
        del self._history[:-5000]
        self._paint_grid()
        moving = int((self.changes > 0).sum())
        bad = int(self.failed.sum())
        self.sc_status.setText(f"sweep {self._sweeps}: {took:.2f} s, {moving} bytes have changed"
                               + (f", {bad} unreadable" if bad else ""))
        if self._scan_continuous and self.poller is not None:
            self.start_scan(True)

    def _paint_grid(self) -> None:
        self.grid.setUpdatesEnabled(False)
        for addr, item in enumerate(self._items):
            value = int(self.cur[addr])
            if self.failed[addr]:
                text, fg, bg = "??", theme.BAD, COL_NONE
            elif value < 0:
                text, fg, bg = "--", theme.DIM, COL_NONE
            else:
                text, fg = f"{value:02X}", theme.TEXT
                if self.fresh[addr]:
                    bg, fg = COL_NEW, "#000000"
                elif self.changes[addr]:
                    bg = COL_SEEN
                else:
                    bg = COL_NONE
            if item.text() != text:
                item.setText(text)
            item.setForeground(QBrush(QColor(fg)))
            item.setBackground(QBrush(bg))
        self.grid.setUpdatesEnabled(True)
        self._show_detail()

    def _show_detail(self) -> None:
        row, col = self.grid.currentRow(), self.grid.currentColumn()
        if row < 0 or col < 0:
            return
        addr = row * 16 + col
        value = int(self.cur[addr])
        if value < 0:
            self.detail.setText(f"0x{addr:02X}: not read yet")
            return
        self.detail.setText(
            f"0x{addr:02X}: now 0x{value:02X} = {value:3d} = {value:08b}b   "
            f"min {int(self.minv[addr])}  max {int(self.maxv[addr])}  "
            f"changed {int(self.changes[addr])}x in {self._sweeps} sweeps")

    def clear_changes(self) -> None:
        self.changes[:] = 0
        self.fresh[:] = False
        known = self.cur >= 0
        self.minv[known] = self.cur[known]
        self.maxv[known] = self.cur[known]
        self._history.clear()
        self._sweeps = 0
        self._paint_grid()
        self.sc_status.setText("changes cleared")

    # ---- export ----

    def export_dialog(self) -> None:
        default = log_dir() / f"scan_{datetime.now():%Y%m%d_%H%M%S}.txt"
        default.parent.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Export memory scan", str(default),
                                              "Text (*.txt)")
        if path:
            written = self.export(Path(path))
            self.sc_status.setText("exported " + ", ".join(p.name for p in written))

    def export(self, path: Path) -> list[Path]:
        """Write a readable dump (.txt) and every sweep as a row (.csv)."""
        def grid(values, fmt, blank):
            lines = ["     " + " ".join(f"x{c:X}".rjust(len(blank)) for c in range(16))]
            for r in range(16):
                cells = [blank if self.cur[r * 16 + c] < 0 else fmt(values[r * 16 + c])
                         for c in range(16)]
                lines.append(f"{r:X}x   " + " ".join(cells))
            return "\n".join(lines)

        ecu = self.poller.ecu_id_text if self.poller else "?"
        parts = [
            f"hondalog memory scan  {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"ECU id: {ecu}",
            f"sweeps: {self._sweeps}",
            "", "Latest values (hex)", grid(self.cur, lambda v: f"{int(v):02X}", "--"),
            "", "Times changed since 'Clear changes'",
            grid(self.changes, lambda v: f"{int(v):4d}", "   -"),
            "", "Minimum seen (decimal)", grid(self.minv, lambda v: f"{int(v):3d}", "  -"),
            "", "Maximum seen (decimal)", grid(self.maxv, lambda v: f"{int(v):3d}", "  -"),
            "", "Addresses that changed: " + (" ".join(
                f"{a:02X}" for a in np.flatnonzero(self.changes > 0)) or "none"), "",
        ]
        path.write_text("\n".join(parts), encoding="utf-8")
        written = [path]
        if self._history:
            csv_path = path.with_suffix(".csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("wall_time," + ",".join(f"{a:02X}" for a in range(256)) + "\n")
                for wall, values in self._history:
                    fh.write(wall.isoformat(timespec="milliseconds") + ","
                             + ",".join("" if v < 0 else str(int(v)) for v in values) + "\n")
            written.append(csv_path)
        return written

    # ---- plumbing ----

    def set_poller(self, poller) -> None:
        self.poller = poller
        on = poller is not None
        for w in (self.send_btn, self.rd_btn, self.sweep_btn, self.cont_btn, self.hex_in):
            w.setEnabled(on)
        if not on:
            self._pending.clear()
            self._scan_active = False
            if self.cont_btn.isChecked():
                self.cont_btn.setChecked(False)

    def tick(self) -> None:
        """Collect finished jobs. Called from the main window's timer."""
        if not self._pending:
            return
        done = [(f, k, i) for f, k, i in self._pending if f.done()]
        still = [(f, k, i) for f, k, i in self._pending if not f.done()]
        self._pending = still
        for fut, kind, info in done:
            if kind == "console":
                self._console_done(info, fut)
            else:
                self._chunk_done(info, fut)
