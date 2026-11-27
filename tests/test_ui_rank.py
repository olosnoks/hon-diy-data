"""Raw tools: the "Rank addresses" panel on W3's correlation finder."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from conftest import spin
from hondalog import addressmap
from hondalog.ui import adapters as ad
from hondalog.ui import main_window as mw
from hondalog.ui.session import build_session
from mapping_helpers import ram


def key(win, k, mods=Qt.NoModifier, text=""):
    win.handle_key(QKeyEvent(QEvent.KeyPress, k, mods, text))


def feed(mapper, rows, dt=0.25):
    """Put sweeps into the mapper the way finished sweeps arrive."""
    for i, row in enumerate(rows):
        mapper._hist[:-1] = mapper._hist[1:]
        mapper._hist[-1] = row
        mapper._sweep_t.append(100.0 + i * dt)
        known = row >= 0
        mapper.minv[known] = np.minimum(mapper.minv[known], row[known])
        mapper.maxv[known] = np.maximum(mapper.maxv[known], row[known])
        mapper.cur[:] = row
        mapper.sweeps += 1
    mapper.revision += 1


@pytest.fixture()
def user_map(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return addressmap.load_user_map()


# ---- the session (wired: W3 correlate + mapedit) ----

def test_rank_and_assign_through_the_wired_mapper(user_map):
    session = build_session()
    session.start()
    mapper = session.mapper
    refs = mapper.rank_references()
    assert all(isinstance(r, ad.RankReference) for r in refs)
    assert [r.key for r in refs[-2:]] == ["shape:rise", "shape:fall"] and refs[-1].kind == "shape"
    channels = [r for r in refs if r.kind == "channel"]
    assert {"rpm", "tps", "ect"} <= {r.key for r in channels}
    verified = [r.verified for r in channels]
    assert verified == sorted(verified, reverse=True)       # proven references first

    assert mapper.rank("tps") == [] and "0 sweeps so far" in mapper.console()
    # throttle pressed three times; the A/C clutch and a counter do their own thing
    feed(mapper, [ram(rpm=800 + 5 * (i % 3), tps=40 * abs(np.sin(i / 5.0)), noise=i, ect=80.0)
                  for i in range(40)])
    rows = mapper.rank("tps")
    assert rows and isinstance(rows[0], ad.RankRow)
    best = rows[0]
    assert (best.addr, best.width, best.label) == (0xA0, 1, "0xA0 u8") and best.score > 0.99 and best.r > 0
    assert best.mapped == "" and (best.lo, best.hi) == (24, 104) and "x" in best.formula   # (x - 24) / 2
    assert 0x14 not in [r.addr for r in rows]               # the reference's own byte is left out
    assert "0xA0 u8" in mapper.console()
    assert [c.addr for c in mapper.rank_against("tps", top=3)][0] == 0xA0      # the short form, same answer
    assert mapper.rank("nope") == [] and "nope" in mapper.console()

    # a shape made by hand: the counter rises steadily, nothing else does
    rise = mapper.rank("shape:rise")
    assert rise[0].addr == 0x80 and rise[0].r > 0.99 and rise[0].formula == ""
    fall = mapper.rank("shape:fall")
    assert fall[0].addr == 0x80 and fall[0].r < -0.99       # it runs the other way

    # assign: through the map editor into the user's map file
    targets = dict(mapper.assign_targets())
    assert "now 0x14" in targets["tps"]
    path = addressmap.user_map_path()
    before = addressmap.load(path)
    message = mapper.assign(best, "tps")
    after = addressmap.load(path)
    tps = after.channel("tps")
    assert (tps.addr, tps.length) == (0xA0, 1) and not tps.verified and tps.formula == before.channel("tps").formula
    assert after.version != before.version and after.version_hash != before.version_hash
    assert "tps now reads 0xA0 u8" in message and "was 0x14" in message and "unverified" in message
    assert session.ecu.amap.channel("tps").addr == 0xA0 and mapper.mapped(0xA0) == ("tps", False)
    assert "nothing to write" in mapper.assign(best, "tps") and "No channel" in mapper.assign(best, "nope")
    wide = ad.RankRow(0x90, 2, "little", False, "0x90 u16le")
    assert "rpm now reads 0x90 u16le" in mapper.assign(wide, "rpm")
    rpm = addressmap.load(path).channel("rpm")
    assert (rpm.addr, rpm.length, rpm.endian) == (0x90, 2, "little")
    session.close()


def test_assign_refuses_the_shipped_map(monkeypatch):
    session = build_session()
    session.start()
    monkeypatch.setattr(session.ecu.amap, "path", str(addressmap.DEFAULT_MAP))
    shipped = addressmap.DEFAULT_MAP.read_text(encoding="utf-8")
    assert "read-only" in session.mapper.assign(ad.RankRow(0xA0, label="0xA0 u8"), "tps")
    assert addressmap.DEFAULT_MAP.read_text(encoding="utf-8") == shipped
    session.close()


# ---- the panel ----

def test_rank_panel_in_the_demo(shell, app):
    shell.show_screen(mw.RAW)
    raw, mapper = shell.raw, shell.session.mapper
    screen = shell.bytes
    spin(app, shell)
    assert screen.page == "memory" and not raw.rank.isVisible()
    key(shell, Qt.Key_R)                                    # R: the Rank page
    assert screen.page == "rank" and raw.rank.isVisible() and not raw.wizard.isVisible()
    card = raw.rank
    assert card.ref is not None and card.ref.verified and card.ref_btn.text().startswith("Follows: ")
    assert not card.assign_btn.isEnabled() and card.rank_btn.isEnabled() and not card.ranked
    key(shell, Qt.Key_N)                                    # the wizard's keys are Verify's
    assert mapper.wizard().step == 0

    key(shell, Qt.Key_G)                                    # too few sweeps: says so
    assert card.ranked and card.rows == [] and "sweeps so far" in card.message
    mapper.sweeping = True
    for k in range(30):
        mapper.fill(60.0 + k * 0.9)
    card.set_reference("tps")
    assert card.ref.key == "tps" and card.rows == [] and not card.ranked
    assert "Throttle" in card.instruction() and "own bytes are left out" in card.instruction()
    key(shell, Qt.Key_G)
    spin(app, shell)
    assert len(card.rows) >= 3 and card.rows[0].score > 0.9 and card.assign_btn.isEnabled()
    assert 0x14 not in [r.addr for r in card.rows] and 0x5A in [r.addr for r in card.rows]
    assert raw.grid.sel == card.rows[0].addr                # the best one is selected in the hex grid
    QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier,
                     QPoint(40, int(card._rows_top() + card.ROW * 1.5)))
    assert card.sel == 1 and raw.grid.sel == card.rows[1].addr
    assert screen.rank_page.inspector.addr == card.rows[1].addr

    # Assign to channel…: asks, then writes through the session's map editor
    asked = []
    card.confirm = lambda title, text: (asked.append(text), False)[1]
    row = card.selected()
    targets = dict(mapper.assign_targets())
    assert "iacv" in targets and "now 0x28" in targets["iacv"]
    assert not card.assign("iacv") and row.label in asked[0] and "unverified" in asked[0]
    assert mapper.mapped(0x28) == ("iacv", False)           # "no": nothing changed
    card.confirm = lambda title, text: True
    assert card.assign("iacv") and "iacv now reads" in card.message
    assert mapper.mapped(row.addr) == ("iacv", False) and mapper.mapped(0x28) is None
    assert card.rows[card.sel].mapped == "iacv"             # the list shows the new owner
    spin(app, shell)
    assert raw.grid.guess[row.addr] and not raw.grid.guess[0x28]

    # a shape: a different instruction, no formula
    card.set_reference("shape:rise")
    assert card.ref_btn.text() == "Rises steadily" and "Clear changes (C)" in card.instruction()
    card.rank()
    assert card.rows and all(r.formula == "" for r in card.rows)
    screen.grab()
    key(shell, Qt.Key_R)                                    # R again: back to the scanner
    assert screen.page == "memory" and not raw.rank.isVisible()
