"""The HL text session (hondalog/handheld/hl_proto.py, hl_client.py) against the
simulated handheld (cydsim_hl.py): the same behaviour the firmware's hh_hl.c has
(tests/test_hh_hl.py holds that against the compiled C)."""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

import pytest

from hondalog.handheld import format as F
from hondalog.handheld import hl_proto as H
from hondalog.handheld import serial_proto as P
from hondalog.handheld.cydsim import CydSim, SimPort, serve
from hondalog.handheld.hl_client import (Cancelled, HlClient, HlError, HlTxOnly, MapFolderError, map_folder_files,
                                         reason_text)
from hondalog.handheld.mapfile import DEFAULT_CFG


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def card(tmp_path):
    return tmp_path / "sd"


@pytest.fixture
def rig(card):
    """(client, sim, port) on a simulated handheld with an SD card, connected."""
    sim = CydSim(sd=card, logs=True)
    port = SimPort(sim)
    port.open()
    consoles: list[str] = []
    client = HlClient(port, on_console=consoles.append)
    client.consoles = consoles          # type: ignore[attr-defined]
    client.connect()
    return client, sim, port


def fake_map(build: int = 7, idx_ok: bool = True, size: int = 70000) -> tuple[bytes, bytes]:
    idx = bytearray(428)
    idx[:8] = b"HHMAPIDX"
    struct.pack_into("<HH", idx, 8, 1, 428)
    struct.pack_into("<I", idx, 96, build)
    struct.pack_into("<I", idx, 424, F.crc32(bytes(idx[:424])) if idx_ok else 1)
    dat = bytearray(b"HHMAPDAT" + struct.pack("<HHI", 1, 16, build))
    dat += bytes((i * 31 + 7) & 0xFF for i in range(size))
    return bytes(idx), bytes(dat)


# ---- the byte stream ----------------------------------------------------------------

def test_splitter_separates_frames_and_lines():
    frame = P.frame_encode(P.SF_LOG, b"hello\nworld", 5)         # a 0x0A inside a frame
    other = P.frame_encode(P.SF_ACK, bytes(P.ACK.pack(1, 2, 0, 0)), 6)
    stream = b"\x00" + frame + b"I (12) tag: text\r\n" + other + b"HL OK\n" + b"x\n" + b"HL END\n"
    for step in (len(stream), 1, 7):
        sp = H.StreamSplitter()
        got = []
        for i in range(0, len(stream), step):
            got += sp.feed(stream[i:i + step])
        assert [k for k, _ in got] == ["frame", "line", "frame", "line", "line", "line"]
        assert got[0][1] == (P.SF_LOG, 5, b"hello\nworld")
        assert [t for k, t in got if k == "line"] == ["I (12) tag: text", "HL OK", "x", "HL END"]
        assert sp.bad == 0


def test_splitter_counts_damage_and_survives_it():
    frame = bytearray(P.frame_encode(P.SF_LOG, b"abc", 1))
    frame[4] ^= 0x10
    good = P.frame_encode(P.SF_LOG, b"ok", 2)
    sp = H.StreamSplitter()
    got = sp.feed(bytes(frame) + good + b"HL OK\n")
    assert [k for k, _ in got] == ["frame", "line"] and got[0][1][2] == b"ok" and sp.bad == 1


@pytest.mark.parametrize("path, parts", [
    ("/", []), ("/MAP/map.dat", ["MAP", "MAP.DAT"]), ("/HANDHELD.CFG", ["HANDHELD.CFG"]), ("/MAP/", ["MAP"]),
    ("MAP/MAP.DAT", None), ("/../x", None), ("/MAP/../HANDHELD.MAP", None), ("/A//B", None), ("/MAP\\X", None),
    ("/MAP/a b", None), ("/" + "A" * 13, None), ("/." + "", None), ("/MAP/..", None), ("/" + "A/" * 40, None),
    ("/MAP/MAP.DAT\n", None), ("", None),
])
def test_path_rules(path, parts):
    assert H.split_path(path) == parts


@pytest.mark.parametrize("path, write, remove", [
    ("/MAP/MAP.DAT", True, True), ("/MAP/README.TXT", True, True), ("/HANDHELD.CFG", True, True),
    ("/HANDHELD.MAP", True, True), ("/DRIVES/D000001.HHD", False, True), ("/CAPTURES/C000001.HHD", False, False),
    ("/DRIVES", False, False), ("/MAP", False, False), ("/MAP/X/Y", False, False), ("/OTHER.TXT", False, False),
    ("/", False, False),
])
def test_write_and_remove_allowlists(path, write, remove):
    parts = H.split_path(path)
    assert H.can_write(parts) is write and H.can_remove(parts) is remove


def test_base64_is_strict():
    assert H.unb64("AAEC") == b"\0\1\2" and H.unb64("") == b""
    for bad in ("AAE", "AA!C", "AAEC=", "A===", "AA\nC"):
        assert H.unb64(bad) is None


# ---- session ------------------------------------------------------------------------

def test_hello_pauses_the_stream_and_bye_resumes_it(rig):
    client, sim, port = rig
    info = client.hello()
    assert info["model"] == "cyd-4827s043n" and info["proto"] == "1" and info["variant"] == "default"
    assert info["id"] == "HH-SIM001" and len(info["map"]) == 12 and int(info["chunk"]) >= 512
    frames_before = sim.tx_frames
    paused_before = sim.hl_paused
    time.sleep(0.6)
    client.pump(0.05)
    assert sim.hl_paused > paused_before, "the engine goes on, its frames are not sent"
    assert sim.tx_frames == frames_before
    assert sim.reads > 0
    client.bye()
    assert not client.open
    got = 0
    end = time.monotonic() + 3
    while time.monotonic() < end and not got:
        client.pump(0.05)
        got = sim.tx_frames - frames_before
    assert got, "the binary stream is back after BYE"
    assert client.hello_frame is not None


def test_idle_session_times_out(card):
    clock = FakeClock()
    sim = CydSim(sd=card, logs=True)
    port = SimPort(sim, clock=clock)
    port.open()
    client = HlClient(port)
    client.connect()
    client.hello()
    assert sim._hl_open
    clock.t += 31
    client.pump(0.05)
    assert not sim._hl_open and not client.open
    # the next command opens it again by itself
    assert client.ls("/")[1]
    assert sim._hl_open


def test_commands_before_hello_are_refused(rig):
    client, sim, port = rig
    client.port.write(b"HL STAT\n")
    for _ in range(40):
        client.pump(0.02)
        if client.q:
            break
    assert client.q == ["HL ERR nosession"]


def test_console_gets_plain_lines_in_a_session_and_log_frames_outside(rig):
    client, sim, port = rig
    time.sleep(0.05)
    client.pump(0.1)
    assert any("hhcore" in l for l in client.consoles), "LOG frames are decoded outside a session"
    client.hello()
    client.consoles.clear()
    sim._next_log = 0.0
    sim.log("hhtest", "inside a session")
    client.pump(0.1)
    assert any("inside a session" in l for l in client.consoles)


def test_tx_only_handheld_is_never_written_to():
    sim = CydSim("p1", logs=True)
    port = SimPort(sim)
    port.open()
    client = HlClient(port)
    with pytest.raises(HlTxOnly) as e:
        client.connect(bauds=(921600,), listen_s=2.5)
    assert "cannot listen" in reason_text(e.value)
    assert port.written == 0 and sim.rx_ignored == 0


def test_wrong_speed_finds_nothing_and_writes_nothing(card):
    sim = CydSim(sd=card)
    port = SimPort(sim)
    port.open()
    port.set_baud(115200)
    client = HlClient(port)
    # connect() tries the speeds in turn: the first two are wrong for a handheld at 115200
    sim.baud = 115200
    assert client.connect(bauds=(921600, 460800, 115200), listen_s=2.2).two_way
    assert port.written == 0


# ---- files --------------------------------------------------------------------------

def test_ls_get_put_rm(rig, card):
    client, sim, _ = rig
    files, dirs = client.ls("/")
    names = {n for n, _, _ in files}
    assert {"/HANDHELD.MAP", "/HANDHELD.CFG"} <= names and "/DRIVES" in dirs
    data = bytes(range(256)) * 301 + b"tail"
    seen = []
    note = client.put("/MAP/MAP.DAT", data, lambda a, b: seen.append((a, b)))
    assert note == "" and seen[0][0] == 0 and seen[-1] == (len(data), len(data))
    assert (card / "MAP" / "MAP.DAT").read_bytes() == data
    assert not (card / "HL_PUT.TMP").exists()
    assert client.get("/MAP/MAP.DAT") == data
    files, _ = client.ls("/MAP")
    assert files[0][:2] == ("/MAP/MAP.DAT", len(data))
    client.rm("/MAP/MAP.DAT")
    assert not (card / "MAP" / "MAP.DAT").exists()
    with pytest.raises(HlError) as e:
        client.get("/MAP/MAP.DAT")
    assert e.value.reason == "nofile"


def test_empty_file(rig, card):
    client, *_ = rig
    client.put("/MAP/README.TXT", b"")
    assert (card / "MAP" / "README.TXT").read_bytes() == b""
    assert client.get("/MAP/README.TXT") == b""


@pytest.mark.parametrize("op, path, reason", [
    ("put", "/DRIVES/D000001.HHD", "denied"), ("put", "/CAPTURES/C000001.HHD", "denied"), ("put", "/X.TXT", "denied"),
    ("put", "/MAP/A/B", "denied"), ("put", "/../HANDHELD.CFG", "path"), ("put", "/MAP/a b", "path"),
    ("rm", "/CAPTURES/C000001.HHD", "denied"), ("rm", "/", "denied"), ("rm", "/MAP/NOPE.DAT", "nofile"),
    ("rm", "/MAP/../HANDHELD.MAP", "path"), ("get", "/../etc/passwd", "path"), ("get", "relative", "path"),
    ("ls", "/..", "path"), ("ls", "/NOPE", "nofile"),
])
def test_paths_are_policed(rig, op, path, reason):
    client, sim, _ = rig
    with pytest.raises(HlError) as e:
        if op == "put":
            client.put(path, b"x")
        elif op == "rm":
            client.rm(path)
        elif op == "get":
            client.get(path)
        else:
            client.ls(path)
    assert e.value.reason == reason


def test_the_drive_being_written_is_busy_and_other_drives_can_be_removed(rig, card):
    client, sim, _ = rig
    sim.advance(sim.now + 3)             # the ECU answers: a drive starts
    assert sim._drive is not None
    name = f"/DRIVES/{sim._drive.path.name}"
    with pytest.raises(HlError) as e:
        client.get(name)
    assert e.value.reason == "busy"
    with pytest.raises(HlError) as e:
        client.rm(name)
    assert e.value.reason == "busy"
    old = card / "DRIVES" / "D000900.HHD"
    old.write_bytes(b"x" * 100)
    client.rm("/DRIVES/D000900.HHD")
    assert not old.exists()


def test_bad_crc_replaces_nothing(rig, card):
    client, *_ = rig
    client.hello()
    client.put("/MAP/README.TXT", b"first")
    client.port.write(b"HL PUT /MAP/README.TXT 5 DEADBEEF\n")
    client.q.clear()
    line = client._next(2)
    assert line.startswith("HL READY")
    client.port.write(b"HL DATA @0 " + H.b64(b"other").encode() + b"\n")
    assert client._next(2) == "HL ACK 5"
    assert client._next(2) == "HL ERR crc"
    assert (card / "MAP" / "README.TXT").read_bytes() == b"first"
    assert client.partial("/MAP/README.TXT") == (0, 0, 0)


def test_put_sizes_and_arguments(rig):
    client, *_ = rig
    for cmd in ("HL PUT /HANDHELD.CFG 9000 00000000", "HL PUT /MAP/X nonsense", "HL PUT /MAP/X 5", "HL PUT /MAP/X -3 00000000"):
        with pytest.raises(HlError) as e:
            client._expect(cmd, "HL READY")
        assert e.value.reason in ("size", "args")


def test_config_get_put_and_skipped_lines(rig, card):
    client, *_ = rig
    cfg = client.cfg_get()
    assert cfg.decode().startswith("# hondalog handheld settings")
    new = DEFAULT_CFG.replace("sync_ms = 1000", "sync_ms = 500") + "mystery_key = 3\n"
    note = client.cfg_put(new.encode())
    assert "skipped=1" in note and "lines=" in note
    assert (card / "HANDHELD.CFG").read_text() == new
    assert (card / "HANDHELD.CBK").exists(), "the old one is kept"
    assert client.cfg_get().decode() == new


def test_map_file_that_does_not_load_is_refused(rig, card):
    client, *_ = rig
    before = (card / "HANDHELD.MAP").read_bytes()
    with pytest.raises(HlError) as e:
        client.put("/HANDHELD.MAP", b"not a map" * 10)
    assert e.value.reason == "refused" and reason_text(e.value).startswith("the handheld could not load")
    assert (card / "HANDHELD.MAP").read_bytes() == before


def test_time_mark_stat_reboot(rig):
    client, sim, _ = rig
    assert client.set_time(1_900_000_000_000) == "ignored", "the GPS has set the clock"
    n = sim.markers
    client.mark()
    assert sim.markers == n + 1
    st = client.stat()
    assert st["link"]["state"] in (0, 1, 2) and {"ecu", "gps", "sd", "drive", "time", "serial"} <= set(st)
    assert st["sd"]["ok"] == 1 and st["time"]["source"] in ("gps", "host", "none")
    boot = sim.boot_no
    client.reboot()
    assert sim.boot_no == boot + 1 and not client.open
    sim2 = CydSim(gps=False, sd=None)
    assert sim2 is not None


def test_clock_can_be_set_when_there_is_no_gps(card):
    sim = CydSim(sd=card, gps=False)
    port = SimPort(sim)
    port.open()
    client = HlClient(port)
    client.connect()
    assert client.set_time(1_900_000_000_000) == "ok"
    assert abs(client.stat()["time"]["unix_ms"] - 1_900_000_000_000) < 5000
    assert client.stat()["time"]["source"] == "host"


# ---- resume ------------------------------------------------------------------------

def big() -> bytes:
    return bytes((i * 7 + (i >> 8)) & 0xFF for i in range(150_000))


def test_upload_resumes_after_a_failure_on_the_card(rig, card):
    client, sim, _ = rig
    data = big()
    sim.hl_fail_after = 20                        # the card errors after 20 chunks
    retries = []
    client.put("/MAP/MAP.DAT", data, on_retry=lambda n, e: retries.append((n, e.reason)))
    assert retries == [(1, "io")]
    assert (card / "MAP" / "MAP.DAT").read_bytes() == data


def test_cancelled_upload_carries_on_where_it_stopped(rig, card):
    client, sim, _ = rig
    data = big()
    client.chunk_override = 1024

    def stop_at_half(held, total):
        if held > total // 2:
            client.cancel_flag = True

    with pytest.raises(Cancelled):
        client.put("/MAP/MAP.DAT", data, stop_at_half)
    client.cancel_flag = False
    client.drain()
    held, size, crc = client.partial("/MAP/MAP.DAT")
    assert len(data) // 2 < held < len(data) and (size, crc) == (len(data), F.crc32(data))
    assert not (card / "MAP" / "MAP.DAT").exists()
    seen = []
    client.put("/MAP/MAP.DAT", data, lambda a, b: seen.append(a))
    assert seen[0] == held, "resumed at what the handheld held"
    assert (card / "MAP" / "MAP.DAT").read_bytes() == data


def test_resume_survives_a_new_session_and_a_different_file_starts_over(rig, card):
    client, sim, _ = rig
    data = big()
    client.chunk_override = 1024

    def stop(held, total):
        if held > 40000:
            client.cancel_flag = True

    with pytest.raises(Cancelled):
        client.put("/MAP/MAP.DAT", data, stop)
    client.cancel_flag = False
    client.bye()
    client.hello()
    held, *_ = client.partial("/MAP/MAP.DAT")
    assert held > 0
    other = data[:-1] + b"?"
    seen = []
    client.put("/MAP/MAP.DAT", other, lambda a, b: seen.append(a))
    assert seen[0] == 0 and (card / "MAP" / "MAP.DAT").read_bytes() == other


def test_resume_offset_the_handheld_does_not_hold_is_corrected(rig):
    client, *_ = rig
    client.hello()
    data = b"x" * 5000
    crc = F.crc32(data)
    client.port.write(f"HL PUT /MAP/A.BIN {len(data)} {H.hex8(crc)} 4096\n".encode())
    client.q.clear()
    assert client._next(2) == "HL READY 0"


def test_out_of_step_data_is_refused_not_stored(rig, card):
    client, *_ = rig
    client.hello()
    data = b"abcdefgh" * 100
    crc = F.crc32(data)
    client.port.write(f"HL PUT /MAP/A.BIN {len(data)} {H.hex8(crc)}\n".encode())
    client.q.clear()
    assert client._next(2) == "HL READY 0"
    client.port.write(b"HL DATA @100 " + H.b64(data[:100]).encode() + b"\n")
    client.port.write(b"HL DATA @0 " + H.b64(data[:100]).encode() + b"\n")        # skipped: the failed PUT's tail
    assert client._next(2) == "HL ERR offset"
    client.drain()
    assert client.partial("/MAP/A.BIN")[0] == 0


def test_damaged_data_line_fails_the_chunk_then_the_client_resumes(rig, card):
    client, *_ = rig
    data = big()
    orig = client.port.write
    state = {"n": 0}

    def flaky(raw: bytes) -> None:
        if raw.startswith(b"HL DATA") and state["n"] == 7:
            state["n"] += 1
            raw = raw[:60] + b"!" + raw[61:]            # a character that is not base64
        elif raw.startswith(b"HL DATA"):
            state["n"] += 1
        orig(raw)

    client.port.write = flaky                           # type: ignore[method-assign]
    retries = []
    client.put("/MAP/MAP.DAT", data, on_retry=lambda n, e: retries.append(e.reason))
    assert retries == ["b64"]
    assert (card / "MAP" / "MAP.DAT").read_bytes() == data


# ---- map folder ------------------------------------------------------------------------

def test_map_upload_with_progress(rig, card, tmp_path):
    client, *_ = rig
    src = tmp_path / "MAP"
    src.mkdir()
    idx, dat = fake_map()
    (src / "MAP.IDX").write_bytes(idx)
    (src / "MAP.DAT").write_bytes(dat)
    (src / "README.TXT").write_text("OpenStreetMap contributors")
    (src / "JUNK.TXT").write_text("not uploaded")
    marks = []
    done = client.put_map(tmp_path, lambda name, held, size, d, total: marks.append((name, d, total)))
    assert [n for n, _ in done] == ["MAP.DAT", "MAP.IDX", "README.TXT"], "the big file first"
    assert (card / "MAP" / "MAP.DAT").read_bytes() == dat and (card / "MAP" / "MAP.IDX").read_bytes() == idx
    assert not (card / "MAP" / "JUNK.TXT").exists()
    assert marks[-1][1] == marks[-1][2] == len(idx) + len(dat) + 26
    assert [m[1] for m in marks] == sorted(m[1] for m in marks)


def test_map_folder_checks(tmp_path):
    with pytest.raises(MapFolderError, match="no MAP.IDX"):
        map_folder_files(tmp_path)
    idx, dat = fake_map()
    (tmp_path / "MAP").mkdir()
    (tmp_path / "MAP" / "MAP.IDX").write_bytes(idx)
    (tmp_path / "MAP" / "MAP.DAT").write_bytes(fake_map(build=8)[1])
    with pytest.raises(MapFolderError, match="different builds"):
        map_folder_files(tmp_path)
    (tmp_path / "MAP" / "MAP.IDX").write_bytes(fake_map(idx_ok=False)[0])
    with pytest.raises(MapFolderError, match="damaged"):
        map_folder_files(tmp_path / "MAP")
    (tmp_path / "MAP" / "MAP.IDX").write_bytes(b"junk" * 200)
    with pytest.raises(MapFolderError, match="not hondalog"):
        map_folder_files(tmp_path)


# ---- over a pty -------------------------------------------------------------------------

def test_client_over_a_pty_with_serialport(card, tmp_path):
    from hondalog.handheld.serial_link import SerialPort
    link = str(tmp_path / "cydsim")
    sim = CydSim(sd=card, logs=True)
    th = threading.Thread(target=serve, kwargs=dict(sim=sim, link_path=link, keys=False, run_for=25, out=open(os.devnull, "w")),
                          daemon=True)
    th.start()
    for _ in range(100):
        if os.path.exists(link):
            break
        time.sleep(0.05)
    port = SerialPort(link)
    port.open()
    try:
        console: list[str] = []
        client = HlClient(port, on_console=console.append)
        assert client.connect(listen_s=4).two_way
        info = client.hello()
        assert info["chunk"] and client.chunk == H.CHUNK and client.window == H.WINDOW
        data = big() * 3
        client.put("/MAP/MAP.DAT", data)
        assert (card / "MAP" / "MAP.DAT").read_bytes() == data
        assert client.get("/MAP/MAP.DAT") == data
        assert client.stat()["sd"]["ok"] == 1
        client.bye()
        assert client.hello_frame is not None
    finally:
        port.close()
        sim.silent = True
