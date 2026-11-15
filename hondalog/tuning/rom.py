"""ROM images: load, recognise the container, checksum, build a burn image.

A P28 ROM is 32 KB of code and tables. On disk it turns up as:

* plain    - a 32 KB file.
* doubled  - a 64 KB file holding the same 32 KB twice (what gets burned to a
             27SF512, so the ECU sees the code whatever A15 does).
* upper    - 64 KB with the code at 0x8000-0xFFFF and blank fill below.
* lower    - 64 KB with the code at 0x0000-0x7FFF and blank fill above.
* mixed    - 64 KB with two different, non-blank halves. Not guessed at: the
             caller has to say which half.

Everything else in the tuning package works on the *logical* image
(``RomImage.data``, 32 KB), so table addresses never depend on the container.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .definition import ChecksumSpec, Definition

P28_ROM_SIZE = 0x8000
CHIP_27SF512_SIZE = 0x10000
BLANK_BYTES = (0xFF, 0x00)


class RomError(Exception):
    pass


class BurnRefused(RomError):
    """A safety check failed; the image must not be burned."""


def sha256_hex(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def is_blank(data: bytes | bytearray) -> bool:
    return len(data) > 0 and any(data.count(b) == len(data) for b in BLANK_BYTES)


@dataclass
class Layout:
    kind: str                    # plain, doubled, upper, lower, mixed
    file_size: int
    logical_size: int
    copies: int = 1
    used_copy: int = 0           # which slice of the file the logical image is
    fill: int = 0xFF

    def describe(self) -> str:
        kb = self.logical_size // 1024
        text = {
            "plain": f"{kb} KB image",
            "doubled": f"{self.file_size // 1024} KB file, the {kb} KB image "
                       f"repeated {self.copies} times",
            "upper": f"{self.file_size // 1024} KB file, image in the top {kb} KB, "
                     f"0x{self.fill:02X} fill below",
            "lower": f"{self.file_size // 1024} KB file, image in the bottom {kb} KB, "
                     f"0x{self.fill:02X} fill above",
            "mixed": f"{self.file_size // 1024} KB file with {self.copies} different "
                     f"parts; using part {self.used_copy}",
        }
        return text[self.kind]


def detect_layout(blob: bytes, logical_size: int = P28_ROM_SIZE,
                  half: str | int | None = None) -> Layout:
    """Work out how ``blob`` holds a ``logical_size`` image.

    ``half`` picks a part of a mixed file: "lower", "upper" or a part number.
    """
    size = len(blob)
    if size == logical_size:
        return Layout("plain", size, logical_size)
    if size == 0 or size % logical_size or size < logical_size:
        raise RomError(
            f"file is {size} bytes; expected {logical_size} "
            f"({logical_size // 1024} KB) or a multiple of it")
    copies = size // logical_size
    parts = [blob[i * logical_size:(i + 1) * logical_size] for i in range(copies)]
    if half is not None:
        index = {"lower": 0, "upper": copies - 1}.get(half, half)  # type: ignore[arg-type]
        if not isinstance(index, int) or not 0 <= index < copies:
            raise RomError(f"half must be 'lower', 'upper' or 0..{copies - 1}")
        kind = "doubled" if all(p == parts[0] for p in parts) else "mixed"
        return Layout(kind, size, logical_size, copies, index)
    if all(p == parts[0] for p in parts):
        return Layout("doubled", size, logical_size, copies, 0)
    blank = [is_blank(p) for p in parts]
    if all(blank[:-1]) and not blank[-1]:
        return Layout("upper", size, logical_size, copies, copies - 1, parts[0][0])
    if all(blank[1:]) and not blank[0]:
        return Layout("lower", size, logical_size, copies, 0, parts[-1][0])
    raise RomError(
        f"the {size // 1024} KB file holds {copies} different "
        f"{logical_size // 1024} KB parts and none is blank; say which one to use "
        "(half='lower' or half='upper')")


def chip_image(logical: bytes | bytearray, chip_size: int = CHIP_27SF512_SIZE,
               layout: str = "doubled", fill: int = 0xFF) -> bytes:
    """Place a logical image in a chip-sized file.

    "doubled" repeats it to fill the chip; "upper" puts it at the top with
    ``fill`` below (for a 32 KB image on a 64 KB chip: 0x8000-0xFFFF).
    """
    logical = bytes(logical)
    if not logical or chip_size % len(logical) or chip_size < len(logical):
        raise RomError(
            f"a {len(logical)}-byte image does not fit a {chip_size}-byte chip evenly")
    if layout == "doubled":
        return logical * (chip_size // len(logical))
    if layout == "upper":
        return bytes([fill]) * (chip_size - len(logical)) + logical
    if layout == "lower":
        return logical + bytes([fill]) * (chip_size - len(logical))
    raise RomError(f"unknown chip layout {layout!r}")


# ---- checksum ---------------------------------------------------------------

@dataclass
class ChecksumReport:
    status: str                  # ok, bad, none, disabled
    stored: int | None = None
    expected: int | None = None
    verified_rule: bool = False  # the rule itself has been confirmed on a real ECU
    message: str = ""

    @property
    def ok(self) -> bool:
        """True unless the checksum is known to be wrong."""
        return self.status != "bad"


def _compute(data: bytes | bytearray, spec: ChecksumSpec, skip_store: bool) -> int:
    region = bytearray(data[spec.start:spec.end])
    n = spec.store_bits // 8
    if skip_store and spec.start <= spec.store < spec.end:
        for i in range(spec.store - spec.start, min(len(region), spec.store - spec.start + n)):
            region[i] = 0
    if spec.kind == "xor8":
        value = 0
        for b in region:
            value ^= b
        return value
    return sum(region) & ((1 << spec.store_bits) - 1)


def _stored(data: bytes | bytearray, spec: ChecksumSpec) -> int:
    n = spec.store_bits // 8
    return int.from_bytes(data[spec.store:spec.store + n],
                          "little" if spec.lsb_first else "big")


def expected_checksum(data: bytes | bytearray, spec: ChecksumSpec) -> int:
    """The value that should be in the store bytes for the ROM as it is now."""
    mask = (1 << spec.store_bits) - 1
    inside = spec.start <= spec.store < spec.end
    rest = _compute(data, spec, skip_store=True)
    if spec.mode == "store" or not inside:
        if spec.mode == "balance":
            # the store bytes sit outside the summed range: they hold the
            # value that, combined with the range, gives the target
            return (rest ^ spec.target) if spec.kind == "xor8" else (spec.target - rest) & mask
        return rest
    if spec.kind == "xor8":
        return rest ^ (spec.target & mask)
    if spec.kind == "sum8":
        return (spec.target - rest) & mask
    # sum16 summing bytes: the two store bytes b_hi + b_lo must make up the gap.
    gap = (spec.target - rest) & mask
    for hi in range(256):
        lo = (gap - hi) & mask
        if lo < 256:
            return (lo << 8 | hi) if spec.lsb_first else (hi << 8 | lo)
    raise RomError("no 16-bit checksum value balances this ROM")


def checksum_report(data: bytes | bytearray, spec: ChecksumSpec) -> ChecksumReport:
    verified = spec.status.lower() == "verified"
    if spec.kind == "none":
        return ChecksumReport("none", verified_rule=verified,
                              message=spec.note or "the definition describes no checksum")
    if spec.disabled:
        return ChecksumReport("disabled", verified_rule=verified,
                              message=spec.note or "checksum routine is disabled in this base ROM")
    if not (0 <= spec.start < spec.end <= len(data)
            and 0 <= spec.store <= len(data) - spec.store_bits // 8):
        raise RomError("the checksum rule points outside the ROM")
    stored = _stored(data, spec)
    if spec.mode == "balance" and spec.start <= spec.store < spec.end:
        total = _compute(data, spec, skip_store=False)
        good = total == (spec.target & ((1 << spec.store_bits) - 1))
        expected = stored if good else expected_checksum(data, spec)
    else:
        expected = expected_checksum(data, spec)
        good = stored == expected
    width = spec.store_bits // 4
    if good:
        return ChecksumReport("ok", stored, expected, verified,
                              f"checksum 0x{stored:0{width}X} is correct")
    return ChecksumReport("bad", stored, expected, verified,
                          f"checksum is 0x{stored:0{width}X}, should be 0x{expected:0{width}X}")


def checksum_fix(data: bytes | bytearray, spec: ChecksumSpec) -> list[tuple[int, bytes]]:
    """Byte writes that make the checksum right. Empty when nothing to do."""
    if not spec.active or checksum_report(data, spec).status == "ok":
        return []
    value = expected_checksum(data, spec)
    blob = value.to_bytes(spec.store_bits // 8, "little" if spec.lsb_first else "big")
    return [(spec.store, blob)]


# ---- the image --------------------------------------------------------------

@dataclass
class RomImage:
    data: bytearray
    layout: Layout
    path: Path | None = None
    file_sha256: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_bytes(cls, blob: bytes, logical_size: int = P28_ROM_SIZE,
                   half: str | int | None = None, path: Path | None = None) -> "RomImage":
        layout = detect_layout(blob, logical_size, half)
        start = layout.used_copy * logical_size
        rom = cls(bytearray(blob[start:start + logical_size]), layout, path,
                  sha256_hex(blob))
        rom.warnings = rom.sanity()
        return rom

    @classmethod
    def load(cls, path: str | Path, logical_size: int = P28_ROM_SIZE,
             half: str | int | None = None) -> "RomImage":
        path = Path(path)
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise RomError(f"cannot read {path}: {exc.strerror or exc}") from exc
        try:
            return cls.from_bytes(blob, logical_size, half, path)
        except RomError as exc:
            raise RomError(f"{path.name}: {exc}") from exc

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        """Hash of the logical image (the same whatever container it came in)."""
        return sha256_hex(self.data)

    def sanity(self) -> list[str]:
        """Cheap checks that this looks like a ROM and not an empty chip read."""
        out: list[str] = []
        data = bytes(self.data)
        if is_blank(data):
            out.append("the image is blank (all 0xFF or all 0x00)")
            return out
        most = max(data.count(b) for b in set(data[:4096]) | {0xFF, 0x00})
        if most > len(data) * 0.9:
            out.append("over 90% of the image is one byte value; "
                       "this may be a failed chip read")
        if self.layout.kind == "mixed":
            out.append("the file holds different images; "
                       f"part {self.layout.used_copy} is in use")
        return out

    def check_signatures(self, definition: Definition) -> list[str]:
        """Mismatches between the ROM and the definition's signature bytes."""
        out: list[str] = []
        if definition.rom_size != self.size:
            out.append(f"the definition is for a {definition.rom_size}-byte ROM, "
                       f"this one is {self.size} bytes")
        for sig in definition.signatures:
            start = sig.address + definition.base_offset
            got = bytes(self.data[start:start + len(sig.data)])
            if got != sig.data:
                out.append(f"signature {sig.name or hex(sig.address)!s} does not match: "
                           f"expected {sig.data.hex()}, found {got.hex()}")
        return out

    def checksum(self, definition: Definition) -> ChecksumReport:
        return checksum_report(self.data, definition.checksum)

    def container_bytes(self, container: str = "plain",
                        chip_size: int = CHIP_27SF512_SIZE, fill: int = 0xFF) -> bytes:
        if container == "plain":
            return bytes(self.data)
        if container == "same":
            kind = self.layout.kind
            if kind in ("plain", "mixed"):
                return bytes(self.data)
            return chip_image(self.data, self.layout.file_size, kind, self.layout.fill)
        return chip_image(self.data, chip_size, container, fill)

    def save(self, path: str | Path, container: str = "plain",
             chip_size: int = CHIP_27SF512_SIZE, overwrite: bool = False) -> Path:
        """Write the image. container: plain, same, doubled, upper, lower."""
        path = Path(path)
        if path.exists() and not overwrite:
            raise RomError(f"{path} already exists")
        write_atomic(path, self.container_bytes(container, chip_size))
        return path


def write_atomic(path: Path, blob: bytes) -> None:
    """Write via a temporary file so a crash never leaves half a ROM behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class BurnImage:
    """A chip-ready image that passed the safety checks."""

    data: bytes
    chip_size: int
    layout: str
    rom_sha256: str              # hash of the logical ROM
    image_sha256: str            # hash of the bytes that go on the chip
    checksum: ChecksumReport
    warnings: list[str] = field(default_factory=list)


def build_burn_image(logical: bytes | bytearray, definition: Definition | None = None,
                     *, chip_size: int = CHIP_27SF512_SIZE, layout: str = "doubled",
                     fill: int = 0xFF, logical_size: int = P28_ROM_SIZE,
                     allow_signature_mismatch: bool = False) -> BurnImage:
    """Checks, then the chip image. Raises :class:`BurnRefused` if unsafe.

    Refused: wrong size, blank image, a checksum that the definition says is
    wrong, or (unless overridden) signature bytes that do not match.
    A checksum the definition cannot describe is a warning, not a refusal.
    """
    logical = bytes(logical)
    if len(logical) != logical_size:
        raise BurnRefused(
            f"the ROM is {len(logical)} bytes, expected {logical_size}")
    if is_blank(logical):
        raise BurnRefused("the ROM is blank")
    warnings: list[str] = []
    if definition is None:
        report = ChecksumReport("none", message="no definition: checksum not checked")
        warnings.append(report.message)
    else:
        rom = RomImage(bytearray(logical), Layout("plain", len(logical), len(logical)))
        mismatches = rom.check_signatures(definition)
        if mismatches and not allow_signature_mismatch:
            raise BurnRefused("the ROM does not match the definition: "
                              + "; ".join(mismatches))
        warnings.extend(mismatches)
        try:
            report = checksum_report(logical, definition.checksum)
        except RomError as exc:
            raise BurnRefused(str(exc)) from exc
        if report.status == "bad":
            raise BurnRefused(f"{report.message}. Fix the checksum before burning.")
        if report.status == "none":
            warnings.append("checksum not checked: " + report.message)
        elif not report.verified_rule:
            warnings.append("the checksum rule in this definition is marked unverified")
    try:
        image = chip_image(logical, chip_size, layout, fill)
    except RomError as exc:
        raise BurnRefused(str(exc)) from exc
    return BurnImage(image, chip_size, layout, sha256_hex(logical), sha256_hex(image),
                     report, warnings)
