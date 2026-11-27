"""The ECU source picked by itself: the KKL cable first, else the CYD (``cyd:``),
never a simulator; a KKL cable that appears wins; while the CYD reads the ECU
there is no relay and no second link to it; the CYD's "K-line cable not found"
shows as no data, never as values.

No real serial port is opened here: the port lists are made up and the CYD is
hondalog's simulator behind a SimPort."""

import time

import pytest

from hondalog import addressmap
from hondalog.handheld import serial_link
from hondalog.handheld import serial_proto as P
from hondalog.handheld.cydsim import CydSim, SimPort
from hondalog.ui import adapters as ad
from hondalog.ui import autoconnect as ac
from hondalog.ui.bus import ChannelBus
from hondalog.ui.chrome import cyd_chip, source_chip
from hondalog.ui.session import cyd_state

KKL = ac.Found("COM21", 0x0403, 0x6001)
CYD = ac.Found("COM24", 0x1A86, 0x7523)
OTHER = ac.Found("COM3", 0x8087, 0x0ACA)        # a Bluetooth serial port or the like
PLAIN = ac.Found("COM1")                          # no USB id


# ---- the pick ----------------------------------------------------------------

def test_pick_prefers_the_kkl_cable_over_the_cyd():
    assert ac.pick([CYD, KKL, OTHER]) == ("COM21", ac.KKL)
    assert ac.pick([CYD, KKL], preferred="com21") == ("COM21", ac.KKL)


def test_pick_falls_back_to_the_cyd():
    assert ac.pick([CYD, OTHER, PLAIN]) == ("cyd:COM24", ac.CYD)
    # the launcher's --port COM21 is not there (the cable is in the CYD now): the CYD
    assert ac.pick([CYD], preferred="COM21") == ("cyd:COM24", ac.CYD)


def test_pick_never_makes_up_a_source():
    assert ac.pick([]) == ("", "")
    assert ac.pick([OTHER, PLAIN]) == ("", "")
    assert ac.pick([ac.Found("COM5", 0x1A86, 0x7523)], preferred="COM5") == ("cyd:COM5", ac.CYD)


def test_source_kind_and_explicit_ports():
    assert ac.source_kind("COM21") == ac.KKL
    assert ac.source_kind("cyd:COM24") == ac.CYD
    assert ac.source_kind("cyd:") == ac.CYD
    assert ac.source_kind("cyd:sim") == ac.SIM and ac.source_kind("sim:") == ac.SIM
    assert ac.source_kind("") == ""
    assert ac.is_explicit("sim:") and ac.is_explicit("cyd:sim") and ac.is_explicit("cyd:COM24")
    assert not ac.is_explicit("COM21") and not ac.is_explicit("/dev/ttyUSB0")
    assert ac.device_of("cyd:COM24@460800") == "COM24"


# ---- start-up and replug -------------------------------------------------------

class FakeEcu:
    def __init__(self):
        self.poller = None


class FakeSession:
    """What AutoConnect reads off the session, and a log of what it was told."""

    demo = False

    def __init__(self):
        self.ecu = FakeEcu()
        self.port = ""
        self.calls = []

    @property
    def connected(self):
        return self.ecu.poller is not None

    def connect(self, port):
        self.calls.append(("connect", port))
        self.port = port
        self.ecu.poller = object()

    def disconnect(self):
        self.calls.append(("disconnect", self.port))
        self.ecu.poller = None


def auto(session, preferred=""):
    return ac.AutoConnect(session, connect=session.connect, preferred=preferred, scanner=lambda: [])


def test_startup_with_the_kkl_cable_uses_it():
    s = FakeSession()
    a = auto(s, preferred="COM21")
    assert "COM21" in a.step([CYD, KKL], 0.0)
    assert s.calls == [("connect", "COM21")]
    # the CYD stays the companion (LiveSession opens it next to the cable): nothing more here
    assert a.step([CYD, KKL], 2.0) == "" and s.calls == [("connect", "COM21")]


def test_startup_without_the_kkl_cable_connects_through_the_cyd():
    s = FakeSession()
    a = auto(s, preferred="COM21")
    assert "through the CYD" in a.step([CYD], 0.0)
    assert s.calls == [("connect", "cyd:COM24")]


def test_nothing_plugged_in_connects_nothing():
    s = FakeSession()
    a = auto(s)
    for t in range(10):
        assert a.step([OTHER, PLAIN], float(t) * 2) == ""
    assert s.calls == []


def test_a_kkl_cable_that_appears_wins_and_the_cyd_link_closes_first():
    s = FakeSession()
    a = auto(s)
    a.step([CYD], 0.0)
    assert "switching" in a.step([CYD, KKL], 2.0)
    assert s.calls == [("connect", "cyd:COM24"), ("disconnect", "cyd:COM24"), ("connect", "COM21")]


def test_kkl_cable_gone_falls_back_to_the_cyd_after_two_scans():
    s = FakeSession()
    a = auto(s)
    a.step([CYD, KKL], 0.0)
    assert a.step([CYD], 6.0) == ""                # one scan without it: a replug blip
    assert "gone" in a.step([CYD], 8.0)
    assert s.calls[-2:] == [("disconnect", "COM21"), ("connect", "cyd:COM24")]


def test_replug_after_the_link_stopped_reconnects():
    s = FakeSession()
    a = auto(s)
    a.step([CYD], 0.0)
    s.ecu.poller = None                             # the session gave up on the link
    assert a.step([CYD], 1.0) == ""                 # not straight away
    assert a.step([CYD], ac.RETRY_S + 0.1)
    assert s.calls[-1] == ("connect", "cyd:COM24")


def test_a_port_picked_by_hand_is_left_alone():
    s = FakeSession()
    a = auto(s)
    s.connect("cyd:COM24")                          # by hand (not through the AutoConnect)
    s.calls.clear()
    assert a.step([CYD, KKL], 0.0) == "" and s.calls == []
    a.pause()
    s.disconnect()
    s.calls.clear()
    assert a.step([CYD, KKL], 10.0) == "" and s.calls == []
    a.resume()
    a.step([CYD, KKL], 20.0)
    assert s.calls == [("connect", "COM21")]


def test_the_demo_is_never_touched():
    s = FakeSession()
    s.demo = True
    assert auto(s).step([KKL, CYD], 0.0) == "" and s.calls == []


def test_tick_acts_once_per_scan():
    s = FakeSession()
    a = ac.AutoConnect(s, connect=s.connect, scanner=lambda: [CYD], clock=lambda: 0.0)
    assert a.tick() == ""                           # no scan yet
    a.scan_now()
    assert a.tick() and a.tick() == ""
    assert s.calls == [("connect", "cyd:COM24")]


# ---- the CYD as the ECU source ------------------------------------------------

def test_cyd_sim_hello_follows_the_firmware_on_its_own_cable():
    """Own cable open: capability 64 cleared, flag 32 set. Cable gone: LINK state 4, relay offered."""
    sim = CydSim(gps=False)
    hello = sim.hello()
    assert not hello.caps & P.CAP_RELAY and hello.flags & P.HF_OWN_CABLE
    sim.own_cable = False
    sim.advance(2.0)
    hello = sim.hello()
    assert hello.caps & P.CAP_RELAY and not hello.flags & P.HF_OWN_CABLE
    assert hello.link_state == P.LINK_ERROR and sim.reads == 0


@pytest.fixture
def cyd_source(monkeypatch, tmp_path):
    """A LinkSource on ``cyd:sim`` whose port is the CYD simulator (its K-line cable is unplugged with
    ``own_cable = False``: LINK state 4, as the firmware); companions are counted, never opened."""
    from hondalog.handheld import companion
    from hondalog.ui import wired
    made = []

    class NoCompanion:
        def __init__(self, *args, **kwargs):
            made.append(args)
            raise AssertionError("a companion link was opened while the CYD reads the ECU")

    monkeypatch.setattr(companion, "HandheldCompanion", NoCompanion)
    sim = CydSim()
    monkeypatch.setattr(serial_link, "make_port", lambda name: SimPort(sim))
    ecu = wired.LinkSource(load_map=addressmap.load_default, out_dir=tmp_path, handheld="auto", relay="on")
    bus = ChannelBus()
    ecu.start(bus)
    ecu.sim, ecu.made, ecu.test_bus = sim, made, bus
    yield ecu
    ecu.disconnect()


def wait_status(ecu, pred, timeout=8.0):
    end = time.monotonic() + timeout
    st = ecu.status()
    while time.monotonic() < end:
        st = ecu.status()
        if pred(st):
            return st
        time.sleep(0.02)
    return st


def test_no_relay_and_no_companion_while_the_cyd_is_the_source(cyd_source):
    ecu = cyd_source
    ecu.connect("cyd:sim")
    st = wait_status(ecu, lambda st: st.state == ad.OK)
    assert st.state == ad.OK
    assert ecu.source_kind == "sim" and ecu.is_cyd
    assert ecu.live.handheld is None and ecu.live.relay is None and ecu.made == []
    assert not ecu.handheld_map_differs()


def test_cyd_without_a_kline_cable_shows_no_data(cyd_source):
    ecu, bus = cyd_source, cyd_source.test_bus
    ecu.sim.own_cable = False
    ecu.connect("cyd:sim")
    st = wait_status(ecu, lambda st: st.headline == "CYD: no K-line cable")
    assert st.state == ad.SILENT and st.headline == "CYD: no K-line cable"
    chip = source_chip("ECU", st, "ecu")
    assert chip[2] == "bad" and chip[3] == "CYD: no K-line cable"
    rpm = bus.channel("rpm")
    assert rpm is not None and rpm.stale(bus.now()) and rpm.gen == 0      # never a value
    state = wait_cyd(ecu, "nocable")
    assert state.link == "nocable"
    assert cyd_chip(state)[3] == "CYD: no K-line cable"


def wait_cyd(ecu, link, timeout=5.0):
    end = time.monotonic() + timeout
    state = cyd_state(ecu)
    while time.monotonic() < end and state.link != link:
        time.sleep(0.02)
        state = cyd_state(ecu)
    return state


def test_kline_cable_pulled_from_the_cyd_blanks_the_values(cyd_source):
    ecu, bus = cyd_source, cyd_source.test_bus
    ecu.connect("cyd:sim")
    assert wait_status(ecu, lambda st: st.state == ad.OK).state == ad.OK
    end = time.monotonic() + 5
    while time.monotonic() < end and (bus.channel("rpm").gen < 3):
        time.sleep(0.02)
    assert not bus.channel("rpm").stale(bus.now())
    ecu.sim.own_cable = False
    st = wait_status(ecu, lambda st: st.headline == "CYD: no K-line cable")
    assert st.headline == "CYD: no K-line cable"
    now = bus.now()
    assert all(state.stale(now) for state in bus.channels("ECU"))
    ecu.sim.own_cable = True                        # plugged back in: values again
    st = wait_status(ecu, lambda st: st.state == ad.OK)
    assert st.state == ad.OK and st.headline == ""


def test_ecu_silent_on_the_cyd_is_no_data_too(cyd_source):
    ecu, bus = cyd_source, cyd_source.test_bus
    ecu.connect("cyd:sim")
    assert wait_status(ecu, lambda st: st.state == ad.OK).state == ad.OK
    ecu.sim.power_cut()                             # key off: LINK state 0
    st = wait_status(ecu, lambda st: st.state == ad.SILENT)
    assert st.headline == "CYD: no reply from the ECU"
    assert all(state.stale(bus.now()) for state in bus.channels("ECU"))


# ---- Setup > Cable -------------------------------------------------------------

def test_setup_cable_page_says_which_source_reads_the_ecu(app, settings):
    from hondalog.ui.screens.setup import CablePage

    class Ecu:
        source_kind = "cyd"
        poller = object()
        live = None

    class Session:
        demo = False
        auto_record = True
        connected = True
        port = "cyd:COM24"
        ecu = Ecu()
        ecu_status = ad.SourceStatus(ad.SILENT, headline="CYD: no K-line cable")

        def ports(self):
            return []

        def disconnect(self):
            self.connected = False

    s = Session()
    page = CablePage(s, settings)
    assert "through the CYD on COM24" in page.source.text()
    assert "no K-line cable" in page.source.text()
    assert page.cyd_btn.text() == "Connect through the CYD" and not page.cyd_btn.isEnabled()
    s.ecu.source_kind, s.port = "kkl", "COM21"
    s.ecu_status = ad.SourceStatus(ad.OK)
    page.sync()
    assert page.source.text().startswith("ECU source: KKL cable on COM21") and page.cyd_btn.isEnabled()
    s.ecu.source_kind, s.connected, s.port = "", False, ""
    page.sync()
    assert page.source.text() == "ECU source: not connected"
