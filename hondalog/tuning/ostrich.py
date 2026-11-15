"""Moates Ostrich 2.0 EPROM emulator: serial driver and a simulator.

Protocol source: the open-source ``mstrand/ostrich2`` Python driver, read in
full. Moates' own command document could not be fetched. From that driver:

    921600 baud, 8N1. Checksum = sum of all bytes in the message, mod 256.
    "VV"                         -> type, version, id letter   (no checksum)
    "NS" cs                      -> 9 bytes (vendor, 8 serial) + cs
    "ZW" n mmsb msb data cs      -> "O"      write n x 256 bytes at page address
    "ZR" n mmsb msb cs           -> n x 256 bytes + cs
    "BR" bank cs -> "O"          set I/O bank          "BRR" cs -> bank
    "BE" bank cs -> "O"          set emulation bank    "BER" cs -> bank
    "BS" bank cs -> "O"          same, kept at power-up "BES" cs -> bank

So the verified way to change one cell is to rewrite its 256-byte page, which
takes about 3 ms at this baud rate. The driver keeps a copy of what it
uploaded, so no read is needed first.

Not verified, off by default (``byte_commands=True`` turns them on):
    "W" n msb lsb data cs -> "O"   write n bytes (n = 0 means 256)
    "R" n msb lsb cs -> data cs    read n bytes
These are remembered from the Moates document, not seen in code.

Also not verified: where a 32 KB image must sit in the emulator's 64 KB for a
Honda ECU. The default mirrors it at 0x0000 and 0x8000, which is right
whichever way the socket drives A15 and matches the doubled chip image.

Nothing here has been run against real hardware. :class:`OstrichSim` speaks
the same protocol for tests and for UI work without the emulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from .session import RomSession

BAUD = 921600
PAGE = 0x100
BANK_SIZE = 0x10000
MAX_PAGES_PER_MESSAGE = 16       # 4 KB, the chunk the reference driver uses


class EmulatorError(Exception):
    pass


class Transport(Protocol):
    """What the driver needs from a serial port (pyserial fits)."""

    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


def checksum(data: bytes | bytearray) -> int:
    return sum(data) & 0xFF


@dataclass
class OstrichInfo:
    device_type: int
    version: int
    device_id: str
    vendor_id: int | None = None
    serial: str = ""

    def describe(self) -> str:
        return (f"Ostrich type {self.device_type}, firmware {self.version}, "
                f"id {self.device_id!r}" + (f", serial {self.serial}" if self.serial else ""))


class Ostrich:
    """Driver. ``Ostrich.open("/dev/ttyUSB0")`` or pass any transport."""

    def __init__(self, transport: Transport, *, image_size: int = 0x8000,
                 layout: str = "doubled", byte_commands: bool = False):
        if layout not in ("doubled", "upper", "lower"):
            raise EmulatorError(f"unknown layout {layout!r}")
        if image_size <= 0 or image_size % PAGE or BANK_SIZE % image_size:
            raise EmulatorError("image size must divide 64 KB and be whole pages")
        self.io = transport
        self.image_size = image_size
        self.layout = layout
        self.byte_commands = byte_commands
        self.shadow: bytearray | None = None     # what the emulator holds
        self.in_sync = False
        self.last_error: str | None = None
        self.on_error: Callable[[str], None] | None = None

    @classmethod
    def open(cls, port: str, **options) -> "Ostrich":
        try:
            import serial
        except ImportError as exc:                # pragma: no cover
            raise EmulatorError("pyserial is not installed") from exc
        try:
            link = serial.Serial(port, BAUD, timeout=5, write_timeout=5)
            link.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            raise EmulatorError(f"cannot open {port}: {exc}") from exc
        return cls(link, **options)

    def close(self) -> None:
        self.io.close()

    # -- framing --

    def _send(self, message: bytes, with_checksum: bool = True) -> None:
        try:
            self.io.write(message + (bytes([checksum(message)]) if with_checksum else b""))
            flush = getattr(self.io, "flush", None)
            if flush:
                flush()
        except OSError as exc:
            raise EmulatorError(f"write to the Ostrich failed: {exc}") from exc

    def _recv(self, size: int) -> bytes:
        try:
            data = self.io.read(size)
        except OSError as exc:
            raise EmulatorError(f"read from the Ostrich failed: {exc}") from exc
        if len(data) != size:
            raise EmulatorError(
                f"the Ostrich sent {len(data)} of {size} bytes (timeout). "
                "Check the USB cable and that this port is the Ostrich.")
        return bytes(data)

    def _recv_checked(self, size: int) -> bytes:
        data = self._recv(size)
        got = self._recv(1)[0]
        if got != checksum(data):
            raise EmulatorError("checksum error in data from the Ostrich")
        return data

    def _expect_ok(self, what: str) -> None:
        reply = self._recv(1)
        if reply != b"O":
            raise EmulatorError(f"the Ostrich refused {what} (replied {reply!r})")

    # -- commands --

    def identify(self) -> OstrichInfo:
        self._send(b"VV", with_checksum=False)
        reply = self._recv(3)
        info = OstrichInfo(reply[0], reply[1], chr(reply[2]))
        self._send(b"NS")
        serial_reply = self._recv_checked(9)
        info.vendor_id = serial_reply[0]
        info.serial = serial_reply[1:].hex()
        return info

    def set_io_bank(self, bank: int) -> None:
        self._send(b"BR" + bytes([bank]))
        self._expect_ok("the I/O bank")

    def io_bank(self) -> int:
        self._send(b"BRR")
        return self._recv(1)[0]

    def set_emulation_bank(self, bank: int, persistent: bool = False) -> None:
        self._send((b"BS" if persistent else b"BE") + bytes([bank]))
        self._expect_ok("the emulation bank")

    def emulation_bank(self, persistent: bool = False) -> int:
        self._send(b"BES" if persistent else b"BER")
        return self._recv(1)[0]

    def select_bank(self, bank: int) -> None:
        """Use one 64 KB bank for both emulation and I/O."""
        if not 0 <= bank <= 7:
            raise EmulatorError("bank must be 0..7")
        self.set_io_bank(bank)
        self.set_emulation_bank(bank)
        self.shadow, self.in_sync = None, False

    def write_pages(self, address: int, data: bytes) -> None:
        """Write whole 256-byte pages starting at a page-aligned address."""
        if address % PAGE or len(data) % PAGE or not data:
            raise EmulatorError("page writes must start and end on 256-byte pages")
        if address + len(data) > BANK_SIZE:
            raise EmulatorError("write runs past the end of the 64 KB bank")
        step = MAX_PAGES_PER_MESSAGE * PAGE
        for off in range(0, len(data), step):
            block = data[off:off + step]
            addr = address + off
            self._send(b"ZW" + bytes([len(block) // PAGE, (addr >> 16) & 0x07,
                                      (addr >> 8) & 0xFF]) + block)
            self._expect_ok(f"a write at 0x{addr:04X}")

    def read_pages(self, address: int, size: int) -> bytes:
        if address % PAGE or size % PAGE or size <= 0:
            raise EmulatorError("page reads must start and end on 256-byte pages")
        if address + size > BANK_SIZE:
            raise EmulatorError("read runs past the end of the 64 KB bank")
        out = bytearray()
        step = MAX_PAGES_PER_MESSAGE * PAGE
        for off in range(0, size, step):
            n = min(step, size - off)
            addr = address + off
            self._send(b"ZR" + bytes([n // PAGE, (addr >> 16) & 0x07, (addr >> 8) & 0xFF]))
            out += self._recv_checked(n)
        return bytes(out)

    def write_bytes_unverified(self, address: int, data: bytes) -> None:
        """Small write with the "W" command. Protocol not verified; see module
        docstring. Only used when ``byte_commands`` is on."""
        for off in range(0, len(data), 256):
            block = data[off:off + 256]
            addr = address + off
            self._send(b"W" + bytes([len(block) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])
                       + block)
            self._expect_ok(f"a write at 0x{addr:04X}")

    # -- image level --

    def _bases(self) -> list[int]:
        """Emulator addresses where the logical image starts."""
        if self.layout == "doubled":
            return list(range(0, BANK_SIZE, self.image_size))
        if self.layout == "upper":
            return [BANK_SIZE - self.image_size]
        return [0]

    def upload(self, image: bytes | bytearray, verify: bool = True,
               progress: Callable[[int, int], None] | None = None) -> None:
        """Send the whole logical image, then read it back and compare."""
        image = bytes(image)
        if len(image) != self.image_size:
            raise EmulatorError(
                f"the image is {len(image)} bytes, expected {self.image_size}")
        bases = self._bases()
        total = len(image) * len(bases) * (2 if verify else 1)
        done = 0
        step = MAX_PAGES_PER_MESSAGE * PAGE
        self.in_sync = False
        for base in bases:
            for off in range(0, len(image), step):
                self.write_pages(base + off, image[off:off + step])
                done += min(step, len(image) - off)
                if progress:
                    progress(done, total)
        self.shadow = bytearray(image)
        if verify:
            for base in bases:
                got = self.read_pages(base, len(image))
                if got != image:
                    self.shadow = None
                    bad = next(i for i, (a, b) in enumerate(zip(got, image)) if a != b)
                    raise EmulatorError(
                        f"verify failed at 0x{base + bad:04X}: the emulator holds "
                        f"0x{got[bad]:02X}, the image has 0x{image[bad]:02X}")
                done += len(image)
                if progress:
                    progress(done, total)
        self.in_sync = True

    def download(self) -> bytes:
        """Read the logical image back from the emulator."""
        return self.read_pages(self._bases()[-1], self.image_size)

    def verify(self, image: bytes | bytearray) -> bool:
        return all(self.read_pages(base, self.image_size) == bytes(image)
                   for base in self._bases())

    def write(self, offset: int, data: bytes | bytearray) -> None:
        """Live write of a few bytes at a logical-image offset.

        If the emulator does not confirm it, what it holds is no longer known:
        ``in_sync`` goes False and the next :meth:`sync` uploads everything.
        """
        data = bytes(data)
        if self.shadow is None:
            raise EmulatorError("upload the ROM before sending live changes")
        if offset < 0 or offset + len(data) > self.image_size:
            raise EmulatorError("live write is outside the image")
        shadow = self.shadow
        shadow[offset:offset + len(data)] = data
        self.shadow = None                       # unknown until every write lands
        self.in_sync = False
        if self.byte_commands:
            for base in self._bases():
                self.write_bytes_unverified(base + offset, data)
        else:
            first = offset - offset % PAGE
            last = ((offset + len(data) - 1) // PAGE + 1) * PAGE
            for base in self._bases():
                self.write_pages(base + first, bytes(shadow[first:last]))
        self.shadow = shadow
        self.in_sync = True

    def sync(self, image: bytes | bytearray) -> int:
        """Send only the pages that differ from what the emulator holds.
        Returns the number of pages written (uploads everything if unknown)."""
        image = bytes(image)
        if self.shadow is None or len(self.shadow) != len(image):
            self.upload(image)
            return len(image) // PAGE
        pages = [p for p in range(0, len(image), PAGE)
                 if image[p:p + PAGE] != self.shadow[p:p + PAGE]]
        for p in pages:
            self.write(p, image[p:p + PAGE])
        self.in_sync = self.shadow is not None
        return len(pages)

    def attach(self, session: "RomSession") -> Callable[[], None]:
        """Mirror every edit, undo and redo of a session to the emulator.

        Upload first. A failed live write does not stop the edit: it sets
        ``in_sync`` False, records ``last_error`` and calls ``on_error``; call
        :meth:`sync` to recover. Returns a function that detaches.
        """
        def changed(ranges: list[tuple[int, int]], _reason: str) -> None:
            try:
                for start, end in ranges:
                    self.write(start, bytes(session.data[start:end]))
            except EmulatorError as exc:
                self.last_error = str(exc)
                if self.on_error:
                    self.on_error(self.last_error)

        return session.on_change(changed)


class OstrichSim:
    """An in-memory Ostrich that answers the protocol above. Use it as the
    transport: ``Ostrich(OstrichSim())``."""

    def __init__(self, device_type: int = 20, version: int = 9, device_id: str = "O",
                 serial: bytes = bytes(range(1, 9)), vendor_id: int = 1):
        self.memory = bytearray(b"\xFF" * (8 * BANK_SIZE))
        self.io_bank = 0
        self.emu_bank = 0
        self.emu_bank_saved = 0
        self._ident = bytes([device_type, version, ord(device_id)])
        self._serial = bytes([vendor_id]) + serial[:8].ljust(8, b"\0")
        self._in = bytearray()
        self._out = bytearray()
        self.messages = 0
        self.bytes_received = 0
        self._waiting = False
        self.fail_next = False           # answer the next command with "?" once
        self.closed = False

    # transport side

    def write(self, data: bytes) -> int:
        self._in += data
        self.bytes_received += len(data)
        while self._step():
            pass
        return len(data)

    def read(self, size: int = 1) -> bytes:
        out = bytes(self._out[:size])
        del self._out[:size]
        return out                       # short read = timeout, as pyserial does

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def visible(self, size: int = 0x8000) -> bytes:
        """What a ``size``-byte ROM socket at the top of the bank reads."""
        base = self.emu_bank * BANK_SIZE
        return bytes(self.memory[base + BANK_SIZE - size:base + BANK_SIZE])

    # device side

    def _address(self, mmsb: int, msb: int, lsb: int = 0) -> int:
        bank = mmsb if mmsb else self.io_bank
        return bank * BANK_SIZE + (msb << 8) + lsb

    def _take(self, n: int, checked: bool = True) -> bytes | None:
        """n message bytes plus checksum, once all have arrived."""
        need = n + (1 if checked else 0)
        self._waiting = len(self._in) < need
        if self._waiting:
            return None
        msg = bytes(self._in[:n])
        if checked and self._in[n] != checksum(msg):
            del self._in[:]
            self._out += b"?"
            return None
        del self._in[:need]
        self.messages += 1
        if self.fail_next:
            self.fail_next = False
            self._out += b"?"
            return None
        return msg

    def _step(self) -> bool:
        buf = self._in
        if not buf:
            return False
        if buf[:2] == b"VV":
            if self._take(2, checked=False) is not None:
                self._out += self._ident
            return True
        if len(buf) < 2:
            return False
        head = bytes(buf[:2])
        if head == b"NS":
            msg = self._take(2)
            if msg is None:
                return not self._waiting
            self._out += self._serial + bytes([checksum(self._serial)])
            return True
        if head in (b"ZW", b"ZR"):
            if len(buf) < 5:
                return False
            n, mmsb, msb = buf[2], buf[3], buf[4]
            size = n * PAGE
            addr = self._address(mmsb, msb)
            if head == b"ZW":
                msg = self._take(5 + size)
                if msg is None:
                    return not self._waiting
                self.memory[addr:addr + size] = msg[5:]
                self._out += b"O"
            else:
                msg = self._take(5)
                if msg is None:
                    return not self._waiting
                data = bytes(self.memory[addr:addr + size])
                self._out += data + bytes([checksum(data)])
            return True
        if head in (b"BR", b"BE", b"BS"):
            # "BRR"/"BER"/"BES" + checksum ask; "BR"/"BE"/"BS" + bank + checksum set.
            msg = self._take(3)
            if msg is None:
                return not self._waiting
            if msg in (b"BRR", b"BER", b"BES"):
                self._out += bytes([{b"BRR": self.io_bank, b"BER": self.emu_bank,
                                     b"BES": self.emu_bank_saved}[msg]])
                return True
            bank = msg[2]
            if bank > 7:
                self._out += b"?"
            else:
                if head == b"BR":
                    self.io_bank = bank
                elif head == b"BE":
                    self.emu_bank = bank
                else:
                    self.emu_bank = self.emu_bank_saved = bank
                self._out += b"O"
            return True
        if head[:1] == b"W":
            if len(buf) < 4:
                return False
            n = buf[1] or 256
            msg = self._take(4 + n)
            if msg is None:
                return not self._waiting
            addr = self._address(0, msg[2], msg[3])
            self.memory[addr:addr + n] = msg[4:]
            self._out += b"O"
            return True
        if head[:1] == b"R":
            if len(buf) < 4:
                return False
            msg = self._take(4)
            if msg is None:
                return not self._waiting
            n = msg[1] or 256
            addr = self._address(0, msg[2], msg[3])
            data = bytes(self.memory[addr:addr + n])
            self._out += data + bytes([checksum(data)])
            return True
        del buf[:]
        self._out += b"?"
        return False
