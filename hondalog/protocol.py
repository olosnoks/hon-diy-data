"""Honda diagnostic (K-line) framing. Pure functions, no I/O.

Everything that might differ on a real ECU lives in ``Framing``. The defaults
follow kerpz/ArduinoHondaOBD (tested there on a P2T OBD2 and a chipped P30):

    request:  20 05 ADDR LEN CKSUM          (05 = total request length)
    reply:    00 LEN+3 DATA*LEN CKSUM       (LEN+3 = total reply length)

In both directions the checksum makes every byte of the frame sum to 0x00
modulo 256. The K-line is one wire, so a KKL cable hears its own transmission:
the request comes back as an echo before the ECU's reply.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Framing:
    baud: int = 9600
    cmd_read: int = 0x20           # "read RAM" command byte
    request_len: int = 5           # 2nd request byte: total request length
    reply_header: int = 0x00       # 1st reply byte; -1 = accept any value
    reply_len_extra: int = 3       # 2nd reply byte = data length + this
    max_read_len: int = 16         # largest LEN to ask for in one request
    # Wake-up bytes kerpz sends once before the first request. "" = send none.
    init_sequence: bytes = bytes.fromhex("686af5afbfb3b2c1dbb3e9")
    init_delay_s: float = 0.3
    reply_timeout_s: float = 0.2   # per request, after the last byte is sent
    inter_request_delay_s: float = 0.002

    def with_overrides(self, table: dict) -> "Framing":
        """Return a copy with values replaced from a [protocol] config table."""
        known = {f.name for f in fields(self)}
        changes = {}
        for key, value in table.items():
            if key not in known:
                raise ValueError(f"unknown protocol setting: {key}")
            if key == "init_sequence":
                value = parse_hex(value) if isinstance(value, str) else bytes(value)
            changes[key] = value
        return replace(self, **changes)


DEFAULT = Framing()


class ProtocolError(Exception):
    """Base class for reply problems."""


class NoReply(ProtocolError):
    """Nothing came back after the echo (or nothing at all)."""


class ShortReply(ProtocolError):
    """A reply started but did not finish before the timeout."""


class BadChecksum(ProtocolError):
    """A full-length reply arrived but its checksum is wrong."""


class BadFrame(ProtocolError):
    """Bytes arrived but nothing in them looks like a reply."""


# ---- checksum ---------------------------------------------------------------

def checksum(data: bytes) -> int:
    """Byte that makes ``data + checksum`` sum to 0 modulo 256."""
    return (-sum(data)) & 0xFF


def with_checksum(data: bytes) -> bytes:
    return bytes(data) + bytes([checksum(data)])


def verify(frame: bytes) -> bool:
    """True if a complete frame (checksum included) sums to 0 modulo 256."""
    return len(frame) > 0 and (sum(frame) & 0xFF) == 0


# ---- requests ---------------------------------------------------------------

def build_read_request(addr: int, length: int, framing: Framing = DEFAULT) -> bytes:
    if not 0 <= addr <= 0xFF:
        raise ValueError(f"address out of range: {addr}")
    if not 1 <= length <= 0xFF:
        raise ValueError(f"length out of range: {length}")
    return with_checksum(bytes([framing.cmd_read, framing.request_len, addr, length]))


def reply_size(length: int, framing: Framing = DEFAULT) -> int:
    """Total reply bytes expected for a read of ``length`` data bytes."""
    return length + 3


def build_reply(data: bytes, framing: Framing = DEFAULT) -> bytes:
    """Frame data the way the ECU does. Used by the simulator and the tests."""
    header = framing.reply_header if framing.reply_header >= 0 else 0x00
    length_byte = (len(data) + framing.reply_len_extra) & 0xFF
    return with_checksum(bytes([header, length_byte]) + bytes(data))


# ---- replies ----------------------------------------------------------------

def strip_echo(buf: bytes, request: bytes) -> tuple[bytes, bool]:
    """Drop our own request from the front of what was received.

    Returns (rest, echo_seen). Junk in front of the echo goes with it. If the
    echo is missing (a cable that does not echo) the buffer comes back whole.
    """
    buf = bytes(buf)
    pos = buf.find(bytes(request)) if request else -1
    if pos < 0:
        return buf, False
    return buf[pos + len(request):], True


def _candidates(rest: bytes, length: int, framing: Framing):
    """Offsets in ``rest`` where a reply header for this read could start."""
    want_len = (length + framing.reply_len_extra) & 0xFF
    for i in range(len(rest)):
        if framing.reply_header >= 0 and rest[i] != framing.reply_header:
            continue
        if i + 1 < len(rest) and rest[i + 1] != want_len:
            continue
        yield i


def find_reply(rest: bytes, length: int, framing: Framing = DEFAULT) -> bytes | None:
    """Scan for a complete, checksum-valid reply. Returns the data bytes."""
    size = reply_size(length, framing)
    for i in _candidates(rest, length, framing):
        frame = rest[i:i + size]
        if len(frame) == size and verify(frame):
            return bytes(frame[2:-1])
    return None


def parse_reply(buf: bytes, request: bytes, length: int,
                framing: Framing = DEFAULT) -> bytes:
    """Extract the data bytes of a read reply from raw received bytes.

    Skips the echo and any junk before the frame. Raises a ProtocolError
    subclass that says what went wrong.
    """
    rest, echo_seen = strip_echo(buf, request)
    data = find_reply(rest, length, framing)
    if data is not None:
        return data

    if not buf:
        raise NoReply("no bytes at all, not even the echo "
                      "(cable unplugged, or no 12 V on the OBD2 plug)")
    if not rest:
        raise NoReply("echo only, the ECU did not answer")

    size = reply_size(length, framing)
    full = [i for i in _candidates(rest, length, framing) if len(rest) - i >= size]
    if full:
        frame = rest[full[0]:full[0] + size]
        raise BadChecksum(f"bad checksum in reply {fmt_hex(frame)}")
    if any(True for _ in _candidates(rest, length, framing)):
        raise ShortReply(f"reply cut short: got {fmt_hex(rest)}, wanted {size} bytes")
    note = "" if echo_seen else " (no echo seen)"
    raise BadFrame(f"no reply frame in {fmt_hex(rest)}{note}")


def find_any_frame(rest: bytes, framing: Framing = DEFAULT) -> bytes | None:
    """Find a checksum-valid frame of unknown length (for the hex console)."""
    for i in range(len(rest) - 2):
        if framing.reply_header >= 0 and rest[i] != framing.reply_header:
            continue
        size = rest[i + 1] - framing.reply_len_extra + 3
        if size >= 3 and i + size <= len(rest) and verify(rest[i:i + size]):
            return bytes(rest[i:i + size])
    return None


# ---- hex helpers ------------------------------------------------------------

def parse_hex(text: str) -> bytes:
    """Parse '20 05 00 10', '20,05,0x00,10' or '20050010' into bytes."""
    cleaned = text.replace(",", " ").replace("0x", " ").replace("0X", " ")
    out = bytearray()
    for token in cleaned.split():
        if len(token) % 2:
            token = "0" + token
        out += bytes.fromhex(token)
    return bytes(out)


def fmt_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)
