"""The simulator on a real pseudo-terminal, read through pyserial."""

import io
import os
import threading
import time

import pytest

from hondalog import protocol
from hondalog.sim import EcuModel, serve
from hondalog.transport import Link, SerialTransport

pytest.importorskip("serial")


@pytest.fixture
def sim_pty(tmp_path):
    link = str(tmp_path / "pty")
    model = EcuModel(rev_every_s=0)
    thread = threading.Thread(target=serve, daemon=True, kwargs=dict(
        model=model, link_path=link, realtime=True, keys=False, run_for=4, out=io.StringIO()))
    thread.start()
    for _ in range(100):
        if os.path.exists(link):
            break
        time.sleep(0.01)
    yield link, model
    thread.join(6)


def test_pty_echo_then_reply(sim_pty):
    path, _ = sim_pty
    t = SerialTransport(path)
    t.open()
    try:
        req = protocol.build_read_request(0x76, 10)
        t.write(req)
        got = b""
        end = time.monotonic() + 1
        while len(got) < 5 + 13 and time.monotonic() < end:
            got += t.read(64, 0.05)
        assert got[:5] == req                       # our own bytes come back first
        assert got[5:] == protocol.build_reply(b"SIM-P2P\x00\x00\x01")
    finally:
        t.close()


def test_pty_link_and_power_cut(sim_pty):
    path, model = sim_pty
    t = SerialTransport(path)
    t.open()
    try:
        link = Link(t)
        link.wake()                                 # wake bytes must not confuse the sim
        assert len(link.read_mem(0x00, 16)) == 16 and link.echo_seen
        model.power_cut()
        with pytest.raises(protocol.NoReply, match="echo only"):
            link.read_mem(0x00, 16)
    finally:
        t.close()
