"""The Tune screen on W5: Open ROM, Load definition, the tracer, save, burn image."""

import dataclasses

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from conftest import spin
from hondalog.tuning import TableTracer, load_definition, save_xdf
from hondalog.tuning.demo import DEMO_MAP_KPA, DEMO_RPM, build_demo_rom, demo_definition
from hondalog.ui import adapters as ad
from hondalog.ui import main_window as mw
from hondalog.ui import wired
from hondalog.ui.backends import DemoTableModel


def key(win, k, mods=Qt.NoModifier, text=""):
    win.handle_key(QKeyEvent(QEvent.KeyPress, k, mods, text))


@pytest.fixture
def files(tmp_path):
    """A demo ROM, its definition as a TunerPro .xdf, one with a table less
    (.toml), one for another ROM size, and one with seven tables."""
    rom = tmp_path / "p28_demo.bin"
    rom.write_bytes(build_demo_rom())
    xdf = tmp_path / "demo.xdf"
    save_xdf(demo_definition(), xdf)
    two = demo_definition()
    two.tables = [t for t in two.tables if t.id != "target_lambda"]
    two.title = "two tables"
    two.save(tmp_path / "two.toml")
    big = demo_definition()
    big.rom_size = 0x10000
    big.save(tmp_path / "wrong_size.toml")
    many = demo_definition()
    fuel = next(t for t in many.tables if t.id == "fuel")
    many.tables += [dataclasses.replace(fuel, id=f"fuel_{k}", title=f"Fuel copy {k}") for k in range(4)]
    many.save(tmp_path / "many.toml")
    (tmp_path / "junk.xdf").write_text("<nope")
    return {"rom": rom, "xdf": xdf, "two": tmp_path / "two.toml", "wrong": tmp_path / "wrong_size.toml",
            "many": tmp_path / "many.toml", "junk": tmp_path / "junk.xdf", "dir": tmp_path}


# ---- the model ----

def test_rom_model_opens_again_with_another_rom_or_definition(files):
    tm = wired.RomTableModel(store_dir=files["dir"] / "roms")
    assert isinstance(tm, ad.TableModel) and tm.layout == 0 and tm.status()["loaded"] is False
    assert not tm.can_undo and not tm.can_redo and not tm.dirty and tm.default_save_path() is None

    # a definition for another ROM size: refused, the samples stay, the ROM waits for the right one
    assert "65536" in tm.open(files["rom"], files["wrong"]) and not tm.loaded and tm.layout == 0
    assert tm.rom_path == files["rom"] and tm.status()["rom"] == "p28_demo.bin" and tm.values("fl").shape == (20, 10)
    assert "junk.xdf" in tm.load_definition(files["junk"]) and not tm.loaded
    assert tm.load_definition(files["xdf"]) == "" and tm.loaded and tm.layout == 1      # ... and opens with it
    assert tm.definition_path == files["xdf"]
    assert [t.name for t in tm.tables()][:2] == ["Fuel (low cam)", "Ignition (low cam)"]
    assert tm.rpm_axis.tolist() == DEMO_RPM and tm.map_axis.tolist() == DEMO_MAP_KPA
    state = tm.status()
    assert state["loaded"] and state["rom"] == "p28_demo.bin" and not state["dirty"]
    assert state["checksum"] == "checksum not checked"      # an .xdf carries no checksum rule: said, not hidden
    assert tm.default_save_path().name == "p28_demo.tuned.bin"

    # the table list follows the definition
    assert len(tm.tables()) == 3
    assert tm.load_definition(files["two"]) == "" and tm.layout == 2 and len(tm.tables()) == 2
    assert tm.status()["definition"] == "two tables" and tm.status()["checksum"] == "checksum OK"
    assert "wrong_size.toml" not in tm.load_definition(files["wrong"]) and tm.layout == 2    # refused: nothing changes
    assert tm.loaded and len(tm.tables()) == 2 and tm.definition_path == files["two"]

    # undo and redo are the session's
    fuel = tm.values("fuel").copy()
    tm.adjust("fuel", [(3, 5)], 2)
    assert tm.dirty and tm.can_undo and not tm.can_redo and tm.status()["dirty"]
    assert "fixed when you save" in tm.status()["checksum"]
    assert tm.undo() and not tm.dirty and tm.can_redo
    np.testing.assert_allclose(tm.values("fuel"), fuel)
    assert tm.redo() and tm.dirty and not tm.redo()
    assert tm.values("fuel")[3, 5] > fuel[3, 5]

    # opening another ROM replaces the session; the old one stops reporting here
    old = tm.session
    other = files["dir"] / "other.bin"
    other.write_bytes(build_demo_rom(seed=7))
    assert tm.open(other) == "" and tm.layout == 3 and tm.session is not old and not tm.dirty
    rev = tm.revision
    old.table("fuel").add(None, 1.0)
    assert tm.revision == rev


def test_save_and_burn_image_go_through_the_rom_session(files):
    tm = wired.RomTableModel(store_dir=files["dir"] / "roms")
    with pytest.raises(ValueError, match="Open a ROM first"):
        tm.burn_image(files["dir"] / "chip.bin")
    assert tm.open(files["rom"]) == ""
    original = files["rom"].read_bytes()
    chip = files["dir"] / "chip.bin"
    text = tm.burn_image(chip)                              # untouched ROM: the checksum is right
    assert "65536 bytes" in text and "doubled" in text and chip.stat().st_size == 65536
    assert chip.read_bytes()[:0x8000] == original == chip.read_bytes()[0x8000:]

    tm.adjust("fuel", [(3, 5)], 2)
    chip.unlink()
    with pytest.raises(ValueError, match="Fix the checksum before burning"):
        tm.burn_image(chip)                                 # an edit made the checksum wrong: refused
    assert not chip.exists()                                # ... and nothing was written
    with pytest.raises(ValueError, match="never overwrites"):
        tm.save(files["rom"])                               # never onto the ROM that was opened
    assert files["rom"].read_bytes() == original
    with pytest.raises(ValueError, match="is the ROM that is open"):
        tm.burn_image(files["rom"])
    out = files["dir"] / "tuned.bin"
    text = tm.save(out)
    assert text.startswith("Saved tuned.bin, checksum fixed, snapshot ") and not tm.dirty
    assert tm.status()["checksum"] == "checksum OK"
    assert "sha256" in tm.burn_image(chip) and chip.read_bytes()[:0x8000] == out.read_bytes()
    assert len(list((files["dir"] / "roms" / "snapshots").rglob("*.bin"))) == 1
    assert len(list((files["dir"] / "roms" / "backups").glob("*p28_demo.bin"))) == 1    # the original, kept


# ---- the screen ----

@pytest.fixture
def tune(shell, app, tmp_path):
    """The Tune screen of the demo app on a RomTableModel (sample tables until a ROM is opened)."""
    session = shell.session
    session.tables = wired.RomTableModel(DemoTableModel(writable=False), store_dir=tmp_path / "roms")
    screen = shell.tune
    screen.tm = session.tables
    screen._layout = -1                                     # a new model: rebuild on the next frame
    shell.show_screen(mw.TUNE)
    spin(app, shell)
    return screen


def test_open_rom_and_load_definition_rebuild_the_table_list(tune, shell, app, files, settings):
    assert [i.id for i in tune.infos] == ["fl", "fh", "il", "ih"] and tune.grid.values.shape == (20, 10)
    assert tune.chip.open_btn.isEnabled() and tune.chip.def_btn.isEnabled()
    assert not tune.chip.save_btn.isEnabled() and not tune.chip.burn_btn.isEnabled()
    assert not tune.inc_btn.isEnabled() and "P28" in tune.chip.text

    assert tune.open_rom(files["rom"])                      # the demo definition until another is loaded
    spin(app, shell)
    assert [i.id for i in tune.infos] == ["fuel", "ignition", "target_lambda"]
    assert [o[0] for o in tune.seg.options] == ["fuel", "ignition", "target_lambda"] and tune.table.id == "fuel"
    assert tune.grid.values.shape == (len(DEMO_RPM), len(DEMO_MAP_KPA))
    assert tune.grid.rpm.tolist() == DEMO_RPM and tune.model.rpm.tolist() == DEMO_RPM
    assert (tune.model.nx, tune.model.ny) == (len(DEMO_RPM), len(DEMO_MAP_KPA)) and tune.scene.model is tune.model
    assert tune.tracer.shape == (len(DEMO_MAP_KPA), len(DEMO_RPM))
    assert "Opened p28_demo.bin" in tune.chip.text and "checksum OK" in tune.chip.text and not tune.chip.alert
    assert tune.inc_btn.isEnabled() and tune.chip.save_btn.isEnabled() and tune.chip.burn_btn.isEnabled()
    assert not tune.undo_btn.isEnabled() and not tune.redo_btn.isEnabled()
    assert settings.value("tune/rom") == str(files["rom"])
    assert tune.grid.geometry().bottom() < tune.dec_btn.geometry().top()       # the smaller grid: laid out again

    assert tune.load_definition(files["two"])               # a hondalog .toml: one table less
    spin(app, shell)
    assert [i.id for i in tune.infos] == ["fuel", "ignition"] and len(tune.seg.options) == 2
    assert "Definition two.toml: 2 tables" in tune.chip.text and settings.value("tune/definition") == str(files["two"])
    assert tune.load_definition(files["xdf"])               # a TunerPro .xdf: all three again
    assert len(tune.infos) == 3 and tune.seg.isVisible() and not tune.pick_btn.isVisible()

    assert not tune.load_definition(files["junk"])          # unreadable: said in the card, nothing changes
    assert tune.chip.alert and "junk.xdf" in tune.chip.text and len(tune.infos) == 3
    assert not tune.open_rom(files["dir"] / "missing.bin") and "missing.bin" in tune.chip.text
    assert tune.tm.loaded and len(tune.infos) == 3

    # more tables than a segment can name: one button, T and a menu instead
    assert tune.load_definition(files["many"])
    spin(app, shell)
    assert len(tune.infos) == 7 and tune.pick_btn.isVisible() and not tune.seg.isVisible()
    assert tune.pick_btn.text() == "Fuel (low cam)  (1 of 7)"
    key(shell, Qt.Key_T)
    assert tune.table.id == "ignition" and "(2 of 7)" in tune.pick_btn.text()


def test_unsaved_edits_are_asked_about_before_they_are_dropped(tune, shell, app, files):
    assert tune.open_rom(files["rom"])
    spin(app, shell)
    asked = []
    answer = [False]
    tune.confirm = lambda title, text: (asked.append(text), answer[0])[1]
    tune.grid.cur = tune.grid.anchor = (3, 5)
    key(shell, 0, text="=")
    spin(app, shell)
    assert tune.tm.dirty and "not saved" in tune.chip.text and "1 cells changed here" in tune.chip.text
    assert tune.undo_btn.isEnabled() and not tune.redo_btn.isEnabled()
    other = files["dir"] / "other.bin"
    other.write_bytes(build_demo_rom(seed=7))
    assert not tune.open_rom(other) and "drops them" in asked[-1] and tune.tm.rom_path == files["rom"]
    assert not tune.load_definition(files["two"]) and len(asked) == 2 and len(tune.infos) == 3
    answer[0] = True
    assert tune.open_rom(other) and tune.tm.rom_path == other and not tune.tm.dirty

    # undo / redo keys, the save and burn buttons: all the session's
    assert tune.grid.cur == (0, 0)                          # a new ROM: the selection starts over
    tune.grid.cur = tune.grid.anchor = (3, 5)
    key(shell, 0, text="=")
    value = float(tune.tm.values("fuel")[3, 5])
    key(shell, Qt.Key_Z, Qt.ControlModifier)
    spin(app, shell)
    assert tune.tm.values("fuel")[3, 5] < value and tune.redo_btn.isEnabled()
    key(shell, Qt.Key_Y, Qt.ControlModifier)
    assert tune.tm.values("fuel")[3, 5] == pytest.approx(value)
    key(shell, Qt.Key_Z, Qt.ControlModifier)
    key(shell, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)                 # Ctrl+Shift+Z is redo too
    assert tune.tm.values("fuel")[3, 5] == pytest.approx(value)
    chip = files["dir"] / "chip.bin"
    assert not tune.build_burn_image(chip) and tune.chip.alert and not chip.exists()
    assert "Not built" in tune.chip.text and "checksum" in tune.chip.text
    assert not tune.save(other) and "Not saved" in tune.chip.text and "never overwrites" in tune.chip.text
    assert tune.save(files["dir"] / "tuned.bin") and "checksum fixed" in tune.chip.text and not tune.chip.alert
    assert tune.build_burn_image(chip) and chip.stat().st_size == 65536 and "Burn image chip.bin" in tune.chip.text


def test_live_cell_and_trail_come_from_the_table_tracer(tune, shell, app, files):
    assert tune.open_rom(files["rom"])
    assert isinstance(tune.tracer, TableTracer) and tune.tracer.trail_seconds == 7.0
    bus = shell.session.bus
    now = bus.now()
    moves = [(1500.0, 35.0), (2100.0, 45.0), (2700.0, 55.0), (3300.0, 65.0)]
    for k, (rpm, kpa) in enumerate(moves):
        bus.publish({"rpm": rpm, "map": kpa, "stft": 2.0, "ltft": 1.0}, t=now + 0.1 * k)
        tune.refresh(now + 0.1 * k)
    t = now + 0.1 * 3
    pos = tune.tracer.cursor
    assert (pos.x, pos.y) == (3300.0, 65.0) and len(tune.tracer.trail(t)) == 4
    row, col = pos.nearest                                  # the tracer: rows are MAP, columns RPM
    assert tune.grid.live == (col, row)                     # the grid's rows are RPM
    assert tune.grid.rpm[tune.grid.live[0]] in (3000, 3600) and tune.grid.map[tune.grid.live[1]] in (60, 70)
    heat = tune.grid.trail
    assert heat.shape == tune.grid.values.shape and 0.2 <= heat.max() <= 1.0
    visited = {(int(r), int(c)) for r, c in zip(*np.nonzero(heat))}
    assert (DEMO_RPM.index(1200), DEMO_MAP_KPA.index(30)) in visited      # where it was 0.3 s ago
    assert (0, 0) not in visited

    # the 3D scene draws the tracer's cursor and trail; it keeps no history of its own here
    x, y, h, value = tune.model.at_index(pos.col, pos.row)
    assert tune.scene.live == (x, y, pytest.approx(h + 0.03)) and tune.scene.trail_n == 4
    assert tuple(tune.scene.trail[-1][:2]) == (pytest.approx(x), pytest.approx(y))
    # the live card: the table value interpolated with the tracer's weights
    values = tune.tm.values("fuel")
    expect = sum(w * float(values[c, r]) for r, c, w in pos.cells)
    assert sum(w for _, _, w in pos.cells) == pytest.approx(1.0)
    assert tune.live_card.value == f"{expect:.{tune.table.decimals}f}"
    assert tune.live_card.name == "3300 rpm × 65 kPa" and tune.live_card.foot == "trim +3.0 %"
    spin(app, shell)
    shell.tune.grab()

    # off the table the tracer holds the edge cell, and the card says so
    bus.publish({"rpm": 7400.0, "map": 104.0}, t=now + 0.5)
    tune.refresh(now + 0.5)
    assert tune.tracer.cursor.clamped and tune.grid.live == (len(DEMO_RPM) - 1, len(DEMO_MAP_KPA) - 1)
    assert tune.live_card.foot.startswith("at edge · trim")

    # the trail fades and is gone after its seven seconds
    bus.publish({"rpm": 7400.0, "map": 104.0}, t=now + 9.0)
    tune.refresh(now + 9.0)
    assert len(tune.tracer.trail(now + 9.0)) == 1 and tune.scene.trail_n == 1

    # no data: no live cell, no point, no trail
    state = bus.channel("rpm")
    tune._gen = -1
    tune.refresh(now + 9.0 + state.timeout + 5.0)
    assert tune.grid.live is None and tune.grid.trail is None and tune.scene.live is None
    assert tune.live_card.value == "---" and not tune.tracer.cursor.valid

    # another table set: a tracer on the new axes
    old = tune.tracer
    assert tune.load_definition(files["two"]) and tune.tracer is not old


def test_demo_tables_keep_working_without_a_rom(shell, app):
    """--demo: the writable sample tables, no ROM actions, the tracer on the sample grid."""
    tune = shell.tune
    shell.show_screen(mw.TUNE)
    spin(app, shell)
    assert isinstance(shell.session.tables, DemoTableModel) and isinstance(tune.tracer, TableTracer)
    assert tune.tracer.shape == (10, 20) and tune.grid.live is not None and tune.scene.live is not None
    assert not tune.chip.open_btn.isEnabled() and not tune.chip.burn_btn.isEnabled()
    assert tune.chip.save_btn.isEnabled() and tune.inc_btn.isEnabled() and not tune.redo_btn.isEnabled()
    assert not tune.open_rom("x.bin") and not tune.load_definition("x.xdf") and not tune.build_burn_image("x")
