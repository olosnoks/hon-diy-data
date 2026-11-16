"""Read a handheld drive or capture file (``Dnnnnnn.HHD`` / ``Cnnnnnn.HHD``).

The reader takes what checks out and nothing else. A file whose power was
cut has no END block and may end in half a block; a file whose space was
taken in advance carries whatever the card held after its last block.
Every block CRC includes the file's own nonce, and block numbers only go
up, so neither a torn block nor an old file's leftovers get in.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import format as F


@dataclass
class Read:
    t_us: int
    addr: int
    data: bytes
    latency_us: int


@dataclass
class IsoRead:
    t_us: int
    mode: int
    pid: int
    data: bytes


@dataclass
class GpsFix:
    t_us: int
    lat: float
    lon: float
    utc: int                    # unix seconds, 0 = none
    speed_kmh: float
    heading: float
    fix: int
    sats: int
    hdop: float | None


@dataclass
class HhEvent:
    t_us: int
    code: int
    src: int
    aux: int
    value: float | int

    @property
    def name(self) -> str:
        return F.EVENT_NAMES.get(self.code, f"event_{self.code}")


@dataclass
class CaptureRecord:
    t_trigger_us: int
    t_start_us: int
    t_end_us: int
    reason: int
    complete: bool
    n_fired: int


@dataclass
class DriveFile:
    path: Path | None
    kind: int                   # F.FK_DRIVE / F.FK_CAPTURE
    file_no: int
    boot_no: int
    flags: int
    backend: int
    link_mode: int              # 0 Honda reads, 1 ISO 9141-2 only
    nonce: int
    t0_us: int
    unix_us_at_t0: int
    map_hash: str
    map_sha: bytes
    fw: str
    device: str
    ecu_id: bytes
    drive_no: int               # capture: the drive it was cut from
    reason: int                 # capture: what fired
    trigger_t_us: int
    prealloc: int
    reads: list[Read] = field(default_factory=list)
    iso: list[IsoRead] = field(default_factory=list)
    gps: list[GpsFix] = field(default_factory=list)
    events: list[HhEvent] = field(default_factory=list)
    captures: list[CaptureRecord] = field(default_factory=list)
    times: list[tuple[int, int, int]] = field(default_factory=list)    # (t_us, unix_us, source)
    config: str = ""
    alarm_names: dict[int, str] = field(default_factory=dict)
    end: dict[str, Any] | None = None       # the END block, None = power was cut
    blocks: int = 0
    bad_blocks: int = 0         # places where a block header did not check out
    missing_blocks: int = 0     # gaps in the block numbers: the card fell behind
    used_bytes: int = 0         # the file up to its last good block
    size: int = 0

    @property
    def complete(self) -> bool:
        return self.end is not None

    @property
    def id(self) -> str:
        """Stable identity of this recording: what the importer remembers."""
        return f"handheld:{self.device or '?'}:{self.boot_no}:{'D' if self.kind == F.FK_DRIVE else 'C'}" \
               f"{self.file_no}:{self.nonce:08x}"

    def seconds(self, t_us: int) -> float:
        """Device time -> seconds of this file (0 = its start)."""
        return (t_us - self.t0_us) / 1e6

    @property
    def epoch_unix(self) -> float | None:
        """Wall clock (unix seconds, UTC) of file second 0. From the newest
        GPS time record (source 1, or 4 for GPS with the pulse) if there is
        one (the handheld has no battery clock, so the header only knows the
        time if a fix came before the drive)."""
        best = None
        for t_us, unix_us, source in self.times:
            if best is None or (source in F.TIME_FROM_GPS) >= (best[2] in F.TIME_FROM_GPS):
                best = (t_us, unix_us, source)
        if best is not None:
            return (best[1] - (best[0] - self.t0_us)) / 1e6
        if self.unix_us_at_t0 > 0 and self.flags & F.HF_TIME_VALID:
            return self.unix_us_at_t0 / 1e6
        return None

    @property
    def t_last_us(self) -> int:
        """Time of the last thing recorded."""
        last = self.t0_us
        for seq in (self.reads, self.iso, self.gps, self.events):
            if seq:
                last = max(last, max(item.t_us for item in seq[-64:]))
        return last

    @property
    def ecu_id_text(self) -> str:
        if not self.ecu_id:
            return "(not read)"
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in self.ecu_id)
        return " ".join(f"{b:02X}" for b in self.ecu_id) + f'  "{text}"'


def _records(df: DriveFile, kind: int, count: int, t0_us: int, body: bytes) -> None:
    if kind == F.B_READS:
        pos = 0
        while pos + F.REC_READ.size <= len(body):
            dt, addr, n, latency = F.REC_READ.unpack_from(body, pos)
            pos += F.REC_READ.size
            if pos + n > len(body):
                break
            df.reads.append(Read(t0_us + dt, addr, body[pos:pos + n], latency))
            pos += n
    elif kind == F.B_ISO:
        pos = 0
        while pos + F.REC_ISO.size <= len(body):
            dt, mode, pid, n, _ = F.REC_ISO.unpack_from(body, pos)
            pos += F.REC_ISO.size
            if pos + n > len(body):
                break
            df.iso.append(IsoRead(t0_us + dt, mode, pid, body[pos:pos + n]))
            pos += n
    elif kind == F.B_GPS:
        for (t_us, lat, lon, utc, speed, course, fix, sats, hdop) in F.REC_GPS.iter_unpack(
                body[:len(body) // F.REC_GPS.size * F.REC_GPS.size]):
            df.gps.append(GpsFix(t_us, lat / 1e7, lon / 1e7, utc, speed * 0.036, course / 100.0, fix, sats,
                                 hdop / 100.0 if hdop else None))
    elif kind == F.B_EVENTS:
        for i in range(len(body) // F.REC_EVENT.size):
            t_us, code, src, aux, value = F.REC_EVENT.unpack_from(body, i * F.REC_EVENT.size)
            if code in F.FLOAT_EVENTS:
                value = struct.unpack("<f", struct.pack("<i", value))[0]
            df.events.append(HhEvent(t_us, code, src, aux, value))
    elif kind == F.B_CAPTURE:
        for rec in F.REC_CAPTURE.iter_unpack(body[:len(body) // F.REC_CAPTURE.size * F.REC_CAPTURE.size]):
            df.captures.append(CaptureRecord(rec[0], rec[1], rec[2], rec[3], bool(rec[4]), rec[6]))
    elif kind == F.B_TIME:
        for t_us, unix_us, source, _ in F.REC_TIME.iter_unpack(body[:len(body) // F.REC_TIME.size * F.REC_TIME.size]):
            df.times.append((t_us, unix_us, source))
    elif kind == F.B_CONFIG:
        df.config = body.decode("utf-8", "replace")
    elif kind == F.B_NAMES:
        for line in body.decode("utf-8", "replace").splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[0] == "A" and parts[1].isdigit():
                df.alarm_names[int(parts[1])] = parts[2]
    elif kind == F.B_END and len(body) >= F.REC_END.size:
        n_blocks, n_reads, status, reason, t_end = F.REC_END.unpack_from(body)
        df.end = {"n_blocks": n_blocks, "n_reads": n_reads, "status": status, "t_end_us": t_end,
                  "reason": F.END_REASONS.get(reason, str(reason))}


def parse_drive_file(data: bytes, path: Path | None = None) -> DriveFile:
    """Parse the bytes of a drive or capture file. Raises ValueError if the
    file header is not one (nothing else is fatal)."""
    if len(data) < F.FILE_HDR.size:
        raise ValueError(f"{path or 'file'}: too short for a handheld file")
    h = F.FILE_HDR.unpack_from(data)
    if h[0] != F.FILE_MAGIC or h[2] != F.FILE_HDR.size:
        raise ValueError(f"{path or 'file'}: not a handheld drive file")
    if h[1] != F.FILE_VERSION:
        raise ValueError(f"{path or 'file'}: file version {h[1]}; this hondalog reads {F.FILE_VERSION}")
    if F.crc32(data[:F.FILE_HDR.size - 4]) != h[-1]:
        raise ValueError(f"{path or 'file'}: the file header's checksum is wrong")
    df = DriveFile(path=path, kind=h[5], file_no=h[3], boot_no=h[4], flags=h[6], backend=h[7], link_mode=h[8],
                   nonce=h[9], t0_us=h[10], unix_us_at_t0=h[11], map_hash=F.cstr(h[12]), map_sha=h[13],
                   fw=F.cstr(h[14]), device=F.cstr(h[15]), ecu_id=h[16][:h[17]], drive_no=h[19], reason=h[20],
                   trigger_t_us=h[22], prealloc=h[23], size=len(data))
    pos = df.used_bytes = F.FILE_HDR.size
    last_seq = -1
    lost = False                # the walk is off a block boundary: looking for the next one
    while pos + F.BLOCK_HDR.size + 4 <= len(data):
        ok = False
        if data[pos:pos + 4] == F.BLOCK_MAGIC_BYTES:
            magic, kind, _flags, count, length, seq, t0_us = F.BLOCK_HDR.unpack_from(data, pos)
            end = pos + F.BLOCK_HDR.size + length
            if length <= F.MAX_PAYLOAD and end + 4 <= len(data) and seq > last_seq \
                    and struct.unpack_from("<I", data, end)[0] == F.block_crc(df.nonce, data[pos:end]):
                ok = True
        if not ok:
            if not lost:
                df.bad_blocks += 1
                lost = True
            nxt = data.find(F.BLOCK_MAGIC_BYTES, pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        lost = False
        df.missing_blocks += seq - last_seq - 1
        last_seq = seq
        df.blocks += 1
        _records(df, kind, count, t0_us, data[pos + F.BLOCK_HDR.size:end])
        pos = df.used_bytes = end + 4
        if kind == F.B_END:
            break
    # what lies after the last good block of a file cut by power is not a "bad block"
    if df.end is None and lost:
        df.bad_blocks -= 1
    df.reads.sort(key=lambda r: r.t_us)
    df.events.sort(key=lambda e: e.t_us)
    return df


def read_drive_file(path: str | Path) -> DriveFile:
    path = Path(path)
    return parse_drive_file(path.read_bytes(), path)
