"""The handheld's ``HL`` text session (firmware/handheld/SERIAL.md, "The HL
session"): the line protocol the Handheld tab and ``hondalog-handheld`` speak over the
same USB-C cable as the binary stream, and what the simulator and the host
tests share: the byte-stream splitter, the path rules, and a small reference
client.

Same idea as the WarHog toolkit's ``WT`` protocol. ``HL HELLO`` pauses the
binary stream; lines follow until ``HL BYE`` or 30 s without a command::

    HL HELLO                 -> HL HELLO fw= model= proto=1 id= map= variant= chunk= win=
    HL LS <dir>              -> HL F <size> <mtime> <path> | HL D <path> ... HL END
    HL GET <path>            -> HL BEGIN <size>, HL DATA <b64>..., HL END <crc32>
    HL PUT <path> <n> <crc32> [<offset>]
                             -> HL READY <offset>; HL DATA <b64> -> HL ACK <held> ...; HL OK
    HL STATUS <path>         -> HL PART <held> <size> <crc32>
    HL CFG GET | HL CFG PUT <n> <crc32>
    HL RM <path>   HL STAT   HL MARK   HL TIME <unix_ms>   HL REBOOT   HL BYE
    errors: HL ERR <reason>; idle: HL TIMEOUT

Nothing in it reaches the K-line.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from typing import Any

from . import format as F
from . import serial_proto as P

TIMEOUT_S = 30.0
CHUNK = 2048            # raw bytes per HL DATA line (2732 characters of base64)
WINDOW = 3              # chunks the laptop may have in flight (one ACK each)
MODEL = "cyd-4827s043n"
MAX_LINE = 8192
PROTO = 1

# ---- paths ----------------------------------------------------------------------

_SEG = re.compile(r"[A-Za-z0-9_.\-]{1,12}")

WRITE_FILES = ("HANDHELD.CFG", "HANDHELD.MAP")
MAX_SIZE = {"HANDHELD.CFG": 8 * 1024, "HANDHELD.MAP": 256 * 1024}
MAX_MAP_FILE = 512 * 1024 * 1024


def split_path(path: str) -> list[str] | None:
    """``/MAP/MAP.DAT`` -> ``['MAP', 'MAP.DAT']`` (upper case, as FAT keeps them); ``/`` -> ``[]``.
    None if the path is not on the card: relative, ``..``, ``//``, a backslash, odd characters, too long."""
    if not path.startswith("/") or len(path) > 64 or "\\" in path:
        return None
    parts = [p for p in path.split("/")[1:]]
    if parts == [""]:
        return []
    if parts and parts[-1] == "":
        parts.pop()                         # a trailing slash on a folder
    for p in parts:
        if p in (".", "..") or not _SEG.fullmatch(p) or p.strip(".") == "":
            return None
    return [p.upper() for p in parts]


def can_write(parts: list[str]) -> bool:
    """PUT: the two settings files at the root and files directly in MAP/."""
    if len(parts) == 1:
        return parts[0] in WRITE_FILES
    return len(parts) == 2 and parts[0] == "MAP"


def can_remove(parts: list[str]) -> bool:
    """RM: what can be put, and the drive files in DRIVES/."""
    return can_write(parts) or (len(parts) == 2 and parts[0] == "DRIVES")


def max_size(parts: list[str]) -> int:
    return MAX_SIZE.get(parts[-1], MAX_MAP_FILE) if len(parts) == 1 else MAX_MAP_FILE


def reason(status: int) -> str:
    return {P.E_IO: "io", P.E_CRC: "crc", P.E_ARG: "args", P.E_BUSY: "busy", P.E_REFUSED: "refused"}.get(status, "state")


# ---- the byte stream ---------------------------------------------------------------

class StreamSplitter:
    """Frames and text lines out of one byte stream. A frame is
    ``COBS(ver=1 type seq payload crc16) 0x00``; a line is text up to LF. The
    first COBS byte is a length code and the second is ``ver`` (0x01), which no
    line has in its second place, so the chunk's second byte says which it is
    (the same rule the firmware uses)."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.skip = False
        self.bad = 0
        self.frames: list[tuple[int, int, bytes]] = []
        self.lines: list[str] = []

    def feed(self, data: bytes) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for b in data:
            if b == 0:
                if self.buf and not self.skip:
                    self._frame(out)
                self.buf.clear()
                self.skip = False
                continue
            if self.skip:
                continue
            if b == 0x0A and self.buf and (len(self.buf) == 1 or self.buf[1] != 1):
                text = self.buf.decode("utf-8", "replace").lstrip("\r\n").rstrip("\r")
                self.buf.clear()
                if text:
                    out.append(("line", text))
                continue
            if len(self.buf) >= MAX_LINE:
                self.skip = True
                self.buf.clear()
                self.bad += 1
                continue
            self.buf.append(b)
        return out

    def _frame(self, out: list[tuple[str, Any]]) -> None:
        if len(self.buf) == 1 and self.buf[0] in (0x0A, 0x0D):
            return
        raw = P.cobs_decode(bytes(self.buf)) if not (len(self.buf) >= 2 and self.buf[1] != 1) else None
        if (raw is None or len(raw) < 5 or raw[0] != 1
                or int.from_bytes(raw[-2:], "little") != P.crc16(raw[:-2])):
            self.bad += 1
            return
        out.append(("frame", (raw[1], raw[2], raw[3:-2])))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes | None:
    if len(text) % 4 or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", text):
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def hex8(n: int) -> str:
    return f"{n & 0xFFFFFFFF:08X}"


# ---- a reference client (tests, tools) ---------------------------------------------------

class HlError(Exception):
    def __init__(self, reason_: str):
        super().__init__(reason_)
        self.reason = reason_


class HlClient:
    """The laptop's side over anything with ``write(bytes)`` and ``read(timeout) -> bytes``
    (``SimPort``, a pyserial wrapper). Used by the tests."""

    def __init__(self, port: Any, timeout: float = 5.0):
        self.port = port
        self.timeout = timeout
        self.split = StreamSplitter()
        self.queue: list[str] = []
        self.console: list[str] = []
        self.frames: list[tuple[int, int, bytes]] = []
        self.info: dict[str, str] = {}

    def _pump(self, wait: float) -> None:
        for kind, item in self.split.feed(self.port.read(wait)):
            if kind == "frame":
                self.frames.append(item)
            elif item.startswith("HL ") or item == "HL":
                self.queue.append(item)
            else:
                self.console.append(item)

    def send(self, text: str) -> None:
        self.port.write(text.encode("ascii") + b"\n")

    def line(self, timeout: float | None = None) -> str:
        end = time.monotonic() + (timeout or self.timeout)
        while not self.queue:
            if time.monotonic() > end:
                raise TimeoutError("no HL line")
            self._pump(0.01)
        return self.queue.pop(0)

    def cmd(self, text: str, until: str = "HL OK", timeout: float | None = None) -> list[str]:
        """Send; return the lines up to and including the first that starts with ``until``."""
        self.send(text)
        lines: list[str] = []
        while True:
            ln = self.line(timeout)
            if ln.startswith("HL ERR"):
                raise HlError(ln[7:])
            lines.append(ln)
            if ln.startswith(until):
                return lines

    def hello(self) -> dict[str, str]:
        self.send("\nHL HELLO")
        while True:
            ln = self.line()
            if ln.startswith("HL HELLO"):
                self.info = dict(t.split("=", 1) for t in ln.split()[2:] if "=" in t)
                return self.info

    def ls(self, path: str) -> tuple[list[tuple[int, int, str]], list[str]]:
        files, dirs = [], []
        for ln in self.cmd("HL LS " + path, "HL END")[:-1]:
            if ln.startswith("HL F "):
                size, mtime, name = ln[5:].split(" ", 2)
                files.append((int(size), int(mtime), name))
            elif ln.startswith("HL D "):
                dirs.append(ln[5:])
        return files, dirs

    def get(self, path: str) -> bytes:
        lines = self.cmd("HL GET " + path, "HL END")
        size = int(lines[0].split()[2])
        data = b"".join(unb64(ln[8:]) or b"" for ln in lines[1:-1])
        if len(data) != size or F.crc32(data) != int(lines[-1].split()[2], 16):
            raise HlError("crc")
        return data

    def put(self, path: str, data: bytes, offset: int = 0, command: str | None = None, window: int = WINDOW,
            chunk: int = CHUNK) -> str:
        crc = F.crc32(data)
        head = command or f"HL PUT {path}"
        self.send(f"{head} {len(data)} {hex8(crc)}" + (f" {offset}" if offset else ""))
        ln = self.line()
        if ln.startswith("HL ERR"):
            raise HlError(ln[7:])
        pos = int(ln.split()[2]) if len(ln.split()) > 2 else 0
        inflight: list[int] = []
        while True:
            while pos < len(data) and len(inflight) < window:
                part = data[pos:pos + chunk]
                pos += len(part)
                inflight.append(pos)
                self.send("HL DATA " + b64(part))
            if not data:
                self.send("HL DATA ")
            ln = self.line()
            if ln.startswith("HL ACK"):
                held = int(ln.split()[2])
                inflight = [e for e in inflight if e > held]
            elif ln.startswith("HL OK"):
                return ln[5:].strip()
            elif ln.startswith("HL ERR"):
                raise HlError(ln[7:])

    def partial(self, path: str) -> tuple[int, int, int]:
        ln = self.cmd("HL STATUS " + path, "HL PART")[-1].split()
        return int(ln[2]), int(ln[3]), int(ln[4], 16)

    def stat(self) -> dict[str, Any]:
        import json
        return json.loads(self.cmd("HL STAT", "HL STAT ")[-1][8:])

    def bye(self) -> None:
        self.cmd("HL BYE")
