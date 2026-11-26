"""The handheld's serial link (firmware/handheld/SERIAL.md): frame types and
payload layouts, mirroring ``components/hhcore/include/hh_serial.h`` byte for
byte (``tests/test_hh_serial.py`` checks them against the compiled C).

The frame is the black box's: ``COBS(ver type seq payload crc16) 0x00``. The
COBS, CRC and deframer are ``hondalog.blackbox.format``'s, used as they are.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from ..blackbox.format import Deframer, cobs_decode, cobs_encode, crc16, frame_encode
from . import format as F

REV = 1
MAX_PAYLOAD = 512
CHUNK = 480
HELLO_S = 2.0
HOST_S = 3.0
MAGIC = b"HHSL"
BAUDS = (921600, 460800, 115200)        # tried in this order when the port stays unreadable

# handheld -> laptop
(SF_HELLO, SF_READ, SF_TICK, SF_LINK, SF_GPS, SF_EVENT, SF_STATS, SF_LOG, SF_ISO, SF_TIME_RSP, SF_ACK,
 SF_FILE_INFO, SF_FILE_DATA, SF_MAPUP) = range(0x40, 0x4E)
# laptop -> handheld (two-way only)
(SC_GET_HELLO, SC_TIME_REQ, SC_TIME_SET, SC_MARK, SC_ACK_ALARM, SC_DRIVE, SC_LIST_FILES, SC_READ_FILE,
 SC_PUT_BEGIN, SC_PUT_DATA, SC_PUT_END, SC_DTC, SC_MAPUP_OPEN, SC_MAPUP_WRITE, SC_MAPUP_STATUS,
 SC_MAPUP_COMMIT) = range(0x60, 0x70)
# laptop -> handheld: the laptop's own ECU reads relayed (two-way, HELLO capability CAP_RELAY)
SR_RELAY_READ, SR_RELAY_ISO, SR_RELAY_TICK, SR_RELAY_LINK = range(0x50, 0x54)
FRAME_NAMES = {SF_HELLO: "hello", SF_READ: "read", SF_TICK: "tick", SF_LINK: "link", SF_GPS: "gps",
               SF_EVENT: "event", SF_STATS: "stats", SF_LOG: "log", SF_ISO: "iso", SF_TIME_RSP: "time_rsp",
               SF_ACK: "ack", SF_FILE_INFO: "file_info", SF_FILE_DATA: "file_data", SF_MAPUP: "mapup"}

VARIANT_DEFAULT, VARIANT_GPS_P1 = 0, 1
VARIANTS = {VARIANT_DEFAULT: "default", VARIANT_GPS_P1: "gps-p1"}
CAP_RX, CAP_FILES, CAP_PUT, CAP_LOG, CAP_MAPUP, CAP_HL = 1, 2, 4, 8, 0x10, 0x20      # CAP_HL: the HL text session
CAP_RELAY = 0x40                        # RELAY_* frames are understood (HH_SER_CAP_RELAY)
F_DRIVE, F_CAPTURE, F_MAP, F_CFG = 1, 2, 3, 4
FILE_KINDS = {F_DRIVE: "drive", F_CAPTURE: "capture", F_MAP: "map", F_CFG: "cfg"}
OK, IGNORED, E_ARG, E_IO, E_CRC, E_STATE, E_REFUSED, E_BUSY, E_UNSUPPORTED, E_OFFSET = 0, 1, -1, -2, -3, -4, -5, -6, -7, -8
STATUS_TEXT = {OK: "ok", IGNORED: "ignored", E_ARG: "bad argument", E_IO: "card error", E_CRC: "checksum mismatch",
               E_STATE: "not expected now", E_REFUSED: "refused: the handheld could not load it",
               E_BUSY: "busy", E_UNSUPPORTED: "not supported (no card, or an older firmware)",
               E_OFFSET: "wrong offset"}
LINK_OFF, LINK_CONNECTING, LINK_OK, LINK_ISO_ONLY, LINK_ERROR, LINK_RELAY = range(6)
LINK_NAMES = {LINK_OFF: "no reply from the ECU", LINK_CONNECTING: "connecting", LINK_OK: "ok",
              LINK_ISO_ONLY: "OBD2 only", LINK_ERROR: "K-line cable not found", LINK_RELAY: "via laptop"}
# where the handheld's wall clock comes from (STATS time_source)
TS_NONE, TS_NMEA, TS_PPS, TS_HOST = range(4)
TS_NAMES = {TS_NONE: "none", TS_NMEA: "GPS sentences", TS_PPS: "GPS PPS", TS_HOST: "set by the laptop"}
ST_SD_OK, ST_RECORDING, ST_GPS_FIX, ST_TIME, ST_ARMED, ST_CAPTURING, ST_HOST, ST_BUILTIN = (1 << i for i in range(8))
FI_OPEN, FI_MORE = 1, 2
FD_EOF, FD_BUSY = 1, 2
NO_FILE = 0xFFFFFFFF
MAPUP_CHUNK = MAX_PAYLOAD - 8            # most file bytes in one MAPUP_WRITE
MAPUP_FINAL, MAPUP_ABORT = 1, 2         # MAPUP_COMMIT flags
MAPUP_IDLE_S = 10.0                     # silent this long: the handheld's map reader gets its files back (the upload stays)
MAPUP_SIZE_MAX = 128 << 20

HELLO = struct.Struct("<4sBBH12s12s12s8s10sBBBBBBIIQqHH")       # 96
READ = struct.Struct("<QBBH")                                   # 12, then len bytes
TICK = struct.Struct("<QHHI")                                   # 16
LINK = struct.Struct("<QBBBB")                                  # 12
ISO = struct.Struct("<QBBBB")                                   # 12, then len bytes
STATS = struct.Struct("<Qq6IHBB7I6I")                           # 96
STATS_EXT = struct.Struct("<B3xI")                              # 8, appended later: time_source, time_err_us
TIME_RSP = struct.Struct("<qQQ")                                # 24
ACK = struct.Struct("<BBhI")                                    # 8
FILE_INFO = struct.Struct("<BBHII")                             # 12
FILE_DATA = struct.Struct("<BBHIII")                            # 16, then data
LIST = struct.Struct("<B3xI")                                   # 8
READ_FILE = struct.Struct("<BxHII")                             # 12
PUT_BEGIN = struct.Struct("<B3xII")                             # 12
MAPUP_OPEN = struct.Struct("<BBxxII")                           # 12, then the name ("MAP/MAP.IDX")
MAPUP_WRITE = struct.Struct("<II")                              # 8, then the data: offset, crc32 of the data
MAPUP_COMMIT = struct.Struct("<B3x")                            # 4
MAPUP_INFO = struct.Struct("<BxxxIII16s")                       # 32: state, got, size, crc32, name (answer to STATUS)
RELAY_READ = struct.Struct("<QBBH")                             # 12, then len raw ECU bytes
RELAY_ISO = struct.Struct("<QBBBB")                             # 12, then len bytes
RELAY_TICK = struct.Struct("<QHHI")                             # 16
RELAY_LINK = struct.Struct("<QBBBB")                            # 12, then up to 10 bytes ECU id
RELAY_LEN_MAX = 64                                              # longer reads are refused by the handheld
MU_NONE, MU_OPEN, MU_PAUSED = 0, 1, 2
assert HELLO.size == 96 and STATS.size == 96 and READ.size == 12 and TICK.size == 16 and LINK.size == 12
assert ACK.size == 8 and FILE_INFO.size == 12 and FILE_DATA.size == 16 and LIST.size == 8
assert READ_FILE.size == 12 and PUT_BEGIN.size == 12 and TIME_RSP.size == 24
assert RELAY_READ.size == 12 and RELAY_ISO.size == 12 and RELAY_TICK.size == 16 and RELAY_LINK.size == 12
assert MAPUP_OPEN.size == 12 and MAPUP_WRITE.size == 8 and MAPUP_COMMIT.size == 4 and MAPUP_INFO.size == 32


@dataclass
class Hello:
    rev: int
    variant: int
    caps: int
    fw: str
    device: str
    map_hash: str
    map_sha: bytes
    ecu_id: bytes
    link_state: int
    backend: int
    link_mode: int
    n_ch: int
    flags: int
    boot_no: int
    baud: int
    t_us: int
    unix_us: int
    hello_ms: int
    max_payload: int

    @property
    def two_way(self) -> bool:
        return bool(self.caps & CAP_RX)

    @property
    def variant_name(self) -> str:
        return VARIANTS.get(self.variant, f"variant {self.variant}")

    @property
    def builtin_map(self) -> bool:
        return bool(self.flags & 1)

    @property
    def clock_from_pps(self) -> bool:
        """The handheld's wall clock (unix_us) is held to the GPS pulse: good to tens of microseconds."""
        return bool(self.flags & 16)

    @property
    def text(self) -> str:
        way = "two-way" if self.two_way else "tx-only"
        return (f"handheld {self.device or '?'} fw {self.fw}, {self.variant_name}, {way}, "
                f"map #{self.map_hash}{' (built in)' if self.builtin_map else ''}, boot {self.boot_no}")

    def pack(self) -> bytes:
        return HELLO.pack(MAGIC, self.rev, self.variant, self.caps, F.fit(self.fw, 12), F.fit(self.device, 12),
                          hash12(self.map_hash), self.map_sha[:8].ljust(8, b"\0"),
                          self.ecu_id[:10].ljust(10, b"\0"), min(len(self.ecu_id), 10), self.link_state,
                          self.backend, self.link_mode, self.n_ch, self.flags, self.boot_no, self.baud, self.t_us,
                          self.unix_us, self.hello_ms, self.max_payload)


def _u16(x: float) -> int:
    return max(0, min(0xFFFF, int(round(x))))


def _u64(x: float) -> int:
    return max(0, int(x)) & 0xFFFFFFFFFFFFFFFF


def pack_relay_read(host_us: int, addr: int, data: bytes, latency_us: float) -> bytes:
    """RELAY_READ: one Honda block read on the laptop (``host_us`` when the request went out)."""
    return RELAY_READ.pack(_u64(host_us), addr & 0xFF, len(data), _u16(latency_us)) + bytes(data)


def pack_relay_iso(host_us: int, pid: int, data: bytes, mode: int = 0x41, flags: int = 0) -> bytes:
    """RELAY_ISO: one OBD2 reply (Mode 01 = 0x41) in OBD2-only mode."""
    return RELAY_ISO.pack(_u64(host_us), mode & 0xFF, pid & 0xFF, len(data), flags & 0xFF) + bytes(data)


def pack_relay_tick(host_us: int, latency_us: float, hz: float, n: int) -> bytes:
    """RELAY_TICK: one laptop sample done; ``hz`` is the sample rate (sent x 100)."""
    return RELAY_TICK.pack(_u64(host_us), _u16(latency_us), _u16(hz * 100), n & 0xFFFFFFFF)


def pack_relay_link(host_us: int, state: int, up: bool, iso: bool, ecu_id: bytes = b"") -> bytes:
    """RELAY_LINK: the laptop's link state (LINK_OFF / CONNECTING / OK / ISO_ONLY)."""
    ecu = bytes(ecu_id or b"")[:10]
    return RELAY_LINK.pack(_u64(host_us), state & 0xFF, 1 if up else 0, 1 if iso else 0, len(ecu)) + ecu


def hash12(text: str) -> bytes:
    """A map hash as the firmware keeps it: all 12 characters, no terminator."""
    return text.encode("ascii", "replace")[:12].ljust(12, b"\0")


def parse_hello(p: bytes) -> Hello | None:
    """None if this is not a handheld's hello."""
    if len(p) < HELLO.size or p[:4] != MAGIC:
        return None
    (_, rev, variant, caps, fw, device, map_hash, sha, ecu, ecu_len, state, backend, mode, n_ch, flags, boot,
     baud, t_us, unix_us, hello_ms, max_payload) = HELLO.unpack(p[:HELLO.size])
    return Hello(rev, variant, caps, F.cstr(fw), F.cstr(device), F.cstr(map_hash), sha, ecu[:min(ecu_len, 10)],
                 state, backend, mode, n_ch, flags, boot, baud, t_us, unix_us, hello_ms, max_payload)


STATS_FIELDS = ("t_us", "unix_us", "reads", "errors", "timeouts", "checksum_failures", "retries", "reconnects",
                "hz_c", "link_state", "flags", "drive_no", "log_bytes", "free_mb", "markers", "stall_count",
                "nmea_good", "nmea_bad", "tx_frames", "tx_dropped", "tx_bytes", "rx_frames", "rx_bad", "ring_peak")


def parse_stats(p: bytes) -> dict[str, int] | None:
    """The STATS fields. A frame from older firmware (96 bytes) has time_source 0 and time_err_us 0, which
    read as "not told": use the hello's flags for the clock then."""
    if len(p) < STATS.size:
        return None
    out = dict(zip(STATS_FIELDS, STATS.unpack(p[:STATS.size])))
    ext = STATS_EXT.unpack(p[STATS.size:STATS.size + STATS_EXT.size]) if len(p) >= STATS.size + STATS_EXT.size else (0, 0)
    out["time_source"], out["time_err_us"] = ext
    return out


def pack_stats(**fields: int) -> bytes:
    """A STATS payload; the appended time fields only when time_source or time_err_us is given."""
    body = STATS.pack(*(int(fields.get(name, 0)) for name in STATS_FIELDS))
    if "time_source" in fields or "time_err_us" in fields:
        body += STATS_EXT.pack(int(fields.get("time_source", 0)), int(fields.get("time_err_us", 0)))
    return body


def time_text(source: int, err_us: int) -> str:
    """The Link screen's wording: "PPS ±20µs", "GPS ±1s", "PC", "none"."""
    if source == TS_PPS:
        return f"PPS ±{err_us / 1000:g}ms" if err_us >= 1000 else f"PPS ±{err_us}µs"
    return {TS_NMEA: "GPS ±1s", TS_HOST: "PC"}.get(source, "none")


_MAPUP_NAME = re.compile(r"MAP/[A-Z0-9_~$-]{1,8}(\.[A-Z0-9_~$-]{0,3})?")


def mapup_name_ok(name: str) -> bool:
    """A name the handheld takes for a MAP/ upload: ``MAP/`` and an 8.3 name in capitals. Nothing else in the
    card, no folders, no ``..``; ``MAP/UPLOAD.TMP`` is the handheld's own."""
    return _MAPUP_NAME.fullmatch(name) is not None and name != "MAP/UPLOAD.TMP"


def status_text(status: int) -> str:
    return STATUS_TEXT.get(status, f"error {status}")


__all__ = ["Deframer", "cobs_decode", "cobs_encode", "crc16", "frame_encode"] + [n for n in dir() if n.isupper()] + [
    "Hello", "hash12", "mapup_name_ok", "pack_relay_iso", "pack_relay_link", "pack_relay_read", "pack_relay_tick", "parse_hello", "parse_stats", "pack_stats", "status_text", "time_text"]
