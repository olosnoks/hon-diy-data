"""Handheld simulator: the stream a CYD sends over its USB-C port, without
the CYD (``hondalog-cydsim``).

``CydSim`` is the firmware's laptop link (``hh_serial.c``) in Python, in
front of the simulated ECU (``hondalog.sim.EcuModel``): the same read plan
the handheld would make for the map, read at 9600-baud pace on a clock of its
own, every read sent as a READ frame, a TICK per fast cycle, hello every 2 s,
stats, GPS fixes, events and stall triggers. With ``sd=`` it also writes the
drive as a real ``DRIVES/Dnnnnnn.HHD`` and answers the file commands, so
``hondalog-handheld sync`` has something to pull.

Both firmware variants:

``default``   two-way: commands are answered.
``p1``        GPS on P1: tx-only. Whatever the laptop writes is counted and
              thrown away (on the real board it would land on the GPS's pin).

``SimPort`` serves it in-process (port name ``cyd:sim`` or ``cyd:sim:p1``);
``serve`` puts it on a pty (``hondalog-cydsim``, ``/tmp/hondalog-cydsim``).
``tests/test_hh_serial.py`` holds this model against the compiled firmware
code frame by frame.
"""

from __future__ import annotations

import argparse
import os
import random
import select
import struct
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .. import addressmap
from ..addressmap import AddressMap
from ..link.planner import CostModel
from ..poller import Poller
from ..sim import EcuModel
from ..transport import LoopbackTransport
from . import format as F
from . import serial_proto as P

CYDSIM_SYMLINK = "/tmp/hondalog-cydsim"
FW = "0.1.0-sim"
LATENCY_S = 0.003               # age of a reply when its last byte is in


class _Drive:
    """A drive file as the firmware writes it (FORMAT.md): header, blocks, END."""

    def __init__(self, path: Path, file_no: int, sim: "CydSim", t: float):
        self.path = path
        self.file_no = file_no
        self.nonce = (0x5EED0000 + file_no * 7919 + sim.boot_no) & 0xFFFFFFFF or 1
        self.seq = 0
        self.n_reads = 0
        self._reads = bytearray()
        self._count = 0
        self._t0 = 0
        self._opened = t
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "wb")
        wall = sim.unix_us(t)
        head = F.FILE_HDR.pack(F.FILE_MAGIC, F.FILE_VERSION, F.FILE_HDR.size, file_no, sim.boot_no, F.FK_DRIVE,
                               (F.HF_TIME_VALID | F.HF_TIME_GPS) if wall else 0, 0, 0, self.nonce, _us(t), wall,
                               P.hash12(sim.map_hash), sim.map_sha, F.fit(FW, 12), F.fit(sim.device, 12),
                               sim.ecu_id.ljust(10, b"\0"), len(sim.ecu_id), 0, 0, 0, b"", 0, 0, b"", 0)
        self.fh.write(head[:-4] + struct.pack("<I", F.crc32(head[:-4])))

    def block(self, kind: int, count: int, t0_us: int, payload: bytes) -> None:
        head = F.BLOCK_HDR.pack(F.BLOCK_MAGIC, kind, 0, count, len(payload), self.seq, t0_us)
        self.seq += 1
        self.fh.write(head + payload + struct.pack("<I", F.block_crc(self.nonce, head + payload)))

    def read(self, t: float, t_us: int, addr: int, data: bytes, latency_us: int) -> None:
        if self._count and (len(self._reads) + 8 + len(data) > 480 or t - self._opened >= 0.25):
            self.flush()
        if not self._count:
            self._t0, self._opened = t_us, t
        self._reads += F.REC_READ.pack(t_us - self._t0, addr, len(data), latency_us) + data
        self._count += 1
        self.n_reads += 1

    def flush(self) -> None:
        if self._count:
            self.block(F.B_READS, self._count, self._t0, bytes(self._reads))
            self._reads.clear()
            self._count = 0
        self.fh.flush()

    def event(self, t_us: int, code: int, src: int, aux: int, value: float | int) -> None:
        self.flush()
        packed = struct.pack("<QBBHf" if code in F.FLOAT_EVENTS else "<QBBHi", t_us, code, src, aux, value)
        self.block(F.B_EVENTS, 1, t_us, packed)

    def close(self, t: float, reason: int = 2) -> None:
        self.flush()
        self.block(F.B_END, 1, _us(t), F.REC_END.pack(self.seq, self.n_reads, 1, reason, _us(t)))
        self.fh.close()


def _us(t: float) -> int:
    return max(0, int(t * 1e6 + 0.5))


class CydSim:
    """The handheld as the laptop sees it on the serial port. ``advance(t)``
    runs it up to device time ``t`` (seconds since its boot); ``take_tx()``
    is what its UART sent; ``rx(data)`` is what the laptop wrote."""

    def __init__(self, variant: str = "default", sd: str | Path | None = None, amap: AddressMap | None = None,
                 stall_after: float | None = None, stall_mode: str = "rpm", restart_after: float | None = 15.0,
                 gps: bool = True, baud: int = 921600, device: str = "HH-SIM001", boot_no: int = 1,
                 map_hash: str | None = None, record: bool = True, dtcs: tuple[int, ...] = (),
                 unix_time: float | None = None, damage: float = 0.0, seed: int = 1):
        if variant not in ("default", "p1"):
            raise ValueError("variant is 'default' or 'p1'")
        self.variant = variant
        self.tx_only = variant == "p1"
        self.amap = amap or addressmap.load_default()
        self.sd = Path(sd) if sd else None
        self.baud = baud
        self.device = device
        self.boot_no = boot_no
        self.map_hash = map_hash or self.amap.version_hash
        self.map_sha = bytes(8)
        self.gps = gps
        self.dtcs = dtcs
        self.now = 0.0
        self._unix0 = (time.time() if unix_time is None else unix_time) if gps else 0.0
        self.model = EcuModel(self.amap.framing, clock=lambda: self.now, stall_after_s=stall_after,
                              stall_mode=stall_mode, restart_after_s=restart_after)
        self.ecu_id = bytes(self.model.ram[0x76:0x80])
        try:                                # channels as the handheld counts them: those its map could compile
            from .mapfile import build_map, read_map
            self.n_ch = len(read_map(build_map(self.amap)).channels)
        except Exception:
            self.n_ch = len(self.amap.channels)
        # the plan the handheld makes is the laptop's planner ported to C: use the original
        poller = Poller(LoopbackTransport(self.model), self.amap)
        self._sched = poller._sched
        self._blocks = list(poller.plan)
        self._cost = CostModel.from_framing(self.amap.framing)
        self._pending = set(self._sched.top)
        self._unread = set(range(len(self._blocks)))
        self._rpm = self.amap.channel("rpm")
        self._armed = False
        self._rpm_low = False
        self._t = 0.05                      # the engine's own cursor: the next exchange ends at or after this
        self._up = False
        self._down_since: float | None = None
        self._link_stalled = False
        self._next_down = 0.0
        self._next_hello = 0.0
        self._next_stats = 0.0
        self._next_gps = 1.0
        self._last_tick: float | None = None
        self.hz = 0.0
        self.reads = self.errors = self.markers = self.stalls = self.ticks = 0
        # the link
        self._tx = bytearray()
        self._seq = 0
        self._deframer = P.Deframer()
        self.tx_frames = self.tx_bytes = self.rx_frames = 0
        self.rx_ignored = 0                 # bytes the laptop wrote to a tx-only handheld
        self._last_rx: float | None = None
        self._damage = damage
        self._rng = random.Random(seed)
        self.sent: list[tuple[int, bytes]] = []     # (type, payload) of every frame, for tests
        self.keep_sent = False
        #: stop answering READ_FILE after this many more answers (the cable is pulled mid-transfer)
        self.file_answers_left: int | None = None
        self.silent = False                 # True: the UART sends nothing (unplugged)
        # the card
        self.record = record and self.sd is not None
        self._drive: _Drive | None = None
        self._drive_no = 0
        self._hold = False
        self._put: dict[str, Any] | None = None
        self.time_set: int | None = None
        if self.sd is not None:
            (self.sd / "DRIVES").mkdir(parents=True, exist_ok=True)
            (self.sd / "CAPTURES").mkdir(exist_ok=True)
            self._drive_no = max([int(p.stem[1:]) for p in (self.sd / "DRIVES").glob("D*.HHD")
                                  if p.stem[1:].isdigit()], default=0)
            if not (self.sd / "HANDHELD.MAP").exists() and map_hash is None:
                from .mapfile import export_map
                export_map(self.amap, self.sd)
            try:
                from .mapfile import read_map
                mf = read_map(self.sd / "HANDHELD.MAP")
                if map_hash is None:
                    self.map_hash = mf.map_hash
            except (OSError, ValueError):
                pass

    # ---- what a test or the keyboard can do to it ----

    def stall(self) -> None:
        self.model.stall()

    def power_cut(self) -> None:
        self.model.power_cut()

    def restart(self) -> None:
        self.model.restart()

    def mark(self) -> None:
        """The button's long press."""
        self.markers += 1
        self._event(self.now, F.EV_MARKER, 0, 0, self.markers)

    def close(self) -> None:
        if self._drive is not None:
            self._drive.close(self.now)
            self._drive = None

    def unix_us(self, t: float) -> int:
        if self.time_set is not None:
            return self.time_set + _us(t)
        return int((self._unix0 + t) * 1e6) if self._unix0 else 0

    # ---- frames out ----

    def _send(self, ftype: int, payload: bytes = b"") -> None:
        frame = P.frame_encode(ftype, payload, self._seq)
        self._seq = (self._seq + 1) & 0xFF
        self.tx_frames += 1
        self.tx_bytes += len(frame)
        if self.keep_sent:
            self.sent.append((ftype, payload))
        if self.silent:
            return
        if self._damage and self._rng.random() < self._damage:
            raw = bytearray(frame)
            raw[self._rng.randrange(len(raw) - 1)] ^= 1 << self._rng.randrange(8)
            frame = bytes(raw)
        self._tx += frame

    def take_tx(self) -> bytes:
        data = bytes(self._tx)
        self._tx.clear()
        return data

    def hello(self) -> P.Hello:
        caps = P.CAP_LOG if self.tx_only else P.CAP_RX | P.CAP_LOG | (P.CAP_FILES | P.CAP_PUT if self.sd else 0)
        unix = self.unix_us(self.now)
        state = P.LINK_OK if self._up else P.LINK_OFF if self._down_since is not None else P.LINK_CONNECTING
        return P.Hello(P.REV, P.VARIANT_GPS_P1 if self.tx_only else P.VARIANT_DEFAULT, caps, FW, self.device,
                       self.map_hash, self.map_sha, self.ecu_id if self.reads else b"", state, 0, 0,
                       self.n_ch, (2 if unix else 0) | (4 if unix and self.gps else 0) | (8 if self.sd else 0),
                       self.boot_no, self.baud, _us(self.now), unix, int(P.HELLO_S * 1000), P.MAX_PAYLOAD)

    def host_attached(self) -> bool:
        return self._last_rx is not None and self.now - self._last_rx < P.HOST_S

    def _stats(self) -> None:
        flags = ((P.ST_SD_OK if self.sd else 0) | (P.ST_RECORDING if self._drive else 0)
                 | (P.ST_GPS_FIX if self.gps else 0) | (P.ST_TIME if self.unix_us(self.now) else 0)
                 | (P.ST_ARMED if self._armed else 0) | (P.ST_HOST if self.host_attached() else 0))
        self._send(P.SF_STATS, P.pack_stats(
            t_us=_us(self.now), unix_us=self.unix_us(self.now), reads=self.reads, errors=self.errors,
            hz_c=int(self.hz * 100), link_state=P.LINK_OK if self._up else P.LINK_OFF, flags=flags,
            drive_no=self._drive.file_no if self._drive else 0,
            log_bytes=self._drive.fh.tell() if self._drive else 0, free_mb=14000 if self.sd else 0,
            markers=self.markers, stall_count=self.stalls, nmea_good=int(self.now) * 2 if self.gps else 0,
            tx_frames=self.tx_frames, tx_bytes=self.tx_bytes, rx_frames=self.rx_frames, rx_bad=self._deframer.bad))

    def _event(self, t: float, code: int, src: int, aux: int, value: float | int) -> None:
        packed = struct.pack("<QBBHf" if code in F.FLOAT_EVENTS else "<QBBHi", _us(t), code, src, aux, value)
        self._send(P.SF_EVENT, packed)
        if self._drive is not None:
            self._drive.event(_us(t), code, src, aux, value)

    def _link(self, t: float) -> None:
        state = P.LINK_OK if self._up else P.LINK_OFF
        self._send(P.SF_LINK, P.LINK.pack(_us(t), state, 1 if self._up else 0, 0, 0))

    # ---- the engine ----

    def _drive_begin(self, t: float) -> None:
        if not self.record or self._drive is not None or self._hold or self.sd is None:
            return
        self._drive_no += 1
        self._drive = _Drive(self.sd / "DRIVES" / f"D{self._drive_no:06d}.HHD", self._drive_no, self, t)
        self._event(t, F.EV_DRIVE_START, 0, 0, self._drive_no)

    def _stall(self, t: float, reason: int, rpm_before: float) -> None:
        self.stalls += 1
        self._armed = False
        self._event(t, F.EV_STALL, reason, 0, rpm_before)

    def _exchange(self) -> None:
        """One read request, or one failed probe, ending at ``self._t``."""
        self.model.step()
        if not self.model.powered:
            t = self._t
            self._t += 0.25
            self.errors += 1
            if self._up or self._down_since is None:
                self._up = False
                self._down_since = t
                self._link_stalled = False
                self.hz = 0.0
                self._last_tick = None
                self._event(t, F.EV_LINK_DOWN, 0, 0, 0)
            if self._armed and not self._link_stalled and t - self._down_since >= 1.0:
                self._link_stalled = True
                self._stall(t, 2, 0.0)
            self._link(t)                   # repeated while down: time passes for the laptop
            return
        index = self._sched.next(self._t)
        block = self._blocks[index]
        t_done = self._t + self._cost.request_s(block.length)
        self._t = t_done
        data = bytes(self.model.ram[(block.addr + i) & 0xFF] for i in range(block.length))
        self._sched.done(index, t_done)
        self.reads += 1
        if not self._up:
            self._up = True
            self._down_since = None
            self._drive_begin(t_done)
            self._event(t_done, F.EV_LINK_UP, 0, 0, 0)
            self._link(t_done)
        t_us, lat_us = _us(t_done - LATENCY_S), int(LATENCY_S * 1e6)
        self._send(P.SF_READ, P.READ.pack(t_us, block.addr, block.length, lat_us) + data)
        if self._drive is not None:
            self._drive.read(t_done, t_us, block.addr, data, lat_us)
        rpm = self._rpm
        if rpm is not None and block.addr <= rpm.addr and rpm.addr + rpm.length <= block.addr + block.length:
            value = rpm.convert(data[rpm.addr - block.addr:rpm.addr - block.addr + rpm.length]) or 0.0
            if value > 400:
                self._armed, self._rpm_low = True, False
            elif value < 50 and self._armed and not self._rpm_low:
                self._rpm_low = True
                self._stall(t_done, 1, 0.0)
        self._unread.discard(index)
        self._pending.discard(index)
        if not self._pending:
            self._pending.update(self._sched.top)
            if not self._unread:
                if self._last_tick is not None and t_done > self._last_tick:
                    inst = 1.0 / (t_done - self._last_tick)
                    self.hz = inst if self.hz == 0 else 0.9 * self.hz + 0.1 * inst
                self._last_tick = t_done
                self.ticks += 1
                self._send(P.SF_TICK, P.TICK.pack(_us(t_done), lat_us, int(self.hz * 100), self.ticks))

    def advance(self, t: float) -> None:
        """Run up to device time ``t``."""
        while True:
            due = min(self._t, self._next_hello, self._next_stats, self._next_gps if self.gps else 1e18)
            if due > t:
                break
            self.now = max(self.now, due)
            if due == self._next_hello:
                self._next_hello += P.HELLO_S
                self._send(P.SF_HELLO, self.hello().pack())
            elif due == self._next_stats:
                self._next_stats += 1.0
                self._stats()
            elif self.gps and due == self._next_gps:
                self._next_gps += 1.0
                gps = F.REC_GPS.pack(_us(due), int((32.27412 + 1e-5 * due) * 1e7), int((-90.13021 - 7e-6 * due) * 1e7),
                                     int(self._unix0 + due), 1150, 8440, 1, 9, 90)
                self._send(P.SF_GPS, gps)
                if self._drive is not None:
                    self._drive.flush()
                    self._drive.block(F.B_GPS, 1, _us(due), gps)
            else:
                self._exchange()
        self.now = max(self.now, t)

    # ---- commands (two-way) ----

    def rx(self, data: bytes) -> None:
        if self.tx_only:
            self.rx_ignored += len(data)    # GPIO44 is the GPS's: nothing is listening
            return
        for ftype, seq, payload in self._deframer.feed(data):
            self.rx_frames += 1
            self._last_rx = self.now
            self._command(ftype, seq, payload)

    def _ack(self, cmd: int, seq: int, status: int = P.OK, value: int = 0) -> None:
        self._send(P.SF_ACK, P.ACK.pack(cmd, seq, status, value & 0xFFFFFFFF))

    def _path(self, kind: int, no: int) -> Path | None:
        if self.sd is None:
            return None
        return {P.F_DRIVE: self.sd / "DRIVES" / f"D{no:06d}.HHD", P.F_CAPTURE: self.sd / "CAPTURES" / f"C{no:06d}.HHD",
                P.F_MAP: self.sd / "HANDHELD.MAP", P.F_CFG: self.sd / "HANDHELD.CFG"}.get(kind)

    def _busy(self, kind: int, no: int) -> bool:
        return kind == P.F_DRIVE and self._drive is not None and self._drive.file_no == no

    def _command(self, ftype: int, seq: int, p: bytes) -> None:
        if ftype == P.SC_GET_HELLO:
            self._send(P.SF_HELLO, self.hello().pack())
        elif ftype == P.SC_TIME_REQ and len(p) >= 8:
            self._send(P.SF_TIME_RSP, P.TIME_RSP.pack(struct.unpack("<q", p[:8])[0], _us(self.now), _us(self.now)))
        elif ftype == P.SC_TIME_SET and len(p) >= 8:
            if self.gps:
                self._ack(ftype, seq, P.IGNORED)        # the GPS has set the clock
            else:
                self.time_set = struct.unpack("<q", p[:8])[0] - _us(self.now)
                self._ack(ftype, seq)
        elif ftype == P.SC_MARK:
            self.mark()
            self._ack(ftype, seq)
        elif ftype == P.SC_ACK_ALARM:
            self._ack(ftype, seq)
        elif ftype == P.SC_DRIVE and len(p) >= 1:
            if p[0]:
                self._hold = False
                if self._up:
                    self._drive_begin(self.now)
            else:
                self._hold = True
                self.close()
            self._ack(ftype, seq, P.OK, 1 if p[0] else 0)
        elif ftype == P.SC_DTC:
            self._ack(ftype, seq)
            for i, code in enumerate(self.dtcs):
                self._event(self.now, F.EV_DTC, i, len(self.dtcs), code)
            if not self.dtcs:
                self._event(self.now, F.EV_DTC_NONE, 0, 0, 0)
        elif ftype == P.SC_LIST_FILES and len(p) >= P.LIST.size:
            self._list(seq, *P.LIST.unpack(p[:P.LIST.size]))
        elif ftype == P.SC_READ_FILE and len(p) >= P.READ_FILE.size:
            self._read_file(seq, *P.READ_FILE.unpack(p[:P.READ_FILE.size]))
        elif ftype in (P.SC_PUT_BEGIN, P.SC_PUT_DATA, P.SC_PUT_END):
            self._put_cmd(ftype, seq, p)
        else:
            self._ack(ftype, seq, P.E_UNSUPPORTED if ftype not in (P.SC_TIME_REQ, P.SC_TIME_SET, P.SC_DRIVE,
                                                                   P.SC_LIST_FILES, P.SC_READ_FILE) else P.E_ARG)

    def _list(self, seq: int, kind: int, after: int) -> None:
        if self.sd is None:
            self._ack(P.SC_LIST_FILES, seq, P.E_UNSUPPORTED)
            return
        entries: list[tuple[int, int]] = []
        if kind in (P.F_MAP, P.F_CFG):
            path = self._path(kind, 0)
            if after < 1 and path is not None and path.is_file():
                entries.append((1, path.stat().st_size))
        elif kind in (P.F_DRIVE, P.F_CAPTURE):
            folder, letter = (self.sd / "DRIVES", "D") if kind == P.F_DRIVE else (self.sd / "CAPTURES", "C")
            for path in folder.glob(f"{letter}*.HHD"):
                if path.stem[1:].isdigit() and int(path.stem[1:]) > after:
                    entries.append((int(path.stem[1:]), path.stat().st_size))
        else:
            self._ack(P.SC_LIST_FILES, seq, P.E_ARG)
            return
        entries.sort()
        page = entries[:24]
        for no, size in page:
            self._send(P.SF_FILE_INFO, P.FILE_INFO.pack(kind, P.FI_OPEN if self._busy(kind, no) else 0, 0, no, size))
        self._send(P.SF_FILE_INFO, P.FILE_INFO.pack(kind, P.FI_MORE if len(entries) > len(page) else 0, 0,
                                                    P.NO_FILE, len(page)))

    def _read_file(self, seq: int, kind: int, want: int, no: int, offset: int) -> None:
        if self.sd is None:
            self._ack(P.SC_READ_FILE, seq, P.E_UNSUPPORTED)
            return
        if self.file_answers_left is not None:
            if self.file_answers_left <= 0:
                return                      # the cable is out: no answer
            self.file_answers_left -= 1
        path = self._path(kind, no)
        if path is None or not path.is_file() or self._busy(kind, no):
            busy = P.FD_BUSY if path is not None and self._busy(kind, no) else 0
            self._send(P.SF_FILE_DATA, P.FILE_DATA.pack(kind, busy, 0, no, P.NO_FILE, 0))
            return
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(min(want, P.CHUNK))
        eof = P.FD_EOF if offset + len(data) >= size else 0
        self._send(P.SF_FILE_DATA, P.FILE_DATA.pack(kind, eof, len(data), no, offset, F.crc32(data)) + data)

    def _put_cmd(self, ftype: int, seq: int, p: bytes) -> None:
        if self.sd is None:
            self._ack(ftype, seq, P.E_UNSUPPORTED)
            return
        put = self._put
        if ftype == P.SC_PUT_BEGIN:
            if len(p) < P.PUT_BEGIN.size:
                self._ack(ftype, seq, P.E_ARG)
                return
            kind, size, crc = P.PUT_BEGIN.unpack(p[:P.PUT_BEGIN.size])
            if kind not in (P.F_MAP, P.F_CFG) or not size or size > (256 << 10 if kind == P.F_MAP else 8 << 10):
                self._ack(ftype, seq, P.E_ARG)
                return
            if put and (put["kind"], put["size"], put["crc"]) == (kind, size, crc):
                self._ack(ftype, seq, P.OK, len(put["data"]))       # the same file again: carry on
                return
            self._put = {"kind": kind, "size": size, "crc": crc, "data": bytearray()}
            self._ack(ftype, seq)
        elif ftype == P.SC_PUT_DATA:
            if put is None or len(p) < 4:
                self._ack(ftype, seq, P.E_STATE if put is None else P.E_ARG)
                return
            offset, data = struct.unpack("<I", p[:4])[0], p[4:]
            got = len(put["data"])
            if offset + len(data) <= got:
                self._ack(ftype, seq, P.OK, got)
            elif offset != got:
                self._ack(ftype, seq, P.E_OFFSET, got)
            elif got + len(data) > put["size"]:
                self._ack(ftype, seq, P.E_ARG, got)
            else:
                put["data"] += data
                self._ack(ftype, seq, P.OK, len(put["data"]))
        else:
            if put is None or len(put["data"]) != put["size"]:
                self._ack(ftype, seq, P.E_STATE, len(put["data"]) if put else 0)
                return
            self._put = None
            data = bytes(put["data"])
            if F.crc32(data) != put["crc"]:
                self._ack(ftype, seq, P.E_CRC)
                return
            value = 0
            if put["kind"] == P.F_MAP:
                from .mapfile import read_map
                try:
                    read_map(data)
                except (ValueError, struct.error):
                    self._ack(ftype, seq, P.E_REFUSED, 1)
                    return
            else:
                keys = {line.split("=")[0].strip() for line in _cfg_defaults().splitlines() if "=" in line}
                for line in data.decode("utf-8", "replace").splitlines():
                    line = line.split("#")[0].strip()
                    if line and line.split("=")[0].strip() not in keys:
                        value += 1
            final = self._path(put["kind"], 0)
            assert final is not None
            if final.exists():
                final.replace(final.with_suffix(".MBK" if put["kind"] == P.F_MAP else ".CBK"))
            final.write_bytes(data)
            self._ack(ftype, seq, P.OK, value)


def _cfg_defaults() -> str:
    from .mapfile import DEFAULT_CFG
    return "\n".join(line for line in DEFAULT_CFG.splitlines() if not line.startswith("#"))


# ---- ports -----------------------------------------------------------------

def sim_from_port(name: str, **kwargs: Any) -> CydSim:
    """``cyd:sim`` -> the default (two-way) variant; ``cyd:sim:p1`` -> tx-only.
    More, colon separated: ``stall@8`` (the engine dies after 8 s), ``power@8``
    (the ECU loses power), ``nogps``."""
    parts = [p for p in name.split(":")[2:] if p]
    opts: dict[str, Any] = {}
    for part in parts:
        word, _, at = part.partition("@")
        if word == "p1":
            opts["variant"] = "p1"
        elif word in ("stall", "power"):
            opts["stall_after"] = float(at) if at else 20.0
            opts["stall_mode"] = "rpm" if word == "stall" else "power"
        elif word == "nogps":
            opts["gps"] = False
        elif word != "default":
            raise ValueError(f"{name}: unknown simulator option {word!r}")
    opts.update(kwargs)
    return CydSim(**opts)


class SimPort:
    """The simulator as a port object (no pty), on the real clock."""

    def __init__(self, sim: CydSim | None = None, clock: Callable[[], float] = time.monotonic):
        self.sim = sim or CydSim()
        self.name = "cyd:sim" + (":p1" if self.sim.tx_only else "")
        self.baud = self.sim.baud
        self._clock = clock
        self._t0: float | None = None
        self.written = 0                    # bytes the laptop sent, whatever became of them
        self.fail: Exception | None = None  # raise this from read/write (the cable is pulled)

    def open(self) -> None:
        if self._t0 is None:
            self._t0 = self._clock()

    def close(self) -> None:
        pass

    def set_baud(self, baud: int) -> None:
        self.baud = baud

    def _advance(self) -> None:
        assert self._t0 is not None
        self.sim.advance(self._clock() - self._t0)

    def write(self, data: bytes) -> None:
        if self.fail is not None:
            raise self.fail
        self.written += len(data)
        self._advance()
        if self.baud == self.sim.baud:
            self.sim.rx(data)

    def read(self, timeout: float) -> bytes:
        end = self._clock() + timeout
        while True:
            if self.fail is not None:
                raise self.fail
            self._advance()
            data = self.sim.take_tx()
            if data and self.baud != self.sim.baud:
                # the wrong speed: bytes arrive, none of them a frame
                data = bytes((b * 7 + 0x55) & 0xFF or 0x55 for b in data[::2])
            now = self._clock()
            if data or now >= end:
                return data
            time.sleep(min(0.002, end - now))


HELP = "keys: s stall (rpm 0)  p power cut (ECU silent)  r restart  m marker  q quit"


def serve(sim: CydSim, link_path: str = CYDSIM_SYMLINK, keys: bool = True, run_for: float | None = None,
          out: Any = sys.stdout) -> None:
    """Put the simulator on a pseudo-terminal until 'q', Ctrl+C or run_for."""
    import termios
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    pts = os.ttyname(slave)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
    os.symlink(pts, link_path)
    way = "tx-only (GPS on P1)" if sim.tx_only else "two-way"
    print(f"simulated handheld on {link_path} -> {pts}  ({way}, map #{sim.map_hash})", file=out)
    print(f"connect with:  hondalog --port cyd:{link_path}", file=out)
    if sim.sd is not None:
        print(f"its SD card is {sim.sd}", file=out)
    stdin_fd = old_term = None
    if keys and sys.stdin.isatty():
        stdin_fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(stdin_fd)
        tty.setcbreak(stdin_fd)
        print(HELP, file=out)
    out.flush()
    t0 = last_status = time.monotonic()
    backlog = bytearray()
    try:
        while run_for is None or time.monotonic() - t0 < run_for:
            fds = [master] + ([stdin_fd] if stdin_fd is not None else [])
            ready, _, _ = select.select(fds, [], [], 0.004)
            now = time.monotonic()
            sim.advance(now - t0)
            if stdin_fd is not None and stdin_fd in ready:
                key = os.read(stdin_fd, 1).decode(errors="ignore").lower()
                if key == "q":
                    break
                action = {"s": sim.stall, "p": sim.power_cut, "r": sim.restart, "m": sim.mark}.get(key)
                if action:
                    action()
                    print(f"\n[{action.__name__}]", file=out)
            if master in ready:
                try:
                    sim.rx(os.read(master, 4096))
                except (BlockingIOError, OSError):
                    pass
            backlog += sim.take_tx()
            if len(backlog) > 65536:        # nobody is reading the pty: keep the newest, as the firmware's ring does
                del backlog[:len(backlog) - 32768]
                cut = backlog.find(0)
                del backlog[:cut + 1]
            if backlog:
                try:
                    done = os.write(master, backlog[:4096])
                    del backlog[:done]
                except (BlockingIOError, OSError):
                    pass
            if stdin_fd is not None and now - last_status > 0.5:
                last_status = now
                print(f"\r{sim.now:7.1f} s  {sim.hz:5.1f} Hz  {sim.reads} reads  {sim.tx_bytes} bytes sent  "
                      f"stalls {sim.stalls}  {'PC attached' if sim.host_attached() else '           '}",
                      end="", file=out, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if old_term is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
        try:
            if os.path.islink(link_path):
                os.unlink(link_path)
        except OSError:
            pass
        sim.close()
        os.close(master)
        os.close(slave)
        print("\nsimulator stopped", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-cydsim",
                                 description="Simulated handheld (CYD) on a pty: the serial stream of SERIAL.md.")
    ap.add_argument("--variant", choices=("default", "p1"), default="default",
                    help="default: two-way. p1: GPS on P1, tx-only")
    ap.add_argument("--link", default=CYDSIM_SYMLINK, help=f"symlink to create (default {CYDSIM_SYMLINK})")
    ap.add_argument("--sd", help="folder that plays the SD card: drives are written there and can be synced")
    ap.add_argument("--map", help="address map TOML the handheld runs (default: the shipped map)")
    ap.add_argument("--stall-after", type=float, metavar="SEC", help="the engine dies after this long")
    ap.add_argument("--stall-mode", choices=("rpm", "power"), default="rpm",
                    help="rpm: the ECU keeps answering. power: it goes silent")
    ap.add_argument("--restart-after", type=float, default=15.0, help="seconds until it starts again, 0 = stays dead")
    ap.add_argument("--no-gps", action="store_true")
    ap.add_argument("--damage", type=float, default=0.0, help="share of frames sent with a flipped bit (0..1)")
    ap.add_argument("--run-for", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    amap = addressmap.load(args.map) if args.map else addressmap.load_default()
    sim = CydSim(args.variant, args.sd, amap, args.stall_after, args.stall_mode, args.restart_after or None,
                 gps=not args.no_gps, damage=args.damage)
    serve(sim, args.link, run_for=args.run_for)
    return 0


if __name__ == "__main__":
    sys.exit(main())
