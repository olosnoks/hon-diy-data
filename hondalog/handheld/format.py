"""Byte layouts shared with the firmware (components/hhcore/include/hhcore.h).
Everything is little-endian and packed. FORMAT.md is the description."""

from __future__ import annotations

import struct
import zlib

# ---- HANDHELD.MAP -----------------------------------------------------------

MAP_MAGIC = 0x504D4848          # "HHMP"
MAP_SCHEMA = 1
MAP_MAX_CORE = 32768
MAP_HDR = struct.Struct("<IHHII12s32sI")                # 64
MAP_SEC = struct.Struct("<BBHI")                        # 8
MAP_INFO = struct.Struct("<48s12shBB")                  # 64
MAP_PROTO = struct.Struct("<IBBhBBBBB16s3s10d")         # 112
MAP_CHAN = struct.Struct("<16s20s8s6fBBBBBBBBHH")       # 80
MAP_ALARM = struct.Struct("<16s24shBBB3s8f6H")          # 92
MAP_STALL = struct.Struct("<5fhH")                      # 24
MS_INFO, MS_PROTO, MS_CONST, MS_CHAN, MS_PROG, MS_ALARM, MS_STALL = 1, 2, 3, 4, 5, 6, 7

MAX_CH = 48
MAX_ALARMS = 24
MAX_CONST = 256
MAX_SLOPES = 4
MAX_READ = 64
EXPR_STACK = 32

KIND_VALUE, KIND_FLAG, KIND_MATH = 0, 1, 2
RATE_CLASS = {"fast": 0, "medium": 1, "slow": 2}

AF_LATCHING, AF_LOW, AF_HIGH, AF_RATE_LOW, AF_RATE_HIGH = 0x01, 0x02, 0x04, 0x08, 0x10
AF_MARKER, AF_CAPTURE, AF_TILE = 0x20, 0x40, 0x80

# ---- expression byte code ---------------------------------------------------

OP_END, OP_CONST, OP_IMM, OP_X, OP_BYTE, OP_CH, OP_TIME = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
OP_NEG, OP_NOT, OP_BITNOT, OP_VALID = 0x10, 0x11, 0x12, 0x13
OP_BINARY = {"+": 0x20, "-": 0x21, "*": 0x22, "/": 0x23, "//": 0x24, "%": 0x25, "**": 0x26,
             "&": 0x27, "|": 0x28, "^": 0x29, "<<": 0x2A, ">>": 0x2B}
OP_COMPARE = {"<": 0x30, "<=": 0x31, ">": 0x32, ">=": 0x33, "==": 0x34, "!=": 0x35}
OP_AND, OP_OR, OP_SELECT, OP_COALESCE, OP_CALL = 0x38, 0x39, 0x3A, 0x3B, 0x40
FUNCTIONS = ["abs", "min", "max", "round", "int", "float", "floor", "ceil", "sqrt", "exp", "ln",
             "log10", "pow", "sin", "cos", "sign", "clamp", "between", "bit", "signed", "poly",
             "table", "thermistor"]
FN = {name: i for i, name in enumerate(FUNCTIONS)}
FN["log"] = FN["ln"]

# ---- drive and capture files ------------------------------------------------

FILE_MAGIC = 0x46444848         # "HHDF"
BLOCK_MAGIC = 0x4B424848        # "HHBK"
BLOCK_MAGIC_BYTES = struct.pack("<I", BLOCK_MAGIC)
FILE_VERSION = 1
FILE_HDR = struct.Struct("<IHHIIBBBBIQq12s8s12s12s10sBBIB3sQI8sI")     # 128
BLOCK_HDR = struct.Struct("<IBBHIIQ")                                   # 24
FK_DRIVE, FK_CAPTURE = 1, 2
HF_TIME_VALID, HF_TIME_GPS, HF_PREALLOC, HF_BUILTIN_MAP = 1, 2, 4, 8

B_READS, B_GPS, B_EVENTS, B_CONFIG, B_ISO, B_NAMES, B_CAPTURE, B_TIME, B_END = 1, 2, 3, 4, 5, 6, 7, 8, 0x7F
BLOCK_NAMES = {B_READS: "reads", B_GPS: "gps", B_EVENTS: "events", B_CONFIG: "config", B_ISO: "iso",
               B_NAMES: "names", B_CAPTURE: "capture", B_TIME: "time", B_END: "end"}
MAX_PAYLOAD = 4096              # no block the firmware writes is longer than 560

REC_READ = struct.Struct("<IBBH")           # dt_us, addr, len, latency_us; then len bytes
REC_ISO = struct.Struct("<IBBBB")           # dt_us, mode, pid, len, flags; then len bytes
REC_GPS = struct.Struct("<QiiIHHBBH")       # 28
REC_EVENT = struct.Struct("<QBBHi")         # 16; the last word is a float for some codes
REC_CAPTURE = struct.Struct("<QQQBBHI")     # 32
REC_TIME = struct.Struct("<QqB7s")          # 24
REC_END = struct.Struct("<IIIIQ")           # 24

(EV_MARKER, EV_LINK_UP, EV_LINK_DOWN, EV_ALARM_RAISED, EV_ALARM_CLEARED, EV_ALARM_ACK, EV_STALL,
 EV_PORT_LOST, EV_PORT_BACK, EV_ISO_SESSION, EV_DTC, EV_LOG_DROPPED, EV_DRIVE_START, EV_DTC_NONE) = range(1, 15)
EVENT_NAMES = {EV_MARKER: "marker", EV_LINK_UP: "link_up", EV_LINK_DOWN: "link_down",
               EV_ALARM_RAISED: "alarm_raised", EV_ALARM_CLEARED: "alarm_cleared", EV_ALARM_ACK: "alarm_ack",
               EV_STALL: "stall", EV_PORT_LOST: "port_lost", EV_PORT_BACK: "port_back",
               EV_ISO_SESSION: "iso_session", EV_DTC: "dtc", EV_LOG_DROPPED: "log_dropped",
               EV_DRIVE_START: "drive_start", EV_DTC_NONE: "dtc_none"}
FLOAT_EVENTS = {EV_ALARM_RAISED, EV_ALARM_CLEARED, EV_ALARM_ACK, EV_STALL}
STALL_REASONS = {1: "rpm", 2: "link", 3: "alarm"}
STALL_MESSAGES = {1: "rpm dropped to 0", 2: "link lost (no reply from ECU)", 3: "alarm"}
END_REASONS = {1: "link_lost", 2: "stopped", 3: "sleep"}
BACKENDS = {0: "usb", 1: "uart"}

assert MAP_HDR.size == 64 and MAP_INFO.size == 64 and MAP_PROTO.size == 112 and MAP_CHAN.size == 80
assert MAP_ALARM.size == 92 and MAP_STALL.size == 24 and FILE_HDR.size == 128 and BLOCK_HDR.size == 24
assert REC_GPS.size == 28 and REC_EVENT.size == 16 and REC_CAPTURE.size == 32 and REC_TIME.size == 24


def crc32(data: bytes, crc: int = 0) -> int:
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def block_crc(nonce: int, header_and_payload: bytes) -> int:
    """A block's CRC covers the file's nonce, then the header and the payload."""
    return crc32(header_and_payload, crc32(struct.pack("<I", nonce)))


def dtc_text(code: int) -> str:
    """Raw two bytes of an OBD2 trouble code -> "P0301"."""
    return "PCBU"[(code >> 14) & 3] + f"{(code >> 12) & 3}{code & 0xFFF:03X}"


def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def fit(text: str, size: int) -> bytes:
    """UTF-8 for a char[size] field: cut on a character boundary, NUL padded."""
    raw = text.encode("utf-8")
    while len(raw) > size - 1:
        text = text[:-1]
        raw = text.encode("utf-8")
    return raw.ljust(size, b"\0")
