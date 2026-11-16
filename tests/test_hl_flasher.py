import stat
import sys
import threading

import pytest

from hondalog.handheld import flasher


def image(tmp_path, ok=True):
    data = bytearray(b"\xff" * 0x9000)
    data[0] = 0xE9
    if ok:
        data[0x8000:0x8002] = b"\xaa\x50"
    p = tmp_path / "img.bin"
    p.write_bytes(bytes(data))
    return p


def test_check_image(tmp_path):
    assert flasher.check_image(image(tmp_path))
    with pytest.raises(flasher.FlashError):
        flasher.check_image(image(tmp_path, ok=False))
    bad = tmp_path / "x.bin"
    bad.write_bytes(b"hello")
    with pytest.raises(flasher.FlashError):
        flasher.check_image(bad)
    with pytest.raises(flasher.FlashError):
        flasher.check_image(tmp_path / "missing.bin")


def test_parse_line_v5_and_v4():
    st: dict = {"total": 1000000}
    p = flasher.parse_line("Writing at 0x00010000 [=====>    ] 35.1% 0:05 286.7kB/815.1kB", st)
    assert p and p.stage == "Writing firmware" and abs(p.pct - 35.1) < 0.01 and p.written == 351000
    p = flasher.parse_line("Writing at 0x00010000... (12 %)", {"total": 100})
    assert p and p.pct == 12
    p = flasher.parse_line("ERROR: A fatal error occurred: Failed to connect", st)
    assert p and p.stage == "Failed" and st["fatal"]
    assert flasher.parse_line("", st) is None


def fake(tmp_path, body):
    script = tmp_path / "fake_esptool.py"
    script.write_text(body)
    return [sys.executable, str(script)]


GOOD = """
import sys, time
print("esptool v5.0", flush=True)
print("Chip is ESP32-S3 (QFN56)", flush=True)
for i in (10, 50, 100):
    print(f"Writing at 0x00000000 [===] {i}.0% 0:01 1.0kB/2.0kB", flush=True)
print("Hard resetting via RTS pin...", flush=True)
"""


def test_flash_success(tmp_path):
    seen = []
    flasher.flash(image(tmp_path), "/dev/null", on_progress=seen.append, command=fake(tmp_path, GOOD))
    assert any(p.pct == 100 for p in seen if p.pct is not None)


def test_flash_failure_is_friendly(tmp_path):
    bad = "import sys\nprint('ERROR: A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.')\nsys.exit(2)\n"
    with pytest.raises(flasher.FlashError) as e:
        flasher.flash(image(tmp_path), "/dev/null", command=fake(tmp_path, bad))
    assert "BOOT" in str(e.value) or "bootloader" in str(e.value).lower()


def test_flash_wrong_chip(tmp_path):
    bad = ("import sys\nprint('ERROR: A fatal error occurred: This chip is ESP32 not ESP32-S3. "
           "Wrong --chip argument?')\nsys.exit(2)\n")
    with pytest.raises(flasher.FlashError) as e:
        flasher.flash(image(tmp_path), "/dev/null", command=fake(tmp_path, bad))
    assert "chip" in str(e.value).lower()


def test_flash_cancel(tmp_path):
    slow = "import time\nprint('Chip is ESP32-S3', flush=True)\ntime.sleep(30)\n"
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    with pytest.raises(flasher.FlashError):
        flasher.flash(image(tmp_path), "/dev/null", cancel=ev, command=fake(tmp_path, slow))
