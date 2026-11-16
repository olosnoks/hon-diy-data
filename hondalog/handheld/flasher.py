"""Install firmware on the handheld (an ESP32-S3 CYD) with the ``esptool`` package:
the merged image (bootloader, partition table, app: ``idf.py merge_bin`` or
``scripts/build-firmware.sh``'s output) goes in at 0x0, the same as
``esptool.py --chip esp32s3 write_flash 0x0 image.bin``.

``flash()`` runs esptool as a subprocess and turns its text into progress
callbacks, so the GUI can show a bar and the command line a percentage. The
chip is checked first (esptool is told ``--chip esp32s3``: another chip is
refused before anything is erased). The caller releases the serial port
before and opens it again after.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

CHIP = "esp32s3"
CHIP_NAME = "ESP32-S3"
DEFAULT_BAUD = 460800
BAUDS = (115200, 230400, 460800, 921600)

# esptool 5 prints "Writing at 0x00010000 ━━━━━━ 35.1% 0:05 286.7kB/815.1kB" (one line per update when it is
# not a terminal); esptool 4 printed "Writing at 0x00010000... (35 %)" or "[===>   ]  35.1% 286720/815104 bytes".
PROGRESS_RE = re.compile(r"Writing at 0x([0-9a-fA-F]+)")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
SIZES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kB|KB|MB|GB|B)?\s*/\s*(\d+(?:\.\d+)?)\s*(kB|KB|MB|GB|B|bytes)?")
UNITS = {"B": 1, "bytes": 1, "kB": 1024, "KB": 1024, "MB": 1048576, "GB": 1073741824}
STAGES = (
    (re.compile(r"Connecting"), "Connecting to the board"),
    (re.compile(r"Chip is|Chip type"), "Chip found"),
    (re.compile(r"Uploading stub|Running stub|Stub running"), "Starting the flasher"),
    (re.compile(r"Changing baud rate|Changed\."), "Changing speed"),
    (re.compile(r"Configuring flash size|Flash will be erased"), "Preparing the flash"),
    (re.compile(r"Compressed \d+ bytes"), "Erasing and writing"),
    (re.compile(r"Hash of data verified"), "Verified"),
    (re.compile(r"Hard resetting|Staying in the bootloader"), "Resetting the board"),
)
FATAL_RE = re.compile(r"(A fatal error occurred: .*)")


class FlashError(Exception):
    pass


class Progress:
    """One update for the GUI: a stage, a percentage (None while there is none), bytes written and total, text."""

    def __init__(self, stage: str, pct: float | None = None, written: int = 0, total: int = 0, text: str = "",
                 elapsed: float = 0.0):
        self.stage, self.pct, self.written, self.total, self.text, self.elapsed = stage, pct, written, total, text, elapsed

    @property
    def eta(self) -> float | None:
        if self.pct is None or self.pct <= 0 or self.pct >= 100 or self.elapsed < 2:
            return None
        return self.elapsed * (100 - self.pct) / self.pct

    def __repr__(self) -> str:
        return f"Progress({self.stage!r}, {self.pct}, {self.written}/{self.total})"


def esptool_command() -> list[str] | None:
    """How to run esptool: this Python's module, or None if the package is not installed."""
    env = os.environ.get("HONDALOG_ESPTOOL")
    if env:
        return env.split()
    try:
        import importlib.util
        if importlib.util.find_spec("esptool") is not None:
            return [sys.executable, "-m", "esptool"]
    except (ImportError, ValueError):
        pass
    return None


def check_image(path: str | Path) -> bytes:
    """The image's bytes, if it looks like a merged image for this chip."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise FlashError(f"cannot read {p}: {exc}") from exc
    if len(data) < 0x9000 or data[0] != 0xE9:
        raise FlashError(f"{p.name} is not an ESP32 firmware image")
    if data[0x8000:0x8002] != b"\xaa\x50":
        raise FlashError(f"{p.name} has no partition table at 0x8000: it is an app-only image. Use the merged image "
                         "(scripts/build-firmware.sh handheld, or idf.py merge_bin), which is written at 0x0.")
    if len(data) > 16 * 1024 * 1024:
        raise FlashError(f"{p.name} is {len(data) // 1048576} MB: more than the board's flash")
    return data


def parse_line(line: str, state: dict) -> Progress | None:
    """One line (or one carriage-return update) of esptool's output -> a Progress, or None."""
    line = line.strip()
    if not line:
        return None
    if PROGRESS_RE.search(line):
        pm = PCT_RE.search(line)
        if pm:
            pct = min(100.0, float(pm.group(1)))
            total = state.get("total", 0)            # the image's own size: esptool's byte counts are rounded
            written = int(total * pct / 100)
            if not total:
                sm = SIZES_RE.search(line[pm.end():])
                if sm:
                    u2 = UNITS.get(sm.group(4) or sm.group(2) or "B", 1)
                    u1 = UNITS.get(sm.group(2) or sm.group(4) or "B", 1)
                    written, total = int(float(sm.group(1)) * u1), int(float(sm.group(3)) * u2)
                    state["total"] = total
            return Progress("Writing firmware", pct, written, total, line)
        return Progress("Writing firmware", None, 0, state.get("total", 0), line)
    f = FATAL_RE.search(line)
    if f:
        state["fatal"] = f.group(1)
        return Progress("Failed", None, 0, 0, f.group(1))
    c = re.search(r"Chip is ([A-Za-z0-9\-]+)", line) or re.search(r"Chip type:\s+([A-Za-z0-9\-]+)", line)
    if c:
        state["chip"] = c.group(1)
    for rx, stage in STAGES:
        if rx.search(line):
            return Progress(stage, None, 0, state.get("total", 0), line)
    return None


def flash(image: str | Path, port: str, baud: int = DEFAULT_BAUD, on_progress: Callable[[Progress], None] | None = None,
          on_line: Callable[[str], None] | None = None, cancel: threading.Event | None = None,
          command: list[str] | None = None, address: int = 0) -> list[str]:
    """Write the merged image at ``address`` (0x0). Raises ``FlashError`` with something a person can act on.
    Returns esptool's output lines."""
    data = check_image(image)
    base = command or esptool_command()
    if base is None:
        raise FlashError("the esptool package is not installed: pip install esptool")
    cmd = base + ["--chip", CHIP, "--port", port, "--baud", str(baud), "--before", "default-reset", "--after",
                  "hard-reset", "write-flash", "--flash-mode", "keep", "--flash-freq", "keep", "--flash-size", "keep",
                  "-z", f"0x{address:x}", str(image)]
    t0 = time.monotonic()
    state: dict = {"total": len(data)}
    lines: list[str] = []
    emit = on_progress or (lambda p: None)
    emit(Progress("Starting esptool", None, 0, len(data)))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except OSError as exc:
        raise FlashError(f"cannot run esptool: {exc}") from exc
    assert proc.stdout is not None
    buf = b""
    cancelled = False

    def handle(chunk: bytes) -> None:
        text = chunk.decode("utf-8", "replace")
        lines.append(text)
        if on_line and not PROGRESS_RE.search(text):
            on_line(text.rstrip())
        p = parse_line(text, state)
        if p is not None:
            p.elapsed = time.monotonic() - t0
            emit(p)

    done_evt = threading.Event()

    def watch() -> None:                       # read() blocks while esptool is silent: stop it from outside
        nonlocal cancelled
        while not done_evt.wait(0.1):
            if cancel is not None and cancel.is_set():
                cancelled = True
                proc.terminate()
                return

    if cancel is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                if buf:
                    handle(buf)
                buf = b""
            else:
                buf += ch
        if buf:
            handle(buf)
    finally:
        done_evt.set()
        proc.stdout.close()
        rc = proc.wait()
    if cancelled:
        raise FlashError("cancelled: the board may have a half-written image. Flash it again.")
    if rc != 0:
        fatal = state.get("fatal", "")
        if "not ESP32-S3" in fatal or ("This chip is" in fatal and "S3" in fatal):
            raise FlashError(f"{fatal}. This firmware is for the CYD's ESP32-S3: nothing was written.")
        if re.search(r"Failed to connect|No serial data received|Wrong boot mode", " ".join(lines)):
            raise FlashError("the board did not enter its bootloader. Hold BOOT, tap RESET, release BOOT, and try again."
                             + (f" ({fatal})" if fatal else ""))
        if re.search(r"Could not open|Permission denied|Resource busy|busy", " ".join(lines)):
            raise FlashError("the serial port is in use or not allowed: close other programs that have it open "
                             "(and check you are in the dialout group). " + fatal)
        raise FlashError(fatal or f"esptool failed (exit {rc})")
    emit(Progress("Done", 100.0, len(data), len(data), "The board is running the new firmware.",
                  time.monotonic() - t0))
    return lines
