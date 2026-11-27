"""The frame shared by every screen: the one top row (wordmark, F1-F8 keys,
status, theme, MARK) as on the design artboards and in the web app, the stall
banner and the alarm strip."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractButton, QWidget

from . import adapters as ad
from . import paint
from .theme import T

#: the eight screens, in the order of their keys (the Main artboard; F6 is
#: "GPS" in the bar, as on the screen artboards and in the web app)
TABS = (("F1", "Live"), ("F2", "Drive"), ("F3", "Events"), ("F4", "Replay"),
        ("F5", "Power"), ("F6", "GPS"), ("F7", "Bytes"), ("F8", "Setup"))
#: shown after them only while a ROM is open (--rom): no key of its own
TUNE_TAB = ("ROM", "Tune")


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _rate(hz: float) -> str:
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else (f"{hz:.1f} Hz" if hz < 100 else f"{hz:.0f} Hz")


def source_chip(name: str, st: ad.SourceStatus, key: str = "") -> tuple:
    """(key, icon, style, text, mono figures) for an ECU / BLACK BOX chip.
    Wording as the web app's top bar (web/src/ui/shell/TopBar.tsx)."""
    sim = " SIM" if st.simulated else ""
    if st.state == ad.OK:
        monos = (_rate(st.rate_hz),) + ((f"{st.errors} err",) if name == "ECU" and st.errors else ())
        if st.link == "OBD2":               # standard PIDs, not Honda reads
            return (key, "ok", "", f"{name}{sim} OBD2", monos)
        return (key, "ok", "", f"{name}{sim}", monos)
    if st.headline and st.state in (ad.SILENT, ad.LOST):
        # no ECU data, and why (the CYD's own K-line: no cable, the ECU not answering)
        return (key, "x", "bad", st.headline,
                (f"{st.silent_s:.1f} s",) if st.state == ad.SILENT and st.silent_s else ())
    if st.state == ad.CONNECTING:
        return (key, "half", "caution", f"{name}{sim} {st.detail or 'connecting'}", ())
    if st.state == ad.SILENT:
        return (key, "x", "bad", f"{name}{sim} silent", (f"{st.silent_s:.1f} s",))
    if st.state == ad.LOST:
        return (key, "x", "bad", f"{name} port lost", ())
    return (key, "off", "", f"{name} {st.detail or 'off'}", ())


def rec_chip(active: bool, elapsed: float) -> tuple:
    return ("rec", "rec", "", "REC", (_mmss(elapsed),)) if active else (
        "rec", "off", "", "REC off", ())


def stall_chip(st: ad.StallStatus) -> tuple:
    if st.state == "armed":
        return ("", "", "", "Stall catcher armed", ())          # plain text, as on the artboard
    if st.state == "standby":
        return ("", "", "", "Stall catcher waits for engine", ())
    if st.state == "saving":
        return ("", "dot", "bad", f"STALL CAUGHT, saving +{st.post_left_s:.0f} s", ())
    if st.state == "triggered":
        return ("", "dot", "bad", "STALL CAUGHT", ())
    return ("", "", "", "Stall catcher off", ())


def cyd_chip(st) -> tuple:
    """The CYD black box: link, its SD log, whether it runs this map. The
    figures (link, SD, map) are drawn only when the bar has room for them."""
    link = getattr(st, "link", "off")
    if link == "off":
        return ("cyd", "off", "", "CYD off", ())
    if link == "connecting":
        return ("cyd", "half", "caution", "CYD connecting", ())
    if link == "lost":
        return ("cyd", "x", "bad", "CYD port lost", ())
    words = {"usb": "USB", "relay": "RELAY", "kline": "K-LINE", "nocable": "NO CABLE", "demo": "SIM"}
    sd = {"rec": f"SD REC #{st.drive_no}" if st.drive_no else "SD REC", "ok": "SD idle",
          "none": "NO SD"}.get(st.sd, "")
    map_ = {True: "map ok", False: "MAP DIFFERS", None: ""}[st.map_ok]
    bad = st.sd == "none" or st.map_ok is False or link == "nocable"
    monos = tuple(x for x in (words.get(link, link.upper()), sd, map_) if x)
    text = ("CYD: no K-line cable" if link == "nocable"
            else "CYD map mismatch" if st.map_ok is False
            else "CYD no SD card" if st.sd == "none" else "CYD black box")
    return ("cyd", "x" if bad else "ok", "caution" if bad else "", text, monos)


#: the status items of the top bar: 12-13 px semibold and a 10 px lamp (the Live artboard)
CHIP_PX = 12
CHIP_LAMP = 9.0
CHIP_GAP = 12
#: horizontal padding of a key in the top row
KEY_PAD = 9
#: the top row: 40 px + the 2 px rule under it
TOPBAR_H = 42


def _chip_fonts():
    return T.font(CHIP_PX, 600), T.font(CHIP_PX, 700, mono=True)


def chip_width(chip: tuple, figures: bool = True) -> float:
    _, _, style, text, monos = chip
    font, mono = _chip_fonts()
    w = (CHIP_LAMP + 6 if chip[1] else 0) + paint.text_w(T.font(CHIP_PX, 800) if style == "bad" else font, text)
    if figures:
        for fig in monos:
            w += 6 + paint.text_w(mono, fig)
    if style == "bad":
        w += 12                             # the alarm fill reaches 6 px past either end
    return float(int(w + 0.99))


def draw_chip(p: QPainter, x: float, y: float, chip: tuple, blink_on: bool = True,
              height: float = 32, figures: bool = True) -> float:
    """One status item at (x, y): lamp + text (+ mono figures) with no box, as
    in the web app's top bar. 'bad' is filled with the alarm colour (STALL
    CAUGHT, a lost link), 'caution' turns the lamp yellow. Returns its width."""
    _, icon, style, text, monos = chip
    w = chip_width(chip, figures)
    ink, lamp = T.fg, T.fg
    font, mono = _chip_fonts()
    if style == "bad":
        p.fillRect(QRectF(x, y + (height - 24) / 2, w, 24), T.alarm)
        ink = lamp = T.on_alarm
        font = T.font(CHIP_PX, 800)
        x += 6
    elif style == "caution":
        lamp = T.warn
    elif icon == "off":
        lamp = T.rule
    tx = x
    if icon:
        paint.chip_icon(p, icon, x, y + (height - CHIP_LAMP) / 2, lamp, blink_on, size=CHIP_LAMP)
        tx += CHIP_LAMP + 6
    p.setFont(font)
    p.setPen(ink)
    p.drawText(QRectF(tx, y, w, height), Qt.AlignVCenter | Qt.AlignLeft, text)
    tx += paint.text_w(font, text)
    if figures:
        p.setFont(mono)
        for fig in monos:
            tx += 6
            p.drawText(QRectF(tx, y, w, height), Qt.AlignVCenter | Qt.AlignLeft, fig)
            tx += paint.text_w(mono, fig)
    return w


class BarButton(QAbstractButton):
    """A full-height cell at the right end of the top bar: MARK (accent fill)
    or the theme toggle. ``show_text`` False draws only the icon and the key;
    the text stays the button's name (tooltip, tests)."""

    def __init__(self, text: str, key: str, kind: str = "normal", icon=None,
                 show_text: bool = True, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.key, self.kind, self.icon_name, self.show_text = key, kind, icon, show_text
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)

    def set_text(self, text: str) -> None:
        if text != self.text():
            self.setText(text)
            self.updateGeometry()
            self.update()

    @staticmethod
    def _keyfont():
        return T.font(12, 700, mono=True)

    def sizeHint(self) -> QSize:
        w = 2 + 10 + 10
        if self.icon_name:
            w += CHIP_LAMP + 6
        if self.show_text and self.text():
            w += paint.text_w(T.font(13, 800), self.text()) + 6
        w += paint.text_w(self._keyfont(), self.key) + 8
        return QSize(int(w + 0.99), TOPBAR_H - 2)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        lit = self.kind == "primary" or self.isDown()
        fill, ink = (T.accent, T.on_accent) if lit else (T.bg, T.fg)
        p.fillRect(r, fill)
        p.fillRect(QRectF(0, 0, 2, r.height()), T.rule)
        x, cy = 12.0, r.height() / 2
        if self.icon_name:
            paint.chip_icon(p, self.icon_name, x, cy - CHIP_LAMP / 2, ink, size=CHIP_LAMP)
            x += CHIP_LAMP + 6
        if self.show_text and self.text():
            font = T.font(13, 800)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(QRectF(x, 0, r.width(), r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       self.text())
            x += paint.text_w(font, self.text()) + 6
        kf = self._keyfont()
        kw = paint.text_w(kf, self.key) + 8
        p.setPen(T.cpen(ink, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x + 0.75, cy - 8.25, kw - 1.5, 16.5))
        p.setFont(kf)
        p.drawText(QRectF(x, cy - 9, kw, 18), Qt.AlignCenter, self.key)
        p.end()


class TopBar(QWidget):
    """The one top row of the design artboards and of the web app: HONDALOG,
    the F1-F8 keys (Tune after them while a ROM is open), the always-on status
    (ECU, REC, stall catcher, CYD black box; the separate ESP32 black box only
    with ``show_bb``), the theme toggle (F9) and MARK (Space).

    It is both the shell's ``status`` and its ``tabs``: ``picked`` /
    ``set_tabs`` / ``set_current`` / ``_rects`` for the keys, ``sync`` /
    ``_chips`` / ``_chip_rects`` for the status. ``info`` (the screen's own
    title, or the link summary) is kept here and drawn by the alarm strip."""

    picked = Signal(int)
    connectionMenu = Signal(QPoint)
    recordClicked = Signal()
    cydClicked = Signal()
    infoChanged = Signal(str)

    def __init__(self, parent=None, tabs=TABS, show_bb: bool = False):
        super().__init__(parent)
        self.setFixedHeight(TOPBAR_H)
        self.setMouseTracking(True)
        self.show_bb = show_bb
        self.tabs: tuple = tuple(tabs)
        self.current = 0
        self.info = ""
        self._rects: list[QRectF] = []
        self._chips: tuple = ()
        self._chip_rects: list = []
        self._blink = True
        #: "DEMO DATA" (--demo) or "SIMULATOR" (a simulated ECU): worked out here, drawn by the
        #: bottom strip (as in the web app: the top bar has no room for it)
        self.badge = ""
        self.theme_btn = BarButton(T.label, "F9", icon="half", show_text=False, parent=self)
        self.mark_btn = BarButton("MARK", "Space", kind="primary", parent=self)
        self.theme_btn.clicked.connect(T.toggle)
        self.retheme()

    # ---- the keys ----

    def set_tabs(self, tabs) -> None:
        tabs = tuple(tabs)
        if tabs != self.tabs:
            self.tabs = tabs
            self.update()

    def set_current(self, index: int) -> None:
        if index != self.current:
            self.current = index
            self.update()

    def set_info(self, text: str) -> None:
        if text != self.info:
            self.info = text
            self.infoChanged.emit(text)

    # ---- the status ----

    def retheme(self) -> None:
        self.theme_btn.set_text(T.label)
        self.theme_btn.setToolTip(f"Theme: {T.label.title()}. F9 switches Dracula / Darker")
        self.mark_btn.setToolTip("Mark this moment (Space)")
        self._place()
        self.update()

    def _place(self) -> None:
        h = TOPBAR_H - 2
        mw = self.mark_btn.sizeHint().width()
        tw = self.theme_btn.sizeHint().width()
        self.mark_btn.setGeometry(self.width() - mw, 0, mw, h)
        self.theme_btn.setGeometry(self.width() - mw - tw, 0, tw, h)

    def resizeEvent(self, _event) -> None:
        self._place()

    def sync(self, session, now: float) -> None:
        chips = (source_chip("ECU", session.ecu_status, "ecu"),
                 *((source_chip("BLACK BOX", session.bb_status),) if self.show_bb else ()),
                 rec_chip(session.recording.active, session.recording.elapsed_s()),
                 stall_chip(session.stall),
                 cyd_chip(getattr(session, "cyd", None)))
        blink = (now % 1.2) < 0.6
        badge = ("DEMO DATA" if getattr(session, "demo", False) else
                 "SIMULATOR" if getattr(session.ecu_status, "simulated", False) else "")
        if chips != self._chips or badge != self.badge or (blink != self._blink and session.recording.active):
            self._chips, self._blink, self.badge = chips, blink, badge
            self.update()

    @staticmethod
    def badge_width(text: str) -> float:
        return paint.text_w(T.font(11, 800, mono=True), text) + 12 if text else 0.0

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for i, rect in enumerate(self._rects):
            if rect.contains(pos):
                self.picked.emit(i)
                return
        for key, rect in self._chip_rects:
            if rect.contains(pos):
                if key == "ecu":
                    self.connectionMenu.emit(self.mapToGlobal(rect.bottomLeft().toPoint()))
                elif key == "rec":
                    self.recordClicked.emit()
                elif key == "cyd":
                    self.cydClicked.emit()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        hot = any(r.contains(pos) for r in self._rects) or any(
            k in ("ecu", "rec", "cyd") and r.contains(pos) for k, r in self._chip_rects)
        self.setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)

    def _fit(self, room: float) -> tuple:
        """The chips that fit in ``room`` px: the figures go first (CYD's, then
        the ECU rate, the REC clock last), then whole items from the right."""
        chips = list(self._chips)
        figures = [True] * len(chips)

        def need():
            return (sum(chip_width(c, f) for c, f in zip(chips, figures))
                    + CHIP_GAP * max(0, len(chips) - 1))
        drop = sorted(range(len(chips)), key=lambda i: ("cyd", "", "ecu", "rec").index(chips[i][0])
                      if chips[i][0] in ("cyd", "", "ecu", "rec") else 1)
        for i in drop:                              # REC's clock goes last
            if need() <= room:
                break
            figures[i] = False
        while chips and need() > room:
            chips.pop()
            figures.pop()
        return chips, figures, need()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), TOPBAR_H - 2
        p.fillRect(self.rect(), T.bg)
        p.fillRect(QRectF(0, h, w, 2), T.rule)
        # the wordmark
        font = T.font(15, 800)
        p.setFont(font)
        p.setPen(T.fg)
        ww = 14 + paint.text_w(font, "HONDALOG") + 14
        p.drawText(QRectF(14, 0, ww, h), Qt.AlignVCenter | Qt.AlignLeft, "HONDALOG")
        p.fillRect(QRectF(ww, 0, 2, h), T.rule)
        x = ww + 2
        # the keys: mono key, 13 px name, the current one filled with the accent
        kfont, nfont = T.font(12, 700, mono=True), T.font(13, 700)
        self._rects = []
        for i, (key, name) in enumerate(self.tabs):
            kw = paint.text_w(kfont, key)
            tw = KEY_PAD + kw + 5 + paint.text_w(nfont, name) + KEY_PAD
            r = QRectF(x, 0, tw, h)
            on = i == self.current
            if on:
                p.fillRect(r, T.accent)
            p.setPen(T.on_accent if on else T.fg)
            p.setFont(kfont)
            p.drawText(QRectF(x + KEY_PAD, 0, tw, h), Qt.AlignVCenter | Qt.AlignLeft, key)
            p.setFont(nfont)
            p.drawText(QRectF(x + KEY_PAD + kw + 5, 0, tw, h), Qt.AlignVCenter | Qt.AlignLeft, name)
            x += tw
            p.fillRect(QRectF(x, 0, 1, h), T.line)
            x += 1
            self._rects.append(r)
        # the status, right-aligned against the theme toggle
        limit = self.theme_btn.x() - 12
        chips, figures, need = self._fit(limit - (x + 2 + 12))
        self._chip_rects = []
        if chips:
            cx = limit - need
            p.fillRect(QRectF(cx - 12 - 2, 0, 2, h), T.rule)
            for chip, fig in zip(chips, figures):
                cw = draw_chip(p, cx, 0, chip, self._blink, height=h, figures=fig)
                self._chip_rects.append((chip[0], QRectF(cx, 0, cw, h)))
                cx += cw + CHIP_GAP
        p.end()


class StallBanner(QWidget):
    """44 px alarm bar laid over the top of the screen, under the top row, so
    the content does not move."""

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
    tone = T.alarm
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
    """Bottom 32 px: the worst active alarm and warning, or 'No active alarms';
    a passing message, else the screen's own title or the link summary (``info``,
    the second row's right side before the bar became one row); the key hints."""

    HINTS = "Space mark · F1–F8 screens · F9 theme · Esc acknowledge"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self._alarm = ""
        self._warn = ""
        self.right_text = self.HINTS
        self.info = ""
        self._rev = -1
        self._message = ""
        self._message_until = 0.0
        self.badge = ""                     # DEMO DATA / SIMULATOR (the top bar works it out)

    def set_badge(self, text: str) -> None:
        if text != self.badge:
            self.badge = text
            self.update()

    def show_message(self, text: str, now: float, seconds: float = 6.0) -> None:
        self._message, self._message_until = text, now + seconds
        self.update()

    def set_info(self, text: str) -> None:
        if text != self.info:
            self.info = text
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
        bw = TopBar.badge_width(self.badge)
        if bw:                                  # DEMO DATA / SIMULATOR, yellow, left of the key hints
            bx = w - right_w - 8 - bw
            p.fillRect(QRectF(bx, 7, bw, 20), T.warn)
            p.setFont(T.font(11, 800, mono=True))
            p.setPen(T.on_warn)
            p.drawText(QRectF(bx, 7, bw, 20), Qt.AlignCenter, self.badge)
            right_w += bw + 16
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
        elif self.info and room - x - 36 > 80:
            p.setFont(mono)
            p.setPen(T.fg2)
            p.drawText(QRectF(x + 12, 2, room - x - 36, 30), Qt.AlignVCenter | Qt.AlignRight,
                       paint.elide(mono, self.info, room - x - 36))
            p.fillRect(QRectF(room - 12, 9, 1, 16), T.line)
        p.setFont(mono)
        p.setPen(T.fg)
        p.drawText(QRectF(w - right_w, 2, right_w - 10, 30), Qt.AlignVCenter | Qt.AlignRight,
                   self.right_text)
        p.end()
