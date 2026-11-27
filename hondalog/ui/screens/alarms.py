"""F8 Setup > Alarms (the same page as the web app's Setup > Alarms,
web/src/ui/setup/AlarmsTab.tsx): the live alarm list, newest first, with
Acknowledge (Esc); the address map's [[alarm]] rules with the live state of
each; and the shape key."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ... import addressmap
from .. import paint
from ..controls import Button, Card
from ..theme import T

SHAPES = (("ok", "filled square: link up, data flowing"),
          ("off", "hollow square: off or not connected"),
          ("half", "half circle: working on it"),
          ("ring", "ring: stall catcher armed"),
          ("tri", "triangle + yellow band: warning limit"),
          ("oct", "octagon + filled tile: alarm limit"),
          ("unv", "dashed box: address is a guess"))


def _num(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def rule_text(rule) -> str:
    """The condition of a rule in words, as the web app writes it (``ruleText``)."""
    ch = rule.channel or ""
    parts = []
    if rule.low is not None:
        parts.append(f"{ch} < {_num(rule.low)}")
    if rule.high is not None:
        parts.append(f"{ch} > {_num(rule.high)}")
    if rule.rate_low is not None:
        parts.append(f"{ch} falls faster than {_num(rule.rate_low)}/s")
    if rule.rate_high is not None:
        parts.append(f"{ch} rises faster than {_num(rule.rate_high)}/s")
    if rule.when:
        parts.append(rule.when.strip())
    text = " or ".join(parts) or "(no condition)"
    if rule.hold_s:
        text += f" for {_num(rule.hold_s)} s"
    if rule.gate:
        text += f" while {rule.gate.strip()}"
    return text


def map_rules(session) -> tuple[list, str]:
    """(the address map's alarm rules, a problem or ""): the map the ECU decodes
    with, else the file Bytes > Address map edits, else the shipped map."""
    amap = getattr(getattr(session, "ecu", None), "amap", None)
    if amap is not None and hasattr(amap, "alarms"):
        return list(amap.alarms), ""
    try:
        from .bytesmap import map_path_for
        path, _ = map_path_for(session)
        path = path if Path(path).exists() else addressmap.DEFAULT_MAP
        return list(addressmap.load(path).alarms), ""
    except Exception as exc:                # a map that does not load: say so, do not fall over
        return [], str(exc)


class ShapeCard(Card):
    def __init__(self, parent=None):
        super().__init__("Shape key", parent=parent)

    def paint_body(self, p, x, y, w, h) -> None:
        font = T.font(14, 700)
        step = min(26.0, h / len(SHAPES))
        for n, (kind, text) in enumerate(SHAPES):
            cy = y + 4 + n * step
            if kind == "unv":
                paint.marker(p, x - 2, cy + (step - 18) / 2, False)
            else:
                paint.chip_icon(p, kind, x, cy + (step - 14) / 2, T.fg)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(QRectF(x + 24, cy, w - 24, step), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(font, text, w - 24))


class AlarmList(Card):
    """Active alarms first (filled), then the cleared ones, newest first."""

    ROW = 34

    def __init__(self, alarms, parent=None):
        super().__init__("Alarm list · live, newest first", parent=parent)
        self.alarms = alarms
        self.rows: list = []
        self.now = 0.0
        self.ack_btn = Button("Acknowledge", "Esc", height=34, parent=self)
        self.ack_btn.px = 13

    def sync(self, now: float) -> None:
        self.now = now
        self.rows = list(self.alarms.active()) + list(self.alarms.history())
        unacked = any(e.t_end is None and not e.acked for e in self.alarms.active())
        self.ack_btn.setEnabled(unacked)
        self.ack_btn.kind = "primary" if unacked else "normal"
        self.ack_btn.update()
        self.update()

    def resizeEvent(self, _event) -> None:
        bw = self.ack_btn.sizeHint().width()
        self.ack_btn.setGeometry(self.width() - 8 - bw, 6, bw, 34)

    def needed_height(self) -> int:
        return 46 + max(1, min(len(self.rows), 6)) * self.ROW + 8

    def paint_body(self, p, x, y, w, h) -> None:
        x, w = 2.0, self.width() - 4.0
        y = 46.0
        body, bold, mono = T.font(14, 600), T.font(14, 800), T.font(14, 700, mono=True)
        if not self.rows:
            p.setFont(body)
            p.setPen(T.fg2)
            p.drawText(QRectF(x + 10, y, w, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       "Nothing yet. Warnings and alarms appear here as they happen.")
        for ev in self.rows[:max(1, int((self.height() - 50) // self.ROW))]:
            active = ev.t_end is None
            r = QRectF(x, y, w, self.ROW)
            if active:
                fill, ink = (T.alarm, T.on_alarm) if ev.severity == "alarm" else (T.warn, T.on_warn)
                p.fillRect(r, fill)
                paint.chip_icon(p, "oct" if ev.severity == "alarm" else "tri", x + 10, y + 9,
                                ink, size=16)
                word = "ALARM" if ev.severity == "alarm" else "WARN"
                age = "active" + (" · ack" if ev.acked else "")
            else:
                ink = T.fg
                paint.chip_icon(p, "off", x + 11, y + 10, ink)
                word = "CLEARED"
                secs = max(0.0, self.now - ev.t_end)
                age = f"{secs:.0f} s ago" if secs < 90 else f"{secs / 60:.0f} min ago"
            p.fillRect(QRectF(x, y + self.ROW - 2, w, 2), T.sunk)
            p.setPen(ink)
            p.setFont(bold)
            p.drawText(QRectF(x + 44, y, 100, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft, word)
            p.setFont(body)
            p.drawText(QRectF(x + 152, y, 190, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(body, f"{ev.source} {ev.label}", 186))
            p.setFont(mono)
            p.drawText(QRectF(x + 346, y, 120, self.ROW - 2), Qt.AlignVCenter | Qt.AlignLeft,
                       ev.value_text)
            p.setFont(body)
            lim = ev.limit_text if active else f"{ev.limit_text} for {ev.t_end - ev.t_start:.1f} s"
            p.drawText(QRectF(x + 470, y, w - 470 - 110, self.ROW - 2),
                       Qt.AlignVCenter | Qt.AlignLeft, paint.elide(body, lim, w - 470 - 114))
            p.setFont(mono)
            p.drawText(QRectF(x, y, w - 10, self.ROW - 2), Qt.AlignVCenter | Qt.AlignRight, age)
            y += self.ROW


class RulesCard(Card):
    """The address map's [[alarm]] rules: name, condition, level, latch, state."""

    ROW = 28

    def __init__(self, parent=None):
        super().__init__("Rules · from the address map", parent=parent)
        self.rules: list = []
        self.states: dict[str, str] = {}
        self.problem = ""

    def set_rules(self, rules, problem: str = "") -> None:
        self.rules, self.problem = list(rules), problem
        self.title = f"Rules · from the address map ({len(self.rules)})"
        self.update()

    def set_states(self, states: dict) -> None:
        if states != self.states:
            self.states = dict(states)
            self.update()

    def needed_height(self) -> int:
        return 30 + 24 + max(1, len(self.rules)) * self.ROW + 30

    def paint_body(self, p, x, y, w, h) -> None:
        head, mono, body = T.font(12, 700, mono=True), T.font(13, 700, mono=True), T.font(13, 600)
        if not self.rules:
            p.setFont(body)
            p.setPen(T.fg2)
            text = "The map has no [[alarm]] rules" + (f" (it does not load: {self.problem})"
                                                         if self.problem else ".")
            p.drawText(QRectF(x, y + 4, w, 40), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, text)
            return
        cols = (0.0, w * 0.22, w * 0.80, w * 0.875, w * 0.94)
        p.setFont(head)
        p.setPen(T.fg)
        for cx, title in zip(cols, ("RULE", "CONDITION", "LEVEL", "LATCH", "STATE")):
            p.drawText(QRectF(x + cx, y, 120, 20), Qt.AlignVCenter | Qt.AlignLeft, title)
        ry = y + 22
        for rule in self.rules:
            if ry + self.ROW > y + h - 22:
                break
            off = not rule.enabled
            p.setPen(T.fg2 if off else T.fg)
            p.setFont(mono)
            p.drawText(QRectF(x + cols[0], ry, cols[1] - 6, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mono, rule.name, cols[1] - 6))
            p.drawText(QRectF(x + cols[1], ry, cols[2] - cols[1] - 8, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mono, rule_text(rule), cols[2] - cols[1] - 8))
            p.setFont(body)
            p.drawText(QRectF(x + cols[2], ry, 60, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       "alarm" if rule.severity == "critical" else "warn")
            p.drawText(QRectF(x + cols[3], ry, 60, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       "yes" if rule.latching else "")
            p.setFont(mono)
            p.drawText(QRectF(x + cols[4], ry, w - cols[4], self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       "off" if off else self.states.get(rule.name, "idle"))
            p.fillRect(QRectF(x, ry + self.ROW - 1, w, 1), T.line)
            ry += self.ROW
        small = T.font(12, 600)
        p.setFont(small)
        p.setPen(T.fg2)
        p.drawText(QRectF(x, y + h - 20, w, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(small, "Rules live in the address map ([[alarm]]): edit them on F7 Bytes > "
                               "Address map. A rule that reads an unverified address says so.", w))


class AlarmsScreen(QWidget):
    SIDE = 380

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list = AlarmList(session.alarms, self)
        self.rules = RulesCard(self)
        self.shapes = ShapeCard(self)
        self.list.ack_btn.clicked.connect(self.acknowledge)
        self.rules.set_rules(*map_rules(session))
        self._rev = -1
        self._t = -1.0

    def acknowledge(self) -> None:
        """Acknowledge (Esc): every active alarm."""
        ack = getattr(self.session, "acknowledge", None)
        (ack if callable(ack) else self.session.alarms.acknowledge)()
        self._rev = -1
        self.refresh(max(self._t, 0.0))

    def reload_rules(self) -> None:
        """The map changed (Bytes > Address map saved, a map imported)."""
        self.rules.set_rules(*map_rules(self.session))
        self._rev = -1

    def rule_states(self) -> dict:
        """rule name -> idle / pending / active / latched, from the alarm engine
        when there is one, else from the active alarms on the rule's channel."""
        alarms = self.session.alarms
        try:
            statuses = alarms.engine.statuses() if getattr(alarms, "engine", None) is not None else None
        except Exception:
            statuses = None
        out = {}
        if statuses is not None:
            for st in statuses:
                out[st.name] = st.state + (f" · {st.count}×" if st.count else "")
            return out
        active = list(alarms.active())
        for rule in self.rules.rules:
            level = "alarm" if rule.severity == "critical" else "warn"
            hit = next((e for e in active if e.channel_id == rule.channel and e.severity == level), None)
            if hit is not None:
                out[rule.name] = "active" + (" · ack" if hit.acked else "")
        return out

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        side = self.SIDE
        lw = w - 16 - side - 8
        lh = min(max(120, self.list.needed_height()), max(120, h - 16 - 160))
        self.list.setGeometry(8, 0, lw, lh)
        self.rules.setGeometry(8, lh + 8, lw, max(60, min(self.rules.needed_height(), h - lh - 16)))
        self.shapes.setGeometry(w - 8 - side, 0, side, 30 + len(SHAPES) * 26 + 10)

    def resizeEvent(self, _event) -> None:
        self._layout()

    def retheme(self) -> None:
        self.update()

    def refresh(self, now: float) -> None:
        if self.session.alarms.revision != self._rev or now - self._t >= 1.0:
            self._rev, self._t = self.session.alarms.revision, now
            self.list.sync(now)
            self.rules.set_states(self.rule_states())
            self._layout()

    def key(self, _event) -> bool:
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.end()
