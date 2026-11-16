"""The simulated handheld's ``HL`` text session (``hl_proto.py``): the same
behaviour as the firmware's ``hh_hl.c``, on the simulator's SD folder. A mixin
of ``cydsim.CydSim``, which supplies the clock, the UART buffer and the engine.

While a session is open the binary stream is not sent (the engine and the
drive file go on), ESP_LOG text goes out as plain lines, and every command
that is not a ``DATA`` line re-arms a 30 s idle timer.

``sd_kbps`` models the card: a chunk costs ``len / sd_kbps`` before its ACK.
``tests`` hooks: ``hl_fail_after`` (answer ``HL ERR io`` after that many DATA
lines), ``hl_commands`` (every command heard).
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

from . import format as F
from . import hl_proto as H
from . import serial_proto as P


class HlMixin:
    def _hl_init(self, logs: bool, sd_kbps: float | None) -> None:
        self._split = H.StreamSplitter()
        self.logs = logs
        self.sd_bps = sd_kbps * 1000.0 if sd_kbps else None
        self._hl_open = False
        self._hl_deadline = 0.0
        self._hl_busy_until = 0.0
        self._hl_later: list[tuple[float, str]] = []
        self._hl_get: dict[str, Any] | None = None
        self._hl_part: dict[str, Any] | None = None
        self._hl_skip_data = False          # a PUT failed: ignore its DATA lines still on the wire
        self._gps_raw: bytes | None = None
        self._boot_logged = False
        self._next_log = 5.0
        self.hl_commands: list[str] = []
        self.hl_fail_after: int | None = None
        self.hl_paused = 0                  # frames the stream did not send while a session was open
        self.hl_rx_bytes = 0
        self.pressure = 0                   # bytes the pty server holds back (set by serve)

    # ---- output ----

    def _hl_text(self, text: str) -> None:
        if not self.silent:
            self._tx += text.encode("utf-8", "replace") + b"\n"

    def _hl_out(self, text: str, cost: float = 0.0) -> None:
        """A reply line, after the work it took (the card is one thing at a time)."""
        start = max(self.now, self._hl_busy_until)
        self._hl_busy_until = start + cost
        if self._hl_busy_until <= self.now and not self._hl_later:
            self._hl_text(text)
        else:
            self._hl_later.append((self._hl_busy_until, text))

    def log(self, tag: str, text: str) -> None:
        """ESP_LOG: a LOG frame in the stream, a plain line in a session."""
        if not self.logs:
            return
        line = f"I ({int(self.now * 1000)}) {tag}: {text}"
        if self._hl_open:
            self._hl_text(line)
        else:
            self._send(P.SF_LOG, line.encode()[:240])

    def _hl_tick(self) -> None:
        """Called at the end of every ``advance``: timers, delayed replies, the GET in progress."""
        if self.logs and not self._boot_logged:
            self._boot_logged = True
            self.log("hhcore", f"map #{self.map_hash} {self.n_ch} channels, boot {self.boot_no}")
        if self.logs and not self._hl_open and self.now >= self._next_log:
            self._next_log = self.now + 5.0
            self.log("hhsim", f"{self.reads} reads, {self.hz:.1f} Hz, {self.stalls} stalls")
        if self._hl_open and self.now > self._hl_deadline:
            self._hl_get = None
            self._hl_later.clear()
            self._hl_text("HL TIMEOUT")
            self._hl_end()
        self._hl_later.sort(key=lambda x: x[0])
        while self._hl_later and self._hl_later[0][0] <= self.now:
            self._hl_text(self._hl_later.pop(0)[1])
        self._hl_pump_get()

    def _hl_end(self) -> None:
        self._hl_open = False
        self._hl_skip_data = False
        if not self.silent:
            self._tx += b"\x00"
        self._next_hello = self.now           # the stream starts again with a hello
        self._next_stats = self.now + 0.2
        self.log("hhserial", "session ended")

    # ---- input ----

    def _hl_feed(self, data: bytes) -> None:
        self.hl_rx_bytes += len(data)
        for kind, item in self._split.feed(data):
            if kind == "frame":
                self.rx_frames += 1
                self._last_rx = self.now
                if not self._hl_open:
                    self._command(item[0], item[1], item[2])
            elif item.startswith("HL"):
                self._last_rx = self.now
                self._hl_line(item)

    def _hl_resolve(self, path: str) -> tuple[list[str], Path] | None:
        parts = H.split_path(path)
        if parts is None or self.sd is None:
            return None
        node = self.sd
        for seg in parts:
            hit = next((c for c in node.iterdir() if c.name.upper() == seg), None) if node.is_dir() else None
            node = hit if hit is not None else node / seg
        return parts, node

    def _hl_line(self, text: str) -> None:
        cmd, _, rest = text[2:].strip().partition(" ")
        cmd = cmd.upper()
        if cmd == "DATA":
            self._hl_data(rest)
            return
        self.hl_commands.append(cmd)
        if self._hl_open and cmd != "STAT":
            self._hl_deadline = self.now + H.TIMEOUT_S
        self._hl_skip_data = False
        if cmd == "HELLO":
            self._hl_get = None
            if not self._hl_open:
                self.log("hhserial", "session open")
                self._hl_open = True
                if not self.silent:
                    self._tx += b"\x00"
            self._hl_deadline = self.now + H.TIMEOUT_S
            self._hl_out("HL HELLO " + self._hl_hello())
            return
        if not self._hl_open:
            self._hl_out("HL ERR nosession")
            return
        if self.sd is None and cmd in ("LS", "GET", "PUT", "STATUS", "CFG", "RM"):
            self._hl_out("HL ERR nosd")
            return
        handler = getattr(self, "_hl_cmd_" + cmd.lower(), None)
        if handler is None:
            self._hl_out("HL ERR badcmd")
            return
        self._hl_get = None
        handler(rest.strip())

    def _hl_hello(self) -> str:
        return (f"fw={self.hello().fw} model={H.MODEL} proto={H.PROTO} id={self.device} map={self.map_hash} "
                f"variant={'gps-p1' if self.tx_only else 'default'} chunk={H.CHUNK} win={H.WINDOW}")

    # ---- commands ----

    def _hl_cmd_bye(self, rest: str) -> None:
        self._hl_out("HL OK bye")
        self._hl_later.sort(key=lambda x: x[0])
        for _, text in self._hl_later:
            self._hl_text(text)
        self._hl_later.clear()
        self._hl_end()

    def _hl_cmd_mark(self, rest: str) -> None:
        self.mark()
        self._hl_out("HL OK")

    def _hl_cmd_time(self, rest: str) -> None:
        try:
            ms = int(rest)
        except ValueError:
            self._hl_out("HL ERR args")
            return
        if ms <= 0:
            self._hl_out("HL ERR args")
        elif self.gps:
            self._hl_out("HL OK ignored")       # the GPS has set the clock
        else:
            self.time_set = ms * 1000 - int(self.now * 1e6)
            self._hl_out("HL OK")

    def _hl_cmd_reboot(self, rest: str) -> None:
        self._hl_out("HL OK reboot")
        self._hl_part = None
        self.boot_no += 1
        self._hl_later.sort(key=lambda x: x[0])
        for _, text in self._hl_later:
            self._hl_text(text)
        self._hl_later.clear()
        self._hl_end()

    def _hl_cmd_stat(self, rest: str) -> None:
        self._hl_out("HL STAT " + json.dumps(self.hl_stat(), separators=(",", ":")))

    def hl_stat(self) -> dict[str, Any]:
        gps = F.REC_GPS.unpack(self._gps_raw) if self._gps_raw else None
        unix = self.unix_us(self.now)
        link = P.LINK_OK if self._up else P.LINK_OFF if self._down_since is not None else P.LINK_CONNECTING
        return {
            "t_ms": int(self.now * 1000), "fw": self.hello().fw, "boot": self.boot_no,
            "link": {"state": link, "name": P.LINK_NAMES[link], "hz": round(self.hz, 2), "reads": self.reads,
                     "errors": self.errors, "timeouts": 0, "checksum": 0, "retries": 0, "reconnects": 0},
            "ecu": {"id": self.ecu_id.hex() if self.reads else "", "len": len(self.ecu_id) if self.reads else 0},
            "gps": {"fix": gps[6] if gps else 0, "sats": gps[7] if gps else 0,
                    "lat": gps[1] / 1e7 if gps else 0.0, "lon": gps[2] / 1e7 if gps else 0.0,
                    "speed_kmh": round(gps[4] * 0.036, 1) if gps else 0.0, "course": gps[5] / 100 if gps else 0.0,
                    "hdop": gps[8] / 100 if gps else 0.0, "nmea_good": int(self.now) * 2 if self.gps else 0,
                    "nmea_bad": 0},
            "sd": {"ok": 1 if self.sd else 0, "free_mb": 14000 if self.sd else 0},
            "drive": {"recording": 1 if self._drive else 0, "no": self._drive.file_no if self._drive else 0,
                      "bytes": self._drive.fh.tell() if self._drive else 0, "markers": self.markers,
                      "stalls": self.stalls, "armed": 1 if self._armed else 0},
            "time": {"unix_ms": unix // 1000, "source": "gps" if unix and self.gps else "host" if unix else "none"},
            "serial": {"baud": self.baud, "tx_frames": self.tx_frames, "tx_dropped": 0,
                       "rx_frames": self.rx_frames, "rx_bad": self._split.bad, "ring_peak": 0},
        }

    def _hl_cmd_ls(self, rest: str) -> None:
        r = self._hl_resolve(rest or "/")
        if r is None:
            self._hl_out("HL ERR path")
            return
        parts, node = r
        if not node.is_dir():
            self._hl_out("HL ERR nofile")
            return
        base = "/" + "/".join(parts) if parts else ""
        for c in sorted(node.iterdir(), key=lambda c: c.name.upper()):
            if c.name.startswith("."):
                continue
            if c.is_dir():
                self._hl_out(f"HL D {base}/{c.name.upper()}")
            else:
                st = c.stat()
                self._hl_out(f"HL F {st.st_size} {int(st.st_mtime)} {base}/{c.name.upper()}")
        self._hl_out("HL END")

    def _hl_busy_path(self, node: Path) -> bool:
        return self._drive is not None and node.resolve() == Path(self._drive.path).resolve()

    def _hl_cmd_get(self, rest: str) -> None:
        r = self._hl_resolve(rest)
        if r is None:
            self._hl_out("HL ERR path")
            return
        self._hl_start_get(r[1])

    def _hl_start_get(self, node: Path) -> None:
        if not node.is_file():
            self._hl_out("HL ERR nofile")
        elif self._hl_busy_path(node):
            self._hl_out("HL ERR busy")
        else:
            size = node.stat().st_size
            self._hl_out(f"HL BEGIN {size}")
            self._hl_get = {"fh": open(node, "rb"), "crc": 0}

    def _hl_pump_get(self) -> None:
        job = self._hl_get
        while job is not None and len(self._tx) + self.pressure < 32768 and not self._hl_later and self.now >= self._hl_busy_until:
            data = job["fh"].read(H.CHUNK)
            if not data:
                job["fh"].close()
                self._hl_text(f"HL END {H.hex8(job['crc'])}")
                self._hl_get = job = None
                break
            job["crc"] = F.crc32(data, job["crc"])
            self._hl_text("HL DATA " + H.b64(data))
            if self.sd_bps:
                self._hl_busy_until = self.now + len(data) / self.sd_bps
                break

    def _hl_cmd_cfg(self, rest: str) -> None:
        sub, _, args = rest.partition(" ")
        if sub.upper() == "GET":
            self._hl_start_get(self.sd / "HANDHELD.CFG")
        elif sub.upper() == "PUT":
            self._hl_put("/HANDHELD.CFG", args)
        else:
            self._hl_out("HL ERR args")

    def _hl_cmd_rm(self, rest: str) -> None:
        r = self._hl_resolve(rest)
        if r is None:
            self._hl_out("HL ERR path")
            return
        parts, node = r
        if not H.can_remove(parts):
            self._hl_out("HL ERR denied")
        elif not node.is_file():
            self._hl_out("HL ERR nofile")
        elif self._hl_busy_path(node):
            self._hl_out("HL ERR busy")
        else:
            node.unlink()
            self._hl_out("HL OK")

    def _hl_cmd_status(self, rest: str) -> None:
        part = self._hl_part
        r = self._hl_resolve(rest)
        if r is None:
            self._hl_out("HL ERR path")
        elif part and part["parts"] == r[0]:
            self._hl_out(f"HL PART {part['held']} {part['n']} {H.hex8(part['crc'])}")
        else:
            self._hl_out("HL PART 0 0 00000000")

    def _hl_cmd_put(self, rest: str) -> None:
        path, _, args = rest.partition(" ")
        self._hl_put(path, args)

    def _hl_close_part(self, drop: bool = True) -> None:
        part = self._hl_part
        if part:
            part["fh"].close()
            if drop:
                Path(part["tmp"]).unlink(missing_ok=True)
                self._hl_part = None

    def _hl_put(self, path: str, args: str) -> None:
        r = self._hl_resolve(path)
        if r is None:
            self._hl_out("HL ERR path")
            return
        parts, node = r
        if not H.can_write(parts):
            self._hl_out("HL ERR denied")
            return
        try:
            f = args.split()
            n, crc = int(f[0]), int(f[1], 16)
            offset = int(f[2]) if len(f) > 2 else 0
            if n < 0 or len(f) not in (2, 3) or offset < 0:
                raise ValueError
        except (ValueError, IndexError):
            self._hl_out("HL ERR args")
            return
        if n > H.max_size(parts):
            self._hl_out("HL ERR size")
            return
        tmp = self.sd / "HL_PUT.TMP"
        part = self._hl_part
        if part and part["parts"] == parts and part["n"] == n and part["crc"] == crc and 0 < offset <= part["held"]:
            part["fh"].truncate(offset)
            part["fh"].seek(offset)
            part["held"] = offset
            part["run"] = _crc_of(tmp, offset)
        else:
            self._hl_close_part()
            part = self._hl_part = {"parts": parts, "node": node, "n": n, "crc": crc, "held": 0, "run": 0,
                                    "tmp": tmp, "fh": open(tmp, "w+b")}
        self._hl_skip_data = False
        part["fail"] = False
        self._hl_out(f"HL READY {part['held']}")
        if n == 0:
            part["held"] = 0

    def _hl_data(self, rest: str) -> None:
        part = self._hl_part
        if self._hl_open:
            self._hl_deadline = self.now + H.TIMEOUT_S
        if not self._hl_open or part is None or self._hl_skip_data:
            if not self._hl_skip_data:
                self._hl_out("HL ERR state" if self._hl_open else "HL ERR nosession")
                self._hl_skip_data = True
            return
        at = None
        if rest.startswith("@"):
            head, _, rest = rest.partition(" ")
            try:
                at = int(head[1:])
            except ValueError:
                pass
        data = H.unb64(rest.strip())
        n_done = part.setdefault("lines", 0)
        part["lines"] = n_done + 1

        def fail(reason: str) -> None:
            self._hl_skip_data = True
            self._hl_out("HL ERR " + reason)

        if data is None:
            fail("b64")
            return
        if self.hl_fail_after is not None and part["lines"] > self.hl_fail_after:
            self.hl_fail_after = None
            fail("io")
            return
        if at is not None and at != part["held"]:
            fail("offset")
            return
        if part["held"] + len(data) > part["n"]:
            fail("size")
            return
        part["fh"].write(data)
        part["held"] += len(data)
        part["run"] = F.crc32(data, part["run"])
        cost = 0.001 + (len(data) / self.sd_bps if self.sd_bps else 0.0)
        self._hl_out(f"HL ACK {part['held']}", cost)
        if part["held"] == part["n"]:
            self._hl_finish(part)

    def _hl_finish(self, part: dict[str, Any]) -> None:
        part["fh"].flush()
        parts, node = part["parts"], part["node"]
        if part["run"] != part["crc"]:
            self._hl_close_part()
            self._hl_out("HL ERR crc")
            return
        info = ""
        data = None
        if len(parts) == 1:
            data = Path(part["tmp"]).read_bytes()
            if parts[0] == "HANDHELD.MAP":
                from .mapfile import read_map
                try:
                    read_map(data)
                except (ValueError, struct.error):
                    self._hl_close_part()
                    self._hl_out("HL ERR refused")
                    return
            else:
                keys = {line.split("=")[0].strip() for line in _cfg_default_lines()}
                lines = skipped = 0
                for line in data.decode("utf-8", "replace").splitlines():
                    line = line.split("#")[0].strip()
                    if line:
                        lines += 1
                        skipped += line.split("=")[0].strip() not in keys
                info = f" lines={lines} skipped={skipped}"
        part["fh"].close()
        final = self.sd / "/".join(parts)
        if len(parts) == 1 and final.exists():
            final.replace(final.with_suffix(".MBK" if parts[0] == "HANDHELD.MAP" else ".CBK"))
        final.parent.mkdir(parents=True, exist_ok=True)
        if len(parts) == 2 and (existing := next((c for c in final.parent.iterdir() if c.name.upper() == parts[1]), None)):
            final = existing
        os.replace(part["tmp"], final)
        self._hl_part = None
        self._hl_out("HL OK" + info, 0.002)


def _crc_of(path: Path, length: int) -> int:
    crc = 0
    with open(path, "rb") as fh:
        left = length
        while left > 0:
            block = fh.read(min(left, 1 << 20))
            if not block:
                break
            crc = F.crc32(block, crc)
            left -= len(block)
    return crc


def _cfg_default_lines() -> list[str]:
    from .mapfile import DEFAULT_CFG
    return [line for line in DEFAULT_CFG.splitlines() if "=" in line and not line.startswith("#")]
