"""The pages of F7 Bytes besides the memory scanner (``rawtools.RawScreen``):

- **Address map**: the map file as TOML text and as a form side by side.
  Every keystroke is validated (``hondalog.addressmap``); a channel picked in
  the table can be edited in the form, marked verified / unverified /
  suspect, and written back into the text without losing comments
  (``hondalog.mapping.mapedit.MapFile``). Version bump, diff against the
  saved file and the version hash come from the same module. Saving keeps
  the old file as ``.bak``; the shipped map is never written.
- **Unknown bytes**: what each unmapped address might be, how sure, and a
  note, seeded from the analysis reports (docs/analysis/2026-11-26 and
  2026-11-27). Notes and confidence are the owner's to change; they are kept
  in the settings, not in the map.
- **Poke test**: runs ``tools/poke_test.py`` (a guided stimulus test that
  sweeps 0x00-0xFF and reports which bytes answered). The live session is
  paused (disconnected) for the run and connected again afterwards, instead
  of the tool's watch-and-run pause file. Its prompts show here; Enter
  answers them.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ... import addressmap
from ...mapping import mapedit
from ..controls import Button
from ..theme import T
from .common import label

ROOT = Path(__file__).resolve().parents[3]
POKE_TOOL = ROOT / "tools" / "poke_test.py"
CONFIDENCE = ("high", "medium-high", "medium", "medium-low", "low", "none")

# (address, bit or None, what it might be, confidence, report)
SEED = (
    (0x27, None, "Ignition correction / advance limit in 1/4° counts: an rpm band 0-4 at idle, a MAP ramp from "
                 "41 kPa, a remembered knock-control state above ~50 kPa (cut on fast rpm rises, recovers slowly)",
     "medium-high", "2026-11-27 index-bytes.md"),
    (0x3C, None, "Knock-sensor background noise level: rises ~rpm³, ignores load, same in fuel cut, "
                 "independent reads. Not a map index", "medium", "2026-11-27 index-bytes.md"),
    (0x29, None, "Idle air control command (0x28 IACV is a fixed curve of it); opens with the throttle off idle, "
                 "dashpot after a stop", "medium-high", "2026-11-26 README.md, drive-bytes.md"),
    (0x2A, None, "Learned idle air (IAC learn) in backup RAM: lagged copy of the IAC command at fully warm idle, "
                 "frozen otherwise, survives key-off", "medium-high", "2026-11-27 output-bytes.md"),
    (0x2F, None, "EVAP purge command (PCS duty or ramp): only with ECT above ~66-69 °C, ramps with airflow, "
                 "cut in decel and on throttle moves, STFT falls as it rises", "medium-high",
     "2026-11-27 output-bytes.md"),
    (0x1C, None, "Copy of ELD (0x19), equal at every read", "high", "2026-11-26 README.md"),
    (0x08, 2, "PSP switch, 0 = steering loaded (formula 1 - bit(x, 2))", "high", "2026-11-26 drive-bytes.md"),
    (0x08, 3, "Brake switch (verified while driving)", "high", "2026-11-26 drive-bytes.md"),
    (0x08, 7, "Always 1: not a clutch or gear input (A/T P/N input, fixed on the M/T?)", "low",
     "2026-11-26 drive-bytes.md"),
    (0x0A, 2, "VTEC solenoid on: seen in one read, 5,376 rpm WOT", "medium", "2026-11-26 drive-bytes.md"),
    (0x0B, 6, "O2-sensor heater control output: off above ~3,500 rpm at MAP >= ~84 kPa. Not ALTC. Primary or "
              "secondary heater unknown", "medium", "2026-11-27 output-bytes.md"),
    (0x0C, 0, "ALTC alternator control (1 = normal charging): drops for a moment at a sudden load at idle",
     "medium", "2026-11-27 output-bytes.md"),
    (0x0C, 3, "VTEC pressure switch: set with 0x0A b2 in one read only", "medium-low", "2026-11-27 output-bytes.md"),
    (0x0F, 0, "Closed loop: 0 after the start, in decel fuel cut and in power enrichment", "high",
     "2026-11-26 drive-bytes.md"),
    (0xA4, 0, "Idle-control status bit: on ~2 s after the fan load dropped off, off 46 s later. One event",
     "low", "2026-11-27 output-bytes.md"),
    (0x76, None, "Idle target, 0x76-0x77, x/4 rpm, set by ECT; about 60 rpm lower while rolling",
     "medium-high", "2026-11-26 README.md, drive-bytes.md"),
    (0x9A, None, "Fuel system status, OBD2 PID 03 encoding (1 open, 2 closed, 4 open by driving)", "high",
     "2026-11-26 drive-bytes.md"),
    (0x9C, None, "Calculated load, as OBD2 PID 04 (98 % at WOT)", "medium-high", "2026-11-26 drive-bytes.md"),
    (0xA0, None, "Rear (post-cat) O2: rich-biased, steadier than the front, empties lean in fuel cut",
     "medium-high", "2026-11-26 drive-bytes.md"),
    (0x32, None, "Fixed ratiometric ~0.88 V analog input (an unused ECM pin with a bias?); flickers 45/44",
     "medium", "2026-11-27 analog-bytes.md"),
    (0x37, None, "Same kind as 0x32; its flicker falls with rpm and load", "medium", "2026-11-27 analog-bytes.md"),
    (0x33, None, "Constant 55 = ECT 67.5 °C: most likely the EVAP purge enable threshold (manual: 68 °C)",
     "medium-low", "2026-11-27 analog-bytes.md"),
    (0x35, None, "Constant 45 = ECT 75.6 °C: a warm-up or thermostat-open level", "low",
     "2026-11-27 analog-bytes.md"),
    (0x36, None, "Constant 7: a calibration constant (count, index or ECT short limit)", "none",
     "2026-11-27 analog-bytes.md"),
    (0x38, None, "Constant 25 (also 0x39) = ECT 99.5 °C: fan or A/C-cut level?", "low",
     "2026-11-27 analog-bytes.md"),
    (0x3A, None, "25, but 24 for ~8 s after a start: a post-start / O2-active status bit", "low",
     "2026-11-27 analog-bytes.md"),
    (0x8F, None, "142 = +10.9 %: a second long-term fuel-trim cell?", "low", "2026-11-27 analog-bytes.md"),
    (0x95, None, "Supported non-continuous OBD2 monitors (0x94, 0x96, 0x99: other monitor bookkeeping)", "low",
     "2026-11-27 analog-bytes.md"),
    (0x80, None, "0x80-0x8C: per-monitor status bit fields (bit 2 = incomplete?)", "low",
     "2026-11-27 analog-bytes.md"),
    (0x16, None, "Dead: 0 in 8,682 reads (crank, WOT, 5,400 rpm, decel cut). Unused analog slot?", "high",
     "2026-11-27 output-bytes.md"),
    (0x0D, None, "Dead flags (also 0x0E): hardware this car lacks (P2T: 0x0D b3 = engine-mount solenoid)", "high",
     "2026-11-27 output-bytes.md"),
    (0x03, None, "Always 0 (0x03-0x07, 0x1A, 0x1B, 0x1D-0x1F, 0x21, 0x23, 0x2B-0x2E, 0x30, 0x31, 0x34, 0x3B), "
                 "idle and driving; a cold start is still to log", "high", "2026-11-26 drive-bytes.md"),
)


def byte_key(addr: int, bit: int | None) -> str:
    return f"0x{addr:02X}" + (f" b{bit}" if bit is not None else "")


def parse_key(text: str) -> tuple[int, int | None] | None:
    """'0x27' / '27' / '0x0B b6' -> (addr, bit)."""
    parts = text.strip().lower().replace("bit", "b").split()
    if not parts:
        return None
    try:
        addr = int(parts[0], 16)
    except ValueError:
        return None
    bit = None
    if len(parts) > 1:
        try:
            bit = int(parts[1].lstrip("b"))
        except ValueError:
            return None
        if not 0 <= bit <= 7:
            return None
    return (addr, bit) if 0 <= addr <= 0xFF else None


# ---- unknown bytes ---------------------------------------------------------

class UnknownList:
    """The list behind the page: the seed, with the owner's notes and confidence
    on top (kept in QSettings as JSON under ``bytes/unknown``)."""

    KEY = "bytes/unknown"

    def __init__(self, settings: QSettings):
        self.settings = settings
        self.rows: list[dict] = [dict(key=byte_key(a, b), addr=a, bit=b, what=w, confidence=c, note="",
                                      source=s) for a, b, w, c, s in SEED]
        try:
            saved = json.loads(str(settings.value(self.KEY, "") or "{}"))
        except ValueError:
            saved = {}
        known = {r["key"]: r for r in self.rows}
        for key, entry in saved.items():
            row = known.get(key)
            if row is None:
                parsed = parse_key(key)
                if parsed is None:
                    continue
                row = dict(key=key, addr=parsed[0], bit=parsed[1], what="", confidence="none", note="",
                           source="added by hand")
                self.rows.append(row)
                known[key] = row
            for f in ("what", "confidence", "note"):
                if isinstance(entry.get(f), str):
                    row[f] = entry[f]
        self.rows.sort(key=lambda r: (r["addr"], -1 if r["bit"] is None else r["bit"]))

    def save(self) -> None:
        seed = {byte_key(a, b): (w, c) for a, b, w, c, _ in SEED}
        out = {}
        for r in self.rows:
            base = seed.get(r["key"])
            if base is None or r["note"] or (r["what"], r["confidence"]) != base:
                out[r["key"]] = {"what": r["what"], "confidence": r["confidence"], "note": r["note"]}
        self.settings.setValue(self.KEY, json.dumps(out))

    def find(self, key: str) -> dict | None:
        return next((r for r in self.rows if r["key"] == key), None)

    def add(self, key: str) -> dict | None:
        parsed = parse_key(key)
        if parsed is None:
            return None
        key = byte_key(*parsed)
        row = self.find(key)
        if row is None:
            row = dict(key=key, addr=parsed[0], bit=parsed[1], what="", confidence="none", note="",
                       source="added by hand")
            self.rows.append(row)
            self.rows.sort(key=lambda r: (r["addr"], -1 if r["bit"] is None else r["bit"]))
            self.save()
        return row

    def set(self, key: str, **changes) -> None:
        row = self.find(key)
        if row is None:
            return
        for f, v in changes.items():
            if f in ("what", "confidence", "note"):
                row[f] = str(v)
        self.save()


class UnknownPage(QWidget):
    """Byte · in the map as · what it might be · confidence · note · report."""

    rankRequested = Signal(int)
    COLS = ("Byte", "In the map as", "What it might be", "Confidence", "Note", "Report")

    def __init__(self, session, settings: QSettings, parent=None):
        super().__init__(parent)
        self.session = session
        self.model = UnknownList(settings)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        top = QHBoxLayout()
        top.addWidget(label("Seeded from docs/analysis 2026-11-26 and 2026-11-27. Double-click What or Note to "
                            "edit; pick a confidence. Kept in the app's settings, not in the map.", dim=True,
                            wrap=True, px=13), 1)
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("0x41 or 0x0B b5")
        self.add_edit.setFixedWidth(170)
        self.add_btn = Button("Add byte", height=36)
        self.rank_btn = Button("Rank against it…", height=36)
        self.rank_btn.setToolTip("Open the memory scanner at this byte (the correlation finder is R there)")
        for w in (self.add_edit, self.add_btn, self.rank_btn):
            top.addWidget(w)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setWordWrap(True)
        self.table.setFont(T.font(13, 600))
        hdr = self.table.horizontalHeader()
        for i, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.ResizeToContents, QHeaderView.Stretch,
                                  QHeaderView.ResizeToContents, QHeaderView.Interactive, QHeaderView.Interactive)):
            hdr.setSectionResizeMode(i, mode)
        self.table.setColumnWidth(4, 220)
        self.table.setColumnWidth(5, 190)
        lay.addWidget(self.table, 1)
        self.add_btn.clicked.connect(self.add_from_field)
        self.add_edit.returnPressed.connect(self.add_from_field)
        self.rank_btn.clicked.connect(self._rank)
        self.table.itemChanged.connect(self._edited)
        self._filling = False
        self.fill()

    def fill(self) -> None:
        self._filling = True
        rows = self.model.rows
        self.table.setRowCount(len(rows))
        mapper = getattr(self.session, "mapper", None)
        for i, r in enumerate(rows):
            info = mapper.mapped(r["addr"]) if mapper is not None else None
            mapped = "—" if info is None else f"{info[0]} ({'verified' if info[1] else 'unverified'})"
            cells = (r["key"], mapped, r["what"], None, r["note"], r["source"])
            for c, text in enumerate(cells):
                if c == 3:
                    combo = QComboBox()
                    combo.addItems(list(CONFIDENCE))
                    if r["confidence"] not in CONFIDENCE:
                        combo.addItem(r["confidence"])
                    combo.setCurrentText(r["confidence"])
                    combo.currentTextChanged.connect(lambda v, k=r["key"]: self.model.set(k, confidence=v))
                    self.table.setCellWidget(i, c, combo)
                    continue
                item = QTableWidgetItem(text)
                if c not in (2, 4):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if c == 0:
                    item.setFont(T.font(13, 700, mono=True))
                if c == 1 and info is not None:
                    item.setForeground(QBrush(T.ok if info[1] else T.unverified))
                if c == 5:
                    item.setForeground(QBrush(T.fg2))
                item.setData(Qt.UserRole, r["key"])
                self.table.setItem(i, c, item)
        self.table.resizeRowsToContents()
        self._filling = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        key = item.data(Qt.UserRole)
        field = {2: "what", 4: "note"}.get(item.column())
        if key and field:
            self.model.set(key, **{field: item.text()})

    def add_from_field(self) -> bool:
        row = self.model.add(self.add_edit.text())
        if row is None:
            return False
        self.add_edit.clear()
        self.fill()
        i = self.model.rows.index(row)
        self.table.selectRow(i)
        return True

    def selected_addr(self) -> int | None:
        i = self.table.currentRow()
        return self.model.rows[i]["addr"] if 0 <= i < len(self.model.rows) else None

    def _rank(self) -> None:
        addr = self.selected_addr()
        if addr is not None:
            self.rankRequested.emit(addr)


# ---- the address map -------------------------------------------------------

def map_path_for(session) -> tuple[Path, bool]:
    """The file the page edits and whether it may be written. The demo edits a
    copy of the shipped map in the temp folder, never the user's."""
    if getattr(session, "demo", False):
        tmp = Path(tempfile.gettempdir()) / "hondalog-demo" / "addressmap.toml"
        if not tmp.exists():
            tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(addressmap.DEFAULT_MAP, tmp)
        return tmp, True
    amap = getattr(getattr(session, "ecu", None), "amap", None)
    path = Path(amap.path) if amap is not None and getattr(amap, "path", "") else addressmap.user_map_path()
    writable = path.resolve() != addressmap.DEFAULT_MAP.resolve()
    return path, writable


STATUS_TOKEN = {"verified": "ok", "suspect": "alarm", "unverified": "unverified"}


class MapPage(QWidget):
    """TOML text on the left, the channel table and a form on the right."""

    message = Signal(str)
    saved = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.path, self.writable = map_path_for(session)
        #: asks before the file is written; a test replaces it. (title, text) -> bool
        self.confirm = self._confirm_box
        self.amap: addressmap.AddressMap | None = None
        self.error = ""
        self.saved_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        lay.setSpacing(6)
        bar = QHBoxLayout()
        self.path_label = label("", mono=True, px=13)
        self.validate_btn = Button("Validate", height=36)
        self.bump_btn = Button("Version +1", height=36)
        self.diff_btn = Button("Diff vs saved", height=36)
        self.reload_btn = Button("Reload", height=36)
        self.save_btn = Button("Save", kind="primary", height=36)
        bar.addWidget(self.path_label, 1)
        for b in (self.validate_btn, self.bump_btn, self.diff_btn, self.reload_btn, self.save_btn):
            bar.addWidget(b)
        lay.addLayout(bar)
        self.status = label("", mono=True, px=13, wrap=True)
        lay.addWidget(self.status)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.text = QPlainTextEdit()
        self.text.setFont(T.font(13, 500, mono=True))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        split.addWidget(self.text)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.setSpacing(6)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Channel", "Addr", "Formula", "Status"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFont(T.font(13, 600))
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        rl.addWidget(self.table, 5)
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)
        self.f_addr, self.f_formula, self.f_label, self.f_units = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.f_notes = QLineEdit()
        self.f_status = QComboBox()
        self.f_status.addItems(list(addressmap.STATUSES))
        self.f_addr.setFont(T.font(13, 700, mono=True))
        self.f_formula.setFont(T.font(13, 700, mono=True))
        for text, w in (("Address", self.f_addr), ("Formula", self.f_formula), ("Label", self.f_label),
                        ("Units", self.f_units), ("Status", self.f_status), ("Notes", self.f_notes)):
            form.addRow(label(text, px=13), w)
        rl.addWidget(form_box)
        self.apply_btn = Button("Apply to the text", height=36)
        rl.addWidget(self.apply_btn)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(T.font(12, 600, mono=True))
        self.diff_view.setPlaceholderText("Diff vs saved shows here: what changed, and whether old logs would "
                                          "decode differently (the version hash).")
        rl.addWidget(self.diff_view, 1)
        split.addWidget(right)
        split.setSizes([640, 520])
        lay.addWidget(split, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.validate)
        self.text.textChanged.connect(self._timer.start)
        self.validate_btn.clicked.connect(self.validate)
        self.bump_btn.clicked.connect(self.bump_version)
        self.diff_btn.clicked.connect(self.show_diff)
        self.reload_btn.clicked.connect(self.reload)
        self.save_btn.clicked.connect(self.save)
        self.apply_btn.clicked.connect(self.apply_form)
        self.table.currentCellChanged.connect(lambda row, *_: self._fill_form(row))
        self.reload()

    # ---- file ----

    def reload(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8")
            self.message.emit(f"Address map: {exc}; showing the shipped map")
        self.saved_text = text
        self.text.setPlainText(text)
        self.validate()
        where = "" if self.writable else "  (read-only: the shipped map)"
        self.path_label.setText(f"{self.path}{where}")
        self.save_btn.setEnabled(self.writable)

    def validate(self) -> bool:
        """Parse the text as a real map. Returns True when it is valid."""
        self._timer.stop()
        try:
            amap = addressmap.loads(self.text.toPlainText(), path=str(self.path))
        except addressmap.MapError as exc:
            self.amap, self.error = None, str(exc)
            self.status.setText(f"NOT VALID  {self.error}")
            self.status.setStyleSheet(f"color: {T.tokens.alarm};")
            return False
        self.amap, self.error = amap, ""
        chans = amap.channels
        n_ver = sum(1 for c in chans if c.status == addressmap.VERIFIED)
        n_sus = sum(1 for c in chans if c.status == addressmap.SUSPECT)
        changed = "" if self.text.toPlainText() == self.saved_text else " · edited, not saved"
        self.status.setText(f"VALID  {amap.version} · #{amap.version_hash} · {len(chans)} channels "
                            f"({n_ver} verified, {n_sus} suspect, {len(chans) - n_ver - n_sus} unverified)"
                            f"{changed}")
        self.status.setStyleSheet(f"color: {T.tokens.ok};")
        self._fill_table(amap)
        return True

    def _fill_table(self, amap) -> None:
        current = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(amap.channels))
        for i, ch in enumerate(amap.channels):
            addr = f"0x{ch.addr:02X}" + (f"+{ch.length - 1}" if ch.length > 1 else "")
            for c, text in enumerate((ch.name, addr, ch.formula, ch.status)):
                item = QTableWidgetItem(text)
                if c in (1, 2):
                    item.setFont(T.font(13, 700, mono=True))
                if c == 3:
                    item.setForeground(QBrush(T.color(STATUS_TOKEN.get(ch.status, "fg"))))
                    item.setText({"verified": "✓ verified", "suspect": "! suspect"}.get(ch.status, "? unverified"))
                self.table.setItem(i, c, item)
        self.table.blockSignals(False)
        if 0 <= current < len(amap.channels):
            self.table.setCurrentCell(current, 0)

    def channel_names(self) -> list[str]:
        return [c.name for c in self.amap.channels] if self.amap is not None else []

    def select_channel(self, name: str) -> bool:
        names = self.channel_names()
        if name not in names:
            return False
        self.table.setCurrentCell(names.index(name), 0)
        self._fill_form(names.index(name))
        return True

    def _fill_form(self, row: int) -> None:
        if self.amap is None or not 0 <= row < len(self.amap.channels):
            return
        ch = self.amap.channels[row]
        self.f_addr.setText(f"0x{ch.addr:02X}")
        self.f_formula.setText(ch.formula)
        self.f_label.setText(ch.label)
        self.f_units.setText(ch.units)
        self.f_status.setCurrentText(ch.status)
        self.f_notes.setText(ch.notes)

    def apply_form(self) -> bool:
        """Write the form into the text (comments kept), then validate."""
        row = self.table.currentRow()
        if self.amap is None or not 0 <= row < len(self.amap.channels):
            self.message.emit("Address map: pick a channel (and make the text valid) first")
            return False
        ch = self.amap.channels[row]
        try:
            addr = int(self.f_addr.text().strip(), 16)
        except ValueError:
            self.message.emit(f"Address map: {self.f_addr.text()!r} is not a hex address")
            return False
        changes = dict(addr=addr, formula=self.f_formula.text().strip() or "x", label=self.f_label.text().strip(),
                       units=self.f_units.text().strip(), status=self.f_status.currentText(),
                       notes=self.f_notes.text().strip())
        changes = {k: v for k, v in changes.items() if v != getattr(ch, k)}
        if not changes:
            return False
        try:
            mf = mapedit.MapFile(self.text.toPlainText(), self.path)
            mf.set_channel(ch.name, **changes)
            mf.load()
        except (addressmap.MapError, ValueError) as exc:
            self.message.emit(f"Address map: not applied: {exc}")
            return False
        self._set_text(mf.text)
        self.message.emit(f"Address map: {ch.name}: " + ", ".join(f"{k} = {v}" for k, v in changes.items())
                          + " (not saved yet)")
        return True

    def bump_version(self) -> str:
        if self.amap is None:
            self.message.emit("Address map: make the text valid first")
            return ""
        new = mapedit.next_version(self.amap.version)
        mf = mapedit.MapFile(self.text.toPlainText(), self.path)
        mf.set_top("version", new)
        self._set_text(mf.text)
        return new

    def _set_text(self, text: str) -> None:
        bar = self.text.verticalScrollBar().value()
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(bar)
        self.validate()

    def diff(self):
        """MapDiff of the saved file -> the text (None if either is not valid)."""
        if self.amap is None:
            return None
        try:
            old = addressmap.loads(self.saved_text, path=str(self.path))
        except addressmap.MapError:
            return None
        return mapedit.diff_maps(old, self.amap)

    def show_diff(self) -> None:
        d = self.diff()
        self.diff_view.setPlainText(d.text() if d is not None else f"Cannot diff: {self.error or 'saved file is not valid'}")

    def _confirm_box(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Cancel, self)
        yes = box.addButton("Save", QMessageBox.AcceptRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is yes

    def save(self) -> bool:
        if not self.writable:
            self.message.emit("Address map: the shipped map is read-only; your copy is made on first connect")
            return False
        if not self.validate():
            self.message.emit(f"Address map: not saved, it is not valid: {self.error}")
            return False
        d = self.diff()
        if d is not None and not d:
            self.message.emit("Address map: nothing changed")
            return False
        summary = d.text() if d is not None else ""
        if d is not None and d.decode_changed and self.amap.version == addressmap.loads(self.saved_text).version:
            summary += "\n\nDecoding changed but the version did not: consider Version +1."
        if not self.confirm("Save the address map", f"{self.path}\n\n{summary[:1500]}"):
            return False
        try:
            mf = mapedit.MapFile(self.text.toPlainText(), self.path)
            mf.save(self.path)
        except (addressmap.MapError, OSError) as exc:
            self.message.emit(f"Address map: not saved: {exc}")
            return False
        self.saved_text = self.text.toPlainText()
        reload_map = getattr(getattr(self.session, "ecu", None), "reload_map", None)
        if callable(reload_map) and not getattr(self.session, "demo", False):
            try:
                reload_map()
            except Exception as exc:                # the file is saved; the live map waits for a reconnect
                self.message.emit(f"Address map saved; reload failed: {exc}")
        mapper = getattr(self.session, "mapper", None)
        if mapper is not None and hasattr(mapper, "map_revision"):
            mapper.map_revision += 1
        self.validate()
        self.saved.emit()
        self.message.emit(f"Address map saved to {self.path.name} (old file kept as .bak); reconnect to decode with it")
        return True


# ---- the poke test ---------------------------------------------------------

BOOT = (
    "import sys\n"
    "sys.path[:0] = [{tools!r}, {root!r}]\n"
    "import poke_test as pt\n"
    "pt.take_cable = lambda port, say, **kw: pt._no_cable()   # the app paused its live session instead\n"
    "raise SystemExit(pt.main(sys.argv[1:]))\n"
)


class PokePage(QWidget):
    """Launcher for tools/poke_test.py with the live session paused around it."""

    message = Signal(str)

    def __init__(self, session, settings: QSettings, parent=None):
        super().__init__(parent)
        self.session = session
        self.settings = settings
        self.proc: QProcess | None = None
        self.paused_port = ""                   # the live port we disconnected (connect it again after)
        self.tool = POKE_TOOL
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(label("Guided stimulus test: one action at a time (A/C, fan, brake, steering, lights ...), "
                            "every address 0x00-0xFF swept before, during and after, then a report of which bytes "
                            "and bits answered. Read-only: Honda read requests only. The live session is paused "
                            "for the run and connected again when it ends.", wrap=True, px=13))
        form = QFormLayout()
        form.setVerticalSpacing(4)
        self.port = QLineEdit(str(settings.value("port/last", "") or ""))
        self.port.setPlaceholderText("COM21 (the car)")
        self.steps = QLineEdit()
        self.steps.setPlaceholderText("all steps (or a comma-separated list: ac,headlights,brake,steering)")
        self.trans = QComboBox()
        self.trans.addItems(["manual", "auto"])
        self.sim = QCheckBox("Simulator (no cable, the app is not touched)")
        self.sim.setChecked(bool(getattr(session, "demo", False)))
        self.out = QLineEdit()
        self.out.setPlaceholderText(str(ROOT / "poke-runs" / "<timestamp>"))
        for text, w in (("Port", self.port), ("Steps", self.steps), ("Gearbox", self.trans), ("", self.sim),
                        ("Run folder", self.out)):
            form.addRow(label(text, px=13), w)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.start_btn = Button("Start the poke test", kind="primary", height=40)
        self.stop_btn = Button("Stop", height=40)
        self.list_btn = Button("List the steps", height=40)
        self.state = label("", px=13)
        for w in (self.start_btn, self.stop_btn, self.list_btn):
            row.addWidget(w)
        row.addWidget(self.state, 1)
        lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(T.font(13, 600, mono=True))
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log, 1)
        reply = QHBoxLayout()
        self.answer = QLineEdit()
        self.answer.setPlaceholderText("answer a prompt (empty = just Enter)")
        self.enter_btn = Button("Enter", height=40)
        reply.addWidget(self.answer, 1)
        reply.addWidget(self.enter_btn)
        lay.addLayout(reply)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.list_btn.clicked.connect(lambda: self.start(listing=True))
        self.enter_btn.clicked.connect(self.send_answer)
        self.answer.returnPressed.connect(self.send_answer)
        self._sync()

    def available(self) -> bool:
        return self.tool.exists() and not getattr(sys, "frozen", False)

    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def arguments(self, listing: bool = False) -> list[str]:
        """The command line after the interpreter."""
        boot = BOOT.format(tools=str(self.tool.parent), root=str(self.tool.parent.parent))
        args = ["-u", "-c", boot]
        if listing:
            args += ["list"]
        else:
            args += ["run"]
            if self.sim.isChecked():
                args += ["--dry-run"]
            else:
                args += ["--port", self.port.text().strip()]
            if self.out.text().strip():
                args += ["--out", self.out.text().strip()]
        if self.steps.text().strip():
            args += ["--steps", self.steps.text().strip().replace(" ", "")]
        args += ["--trans", self.trans.currentText()]
        return args

    def start(self, listing: bool = False) -> bool:
        if self.running():
            return False
        if not self.available():
            self.message.emit("Poke test: tools/poke_test.py is not next to this app (run it from a source checkout)")
            return False
        if not listing and not self.sim.isChecked():
            port = self.port.text().strip()
            if not port:
                self.message.emit("Poke test: give the car's port, or tick Simulator")
                return False
            self.pause_live()
        self.log.clear()
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda _e: self._finished(-1, None))
        self.proc.start(sys.executable, self.arguments(listing))
        self._sync()
        return True

    def pause_live(self) -> None:
        """Disconnect the live session (the test needs the cable) and remember the port."""
        s = self.session
        if getattr(s, "connected", False) and s.port and not self.paused_port:
            self.paused_port = s.port
            s.disconnect()
            self.message.emit(f"Live session paused: {self.paused_port} is the poke test's until it ends")

    def resume_live(self) -> None:
        if self.paused_port:
            port, self.paused_port = self.paused_port, ""
            self.session.connect(port)
            self.message.emit(f"Poke test over: live session connecting to {port} again")

    def stop(self) -> None:
        if self.running():
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def send_answer(self) -> None:
        if self.running():
            text = self.answer.text()
            self.proc.write((text + "\n").encode("utf-8"))
            self.log.appendPlainText(f"  <<< {text or '(Enter)'}")
            self.answer.clear()

    def _read(self) -> None:
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(data.replace("\r\n", "\n"))
        self.log.ensureCursorVisible()

    def _finished(self, code, _status) -> None:
        if self.proc is not None and self.proc.bytesAvailable():
            self._read()
        self.state.setText(f"finished (exit {code})")
        self.resume_live()
        self._sync()

    def _sync(self) -> None:
        run = self.running()
        self.start_btn.setEnabled(not run and self.available())
        self.list_btn.setEnabled(not run and self.available())
        self.stop_btn.setEnabled(run)
        self.enter_btn.setEnabled(run)
        if run:
            self.state.setText("running" + (f" · live session paused ({self.paused_port})" if self.paused_port else ""))
        elif not self.available():
            self.state.setText("tools/poke_test.py not found")

    def shutdown(self) -> None:
        self.stop()
