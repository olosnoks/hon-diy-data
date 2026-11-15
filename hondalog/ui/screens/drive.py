"""F2, driving view: six huge readouts, the stall catcher, MARK (HANDOFF 5.2)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint
from ..controls import Card
from ..session import DRIVE_DEFAULT
from ..theme import T
from ..tiles import DRIVE, Tile

STATE_WORD = {"off": "OFF", "standby": "STANDBY", "armed": "ARMED", "saving": "SAVING",
              "triggered": "TRIGGERED"}
STATE_ICON = {"off": "off", "standby": "half", "armed": "ring", "saving": "oct",
              "triggered": "oct"}


class StallCard(Card):
    def __init__(self, parent=None):
        super().__init__("Stall catcher", parent=parent)
        self.status = None
        self._key = None
        self._lines: list[str] = []
        self._lines_key = None

    def sync(self, status) -> None:
        key = (status.state, round(status.buffer_s, 1), round(status.post_left_s, 1),
               status.count, status.last_file, status.pre_s, status.post_s)
        if key != self._key:
            self._key, self.status = key, status
            self.update()

    def _hot(self) -> bool:
        return self.status is not None and self.status.state in ("triggered", "saving")

    def ground(self):
        return T.alarm if self._hot() else T.panel

    def ink2(self):
        return T.on_alarm if self._hot() else T.fg2

    def paint_body(self, p, x, y, w, h) -> None:
        st = self.status
        if st is None:
            return
        ink = T.on_alarm if self._hot() else T.fg
        # state word with its 44 px icon
        p.setRenderHint(QPainter.Antialiasing, True)
        icon = STATE_ICON[st.state]
        if icon == "ring":
            p.setPen(T.cpen(ink, 10))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(x + 5, y + 9, 34, 34))
        elif icon == "half":
            p.setPen(T.cpen(ink, 6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(x + 3, y + 7, 38, 38))
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawPie(QRectF(x + 3, y + 7, 38, 38), 90 * 16, 180 * 16)
        elif icon == "oct":
            paint.shape(p, paint.OCTAGON, x, y + 4, 44, ink)
        else:
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(T.cpen(ink, 8))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x + 4, y + 8, 36, 36))
        p.setRenderHint(QPainter.Antialiasing, False)
        word = STATE_WORD[st.state]
        font = T.font(52, 800, spacing=-0.02)
        if paint.text_w(font, word) > w - 58:
            font = T.font(38, 800, spacing=-0.02)
        p.setFont(font)
        p.setPen(ink)
        p.drawText(QRectF(x + 58, y - 4, w - 58, 60), Qt.AlignVCenter | Qt.AlignLeft, word)
        y += 62
        # buffer / countdown bar
        body = T.font(14, 700)
        mono = T.font(14, 700, mono=True)
        saving = st.state == "saving"
        left = "Saving after trigger" if saving else "Pre-trigger buffer"
        right = (f"+{st.post_s - st.post_left_s:.1f} / {st.post_s:g} s" if saving
                 else f"{st.buffer_s:.1f} / {st.pre_s:g} s")
        frac = ((st.post_s - st.post_left_s) / st.post_s if saving
                else st.buffer_s / st.pre_s) if st.pre_s and st.post_s else 0.0
        p.setFont(body)
        p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignLeft, left)
        p.setFont(mono)
        p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignRight, right)
        y += 26
        if self._hot():
            paint.track(p, QRectF(x, y, w, 18), frac, ink, ink, T.alarm)
        else:
            paint.track(p, QRectF(x, y, w, 18), frac)
        y += 28
        # one sentence
        if st.state == "triggered" and st.last_file:
            text = f"Saved {st.last_file}. Enter opens it in Replay, Esc dismisses."
        elif st.state == "off":
            text = "Off: the catcher runs while recording. Ctrl+R starts a recording."
        elif st.state == "standby":
            text = (f"Recording. Arms once the engine runs, then fires on RPM = 0 or ECU "
                    f"silent 1 s. Saves {st.pre_s:g} s before and {st.post_s:g} s after.")
        else:
            text = (f"Fires on RPM = 0 after running, or ECU silent 1 s. Saves "
                    f"{st.pre_s:g} s before and {st.post_s:g} s after.")
        font = T.font(14, 600)
        p.setPen(ink)
        if self._lines_key != (text, w):
            self._lines_key = (text, w)
            self._lines = paint.wrap(font, text, w)
        p.setFont(font)
        for line in self._lines[:4]:
            p.drawText(QRectF(x, y, w, 20), Qt.AlignVCenter | Qt.AlignLeft, line)
            y += 20
        # stall count, pinned to the bottom
        by = self.height() - self.PAD_Y - 24
        p.fillRect(QRectF(x, by - 8, w, 2), ink if self._hot() else T.rule2)
        p.setFont(body)
        p.setPen(ink)
        p.drawText(QRectF(x, by, w, 24), Qt.AlignVCenter | Qt.AlignLeft, "Stalls this drive")
        p.setFont(mono)
        p.drawText(QRectF(x, by, w, 24), Qt.AlignVCenter | Qt.AlignRight, str(st.count))


class MarkPad(QWidget):
    """The MARK button: about 340 x 370, one press, no aiming."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line = ""
        self._down = False
        self.setCursor(Qt.PointingHandCursor)

    def sync(self, recording) -> None:
        age = recording.last_mark_age_s()
        n = recording.marks
        if not recording.active:
            line = "not recording · Ctrl+R"
        elif age is None:
            line = f"{n} marks"
        else:
            ago = f"{int(age)} s ago" if age < 60 else f"{int(age // 60)} min ago"
            line = f"{n} mark{'s' if n != 1 else ''} · last {ago}"
        if line != self.line:
            self.line = line
            self.update()

    def mousePressEvent(self, _event) -> None:
        self._down = True
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        self._down = False
        self.update()
        self.clicked.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        fill, ink = (T.fg, T.bg) if self._down else (T.accent, T.on_accent)
        p.fillRect(self.rect(), fill)
        font = T.font(104, 800, spacing=-0.03)
        if paint.text_w(font, "MARK") > w - 16:
            font = T.font(80, 800, spacing=-0.03)
        cy = h / 2.0 - 34
        p.setFont(font)
        p.setPen(ink)
        p.drawText(QRectF(0, cy - 70, w, 140), Qt.AlignCenter, "MARK")
        sub = T.font(18, 700)
        text = "it just stumbled"
        kw = paint.keycap_w("Space") + 10
        total = kw + 12 + paint.text_w(sub, text)
        x = (w - total) / 2.0
        y = cy + 82
        p.setPen(T.cpen(ink, 2))
        p.drawRect(QRectF(x + 1, y - 13, kw - 2, 26))
        p.setFont(T.font(16, 700, mono=True))
        p.drawText(QRectF(x, y - 14, kw, 28), Qt.AlignCenter, "Space")
        p.setFont(sub)
        p.drawText(QRectF(x + kw + 12, y - 14, w, 28), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.setFont(T.font(16, 700, mono=True))
        p.drawText(QRectF(0, y + 22, w, 26), Qt.AlignCenter, self.line)
        p.end()


class DriveScreen(QWidget):
    markRequested = Signal()
    menuRequested = Signal(int, QPoint)     # slot index

    def __init__(self, session, settings=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.settings = settings
        saved = settings.value("drive/slots", "") if settings is not None else ""
        self.slots = [s for s in str(saved).split(",") if s] or list(DRIVE_DEFAULT)
        self.four_up = bool(settings is not None and settings.value("drive/four", "0") == "1")
        self._pin_next = 0
        self.tiles = [Tile(None, DRIVE, self) for _ in range(6)]
        for i, tile in enumerate(self.tiles):
            tile.menuRequested.connect(lambda _cid, pos, i=i: self.menuRequested.emit(i, pos))
        self.card = StallCard(self)
        self.mark = MarkPad(self)
        self.mark.clicked.connect(self.markRequested)
        self._version = -1

    def _bind(self) -> None:
        self._version = self.session.bus.version
        bus = self.session.bus
        for tile, cid in zip(self.tiles, self.slots + [""] * 6):
            tile.bind(bus.channel(cid))
        self._layout()

    def pin(self, channel_id: str, slot: int | None = None) -> int:
        """Put a channel on the Drive view. Returns the slot used."""
        n = 4 if self.four_up else 6
        while len(self.slots) < 6:
            self.slots.append("")
        if channel_id in self.slots[:n] and slot is None:
            return self.slots.index(channel_id)
        if slot is None:
            slot, self._pin_next = self._pin_next % n, self._pin_next + 1
        self.slots[slot] = channel_id
        self._save()
        self._bind()
        return slot

    def set_four_up(self, on: bool) -> None:
        self.four_up = on
        self._save()
        self._layout()

    def _save(self) -> None:
        if self.settings is not None:
            self.settings.setValue("drive/slots", ",".join(self.slots))
            self.settings.setValue("drive/four", "1" if self.four_up else "0")

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        side = 340
        left_w = w - 8 - side - 8 - 8
        cols = 2 if self.four_up else 3
        tw = (left_w - (cols - 1) * 8) / cols
        th = (h - 16 - 8) // 2
        for i, tile in enumerate(self.tiles):
            if i >= cols * 2:
                tile.hide()
                continue
            r, c = divmod(i, cols)
            x0, x1 = round(8 + c * (tw + 8)), round(8 + c * (tw + 8) + tw)
            tile.setGeometry(x0, 8 + r * (th + 8), x1 - x0, th)
            tile.show()
        sx = w - 8 - side
        card_h = 268
        self.card.setGeometry(sx, 8, side, card_h)
        self.mark.setGeometry(sx, 8 + card_h + 8, side, h - 16 - card_h - 8)

    def resizeEvent(self, _event) -> None:
        self._layout()

    def retheme(self) -> None:
        for tile in self.tiles:
            tile._value_font = None

    def refresh(self, now: float) -> None:
        if self.session.bus.version != self._version:
            self._bind()
        blink = (now % 1.0) < 0.5
        for tile in self.tiles:
            if tile.isVisible():
                tile.sync(now, self.session.alarms, blink)
        self.card.sync(self.session.stall)
        self.mark.sync(self.session.recording)

    def key(self, _event) -> bool:
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        p.end()
