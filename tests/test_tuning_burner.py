import json
import stat
import subprocess
import sys

import pytest

from hondalog.tuning import BurnerError, Minipro, build_burn_image, read_burn_log
from hondalog.tuning.burner import INSTALL_HINT, find_minipro
from hondalog.tuning.demo import build_demo_rom, demo_definition

IMAGE = build_burn_image(build_demo_rom(), demo_definition())

# A stand-in for the real tool: keeps the "chip" in a file next to itself and
# understands the options hondalog uses. Behaviour switches come from a mode
# file so each test can make a different step fail.
FAKE = r'''#!%s
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
chip, mode_file, log = (os.path.join(here, n) for n in ("chip.bin", "mode", "calls.log"))
mode = open(mode_file).read().strip() if os.path.exists(mode_file) else ""
args = sys.argv[1:]
open(log, "a").write(" ".join(args) + "\n")
SIZE = 65536
def fail(msg, code=1):
    sys.stderr.write(msg + "\n"); sys.exit(code)
if args == ["-V"]:
    sys.stderr.write("minipro version 0.7.2     A free and open TL866 series programmer\n"); sys.exit(0)
if args == ["-k"]:
    if mode == "no-programmer": fail("[No programmer found]")
    sys.stderr.write("TL866II+: 04.2.131\n"); sys.exit(0)
if args[:1] == ["-L"]:
    if mode != "unknown-chip":
        names = ["SST27SF512@DIP28", "SST27SF512@PLCC32"] if mode != "exact" else ["SST27SF512"]
        print("\n".join(n for n in names if args[1].upper() in n))
    sys.exit(0)
if args[:1] != ["-p"]: fail("usage")
op = [a for a in args[2:] if a != "-y"]
if op == ["-E"]:
    if mode == "erase-fails": fail("Erase failed")
    open(chip, "wb").write(b"\xff" * SIZE)
elif op == ["-b"]:
    data = open(chip, "rb").read() if os.path.exists(chip) else b""
    if mode == "not-blank" or data != b"\xff" * SIZE: fail("Chip is not blank")
elif op[0] == "-w":
    data = open(op[1], "rb").read()
    if len(data) != SIZE: fail("Incorrect file size")
    if mode == "write-fails": fail("Write failed")
    if "-e" not in op: pass
    if mode == "bad-bit": data = data[:100] + bytes([data[100] ^ 1]) + data[101:]
    open(chip, "wb").write(data)
elif op[0] == "-m":
    if mode == "verify-fails" or open(chip, "rb").read() != open(op[1], "rb").read():
        fail("Verification failed at address 0x0064")
elif op[0] == "-r":
    if mode == "read-fails": fail("Read failed")
    open(op[1], "wb").write(open(chip, "rb").read())
else:
    fail("unknown op")
sys.stderr.write("OK\n")
'''


@pytest.fixture
def fake(tmp_path):
    exe = tmp_path / "minipro"
    exe.write_text(FAKE % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)

    def set_mode(mode):
        (tmp_path / "mode").write_text(mode)

    def calls():
        log = tmp_path / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    return exe, set_mode, calls


def test_find_minipro(fake, monkeypatch, tmp_path):
    exe, _, _ = fake
    assert find_minipro(str(exe)) == str(exe)
    assert find_minipro(str(tmp_path / "nope")) is None
    monkeypatch.setenv("HONDALOG_MINIPRO", "/opt/x/minipro")
    assert find_minipro() == "/opt/x/minipro"
    monkeypatch.delenv("HONDALOG_MINIPRO")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_minipro() == str(exe)


def test_missing_minipro_gives_install_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("HONDALOG_MINIPRO", raising=False)
    burner = Minipro()
    assert not burner.available
    with pytest.raises(BurnerError, match="apt install minipro"):
        burner.burn(IMAGE)
    assert INSTALL_HINT in str(pytest.raises(BurnerError, burner.version).value)


def test_dry_run_needs_nothing_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    result = Minipro().burn(IMAGE, dry_run=True)
    assert result.ok and result.dry_run and all(s.skipped for s in result.steps)
    assert result.commands() == [
        "minipro -p SST27SF512 -E", "minipro -p SST27SF512 -b",
        "minipro -p SST27SF512 -w '<image.bin>' -e",
        "minipro -p SST27SF512 -m '<image.bin>'",
        "minipro -p SST27SF512 -r '<readback.bin>'"]
    assert result.image_sha256 == IMAGE.image_sha256 and result.rom_sha256 == IMAGE.rom_sha256
    assert "nothing was sent" in result.message


def test_queries(fake):
    exe, set_mode, _ = fake
    burner = Minipro(str(exe))
    assert burner.available and burner.version().startswith("minipro version 0.7.2")
    assert burner.programmer() == "TL866II+: 04.2.131"
    assert burner.matching_devices() == ["SST27SF512@DIP28", "SST27SF512@PLCC32"]
    assert burner.resolve_device() == "SST27SF512@DIP28"
    set_mode("exact")
    assert burner.resolve_device() == "SST27SF512"
    set_mode("unknown-chip")
    with pytest.raises(BurnerError, match="does not list a device"):
        burner.resolve_device()
    set_mode("no-programmer")
    with pytest.raises(BurnerError, match="no programmer found"):
        burner.programmer()


def test_full_burn(fake, tmp_path):
    exe, _, calls = fake
    seen = []
    log = tmp_path / "store" / "burns.jsonl"
    result = Minipro(str(exe)).burn(IMAGE, progress=lambda *a: seen.append(a),
                                    log_path=log)
    assert result.ok and result.message == "burned and verified"
    assert result.device == "SST27SF512@DIP28"
    assert [s.name for s in result.steps] == ["erase", "blank_check", "write", "verify",
                                              "read_back", "compare"]
    assert all(s.ok and not s.skipped for s in result.steps)
    assert result.readback_sha256 == IMAGE.image_sha256
    chip = (tmp_path / "chip.bin").read_bytes()
    assert chip == IMAGE.data and chip[0x8000:] == build_demo_rom()
    ops = [c.split()[2] for c in calls() if c.startswith("-p")]
    assert ops == ["-E", "-b", "-w", "-m", "-r"]
    assert seen[0] == ("erase", 0, 6) and seen[-1] == ("done", 6, 6)
    record, = read_burn_log(log)
    assert record["ok"] and record["rom_sha256"] == IMAGE.rom_sha256
    assert record["image_sha256"] == IMAGE.image_sha256 and record["started"]
    assert read_burn_log(tmp_path / "none.jsonl") == []


def test_options_change_the_plan(fake):
    exe, _, calls = fake
    burner = Minipro(str(exe), ignore_id_error=True)
    result = burner.burn(IMAGE.data, erase=False, blank_check=False, verify=False,
                         read_back=False)
    assert result.ok and result.message == "burned (not verified)" and result.rom_sha256 == ""
    assert [c for c in calls() if c.startswith("-p")][0].split()[2:4] == ["-y", "-w"]
    assert "-e" not in calls()[-1].split()            # minipro does its own erase


@pytest.mark.parametrize("mode,step,text", [
    ("erase-fails", "erase", "Erasing the chip failed"),
    ("not-blank", "blank_check", "not blank"),
    ("write-fails", "write", "Writing failed"),
    ("bad-bit", "verify", "Do not put it in the ECU"),
    ("read-fails", "read_back", "Reading the chip back failed"),
])
def test_failures_stop_the_sequence(fake, mode, step, text):
    exe, set_mode, _ = fake
    set_mode(mode)
    result = Minipro(str(exe)).burn(IMAGE)
    assert not result.ok and text in result.message
    failed = next(s for s in result.steps if not s.ok and not s.skipped)
    assert failed.name == step and failed.returncode != 0
    after = result.steps[result.steps.index(failed) + 1:]
    assert all(s.skipped for s in after)


def test_read_back_compare_catches_what_verify_missed(fake):
    exe, set_mode, _ = fake
    set_mode("bad-bit")
    result = Minipro(str(exe)).burn(IMAGE, verify=False)
    assert not result.ok and result.steps[-1].name == "compare"
    assert "1 bytes differ" in result.steps[-1].output
    assert "Do not put this chip in the ECU" in result.message


def test_wrong_size_refused_before_anything_runs(fake):
    exe, _, calls = fake
    with pytest.raises(BurnerError, match="the chip holds 65536"):
        Minipro(str(exe)).burn(build_demo_rom())
    assert calls() == []


def test_read_chip(fake, tmp_path):
    exe, set_mode, _ = fake
    (tmp_path / "chip.bin").write_bytes(IMAGE.data)
    assert Minipro(str(exe)).read_chip() == IMAGE.data
    set_mode("read-fails")
    with pytest.raises(BurnerError, match="reading the chip failed"):
        Minipro(str(exe)).read_chip()


def test_runner_errors_become_burner_errors():
    def timeout(cmd, t):
        raise subprocess.TimeoutExpired(cmd, t)

    def gone(cmd, t):
        raise FileNotFoundError(cmd[0])

    def denied(cmd, t):
        raise PermissionError("no")

    with pytest.raises(BurnerError, match="did not finish within 5 s"):
        Minipro(runner=timeout, timeout=5).version()
    with pytest.raises(BurnerError, match="not installed"):
        Minipro(runner=gone).version()
    with pytest.raises(BurnerError, match="cannot run minipro"):
        Minipro(runner=denied).version()
    silent = Minipro(runner=lambda c, t: subprocess.CompletedProcess(c, 0, "", ""))
    assert silent.version() == "(no version text)" and silent.available


def test_burn_log_skips_garbage(tmp_path):
    log = tmp_path / "b.jsonl"
    log.write_text('{"ok": true}\nnot json\n')
    assert read_burn_log(log) == [{"ok": True}]
    assert json.loads(log.read_text().splitlines()[0])["ok"]
