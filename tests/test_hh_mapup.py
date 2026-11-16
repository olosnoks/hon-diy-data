"""Map files over the handheld's USB-C cable: ``hondalog-handheld put-map``.

The laptop side (``CydClient.put_map``) against the simulator (``cydsim``,
which keeps the files in its ``--sd`` folder), against the compiled firmware
core, and through the command line on a pty. The firmware's own checks
(bad chunk CRC, bad whole-file CRC, names, the temp-then-rename sequence) are
in firmware/handheld/test/host/hhsertest.c.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import zlib
from pathlib import Path

import pytest

import hhhost
from hondalog import addressmap
from hondalog.handheld import serial_proto as P
from hondalog.handheld.cli import main as cli_main
from hondalog.handheld.cydsim import CydSim, SimPort, serve
from hondalog.handheld.mapfile import build_map
from hondalog.handheld.serial_link import CydClient, CydError

ROOT = Path(__file__).resolve().parent.parent
BIG = ROOT / "firmware" / "handheld" / "sim" / "testmap" / "MAP"        # 12 miles of Jackson: 1.3 MB
TINY = ROOT / "firmware" / "handheld" / "test" / "host" / "fixtures" / "tinymap" / "MAP"
NAMES = ("MAP.IDX", "MAP.DAT", "README.TXT")


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
def tiny(tmp_path):
    """A small MAP folder with all three files."""
    folder = tmp_path / "tiny" / "MAP"
    shutil.copytree(TINY, folder)
    (folder / "README.TXT").write_text("tiny map for tests\n")
    return folder


def same(a: Path, b: Path) -> bool:
    return all((a / n).read_bytes() == (b / n).read_bytes() for n in NAMES)


def opened(tmp_path: Path, **kw) -> tuple[CydSim, SimPort, CydClient]:
    sim = CydSim(sd=tmp_path / "card", record=False, **kw)
    port = SimPort(sim)
    cyd = CydClient(port, timeout=0.3, retries=1)
    cyd.open()
    return sim, port, cyd


def test_put_map_into_the_simulated_card(tmp_path, tiny):
    sim, _port, cyd = opened(tmp_path)
    seen = []
    names = cyd.put_map(tiny, lambda name, done, size, all_done, all_total: seen.append((name, all_done, all_total)))
    assert names == ["README.TXT", "MAP.DAT", "MAP.IDX"]              # the index last: the pair is half-replaced briefly
    assert same(tiny, tmp_path / "card" / "MAP")
    assert sim.map_commits == ["MAP/README.TXT", "MAP/MAP.DAT", "MAP/MAP.IDX"]
    assert not (tmp_path / "card" / "MAP" / "UPLOAD.TMP").exists()
    assert not sim.map_held                                           # the last file let the map reader go
    assert seen[-1][1] == seen[-1][2] == sum((tiny / n).stat().st_size for n in NAMES)
    assert [s[1] for s in seen] == sorted(s[1] for s in seen)         # the bar only moves forward
    # the card that holds MAP/ is as good as the folder
    again = tmp_path / "again"
    (again / "MAP").mkdir(parents=True)
    for n in NAMES:
        shutil.copy(tiny / n, again / "MAP" / n)
    assert cyd.put_map(again) == names
    cyd.close()


def test_put_map_replaces_the_old_files_whole(tmp_path, tiny):
    sim, _port, cyd = opened(tmp_path)
    folder = tmp_path / "card" / "MAP"
    folder.mkdir(parents=True)
    for n in NAMES:
        (folder / n).write_bytes(b"OLD " + n.encode())
    sim.mapup_answers_left = 1
    with pytest.raises(CydError):
        cyd.put_map(tiny)                                   # the cable goes out in the middle
    assert (folder / "README.TXT").read_bytes() == (tiny / "README.TXT").read_bytes()     # the small one was through
    assert (folder / "MAP.DAT").read_bytes() == b"OLD MAP.DAT"          # untouched until its commit
    assert (folder / "MAP.IDX").read_bytes() == b"OLD MAP.IDX"
    cyd.close()


def test_put_map_resumes_after_an_interrupted_upload(tmp_path):
    sim, port, cyd = opened(tmp_path)
    total = sum((BIG / n).stat().st_size for n in NAMES)
    assert total > 2000 * P.MAPUP_CHUNK
    sim.mapup_answers_left = 1200                           # the cable is pulled about 600 kB in
    with pytest.raises(CydError):
        cyd.put_map(BIG)
    folder = tmp_path / "card" / "MAP"
    tmp = folder / "UPLOAD.TMP"
    assert tmp.stat().st_size > 400_000 and "MAP.DAT" not in [p.name for p in folder.iterdir()]
    assert sim.map_held and sim._mapup is not None
    held = sim._mapup["got"]
    assert held == tmp.stat().st_size

    # plugged in again, a new program run: it asks, and sends only what is missing
    sim.mapup_answers_left = None
    cyd2 = CydClient(SimPort(sim), timeout=0.3, retries=1)
    cyd2.open()
    assert cyd2.map_upload_status() == (True, held)
    cyd2.put_map(BIG)
    assert same(BIG, folder) and not tmp.exists() and not sim.map_held
    # the first 600 kB were not sent again (frames add about 3 % to the file bytes)
    assert 0 < cyd2.port.written < (total - held) * 1.06 + 2000, (cyd2.port.written, total, held)
    assert port.written < total
    cyd.close()
    cyd2.close()


def test_a_damaged_chunk_is_sent_again_and_a_bad_file_is_not_kept(tmp_path, tiny):
    sim, _port, cyd = opened(tmp_path)
    data = (tiny / "MAP.DAT").read_bytes()
    crc = zlib.crc32(data)
    name = b"MAP/MAP.DAT"
    assert cyd.command(P.SC_MAPUP_OPEN, P.MAPUP_OPEN.pack(0, len(name), len(data), crc) + name) == (P.OK, 0)
    # the data and its CRC disagree: refused, not counted
    status, got = cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(0, crc ^ 1) + data[:100])
    assert (status, got) == (P.E_CRC, 0)
    status, got = cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(50, zlib.crc32(data[50:150])) + data[50:150])
    assert (status, got) == (P.E_OFFSET, 0)
    for off in range(0, len(data), 400):
        chunk = data[off:off + 400]
        assert cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(off, zlib.crc32(chunk)) + chunk)[0] == P.OK
    assert cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(0, zlib.crc32(data[:400])) + data[:400]) == (P.OK, len(data))
    # the whole-file CRC was wrong at the open: the commit refuses and nothing is put in place
    bad = bytearray(data)
    bad[0] ^= 0xFF
    assert cyd.command(P.SC_MAPUP_COMMIT, P.MAPUP_COMMIT.pack(0), retries=0) == (P.OK, len(data))   # (the real one)
    assert (tmp_path / "card" / "MAP" / "MAP.DAT").read_bytes() == data
    assert cyd.command(P.SC_MAPUP_OPEN, P.MAPUP_OPEN.pack(0, len(name), len(bad), zlib.crc32(data)) + name)[0] == P.OK
    for off in range(0, len(bad), 400):
        chunk = bytes(bad[off:off + 400])
        assert cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(off, zlib.crc32(chunk)) + chunk)[0] == P.OK
    assert cyd.command(P.SC_MAPUP_COMMIT, P.MAPUP_COMMIT.pack(P.MAPUP_FINAL), retries=0)[0] == P.E_CRC
    assert (tmp_path / "card" / "MAP" / "MAP.DAT").read_bytes() == data         # the good one stays
    assert not (tmp_path / "card" / "MAP" / "UPLOAD.TMP").exists() and not sim.map_held
    assert cyd.map_upload_status() == (False, 0)
    cyd.close()


@pytest.mark.parametrize("name", ["MAP/../MAP.IDX", "../MAP/A.B", "MAP/SUB/A.B", "/MAP/A.B", "MAP/", "MAP/.HID",
                                  "MAP/LONGNAME9.DAT", "MAP/A.LONG", "MAP/a.dat", "MAP/A B.DAT", "MAP/A..B",
                                  "MAP/UPLOAD.TMP", "HANDHELD.MAP", "MAP\\A.DAT", "DRIVES/D000001.HHD"])
def test_other_names_are_refused(tmp_path, name):
    sim, _port, cyd = opened(tmp_path)
    if name != "HANDHELD.MAP":                              # (a bare name means a file in MAP/, which this one would be)
        with pytest.raises(CydError, match="8.3"):
            cyd.put_map_file(name, b"x" * 10)               # the laptop will not send it
    raw = name.encode()                                     # and the handheld would not take it
    assert cyd.command(P.SC_MAPUP_OPEN, P.MAPUP_OPEN.pack(0, len(raw), 10, 0) + raw)[0] == P.E_REFUSED
    assert not sim.map_held and sim._mapup is None
    assert [p.name for p in (tmp_path / "card").iterdir() if p.name not in ("DRIVES", "CAPTURES", "HANDHELD.MAP",
                                                                          "HANDHELD.CFG")] == []
    cyd.close()


def test_an_upload_nobody_finishes_lets_go_of_the_map_files(tmp_path):
    sim, _port, cyd = opened(tmp_path)
    data = bytes(range(256)) * 8
    name = b"MAP/MAP.DAT"
    tmp = tmp_path / "card" / "MAP" / "UPLOAD.TMP"
    assert cyd.command(P.SC_MAPUP_OPEN, P.MAPUP_OPEN.pack(0, len(name), len(data), zlib.crc32(data)) + name)[0] == P.OK
    assert cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(0, zlib.crc32(data[:400])) + data[:400])[0] == P.OK
    assert sim.map_held
    sim.advance(sim.now + P.MAPUP_IDLE_S + 1)
    assert not sim.map_held and tmp.stat().st_size == 400          # the map reader has its files; the upload waits
    info = cyd.map_upload_info()
    assert info == {"name": "MAP/MAP.DAT", "size": len(data), "crc32": zlib.crc32(data), "got": 400, "paused": True}
    # the next chunk takes the folder again, and the commit completes it
    for off in range(400, len(data), 500):
        piece = data[off:off + 500]
        status, got = cyd.command(P.SC_MAPUP_WRITE, P.MAPUP_WRITE.pack(off, zlib.crc32(piece)) + piece)
        assert status == P.OK and got == off + len(piece)
    assert sim.map_held
    assert cyd.command(P.SC_MAPUP_COMMIT, P.MAPUP_COMMIT.pack(P.MAPUP_FINAL), retries=0) == (P.OK, len(data))
    assert (tmp_path / "card" / "MAP" / "MAP.DAT").read_bytes() == data and not tmp.exists() and not sim.map_held
    assert cyd.map_upload_info() is None
    cyd.close()


def test_put_map_needs_a_two_way_handheld_with_a_card(tmp_path, tiny):
    sim = CydSim("p1", sd=tmp_path / "card")
    cyd = CydClient(SimPort(sim), timeout=0.3, retries=0)
    cyd.open()
    with pytest.raises(CydError, match="tx-only"):
        cyd.put_map(tiny)
    cyd.close()
    cyd = CydClient(SimPort(CydSim()), timeout=0.3, retries=0)      # no card
    cyd.open()
    with pytest.raises(CydError, match="no SD card"):
        cyd.put_map(tiny)
    cyd.close()


def test_put_map_checks_the_folder_first(tmp_path):
    sim, port, cyd = opened(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CydError):
        cyd.put_map(empty)
    broken = tmp_path / "broken" / "MAP"
    shutil.copytree(TINY, broken)
    (broken / "MAP.DAT").write_bytes(b"not a map")
    with pytest.raises(CydError, match="MAP.DAT"):
        cyd.put_map(broken)
    assert sim._mapup is None and not sim.map_held
    cyd.close()


# ---- against the compiled firmware core ------------------------------------------------

class CPort:
    """The firmware core as a serial port: every read runs it 20 ms on. ``cut`` writes after which the cable fails."""

    name = "cyd:c-firmware"

    def __init__(self, hh, cut: int | None = None):
        self.hh, self.cut, self.baud, self.writes = hh, cut, 921600, 0

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_baud(self, baud: int) -> None:
        self.baud = baud

    def write(self, data: bytes) -> None:
        if self.cut is not None and self.writes >= self.cut:
            raise OSError("cable pulled")
        self.writes += 1
        self.hh.serial_rx(data)

    def read(self, timeout: float) -> bytes:
        self.hh.run_for(0.02)
        return self.hh.serial_poll()


def test_put_map_into_the_firmware_core(L, tmp_path, amap, tiny):
    hh = hhhost.Handheld(L, tmp_path / "sd", map_bytes=build_map(amap), cfg="prealloc_mb = 0\n")
    hh.serial(False)
    try:
        cyd = CydClient(CPort(hh), timeout=0.5)
        cyd.open()
        folder = hh.sd / "MAP"
        folder.mkdir(exist_ok=True)
        (folder / "MAP.IDX").write_bytes(b"old")
        cyd.put_map(tiny)
        assert same(tiny, folder) and not (folder / "UPLOAD.TMP").exists()

        # interrupted half-way through a bigger file, carried on by a new client
        big = tmp_path / "big" / "MAP"
        shutil.copytree(BIG, big)
        port = CPort(hh, cut=700)
        c1 = CydClient(port, timeout=0.5)
        c1.open()
        with pytest.raises(OSError):
            c1.put_map(big)
        part = (folder / "UPLOAD.TMP").stat().st_size
        assert part > 100_000
        c2 = CydClient(CPort(hh), timeout=0.5)
        c2.open()
        assert c2.map_upload_status() == (True, part)
        c2.put_map(big)
        assert same(big, folder) and not (folder / "UPLOAD.TMP").exists()
        assert hh.serial_stat("rx_bad") == 0 and hh.serial_stat("blocked") == 0
    finally:
        hh.close()


# ---- the command line, on a pty ---------------------------------------------------------

def test_put_map_command_over_a_pty(tmp_path, capsys):
    link = tmp_path / "cyd"
    sim = CydSim(sd=tmp_path / "card", record=False)
    stop = threading.Event()
    th = threading.Thread(target=serve, args=(sim, str(link)), kwargs={"keys": False, "run_for": 300.0,
                                                                      "out": open(os.devnull, "w"), "stop": stop})
    th.start()
    try:
        for _ in range(100):
            if link.exists():
                break
            time.sleep(0.02)
        t0 = time.monotonic()
        rc = cli_main(["put-map", str(BIG), "--port", f"cyd:{link}"])
        secs = time.monotonic() - t0
        out = capsys.readouterr().out
        assert rc == 0 and "sent and verified" in out and "MAP.IDX" in out
        assert same(BIG, tmp_path / "card" / "MAP")
        total = sum((BIG / n).stat().st_size for n in NAMES)
        print(f"cydsim put-map over a pty: {total / 1000:.0f} kB in {secs:.1f} s = {total / 1000 / secs:.0f} kB/s")
        assert not sim.map_held
        # not a map folder: a message, status 1, nothing sent
        assert cli_main(["put-map", str(tmp_path), "--port", f"cyd:{link}"]) == 1
    finally:
        stop.set()
        th.join(timeout=10)


# ---- tools/cyd_debug.py --put-map: stand-alone (Python and pyserial only), on a pty ----------------

TOOL = ROOT / "tools" / "cyd_debug.py"


def test_cyd_debug_needs_nothing_of_hondalog():
    import ast
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    roots |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not roots & {"hondalog", "tests", "hhhost"}
    assert roots <= {"argparse", "os", "struct", "sys", "time", "zlib", "serial", "__future__"}


@pytest.fixture
def pty_sim(tmp_path):
    link = tmp_path / "cyd"
    sim = CydSim(sd=tmp_path / "card", record=False)
    stop = threading.Event()
    th = threading.Thread(target=serve, args=(sim, str(link)), kwargs={"keys": False, "run_for": 300.0,
                                                                      "out": open(os.devnull, "w"), "stop": stop})
    th.start()
    for _ in range(100):
        if link.exists():
            break
        time.sleep(0.02)
    yield sim, link
    stop.set()
    th.join(timeout=10)


def run_tool(link: Path, *args: str, timeout: float = 120.0):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-I", str(TOOL), str(link), *args], capture_output=True, text=True,
                          timeout=timeout)


def test_cyd_debug_put_map_with_resume(tmp_path, pty_sim):
    sim, link = pty_sim
    card = tmp_path / "card" / "MAP"
    total = sum((BIG / n).stat().st_size for n in NAMES)

    sim.mapup_answers_left = 1000                               # the cable goes out about 500 kB in
    r = run_tool(link, "--put-map", str(BIG))
    assert r.returncode == 1 and "did not answer" in r.stderr and "Run the same command again" in r.stderr
    held = sim._mapup["got"]
    assert 400_000 < held < 700_000 and (card / "UPLOAD.TMP").stat().st_size == held
    assert (card / "README.TXT").read_bytes() == (BIG / "README.TXT").read_bytes()

    sim.mapup_answers_left = None
    t0 = time.monotonic()
    r = run_tool(link, "--put-map", str(BIG))
    secs = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    assert f"carrying on with MAP.DAT from byte {held}" in r.stdout
    assert same(BIG, card) and not (card / "UPLOAD.TMP").exists() and not sim.map_held
    pcts = [int(line.split("%")[0]) for line in r.stdout.splitlines() if "kB/s" in line and "%" in line[:6]]
    assert pcts == sorted(pcts) and pcts[-1] >= 95 and 5 <= len(pcts) <= 25      # a line every few percent, not every chunk
    sent = total - held
    print(f"cyd_debug --put-map over a pty (resumed): {sent / 1000:.0f} kB in {secs:.1f} s = {sent / 1000 / secs:.0f} kB/s")

    # the same again, the other way round: the card folder that holds MAP/
    holder = tmp_path / "holder"
    shutil.copytree(BIG, holder / "MAP")
    r = run_tool(link, "--put-map", str(holder))
    assert r.returncode == 0 and "sent, checked on the card and in place" in r.stdout


def test_cyd_debug_put_map_refusals(tmp_path, pty_sim):
    sim, link = pty_sim
    r = run_tool(link, "--put-map", str(tmp_path))
    assert r.returncode == 1 and "map-build" in r.stderr
    broken = tmp_path / "broken"
    shutil.copytree(TINY, broken)
    other = broken / "MAP.IDX"
    data = bytearray(other.read_bytes())
    data[96] ^= 0xFF                                            # another build's id
    other.write_bytes(bytes(data))
    r = run_tool(link, "--put-map", str(broken))
    assert r.returncode == 1 and "different builds" in r.stderr
    assert sim._mapup is None and sim.map_commits == []
