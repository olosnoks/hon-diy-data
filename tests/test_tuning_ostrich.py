import time

import pytest

from hondalog.tuning import EmulatorError, Ostrich, OstrichSim
from hondalog.tuning.demo import build_demo_rom
from hondalog.tuning.ostrich import BANK_SIZE, checksum
from tuning_helpers import demo_session

ROM = build_demo_rom()


@pytest.fixture
def pair():
    sim = OstrichSim()
    return Ostrich(sim), sim


def test_wire_format_matches_the_reference_driver():
    """Byte-for-byte what mstrand/ostrich2 sends for the same operations."""
    sent = []

    class Spy(OstrichSim):
        def write(self, data):
            sent.append(bytes(data))
            return super().write(data)

    o = Ostrich(Spy())
    o.identify()
    assert sent[0] == b"VV" and sent[1] == b"NS" + bytes([checksum(b"NS")])
    sent.clear()
    page = bytes(range(256))
    o.write_pages(0x8100, page)
    assert sent == [b"ZW\x01\x00\x81" + page + bytes([checksum(b"ZW\x01\x00\x81" + page)])]
    sent.clear()
    o.read_pages(0x0200, 0x200)
    assert sent == [b"ZR\x02\x00\x02" + bytes([checksum(b"ZR\x02\x00\x02")])]
    sent.clear()
    o.set_io_bank(2)
    o.set_emulation_bank(2)
    o.set_emulation_bank(2, persistent=True)
    o.io_bank()
    assert [m[:-1] for m in sent] == [b"BR\x02", b"BE\x02", b"BS\x02", b"BRR"]
    assert all(m[-1] == checksum(m[:-1]) for m in sent)


def test_identify(pair):
    o, _ = pair
    info = o.identify()
    assert (info.device_type, info.version, info.device_id) == (20, 9, "O")
    assert info.vendor_id == 1 and info.serial == "0102030405060708"
    assert "firmware 9" in info.describe()


def test_upload_verify_download_and_speed(pair):
    o, sim = pair
    seen = []
    start = time.perf_counter()
    o.upload(ROM, progress=lambda done, total: seen.append((done, total)))
    assert time.perf_counter() - start < 3.0             # W5-08: under 3 s
    assert o.in_sync and sim.visible() == ROM
    assert bytes(sim.memory[:0x8000]) == ROM              # mirrored below as well
    assert seen[-1] == (4 * 0x8000, 4 * 0x8000) and seen[0][0] == 0x1000
    assert o.download() == ROM and o.verify(ROM)
    assert not o.verify(ROM[::-1])
    # serial time at 921600 baud, 10 bits per byte, for upload + verify
    assert sim.bytes_received * 10 / 921600 < 1.0


def test_layouts():
    for layout, at in (("upper", 0x8000), ("lower", 0)):
        sim = OstrichSim()
        o = Ostrich(sim, layout=layout)
        o.upload(ROM)
        assert bytes(sim.memory[at:at + 0x8000]) == ROM
        other = 0x8000 - at
        assert sim.memory[other:other + 0x8000] == b"\xff" * 0x8000
        assert o.download() == ROM
    full = Ostrich(OstrichSim(), image_size=0x10000)
    full.upload(ROM + ROM)
    assert full.download() == ROM + ROM
    for bad in (dict(layout="diagonal"), dict(image_size=1000), dict(image_size=0x6000)):
        with pytest.raises(EmulatorError):
            Ostrich(OstrichSim(), **bad)


def test_live_write_touches_one_page_per_mirror(pair):
    o, sim = pair
    o.upload(ROM)
    before = sim.messages
    received = sim.bytes_received
    o.write(0x4025, b"\x11\x22")
    assert sim.messages - before == 2
    assert sim.visible()[0x4025:0x4027] == b"\x11\x22" and sim.memory[0x4025] == 0x11
    assert sim.visible()[:0x4025] == ROM[:0x4025] and sim.visible()[0x4027:] == ROM[0x4027:]
    # W5-08: a cell edit is live in under 100 ms; this is about 6 ms of serial time
    assert (sim.bytes_received - received) * 10 / 921600 < 0.1
    o.write(0x40FF, b"\x01\x02")                          # spans two pages
    assert sim.visible()[0x40FF:0x4101] == b"\x01\x02"
    with pytest.raises(EmulatorError, match="outside the image"):
        o.write(0x7FFF, b"\x01\x02")
    with pytest.raises(EmulatorError, match="upload the ROM"):
        Ostrich(OstrichSim()).write(0, b"\x00")


def test_byte_commands_are_opt_in(pair):
    _, sim = pair
    o = Ostrich(sim, byte_commands=True)
    o.upload(ROM)
    received = sim.bytes_received
    o.write(0x7FFF, b"\xAB")
    assert sim.visible()[0x7FFF] == 0xAB and sim.memory[0x7FFF] == 0xAB
    assert sim.bytes_received - received == 2 * 6          # W n msb lsb data cs, twice


def test_sync_sends_only_changed_pages(pair):
    o, sim = pair
    assert o.sync(ROM) == 0x80 and sim.visible() == ROM   # nothing known: full upload
    changed = bytearray(ROM)
    changed[0x10] ^= 1
    changed[0x4155] ^= 1
    before = sim.messages
    assert o.sync(changed) == 2 and sim.visible() == bytes(changed)
    assert sim.messages - before == 4
    assert o.sync(changed) == 0


def test_banks(pair):
    o, sim = pair
    o.upload(ROM)
    o.select_bank(3)
    assert (o.io_bank(), o.emulation_bank(), o.emulation_bank(True)) == (3, 3, 0)
    assert o.shadow is None and not o.in_sync
    o.upload(ROM[::-1])
    assert sim.visible() == ROM[::-1]
    assert bytes(sim.memory[BANK_SIZE - 0x8000:BANK_SIZE]) == ROM   # bank 0 untouched
    o.set_emulation_bank(1, persistent=True)
    assert o.emulation_bank(True) == 1
    with pytest.raises(EmulatorError, match="0..7"):
        o.select_bank(8)
    with pytest.raises(EmulatorError, match="refused the I/O bank"):
        o.set_io_bank(9)


def test_errors_are_clear(pair):
    o, sim = pair
    with pytest.raises(EmulatorError, match="256-byte pages"):
        o.write_pages(0x10, bytes(256))
    with pytest.raises(EmulatorError, match="256-byte pages"):
        o.read_pages(0, 10)
    with pytest.raises(EmulatorError, match="past the end"):
        o.write_pages(0xFF00, bytes(512))
    with pytest.raises(EmulatorError, match="past the end"):
        o.read_pages(0xFF00, 512)
    with pytest.raises(EmulatorError, match="expected 32768"):
        o.upload(b"abc")
    sim.fail_next = True
    with pytest.raises(EmulatorError, match="refused a write at 0x0000"):
        o.write_pages(0, bytes(256))

    class Dead:
        def write(self, data): return len(data)
        def read(self, size=1): return b""
        def close(self): self.closed = True

    dead = Ostrich(Dead())
    with pytest.raises(EmulatorError, match="timeout"):
        dead.identify()
    dead.close()
    assert dead.io.closed

    class Broken(Dead):
        def write(self, data): raise OSError("unplugged")

    with pytest.raises(EmulatorError, match="unplugged"):
        Ostrich(Broken()).identify()


def test_verify_failure_and_corrupt_reply():
    class Flaky(OstrichSim):
        def read(self, size=1):
            data = super().read(size)
            if size == 0x1000 and not getattr(self, "done", False):
                self.done = True
                return bytes([data[0] ^ 0xFF]) + data[1:]
            return data

    with pytest.raises(EmulatorError, match="checksum error"):
        Ostrich(Flaky()).upload(ROM)

    class Stuck(OstrichSim):
        def write(self, data):
            n = super().write(data)
            self.memory[0x123] = 0x00                    # a dead cell
            return n

    o = Ostrich(Stuck())
    with pytest.raises(EmulatorError, match="verify failed at 0x0123"):
        o.upload(ROM)
    assert o.shadow is None and not o.in_sync


def test_sim_rejects_garbage_and_bad_checksums():
    sim = OstrichSim()
    sim.write(b"ZW\x01\x00\x00" + bytes(256) + b"\x55")   # wrong checksum
    assert sim.read(1) == b"?"
    sim.write(b"??")
    assert sim.read(1) == b"?"
    sim.write(b"V")
    assert sim.read(1) == b""                             # waiting for more
    sim.write(b"V")
    assert len(sim.read(3)) == 3
    sim.flush()
    sim.close()
    assert sim.closed


def test_session_edits_reach_the_emulator(tmp_path):
    s = demo_session(tmp_path)
    sim = OstrichSim()
    o = Ostrich(sim)
    o.upload(s.data)
    detach = o.attach(s)
    s.table("fuel").percent((0, 0, 3, 3), 5)
    assert sim.visible() == bytes(s.data) != ROM
    s.set_scalar("rev_limit", 7000)
    s.undo()
    s.undo()
    assert sim.visible() == ROM
    s.redo()
    assert sim.visible() == bytes(s.data)

    errors = []
    o.on_error = errors.append
    sim.fail_next = True
    s.table("ignition").add((0, 0, 0, 0), 1)             # the edit still happens
    assert s.table("ignition").changed_mask()[0, 0]
    assert not o.in_sync and errors and "refused" in o.last_error
    assert o.shadow is None                              # state unknown: full upload next
    assert o.sync(s.data) == 0x80 and o.in_sync and sim.visible() == bytes(s.data)

    detach()
    s.table("ignition").add((0, 0, 0, 0), 1)
    assert sim.visible() != bytes(s.data)
