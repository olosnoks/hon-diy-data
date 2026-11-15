"""Read and write black box event files (``EVnnnnnn.BBX``).

Layout (details in firmware/blackbox/FORMAT.md)::

    file header (84 bytes, own CRC)
    blocks: 24-byte header, payload, CRC32 of both
        EVENTS   what fired, with reason codes
        CONFIG   the config text in force
        SAMPLES  256 x 28-byte records, one per millisecond
        EDGES    64-bit edge words with microsecond times
        GPS      optional fixes
        INDEX    where every block is
        END      marks a complete file

The firmware writes the newest pre-trigger blocks first, so blocks are not
in time order on disk. ``read_event`` sorts them. A file cut short by a power
loss has no INDEX/END; the reader then walks the blocks, keeps every one whose
CRC is good, and says so in ``BlackboxEvent.warnings``.
"""

from __future__ import annotations

import csv
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import format as F
from .triggers import Config


class EventFileError(ValueError):
    pass


@dataclass
class BBEventRecord:
    t_us: int
    code: int
    src: int
    value: int
    action: int = 0

    @property
    def name(self) -> str:
        return F.reason_name(self.code)

    @property
    def text(self) -> str:
        return F.reason_text(self.code)

    @property
    def is_trigger(self) -> bool:
        return 1 <= self.code <= F.R_MAX_TRIGGER


@dataclass
class BlackboxEvent:
    """One capture, decoded.

    ``t_us`` is device time (microseconds since the box booted) of each
    1 kHz record; ``samples`` is the matching structured array
    (``format.SAMPLE_DTYPE``). ``channels()`` gives plain float arrays.
    """
    path: Path | None = None
    event_no: int = 0
    boot_no: int = 0
    reason: int = 0
    trigger_t_us: int = 0
    trigger_value: int = 0
    unix_us_at_t0: int = 0          # wall clock at t_us = 0; 0 = clock was never set
    time_valid: bool = False
    time_from_gps: bool = False
    pre_ms: int = 0
    post_ms: int = 0
    fw: str = ""
    device: str = ""
    tach_ppr: int = 2
    ign_ppr: int = 2
    config_text: str = ""
    t_us: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    samples: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=F.SAMPLE_DTYPE))
    edge_t_us: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    edge_ch: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    edge_level: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    events: list = field(default_factory=list)          # BBEventRecord, by time
    gps: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=F.GPS_DTYPE))
    complete: bool = False          # END block present and the index checked out
    blocks_ok: int = 0
    blocks_bad: int = 0
    warnings: list = field(default_factory=list)

    @property
    def reason_name(self) -> str:
        return F.reason_name(self.reason)

    @property
    def reason_text(self) -> str:
        return F.reason_text(self.reason)

    @property
    def config(self) -> Config:
        return Config.parse(self.config_text, strict=False)

    @property
    def trigger_wall(self) -> datetime | None:
        """Laptop wall-clock time of the trigger, if the clock was set."""
        if not self.time_valid:
            return None
        return datetime.fromtimestamp((self.unix_us_at_t0 + self.trigger_t_us) / 1e6)

    def seconds(self) -> np.ndarray:
        """Record times in seconds relative to the trigger (negative = before)."""
        return (self.t_us - self.trigger_t_us) / 1e6

    def channels(self) -> dict[str, np.ndarray]:
        """Every channel as a float array, named as the live reader names them."""
        s = self.samples
        out = {}
        for i, name in enumerate(F.ANALOG):
            out[f"bb_{name}"] = s["mv"][:, i] / 1000.0
        for i, name in enumerate(F.RAILS):
            out[f"bb_{name}_min"] = s["min_dv"][:, i] / 10.0
            out[f"bb_{name}_max"] = s["max_dv"][:, i] / 10.0
        out["bb_rpm_tach"] = s["rpm_tach"].astype(float)
        out["bb_rpm_ign"] = s["rpm_ign"].astype(float)
        out["bb_inj_pw"] = s["inj_pw_us"] / 1000.0
        out["bb_inj_duty"] = s["inj_duty"] / 2.0
        for bit, name in ((F.DIG_TACH, "tach"), (F.DIG_IGN, "ign"), (F.DIG_INJ, "inj"),
                          (F.DIG_BUTTON, "button"), (F.DIG_IG1_ON, "ig1_on"),
                          (F.DIG_RUNNING, "running")):
            out[f"bb_{name}"] = ((s["dig"] & bit) != 0).astype(float)
        return out

    def edges(self, channel: str | int) -> tuple[np.ndarray, np.ndarray]:
        """(times_us, levels) of one pulse input: "tach", "ign" or "inj"."""
        ch = F.EDGE.index(channel) if isinstance(channel, str) else channel
        sel = self.edge_ch == ch
        return self.edge_t_us[sel], self.edge_level[sel]

    def triggers(self) -> list[BBEventRecord]:
        return [e for e in self.events if e.is_trigger]


# ---- reading ----------------------------------------------------------------

def _parse_header(data: bytes) -> BlackboxEvent:
    if len(data) < F.FILE_HDR.size:
        raise EventFileError("file is shorter than the header")
    (magic, version, hdr_len, event_no, boot_no, reason, _na, _ne, flags, _hz, pre_ms, post_ms,
     trig_t, unix0, trig_val, fw, device, tach_ppr, ign_ppr, _rsv, crc) = F.FILE_HDR.unpack_from(data)
    if magic != F.FILE_MAGIC:
        raise EventFileError("not a black box event file (bad magic)")
    if version != F.FILE_VERSION:
        raise EventFileError(f"file version {version} is not supported (this reads {F.FILE_VERSION})")
    if hdr_len != F.FILE_HDR.size or crc != F.crc32(data[:F.FILE_HDR.size - 4]):
        raise EventFileError("header checksum is wrong")
    return BlackboxEvent(
        event_no=event_no, boot_no=boot_no, reason=reason, trigger_t_us=trig_t,
        trigger_value=trig_val, unix_us_at_t0=unix0, time_valid=bool(flags & F.HF_TIME_VALID),
        time_from_gps=bool(flags & F.HF_TIME_GPS), pre_ms=pre_ms, post_ms=post_ms,
        fw=fw.split(b"\0")[0].decode("ascii", "replace"),
        device=device.split(b"\0")[0].decode("ascii", "replace"),
        tach_ppr=tach_ppr or 2, ign_ppr=ign_ppr or 2)


def _block_at(data: bytes, off: int):
    """(type, count, t0, payload, next_offset) or None if there is no good block here."""
    if off + F.BLOCK_HDR.size + 4 > len(data):
        return None
    magic, btype, _flags, count, plen, _seq, t0 = F.BLOCK_HDR.unpack_from(data, off)
    end = off + F.BLOCK_HDR.size + plen
    if magic != F.BLOCK_MAGIC or end + 4 > len(data):
        return None
    if struct.unpack_from("<I", data, end)[0] != F.crc32(data[off:end]):
        return None
    return btype, count, t0, data[off + F.BLOCK_HDR.size:end], end + 4


def read_event(path) -> BlackboxEvent:
    """Parse an event file. Raises EventFileError only if nothing is usable."""
    path = Path(path)
    data = path.read_bytes()
    ev = _parse_header(data)
    ev.path = path

    blocks = []
    magic = struct.pack("<I", F.BLOCK_MAGIC)
    off = F.FILE_HDR.size
    while off < len(data):
        got = _block_at(data, off)
        if got is None:
            ev.blocks_bad += 1
            nxt = data.find(magic, off + 1)         # resync on the next block magic
            if nxt < 0:
                break
            off = nxt
            continue
        blocks.append((off, *got[:4]))
        off = got[4]
    ev.blocks_ok = len(blocks)

    end = [b for b in blocks if b[1] == F.B_END]
    index = [b for b in blocks if b[1] == F.B_INDEX]
    if end and index and len(end[-1][4]) >= F.END_REC.size:
        index_off, n_blocks, status, _n = F.END_REC.unpack_from(end[-1][4])
        idx = np.frombuffer(index[-1][4], dtype=F.INDEX_DTYPE)
        listed = {int(o) for o in idx["offset"]}
        found = {b[0] for b in blocks if b[1] not in (F.B_INDEX, F.B_END)}
        ev.complete = (status == F.END_COMPLETE and index_off == index[-1][0]
                       and n_blocks == len(idx) and listed == found)
        if not ev.complete:
            ev.warnings.append("index does not match the blocks found; used the blocks")
    else:
        ev.warnings.append("no end marker: the capture was cut short (power loss?); "
                           "kept every block that passed its checksum")
    if ev.blocks_bad:
        ev.warnings.append(f"{ev.blocks_bad} damaged region(s) skipped")

    t_parts, s_parts, e_parts, g_parts, records = [], [], [], [], {}
    for _off, btype, count, t0, payload in blocks:
        if btype == F.B_SAMPLES:
            rec = np.frombuffer(payload, dtype=F.SAMPLE_DTYPE)[:count]
            s_parts.append(rec)
            t_parts.append(t0 + np.arange(len(rec), dtype=np.int64) * F.TICK_US)
        elif btype == F.B_EDGES:
            e_parts.append(np.frombuffer(payload, dtype="<u8")[:count])
        elif btype == F.B_EVENTS:
            for r in np.frombuffer(payload, dtype=F.EVENT_DTYPE)[:count]:
                key = (int(r["t_us"]), int(r["code"]), int(r["value"]))
                records[key] = BBEventRecord(key[0], key[1], int(r["src"]), key[2], int(r["rsv"]))
        elif btype == F.B_CONFIG:
            ev.config_text = payload.decode("ascii", "replace")
        elif btype == F.B_GPS:
            g_parts.append(np.frombuffer(payload, dtype=F.GPS_DTYPE)[:count])

    if s_parts:
        t = np.concatenate(t_parts)
        s = np.concatenate(s_parts)
        t, first = np.unique(t, return_index=True)      # sorts, drops any duplicate
        ev.t_us, ev.samples = t, s[first]
        gaps = int(np.count_nonzero(np.diff(t) != F.TICK_US))
        if gaps:
            ev.warnings.append(f"{gaps} gap(s) in the 1 kHz record")
    if e_parts:
        words = np.unique(np.concatenate(e_parts))
        order = np.argsort(words & np.uint64(F.EDGE_T_MASK), kind="stable")
        words = words[order]
        ev.edge_t_us = (words & np.uint64(F.EDGE_T_MASK)).astype(np.int64)
        ev.edge_ch = ((words >> np.uint64(56)) & np.uint64(3)).astype(np.uint8)
        ev.edge_level = ((words >> np.uint64(60)) & np.uint64(1)).astype(np.uint8)
    if g_parts:
        ev.gps = np.sort(np.concatenate(g_parts), order="t_us")
    ev.events = sorted(records.values(), key=lambda r: (r.t_us, r.code))
    if not blocks:
        ev.warnings.append("header only: no data block survived")
    return ev


# ---- writing (simulator, tests) ---------------------------------------------

def _block(btype: int, count: int, t0: int, payload: bytes, seq: int) -> bytes:
    head = F.BLOCK_HDR.pack(F.BLOCK_MAGIC, btype, 0, count, len(payload), seq, t0)
    return head + payload + struct.pack("<I", F.crc32(head + payload))


def write_event(path, ev: BlackboxEvent, newest_first: bool = True) -> Path:
    """Write ``ev`` the way the firmware does (same blocks, same order)."""
    path = Path(path)
    flags = (F.HF_TIME_VALID if ev.time_valid else 0) | (F.HF_TIME_GPS if ev.time_from_gps else 0)
    head = F.FILE_HDR.pack(F.FILE_MAGIC, F.FILE_VERSION, F.FILE_HDR.size, ev.event_no, ev.boot_no,
                           ev.reason, len(F.ANALOG), len(F.EDGE), flags, F.SAMPLE_HZ, ev.pre_ms,
                           ev.post_ms, ev.trigger_t_us, ev.unix_us_at_t0, ev.trigger_value,
                           ev.fw.encode()[:11], ev.device.encode()[:11], ev.tach_ppr, ev.ign_ppr, 0, 0)
    head = head[:-4] + struct.pack("<I", F.crc32(head[:-4]))
    out = bytearray(head)
    index = []
    seq = 0

    def add(btype, count, t0, payload):
        nonlocal seq
        if btype not in (F.B_INDEX, F.B_END):
            index.append((len(out), btype, 0, count, t0))
        out.extend(_block(btype, count, t0, payload, seq))
        seq += 1

    def event_bytes(records):
        arr = np.zeros(len(records), dtype=F.EVENT_DTYPE)
        for i, r in enumerate(records):
            arr[i] = (r.t_us, r.code, r.src, r.action, r.value)
        return arr.tobytes()

    early = [r for r in ev.events if r.t_us <= ev.trigger_t_us]
    late = [r for r in ev.events if r.t_us > ev.trigger_t_us]
    add(F.B_EVENTS, len(early), early[0].t_us if early else ev.trigger_t_us, event_bytes(early))
    add(F.B_CONFIG, 0, ev.trigger_t_us, ev.config_text.encode("ascii", "replace"))

    words = (ev.edge_t_us.astype(np.uint64) | (ev.edge_ch.astype(np.uint64) << np.uint64(56))
             | (ev.edge_level.astype(np.uint64) << np.uint64(60))).astype("<u8")
    n = len(ev.t_us)
    n_pre = int(np.searchsorted(ev.t_us, ev.trigger_t_us, side="right"))
    chunks = [(max(0, hi - F.BLOCK_SAMPLES), hi) for hi in range(n_pre, 0, -F.BLOCK_SAMPLES)]
    if not newest_first:
        chunks.reverse()
    chunks += [(lo, min(n, lo + F.BLOCK_SAMPLES)) for lo in range(n_pre, n, F.BLOCK_SAMPLES)]
    for lo, hi in chunks:
        t0, t1 = int(ev.t_us[lo]), int(ev.t_us[hi - 1]) + F.TICK_US
        add(F.B_SAMPLES, hi - lo, t0, ev.samples[lo:hi].tobytes())
        if lo == 0:
            t0 = 0
        if hi == n:
            t1 = 1 << 62
        sel = words[(ev.edge_t_us >= t0) & (ev.edge_t_us < t1)]
        for k in range(0, len(sel), F.BLOCK_EDGES):
            part = sel[k:k + F.BLOCK_EDGES]
            add(F.B_EDGES, len(part), int(part[0]) & F.EDGE_T_MASK, part.tobytes())
    if late:
        add(F.B_EVENTS, len(late), late[0].t_us, event_bytes(late))
    if len(ev.gps):
        add(F.B_GPS, len(ev.gps), int(ev.gps["t_us"][0]), ev.gps.tobytes())
    idx = np.array(index, dtype=F.INDEX_DTYPE)
    index_off = len(out)
    t_lo = int(ev.t_us[0]) if n else ev.trigger_t_us
    t_hi = int(ev.t_us[-1]) + F.TICK_US if n else ev.trigger_t_us
    add(F.B_INDEX, len(idx), t_lo, idx.tobytes())
    add(F.B_END, 1, t_hi, F.END_REC.pack(index_off, len(idx), F.END_COMPLETE, n))
    path.write_bytes(out)
    return path


# ---- CSV export -------------------------------------------------------------

CSV_COLUMNS = ("time_s", "vbat_v", "ecub_v", "pump_v", "ig1_v", "alt_v",
               "vbat_min", "ecub_min", "pump_min", "ig1_min",
               "vbat_max", "ecub_max", "pump_max", "ig1_max",
               "rpm_tach", "rpm_ign", "inj_pw_ms", "inj_duty_pct",
               "tach", "ign", "inj", "button", "ig1_on", "running", "gap")


def export_csv(ev: BlackboxEvent, path, with_events: bool = True) -> Path:
    """Write the 1 kHz record as CSV, same columns as the firmware's own
    ``EVnnnnnn.CSV``. ``time_s`` is device time. With ``with_events`` a
    comment header lists the trigger and every event."""
    path = Path(path)
    s = ev.samples
    with open(path, "w", newline="", encoding="utf-8") as fh:
        if with_events:
            fh.write(f"# black box event {ev.event_no}: {ev.reason_name} ({ev.reason_text})\n")
            fh.write(f"# trigger_time_s: {ev.trigger_t_us / 1e6:.6f}\n")
            if ev.trigger_wall:
                fh.write(f"# trigger_wall_time: {ev.trigger_wall.isoformat(timespec='milliseconds')}\n")
            for r in ev.events:
                fh.write(f"# event: {r.t_us / 1e6:.6f} {r.name} value={r.value}\n")
            for w in ev.warnings:
                fh.write(f"# warning: {w}\n")
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        dig = s["dig"]
        for i in range(len(s)):
            r = s[i]
            d = int(dig[i])
            w.writerow([f"{ev.t_us[i] // 1000000}.{ev.t_us[i] // 1000 % 1000:03d}",
                        *(f"{v / 1000:.3f}" for v in r["mv"]),
                        *(f"{v / 10:.1f}" for v in r["min_dv"]),
                        *(f"{v / 10:.1f}" for v in r["max_dv"]),
                        int(r["rpm_tach"]), int(r["rpm_ign"]), f"{r['inj_pw_us'] / 1000:.3f}",
                        f"{r['inj_duty'] / 2:.1f}",
                        *(1 if d & bit else 0 for bit in (F.DIG_TACH, F.DIG_IGN, F.DIG_INJ,
                                                          F.DIG_BUTTON, F.DIG_IG1_ON,
                                                          F.DIG_RUNNING, F.DIG_GAP))])
    return path
