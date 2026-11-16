"""The handheld firmware's HL text session (components/hhcore/hh_hl.c) driven by the laptop's own client
(hondalog.handheld.hl_client): the C against the Python it was written for, on a simulated card and engine."""
from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

import hhhost
from hondalog.handheld import serial_proto as P
from hondalog.handheld.hl_client import Cancelled, HlClient, HlError
from hondalog.handheld.mapfile import DEFAULT_CFG


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


class FwPort:
    """The firmware's UART as a port object: the engine's virtual clock moves while the client waits."""
    baud = 921600
    fixed_baud = False

    def __init__(self, hh):
        self.hh = hh
        self.written = 0

    def open(self):
        pass

    def close(self):
        pass

    def set_baud(self, baud):
        pass

    def write(self, data):
        self.written += len(data)
        self.hh.serial_rx(data)
        self.hh.serial_tick()

    def read(self, timeout):
        end = self.hh.clock.now() + timeout
        while True:
            self.hh.run_for(0.005)
            self.hh.serial_tick()
            data = self.hh.serial_poll()
            if data or self.hh.clock.now() >= end:
                return data


@pytest.fixture
def rig(L, tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    (sd / "HANDHELD.CFG").write_text(DEFAULT_CFG)
    (sd / "MAP").mkdir()
    hh = hhhost.Handheld(L, sd)
    hh.serial()
    port = FwPort(hh)
    client = HlClient(port)
    client.connect()
    client.hello()
    yield hh, port, client, sd
    hh.close()


def blob(n: int, seed: int = 1) -> bytes:
    return bytes((i * 131 + seed * 17 + (i >> 8)) & 0xFF for i in range(n))


def test_hello_stat_mark_time_and_the_stream_comes_back(rig):
    hh, port, client, sd = rig
    assert client.info["model"] == "cyd-4827s043n" and client.info["chunk"] == "2048" and client.info["win"] == "3"
    st = client.stat()
    assert st["link"]["state"] == 2 and st["sd"]["ok"] == 1 and st["serial"]["baud"] == 921600
    assert st["ecu"]["id"] and isinstance(st["gps"]["hdop"], float)
    client.mark()
    client.set_time()
    assert client.stat()["time"]["unix_ms"] > 0
    paused = hh.serial_stat("tx_frames")
    hh.run_for(3.0)
    hh.serial_poll()
    assert hh.serial_stat("tx_frames") == paused, "no frames are queued as such while a session is open"
    client.bye()
    hh.run_for(3.0)
    data = hh.serial_poll()
    assert b"HHSL" in data or len(data) > 100, "the stream starts again with a hello"
    assert hh.serial_stat("tx_frames") > paused


def test_ls_and_get(rig):
    hh, port, client, sd = rig
    big = blob(300_001)
    (sd / "MAP" / "BIG.BIN").write_bytes(big)
    files, dirs = client.ls("/")
    assert any(f[0] == "/HANDHELD.CFG" for f in files) and "/MAP" in dirs and "/DRIVES" in dirs
    files, _ = client.ls("/MAP")
    assert ("/MAP/BIG.BIN", len(big)) == files[0][:2]
    assert client.get("/MAP/BIG.BIN") == big
    assert client.get("/map/big.bin") == big                       # case does not matter
    assert client.cfg_get().decode() == DEFAULT_CFG
    with pytest.raises(HlError) as e:
        client.get("/NOPE.TXT")
    assert e.value.reason == "nofile"
    for bad in ("/../etc/passwd", "MAP/BIG.BIN", "/MAP//BIG.BIN", "/MAP/BIG\\BIN"):
        with pytest.raises(HlError) as e:
            client.get(bad)
        assert e.value.reason == "path"
    with pytest.raises(HlError) as e:
        client.ls("/NOWHERE")
    assert e.value.reason in ("nofile",)


def test_get_through_a_small_ring(L, tmp_path):
    sd = tmp_path / "sd"
    (sd / "MAP").mkdir(parents=True)
    big = blob(200_000, 5)
    (sd / "MAP" / "BIG.BIN").write_bytes(big)
    hh = hhhost.Handheld(L, sd)
    hh.serial(ring_size=8192)
    client = HlClient(FwPort(hh))
    client.connect()
    client.hello()
    assert client.get("/MAP/BIG.BIN") == big
    assert hh.serial_stat("tx_dropped") == 0
    hh.close()


def test_a_busy_drive_cannot_be_read_or_deleted(rig):
    hh, port, client, sd = rig
    hh.run_for(1.0)
    drive = "/DRIVES/D000001.HHD"
    assert hh.drive(1).exists()
    with pytest.raises(HlError) as e:
        client.get(drive)
    assert e.value.reason == "busy"
    with pytest.raises(HlError) as e:
        client.rm(drive)
    assert e.value.reason == "busy"
    old = sd / "DRIVES" / "D000009.HHD"
    old.write_bytes(b"x" * 100)
    assert client.get("/DRIVES/D000009.HHD") == b"x" * 100
    client.rm("/DRIVES/D000009.HHD")
    assert not old.exists()


def test_rm_is_limited(rig):
    hh, port, client, sd = rig
    (sd / "MAP" / "OLD.BIN").write_bytes(b"1")
    (sd / "OTHER.TXT").write_bytes(b"1")
    client.rm("/MAP/OLD.BIN")
    assert not (sd / "MAP" / "OLD.BIN").exists()
    for path in ("/OTHER.TXT", "/MAP", "/DRIVES", "/CAPTURES/C000001.HHD"):
        with pytest.raises(HlError) as e:
            client.rm(path)
        assert e.value.reason == "denied"
    assert (sd / "OTHER.TXT").exists()
    with pytest.raises(HlError) as e:
        client.rm("/MAP/NONE.BIN")
    assert e.value.reason == "nofile"


def test_put_into_map_whole_and_verified(rig):
    hh, port, client, sd = rig
    data = blob(150_000, 3)
    seen = []
    note = client.put("/MAP/MAP.DAT", data, lambda held, size: seen.append(held))
    assert (sd / "MAP" / "MAP.DAT").read_bytes() == data
    assert not (sd / "MAP" / "UPLOAD.TMP").exists()
    assert seen[-1] == len(data) and seen == sorted(seen)
    client.put("/MAP/MAP.DAT", data[:1000])                          # replaces it whole
    assert (sd / "MAP" / "MAP.DAT").read_bytes() == data[:1000]


def test_put_rules(rig):
    hh, port, client, sd = rig
    for path, reason in (("/OTHER.TXT", "denied"), ("/DRIVES/D000002.HHD", "denied"), ("/MAP/LONGNAME9.D", "denied"),
                         ("/MAP/UPLOAD.TMP", "denied"), ("/MAP/A/B.DAT", "denied"), ("/MAP/x y.dat", "path")):
        with pytest.raises(HlError) as e:
            client.put(path, b"data", attempts=1)
        assert e.value.reason == reason, path
    with pytest.raises(HlError) as e:
        client.put("/HANDHELD.CFG", b"x" * 9000, attempts=1)
    assert e.value.reason == "size"
    assert not (sd / "OTHER.TXT").exists()


def test_bad_crc_leaves_the_old_file(rig):
    hh, port, client, sd = rig
    (sd / "MAP" / "KEEP.BIN").write_bytes(b"old")
    data = blob(5000)
    real = client._put_once

    def lie(command, payload, start, progress):                      # the checksum in the PUT line is wrong
        bad = bytearray(payload)
        n = len(payload)
        client._ensure()
        client._send(f"{command} {n} {zlib.crc32(bytes(payload)) ^ 0x1:08x}")
        line = client._next(5.0)
        assert line.startswith("HL READY")
        pos = 0
        while pos < n:
            from hondalog.handheld import hl_proto as H
            part = bytes(bad[pos:pos + 2048])
            client.port.write(b"HL DATA @%d %s\n" % (pos, H.b64(part).encode()))
            pos += len(part)
            client._next(5.0)
        line = client._next(5.0)
        if line.startswith("HL ERR"):
            raise HlError(line[7:].strip())
        return line

    client._put_once = lie                                          # type: ignore[method-assign]
    with pytest.raises(HlError) as e:
        client.put("/MAP/KEEP.BIN", data, attempts=1)
    assert e.value.reason == "crc"
    client._put_once = real                                         # type: ignore[method-assign]
    assert (sd / "MAP" / "KEEP.BIN").read_bytes() == b"old"
    assert not (sd / "MAP" / "UPLOAD.TMP").exists()


def test_put_resumes_in_a_new_session(rig):
    hh, port, client, sd = rig
    data = blob(400_000, 9)
    marks = []

    def stop_half_way(held, size):
        marks.append(held)
        if held >= 150_000:
            client.cancel_flag = True

    with pytest.raises(Cancelled):
        client.put("/MAP/MAP.DAT", data, stop_half_way, attempts=1)
    client.cancel_flag = False
    client.drain()
    client.bye()
    hh.run_for(3.0)
    hh.serial_poll()
    again = HlClient(port)
    again.connect()
    again.hello()
    held, size, crc = again.partial("/MAP/MAP.DAT")
    assert 0 < held < len(data) and size == len(data) and crc == zlib.crc32(data)
    seen = []
    again.put("/MAP/MAP.DAT", data, lambda h, s: seen.append(h))
    assert seen[0] >= held > 0, "it carried on from what the handheld held"
    assert (sd / "MAP" / "MAP.DAT").read_bytes() == data


def test_cfg_put_is_checked_and_backed_up(rig):
    hh, port, client, sd = rig
    text = DEFAULT_CFG + "\nnot_a_setting = 1\n"
    note = client.cfg_put(text.encode())
    assert "skipped=1" in note
    assert (sd / "HANDHELD.CFG").read_text() == text
    assert (sd / "HANDHELD.CBK").read_text() == DEFAULT_CFG
    assert not (sd / "HANDHELD.TMP").exists()


def test_map_that_does_not_load_is_refused(rig):
    hh, port, client, sd = rig
    with pytest.raises(HlError) as e:
        client.put("/HANDHELD.MAP", b"not a map" * 50, attempts=1)
    assert e.value.reason == "refused"
    assert not (sd / "HANDHELD.MAP").exists()


def test_the_session_times_out_after_30_seconds(rig):
    hh, port, client, sd = rig
    hh.run_for(29.0)
    hh.serial_tick()
    assert b"HL TIMEOUT" not in hh.serial_poll()
    hh.run_for(2.0)
    hh.serial_tick()
    assert b"HL TIMEOUT" in hh.serial_poll()
    hh.run_for(3.0)
    assert b"HHSL" in hh.serial_poll() or hh.serial_stat("tx_frames") > 0   # the stream is back
    # a command without a session
    port.write(b"\nHL STAT\n")
    assert b"HL ERR nosession" in port.read(0.1)


def test_console_lines_and_binary_frames_are_not_commands(rig):
    hh, port, client, sd = rig
    port.write(b"hello there\nrandom text\n")
    port.write(P.frame_encode(P.SC_MARK, b"", 3))                    # binary commands wait for the session's end
    st = client.stat()
    assert st["drive"]["markers"] == 0
    port.write(b"HL " + b"X" * 5000 + b"\n")                         # a line over the limit is dropped
    client.stat()


def test_a_log_line_does_not_split_a_data_line(rig):
    hh, port, client, sd = rig
    big = blob(100_000, 2)
    (sd / "MAP" / "BIG.BIN").write_bytes(big)
    assert client.get("/MAP/BIG.BIN") == big


def test_tx_only_build_never_listens(L, tmp_path):
    hh = hhhost.Handheld(L, tmp_path / "sd")
    (tmp_path / "sd").mkdir(exist_ok=True)
    hh.serial(tx_only=True)
    port = FwPort(hh)
    port.write(b"\nHL HELLO\n")
    hh.run_for(1.0)
    data = hh.serial_poll()
    assert b"HL HELLO" not in data
    assert hh.serial_stat("rx_frames") == 0
    hh.close()
