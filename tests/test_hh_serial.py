"""The handheld's serial link (firmware/handheld/SERIAL.md), laptop side:
the decoder against the compiled firmware code (hh_serial.c) and against the
simulator, the ``cyd:`` source end to end in both firmware variants, sync
with an interrupted transfer, the map-hash warning."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
import time
from pathlib import Path

import pytest

import hhhost
from hondalog import addressmap
from hondalog.handheld import format as F
from hondalog.handheld import serial_proto as P
from hondalog.handheld.cydsim import CydSim, SimPort, serve
from hondalog.handheld.mapfile import build_map
from hondalog.handheld.serial_link import CydClient, CydError, CydPoller, UsedLength, list_ports
from hondalog.handheld.sync import sync
from hondalog.live import LiveSession


@pytest.fixture(scope="session")
def amap():
    return addressmap.load_default()


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


@pytest.fixture
def firmware(L, tmp_path, amap):
    """The handheld's engine (C) with its laptop link, on the simulated ECU."""
    made = []

    def make(tx_only=False, cfg="prealloc_mb = 0\n", **kw):
        h = hhhost.Handheld(L, tmp_path / f"sd{len(made)}", map_bytes=build_map(amap), cfg=cfg, **kw)
        h.serial(tx_only)
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


class CPort:
    """The compiled firmware as a serial port: every read runs it 20 ms on."""

    name = "cyd:c-firmware"

    def __init__(self, hh: hhhost.Handheld, step: float = 0.02):
        self.hh = hh
        self.step = step
        self.baud = 921600
        self.written = 0

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_baud(self, baud: int) -> None:
        self.baud = baud

    def write(self, data: bytes) -> None:
        self.written += len(data)
        self.hh.serial_rx(data)

    def read(self, timeout: float) -> bytes:
        self.hh.run_for(self.step)
        return self.hh.serial_poll()


def frames_of(data: bytes) -> tuple[list[tuple[int, int, bytes]], int]:
    d = P.Deframer()
    out = d.feed(data)
    return out, d.bad


# ---- the decoder against the firmware -----------------------------------------

def test_firmware_stream_decodes(firmware, amap):
    hh = firmware()
    data = bytearray()
    for _ in range(40):
        hh.run_for(0.25)
        data += hh.serial_poll()
    frames, bad = frames_of(bytes(data))
    assert bad == 0 and frames
    seqs = [seq for _, seq, _ in frames]
    assert all((b - a) & 0xFF == 1 for a, b in zip(seqs, seqs[1:])), "no frame lost"
    kinds = {t for t, _, _ in frames}
    assert {P.SF_HELLO, P.SF_READ, P.SF_TICK, P.SF_LINK, P.SF_STATS, P.SF_EVENT} <= kinds

    hellos = [P.parse_hello(p) for t, _, p in frames if t == P.SF_HELLO]
    assert 4 <= len(hellos) <= 6 and all(h is not None for h in hellos)
    h = hellos[-1]
    assert h.map_hash == amap.version_hash and h.variant == P.VARIANT_DEFAULT and h.two_way
    assert h.caps & P.CAP_FILES and h.rev == P.REV and h.max_payload == P.MAX_PAYLOAD
    assert h.ecu_id == bytes(hh.model.ram[0x76:0x80])

    # every READ is the ECU's own bytes, decoded here with the laptop's map
    rpm = amap.channel("rpm")
    n = 0
    for t, _, p in frames:
        if t != P.SF_READ:
            continue
        t_us, addr, length, lat = P.READ.unpack_from(p)
        body = p[P.READ.size:]
        assert len(body) == length and 0 < lat < 30000
        if addr <= rpm.addr and rpm.addr + rpm.length <= addr + length:
            value = rpm.convert(body[rpm.addr - addr:rpm.addr - addr + rpm.length])
            assert 300 < value < 8000
            n += 1
    assert n > 50
    stats = [P.parse_stats(p) for t, _, p in frames if t == P.SF_STATS][-1]
    assert stats["reads"] > 100 and stats["tx_dropped"] == 0 and stats["flags"] & P.ST_RECORDING
    assert hh.serial_stat("blocked") == 0


def test_sim_speaks_like_the_firmware(firmware):
    """cydsim stands in for the firmware in the end-to-end tests: same frame
    types, same fixed payload sizes, same hello fields."""
    hh = firmware()
    data = bytearray()
    for _ in range(24):
        hh.run_for(0.25)
        data += hh.serial_poll()
    c_frames, _ = frames_of(bytes(data))
    sim = CydSim(gps=True)
    sim.advance(6.0)
    s_frames, bad = frames_of(sim.take_tx())
    assert bad == 0

    def shapes(frames):
        fixed = {P.SF_HELLO, P.SF_TICK, P.SF_LINK, P.SF_STATS, P.SF_EVENT, P.SF_GPS}
        return {(t, len(p)) for t, _, p in frames if t in fixed}

    c_shapes = shapes(c_frames)
    s_shapes = shapes(s_frames)
    assert {t for t, _ in c_shapes} - {P.SF_GPS} <= {t for t, _ in s_shapes}
    for t, n in s_shapes:
        assert (t, n) in c_shapes or t == P.SF_GPS, f"frame type {t:#x} is {n} bytes in the sim"
    assert (P.SF_GPS, F.REC_GPS.size) in s_shapes
    c_hello = P.parse_hello([p for t, _, p in c_frames if t == P.SF_HELLO][-1])
    s_hello = P.parse_hello([p for t, _, p in s_frames if t == P.SF_HELLO][-1])
    card = P.CAP_FILES | P.CAP_PUT | P.CAP_MAPUP     # these need an SD card
    assert c_hello.map_hash == s_hello.map_hash and c_hello.caps & ~card == s_hello.caps & ~card
    assert c_hello.n_ch == s_hello.n_ch and c_hello.max_payload == s_hello.max_payload


def test_firmware_commands(firmware, amap, tmp_path):
    hh = firmware()
    port = CPort(hh)
    cyd = CydClient(port, timeout=0.5)
    hello = cyd.open()
    assert hello.two_way and hello.map_hash == amap.version_hash
    # the clock (no GPS yet), a marker, the drive cut in two
    assert cyd.set_time(1_790_000_000.0) is True
    assert hh.time_set > 0
    cyd.mark()
    assert hh.get("markers") == 1
    cyd.drive(False)
    cyd.drive(True)
    cyd.request_dtc()
    entries = cyd.list_files(P.F_DRIVE)
    assert [e.file_no for e in entries] == [1, 2] and not entries[0].open and entries[1].open
    one = cyd.fetch(entries[0], tmp_path / "copy" / "D000001.HHD")
    assert one.read_bytes() == hh.drive(1).read_bytes()
    with pytest.raises(CydError):
        cyd.read_chunk(P.F_DRIVE, 2, 0)          # still being written
    # a new map: checked, stored for the next start
    data = build_map(amap)
    cyd.push(P.F_MAP, data)
    assert (hh.sd / "HANDHELD.MAP").read_bytes() == data
    assert cyd.push(P.F_CFG, b"serial_baud = 460800\nnot_a_key = 3\n") == 1
    with pytest.raises(CydError):
        cyd.push(P.F_MAP, b"\x00" * 300)        # would not load: refused, the old map stays
    assert (hh.sd / "HANDHELD.MAP").read_bytes() == data
    # nothing the laptop sent reached the ECU: the K-line saw reads only
    assert hh.serial_stat("rx_bad") == 0 and hh.serial_stat("rx_frames") > 10
    # the link has no transport of its own; the K-line guard refused nothing
    assert hh.serial_stat("blocked") == 0


def test_firmware_tx_only(firmware):
    hh = firmware(tx_only=True)
    port = CPort(hh)
    cyd = CydClient(port, timeout=0.3)
    hello = cyd.open()
    assert not hello.two_way and hello.variant == P.VARIANT_GPS_P1 and not hello.caps & P.CAP_FILES
    with pytest.raises(CydError, match="tx-only"):
        cyd.mark()
    assert port.written == 0, "nothing is written to a tx-only handheld"
    hh.serial_rx(P.frame_encode(P.SC_MARK, b"", 1))    # and if something were, nobody listens
    hh.run_for(0.1)
    assert hh.serial_stat("rx_frames") == 0 and hh.get("markers") == 0


# ---- the cyd: source, end to end ------------------------------------------------

@pytest.mark.parametrize("variant", ["default", "p1"])
def test_cyd_source_records_a_drive(tmp_path, amap, variant):
    port = SimPort(CydSim(variant, stall_after=2.5))
    session = LiveSession(amap, out_dir=tmp_path, fsync=False)
    poller = CydPoller(port, amap)
    poller.port_name = port.name
    session.attach(poller)
    poller.start()
    assert poller.ready.wait(5)
    session.start_recording()
    time.sleep(1.2)
    session.mark()
    time.sleep(3.0)
    assert poller.hz > 10 and poller.lost == 0 and not poller.map_mismatch
    text = poller.link_text()
    assert ("two-way" if variant == "default" else "TX-only") in text
    rec = session.stop_recording()
    session.close()
    if variant == "p1":
        assert port.written == 0 and port.sim.rx_ignored == 0
    else:
        assert port.sim.rx_frames > 3          # the heartbeat ("PC" on the handheld) and the marker
        assert port.sim.markers == 1           # the marker reached its SD file too
    con = sqlite3.connect(rec.db)
    meta = json.loads(con.execute("select meta from drives").fetchone()[0])
    assert meta["source"] == "cyd" and meta["cyd"]["variant"] == ("default" if variant == "default" else "gps-p1")
    streams = dict(con.execute("select name, source from streams").fetchall())
    assert streams.get("gps") == "gps" and any(n.startswith("ecu/") and n != "ecu/link" for n in streams)
    events = [row[0] for row in con.execute("select kind from events")]
    texts = [row[0] for row in con.execute("select text from events")]
    assert events.count("marker") >= 1 and "handheld" in events
    assert any("stall" in t.lower() for t in texts), "the handheld's stall trigger is in the drive"
    rows = con.execute("select count(*) from samples").fetchone()[0] + \
        con.execute("select count(*) from chunks").fetchone()[0]
    assert rows > 0


@pytest.mark.parametrize("variant", ["default", "p1"])
def test_cyd_over_a_pty(tmp_path, amap, variant):
    link = tmp_path / "cyd"
    sim = CydSim(variant)
    th = threading.Thread(target=serve, args=(sim, str(link)), kwargs={"keys": False, "run_for": 6.0,
                                                                      "out": open(os.devnull, "w")})
    th.start()
    try:
        for _ in range(100):
            if link.exists():
                break
            time.sleep(0.02)
        got = []
        poller = CydPoller(f"cyd:{link}", amap, on_sample=got.append)
        poller.start()
        assert poller.ready.wait(5)
        time.sleep(2.5)
        poller.stop()
        assert poller.hello is not None and poller.hello.two_way == (variant == "default")
        assert sum(1 for s in got if s.link) > 20 and poller.reads > 50 and poller._deframer.bad == 0
        if variant == "p1":
            assert sim.rx_frames == 0 and sim.rx_ignored == 0
        else:
            assert sim.rx_frames > 0
    finally:
        th.join()


def test_map_hash_mismatch(tmp_path, amap):
    port = SimPort(CydSim(map_hash="0123456789ab"))
    session = LiveSession(amap, out_dir=tmp_path, fsync=False)
    poller = CydPoller(port, amap)
    session.attach(poller)
    poller.start()
    assert poller.ready.wait(5)
    session.start_recording()
    time.sleep(1.0)
    assert poller.map_mismatch and "#0123456789ab" in poller.warning and amap.version_hash in poller.warning
    assert "MAP MISMATCH" in poller.link_text()
    rec = session.stop_recording()
    session.close()
    meta = json.loads(sqlite3.connect(rec.db).execute("select meta from drives").fetchone()[0])
    assert meta["cyd"]["map_mismatch"] is True and "push-map" in meta["cyd_warning"]
    # the raw reads were still decoded with the laptop's map
    assert poller.reads > 10


def test_damaged_frames_are_counted(amap):
    port = SimPort(CydSim(damage=0.05))
    poller = CydPoller(port, amap)
    poller.start()
    time.sleep(2.5)
    poller.stop()
    assert poller._deframer.bad + poller.lost > 0 and poller.reads > 50 and poller.sweeps > 10


def test_wrong_baud_is_found(amap):
    port = SimPort(CydSim())
    port.baud = 460800                          # the laptop starts at the other speed
    cyd = CydClient(port)
    hello = cyd.open(wait_s=8)
    assert hello is not None and port.baud == 921600


# ---- sync ---------------------------------------------------------------------------

def _recorded_sim(tmp_path: Path, seconds: float = 3.0) -> CydSim:
    sim = CydSim(sd=tmp_path / "sd")
    sim.advance(seconds)
    sim.take_tx()
    return sim


def test_sync_resumes_after_an_interrupted_transfer(tmp_path):
    sim = _recorded_sim(tmp_path, 8.0)
    port = SimPort(sim)
    cyd = CydClient(port, timeout=0.3, retries=1)
    cyd.open()
    cyd.drive(False)                            # end the drive so it can be copied
    source = tmp_path / "sd" / "DRIVES" / "D000001.HHD"
    assert source.stat().st_size > 3 * P.CHUNK
    db = tmp_path / "db"
    map_chunks = -(-(tmp_path / "sd" / "HANDHELD.MAP").stat().st_size // P.CHUNK)
    sim.file_answers_left = map_chunks + 4      # the cable is pulled 4 chunks into the drive
    with pytest.raises(CydError):
        sync(database=db, client=cyd)
    mirror = db / "handheld" / "HH-SIM001"
    part = mirror / "DRIVES" / "D000001.HHD.part"
    assert part.is_file() and 0 < part.stat().st_size < source.stat().st_size

    had = part.stat().st_size
    sim.file_answers_left = None                # plugged in again
    report = sync(database=db, client=cyd)
    assert report.fetched == ["DRIVES/D000001.HHD"] and report.present == ["HANDHELD.MAP"] and not part.exists()
    assert report.bytes == source.stat().st_size - had      # only the rest was sent again
    assert (mirror / "DRIVES" / "D000001.HHD").read_bytes() == source.read_bytes()
    assert [r.status for r in report.imported] == ["imported"]
    again = sync(database=db, client=cyd)
    assert again.present == ["HANDHELD.MAP", "DRIVES/D000001.HHD"] and [r.status for r in again.imported] == ["skipped"]
    cyd.close()


def test_sync_skips_the_preallocated_tail(tmp_path):
    sim = _recorded_sim(tmp_path, 4.0)
    sim.close()                                 # ... and then the power was cut: no END block
    source = tmp_path / "sd" / "DRIVES" / "D000001.HHD"
    real = source.read_bytes()
    end_block = real.rfind(F.BLOCK_MAGIC_BYTES)
    real = real[:end_block]                     # drop the END block: a power cut
    junk = bytes((i * 131 + 7) & 0xFF for i in range(700_000))
    source.write_bytes(real + junk)             # preallocated space holds old card data
    cyd = CydClient(SimPort(sim), timeout=0.3)
    cyd.open()
    report = sync(database=tmp_path / "db", client=cyd)
    copy = tmp_path / "db" / "handheld" / "HH-SIM001" / "DRIVES" / "D000001.HHD"
    assert copy.read_bytes() == real
    assert report.bytes < len(real) + UsedLength.GARBAGE + 50_000
    assert [r.status for r in report.imported] == ["imported"]
    again = sync(database=tmp_path / "db", client=cyd)
    assert again.present == ["HANDHELD.MAP", "DRIVES/D000001.HHD"] and not again.fetched
    cyd.close()


def test_used_length_follows_blocks(tmp_path):
    sim = _recorded_sim(tmp_path, 3.0)
    sim.close()
    data = (tmp_path / "sd" / "DRIVES" / "D000001.HHD").read_bytes()
    watch = UsedLength()
    got = None
    for i in range(0, len(data), 97):           # chunks that cut headers and blocks anywhere
        got = watch(data[i:i + 97])
        if got is not None:
            break
    assert got == len(data), "a clean END is where it stops"
    other = UsedLength()
    assert other(b"not a drive file at all" * 10) is None and other.off


# ---- the UI's connection menu -------------------------------------------------------

def test_connection_menu_offers_the_handheld(tmp_path, monkeypatch):
    from hondalog.handheld import cydsim
    from hondalog.ui import wired
    link = tmp_path / "hondalog-cydsim"
    link.symlink_to(os.devnull)
    monkeypatch.setattr(cydsim, "CYDSIM_SYMLINK", str(link))
    ports = wired.LinkSource(load_map=addressmap.load_default).ports()
    labels = [label for _, label in ports]
    assert any(label.startswith("CYD (serial)") for label in labels)
    assert ("cyd:sim", ) == tuple(dev for dev, _ in ports if dev == "cyd:sim")
    assert any(dev == f"cyd:{link}" for dev, _ in list_ports())


def test_app_session_on_the_handheld(tmp_path, monkeypatch):
    """The app's wired session connected to "CYD (serial)": values reach the
    bus, the info bar shows the handheld's link, a recording runs."""
    from hondalog.ui import adapters as ad
    from hondalog.ui.session import build_session
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    addressmap.load_user_map()
    session = build_session(log_dir=tmp_path / "logs")
    session.start()
    session.connect("cyd:sim")
    try:
        end = time.monotonic() + 8
        while time.monotonic() < end:
            session.pump(session.bus.now())
            if session.ecu_status.state == ad.OK and session.recording.active:
                break
            time.sleep(0.02)
        st = session.ecu_status
        assert st.state == ad.OK and st.simulated and session.recording.active
        time.sleep(1.0)
        session.pump(session.bus.now())
        assert "CYD HH-SIM001" in session.ecu.link_text() and "two-way" in session.ecu.link_text()
        assert session.ecu.take_notice() == ""          # same map: nothing to say
        rpm = session.bus.channel("rpm")
        assert rpm is not None and rpm.gen > 10 and rpm.value > 300
    finally:
        session.close()
