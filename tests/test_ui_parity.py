"""Desktop / web parity, round 2 (PARITY-DECISIONS.md in the web worktree): the
square link lamp, "(F4)" key labels, the joined sub-tab strip, the DEMO DATA
badge, Setup > Alarms, Replay's lane ×, the GPS screen's zoom and tile buttons."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest

from conftest import spin
from hondalog.alarms import AlarmRule
from hondalog.ui import main_window as mw
from hondalog.ui import paint, theme
from hondalog.ui.controls import Button
from hondalog.ui.screens import setup as su
from hondalog.ui.screens.alarms import rule_text
from hondalog.ui.screens.common import TAB_H, TabStrip


def test_link_lamp_is_a_filled_square(app):
    img = QImage(20, 20, QImage.Format_RGB32)
    img.fill(theme.T.bg)
    p = QPainter(img)
    paint.chip_icon(p, "ok", 2, 2, theme.T.fg, size=14)
    p.end()
    # a circle leaves its corners empty; the square fills them
    assert img.pixelColor(3, 3) == theme.T.ok and img.pixelColor(14, 14) == theme.T.ok


def test_f_keys_are_written_after_the_label(app):
    b = Button("Open in Replay", "F4")
    assert b.shown_text() == "Open in Replay (F4)" and b._cap() == ""
    plain = Button("Add a note", "N")
    assert plain.shown_text() == "Add a note" and plain._cap() == "N"
    assert b.sizeHint().width() > Button("Open in Replay").sizeHint().width()


def test_sub_tabs_are_one_joined_strip(app):
    strip = TabStrip((("a", "Alarms"), ("b", "Units"), ("c", "About")), "a")
    cells = strip._cells()
    assert cells[1].x() == cells[0].right() + 1 and cells[2].x() == cells[1].right() + 1   # 1 px line between
    strip.resize(600, TAB_H)
    img = strip.grab().toImage()
    assert img.pixelColor(300, TAB_H - 1) == theme.T.rule          # the rule runs under the whole strip
    assert img.pixelColor(int(cells[0].center().x()), 4) == theme.T.accent


def test_demo_badge_in_the_top_bar(shell, app):
    spin(app, shell)
    assert shell.status.badge == "DEMO DATA" and shell.strip.badge == "DEMO DATA"   # drawn in the bottom strip
    assert shell.status.badge_width("DEMO DATA") > 40


def test_setup_pages_match_the_web_plus_rom(shell, app):
    assert [label for _, label in su.PAGES] == ["Alarms", "CYD black box", "Units", "Cable", "Files", "Theme",
                                                "ROM / Tune", "About"]


def test_alarms_page_lists_rules_and_acknowledges(shell, app):
    shell.show_screen(mw.ALARMS)
    spin(app, shell, 3)
    page = shell.alarms
    names = [r.name for r in page.rules.rules]
    assert "ect_high" in names and "idle_collapse" in names
    assert page.rules.title.endswith(f"({len(names)})")
    ect = next(r for r in page.rules.rules if r.name == "ect_high")
    assert rule_text(ect) == "ect > 105 for 2 s"
    collapse = next(r for r in page.rules.rules if r.name == "idle_collapse")
    assert rule_text(collapse).startswith("rpm falls faster than -400/s") and "while [rpm] < 600" in rule_text(collapse)
    assert rule_text(AlarmRule("x", when="[a] > 1", hold_s=0.5)) == "[a] > 1 for 0.5 s"
    # the demo's intake air warning: in the list, acknowledged with the button
    active = list(shell.session.alarms.active())
    if active:
        assert page.list.ack_btn.isEnabled()
        page.list.ack_btn.click()
        spin(app, shell)
        assert all(e.acked for e in shell.session.alarms.active())
        assert not page.list.ack_btn.isEnabled()
    assert not hasattr(page, "states") and not hasattr(page, "key_tiles")   # the old design legend is gone


def test_replay_lane_closes_with_its_x(shell, app):
    shell.show_screen(mw.REPLAY)
    spin(app, shell, 3)
    block = shell.replay.block
    before = block.lane_ids()
    assert len(before) > 1
    lane = block.lanes[1]
    r = block.close_rect(lane)
    QTest.mouseClick(block, Qt.LeftButton, Qt.NoModifier, r.center().toPoint())
    assert block.lane_ids() == [c for c in before if c != lane.cid]
    # the label itself does not close it
    QTest.mouseClick(block, Qt.LeftButton, Qt.NoModifier, QPoint(30, int(block.lanes[0].y + 10)))
    assert len(block.lane_ids()) == len(before) - 1


def test_gps_screen_zoom_and_tile_buttons(shell, app):
    shell.show_screen(mw.MAP)
    spin(app, shell, 3)
    m = shell.map
    assert m.zoom_in.isVisible() and m.zoom_out.isVisible()
    assert m.canvas.geometry().contains(m.zoom_in.geometry())
    z = m.canvas.zoom
    m.zoom_in.click()
    assert m.canvas.zoom > z
    m.zoom_out.click()
    assert abs(m.canvas.zoom - z) < 1e-6
    assert m.tiles_btn.isVisible() and m.clear_btn.isVisible()
    m.set_tiles_online(False)
    assert shell.settings.value("map/online") == "false" and not m.tiles_btn.isChecked()
    assert m.tiles_btn.text() == "Map tiles: off"
    m.set_tiles_online(True)
    assert shell.settings.value("map/online") == "true" and m.tiles_btn.isChecked()
    # nothing on the side column overlaps
    side = [w for w in (m.tiles_btn, m.server_btn, m.clear_btn, m.fit_btn, m.gpx_btn, m.kml_btn,
                        m.drive_json_btn, m.info) if w.isVisible()]
    for i, a in enumerate(side):
        assert not any(a.geometry().intersects(b.geometry()) for b in side[i + 1:]), a


def test_events_button_says_f4(shell, app):
    shell.show_screen(mw.EVENTS)
    spin(app, shell, 3)
    assert shell.events.replay_btn.shown_text() == "Open in Replay (F4)"
    assert shell.replay.pull_card.power_btn.shown_text() == "Open in Power (F5)"

