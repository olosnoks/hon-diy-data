"""The UI on a link in OBD2-only mode (ECU answers ISO 9141-2 PIDs, not Honda
reads): the chip says so, PID channels are standard values, raw tools stand down."""

import time

import pytest

from hondalog import addressmap
from hondalog.link import VirtualClock
from hondalog.obd2 import PID_CHANNEL
from hondalog.poller import OBD2, OK, Poller
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport
from hondalog.ui import adapters as ad
from hondalog.ui import backends as be
from hondalog.ui.chrome import source_chip
from hondalog.ui.session import build_session


def run_until(clock, t, wall=30.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t, "virtual time did not advance"


@pytest.fixture()
def obd2_session(tmp_path, monkeypatch):
    """A wired session whose ECU source holds a poller already in OBD2 mode."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    clock = VirtualClock()
    model = EcuModel(honda_protocol=False, clock=clock.now, rev_every_s=0)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), addressmap.load_default(),
                    clock=clock)
    poller.start()
    session = build_session(log_dir=tmp_path / "logs")
    session.auto_record = False
    session.start()
    try:
        run_until(clock, 15)
        assert poller.mode == OBD2 and poller.state == OK
        session.ecu.poller, session.ecu.port = poller, "sim:iso"
        session._status_t = -1.0
        session.pump(session.bus.now())
        yield session
    finally:
        session.ecu.poller = None
        poller.stop()
        session.close()


def test_status_chip_and_link_line_say_obd2_not_honda(obd2_session):
    s = obd2_session
    st = s.ecu_status
    assert st.state == ad.OK and st.link == "OBD2" and st.simulated and st.rate_hz > 1
    _key, icon, style, text, monos = source_chip("ECU", st, "ecu")
    assert icon == "ok" and style == "" and text == "ECU SIM OBD2 (ISO 9141-2)"
    assert monos[0].endswith("Hz")
    line = s.ecu.link_text()
    assert line.startswith("OBD2 ISO 9141-2 via 5-baud init") and "Honda reads off" in line
    assert "wire" not in line and "fast" not in line         # no Honda rate classes to fall short of
    # a Honda link stays as it was
    assert source_chip("ECU", ad.SourceStatus(ad.OK, 20.0))[3] == "ECU OK"


def test_pid_channels_are_shown_as_standard_values_and_put_back_after(obd2_session):
    s = obd2_session
    for cid in PID_CHANNEL.values():
        assert s.bus.channel(cid).spec.verified, cid
    for cid in ("batt", "inj", "iacv", "knock"):              # no PID for these: map's mark, and no value
        st = s.bus.channel(cid)
        assert not st.spec.verified and st.stale(s.bus.now()), cid
    s._mark_obd2(False)
    assert not s.bus.channel("rpm").spec.verified


def test_raw_tools_say_obd2_only_instead_of_failing(obd2_session):
    s, m = obd2_session, obd2_session.mapper
    assert not m.available() and m.obd2_only()
    assert "OBD2" in m.console()                              # said as soon as the mode changed
    m.set_sweeping(True)
    assert not m.sweeping
    m.send(bytes.fromhex("20 05 76 0A"), True)
    s.pump(s.bus.now())
    assert m.console() == be.OBD2_ONLY and not m._pending
