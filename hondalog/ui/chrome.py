"""The frame shared by every screen (HANDOFF section 3): status bar, tab bar,
stall banner and the alarm strip."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from . import adapters as ad
from . import paint
from .controls import Button
from .theme import T

TABS = (("F1", "Sensors"), ("F2", "Drive"), ("F3", "Map 3D"), ("F4", "Replay"),
        ("F5", "Raw tools"), ("F6", "Tune"), ("F7", "Alarms"), ("F8", "Map"))


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _rate(hz: float) -> str:
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else (f"{hz:.1f} Hz" if hz < 100 else f"{hz:.0f} Hz")


def source_chip(name: str, st: ad.SourceStatus, key: str = "") -> tuple:
    """(key, icon, style, text, mono figures) for an ECU / BLACK BOX chip."""
    sim = " SIM" if st.simulated else ""
    if st.state == ad.OK:
        monos = (_rate(st.rate_hz), f"{st.errors} err") if name == "ECU" else (_rate(st.rate_hz),)
        return (key, "ok", "", f"{name}{sim} OK", monos)
    if st.state == ad.CONNECTING:
        return (key, "half", "caution", f"{name}{sim} {st.detail or 'connecting'}", ())
    if st.state == ad.SILENT:
        return (key, "x", "bad", f"{name}{sim} SILENT", (f"{st.silent_s:.1f} s",))
    if st.state == ad.LOST:
        return (key, "x", "bad", f"{name} PORT LOST", ())
    return (key, "off", "", f"{name} {st.detail or 'off'}", ())


def rec_chip(active: bool, elapsed: float) -> tuple:
    return ("rec", "rec", "", "REC", (_mmss(elapsed),)) if active else (
        "rec", "off", "", "REC stopped", ())


def stall_chip(st: ad.StallStatus) -> tuple:
    if st.state == "armed":
        return ("", "ring", "", "STALL CATCHER ARMED", ())
    if st.state == "standby":
        return ("", "half", "", "STALL CATCHER waits for engine", ())
    if st.state == "saving":
        return ("", "dot", "bad", f"STALL CAUGHT, saving +{st.post_left_s:.0f} s", ())
    if st.state == "triggered":
        return ("", "dot", "bad", "STALL CAUGHT", ())
    return ("", "off", "", "STALL CATCHER off", ())


def chip_width(chip: tuple) -> float:
    _, _, _, text, monos = chip
    w = 10 + 14 + 8 + paint.text_w(T.font(13, 700), text) + 10
    mono = T.font(13, 700, mono=True)
    for fig in monos:
        w += 8 + paint.text_w(mono, fig)
    return float(int(w + 0.99))


def draw_chip(p: QPainter, x: float, y: float, chip: tuple, blink_on: bool = True) -> float:
    """One 32 px chip at (x, y). Returns its width."""
    _, icon, style, text, monos = chip
    w = chip_width(chip)
    r = QRectF(x, y, w, 32)
    if style == "bad":
        fill, ink, edge = T.alarm, T.on_alarm, T.alarm
    elif style == "caution":
        fill, ink, edge = T.warn, T.on_warn, T.warn
    else:
        fill, ink, edge = T.panel, T.fg, T.rule
    paint.frame(p, r, 2, edge, fill)
    paint.chip_icon(p, icon, x + 10, y + 9, ink, blink_on)
    font = T.font(13, 700)
    p.setFont(font)
    p.setPen(ink)
    tx = x + 32
    p.drawText(QRectF(tx, y, w, 32), Qt.AlignVCenter | Qt.AlignLeft, text)
    tx += paint.text_w(font, text)
    mono = T.font(13, 700, mono=True)
    p.setFont(mono)
    for fig in monos:
        tx += 8
        p.drawText(QRectF(tx, y, w, 32), Qt.AlignVCenter | Qt.AlignLeft, fig)
        tx += paint.text_w(mono, fig)
    return w


class StatusBar(QWidget):
    """Wordmark, chips (ECU, BLACK BOX, REC, STALL CATCHER), theme, MARK."""

    connectionMenu = Signal(QPoint)
    recordClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.theme_btn = Button("DAYLIGHT", "F9", icon="half", height=36, parent=self)
        self.mark_btn = Button("MARK", "Space", kind="primary", height=36, parent=self)
        self.mark_btn.setMinimumWidth(150)
        self.theme_btn.clicked.connect(T.toggle)
        self._chips: tuple = ()
        self._rects: list = []
        self._blink = True
        self.retheme()

    def retheme(self) -> None:
        self.theme_btn.set_text("NIGHT" if T.night else "DAYLIGHT")
        self._place()

    def _place(self) -> None:
        mw = max(150, self.mark_btn.sizeHint().width())
        tw = self.theme_btn.sizeHint().width()
        self.mark_btn.setGeometry(self.width() - 8 - mw, 3, mw, 36)
        self.theme_btn.setGeometry(self.width() - 8 - mw - 8 - tw, 3, tw, 36)

    def resizeEvent(self, _event) -> None:
        self._place()

    def sync(self, session, now: float) -> None:
        chips = (source_chip("ECU", session.ecu_status, "ecu"),
                 source_chip("BLACK BOX", session.bb_status),
                 rec_chip(session.recording.active, session.recording.elapsed_s()),
                 stall_chip(session.stall))
        blink = (now % 1.2) < 0.6
        if chips != self._chips or (blink != self._blink and session.recording.active):
            self._chips, self._blink = chips, blink
            self.update()

    def mousePressEvent(self, event) -> None:
        for key, rect in self._rects:
            if rect.contains(event.position()):
                if key == "ecu":
                    self.connectionMenu.emit(self.mapToGlobal(rect.bottomLeft().toPoint()))
                elif key == "rec":
                    self.recordClicked.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        p.fillRect(QRectF(0, 42, w, 2), T.rule)
        font = T.font(19, 800, spacing=-0.02)
        p.setFont(font)
        p.setPen(T.fg)
        p.drawText(QRectF(8, 0, 200, 42), Qt.AlignVCenter | Qt.AlignLeft, "hondalog")
        x = 8 + paint.text_w(font, "hondalog") + 12
        self._rects = []
        limit = self.theme_btn.x() - 8
        for chip in self._chips:
            cw = chip_width(chip)
            if x + cw > limit:
                break
            draw_chip(p, x, 5, chip, self._blink)
            self._rects.append((chip[0], QRectF(x, 5, cw, 32)))
            x += cw + 8
        p.end()


class TabBar(QWidget):
    picked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.current = 0
        self.info = ""
        self._rects: list[QRectF] = []
        self.setCursor(Qt.PointingHandCursor)

    def set_current(self, index: int) -> None:
        if index != self.current:
            self.current = index
            self.update()

    def set_info(self, text: str) -> None:
        if text != self.info:
            self.info = text
            self.update()

    def mousePressEvent(self, event) -> None:
        for i, rect in enumerate(self._rects):
            if rect.contains(event.position()):
                self.picked.emit(i)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.sunk)
        font = T.font(15, 700)
        x = 8.0
        p.fillRect(QRectF(x, 0, 2, 42), T.rule)
        x += 2
        self._rects = []
        for i, (key, name) in enumerate(TABS):
            tw = 15 + paint.keycap_w(key) + 8 + paint.text_w(font, name) + 15
            r = QRectF(x, 0, tw, 42)
            on = i == self.current
            if on:
                p.fillRect(r, T.fg)
            ink = T.bg if on else T.fg
            p.setPen(ink)
            kw = paint.keycap(p, x + 15, 21, key, ink)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(QRectF(x + 15 + kw + 8, 0, tw, 42), Qt.AlignVCenter | Qt.AlignLeft, name)
            x += tw
            p.fillRect(QRectF(x, 0, 2, 42), T.rule)
            x += 2
            self._rects.append(r)
        mono = T.font(13, 500, mono=True)
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(x + 8, 0, w - x - 16, 42), Qt.AlignVCenter | Qt.AlignRight,
                   paint.elide(mono, self.info, w - x - 16))
        p.fillRect(QRectF(0, 42, w, 2), T.rule)
        p.end()


class StallBanner(QWidget):
    """44 px alarm bar that covers the tab row, so the content does not move."""

    openRequested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.title = ""
        self.cause = ""
        self._open = QRectF()
        self._dismiss = QRectF()
        self.hide()

    def show_stall(self, title: str, cause: str) -> None:
        self.title, self.cause = title, cause
        self.show()
        self.raise_()
        self.update()

    def mousePressEvent(self, event) -> None:
        if self._open.contains(event.position()):
            self.openRequested.emit()
        elif self._dismiss.contains(event.position()):
            self.dismissed.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_banner(p, QRectF(self.rect()), self.title, self.cause, self)
        p.end()


def paint_banner(p: QPainter, r: QRectF, title: str, cause: str, owner=None) -> None:
    """Shared with the sample on the Alarms screen."""
    p.fillRect(r, T.alarm)
    ink = T.on_alarm
    cy = r.center().y()
    p.setRenderHint(QPainter.Antialiasing, True)
    paint.shape(p, paint.OCTAGON, r.x() + 12, cy - 11, 22, ink)
    p.setRenderHint(QPainter.Antialiasing, False)
    font = T.font(18, 800)
    p.setFont(font)
    p.setPen(ink)
    x = r.x() + 46
    p.drawText(QRectF(x, r.y(), r.width(), r.height()), Qt.AlignVCenter | Qt.AlignLeft, title)
    x += paint.text_w(font, title) + 14
    bfont = T.font(14, 700)
    dw = 28 + paint.text_w(bfont, "Dismiss") + 8 + paint.keycap_w("Esc")
    ow = 28 + paint.text_w(bfont, "Open in replay") + 8 + paint.keycap_w("Enter")
    dismiss = QRectF(r.right() - 8 - dw, r.y() + 4, dw, 36)
    open_ = QRectF(dismiss.x() - 8 - ow, r.y() + 4, ow, 36)
    cfont = T.font(14, 700)
    p.setFont(cfont)
    p.drawText(QRectF(x, r.y(), open_.x() - x - 10, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
               paint.elide(cfont, cause, open_.x() - x - 10))
    # "Open in replay": panel ground, so it is the obvious next step
    paint.frame(p, open_, 2, T.panel, T.panel)
    tone = T.fg if T.night else T.alarm
    p.setFont(bfont)
    p.setPen(tone)
    p.drawText(QRectF(open_.x() + 14, open_.y(), ow, 36), Qt.AlignVCenter | Qt.AlignLeft,
               "Open in replay")
    paint.keycap(p, open_.right() - 14 - paint.keycap_w("Enter"), open_.center().y(),
                 "Enter", tone)
    paint.frame(p, dismiss, 2, ink)
    p.setFont(bfont)
    p.setPen(ink)
    p.drawText(QRectF(dismiss.x() + 14, dismiss.y(), dw, 36), Qt.AlignVCenter | Qt.AlignLeft,
               "Dismiss")
    paint.keycap(p, dismiss.right() - 14 - paint.keycap_w("Esc"), dismiss.center().y(),
                 "Esc", ink)
    if owner is not None:
        owner._open, owner._dismiss = open_, dismiss


class AlarmStrip(QWidget):
    """Bottom 32 px: the worst active alarm and warning, or 'No active alarms'."""

    HINTS = "Space mark · F1–F8 screens · F9 theme · Esc acknowledge"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self._alarm = ""
        self._warn = ""
        self.right_text = self.HINTS
        self._rev = -1
        self._message = ""
        self._message_until = 0.0

    def show_message(self, text: str, now: float, seconds: float = 6.0) -> None:
        self._message, self._message_until = text, now + seconds
        self.update()

    def set_right(self, text: str) -> None:
        if text != self.right_text:
            self.right_text = text
            self.update()

    def sync(self, alarms, now: float) -> None:
        if self._message and now > self._message_until:
            self._message = ""
            self.update()
        if alarms.revision == self._rev:
            return
        self._rev = alarms.revision
        active = alarms.active()
        al = [e for e in active if e.severity == "alarm"]
        wa = [e for e in active if e.severity == "warn"]

        def one(rows):
            if not rows:
                return ""
            e = rows[0]
            more = f"  (+{len(rows) - 1})" if len(rows) > 1 else ""
            return f"{e.source} {e.label} {e.value_text} — {e.limit_text}{more}"
        self._alarm, self._warn = one(al), one(wa)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), T.panel)
        p.fillRect(QRectF(0, 0, w, 2), T.rule)
        font = T.font(14, 800)
        x = 0.0
        mono = T.font(13, 500, mono=True)
        right_w = paint.text_w(mono, self.right_text) + 20
        room = w - right_w
        for text, fill, ink, path in ((self._alarm, T.alarm, T.on_alarm, paint.OCTAGON),
                                      (self._warn, T.warn, T.on_warn, paint.TRIANGLE)):
            if not text:
                continue
            avail = room - x - 48
            if avail < 60:
                break
            text = paint.elide(font, text, avail)
            sw = 12 + 16 + 8 + paint.text_w(font, text) + 12
            p.fillRect(QRectF(x, 2, sw, 30), fill)
            p.setRenderHint(QPainter.Antialiasing, True)
            paint.shape(p, path, x + 12, 9, 16, ink)
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(QRectF(x + 36, 2, sw, 30), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += sw
        if not self._alarm and not self._warn:
            paint.chip_icon(p, "ok", 12, 10, T.fg)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(QRectF(34, 2, 300, 30), Qt.AlignVCenter | Qt.AlignLeft, "No active alarms")
            x = 34 + paint.text_w(font, "No active alarms") + 12
        if self._message:
            mfont = T.font(14, 600)
            p.setFont(mfont)
            p.setPen(T.fg)
            p.drawText(QRectF(x + 12, 2, room - x - 24, 30), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mfont, self._message, room - x - 24))
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(w - right_w, 2, right_w - 10, 30), Qt.AlignVCenter | Qt.AlignRight,
                   self.right_text)
        p.end()
