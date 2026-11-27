"""F8, Map: where the drive went, coloured by a channel, and where the stalls were.

Two views:

- **Drive**: the track of whatever Replay has open (a drive or a capture),
  coloured by speed, RPM, MAP, AFR or any other channel of the drive, with
  pins for markers, alarms and captures. Clicking the track moves Replay's
  cursor there; Replay's cursor is the magenta dot. While Replay shows the
  live lanes, the map shows the car now and the last ten minutes behind it
  (the handheld's GPS over ``cyd:``).
- **All stalls**: every stall capture of the database where it happened,
  filtered by date and trigger reason, with the biggest group of them in one
  place named in the Reading card. Clicking a pin opens that capture in Replay.

Layout as Map 3D (HANDOFF 5.3): the map on the left, a 374 px side panel.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QLinearGradient, QPainter
from PySide6.QtWidgets import QFileDialog, QMenu, QWidget

from ... import ramps
from ...geo import proj
from ...geo import stalls as gs
from ...geo import tiles as gt
from ...geo.track import Track, view_waypoints
from .. import paint
from ..controls import Button, Card, Segment, TextCard
from ..mapview import MapCanvas, Pin, draw_dot, draw_pin
from ..theme import T
from ..tilelayer import TileLayer
from ..units import U

SIDE = 374
#: (preset id, label, the channel ids that can stand for it, first that has data wins)
PRESETS = (("speed", "Speed", ("vss", "gps_speed")), ("rpm", "RPM", ("rpm",)), ("map", "MAP", ("map",)),
           ("afr", "AFR", ("afr_est",)))
SINCE = (("all", "All"), ("365", "1 yr"), ("90", "90 d"), ("30", "30 d"), ("7", "7 d"))
TRAIL_S = 600.0
RELOAD_STALLS_S = 10.0
SKIP_CHANNELS = {"gps_lat", "gps_lon", "gps_utc", "gps_fix", "gps_hdop", "gps_sats", "gps_heading", "bbmark",
                 "link"}


def _mark_label(text: str) -> str:
    m = re.match(r"MARK\s+(\d+)", text or "")
    return m.group(1) if m else "M"


def _signed(t: float) -> str:
    sign = "−" if t < -0.05 else ("+" if t > 0.05 else "")
    return f"{sign}{abs(t):.1f}"


def _speed_spec():
    from ..bus import ChannelSpec
    return ChannelSpec("gps_speed", "Speed", "km/h", 0, 0, 200)


def speed_text(kmh: float | None) -> str:
    if kmh is None or kmh != kmh:
        return "---"
    return U.with_unit(_SPEED, kmh)


def distance_text(metres: float) -> str:
    if U.unit("km") == "mi":
        miles = metres / 1609.344
        return f"{miles:.1f} mi" if miles >= 0.1 else f"{metres / 0.3048:.0f} ft"
    return f"{metres / 1000:.1f} km" if metres >= 100 else f"{metres:.0f} m"


_SPEED = _speed_spec()


# ---- side panel cards -------------------------------------------------------

class LegendCard(Card):
    """The colour ramp of the track with its range, then the pin shapes."""

    KEY = (("stall", "stall"), ("capture", "capture"), ("alarm", "alarm"), ("marker", "mark"))

    def __init__(self, parent=None):
        super().__init__("Colour", parent=parent)
        self.stops = ramps.SEQ_RAMP
        self.labels = ("", "", "")

    def set(self, title: str, stops, labels) -> None:
        self.title, self.stops, self.labels = title, stops, labels
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        bar = QRectF(x, y + 2, w, 22)
        if self.stops:
            grad = QLinearGradient(bar.topLeft(), bar.topRight())
            for i, stop in enumerate(self.stops):
                grad.setColorAt(i / (len(self.stops) - 1), QColor(stop))
            p.fillRect(bar, grad)
        else:
            p.fillRect(bar, T.nodata)
        paint.frame(p, bar, 2, T.rule2)
        p.setFont(T.font(13, 700, mono=True))
        p.setPen(T.fg)
        row = QRectF(x, y + 28, w, 18)
        for text, flag in zip(self.labels, (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)):
            p.drawText(row, Qt.AlignVCenter | flag, text)
        ky = y + 56
        p.fillRect(QRectF(x, ky - 6, 22, 12), T.nodata)
        paint.frame(p, QRectF(x, ky - 6, 22, 12), 2, T.rule2)
        p.setFont(T.font(13, 600))
        p.setPen(T.fg)
        p.drawText(QRectF(x + 30, ky - 10, w - 30, 20), Qt.AlignVCenter | Qt.AlignLeft, "grey = no value there")
        ky += 26
        cw = w / 4
        for i, (kind, word) in enumerate(self.KEY):
            cx = x + i * cw
            draw_pin(p, kind, cx + 11, ky, "1" if kind == "marker" else "", size=18)
            p.setFont(T.font(13, 700))
            p.setPen(T.fg)
            p.drawText(QRectF(cx + 26, ky - 10, cw - 26, 20), Qt.AlignVCenter | Qt.AlignLeft, word)


class ReadoutCard(Card):
    """Pairs of (label, value) in two columns, then one mono line."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent=parent)
        self.cells: list[tuple[str, str]] = []
        self.line = ""
        self.dot = False                    # draw the cursor dot before the title

    def set(self, cells, line: str = "") -> None:
        if (cells, line) != (self.cells, self.line):
            self.cells, self.line = list(cells), line
            self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        lab, val = T.font(12, 700), T.font(22, 800)
        for n, (label, value) in enumerate(self.cells[:4]):
            r, c = divmod(n, 2)
            cx, cy = x + c * (w / 2 + 4), y + r * 42
            p.setFont(lab)
            p.setPen(T.fg2)
            p.drawText(QRectF(cx, cy, w / 2 - 4, 14), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(lab, label.upper(), w / 2 - 6))
            p.setFont(val)
            p.setPen(T.fg)
            p.drawText(QRectF(cx, cy + 13, w / 2 - 4, 28), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(val, value, w / 2 - 6))
        if self.line:
            mono = T.font(13, 700, mono=True)
            p.setFont(mono)
            p.setPen(T.fg)
            p.drawText(QRectF(x, y + h - 18, w, 18), Qt.AlignVCenter | Qt.AlignLeft, paint.elide(mono, self.line, w))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.dot:
            p = QPainter(self)
            draw_dot(p, self.width() - 20, 17, 6.0)
            p.end()


class StallList(Card):
    """The captures of the All stalls view, newest first. Click selects
    (and centres the map on it), a double click opens it in Replay."""

    picked = Signal(int)
    opened = Signal(int)
    ROW = 34

    def __init__(self, parent=None):
        super().__init__("Captures · fastest in the 30 s before", parent=parent)
        self.rows: list[tuple[str, str, str, str]] = []     # kind, when, reason, speed
        self.selected = -1
        self.first = 0

    def set_rows(self, rows, selected: int = -1) -> None:
        self.rows, self.selected, self.first = list(rows), selected, 0
        self.show_selected()
        self.update()

    def visible(self) -> int:
        return max(1, int(self.body_rect().height() // self.ROW))

    def show_selected(self) -> None:
        n = self.visible()
        if self.selected >= 0:
            if self.selected < self.first:
                self.first = self.selected
            elif self.selected >= self.first + n:
                self.first = self.selected - n + 1
        self.first = max(0, min(self.first, max(0, len(self.rows) - n)))

    def select(self, i: int) -> None:
        if 0 <= i < len(self.rows) and i != self.selected:
            self.selected = i
            self.show_selected()
            self.update()

    def _row_at(self, y: float) -> int:
        b = self.body_rect()
        i = int((y - b.y()) // self.ROW)
        return self.first + i if 0 <= i < self.visible() and self.first + i < len(self.rows) else -1

    def mousePressEvent(self, event) -> None:
        i = self._row_at(event.position().y())
        if i >= 0:
            self.select(i)
            self.picked.emit(i)

    def mouseDoubleClickEvent(self, event) -> None:
        i = self._row_at(event.position().y())
        if i >= 0:
            self.opened.emit(i)

    def wheelEvent(self, event) -> None:
        step = -1 if event.angleDelta().y() > 0 else 1
        self.first = max(0, min(self.first + step, max(0, len(self.rows) - self.visible())))
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        if not self.rows:
            p.setFont(T.font(14, 600))
            p.setPen(T.fg2)
            p.drawText(QRectF(x, y, w, 30), Qt.AlignVCenter | Qt.AlignLeft, "None yet.")
            return
        mono = T.font(14, 700, mono=True)
        for k in range(self.visible()):
            i = self.first + k
            if i >= len(self.rows):
                break
            kind, when, reason, speed = self.rows[i]
            r = QRectF(x - 4, y + k * self.ROW, w + 8, self.ROW - 2)
            on = i == self.selected
            if on:
                p.fillRect(r, T.accent)
            elif k % 2:
                p.fillRect(r, T.sunk)
            draw_pin(p, kind, r.x() + 16, r.center().y(), size=18)
            ink = T.on_accent if on else T.fg
            p.setFont(mono)
            p.setPen(ink)
            p.drawText(QRectF(r.x() + 34, r.y(), 150, r.height()), Qt.AlignVCenter | Qt.AlignLeft, when)
            p.setFont(T.font(14, 700))
            p.drawText(QRectF(r.x() + 190, r.y(), 70, r.height()), Qt.AlignVCenter | Qt.AlignLeft, reason)
            p.setFont(mono)
            p.drawText(QRectF(r.right() - 110, r.y(), 102, r.height()), Qt.AlignVCenter | Qt.AlignRight, speed)
        if len(self.rows) > self.visible():
            p.setFont(T.font(12, 700))
            p.setPen(T.fg2)
            p.drawText(QRectF(x, y - 22, w, 16), Qt.AlignVCenter | Qt.AlignRight,
                       f"{self.first + 1}–{min(len(self.rows), self.first + self.visible())} of {len(self.rows)}")


# ---- the screen -------------------------------------------------------------

def tile_settings(settings) -> dict:
    """The Map tiles settings with their defaults."""
    def get(key, default):
        return settings.value(key, default) if settings is not None else default
    return {"url": str(get("map/tile_url", "") or ""), "attribution": get("map/tile_attribution", None),
            "online": str(get("map/online", "true")) == "true",
            "cap_mb": float(get("map/cache_mb", gt.DEFAULT_CAP_MB) or gt.DEFAULT_CAP_MB)}


class MapScreen(QWidget):
    """``openRequested(ref)``: open a capture in Replay (the shell switches to it)."""

    openRequested = Signal(object)

    def __init__(self, session, replay, settings=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.replay = replay
        self.settings = settings
        self.tiles: TileLayer | None = None
        self.canvas = MapCanvas(None, self)
        self.configure_tiles()
        self.mode = "drive"
        self.colour = "speed"               # a preset id, or a channel id picked from the menu
        self.view_seg = Segment([("drive", "Drive"), ("stalls", "All stalls")], "drive", parent=self)
        self.col_seg = Segment([(k, label) for k, label, _ in PRESETS], "speed", parent=self)
        self.chan_btn = Button("Other channel…", "⇧C", parent=self)
        self.legend = LegendCard(self)
        self.at = ReadoutCard("At the cursor", self)
        self.at.dot = True
        self.info = ReadoutCard("Track", self)
        self.fit_btn = Button("Fit", "F", parent=self)
        self.gpx_btn = Button("GPX", parent=self)           # four in a row: the keys are in the tooltips
        self.kml_btn = Button("KML", parent=self)
        self.gpx_btn.setToolTip("The drive as GPX (G)")
        self.kml_btn.setToolTip("The drive as KML, coloured by the channel (K)")
        self.since_seg = Segment(list(SINCE), "all", parent=self)
        self.reason_btn = Button("Stalls, any reason", "R", parent=self)
        self.reading = TextCard("Where they happen", border=4, px=15, weight=700)
        self.reading.setParent(self)
        self.list = StallList(self)
        self.json_btn = Button("GeoJSON", "G", parent=self)
        self.open_btn = Button("Open in Replay", "↵", parent=self)
        # the drive as GeoJSON too (the web app has GPX, KML and GeoJSON)
        self.drive_json_btn = Button("GeoJSON", parent=self)
        self.drive_json_btn.setToolTip("The drive as GeoJSON (J)")
        self.drive_json_btn.clicked.connect(lambda: self.export("geojson"))
        # map tiles, on the screen itself: download on / off, the server, the cache
        self.tiles_btn = Button("Download tiles", height=36, parent=self)
        self.tiles_btn.setCheckable(True)
        self.tiles_btn.px = 13
        self.tiles_btn.setToolTip("Download the map tiles the map shows (off: only what is cached)")
        self.server_btn = Button("Tile server…", height=36, parent=self)
        self.server_btn.px = 13
        self.clear_btn = Button("Clear cache", height=36, parent=self)
        self.clear_btn.px = 13
        self.tiles_btn.clicked.connect(lambda: self.set_tiles_online(self.tiles_btn.isChecked()))
        self.server_btn.clicked.connect(self._ask_server)
        self.clear_btn.clicked.connect(self._clear_cache)
        # zoom buttons over the map (the keys are + and -)
        self.zoom_in = Button("+", height=36, parent=self)
        self.zoom_out = Button("−", height=36, parent=self)
        for b, step, tip in ((self.zoom_in, 1, "Zoom in (+)"), (self.zoom_out, -1, "Zoom out (-)")):
            b.px = 18
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, n=step: self.canvas.zoom_by(n))
        self.view_seg.picked.connect(self.set_mode)
        self.col_seg.picked.connect(self.set_colour)
        self.chan_btn.clicked.connect(self.channel_menu)
        self.fit_btn.clicked.connect(self.fit)
        self.gpx_btn.clicked.connect(lambda: self.export("gpx"))
        self.kml_btn.clicked.connect(lambda: self.export("kml"))
        self.since_seg.picked.connect(lambda _k: self.load_stalls(fit=True))
        self.reason_btn.clicked.connect(self.reason_menu)
        self.json_btn.clicked.connect(lambda: self.export("geojson"))
        self.open_btn.clicked.connect(self.open_selected)
        self.list.picked.connect(self.select_pin)
        self.list.opened.connect(lambda i: self.open_pin(i))
        self.canvas.clicked.connect(self._clicked)
        self.canvas.pinClicked.connect(self._pin_clicked)
        self.canvas.moved.connect(self._user_moved)
        self.reason: tuple[str | None, tuple | None] = (None, ("stall",))   # (reason, kinds)
        self._caps: list = []
        self._view = None                   # the LogView the track was built from
        self.track: Track | None = None
        self.waypoints: list = []
        self.values = None
        self.colour_cid = ""
        self._cursor = None
        self._live_gen = -1
        self._follow = True
        self._playing_follow = True
        self.stall_pins: list[gs.StallPin] = []
        self._stalls_t = -1e9
        self._stall_count = -1
        self.selected = -1
        self.note = ""
        self.canvas.miles = U.unit("km") == "mi"
        self._apply_mode()

    # ---- tiles ----

    def configure_tiles(self) -> None:
        """(Re)build the tile layer from the settings."""
        cfg = tile_settings(self.settings)
        source = gt.source_for(cfg["url"], cfg["attribution"])
        old = self.tiles
        if old is not None and old.source == source and old.cache.cap == int(cfg["cap_mb"] * 1e6):
            old.set_online(cfg["online"])
            self.canvas._layout()
            if hasattr(self, "tiles_btn"):
                self._sync_tiles()
            return
        if old is not None:
            old.close()
        self.tiles = TileLayer(source, online=cfg["online"], cap_mb=cfg["cap_mb"]) if source.valid else None
        if self.tiles is not None:
            self.canvas.max_zoom = min(proj.MAX_ZOOM, source.max_zoom)
        self.canvas.set_tiles(self.tiles)
        if hasattr(self, "tiles_btn"):
            self._sync_tiles()

    def use_tiles(self, layer: TileLayer | None) -> None:
        """Draw with another tile layer (tests, screenshots, the benchmark)."""
        if self.tiles is not None and self.tiles is not layer:
            self.tiles.close()
        self.tiles = layer
        if layer is not None:
            self.canvas.max_zoom = min(proj.MAX_ZOOM, layer.source.max_zoom)
        self.canvas.set_tiles(layer)

    def close_tiles(self) -> None:
        if self.tiles is not None:
            self.tiles.close()

    def set_tiles_online(self, on: bool) -> None:
        """The screen's "Download tiles": through the shell (it tells Replay's pane too)."""
        hook = getattr(self.window(), "_set_tiles", None)
        if callable(hook):
            hook(online=on)
        else:
            if self.settings is not None:
                self.settings.setValue("map/online", "true" if on else "false")
            self.configure_tiles()
        self._sync_tiles()

    def _ask_server(self) -> None:
        hook = getattr(self.window(), "ask_tile_server", None)
        if callable(hook):
            hook()
            self._sync_tiles()

    def _clear_cache(self) -> None:
        hook = getattr(self.window(), "clear_tile_cache", None)
        if callable(hook):
            hook()
        elif self.tiles is not None:
            self.tiles.clear_cache()
        self._sync_tiles()

    def _sync_tiles(self) -> None:
        cfg = tile_settings(self.settings)
        self.tiles_btn.setChecked(cfg["online"])
        forced = cfg["online"] and self.tiles is not None and not self.tiles.online   # HONDALOG_TILES=offline
        self.tiles_btn.set_text("Map tiles: offline" if forced else
                                "Map tiles: on" if cfg["online"] else "Map tiles: off")
        self.server_btn.setVisible(callable(getattr(self.window(), "ask_tile_server", None)))
        self.clear_btn.setEnabled(self.tiles is not None)

    # ---- modes ----

    def set_mode(self, mode: str) -> None:
        if mode not in ("drive", "stalls"):
            return
        self.mode = mode
        self.view_seg.set_current(mode)
        self._apply_mode()
        if mode == "stalls":
            self.load_stalls(fit=True)
        else:
            self._view = None               # rebuilt on the next frame
            self.refresh(self.session.bus.now())

    def _apply_mode(self) -> None:
        drive = self.mode == "drive"
        for widget in (self.col_seg, self.chan_btn, self.legend, self.at, self.info, self.gpx_btn, self.kml_btn,
                       self.drive_json_btn):
            widget.setVisible(drive)
        for widget in (self.since_seg, self.reason_btn, self.reading, self.list, self.json_btn, self.open_btn):
            widget.setVisible(not drive)
        self.fit_btn.setVisible(True)
        # one view's things never show in the other: the drive comes back on the next frame
        self.canvas.set_track(None)
        self.canvas.set_pins([])
        self.canvas.set_cursor(None)
        self.canvas.set_live(None)
        self._view = None
        self._live_gen = -1
        self._layout_side()
        self.update()

    # ---- the drive ----

    def _replay_view(self):
        return getattr(self.replay, "view", None)

    def _load_view(self, view) -> None:
        self._view = view
        self._cursor = None
        if view is None or view.live:
            self.track, self.waypoints, self.values = None, [], None
            self.canvas.set_track(None)
            self.canvas.set_pins([])
            self.canvas.set_cursor(None)
            self._live_gen = -1
            self._follow = True
            return
        self.canvas.set_live(None)
        self.track = Track.from_view(view)
        self.waypoints = view_waypoints(view) if not self.track.empty else []
        pins = []
        if not self.track.empty:
            from ...export.geo import place
            for w in self.waypoints:
                where = place(self.track, w.t)
                if where is None:
                    continue
                x, y = proj.to_unit(where[1], where[0])
                pins.append(Pin(float(x), float(y), w.kind, _mark_label(w.name) if w.kind == "marker" else "", w))
            pins.insert(0, Pin(float(self.track.x[0]), float(self.track.y[0]), "start"))
        self.canvas.set_track(self.track)
        self.canvas.set_pins(pins)
        self._colourize()
        if not self.track.empty:
            self.canvas.fit(self.track.bbox())
        self._texts()
        self._track_info()

    def _channels(self) -> list[str]:
        view = self._view
        if view is None:
            return []
        return [cid for cid in view.t if cid not in SKIP_CHANNELS and len(view.t[cid])
                and np.isfinite(np.asarray(view.data[cid], dtype=float)).any()]

    def _resolve(self, colour: str) -> str:
        """A preset (or a channel id) -> the channel id of this drive that shows it."""
        view = self._view
        have = self._channels()
        for key, _label, ids in PRESETS:
            if key == colour:
                for cid in ids:
                    if cid in have and (cid != "vss" or np.nanmax(view.data[cid]) > 0):
                        return cid
                return ""
        return colour if colour in have else ""

    def set_colour(self, colour: str) -> None:
        self.colour = colour
        is_preset = colour in {k for k, _, _ in PRESETS}
        self.col_seg.set_current(colour if is_preset else None)
        self.chan_btn.setCheckable(True)
        self.chan_btn.setChecked(not is_preset)
        self.chan_btn.set_text("Other channel…" if is_preset else self._label(colour) + " ▾")
        self._colourize()
        self._texts()

    def cycle_colour(self) -> None:
        keys = [k for k, _, _ in PRESETS]
        nxt = keys[(keys.index(self.colour) + 1) % len(keys)] if self.colour in keys else keys[0]
        self.set_colour(nxt)

    def _label(self, cid: str) -> str:
        view = self._view
        spec = view.specs.get(cid) if view is not None else None
        return spec.label if spec is not None else cid

    def _spec(self, cid: str):
        view = self._view
        spec = view.specs.get(cid) if view is not None else None
        if spec is None:
            from ..bus import ChannelSpec
            spec = ChannelSpec(cid, cid)
        return spec

    def _colourize(self) -> None:
        track, view = self.track, self._view
        cid = self._resolve(self.colour) if view is not None and not view.live else ""
        self.colour_cid = cid
        if track is None or track.empty or not cid:
            self.values = None
            self.canvas.set_values(None)
            what = dict((k, label) for k, label, _ in PRESETS).get(self.colour, self.colour)
            self.legend.set(f"Colour: {what} (not in this drive)" if track is not None and not track.empty
                            else "Colour", None, ("", "", ""))
            return
        values = track.sample(view.t[cid], view.data[cid])
        lo, hi, diverging = ramps.value_range(cid, values)
        self.values = values
        self.canvas.set_values(values, lo, hi, diverging)
        spec = self._spec(cid)
        shown = U.spec(spec)
        mid = (lo + hi) / 2

        def txt(v):
            return U.text(spec, v)
        units = f" ({shown.units})" if shown.units else ""
        self.legend.set(f"Colour: {spec.label}{units}", ramps.DIV_RAMP if diverging else ramps.SEQ_RAMP,
                        (txt(lo), txt(mid), txt(hi)))

    def _texts(self) -> None:
        view = self._view
        if self.mode == "stalls":
            n = len(self.stall_pins)
            placed = sum(1 for p in self.stall_pins if p.located)
            what = "captures" if self.reason[1] is None else "stalls"
            self.canvas.set_text(f"All {what} · {placed} of {n} placed" if n else f"All {what}",
                                 "Octagon = stall, diamond = other capture. Click a pin to open it in Replay."
                                 if n else "None match the filters yet.")
            return
        if view is None:
            self.canvas.set_text("No drive open", "O opens a drive in Replay; its GPS track shows here.")
        elif view.live:
            return                          # _refresh_live words it
        elif self.track is None or self.track.empty:
            self.canvas.set_text(view.title, "No GPS in this drive: the GPS comes from the handheld "
                                             "(its SD card, or live over cyd:).")
        else:
            what = self._label(self.colour_cid) if self.colour_cid else "nothing (not in this drive)"
            self.canvas.set_text(view.title, f"Coloured by {what}. Click the track to move Replay's cursor there.")

    def _track_info(self) -> None:
        tr = self.track
        if tr is None or tr.empty:
            self.info.set([("Distance", "---"), ("Time", "---")], "no GPS fixes")
            return
        gaps = int(tr.breaks[1:].sum())
        minutes = tr.duration / 60.0
        self.info.set([("Distance", distance_text(tr.length_m)),
                       ("Time", f"{minutes:.0f} min" if minutes >= 2 else f"{tr.duration:.0f} s")],
                      f"{len(tr)} fixes · {gaps} gap{'s' if gaps != 1 else ''} over 5 s")

    def _update_cursor(self, t: float, follow: bool) -> None:
        tr = self.track
        pos = tr.at(t) if tr is not None else None
        if pos is None:
            self.canvas.set_cursor(None)
            self.at.set([("Time", f"{_signed(t)} s"), ("Speed", "---"), ("", ""), ("", "")], "no GPS fix here")
            return
        self.canvas.set_cursor((pos.x, pos.y), pos.heading)
        value = "---"
        label = "Value"
        if self.colour_cid in ("", "gps_speed"):
            label, value = "From start", distance_text(float(np.interp(pos.index, np.arange(len(tr)), tr.dist)))
        elif self.colour_cid and self.values is not None:
            view = self._view
            cid = self.colour_cid
            tt = view.t[cid]
            i = int(np.searchsorted(tt, t, side="right")) - 1
            if i >= 0:
                value = U.with_unit(self._spec(cid), float(view.data[cid][i]))
            label = self._label(cid)
        self.at.set([("Time", f"{_signed(t)} s"), ("GPS speed", speed_text(pos.speed_kmh)),
                     (label, value), ("Heading", f"{pos.heading:.0f}°" if pos.heading == pos.heading else "---")],
                    f"{pos.lat:.6f}, {pos.lon:.6f}")
        if follow and not self.canvas.on_screen(pos.x, pos.y, 40):
            self.canvas.center_on(pos.x, pos.y)

    def _refresh_live(self, now: float) -> None:
        bus = self.session.bus
        lat, lon = bus.channel("gps_lat"), bus.channel("gps_lon")
        if lat is None or lon is None:
            if self._live_gen != -2:
                self._live_gen = -2
                self.canvas.set_live(None)
                self.canvas.set_text("Live: no GPS", "The handheld sends its GPS over cyd: (connect it in the "
                                                     "ECU chip's menu). O opens a recorded drive.")
                self.at.set([("Speed", "---"), ("Heading", "---"), ("Satellites", "---"), ("", "")], "")
                self.info.set([("Distance", "---"), ("Time", "live")], "no fixes yet")
                self.legend.set("Colour", None, ("", "", ""))
            return
        if lat.gen == self._live_gen:
            return
        self._live_gen = lat.gen
        stale = lat.stale(now) or lon.stale(now)
        t, la = bus.history("gps_lat", now - TRAIL_S)
        t2, lo = bus.history("gps_lon", now - TRAIL_S)
        n = min(len(la), len(lo))
        trail = None
        if n > 1:
            x, y = proj.to_unit(lo[-n:].astype(np.float64), la[-n:].astype(np.float64))
            trail = np.column_stack((x, y))
        here = proj.to_unit(lon.value, lat.value)
        here = (float(here[0]), float(here[1]))
        heading = bus.channel("gps_heading")
        hd = heading.value if heading is not None and not heading.stale(now) else None
        self.canvas.set_live(None if stale else here, hd, trail)
        if self._follow and not stale:
            if self.canvas.zoom < 14:
                self.canvas.center_on(*here, zoom=16)
            elif not self.canvas.on_screen(*here, 80):
                self.canvas.center_on(*here)
        speed = bus.channel("gps_speed")
        sats = bus.channel("gps_sats")
        self.canvas.set_text("Live: handheld GPS" + (" (no fix now)" if stale else ""),
                             f"The car and the last {TRAIL_S / 60:.0f} min. F follows it; O opens a drive.")
        self.at.set([("Speed", speed_text(speed.value if speed is not None and not stale else None)),
                     ("Heading", f"{hd:.0f}°" if hd is not None else "---"),
                     ("Satellites", f"{sats.value:.0f}" if sats is not None and not sats.stale(now) else "---"),
                     ("Trail", f"{n} fixes")], f"{lat.value:.6f}, {lon.value:.6f}")
        self.info.set([("Distance", distance_text(float(np.sum(proj.haversine_m(
            la[-n:-1], lo[-n:-1], la[-n + 1:], lo[-n + 1:])))) if n > 1 else "---"), ("Time", "live")],
            f"the last {n} fixes")
        self.legend.set("Colour (recorded drives)", None, ("", "", ""))

    # ---- all stalls ----

    def _since(self) -> float | None:
        key = self.since_seg.current or "all"
        return None if key == "all" else time.time() - float(key) * 86400.0

    def load_stalls(self, fit: bool = False) -> None:
        self._stalls_t = self.session.bus.now()
        reason, kinds = self.reason
        database = self._database()
        if database is not None:
            pins = gs.stall_pins(database, since=self._since(), reason=reason, kinds=kinds)
        elif self.session.demo:
            from ..backends import demo_stall_pins
            pins = [p for p in demo_stall_pins() if (reason is None or p.reason == reason)
                    and (kinds is None or p.kind in kinds)]
        else:
            pins = []
        pins = sorted(pins, key=lambda p: (p.wall_unix or 0.0), reverse=True)
        keep = self.stall_pins[self.selected].name if 0 <= self.selected < len(self.stall_pins) else None
        self.stall_pins = pins
        self.selected = next((i for i, p in enumerate(pins) if p.name == keep), -1)
        self._stall_pins_to_canvas()
        self.list.set_rows([("stall" if p.is_stall else "capture", (p.wall_time or "")[:16].replace("T", " "),
                             p.reason or p.kind, speed_text(p.max_speed_30s) if p.located else "no fix")
                            for p in pins], self.selected)
        self.reading.set_text(gs.summary(pins, speed=speed_text, distance=distance_text))
        self.open_btn.setEnabled(self.selected >= 0)
        self._texts()
        if fit:
            self.fit()

    def _stall_pins_to_canvas(self) -> None:
        pins = []
        for i, p in enumerate(self.stall_pins):
            if not p.located:
                continue
            x, y = proj.to_unit(p.lon, p.lat)
            pins.append(Pin(float(x), float(y), "stall" if p.is_stall else "capture", "", i, i == self.selected))
        pins.sort(key=lambda pin: pin.selected)
        self.canvas.set_pins(pins)

    def _database(self):
        """The drive database behind Replay's drive picker (None in the demo)."""
        return getattr(self.session.logs, "database", None)

    def select_pin(self, i: int, centre: bool = True) -> None:
        if not 0 <= i < len(self.stall_pins):
            return
        self.selected = i
        self.list.select(i)
        self.open_btn.setEnabled(True)
        self._stall_pins_to_canvas()
        p = self.stall_pins[i]
        if centre and p.located:
            x, y = proj.to_unit(p.lon, p.lat)
            if not self.canvas.on_screen(float(x), float(y), 60):
                self.canvas.center_on(float(x), float(y))

    def step_pin(self, direction: int) -> None:
        if self.stall_pins:
            i = self.selected + direction if self.selected >= 0 else (0 if direction > 0 else len(self.stall_pins) - 1)
            self.select_pin(max(0, min(len(self.stall_pins) - 1, i)))

    def open_pin(self, i: int) -> None:
        if not 0 <= i < len(self.stall_pins):
            return
        self.select_pin(i, centre=False)
        pin = self.stall_pins[i]
        if self._database() is None and self.session.demo:
            from ..backends import demo_stall_view
            self.replay.show_view(demo_stall_view())
            self.openRequested.emit(None)
            return
        self.openRequested.emit(pin.ref)

    def open_selected(self) -> None:
        self.open_pin(self.selected)

    def reason_menu(self) -> None:
        menu = QMenu(self)
        options: list[tuple[str, tuple]] = [("Stalls, any reason", (None, ("stall",))),
                                            ("Every capture", (None, None))]
        database = self._database()
        if database is not None:
            counts = gs.reasons(database)
        elif self.session.demo:
            counts = [("stall", "rpm", 3), ("stall", "link", 1)]
        else:
            counts = []
        for kind, reason, n in counts:
            if reason:
                options.append((f"{kind}: {reason}  ({n})", (reason, (kind,))))
        for text, value in options:
            action = QAction(text, menu, checkable=True)
            action.setChecked(value == self.reason)
            action.triggered.connect(lambda _=False, v=value, t=text: self.set_reason(v, t))
            menu.addAction(action)
        menu.exec(QCursor.pos())

    def set_reason(self, value, text: str = "") -> None:
        self.reason = value
        self.reason_btn.set_text(re.sub(r"\s+\(\d+\)$", "", text) if text else "Stalls, any reason")
        self._layout_side()
        self.load_stalls(fit=True)

    # ---- actions ----

    def fit(self) -> None:
        if self.mode == "stalls":
            located = [p for p in self.stall_pins if p.located]
            if located:
                x, y = proj.to_unit(np.array([p.lon for p in located]), np.array([p.lat for p in located]))
                pad = 0.25 / proj.world_px(16) * 256
                self.canvas.fit((float(x.min()) - pad, float(y.min()) - pad, float(x.max()) + pad,
                                 float(y.max()) + pad), max_zoom=16)
            return
        view = self._view
        if view is not None and view.live:
            self._follow = True
            self._live_gen = -1
            if self.canvas.live is not None:
                self.canvas.center_on(*self.canvas.live, zoom=max(self.canvas.zoom, 15))
            return
        if self.track is not None and not self.track.empty:
            self.canvas.fit(self.track.bbox())

    def _user_moved(self) -> None:
        self._follow = False

    def _clicked(self, x: float, y: float) -> None:
        tr = self.track
        if self.mode != "drive" or tr is None or tr.empty or self._view is None or self._view.live:
            return
        hit = tr.nearest(x, y, self.canvas.px_to_unit(14.0))
        if hit is not None:
            self.move_cursor(hit[0])

    def move_cursor(self, t: float) -> None:
        """Replay's cursor to view time ``t`` (the dot follows on the next frame)."""
        block = getattr(self.replay, "block", None)
        if block is not None:
            block.set_cursor(t)
        self._update_cursor(t, follow=False)
        self._cursor = getattr(block, "cursor", t)

    def _pin_clicked(self, payload) -> None:
        if self.mode == "stalls":
            if isinstance(payload, int):
                self.open_pin(payload)
            return
        if payload is not None and hasattr(payload, "t"):
            self.move_cursor(float(payload.t))

    def step_waypoint(self, direction: int) -> None:
        if not self.waypoints:
            return
        now = self._cursor if self._cursor is not None else (self.track.t[0] if self.track is not None else 0.0)
        times = [w.t for w in self.waypoints]
        target = ([t for t in times if t > now + 1e-6][:1] if direction > 0 else
                  [t for t in times if t < now - 1e-6][-1:])
        if target:
            self.move_cursor(target[0])

    def channel_menu(self) -> None:
        if self.mode != "drive":
            return
        menu = QMenu(self)
        view = self._view
        if view is None or view.live or self.track is None or self.track.empty:
            menu.addAction("Open a drive with GPS first").setEnabled(False)
            menu.exec(QCursor.pos())
            return
        groups = {"ECU": [], "MATH": [], "BB": [], "GPS": []}
        for cid in self._channels():
            spec = view.specs.get(cid)
            source = "GPS" if cid.startswith("gps_") else (spec.source if spec is not None else "ECU")
            groups.setdefault(source, []).append(cid)
        titles = {"ECU": "ECU", "MATH": "Worked out", "BB": "Black box", "GPS": "GPS"}
        for source, cids in groups.items():
            if not cids:
                continue
            menu.addSection(titles.get(source, source))
            for cid in cids:
                action = QAction(self._label(cid), menu, checkable=True)
                action.setChecked(cid == self.colour_cid)
                action.triggered.connect(lambda _=False, c=cid: self.set_colour(c))
                menu.addAction(action)
        menu.exec(QCursor.pos())

    def default_path(self, ext: str) -> Path:
        from ...data import files_dir
        name = "stalls" if self.mode == "stalls" else (getattr(self._view, "title", "") or "drive")
        return files_dir() / f"{Path(str(name)).stem}.{ext}"

    def export(self, fmt: str, path: str | os.PathLike | None = None) -> str:
        """GPX / KML / GeoJSON of the drive, or GeoJSON of the stalls (in the
        All stalls view). Without ``path`` a save dialog asks. Returns a line
        for the user ('' when cancelled)."""
        fmt = fmt.lower()
        if self.mode == "stalls":
            if not self.stall_pins:
                return self._say("No stalls to export")
            if path is None:
                path, _ = QFileDialog.getSaveFileName(self, "Export stalls", str(self.default_path("geojson")),
                                                      "GeoJSON (*.geojson *.json)")
                if not path:
                    return ""
            out = gs.write_geojson(self.stall_pins, path)
            return self._say(f"Wrote {out} ({sum(1 for p in self.stall_pins if p.located)} stalls)")
        view = self._view
        log = getattr(getattr(view, "replay", None), "log", None)
        if view is None or view.live or log is None or self.track is None or self.track.empty:
            return self._say("Open a recorded drive with GPS first (O)")
        if path is None:
            what = {"gpx": "GPX (*.gpx)", "kml": "KML (*.kml)"}.get(fmt, "GeoJSON (*.geojson *.json)")
            path, _ = QFileDialog.getSaveFileName(self, f"Export {fmt.upper()}", str(self.default_path(fmt)), what)
            if not path:
                return ""
        from ...export.geo import export_geo
        channel = view.names.get(self.colour_cid, (self.colour_cid, 1.0))[0] if self.colour_cid else None
        try:
            out = export_geo(log, path, fmt, channel=channel if channel and log.column(channel) is not None else None)
        except (OSError, ValueError) as exc:
            return self._say(f"Export failed: {exc}")
        return self._say(f"Wrote {out}")

    def _say(self, text: str) -> str:
        self.note = text
        win = self.window()
        if hasattr(win, "say"):
            win.say(text)
        return text

    # ---- frame ----

    def refresh(self, now: float) -> None:
        self.canvas.refresh()
        if self.mode == "stalls":
            count = self.session.stall.count
            if count != self._stall_count or now - self._stalls_t >= RELOAD_STALLS_S:
                first = self._stall_count < 0
                self._stall_count = count
                if not first:
                    self.load_stalls()
            return
        view = self._replay_view()
        if view is not self._view and not (view is not None and view.live and self._view is not None
                                           and self._view.live):
            self._load_view(view)
            if view is None or not view.live:
                self._texts()
        if view is not None and view.live:
            self._view = view
            self._refresh_live(now)
            return
        block = getattr(self.replay, "block", None)
        cursor = getattr(block, "cursor", None)
        if cursor is not None and cursor != self._cursor:
            self._cursor = cursor
            self._update_cursor(cursor, follow=self._playing_follow and bool(getattr(self.replay, "playing", False)))

    def key(self, event) -> bool:
        k, mods = event.key(), event.modifiers()
        shift = bool(mods & Qt.ShiftModifier)
        text = event.text()
        c = self.canvas
        step = 0.25
        if k == Qt.Key_Left:
            c.pan_px(c.width() * step, 0)
        elif k == Qt.Key_Right:
            c.pan_px(-c.width() * step, 0)
        elif k == Qt.Key_Up:
            c.pan_px(0, c.height() * step)
        elif k == Qt.Key_Down:
            c.pan_px(0, -c.height() * step)
        elif text in ("+", "=") or k == Qt.Key_PageUp:
            c.zoom_by(1)
        elif text in ("-", "_") or k == Qt.Key_PageDown:
            c.zoom_by(-1)
        elif k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_V:
            self.set_mode("stalls" if self.mode == "drive" else "drive")
        elif k == Qt.Key_C and shift:
            self.channel_menu()
        elif k == Qt.Key_C:
            if self.mode == "drive":
                self.cycle_colour()
        elif k == Qt.Key_G:
            self.export("geojson" if self.mode == "stalls" else "gpx")
        elif k == Qt.Key_K:
            if self.mode == "drive":
                self.export("kml")
        elif k == Qt.Key_J:
            self.export("geojson")
        elif k == Qt.Key_R:
            if self.mode == "stalls":
                self.reason_menu()
        elif text == "[":
            self.step_pin(-1) if self.mode == "stalls" else self.step_waypoint(-1)
        elif text == "]":
            self.step_pin(1) if self.mode == "stalls" else self.step_waypoint(1)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            if self.mode == "stalls":
                self.open_selected()
            else:
                return False
        elif k == Qt.Key_O:
            self.replay.open_dialog()
        elif k == Qt.Key_L:
            self.replay.go_live()
        else:
            return False
        return True

    # ---- layout / paint ----

    def _layout_side(self) -> None:
        w, h = self.width(), self.height()
        sx = w - 8 - SIDE
        self._caps = [("View", sx, 8)]
        self.view_seg.setGeometry(sx, 25, SIDE, 44)
        bottom = h - 8 - 44
        # map tiles: a row of their own above the buttons, in both views
        ty = bottom - 8 - 36
        sw = self.server_btn.sizeHint().width() if self.server_btn.isVisibleTo(self) else 0
        cw = self.clear_btn.sizeHint().width()
        tw = SIDE - cw - 8 - (sw + 8 if sw else 0)
        self.tiles_btn.setGeometry(sx, ty, tw, 36)
        if sw:
            self.server_btn.setGeometry(sx + tw + 8, ty, sw, 36)
        self.clear_btn.setGeometry(sx + SIDE - cw, ty, cw, 36)
        bottom_card = ty - 8
        if self.mode == "drive":
            self._caps.append(("Colour by", sx, 77))
            self.col_seg.setGeometry(sx, 94, SIDE, 44)
            self.chan_btn.setGeometry(sx, 146, SIDE, 44)
            self.legend.setGeometry(sx, 198, SIDE, 128)
            self.at.setGeometry(sx, 334, SIDE, 138)
            self.info.setGeometry(sx, 480, SIDE, max(60, bottom_card - 480))
            bw = (SIDE - 24) / 4
            for i, btn in enumerate((self.fit_btn, self.gpx_btn, self.kml_btn, self.drive_json_btn)):
                btn.setGeometry(int(sx + i * (bw + 8)), bottom, int(bw), 44)
        else:
            self._caps.append(("Since", sx, 77))
            self.since_seg.setGeometry(sx, 94, SIDE, 44)
            self._caps.append(("Trigger", sx, 146))
            self.reason_btn.setGeometry(sx, 163, SIDE, 44)
            self.reading.setGeometry(sx, 215, SIDE, 128)
            self.list.setGeometry(sx, 351, SIDE, max(80, bottom_card - 351))
            ow = self.open_btn.sizeHint().width()
            fw = (SIDE - 16 - ow) / 2
            self.fit_btn.setGeometry(sx, bottom, int(fw), 44)
            self.json_btn.setGeometry(int(sx + fw + 8), bottom, int(fw), 44)
            self.open_btn.setGeometry(int(sx + 2 * fw + 16), bottom, SIDE - int(2 * fw + 16), 44)

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        cw = w - 8 - SIDE - 16
        self.canvas.setGeometry(8, 8, cw, h - 16)
        for i, b in enumerate((self.zoom_in, self.zoom_out)):   # top right of the map
            b.setGeometry(8 + cw - 10 - 40, 18 + i * 40, 40, 36)
            b.raise_()
        self._sync_tiles()
        self._layout_side()

    def retheme(self) -> None:
        self.canvas.retheme()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        for cap, x, y in self._caps:
            paint.caption(p, x, y, cap)
        p.end()

    def stats(self) -> dict:
        return self.canvas.stats()

    def units_changed(self) -> None:
        self.canvas.miles = U.unit("km") == "mi"
        self._colourize()
        self._track_info()
        self.canvas._layout()


__all__ = ["MapScreen", "distance_text", "speed_text", "tile_settings"]
