"""The laptop's side of the handheld's ``HL`` text session (``hl_proto.py``):
list, read, write and delete files on the handheld's SD card, edit
``HANDHELD.CFG``, upload the road map, read a live status snapshot, mark,
set the clock, restart. One blocking client over a port object (``SerialPort``,
``SimPort``: ``write(bytes)`` and ``read(timeout) -> bytes``); the GUI runs it
on a worker thread (``hondalog/ui/handheld_worker.py``), the command line
(``hondalog-handheld ls|get|put|cfg|stat|...``) calls it directly.

Writing to a handheld that cannot listen (the GPS-on-P1 build) is refused
before a byte goes out: ``connect()`` waits for the binary hello and reads the
variant first (``HlTxOnly``).

Uploads go in a window of ``win`` chunks (the handheld says how many it can
buffer in HELLO), one ACK per chunk, whole-file CRC-32 checked by the
handheld before the new file replaces the old one. A failed or interrupted
upload carries on from what the handheld already holds (``HL STATUS``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import format as F
from . import hl_proto as H
from . import serial_proto as P
from .hl_proto import HlError

Progress = Callable[[int, int], None]
REASONS = {
    "badcmd": "the handheld did not understand the command (older firmware?)",
    "args": "bad arguments", "path": "that path is not allowed", "denied": "that file cannot be changed over USB",
    "nosd": "no SD card in the handheld", "nofile": "no such file",
    "busy": "the file is being written by the handheld: end the drive first", "io": "SD card error",
    "crc": "checksum mismatch", "size": "wrong size or too big", "refused": "the handheld could not load it",
    "state": "not expected now", "offset": "out of step", "b64": "a damaged data line",
    "nosession": "no session open", "timeout": "the handheld stopped answering",
    "disconnected": "the cable was disconnected", "cancelled": "cancelled",
    "nohello": "no hello from a handheld", "txonly": "this handheld cannot listen (GPS on P1 build)",
}
FINAL = {"cancelled", "disconnected", "denied", "path", "nosd", "busy", "refused", "args", "txonly", "badcmd"}


def reason_text(exc: BaseException) -> str:
    r = getattr(exc, "reason", None)
    return REASONS.get(r, str(exc)) if r else str(exc)


class HlTxOnly(HlError):
    def __init__(self) -> None:
        super().__init__("txonly")


class Cancelled(HlError):
    def __init__(self) -> None:
        super().__init__("cancelled")


class HlClient:
    def __init__(self, port: Any, on_console: Callable[[str], None] | None = None,
                 on_state: Callable[[bool], None] | None = None, idle_s: float = 20.0,
                 chunk: int | None = None, window: int | None = None):
        self.port = port
        self.on_console = on_console or (lambda text: None)
        self.on_state = on_state or (lambda is_open: None)
        self.idle_s = idle_s
        self.chunk_override, self.window_override = chunk, window
        self.split = H.StreamSplitter()
        self.q: list[str] = []
        self.hello_frame: P.Hello | None = None
        self.info: dict[str, str] = {}
        self.open = False
        self.deadline = 0.0             # when the handheld will end an idle session (our estimate)
        self.cancel_flag = False
        self.rate = 0.0                 # bytes/s of the last transfer
        self.last_stats: dict[str, Any] | None = None

    # ---- the wire ----

    def _frame(self, ftype: int, payload: bytes) -> None:
        if ftype == P.SF_HELLO:
            self.hello_frame = P.parse_hello(payload) or self.hello_frame
        elif ftype == P.SF_LOG:
            self.on_console(payload.decode("utf-8", "replace").rstrip("\r\n"))
        elif ftype == P.SF_STATS:
            self.last_stats = P.parse_stats(payload)

    def pump(self, wait: float = 0.02) -> None:
        for kind, item in self.split.feed(self.port.read(wait)):
            if kind == "frame":
                self._frame(item[0], item[2])
            elif item == "HL TIMEOUT":
                self._set_open(False)
                self.q.append(item)
            elif item.startswith("HL ") or item == "HL":
                self.q.append(item)
            else:
                self.on_console(item)

    def _set_open(self, value: bool) -> None:
        if self.open != value:
            self.open = value
            self.on_state(value)

    def _send(self, text: str) -> None:
        self.port.write(text.encode("ascii", "replace") + b"\n")

    def _next(self, idle: float) -> str:
        end = time.monotonic() + idle
        while not self.q:
            if self.cancel_flag:
                raise Cancelled()
            if time.monotonic() > end:
                raise HlError("timeout")
            self.pump(0.02)
        line = self.q.pop(0)
        if line == "HL TIMEOUT":
            raise HlError("timeout")
        return line

    def _expect(self, command: str, until: str, idle: float = 8.0) -> list[str]:
        """Send; the HL lines up to and including the first one that starts with ``until``."""
        self._ensure()
        self.q.clear()
        self._send(command)
        out: list[str] = []
        while True:
            line = self._next(idle)
            if line.startswith("HL ERR"):
                raise HlError(line[7:].strip() or "state")
            out.append(line)
            if line.startswith(until):
                self.deadline = time.monotonic() + H.TIMEOUT_S
                return out

    def drain(self, quiet: float = 0.3) -> None:
        """Throw away what is still arriving (the tail of a failed transfer)."""
        end = time.monotonic() + quiet
        while time.monotonic() < end:
            before = len(self.q)
            self.pump(0.02)
            if len(self.q) != before:
                end = time.monotonic() + quiet
        self.q.clear()

    # ---- connection ----

    def connect(self, bauds: tuple[int, ...] = P.BAUDS, listen_s: float = 3.5) -> P.Hello:
        """Find the speed the handheld talks at (its binary hello says who it is) and refuse a TX-only one.
        Nothing is written until the hello is in."""
        for baud in bauds:
            try:
                if hasattr(self.port, "set_baud"):
                    self.port.set_baud(baud)
            except Exception:               # a pty, a port that has not opened yet
                pass
            self.hello_frame = None
            end = time.monotonic() + listen_s
            while time.monotonic() < end and self.hello_frame is None:
                self.pump(0.05)
                if any(l.startswith("HL ") for l in self.q):
                    break
            if self.hello_frame is not None:
                if not self.hello_frame.two_way:
                    raise HlTxOnly()
                return self.hello_frame
            if self.q:                      # an HL session left open by an earlier program: it is two-way
                self.hello_frame = None
                return P.Hello(1, 0, P.CAP_RX, "?", "?", "", b"", b"", 0, 0, 0, 0, 0, 0, baud, 0, 0, 0, 0)
        raise HlError("nohello")

    def hello(self, total: float = 8.0) -> dict[str, str]:
        """Open (or re-open) the session. Asked again every second: the handheld may be mid-frame."""
        end = time.monotonic() + total
        nxt = 0.0
        self.q.clear()
        while True:
            now = time.monotonic()
            if self.cancel_flag:
                raise Cancelled()
            if now > end:
                raise HlError("nohello")
            if now >= nxt:
                nxt = now + 1.0
                self.port.write(b"\nHL HELLO\n")        # the LF drops a half-received line
            self.pump(0.05)
            while self.q:
                line = self.q.pop(0)
                if line.startswith("HL HELLO"):
                    self.info = dict(t.split("=", 1) for t in line.split()[2:] if "=" in t)
                    self._set_open(True)
                    self.deadline = time.monotonic() + H.TIMEOUT_S
                    return self.info

    def _ensure(self) -> None:
        if self.open and time.monotonic() > self.deadline:
            self._set_open(False)       # the handheld has ended it by now
        if not self.open:
            self.hello()

    def bye(self) -> None:
        if not self.open:
            return
        try:
            self._expect("HL BYE", "HL OK", 2.0)
        except HlError:
            pass
        self._set_open(False)

    def close(self) -> None:
        try:
            self.bye()
        except Exception:
            pass

    def keepalive(self) -> None:
        """A command that costs nothing, to keep the session from timing out."""
        self._expect("HL STAT", "HL STAT ")

    @property
    def chunk(self) -> int:
        return self.chunk_override or int(self.info.get("chunk", 768))

    @property
    def window(self) -> int:
        return self.window_override or int(self.info.get("win", 2))

    # ---- commands ----

    def ls(self, path: str = "/") -> tuple[list[tuple[str, int, int]], list[str]]:
        """(files as (path, size, mtime), folders)"""
        files, dirs = [], []
        for line in self._expect("HL LS " + path, "HL END", 15.0)[:-1]:
            if line.startswith("HL F "):
                size, mtime, name = line[5:].split(" ", 2)
                files.append((name, int(size), int(mtime)))
            elif line.startswith("HL D "):
                dirs.append(line[5:].strip())
        return files, dirs

    def _get(self, command: str, progress: Progress | None, idle: float = 20.0) -> bytes:
        self._ensure()
        self.q.clear()
        self._send(command)
        size, got, crc = -1, 0, 0
        chunks: list[bytes] = []
        t0 = time.monotonic()
        while True:
            line = self._next(idle)
            if line.startswith("HL BEGIN "):
                size = int(line[9:])
            elif size < 0 and (line.startswith("HL DATA ") or line.startswith("HL END")):
                continue                    # the tail of an earlier transfer
            elif line.startswith("HL DATA "):
                data = H.unb64(line[8:].strip())
                if data is None:
                    raise HlError("b64")
                chunks.append(data)
                got += len(data)
                if progress:
                    progress(got, size)
            elif line.startswith("HL END "):
                out = b"".join(chunks)
                if size != len(out):
                    raise HlError("size")
                if F.crc32(out) != int(line[7:].strip(), 16):
                    raise HlError("crc")
                self.deadline = time.monotonic() + H.TIMEOUT_S
                self.rate = len(out) / max(time.monotonic() - t0, 1e-6)
                return out
            elif line.startswith("HL ERR"):
                raise HlError(line[7:].strip() or "state")

    def get(self, path: str, progress: Progress | None = None) -> bytes:
        """The file's bytes (one retry for a damaged line)."""
        try:
            return self._get("HL GET " + path, progress)
        except HlError as exc:
            if exc.reason in FINAL:
                raise
            self.drain()
            return self._get("HL GET " + path, progress)

    def cfg_get(self, progress: Progress | None = None) -> bytes:
        return self._get("HL CFG GET", progress)

    def rm(self, path: str) -> None:
        self._expect("HL RM " + path, "HL OK")

    def stat(self) -> dict[str, Any]:
        return json.loads(self._expect("HL STAT", "HL STAT ")[-1][8:])

    def mark(self) -> None:
        self._expect("HL MARK", "HL OK")

    def set_time(self, unix_ms: float | None = None) -> str:
        """The handheld's clock, to ``unix_ms`` (now). Answer: ``ok`` or ``ignored`` (it has GPS time)."""
        ms = int((time.time() * 1000) if unix_ms is None else unix_ms)
        return self._expect(f"HL TIME {ms}", "HL OK")[-1][5:].strip() or "ok"

    def reboot(self) -> None:
        try:
            self._expect("HL REBOOT", "HL OK", 3.0)
        except HlError as exc:
            if exc.reason != "timeout":
                raise
        self._set_open(False)

    def partial(self, path: str) -> tuple[int, int, int]:
        """What the handheld holds of an unfinished upload of ``path``: (held bytes, size, crc32); held 0 = nothing."""
        f = self._expect("HL STATUS " + path, "HL PART", 5.0)[-1].split()
        return int(f[2]), int(f[3]), int(f[4], 16)

    # ---- uploads ----

    def _put_once(self, command: str, data: bytes, start: int, progress: Progress | None) -> str:
        self._ensure()
        self.q.clear()
        crc = F.crc32(data)
        n = len(data)
        self._send(f"{command} {n} {H.hex8(crc)}" + (f" {start}" if start else ""))
        line = self._next(10.0)
        if line.startswith("HL ERR"):
            raise HlError(line[7:].strip() or "state")
        if not line.startswith("HL READY"):
            raise HlError("state")
        f = line.split()
        pos = acked = int(f[2]) if len(f) > 2 else 0
        t0, base = time.monotonic(), pos
        inflight: list[int] = []
        chunk, window = self.chunk, self.window
        if progress:
            progress(acked, n)
        sent_empty = False
        while True:
            while pos < n and len(inflight) < window:
                if self.cancel_flag:
                    raise Cancelled()
                part = data[pos:pos + chunk]
                self.port.write(b"HL DATA @%d %s\n" % (pos, H.b64(part).encode("ascii")))
                pos += len(part)
                inflight.append(pos)
            if n == 0 and not sent_empty:
                sent_empty = True
                self._send("HL DATA ")
            line = self._next(self.idle_s)
            if line.startswith("HL ACK "):
                acked = int(line[7:])
                inflight = [e for e in inflight if e > acked]
                if progress:
                    progress(acked, n)
            elif line.startswith("HL OK"):
                self.deadline = time.monotonic() + H.TIMEOUT_S
                self.rate = (n - base) / max(time.monotonic() - t0, 1e-6)
                if progress:
                    progress(n, n)
                return line[5:].strip()
            elif line.startswith("HL ERR"):
                raise HlError(line[7:].strip() or "state")

    def _put(self, command: str, path: str, data: bytes, progress: Progress | None, attempts: int,
             on_retry: Callable[[int, Exception], None] | None) -> str:
        crc = F.crc32(data)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                try:
                    held, size, pcrc = self.partial(path)
                except HlError as exc:
                    if exc.reason in FINAL:
                        raise
                    held, size, pcrc = 0, 0, 0
                start = held if (size, pcrc) == (len(data), crc) and 0 < held < len(data) else 0
                return self._put_once(command, data, start, progress)
            except HlError as exc:
                last = exc
                if exc.reason in FINAL or (exc.reason == "crc" and attempt >= 1):
                    raise
                if on_retry:
                    on_retry(attempt + 1, exc)
                self._set_open(False)
                try:
                    self.drain()
                except Exception:
                    pass
        assert last is not None
        raise last

    def put(self, path: str, data: bytes, progress: Progress | None = None, attempts: int = 4,
            on_retry: Callable[[int, Exception], None] | None = None) -> str:
        """Write ``data`` to ``path`` (MAP/*, HANDHELD.CFG, HANDHELD.MAP). Resumes after an interruption.
        Returns the handheld's note (``lines=.. skipped=..`` for a config)."""
        return self._put("HL PUT " + path, path, data, progress, attempts, on_retry)

    def cfg_put(self, data: bytes, progress: Progress | None = None, attempts: int = 4) -> str:
        return self._put("HL CFG PUT", "/HANDHELD.CFG", data, progress, attempts, None)

    def put_map(self, folder: str | Path, progress: Callable[[str, int, int, int, int], None] | None = None,
                on_retry: Callable[[int, Exception], None] | None = None) -> list[tuple[str, int]]:
        """Upload a ``MAP`` folder (MAP.DAT, MAP.IDX, README.TXT) into /MAP on the card. The pair is checked first
        (same build id), the big file goes first so an interrupted upload never leaves a new index over an old
        map, and each file replaces the old one only when it is whole. ``progress(name, held, size, done, total)``."""
        return self.put_map_files(map_folder_files(folder), progress, on_retry)

    def put_map_files(self, files: list[tuple[str, bytes]],
                      progress: Callable[[str, int, int, int, int], None] | None = None,
                      on_retry: Callable[[int, Exception], None] | None = None) -> list[tuple[str, int]]:
        total = sum(len(d) for _, d in files)
        done = 0
        out = []
        for name, data in files:
            base = done
            cb = ((lambda held, size, name=name, base=base: progress(name, held, size, base + held, total))
                  if progress else None)
            self._put("HL PUT /MAP/" + name, "/MAP/" + name, data, cb, 4, on_retry)
            out.append((name, len(data)))
            done += len(data)
        return out


MAP_FILES = ("MAP.DAT", "MAP.IDX", "README.TXT")


class MapFolderError(ValueError):
    pass


def map_folder_files(folder: str | Path) -> list[tuple[str, bytes]]:
    """Read and check a MAP folder (or a card folder that holds one): (name, bytes) in upload order."""
    root = Path(folder)
    if not (root / "MAP.IDX").exists() and (root / "MAP").is_dir():
        root = root / "MAP"
    found = {p.name.upper(): p for p in root.iterdir() if p.is_file()} if root.is_dir() else {}
    for need in ("MAP.IDX", "MAP.DAT"):
        if need not in found:
            raise MapFolderError(f"{root} has no {need}: pick the MAP folder made by `hondalog-handheld map-build`")
    idx, dat = found["MAP.IDX"].read_bytes(), found["MAP.DAT"].read_bytes()
    if idx[:8] != b"HHMAPIDX" or dat[:8] != b"HHMAPDAT" or len(idx) < 428 or len(dat) < 16:
        raise MapFolderError("MAP.IDX / MAP.DAT are not hondalog map files")
    if F.crc32(idx[:424]) != int.from_bytes(idx[424:428], "little"):
        raise MapFolderError("MAP.IDX is damaged (header checksum)")
    if idx[96:100] != dat[12:16]:
        raise MapFolderError("MAP.IDX and MAP.DAT are from different builds")
    out = [("MAP.DAT", dat), ("MAP.IDX", idx)]
    if "README.TXT" in found:
        out.append(("README.TXT", found["README.TXT"].read_bytes()))
    return out
