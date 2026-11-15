import subprocess
import sys
from pathlib import Path

import pytest

from hondalog.tuning.cli import main
from hondalog.tuning.demo import build_demo_rom

ROOT = Path(__file__).parent.parent


@pytest.fixture
def rom(tmp_path, monkeypatch):
    monkeypatch.setenv("HONDALOG_ROM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("HONDALOG_MINIPRO", raising=False)
    path = tmp_path / "demo.bin"
    assert main(["demo-rom", str(path)]) == 0
    return path


def run(capsys, *args):
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_demo_rom_and_info(rom, capsys):
    assert rom.read_bytes() == build_demo_rom()
    assert run(capsys, "demo-rom", rom)[0] == 2                    # will not overwrite
    code, out, _ = run(capsys, "info", rom, "-d", "demo")
    assert code == 0 and "32 KB image" in out and "checksum: ok" in out
    assert "(rule unverified)" in out and "4 tables" in out
    code, out, _ = run(capsys, "info", rom)
    assert code == 0 and "checksum" not in out


def test_list_and_show(rom, capsys):
    code, out, _ = run(capsys, "list", "-d", "demo")
    assert code == 0 and "[Fuel]" in out and "fuel" in out and "8x10" in out
    code, out, _ = run(capsys, "show", rom, "-d", "demo", "ignition")
    lines = out.splitlines()
    assert code == 0 and "deg BTDC" in lines[0] and lines[1].split()[:2] == ["800", "1200"]
    assert lines[2].split()[0] == "20" and len(lines) == 10
    code, out, _ = run(capsys, "show", rom, "-d", "demo", "fuel", "--raw")
    assert all(cell.isdigit() for cell in out.splitlines()[2].split())
    code, _, err = run(capsys, "show", rom, "-d", "demo", "nope")
    assert code == 2 and "no item named 'nope'" in err
    code, _, err = run(capsys, "list")
    assert code == 2 and "-d FILE" in err


def test_convert_both_ways(rom, capsys, tmp_path):
    from hondalog.tuning.demo import DEMO_DEFINITION
    xdf, toml = tmp_path / "d.xdf", tmp_path / "d.toml"
    assert run(capsys, "convert", DEMO_DEFINITION, xdf)[0] == 0
    assert xdf.read_text().startswith("<!-- Written by hondalog -->")
    code, out, _ = run(capsys, "convert", xdf, toml)
    assert code == 0 and "4 tables" in out
    assert run(capsys, "convert", xdf, toml)[0] == 2
    assert run(capsys, "convert", xdf, toml, "--force")[0] == 0
    code, out, _ = run(capsys, "show", rom, "-d", toml, "Fuel (low cam)")
    assert code == 0 and "ms" in out


def test_checksum_diff_and_burn_refusal(rom, capsys, tmp_path):
    bad = tmp_path / "bad.bin"
    data = bytearray(rom.read_bytes())
    data[0x4020] += 5
    bad.write_bytes(data)
    code, out, _ = run(capsys, "checksum", bad, "-d", "demo")
    assert code == 1 and out.startswith("bad:")
    assert run(capsys, "info", bad, "-d", "demo")[0] == 1
    code, _, err = run(capsys, "burn-image", bad, "-d", "demo", tmp_path / "chip.bin")
    assert code == 3 and "refused" in err and not (tmp_path / "chip.bin").exists()
    code, _, err = run(capsys, "burn", bad, "-d", "demo", "--dry-run")
    assert code == 3

    fixed = tmp_path / "fixed.bin"
    code, out, _ = run(capsys, "checksum", bad, "-d", "demo", "--fix", fixed)
    assert code == 0 and "is correct" in out
    assert run(capsys, "checksum", fixed, "-d", "demo")[0] == 0

    code, out, _ = run(capsys, "diff", rom, fixed, "-d", "demo", "--bytes", "--table", "fuel")
    assert code == 1 and "Fuel (low cam): 1 of 80 cells" in out
    assert "0x4020-0x4020" in out and "+0.32" in out
    code, out, _ = run(capsys, "diff", rom, rom, "--table", "fuel")
    assert code == 0 and "identical" in out and "no difference" in out


def test_burn_image_and_dry_run(rom, capsys, tmp_path):
    chip = tmp_path / "chip.bin"
    code, out, err = run(capsys, "burn-image", rom, "-d", "demo", chip)
    assert code == 0 and "65536 bytes, doubled" in out and "unverified" in err
    assert chip.read_bytes() == rom.read_bytes() * 2
    assert run(capsys, "burn-image", rom, "-d", "demo", chip)[0] == 2
    upper = tmp_path / "upper.bin"
    run(capsys, "burn-image", rom, "-d", "demo", upper, "--layout", "upper")
    assert upper.read_bytes() == b"\xff" * 0x8000 + rom.read_bytes()
    code, out, _ = run(capsys, "info", chip, "-d", "demo")
    assert code == 0 and "repeated 2 times" in out

    code, out, _ = run(capsys, "burn", rom, "-d", "demo", "--dry-run")
    assert code == 0 and "minipro -p SST27SF512 -E" in out and "dry run" in out
    code, _, err = run(capsys, "burn", rom, "-d", "demo")
    assert code == 2 and "minipro is not installed" in err
    assert not (tmp_path / "store" / "burns.jsonl").exists()


def test_errors_are_one_line(rom, capsys, tmp_path):
    code, _, err = run(capsys, "info", tmp_path / "missing.bin")
    assert code == 2 and err.startswith("error: cannot read") and "Traceback" not in err
    (tmp_path / "odd.bin").write_bytes(b"x" * 1000)
    code, _, err = run(capsys, "info", tmp_path / "odd.bin")
    assert code == 2 and "expected 32768" in err
    (tmp_path / "bad.xdf").write_text("<nope/>")
    assert run(capsys, "list", "-d", tmp_path / "bad.xdf")[0] == 2


def test_module_and_launcher_entry_points(rom):
    out = subprocess.run([sys.executable, "-m", "hondalog.tuning", "info", str(rom),
                          "-d", "demo"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and "checksum: ok" in out.stdout
    out = subprocess.run(["/bin/sh", str(ROOT / "bin" / "hondalog-tune"), "--help"],
                         capture_output=True, text=True,
                         env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"})
    assert out.returncode == 0 and "burn-image" in out.stdout
