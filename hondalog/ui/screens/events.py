"""F3 Events: every stall, link dropout, near-stall, key-off, VTEC run, harsh
acceleration or braking, knock retard and mark of a day's drives in one list,
with filters and counts ("All" leaves the many knock retards to their own
chip); the selected event's verdict, why, and a timeline
from 10 s before to 5 s after; Open in Replay (F4), Show on GPS map, Add a
note, Change verdict (Events.dc.html).

The events are worked out from the recorded channels by
``hondalog.analysis.events`` (never typed in); a person's notes and verdicts
are kept in the drive's own database by ``hondalog.analysis.eventnotes``
(rows of the existing events table, no schema change).

Keys: ↑ ↓ select, Enter opens in Replay, M shows it on the GPS map, N adds a
note, V changes the verdict, F cycles the filter, PgUp / PgDn the previous or
next day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QPainter, QPolygonF
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from ...analysis import eventnotes
from ...analysis import events as ev
from ...data.db import DriveRef
from .. import paint
from ..controls import Button
from ..replaymodel import analysis_channels
from ..theme import T

LIST_ROW_H = 38
#: badge per kind: (fill token or None, border token, ink token)
BADGES = {"stall": ("alarm", "alarm", "on_alarm"), "near_stall": ("warn", "warn", "on_warn"),
          "dropout": (None, "accent", "accent"), "key_off": (None, "line", "fg2"),
          "vtec": ("ok", "ok", "panel"), "harsh_accel": (None, "unverified", "unverified"),
          "harsh_brake": (None, "unverified", "unverified"), "knock": (None, "unverified", "unverified"),
          "mark": ("fg", "fg", "bg")}
WINDOW = (-10.0, 5.0)
#: kinds so frequent that "All" leaves them out: they have their own chip
QUIET = ("knock",)


@dataclass
class DriveData:
    """One drive's events and the channels its timelines are drawn from (log time)."""
    name: str
    ref: object
    epoch: float | None                 # wall clock of log second 0
    origin: float                       # log time of the drive's view zero (what Replay counts from)
    duration: float
    events: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)
    database: object = None             # where notes go (None: demo, not saved)


@dataclass
class Row:
    drive: DriveData
    event: ev.DriveEvent

    @property
    def wall(self) -> str:
        if self.drive.epoch is None:
            return f"{self.event.t:.1f} s"
        return datetime.fromtimestamp(self.drive.epoch + self.event.t).strftime("%H:%M:%S")


def load_drive(view, ref=None, database=None) -> DriveData:
    """Events of a drive (or capture) view, on log time, with the stored notes."""
    origin = float(view.origin)
    channels = {name: (t + origin, v) for name, (t, v) in analysis_channels(view).items()}
    lo, hi = view.span()
    markers = [(t + origin, text) for t, text in view.markers]
    found = ev.detect(channels, markers, t_end=hi + origin if view.trigger_t is None else None)
    try:
        epoch = float(view.meta["epoch_unix"])
    except (KeyError, TypeError, ValueError):
        epoch = None
    name = getattr(ref, "drive", None) or view.title
    data = DriveData(name, ref if ref is not None else view.path, epoch, origin, hi - lo, found,
                     {k: channels[k] for k in ("rpm", "idle_target", "tps", "map", "vss") if k in channels},
                     database)
    if database is not None and isinstance(ref, DriveRef):
        eventnotes.apply(found, eventnotes.load(database, ref.drive))
    return data


class EventList(QWidget):
    """The chips (filters with counts) and the list."""

    selected = Signal(int)
    activated = Signal(int)
    filterPicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self.counts: dict[str, int] = {}
        self.filter = "all"
        self.current = -1
        self.top = 0
        self._chips: list[tuple[QRectF, str]] = []
        self.setFocusPolicy(Qt.NoFocus)

    def set_rows(self, rows, counts, current) -> None:
        self.rows, self.counts, self.current = rows, counts, current
        self.reveal()
        self.update()

    def chips(self) -> list[tuple[str, str]]:
        total = sum(n for k, n in self.counts.items() if k not in QUIET)
        out = [("all", f"All {total}")]
        for kind in ev.KINDS:
            n = self.counts.get(kind, 0)
            if n or kind in ("stall", "near_stall", "dropout", "key_off", "vtec", "mark"):
                out.append((kind, f"{ev.CHIPS[kind]} {n}"))
        return out

    def list_top(self) -> float:
        return (self._chips[-1][0].bottom() + 10) if self._chips else 52.0

    def visible(self) -> int:
        return max(1, int((self.height() - self.list_top()) // LIST_ROW_H))

    def reveal(self) -> None:
        if self.current < 0:
            return
        n = self.visible()
        if self.current < self.top:
            self.top = self.current
        elif self.current >= self.top + n:
            self.top = self.current - n + 1

    def wheelEvent(self, event) -> None:
        step = -3 if event.angleDelta().y() > 0 else 3
        self.top = int(max(0, min(len(self.rows) - self.visible(), self.top + step)))
        self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for r, key in self._chips:
            if r.contains(pos):
                self.filterPicked.emit(key)
                return
        i = self.top + int((pos.y() - self.list_top()) // LIST_ROW_H)
        if pos.y() >= self.list_top() and 0 <= i < len(self.rows):
            self.selected.emit(i)

    def mouseDoubleClickEvent(self, event) -> None:
        i = self.top + int((event.position().y() - self.list_top()) // LIST_ROW_H)
        if 0 <= i < len(self.rows):
            self.activated.emit(i)

    def _layout_chips(self) -> None:
        font = T.font(12, 700)
        x, y = 12.0, 10.0
        self._chips = []
        for key, text in self.chips():
            w = paint.text_w(font, text) + 24
            if x + w > self.width() - 12 and x > 12:
                x, y = 12.0, y + 34
            self._chips.append((QRectF(x, y, w, 28), key))
            x += w + 6

    def resizeEvent(self, _event) -> None:
        self._layout_chips()

    def paintEvent(self, _event) -> None:
        self._layout_chips()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), T.bg)
        font = T.font(12, 700)
        labels = dict(self.chips())
        for r, key in self._chips:
            on = key == self.filter
            if on:
                p.fillRect(r, T.accent)
                p.setPen(T.cpen(T.rule, 2))
            else:
                p.setPen(T.cpen(T.line, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.setFont(font)
            p.setPen(T.on_accent if on else T.fg2)
            p.drawText(r, Qt.AlignCenter, labels[key])
        top = self.list_top()
        p.fillRect(QRectF(0, top - 6, w, 1), T.rule)
        p.setClipRect(QRectF(0, top - 5, w, h - top + 5))
        mono, badge_f, text_f = T.font(13, 700, mono=True), T.font(12, 800), T.font(13, 600)
        if not self.rows:
            p.setFont(T.font(14, 600))
            p.setPen(T.fg2)
            p.drawText(QRectF(12, top, w - 24, 40), Qt.AlignVCenter | Qt.AlignLeft, "Nothing of this kind.")
        for k in range(self.top, min(len(self.rows), self.top + self.visible() + 1)):
            row = self.rows[k]
            y = top + (k - self.top) * LIST_ROW_H
            r = QRectF(0, y, w, LIST_ROW_H)
            sel = k == self.current
            if sel:
                p.fillRect(r, T.sunk)
                p.fillRect(QRectF(0, y, 5, LIST_ROW_H), T.accent)
            p.fillRect(QRectF(0, y + LIST_ROW_H - 1, w, 1), T.line)
            p.setFont(mono)
            p.setPen(T.fg)
            p.drawText(QRectF(14, y, 74, LIST_ROW_H), Qt.AlignVCenter | Qt.AlignLeft, row.wall)
            e = row.event
            fill, border, ink = BADGES.get(e.kind, (None, "line", "fg2"))
            b = QRectF(92, y + 8, 106, LIST_ROW_H - 16)
            if fill:
                p.fillRect(b, T.color(fill))
            else:
                p.setPen(T.cpen(T.color(border), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(b.adjusted(1, 1, -1, -1))
            p.setFont(badge_f)
            p.setPen(T.color(ink))
            p.drawText(b, Qt.AlignCenter, paint.elide(badge_f, e.label, b.width() - 6))
            text = e.summary
            if e.verdict_override:
                text = f"{text.split(' · ')[0]} · {e.verdict_override} (yours)"
            if e.note:
                text += f" · ✎ {e.note}"
            p.setFont(text_f)
            p.setPen(T.fg2)
            p.drawText(QRectF(208, y, w - 216, LIST_ROW_H), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(text_f, text, w - 216))
        p.end()


class Timeline(QWidget):
    """RPM (and the idle target), throttle and MAP from 10 s before to 5 s after the event."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row: Row | None = None

    def set_row(self, row: Row | None) -> None:
        self.row = row
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        paint.frame(p, r, 1, T.rule, T.panel)
        row = self.row
        if row is None:
            p.end()
            return
        e, ch = row.event, row.drive.channels
        x0, x1 = 90.0, r.width() - 12
        lo, hi = e.t + WINDOW[0], e.t + WINDOW[1]

        def xs(t):
            return x0 + (np.asarray(t) - lo) / (hi - lo) * (x1 - x0)
        h = r.height()
        axis_y = h - 22
        lanes = [("rpm", "RPM", "series1", 12, axis_y * 0.5), ("tps", "Throttle", "series2", axis_y * 0.5 + 6,
                                                                  axis_y * 0.74), ("map", "MAP", "series3",
                                                                                   axis_y * 0.74 + 6, axis_y - 4)]
        p.setPen(T.cpen(T.line, 1))
        for t_rel in (-10, -6, -3, 0, 3):
            x = float(xs(e.t + t_rel))
            p.drawLine(QPointF(x, 8), QPointF(x, axis_y))
        p.setFont(T.font(12, 700, mono=True))
        p.setPen(T.fg)
        for t_rel in (-10, -6, -3, 0, 3):
            x = float(xs(e.t + t_rel))
            text = "0" if t_rel == 0 else (f"−{abs(t_rel)} s" if t_rel == -10 else f"{t_rel:+d}".replace("-", "−"))
            p.drawText(QRectF(x - 30, axis_y + 2, 60, 18), Qt.AlignCenter, text)
        p.setRenderHint(QPainter.Antialiasing, True)
        for cid, name, token, top, bottom in lanes:
            pair = ch.get(cid)
            p.setFont(T.font(12, 700))
            p.setPen(T.fg)
            p.drawText(QPointF(8, top + 16), name)
            p.fillRect(QRectF(x0, bottom, x1 - x0, 1), T.rule)
            if pair is None:
                continue
            t, v = pair
            i0, i1 = np.searchsorted(t, lo), np.searchsorted(t, hi, side="right")
            tt, vv = t[i0:i1], v[i0:i1]
            if not len(tt):
                continue
            span = [float(np.nanmin(vv)), float(np.nanmax(vv))]
            if cid == "rpm" and "idle_target" in ch:
                tgt = ch["idle_target"]
                j0, j1 = np.searchsorted(tgt[0], lo - 3), np.searchsorted(tgt[0], hi + 1)
                if j1 > j0:
                    span = [min(span[0], float(np.nanmin(tgt[1][j0:j1]))), max(span[1], float(np.nanmax(tgt[1][j0:j1])))]
            if span[1] - span[0] < 1e-6:
                span[1] = span[0] + 1.0
            pad = 0.08 * (span[1] - span[0])
            vlo, vhi = span[0] - pad, span[1] + pad

            def ys(val):
                return bottom - 4 - (np.asarray(val) - vlo) / (vhi - vlo) * (bottom - top - 22)
            if cid == "rpm" and "idle_target" in ch:
                tgt = ch["idle_target"]
                j0, j1 = np.searchsorted(tgt[0], lo - 3), np.searchsorted(tgt[0], hi + 1)
                if j1 > j0:
                    target = float(np.median(tgt[1][j0:j1]))
                    y = float(ys(target))
                    p.setPen(T.cpen(T.fg, 1.5, dash=(6.0, 4.0)))
                    p.drawLine(QPointF(x0, y), QPointF(x1, y))
                    p.setFont(T.font(12, 600, mono=True))
                    p.drawText(QRectF(x1 - 160, y - 18, 158, 16), Qt.AlignRight | Qt.AlignVCenter,
                               f"target {target:,.0f}")
            pts = QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs(tt), ys(vv))
                             if np.isfinite(b)])
            p.setPen(T.cpen(T.color(token), 3))
            p.drawPolyline(pts)
        p.setRenderHint(QPainter.Antialiasing, False)
        x = float(xs(e.t))
        p.fillRect(QRectF(x - 1, 8, 2, axis_y - 8), T.alarm)
        if e.kind == "near_stall" and "rpm" in ch:
            p.setFont(T.font(12, 700, mono=True))
            p.setPen(T.alarm)
            p.drawText(QPointF(x + 6, 12 + 16 + 14), f"{e.value:,.0f}")
        p.end()


class EventsScreen(QWidget):
    """``openRequested(ref)``: open that drive in Replay (when no shell is wired)."""

    openRequested = Signal(object)
    message = Signal(str)

    def __init__(self, session, settings=None, shell=None, replay=None, parent=None, **_ignored):
        super().__init__(parent)
        self.session = session
        self.settings = settings
        self.shell = shell
        self.replay = replay
        self.drives: list[DriveData] = []
        self.rows: list[Row] = []           # all rows of the day
        self.shown: list[Row] = []          # after the filter
        self.filter = "all"
        self.current = -1
        self.day = ""
        self.days: list[str] = []
        self._loaded = False
        self._cache: dict[str, DriveData] = {}
        self.list = EventList(self)
        self.list.selected.connect(self.select)
        self.list.activated.connect(lambda i: (self.select(i), self.open_in_replay()))
        self.list.filterPicked.connect(self.set_filter)
        self.timeline = Timeline(self)
        self.replay_btn = Button("Open in Replay", "F4", kind="primary", height=38, parent=self)
        self.map_btn = Button("Show on GPS map", "M", height=38, parent=self)
        self.note_btn = Button("Add a note", "N", height=38, parent=self)
        self.verdict_btn = Button("Change verdict", "V", height=38, parent=self)
        self.replay_btn.clicked.connect(self.open_in_replay)
        self.map_btn.clicked.connect(self.show_on_map)
        self.note_btn.clicked.connect(self.ask_note)
        self.verdict_btn.clicked.connect(self.verdict_menu)
        self.prev_day = Button(icon=paint.PREV, height=30, parent=self)
        self.next_day = Button(icon=paint.NEXT, height=30, parent=self)
        self.prev_day.clicked.connect(lambda: self.step_day(-1))
        self.next_day.clicked.connect(lambda: self.step_day(1))
        self.prev_day.setToolTip("Previous day (PgUp)")
        self.next_day.setToolTip("Next day (PgDn)")
        self._enable()

    # ---- loading ----

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.reload()

    def reload(self, day: str | None = None) -> None:
        """Read the drives of ``day`` (default: the newest day with drives)."""
        self.drives = []
        if getattr(self.session, "demo", False) and not self._database_rows():
            from ..replaymodel import demo_drive_view
            view = demo_drive_view()
            self.drives = [load_drive(view)]
            self.days, self.day = [], datetime.fromtimestamp(view.meta["epoch_unix"]).strftime("%Y-%m-%d")
        else:
            rows = self._database_rows()
            self.days = sorted({r.started[:10] for r in rows if r.started}, reverse=True)
            self.day = day if day in self.days else (self.days[0] if self.days else "")
            for r in sorted((r for r in rows if r.started[:10] == self.day), key=lambda r: r.started):
                data = self._cache.get(str(r.ref))
                if data is None:
                    try:
                        view = self.session.logs.read(r.ref)
                    except (OSError, ValueError) as exc:
                        self.message.emit(f"cannot read {r.name}: {exc}")
                        continue
                    data = load_drive(view, r.ref, getattr(r.ref, "db", None))
                    self._cache[str(r.ref)] = data
                self.drives.append(data)
        self._rebuild()

    def _database_rows(self) -> list:
        logs = getattr(self.session, "logs", None)
        try:
            return [r for r in logs.drives() if r.kind == "drive"] if logs is not None else []
        except (OSError, ValueError, AttributeError):
            return []

    def _rebuild(self, keep=None) -> None:
        rows = [Row(d, e) for d in self.drives for e in d.events]
        rows.sort(key=lambda r: ((r.drive.epoch or 0) + r.event.t), reverse=True)     # newest first
        for r in rows:
            r.event.data.pop("closest", None)
        near = [r for r in rows if r.event.kind == "near_stall"]
        if near:
            min(near, key=lambda r: r.event.value).event.data["closest"] = True
        self.rows = rows
        self.set_filter(self.filter, keep=keep)

    def counts(self) -> dict[str, int]:
        return ev.counts([r.event for r in self.rows])

    def set_filter(self, key: str, keep=None) -> None:
        self.filter = key if key == "all" or key in ev.KINDS else "all"
        self.list.filter = self.filter
        self.shown = [r for r in self.rows if (r.event.kind not in QUIET if self.filter == "all"
                                               else r.event.kind == self.filter)]
        if keep is not None and keep in self.shown:
            self.current = self.shown.index(keep)
        else:
            self.current = 0 if self.shown else -1
        self.list.set_rows(self.shown, self.counts(), self.current)
        self._show_current()

    def cycle_filter(self) -> None:
        keys = [k for k, _ in self.list.chips()]
        self.set_filter(keys[(keys.index(self.filter) + 1) % len(keys)] if self.filter in keys else "all")

    def step_day(self, direction: int) -> None:
        if not self.days or self.day not in self.days:
            return
        i = self.days.index(self.day) - direction       # days are newest first
        if 0 <= i < len(self.days):
            self.reload(self.days[i])

    def select(self, i: int) -> None:
        if 0 <= i < len(self.shown):
            self.current = i
            self.list.current = i
            self.list.reveal()
            self.list.update()
            self._show_current()

    def selected_row(self) -> Row | None:
        return self.shown[self.current] if 0 <= self.current < len(self.shown) else None

    def _show_current(self) -> None:
        self.timeline.set_row(self.selected_row())
        self._enable()
        self.update()

    def _enable(self) -> None:
        row = self.selected_row()
        for b in (self.replay_btn, self.map_btn, self.note_btn, self.verdict_btn):
            b.setEnabled(row is not None)
        self.prev_day.setEnabled(bool(self.days) and self.day in self.days and self.days.index(self.day) < len(self.days) - 1)
        self.next_day.setEnabled(bool(self.days) and self.day in self.days and self.days.index(self.day) > 0)

    # ---- actions ----

    def _open(self, row: Row) -> bool:
        replay = self.replay
        if replay is None:
            self.openRequested.emit(row.drive.ref)
            return False
        view = getattr(replay, "view", None)
        same = view is not None and not view.live and str(view.path) == str(row.drive.ref)
        if not same:
            if isinstance(row.drive.ref, DriveRef) or not getattr(self.session, "demo", False):
                if not replay.open(row.drive.ref):
                    self.message.emit(f"Cannot open {row.drive.name}")
                    return False
            else:
                from ..replaymodel import demo_drive_view
                replay.show_view(demo_drive_view())
        origin = float(replay.view.origin) if replay.view is not None else row.drive.origin
        replay.seek(row.event.t - origin)
        return True

    def _show(self, screen_name: str, default: int) -> None:
        show = getattr(self.shell, "show_screen", None)
        if callable(show):
            from .. import main_window as mw
            show(getattr(mw, screen_name, default))

    def open_in_replay(self) -> None:
        row = self.selected_row()
        if row is not None and self._open(row):
            self._show("REPLAY", 3)

    def show_on_map(self) -> None:
        row = self.selected_row()
        if row is not None and self._open(row):
            self._show("MAP", 5)

    def set_note(self, text: str) -> str:
        return self._store(note=text)

    def set_verdict(self, text: str) -> str:
        return self._store(verdict=text)

    def _store(self, note: str | None = None, verdict: str | None = None) -> str:
        row = self.selected_row()
        if row is None:
            return ""
        e = row.event
        if note is not None:
            e.note = note
        if verdict is not None:
            e.verdict_override = "" if verdict == e.verdict else verdict
        ref, database = row.drive.ref, row.drive.database
        if database is None or not isinstance(ref, DriveRef):
            text = "Kept for now: a simulated drive has no database to save it in"
        else:
            try:
                eventnotes.save(database, ref.drive, e.key, note=note,
                                verdict=None if verdict is None else e.verdict_override, kind=e.kind, t=e.t)
                text = "Saved with the drive"
            except (OSError, ValueError) as exc:
                text = f"Could not save: {exc}"
        self.message.emit(text)
        self.list.update()
        self.update()
        return text

    def ask_note(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Note", f"{row.wall} {row.event.title}", row.event.note)
        if ok:
            self.set_note(text.strip())

    def verdict_menu(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        e = row.event
        menu = QMenu(self)
        computed = QAction(f"Computed: {e.verdict}", menu, checkable=True)
        computed.setChecked(not e.verdict_override)
        computed.triggered.connect(lambda: self.set_verdict(e.verdict))
        menu.addAction(computed)
        menu.addSeparator()
        for choice in ev.VERDICT_CHOICES.get(e.kind, ()):
            if choice == e.verdict:
                continue
            action = QAction(choice, menu, checkable=True)
            action.setChecked(choice == e.verdict_override)
            action.triggered.connect(lambda _=False, c=choice: self.set_verdict(c))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Other…", self.ask_verdict)
        menu.exec(QCursor.pos())

    def ask_verdict(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        text, ok = QInputDialog.getText(self, "Verdict", row.event.title, text=row.event.shown_verdict)
        if ok and text.strip():
            self.set_verdict(text.strip())

    # ---- the shell's calls ----

    def refresh(self, _now: float) -> None:
        self.ensure_loaded()

    def retheme(self) -> None:
        self.update()
        self.list.update()
        self.timeline.update()

    def title_text(self) -> str:
        """"2026-11-26 · 2 drives · 57 min" for the status line."""
        minutes = sum(d.duration for d in self.drives) / 60
        n = len(self.drives)
        return f"{self.day} · {n} drive{'s' if n != 1 else ''} · {minutes:.0f} min" if self.day else "no drives"

    def key(self, event) -> bool:
        k = event.key()
        if k == Qt.Key_Down:
            self.select(min(len(self.shown) - 1, self.current + 1))
        elif k == Qt.Key_Up:
            self.select(max(0, self.current - 1))
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.open_in_replay()
        elif k == Qt.Key_M:
            self.show_on_map()
        elif k == Qt.Key_N:
            self.ask_note()
        elif k == Qt.Key_V:
            self.verdict_menu()
        elif k == Qt.Key_F:
            self.cycle_filter()
        elif k == Qt.Key_PageUp:
            self.step_day(-1)
        elif k == Qt.Key_PageDown:
            self.step_day(1)
        elif k == Qt.Key_Home:
            self.select(0)
        elif k == Qt.Key_End:
            self.select(len(self.shown) - 1)
        else:
            return False
        return True

    # ---- layout / paint ----

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        left = int(min(560, max(420, w * 0.41)))
        self._left = left
        self.list.setGeometry(0, 44, left, h - 44)
        self.prev_day.setGeometry(left - 84, 7, 36, 30)
        self.next_day.setGeometry(left - 44, 7, 36, 30)
        x = left + 16
        rw = w - x - 16
        self._detail = QRectF(x, 12, rw, h - 24)
        by = h - 12 - 38
        bx = x
        for b in (self.replay_btn, self.map_btn, self.note_btn, self.verdict_btn):
            bw = b.sizeHint().width()
            b.setGeometry(bx, by, bw, 38)
            bx += bw + 8
        self._why_top = 12 + 40
        self.timeline.setGeometry(x, int(self._why_top + 150), rw, max(80, int(by - 10 - (self._why_top + 150))))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        left = getattr(self, "_left", 560)
        p.fillRect(QRectF(left, 0, 2, self.height()), T.rule)
        p.setFont(T.font(14, 800))
        p.setPen(T.fg)
        p.drawText(QRectF(12, 0, left - 100, 44), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(T.font(14, 800), self.title_text(), left - 100))
        p.fillRect(QRectF(0, 43, left, 1), T.line)
        row = self.selected_row()
        d = getattr(self, "_detail", QRectF(left + 16, 12, 600, 600))
        if row is None:
            p.setFont(T.font(15, 600))
            p.setPen(T.fg2)
            p.drawText(d, Qt.AlignTop | Qt.AlignLeft, "No events" if self._loaded else "Loading the drives…")
            p.end()
            return
        e = row.event
        big_m, big = T.font(20, 700, mono=True), T.font(20, 800)
        p.setFont(big_m)
        p.setPen(T.fg)
        tw = paint.text_w(big_m, row.wall)
        p.drawText(QPointF(d.x(), d.y() + 26), row.wall)
        p.setFont(big)
        title = e.title
        p.drawText(QPointF(d.x() + tw + 14, d.y() + 26), paint.elide(big, title, d.width() - tw - 14))
        if e.data.get("closest"):
            tx = d.x() + tw + 14 + paint.text_w(big, title) + 14
            p.setFont(T.font(13, 700))
            p.setPen(T.alarm)
            p.drawText(QPointF(tx, d.y() + 25), "closest call of the day")
        # why
        box = QRectF(d.x(), self._why_top, d.width(), 140)
        p.fillRect(box, T.sunk)
        verdict = e.shown_verdict
        lead = f"Why: {verdict[0].upper() + verdict[1:]}" + (" (your verdict)" if e.verdict_override else "") + ". "
        text = lead + e.why + (f"  Note: {e.note}" if e.note else "")
        y = paint.paragraph(p, box.x() + 12, box.y() + 8, box.width() - 24, text, T.font(14, 600), T.fg, 20, 6)
        p.setFont(T.font(12, 600))
        p.setPen(T.fg2)
        p.drawText(QRectF(box.x() + 12, box.bottom() - 22, box.width() - 24, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   f"{row.drive.name} · {e.label.lower()} · worked out from the log")
        p.end()
