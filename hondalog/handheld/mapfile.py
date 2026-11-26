"""HANDHELD.MAP: the address map compiled for the handheld.

The handheld reads the *core* (header and sections: channels with their
formulas as byte code, alarm rules, link timing). After the core comes the
map's TOML text, which the handheld skips and the importer uses: a drive
recorded with this file can be decoded on any laptop, with the very map that
was on the card, and gets the same ``address_map_hash`` as a laptop
recording made with that map.

Layout: firmware/handheld/FORMAT.md.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import addressmap
from ..addressmap import AddressMap
from ..alarms import AlarmRule
from . import format as F
from .bytecode import Pool, Unsupported, compile_condition, compile_formula

MAP_NAME = "HANDHELD.MAP"
CFG_NAME = "HANDHELD.CFG"
NAN = float("nan")

DEFAULT_CFG = """\
# hondalog handheld settings. Edit, save, restart the handheld.
# Lines that are missing or wrong fall back to the value shown here.
backend = auto          # usb: KKL cable on the USB socket.  uart: L9637D on the UART.  auto: usb
gps_baud = 9600         # 0 = no GPS
iso_fallback = 1        # try plain OBD2 (ISO 9141-2) when Honda reads stay unanswered
iso_after_s = 12        # ... for this long (an ISO session left open lasts 5 s)
drive_gap_s = 120       # ECU silent this long ends the drive
sync_ms = 1000          # the card is synced this often: the most a power cut can take
block_ms = 250          # a log block is closed after this long
prealloc_mb = 16        # space taken for a drive file at its start (about 3 hours); 0 = none
stall_pre_s = -1        # seconds kept before a stall; -1 = what the address map says (30)
stall_post_s = -1       # seconds after; -1 = the map's (10)
capture_files = 1       # also write CAPTURES/Cnnnnnn.HHD at a stall
sleep_after_s = 300     # sleep this long after the drive ended; 0 = never (use with a switched supply)
sleep_probe_s = 20      # asleep: wake this often and ask the ECU
usb_latency_ms = 1      # FTDI latency timer
utc_offset_min = 0      # clock on the screen only; files are UTC
serial = 1              # the link to the laptop on the USB-C port; 0 = off (plain log console)
serial_baud = 921600    # its speed; try 460800 if the laptop sees damaged frames
# rotate = 180          # display: 0 or 180 (upside down); unset = the firmware's built-in default
"""


@dataclass
class MapFile:
    """A parsed HANDHELD.MAP."""
    map_hash: str
    map_sha256: bytes
    version: str
    variant: str
    core: bytes
    source: str = ""                    # the TOML text, "" if the file carries none
    channels: list[dict[str, Any]] = field(default_factory=list)
    alarms: list[dict[str, Any]] = field(default_factory=list)
    stall: dict[str, Any] = field(default_factory=dict)
    protocol: dict[str, Any] = field(default_factory=dict)
    constants: list[float] = field(default_factory=list)
    programs: bytes = b""
    ecu_id_addr: int | None = None
    ecu_id_len: int = 0

    def address_map(self) -> AddressMap | None:
        """The map this file was made from, if its text is inside."""
        return addressmap.loads(self.source, variant=self.variant or None) if self.source else None


@dataclass
class ExportReport:
    channels: list[str] = field(default_factory=list)
    math: list[str] = field(default_factory=list)
    alarms: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # "name: why", one line each
    core_bytes: int = 0
    total_bytes: int = 0
    map_hash: str = ""


def _section(kind: int, count: int, body: bytes) -> bytes:
    return F.MAP_SEC.pack(kind, 0, count, len(body)) + body


def _f32(value: float | None) -> float:
    return NAN if value is None else float(value)


def _limits(rules: list[AlarmRule], channel: str) -> tuple[float, float, float, float]:
    """(warn_lo, warn_hi, alarm_lo, alarm_hi) of the rules on one channel: the
    limit a value crosses first on its way out."""
    warn_lo = warn_hi = alarm_lo = alarm_hi = NAN
    for rule in rules:
        if rule.channel != channel:
            continue
        critical = rule.severity == "critical"
        if rule.low is not None:
            if critical:
                alarm_lo = rule.low if math.isnan(alarm_lo) else max(alarm_lo, rule.low)
            else:
                warn_lo = rule.low if math.isnan(warn_lo) else max(warn_lo, rule.low)
        if rule.high is not None:
            if critical:
                alarm_hi = rule.high if math.isnan(alarm_hi) else min(alarm_hi, rule.high)
            else:
                warn_hi = rule.high if math.isnan(warn_hi) else min(warn_hi, rule.high)
    return warn_lo, warn_hi, alarm_lo, alarm_hi


def build_map(amap: AddressMap, source: str | None = None, *, stall: dict[str, float] | None = None,
              report: ExportReport | None = None) -> bytes:
    """Compile ``amap`` into the bytes of a HANDHELD.MAP.

    ``source``: the map's TOML text (default: read from ``amap.path``); it is
    appended so the importer can decode with exactly this map. ``stall``:
    rpm_run, rpm_zero, link_down_s, pre_s, post_s (defaults: the laptop's
    stall rules). Raises ``MapError`` if a channel formula cannot be compiled:
    a channel is never dropped silently, the read plan depends on it.
    """
    report = report if report is not None else ExportReport()
    if source is None:
        try:
            source = Path(amap.path).read_text(encoding="utf-8") if amap.path else ""
        except OSError:
            source = ""
    pool = Pool()
    programs = bytearray()
    constants = amap.constants

    def add(program: bytes) -> tuple[int, int]:
        at = programs.find(program)             # identical formulas share their bytes
        if at < 0:
            at = len(programs)
            programs.extend(program)
        if at + len(program) > 0xFFFF:
            raise addressmap.MapError("the formulas do not fit a handheld map (64 KB of programs)")
        return at, len(program)

    measured = list(amap.channels)
    if len(measured) > F.MAX_CH:
        raise addressmap.MapError(f"the handheld takes {F.MAX_CH} channels, the map has {len(measured)}")
    records: list[list[Any]] = []
    index: dict[str, int] = {}
    enabled_rules = [r for r in amap.alarms if r.enabled]
    for ch in measured:
        try:
            program = compile_formula(ch.formula, pool, constants, ch.length)
        except Unsupported as exc:
            raise addressmap.MapError(f"{ch.name}: formula {ch.formula!r} cannot run on the handheld: {exc}") from exc
        off, length = add(program)
        index[ch.name] = len(records)
        records.append([ch.name, ch.label, ch.units, float(ch.min), float(ch.max), *_limits(enabled_rules, ch.name),
                        max(0, min(9, int(ch.decimals))), 1 if ch.verified else 0, F.RATE_CLASS[ch.rate],
                        F.KIND_FLAG if ch.kind == "flag" else F.KIND_VALUE, ch.addr, ch.length,
                        1 if ch.endian == "little" else 0, 0, off, length])
        report.channels.append(ch.name)

    # math channels, inputs first (the engine's order); one that needs history is left out,
    # and so is everything built on it
    engine = amap.math_engine()
    by_name = {m.name: m for m in amap.math}
    for name in engine.names:
        m = by_name[name]
        try:
            if len(records) >= F.MAX_CH:
                raise Unsupported(f"no room: the handheld takes {F.MAX_CH} channels")
            program, _ = compile_condition(m.expr, pool, index, constants, amap.obd2_names)
        except Unsupported as exc:
            report.skipped.append(f"math {name}: {exc}")
            continue
        off, length = add(program)
        index[name] = len(records)
        inputs = engine.base_inputs(name)
        slowest = max((F.RATE_CLASS[c.rate] for c in measured if c.name in inputs), default=2)
        records.append([name, m.label, m.units, float(m.min), float(m.max), *_limits(enabled_rules, name),
                        max(0, min(9, int(m.decimals))), 1 if amap.is_verified(name) else 0, slowest,
                        F.KIND_MATH, 0, 0, 0, 0, off, length])
        report.math.append(name)

    alarm_records: list[bytes] = []
    slopes = 0
    for rule in enabled_rules:
        try:
            if len(alarm_records) >= F.MAX_ALARMS:
                raise Unsupported(f"no room: the handheld takes {F.MAX_ALARMS} alarms")
            if rule.channel and rule.channel not in index:
                raise Unsupported(f"its channel {rule.channel!r} is not on the handheld")
            if rule.uses_rate:
                if slopes >= F.MAX_SLOPES:
                    raise Unsupported(f"no room: the handheld takes {F.MAX_SLOPES} rate alarms")
            spans = []
            for text in (rule.when, rule.clear_when, rule.gate):
                if text.strip():
                    program, _ = compile_condition(text, pool, index, constants, amap.obd2_names)
                    spans += add(program)
                else:
                    spans += (0, 0)
        except Unsupported as exc:
            report.skipped.append(f"alarm {rule.name}: {exc}")
            continue
        slopes += 1 if rule.uses_rate else 0
        flags = ((F.AF_LATCHING if rule.latching else 0) | (F.AF_LOW if rule.low is not None else 0)
                 | (F.AF_HIGH if rule.high is not None else 0)
                 | (F.AF_RATE_LOW if rule.rate_low is not None else 0)
                 | (F.AF_RATE_HIGH if rule.rate_high is not None else 0)
                 | (F.AF_MARKER if "marker" in rule.actions else 0)
                 | (F.AF_CAPTURE if "capture" in rule.actions else 0)
                 | (F.AF_TILE if "tile" in rule.actions else 0))
        alarm_records.append(F.MAP_ALARM.pack(
            F.fit(rule.name, 16), F.fit(rule.label, 24), index.get(rule.channel, -1) if rule.channel else -1,
            2 if rule.severity == "critical" else 1, flags, max(0, min(255, int(rule.priority or 0))), b"",
            _f32(rule.low), _f32(rule.high), float(rule.hysteresis), float(rule.hold_s),
            float(rule.clear_hold_s), _f32(rule.rate_low), _f32(rule.rate_high), float(rule.rate_window_ms),
            *spans))
        report.alarms.append(rule.name)

    fr = amap.framing
    if fr.request_len != 5:
        raise addressmap.MapError("the handheld only sends 5-byte read requests (protocol.request_len)")
    if len(fr.init_sequence) > 16:
        raise addressmap.MapError("protocol.init_sequence is longer than 16 bytes")
    proto = F.MAP_PROTO.pack(
        fr.baud, fr.cmd_read, fr.request_len, fr.reply_header, fr.reply_len_extra, min(fr.max_read_len, F.MAX_READ),
        1 if fr.adaptive_timing else 0, max(0, min(5, int(fr.retries))), len(fr.init_sequence),
        bytes(fr.init_sequence).ljust(16, b"\0"), b"", fr.init_delay_s, fr.reply_timeout_s,
        fr.inter_request_delay_s, fr.min_reply_timeout_s, fr.inter_byte_timeout_s, fr.max_inter_request_delay_s,
        fr.turnaround_s, fr.rate_fast_hz, fr.rate_medium_hz, fr.rate_slow_hz)
    opts = {"rpm_run": 400.0, "rpm_zero": 50.0, "link_down_s": 1.0, "pre_s": 30.0, "post_s": 10.0,
            "rpm_channel": "rpm", **(stall or {})}
    stall_body = F.MAP_STALL.pack(opts["rpm_run"], opts["rpm_zero"], opts["link_down_s"], opts["pre_s"],
                                  opts["post_s"], index.get(str(opts["rpm_channel"]), -1), 0)
    info = F.MAP_INFO.pack(F.fit(amap.version, 48), F.fit(amap.variant, 12),
                           -1 if amap.ecu_id_addr is None else amap.ecu_id_addr,
                           min(10, amap.ecu_id_len) if amap.ecu_id_addr is not None else 0, 0)
    chan_body = b"".join(F.MAP_CHAN.pack(F.fit(r[0], 16), F.fit(r[1], 20), F.fit(r[2], 8), *r[3:]) for r in records)
    body = (_section(F.MS_INFO, 1, info) + _section(F.MS_PROTO, 1, proto)
            + _section(F.MS_CONST, len(pool.values), struct.pack(f"<{len(pool.values)}d", *pool.values))
            + _section(F.MS_CHAN, len(records), chan_body) + _section(F.MS_PROG, 0, bytes(programs))
            + _section(F.MS_ALARM, len(alarm_records), b"".join(alarm_records))
            + _section(F.MS_STALL, 1, stall_body))
    core_len = F.MAP_HDR.size + len(body)
    if core_len > F.MAP_MAX_CORE:
        raise addressmap.MapError(f"the compiled map is {core_len} bytes; the handheld reads {F.MAP_MAX_CORE}")
    src = source.encode("utf-8")
    sha = hashlib.sha256(src).digest() if src else bytes(32)
    map_hash = amap.version_hash.encode("ascii")[:12].ljust(12, b"\0")

    def header(crc: int) -> bytes:
        return F.MAP_HDR.pack(F.MAP_MAGIC, F.MAP_SCHEMA, F.MAP_HDR.size, core_len, crc, map_hash, sha, len(src))
    core = header(F.crc32(header(0) + body)) + body
    report.core_bytes = len(core)
    report.map_hash = amap.version_hash
    out = core + (src + struct.pack("<I", F.crc32(src)) if src else b"")
    report.total_bytes = len(out)
    return out


def read_map(data: bytes | str | Path) -> MapFile:
    """Parse a HANDHELD.MAP (bytes or a path). Raises ValueError."""
    if not isinstance(data, (bytes, bytearray)):
        data = Path(data).read_bytes()
    data = bytes(data)
    if len(data) < F.MAP_HDR.size:
        raise ValueError("not a handheld map: too short")
    magic, schema, hdr_len, core_len, crc, map_hash, sha, src_len = F.MAP_HDR.unpack_from(data)
    if magic != F.MAP_MAGIC or hdr_len != F.MAP_HDR.size:
        raise ValueError("not a handheld map")
    if schema != F.MAP_SCHEMA:
        raise ValueError(f"handheld map schema {schema}; this hondalog reads {F.MAP_SCHEMA}")
    if core_len > len(data) or core_len < hdr_len:
        raise ValueError("handheld map is cut short")
    zeroed = data[:12] + b"\0\0\0\0" + data[16:core_len]
    if F.crc32(zeroed) != crc:
        raise ValueError("handheld map: checksum is wrong")
    out = MapFile(F.cstr(map_hash), sha, "", "", data[:core_len])
    pos = hdr_len
    while pos + F.MAP_SEC.size <= core_len:
        kind, _, count, length = F.MAP_SEC.unpack_from(data, pos)
        pos += F.MAP_SEC.size
        body = data[pos:pos + length]
        pos += length
        if kind == F.MS_INFO:
            version, variant, id_addr, id_len, _ = F.MAP_INFO.unpack(body)
            out.version, out.variant = F.cstr(version), F.cstr(variant)
            out.ecu_id_addr, out.ecu_id_len = (None if id_addr < 0 else id_addr), id_len
        elif kind == F.MS_PROTO:
            v = F.MAP_PROTO.unpack(body)
            out.protocol = {"baud": v[0], "cmd_read": v[1], "request_len": v[2], "reply_header": v[3],
                            "reply_len_extra": v[4], "max_read_len": v[5], "adaptive_timing": bool(v[6]),
                            "retries": v[7], "init_sequence": v[9][:v[8]], "init_delay_s": v[11],
                            "reply_timeout_s": v[12], "inter_request_delay_s": v[13],
                            "min_reply_timeout_s": v[14], "inter_byte_timeout_s": v[15],
                            "max_inter_request_delay_s": v[16], "turnaround_s": v[17],
                            "rate_fast_hz": v[18], "rate_medium_hz": v[19], "rate_slow_hz": v[20]}
        elif kind == F.MS_CONST:
            out.constants = list(struct.unpack(f"<{count}d", body))
        elif kind == F.MS_CHAN:
            for i in range(count):
                v = F.MAP_CHAN.unpack_from(body, i * F.MAP_CHAN.size)
                out.channels.append({
                    "name": F.cstr(v[0]), "label": F.cstr(v[1]), "units": F.cstr(v[2]), "lo": v[3], "hi": v[4],
                    "warn_lo": v[5], "warn_hi": v[6], "alarm_lo": v[7], "alarm_hi": v[8], "decimals": v[9],
                    "verified": bool(v[10]), "rate_class": v[11], "kind": v[12], "addr": v[13], "length": v[14],
                    "little_endian": bool(v[15]), "prog_off": v[17], "prog_len": v[18]})
        elif kind == F.MS_PROG:
            out.programs = body
        elif kind == F.MS_ALARM:
            for i in range(count):
                v = F.MAP_ALARM.unpack_from(body, i * F.MAP_ALARM.size)
                out.alarms.append({
                    "name": F.cstr(v[0]), "label": F.cstr(v[1]), "channel": v[2], "severity": v[3],
                    "flags": v[4], "priority": v[5], "low": v[7], "high": v[8], "hysteresis": v[9],
                    "hold_s": v[10], "clear_hold_s": v[11], "rate_low": v[12], "rate_high": v[13],
                    "rate_window_ms": v[14], "when": (v[15], v[16]), "clear_when": (v[17], v[18]),
                    "gate": (v[19], v[20])})
        elif kind == F.MS_STALL:
            v = F.MAP_STALL.unpack(body)
            out.stall = {"rpm_run": v[0], "rpm_zero": v[1], "link_down_s": v[2], "pre_s": v[3], "post_s": v[4],
                         "rpm_channel": v[5]}
    if src_len:
        src = data[core_len:core_len + src_len]
        tail = data[core_len + src_len:core_len + src_len + 4]
        if len(src) == src_len and len(tail) == 4 and struct.unpack("<I", tail)[0] == F.crc32(src) \
                and hashlib.sha256(src).digest() == sha:
            out.source = src.decode("utf-8")
    return out


def export_map(amap: AddressMap, folder: str | Path, *, source: str | None = None,
               stall: dict[str, float] | None = None, write_cfg: bool = True) -> ExportReport:
    """Write HANDHELD.MAP into ``folder`` (the SD card), and HANDHELD.CFG with
    the defaults if there is none yet. The map is written to a temporary name
    and renamed, so a card pulled half-way keeps the old one."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report = ExportReport()
    data = build_map(amap, source, stall=stall, report=report)
    tmp = folder / "HANDHELD.TMP"
    tmp.write_bytes(data)
    tmp.replace(folder / MAP_NAME)
    cfg = folder / CFG_NAME
    if write_cfg and not cfg.exists():
        cfg.write_text(DEFAULT_CFG, encoding="utf-8", newline="\n")
    for sub in ("DRIVES", "CAPTURES"):
        (folder / sub).mkdir(exist_ok=True)
    return report


def c_array(amap: AddressMap, source: str | None = None) -> str:
    """The core of a map as a C initialiser: the firmware's built-in map
    (components/hhcore/port/hh_default_map.inc), used when the card has no
    HANDHELD.MAP. The TOML text is left out; the header still carries its
    sha256 and the map hash."""
    data = build_map(amap, source)
    core_len = F.MAP_HDR.unpack_from(data)[3]
    core = data[:core_len]
    lines = ["/* Generated by `hondalog-handheld export-map --c-array` from hondalog/data/addressmap.toml.",
             f" * Map hash {amap.version_hash}. Do not edit: tests/test_hh_map.py checks it is current. */"]
    for i in range(0, len(core), 16):
        lines.append("    " + " ".join(f"0x{b:02x}," for b in core[i:i + 16]))
    return "\n".join(lines) + "\n"
