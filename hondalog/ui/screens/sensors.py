"""F1 Live: the sensors grouped by system (design: Live.dc.html).

Six panels in a 3 x 2 grid: ENGINE, IDLE CONTROL (with the rpm against the
ECU's idle target), FUEL, IGNITION, ELECTRICAL and CYD · BLACK BOX. The on /
off channels sit in a SWITCHES strip along the bottom; the raw bytes and
anything the group table does not know are in a drawer behind
"Raw bytes (N) ▸". A channel the address map does not have is simply absent.
The separate USB black box ("BB") gets a panel only when the app was started
with ``--bb`` (``show_bb``).
"""

from __future__ import annotations

import math
import re

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint
from ..bus import ChannelSpec, ChannelState
from ..theme import T
from ..tiles import (PANEL, PANEL_COMPACT, PANEL_TIGHT, RAW, RAW_TIGHT, FlagChip, IdleBar, Tile,
                     metrics_height)
from ..units import U

PAD, GAP, TILE_GAP = 8, 8, 6
HEAD_H = 28                     # panel title row
STRIP_H = 40                    # the switches strip under the panels
DRAWER_HEAD = 34
SPARK_FRAMES = 30               # frames in which every tile redraws its sparkline once (1 s)

# ---- the group table --------------------------------------------------------
# name -> group, in the order shown. A channel the map does not have is not
# shown; a channel this table does not name goes to the Raw drawer.

PANELS = (
    ("engine", "ENGINE", ("rpm", "map", "tps", "load_ecu", "load", "ect", "iat")),
    ("idle", "IDLE CONTROL", ("idle_target", "iac_cmd", "iacv", "iac_learn", "ign_limit", "vss")),
    ("fuel", "FUEL", ("stft", "ltft", "o2", "o2_2", "o2_post", "inj", "purge_duty", "fuel_status")),
    ("ignition", "IGNITION", ("ign", "vtec", "vtec_psw", "knock")),
    ("electrical", "ELECTRICAL", ("batt", "alt_fr", "eld")),
    ("cyd", "CYD · BLACK BOX", ("gps_speed", "@gear", "@trip", "@cyd_sd", "@cyd_link",
                                "@cyd_stalls")),
)
SWITCHES = ("starter", "brake", "psp", "ac_sw", "ac_clutch", "fan", "fuel_pump", "closed_loop",
            "o2_heater", "mil")
#: read into another tile's note, never a tile of their own
CONSUMED = ("gps_lat", "gps_lon", "gps_heading", "gps_sats", "gps_fix")
#: second choices: in the panel only when the first is not in the map (else in the drawer)
FALLBACK = {"load": "load_ecu", "o2_post": "o2_2"}
GROUP = {cid: key for key, _, ids in PANELS for cid in ids}
GROUP.update({cid: "switches" for cid in SWITCHES})
GROUP.update({cid: "consumed" for cid in CONSUMED})

#: Live's own names where the map's label says something the byte is not, and
#: the Live artboard's names (the web app's web/src/ui/live/layout.ts uses the same)
LABELS = {"ign_limit": "RPM×MAP idx", "knock": "RPM idx 0x3C", "psp": "Steering load",
          "ign": "Timing", "o2": "O2 front", "alt_fr": "Alt field"}

#: worked-out tiles of the CYD panel: (label, units, decimals)
VIRTUAL = {
    "@gear": ("Gear", "", 0),
    "@trip": ("Trip", "km", 1),
    "@cyd_sd": ("CYD SD log", "", 0),
    "@cyd_link": ("CYD link", "", 0),
    "@cyd_stalls": ("CYD stalls", "", 0),
}

SUBTITLE = {"idle": "what the ECU asks for vs gets", "ignition": "timing and VTEC",
            "cyd": "GPS, its own SD log and stall catcher", "bb": "the separate ESP32 box (--bb)"}

# rpm per km/h in each gear (P2P Civic, S40 box, 4.25 final drive, 185/65 R14):
# the gear is the one rpm ÷ speed lands within 12 % of. An estimate, not a sensor.
def gear_of(rpm: float, kmh: float) -> str:
    """"N" stopped, "1".."5" when rpm ÷ speed matches a gear by D3's coupling
    test (``hondalog.analysis.motion``: the same clusters Replay and Power use),
    "–" otherwise (clutch in, coasting, crawling), "---" without rpm or speed."""
    if not (rpm == rpm and kmh == kmh):
        return "---"
    if kmh < 2.0:
        return "N"
    from ...analysis import motion
    g = int(motion.gear_raw([rpm], [kmh])[0])
    return str(g) if g > 0 else "–"


_DATE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*:\s*")


def short_note(text: str, limit: int = 44) -> str:
    """A map note cut to one line: no leading date, the first clause only."""
    text = " ".join(_DATE.sub("", text or "").split())
    for stop in (". ", "; ", " (", ": "):
        cut = text.find(stop)
        if cut > 0:
            text = text[:cut]
    text = text.rstrip(".;: ")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def map_notes(session) -> dict:
    """channel id -> the address map's note, shortened."""
    amap = getattr(session.ecu, "amap", None)
    out = {}
    for group in ("channels", "obd2", "math"):
        for ch in getattr(amap, group, None) or ():
            text = getattr(ch, "notes", "") or getattr(ch, "description", "")
            if text:
                out[ch.name] = short_note(text)
    return out


def signed(value: float) -> str:
    """+18, −3 (a true minus sign)."""
    return ("−" if value <= -0.5 else "+" if value >= 0.5 else "") + f"{abs(value):.0f}"


def _handheld(session):
    """The handheld's link object (its hello and last STATS), or None."""
    ecu = session.ecu
    poller = getattr(ecu, "poller", None)
    if poller is None:
        return None
    if getattr(poller, "source", "") == "cyd" or getattr(ecu, "is_cyd", False):
        return poller
    return getattr(getattr(ecu, "live", None), "handheld", None)


LINK_WORD = {"usb": "USB", "relay": "RELAY", "kline": "K-LINE", "demo": "DEMO",
             "connecting": "…", "lost": "LOST"}


def cyd_facts(session) -> dict:
    """What the CYD (the handheld, the black box) says about itself:
    {"sd": (text, note), "link": (text, note, hot), "stalls": (count, note)}.
    Empty when there is no handheld. Reads ``session.cyd`` (the status bar's
    CydState) and the handheld's last STATS frame for free space and stalls."""
    cyd = getattr(session, "cyd", None)
    link_state = getattr(cyd, "link", "off") if cyd is not None else (
        "demo" if getattr(session, "demo", False) else "off")
    if link_state == "off":
        return {}
    stats, map_hash = {}, ""
    try:
        client = _handheld(session)
        stats = getattr(client, "device", None) or getattr(client, "stats", None) or {}
        hello = getattr(client, "hello", None)
        map_hash = getattr(hello, "map_hash", "") or ""
    except Exception:                       # half-way through connecting: nothing more to say
        stats = {}
    if not isinstance(stats, dict):
        stats = {}
    out = {}
    sd = getattr(cyd, "sd", "") if cyd is not None else "rec"
    free = stats.get("free_mb")
    free_text = f"{free / 1024:.1f} GB free" if free else ""
    if sd == "rec":
        drive = getattr(cyd, "drive_no", 0) if cyd is not None else 0
        note = " · ".join(x for x in (f"drive {drive}" if drive else "", free_text) if x)
        out["sd"] = ("REC", note or ("simulated" if link_state == "demo" else ""))
    elif sd == "ok":
        out["sd"] = ("SD OK", free_text or "not recording")
    elif sd == "none":
        out["sd"] = ("NO SD", "no card")
    map_ok = getattr(cyd, "map_ok", None) if cyd is not None else True
    tag = f"map #{map_hash}" if map_hash else "map"
    if map_ok is True:
        note, hot = f"{tag} ✓", False
    elif map_ok is False:
        note, hot = f"{tag} differs: push it from Setup", True
    else:
        note, hot = getattr(cyd, "detail", "") if cyd is not None else "", False
    out["link"] = (LINK_WORD.get(link_state, link_state.upper()), note, hot)
    if "stall_count" in stats:
        out["stalls"] = (int(stats["stall_count"]), "")
    elif link_state == "demo":
        st = getattr(session, "stall", None)
        out["stalls"] = (int(getattr(st, "count", 0)), getattr(st, "state", ""))
    return out


class _Panel:
    __slots__ = ("key", "title", "sub", "tiles", "bar", "rect", "cols")

    def __init__(self, key, title):
        self.key, self.title, self.sub = key, title, ""
        self.tiles: list[Tile] = []
        self.bar: IdleBar | None = None
        self.rect = QRectF()
        self.cols = 3


class RawButton(QWidget):
    """"Raw bytes (N) ▸": opens and closes the drawer."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.open = False
        self.setCursor(Qt.PointingHandCursor)

    def set_count(self, n: int, open_: bool) -> None:
        text = f"Raw bytes ({n}) {'▾' if open_ else '▸'}"
        if text != self.text or open_ != self.open:
            self.text, self.open = text, open_
            self.update()

    def preferred_width(self) -> int:
        return int(paint.text_w(T.font(13, 700), self.text or "Raw bytes (00) ▸")) + 28

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), T.accent if self.open else T.bg)
        p.setPen(T.cpen(T.rule, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
        p.setFont(T.font(13, 700))
        p.setPen(T.on_accent if self.open else T.fg)
        p.drawText(self.rect(), Qt.AlignCenter, self.text)
        p.end()


class RawDrawer(QWidget):
    """The raw bytes, and every channel the group table does not place."""

    TILE_W = 124

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles: list[Tile] = []
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def needed_height(self, width: int) -> int:
        cols = max(1, (width - 2 * PAD) // self.TILE_W)
        rows = math.ceil(len(self.tiles) / cols) if self.tiles else 1
        return DRAWER_HEAD + rows * (metrics_height(RAW) + TILE_GAP) + PAD

    def layout_tiles(self) -> None:
        w, h = self.width(), self.height()
        n = len(self.tiles)
        if not n:
            return
        cols = max(1, (w - 2 * PAD) // self.TILE_W)
        rows = math.ceil(n / cols)
        avail = h - DRAWER_HEAD - PAD
        th = min(metrics_height(RAW), (avail - (rows - 1) * TILE_GAP) // max(1, rows))
        metrics = RAW if th >= metrics_height(RAW) else RAW_TIGHT
        th = max(th, metrics_height(RAW_TIGHT))
        tw = (w - 2 * PAD - (cols - 1) * TILE_GAP) / cols
        for i, tile in enumerate(self.tiles):
            r, c = divmod(i, cols)
            x0 = round(PAD + c * (tw + TILE_GAP))
            x1 = round(PAD + c * (tw + TILE_GAP) + tw)
            y = DRAWER_HEAD + r * (th + TILE_GAP)
            if tile.m is not metrics:
                tile.m = metrics
            tile.setGeometry(x0, y, x1 - x0, th)
            tile.setVisible(y + th <= h - 2)

    def resizeEvent(self, _event) -> None:
        self.layout_tiles()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), T.panel)
        p.setPen(T.cpen(T.rule, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
        cap = T.font(13, 800, spacing=0.06)
        p.setFont(cap)
        p.setPen(T.fg)
        title = "RAW BYTES"
        p.drawText(QRectF(PAD + 2, 4, 300, DRAWER_HEAD - 8), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.setFont(T.font(12, 600))
        p.setPen(T.fg2)
        hidden = sum(1 for t in self.tiles if t.isHidden())
        more = f" · {hidden} more: widen the window" if hidden else ""
        p.drawText(QRectF(PAD + 14 + paint.text_w(cap, title), 4, w, DRAWER_HEAD - 8),
                   Qt.AlignVCenter | Qt.AlignLeft,
                   f"{len(self.tiles)} channels the panels do not show · click pins to Drive · "
                   f"Esc closes{more}")
        p.end()


class SensorsScreen(QWidget):
    """F1 Live. (The class keeps its old name; ``LiveScreen`` is the same.)"""

    pinRequested = Signal(str)
    menuRequested = Signal(str, QPoint)

    def __init__(self, session, parent=None, show_bb: bool | None = None):
        super().__init__(parent)
        self.session = session
        self.show_bb = show_bb
        self.tiles: list[Tile] = []         # every channel tile: the panels', then the drawer's
        self.panels: list[_Panel] = []
        self.flags: list[FlagChip] = []
        self.virtual: dict[str, Tile] = {}  # "@gear" ... -> its tile
        self._vstates: dict[str, ChannelState] = {}
        self.raw_button = RawButton(self)
        self.raw_button.clicked.connect(self.toggle_raw)
        self.drawer = RawDrawer(self)
        self.drawer.hide()
        self.raw_open = False
        self._notes: dict = {}
        self._version = -1
        self._bb = None
        self._info_t = -1.0
        self._frame = 0
        self._trip_km = 0.0
        self._trip_t: float | None = None
        for cid, (label, units, decimals) in VIRTUAL.items():
            state = ChannelState(ChannelSpec(cid, label, units, decimals, 0, 1000, source="CYD",
                                             verified=True), hist_n=2)
            state.timeout = 3.0
            self._vstates[cid] = state

    def _bb_shown(self) -> bool:
        if self.show_bb is not None:
            return bool(self.show_bb)
        return bool(getattr(self.session, "show_bb", False))

    # ---- build / layout ----

    def _channel_tile(self, state, parent) -> Tile:
        tile = Tile(None, PANEL, parent)
        tile.label_override = LABELS.get(state.spec.id, "")
        tile.max_decimals = 2
        tile.bind(state)
        tile.clicked.connect(self.pinRequested)
        tile.menuRequested.connect(self.menuRequested)
        tile.show()
        return tile

    def _rebuild(self) -> None:
        bus = self.session.bus
        self._version = bus.version
        self._bb = self._bb_shown()
        for w in [*self.tiles, *self.virtual.values(), *self.flags,
                  *(p.bar for p in self.panels if p.bar is not None)]:
            w.setParent(None)
            w.deleteLater()
        self.tiles, self.panels, self.flags, self.virtual = [], [], [], {}
        self._notes = map_notes(self.session)
        have = {s.spec.id: s for s in bus.channels() if s.spec.mapped and s.spec.source != "BB"}
        placed = set()
        for key, title, ids in PANELS:
            panel = _Panel(key, title)
            for cid in ids:
                if cid.startswith("@"):
                    tile = Tile(None, PANEL, self)
                    tile.foot_override = ""
                    tile.bind(self._vstates[cid])
                    tile.show()
                    self.virtual[cid] = tile
                    panel.tiles.append(tile)
                    continue
                state = have.get(cid)
                if state is None or FALLBACK.get(cid) in have:
                    continue
                panel.tiles.append(self._channel_tile(state, self))
                placed.add(cid)
            if key == "idle":
                panel.bar = IdleBar("idle", parent=self)
                panel.bar.show()
            if any(not t.state.spec.id.startswith("@") for t in panel.tiles) or key == "cyd":
                self.panels.append(panel)
                self.tiles += [t for t in panel.tiles if not t.state.spec.id.startswith("@")]
            elif panel.bar is not None:
                panel.bar.setParent(None)
                panel.bar.deleteLater()
        # the separate USB black box: a panel of its own with --bb, otherwise not shown at all
        bb = [s for s in self.session.ordered("BB") if s.spec.mapped]
        if bb and self._bb:
            panel = _Panel("bb", "BLACK BOX · USB")
            panel.tiles = [self._channel_tile(s, self) for s in bb]
            self.tiles += panel.tiles
            self.panels.append(panel)
        for cid in SWITCHES:
            state = have.get(cid)
            if state is None or not state.spec.is_flag:
                continue
            chip = FlagChip(state, LABELS.get(cid, ""), self)
            chip.clicked.connect(self.pinRequested)
            chip.menuRequested.connect(self.menuRequested)
            chip.show()
            self.flags.append(chip)
            placed.add(cid)
        placed.update(CONSUMED)
        rest = [s for s in have.values() if s.spec.id not in placed]
        rest.sort(key=lambda s: (not s.spec.id.startswith("raw_"), s.spec.source != "ECU",
                                 s.spec.id))
        self.drawer.tiles = [self._channel_tile(s, self.drawer) for s in rest]
        self.tiles += self.drawer.tiles
        self.raw_button.set_count(len(rest), self.raw_open)
        self._info_t = -1.0
        self._layout()

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        # the switches strip
        sy = h - STRIP_H
        x = PAD + 92                        # after the SWITCHES caption
        bw = self.raw_button.preferred_width()
        right = w - PAD - bw - 12
        chip_y = sy + (STRIP_H - PAD - FlagChip.HEIGHT) // 2
        for chip in self.flags:
            cw = chip.preferred_width()
            if x + cw > right:
                chip.hide()
                continue
            chip.setGeometry(x, chip_y, cw, FlagChip.HEIGHT)
            chip.show()
            x += cw + 6
        self.raw_button.setGeometry(w - PAD - bw, sy + (STRIP_H - PAD - 32) // 2, bw, 32)
        self.raw_button.setVisible(bool(self.drawer.tiles))
        # the panels: 3 x 2 (4 x 2 with the USB black box)
        n = len(self.panels)
        if n:
            cols = 3 if n <= 6 else 4
            rows = math.ceil(n / cols)
            pw = (w - 2 * PAD - (cols - 1) * GAP) / cols
            ph = (sy - PAD - (rows - 1) * GAP) / rows
            th = 10 ** 6
            for i, panel in enumerate(self.panels):
                r, c = divmod(i, cols)
                x0, y0 = round(PAD + c * (pw + GAP)), round(PAD + r * (ph + GAP))
                x1, y1 = round(PAD + c * (pw + GAP) + pw), round(PAD + r * (ph + GAP) + ph)
                panel.rect = QRectF(x0, y0, x1 - x0, y1 - y0)
                inner_w = panel.rect.width() - 20
                count = max(1, len(panel.tiles))
                avail = panel.rect.height() - HEAD_H - 12
                if panel.bar is not None:
                    avail -= IdleBar.preferred_height() + 6
                # three across; four when that saves a row the panel has no room for
                panel.cols = 3
                if (math.ceil(count / 3) > math.ceil(count / 4) and inner_w / 4 >= 100
                        and math.ceil(count / 3) * (metrics_height(PANEL_COMPACT) + TILE_GAP) > avail):
                    panel.cols = 4
                prow = math.ceil(count / panel.cols)
                th = min(th, (avail - (prow - 1) * TILE_GAP) // prow)
            th = int(min(th, metrics_height(PANEL)))
            metrics = (PANEL if th >= metrics_height(PANEL) else
                       PANEL_COMPACT if th >= metrics_height(PANEL_COMPACT) else PANEL_TIGHT)
            th = max(th, 40)
            for panel in self.panels:
                x0, y0 = panel.rect.x() + 10, panel.rect.y() + HEAD_H + 6
                inner_w = panel.rect.width() - 20
                if panel.bar is not None:
                    bh = IdleBar.preferred_height()
                    panel.bar.setGeometry(int(x0), int(y0), int(inner_w), bh)
                    y0 += bh + 6
                tw = (inner_w - (panel.cols - 1) * TILE_GAP) / panel.cols
                for i, tile in enumerate(panel.tiles):
                    r, c = divmod(i, panel.cols)
                    tx0 = round(x0 + c * (tw + TILE_GAP))
                    tx1 = round(x0 + c * (tw + TILE_GAP) + tw)
                    if tile.m is not metrics:
                        tile.m = metrics
                    tile.setGeometry(tx0, int(y0 + r * (th + TILE_GAP)), tx1 - tx0, th)
        # the drawer, over the lower panels
        if self.raw_open:
            dh = min(self.drawer.needed_height(w - 2 * PAD), int((sy - PAD) * 0.66))
            self.drawer.setGeometry(PAD, sy - dh, w - 2 * PAD, dh)
            self.drawer.layout_tiles()
            self.drawer.raise_()
        self.update()

    def toggle_raw(self) -> None:
        self.raw_open = not self.raw_open
        self.raw_button.set_count(len(self.drawer.tiles), self.raw_open)
        self._layout()
        self.drawer.setVisible(self.raw_open)

    def resizeEvent(self, _event) -> None:
        self._layout()

    def retheme(self) -> None:
        for tile in self.tiles + list(self.virtual.values()):
            tile._value_font = None
            tile._note_w = -1

    # ---- frame ----

    def _value(self, cid: str, now: float) -> float:
        state = self.session.bus.channel(cid)
        if state is None or state.stale(now):
            return float("nan")
        return state.value

    def _put(self, cid: str, now: float, value: float, text: str | None = None) -> None:
        """A worked-out tile's value ("N", "REC" ... as text_override)."""
        if cid not in self.virtual:
            return
        state = self._vstates[cid]
        self.virtual[cid].text_override = text
        if value != value:
            state.t = -math.inf             # stale: hatched, "---"
            state.gen += 1
        else:
            state._put(now, float(value))

    def _update_derived(self, now: float) -> None:
        """Notes, the idle bar, the CYD panel's tiles and the panel subtitles."""
        v, bus = self._value, self.session.bus
        rpm, kmh = v("rpm", now), v("vss", now)
        if kmh != kmh:
            kmh = v("gps_speed", now)
        gear = gear_of(rpm, kmh)
        self._put("@gear", now, float("nan") if gear == "---" else float("N12345–".find(gear)),
                  gear)
        self._put("@trip", now, self._trip_km)
        facts = cyd_facts(self.session)
        sd, link, stalls = facts.get("sd"), facts.get("link"), facts.get("stalls")
        self._put("@cyd_sd", now, 1.0 if sd else float("nan"), sd[0] if sd else None)
        self._put("@cyd_link", now, 1.0 if link else float("nan"), link[0] if link else None)
        self._put("@cyd_stalls", now, float(stalls[0]) if stalls else float("nan"))
        nobody = "no handheld" if not link else ""
        notes = {"@gear": ("from rpm ÷ speed", False), "@trip": ("since the app started", False),
                 "@cyd_sd": (sd[1] if sd else nobody, False),
                 "@cyd_link": ((link[1], link[2]) if link else (nobody, False)),
                 "@cyd_stalls": (stalls[1] if stalls else nobody, False)}
        for cid, (text, hot) in notes.items():
            tile = self.virtual.get(cid)
            if tile is not None:
                tile.set_note(text, hot)
        # worked-out notes win over the map's note
        live = {}
        baro = bus.channel("baro")
        if baro is not None and not baro.stale(now):
            live["map"] = (f"baro {U.with_unit(baro.spec, baro.value)}", False)
        total = bus.channel("total_trim")
        if total is not None and not total.stale(now):
            live["ltft"] = (f"total {signed(total.value)} %", abs(total.value) >= 15)
        if gear != "---":
            live["vss"] = (f"gear {gear}", False)
        sats, fix = v("gps_sats", now), v("gps_fix", now)
        if sats == sats or fix == fix:
            parts = (f"{sats:.0f} sats" if sats == sats else "",
                     ("fix 3D" if fix >= 0.5 else "no fix") if fix == fix else "")
            live["gps_speed"] = (" · ".join(x for x in parts if x), fix == fix and fix < 0.5)
        for tile in self.tiles:
            cid = tile.state.spec.id
            text, hot = live.get(cid, (self._notes.get(cid, ""), False))
            tile.set_note(text, hot)
        # panel subtitles
        subs = dict(SUBTITLE)
        if rpm == rpm:
            ect = v("ect", now)
            warm = "" if ect != ect else ("warm " if ect >= 70 else "cold ")
            subs["engine"] = ("engine off" if rpm < 200 else f"{warm}idle, stopped"
                              if kmh == kmh and kmh < 2 else f"{warm}running")
        loop = v("closed_loop", now)
        if loop == loop:
            subs["fuel"] = "closed loop" if loop >= 0.5 else "open loop"
        batt = v("batt", now)
        if batt == batt:
            subs["electrical"] = ("overcharging" if batt > 14.9 else "charging normal"
                                  if batt >= 13.2 else "not charging" if batt >= 12.0
                                  else "battery low")
        changed = False
        for panel in self.panels:
            sub = subs.get(panel.key, "")
            if sub != panel.sub:
                panel.sub, changed = sub, True
        if changed:
            self.update()

    def refresh(self, now: float) -> None:
        if self.session.bus.version != self._version or self._bb_shown() != self._bb:
            self._rebuild()
        if self._trip_t is not None:        # the trip: speed integrated frame by frame
            kmh = self._value("vss", now)
            if kmh != kmh:
                kmh = self._value("gps_speed", now)
            if kmh == kmh and kmh > 0:
                self._trip_km += kmh * min(1.0, max(0.0, now - self._trip_t)) / 3600.0
        self._trip_t = now
        if now - self._info_t >= 1.0 or self._info_t < 0:
            self._info_t = now
            self._update_derived(now)
        self._sync_bar(now)
        alarms = self.session.alarms
        blink = (now % 1.0) < 0.5
        # Values update at once. A sparkline covers 60 s: each tile strokes its
        # own once a second (the bus samples it at 2 Hz), the tiles taking turns
        # over SPARK_FRAMES frames, so one or two polylines land in a frame.
        self._frame = phase = (self._frame + 1) % SPARK_FRAMES
        drawer = self.raw_open
        for i, tile in enumerate(self.tiles):
            if drawer or tile.parent() is self:
                tile.sync(now, alarms, blink, i % SPARK_FRAMES == phase)
        for tile in self.virtual.values():
            tile.sync(now, None, blink, False)
        for chip in self.flags:
            chip.sync(now, alarms, blink)

    def _sync_bar(self, now: float) -> None:
        for panel in self.panels:
            if panel.bar is not None:
                panel.bar.set_values(self._value("rpm", now), self._value("idle_target", now),
                                     self.session.bus.channel("idle_target") is not None)

    def key(self, event) -> bool:
        if event.key() == Qt.Key_Escape and self.raw_open:
            self.toggle_raw()
            return True
        return False

    # ---- paint ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        title_f = T.font(13, 800, spacing=0.06)
        sub_f = T.font(12, 600)
        for panel in self.panels:
            r = panel.rect
            p.setPen(T.cpen(T.rule, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.fillRect(QRectF(r.x() + 2, r.y() + HEAD_H, r.width() - 4, 1), T.rule)
            p.setFont(title_f)
            p.setPen(T.fg)
            p.drawText(QRectF(r.x() + 10, r.y() + 2, r.width() - 20, HEAD_H - 2),
                       Qt.AlignVCenter | Qt.AlignLeft, panel.title)
            tw = paint.text_w(title_f, panel.title)
            if panel.sub:
                p.setFont(sub_f)
                p.setPen(T.fg2)
                room = r.width() - 30 - tw
                p.drawText(QRectF(r.x() + 20 + tw, r.y() + 2, room, HEAD_H - 2),
                           Qt.AlignVCenter | Qt.AlignLeft, paint.elide(sub_f, panel.sub, room))
        if self.flags:
            sy = self.height() - STRIP_H
            p.setFont(T.font(12, 800, spacing=0.06))
            p.setPen(T.fg)
            p.drawText(QRectF(PAD, sy, 90, STRIP_H - PAD), Qt.AlignVCenter | Qt.AlignLeft,
                       "SWITCHES")
        p.end()


LiveScreen = SensorsScreen
