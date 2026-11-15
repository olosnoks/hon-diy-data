import os
import stat

import numpy as np
import pytest

from hondalog.tuning import BurnRefused, RomError, RomSession
from hondalog.tuning.demo import build_demo_rom, demo_definition
from hondalog.tuning.session import rom_dir
from tuning_helpers import demo_session

ROM = build_demo_rom()


@pytest.fixture
def opened(tmp_path):
    src = tmp_path / "stock.bin"
    src.write_bytes(ROM)
    return RomSession.open(src, demo_definition(), store_dir=tmp_path / "store")


def test_open_makes_a_read_only_backup_once(opened, tmp_path):
    backup = opened.backup_path
    assert backup.read_bytes() == ROM and backup.parent.name == "backups"
    assert not stat.S_IMODE(backup.stat().st_mode) & 0o222
    again = RomSession.open(tmp_path / "stock.bin", demo_definition(),
                            store_dir=tmp_path / "store")
    assert again.backup_path == backup
    assert len(list(backup.parent.iterdir())) == 1


def test_backup_keeps_the_container_as_it_was(tmp_path):
    src = tmp_path / "chip.bin"
    src.write_bytes(ROM + ROM)
    s = RomSession.open(src, demo_definition(), store_dir=tmp_path / "store")
    assert s.backup_path.read_bytes() == ROM + ROM and bytes(s.data) == ROM
    off = RomSession.open(src, demo_definition(), store_dir=tmp_path / "s2", backup=False)
    assert off.backup_path is None and not (tmp_path / "s2").exists()


def test_original_is_never_written(opened, tmp_path):
    src = tmp_path / "stock.bin"
    opened.table("fuel").add(None, 1)
    with pytest.raises(RomError, match="never overwrites"):
        opened.save(src)
    link = tmp_path / "alias.bin"
    os.symlink(src, link)
    with pytest.raises(RomError, match="never overwrites"):
        opened.save(link)
    assert src.read_bytes() == ROM
    result = opened.save()
    assert result.path == tmp_path / "stock.tuned.bin" and src.read_bytes() == ROM
    assert result.path.read_bytes() == bytes(opened.data)


def test_save_fixes_checksum_and_snapshots(opened):
    opened.table("fuel").percent(None, 3)
    assert opened.checksum().status == "bad" and opened.dirty
    first = opened.save()
    assert first.checksum_fixed and first.checksum.status == "ok" and not opened.dirty
    assert first.snapshot.read_bytes() == bytes(opened.data)
    opened.set_scalar("rev_limit", 7000)
    second = opened.save()                       # same target as before
    assert second.path == first.path
    assert [p.name for p in opened.snapshots()] == sorted(
        [first.snapshot.name, second.snapshot.name])
    assert opened.history[-1] == "Fix checksum"
    kept = opened.save(fix_checksum=False, snapshot=False, container="doubled")
    assert kept.snapshot is None and len(kept.path.read_bytes()) == 0x10000


def test_restore_snapshot_is_undoable(opened):
    opened.table("fuel").add(None, 1)
    snap = opened.snapshot("before big change")
    assert "before-big-change" in snap.name
    after_first = bytes(opened.data)
    opened.table("ignition").add(None, 2)
    assert opened.restore(snap)
    assert bytes(opened.data) == after_first
    opened.undo()
    assert bytes(opened.data) != after_first
    assert opened.restore(opened.backup_path, "Back to stock")
    assert bytes(opened.data) == ROM and opened.history[-1] == "Back to stock"
    assert not opened.restore(ROM)               # already there
    with pytest.raises(RomError, match="different size"):
        opened.restore(b"abc")


def test_undo_redo_and_history(tmp_path):
    s = demo_session(tmp_path)
    assert not s.can_undo and s.undo() is None and s.redo() is None
    fuel = s.table("fuel")
    states = [bytes(s.data)]
    fuel.set_cell(0, 0, 5.0)
    states.append(bytes(s.data))
    s.set_scalar("rev_limit", 7200)
    states.append(bytes(s.data))
    s.set_flag("closed_loop", False)
    states.append(bytes(s.data))
    assert s.history == ["Fuel (low cam): set", "Set Rev limit",
                         "Clear Closed loop enabled"]
    for want in reversed(states[:-1]):
        s.undo()
        assert bytes(s.data) == want
    assert not s.modified and s.redo_history[0] == "Fuel (low cam): set"
    for want in states[1:]:
        s.redo()
        assert bytes(s.data) == want
    s.undo()
    s.set_scalar("idle_target", 900)             # a new edit drops the redo branch
    assert not s.can_redo


def test_unlimited_undo(tmp_path):
    s = demo_session(tmp_path)
    fuel = s.table("fuel")
    for i in range(500):
        fuel.set_cell(i % 8, i % 10, 1.0 + (i % 50) * 0.064)
    n = len(s.history)
    assert n > 400
    for _ in range(n):
        s.undo()
    assert bytes(s.data) == ROM


def test_no_op_edits_leave_no_history(tmp_path):
    s = demo_session(tmp_path)
    assert not s.set_scalar("rev_limit", 6800)
    assert not s.set_flag("closed_loop", True)
    assert not s.apply("nothing", [(0, ROM[:4])])
    assert not s.history and not s.fix_checksum()
    with pytest.raises(RomError, match="outside the ROM"):
        s.apply("bad", [(0x7FFF, b"ab")])


def test_group_is_one_undo_step(tmp_path):
    s = demo_session(tmp_path)
    with s.group("Lean out and retard"):
        s.table("fuel").percent(None, -2)
        with s.group("inner"):
            s.table("ignition").add(None, -1)
    assert s.history == ["Lean out and retard"]
    s.undo()
    assert bytes(s.data) == ROM
    with s.group("empty"):
        pass
    assert not s.history


def test_listeners_see_edits_undo_and_redo(tmp_path):
    s = demo_session(tmp_path)
    seen = []
    stop = s.on_change(lambda ranges, why: seen.append((why, ranges)))
    s.set_scalar("idle_target", 800)
    s.undo()
    s.redo()
    assert [w for w, _ in seen] == ["edit", "undo", "redo"]
    assert seen[0][1] == [(0x4162, 0x4163)]
    stop()
    s.undo()
    assert len(seen) == 3


def test_scalars_and_flags(tmp_path):
    s = demo_session(tmp_path)
    assert s.scalar("idle_target") == 750 and s.scalar("idle_target", "raw") == 75
    s.set_scalar("idle_target", 80, view="raw")
    assert s.scalar("idle_target") == 800
    s.set_flag("knock_retard", False)
    assert not s.flag("knock_retard") and s.flag("closed_loop")
    assert s.data[0x4163] == 0x01


def test_changed_bytes_and_revert(tmp_path):
    s = demo_session(tmp_path)
    s.table("fuel").set_cell(2, 3, 9.0)
    s.set_scalar("rev_limit", 7000)
    changed = np.nonzero(s.changed_bytes())[0].tolist()
    assert 0x4020 + 2 * 10 + 3 in changed and set(changed) <= {0x4037, 0x4160, 0x4161}
    assert s.revert_item("fuel") and s.scalar("rev_limit") == 7000
    assert s.revert_all() and not s.modified
    assert s.history[-1] == "Revert to loaded ROM"


def test_patches(tmp_path):
    s = demo_session(tmp_path)
    assert s.patch_state("skip_checksum") == "removed"
    assert s.apply_patch("skip_checksum") and s.patch_state("skip_checksum") == "applied"
    assert not s.apply_patch("skip_checksum")
    assert s.remove_patch("skip_checksum") and s.data[0x100:0x102] == bytes.fromhex("c3f0")
    s.apply("scribble", [(0x100, b"\x11\x22")])
    assert s.patch_state("skip_checksum") == "unknown"
    with pytest.raises(RomError, match="different base"):
        s.apply_patch("skip_checksum")
    assert s.apply_patch("skip_checksum", force=True)
    s.definition.patches[0].entries[0].base = None
    with pytest.raises(RomError, match="does not record"):
        s.remove_patch("skip_checksum")


def test_burn_image_from_session(tmp_path):
    s = demo_session(tmp_path)
    s.table("fuel").percent(None, 2)
    with pytest.raises(BurnRefused):
        s.burn_image()
    s.fix_checksum()
    image = s.burn_image()
    assert image.data == bytes(s.data) * 2
    assert s.diff().table("fuel").cells_changed > 0


def test_size_mismatch_and_warnings(tmp_path):
    d = demo_definition()
    d.rom_size = 0x10000
    from hondalog.tuning import RomImage
    with pytest.raises(RomError, match="is for 65536 bytes"):
        RomSession(RomImage.from_bytes(ROM), d)
    bad = bytearray(ROM)
    bad[0x10] = 0
    s = RomSession(RomImage.from_bytes(bytes(bad)), demo_definition(), store_dir=tmp_path)
    assert any("signature" in w for w in s.warnings)
    assert s.default_save_path() == tmp_path / "rom.tuned.bin"


def test_rom_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HONDALOG_ROM_DIR", str(tmp_path / "r"))
    assert rom_dir() == tmp_path / "r"
    monkeypatch.delenv("HONDALOG_ROM_DIR")
    assert rom_dir().name == "roms"
