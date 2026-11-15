"""First-contact wizard, link probes, adapter detection and the byte trace."""

from __future__ import annotations

import os

import pytest

from hondalog import protocol
from hondalog.diag import firstcontact as fc
from hondalog.diag import ftdi
from hondalog.diag.probe import probe_block_length, probe_timing, sweep
from hondalog.diag.trace import TracingTransport
from hondalog.link import VirtualClock
from hondalog.sim import EcuModel
from hondalog.transport import Link, LoopbackTransport, Transport, TransportError


def rig(**kw: object) -> tuple[VirtualClock, EcuModel, LoopbackTransport]:
    clock = VirtualClock(1000.0)
    model = EcuModel(clock=clock.now, rev_every_s=0, **kw)    # type: ignore[arg-type]
    return clock, model, LoopbackTransport(model, clock, byte_timing=True)


def run(model_setup=None, **kw: object) -> fc.FirstContactReport:
    clock, model, transport = rig(**kw)
    if model_setup is not None:
        model_setup(model)
    return fc.run("sim:", transport=transport, clock=clock, listen_s=0.5)


# ---- the four states -------------------------------------------------------

def test_verdict_honda_ok_with_everything_measured():
    r = run(dtcs=("P0135",))
    assert r.verdict == fc.HONDA_OK
    assert r.echo and r.honda_no_init
    assert r.ecu_id.startswith(b"SIM-P2P")
    assert r.block_probe is not None and r.block_probe.max_read_len == 64
    assert r.timing is not None and r.timing.ok
    assert 1.5 < r.timing.turnaround_median_ms < 4.5        # sim: 2 ms + 1 ms USB tick
    assert r.sweep is not None and r.sweep.answered == 256
    assert r.iso_ok and "P0135" in r.obd2_text
    assert r.recommended["max_read_len"] == 64
    md = r.to_markdown()
    assert "**verdict: Honda protocol OK**" in md
    assert "## Byte trace" in md and "TX    20 05 00 02 D9" in md
    assert "BREAK" in md                                     # the 5-baud init is in the trace


def test_verdict_iso_only():
    r = run(honda_protocol=False)
    assert r.verdict == fc.ISO_ONLY
    assert r.echo and not r.honda_reply and r.iso_ok
    names = [s.name for s in r.steps]
    assert "Honda read 0x00 (after wake-up)" in names        # the wake bytes were tried
    assert "alternate framings" in names
    assert r.sweep is None


def test_verdict_cable_dead():
    def dead(m: EcuModel) -> None:
        m.cable_dead = True
    r = run(dead)
    assert r.verdict == fc.CABLE_DEAD
    assert not r.echo
    assert any(s.name == "ISO 9141-2 init" and s.ok is None for s in r.steps)
    assert "## Next steps" in r.to_markdown() and "pin 16" in r.to_markdown()


def test_verdict_ecu_silent():
    r = run(lambda m: m.power_cut())
    assert r.verdict == fc.ECU_SILENT
    assert r.echo and not r.honda_reply and not r.iso_ok


def test_verdict_no_port():
    class Gone(Transport):
        name = "/dev/nothing"

        def open(self) -> None:
            raise TransportError("no such device")
    r = fc.run("/dev/nothing", transport=Gone(), clock=VirtualClock(), listen_s=0)
    assert r.verdict == fc.NO_PORT
    assert "dialout" in r.summary


def test_alternate_framing_is_found():
    # An ECU whose length byte is the data length (no +3): the default parser
    # rejects it, an alternate framing accepts it.
    clock, model, transport = rig()
    model.framing = protocol.DEFAULT.with_overrides({"reply_len_extra": 0})
    r = fc.run("sim:", transport=transport, clock=clock, listen_s=0, iso=False)
    assert r.verdict == fc.HONDA_OK
    assert r.honda_framing == "reply length byte = data length"
    assert r.recommended["reply_len_extra"] == 0


def test_quick_skips_slow_steps_and_bus_chatter_is_reported():
    clock, model, transport = rig()
    r = fc.run("sim:", transport=transport, clock=clock, listen_s=0.2, quick=True, iso=False)
    assert r.timing is None and r.sweep is None
    assert any(s.name == "timing probe" and s.ok is None for s in r.steps)


def test_cli_writes_report(tmp_path, capsys):
    out = tmp_path / "fc.md"
    code = fc.main(["--port", "sim:", "--quick", "--no-iso", "--listen", "0", "-o", str(out)])
    assert code == 0
    text = out.read_text()
    assert "verdict: Honda protocol OK" in text
    raw = (tmp_path / "fc.hex.log").read_text()
    assert "TX    20 05 00 02 D9" in raw and raw.startswith("# hondalog first contact")
    assert fc.main(["--udev"]) == 0
    assert 'ATTR{latency_timer}="1"' in capsys.readouterr().out


# ---- probes ----------------------------------------------------------------

def test_block_length_probe_finds_the_ecu_limit():
    clock, model, transport = rig(max_read_len=16)
    link = Link(transport, protocol.DEFAULT, clock)
    p = probe_block_length(link)
    assert p.max_read_len == 16
    assert p.results[16] and not p.results[32]


def test_timing_probe_measures_a_slow_ecu_and_recommends():
    clock, model, transport = rig(turnaround_s=0.010)
    link = Link(transport, protocol.DEFAULT, clock)
    p = probe_timing(link, reads=50)
    assert p.reads == 50 and p.failed == 0
    assert 9.5 < p.turnaround_median_ms < 12.5
    assert p.recommended["reply_timeout_s"] >= 0.05
    assert p.recommended["inter_request_delay_s"] == 0.001
    assert link.timing.gap == link.timing.gap_floor == protocol.DEFAULT.inter_request_delay_s


def test_timing_probe_apply_sets_link_limits():
    clock, model, transport = rig(turnaround_s=0.010)
    link = Link(transport, protocol.DEFAULT, clock)
    p = probe_timing(link, reads=30, apply=True)
    assert link.timing.gap_floor == p.recommended["inter_request_delay_s"]
    assert link.timing.timeout_ceiling == p.recommended["reply_timeout_s"]


def test_sweep_marks_dead_addresses_byte_by_byte():
    class Holey(EcuModel):
        def handle(self, frame: bytes) -> bytes:
            addr, length = frame[2], frame[3]
            if addr <= 0x42 < addr + length:
                self.requests += 1
                return b""
            return super().handle(frame)
    clock = VirtualClock()
    model = Holey(clock=clock.now, rev_every_s=0)
    link = Link(LoopbackTransport(model, clock, byte_timing=True), protocol.DEFAULT, clock)
    s = sweep(link)
    assert s.answered == 255 and s.unanswered == [(0x42, 1)]
    assert "--" in s.hexdump().splitlines()[5]
    assert any("SIM-P2P" in text for _, text in s.ascii_runs())


# ---- trace -----------------------------------------------------------------

def test_trace_records_and_merges():
    clock, model, transport = rig()
    tr = TracingTransport(transport, clock)
    link = Link(tr, protocol.DEFAULT, clock)
    link.read_block(0x00, 2)
    assert tr.bytes_sent() == 5
    assert tr.bytes_received() == 5 + 5
    lines = tr.dump().splitlines()
    assert lines[0].split()[1:] == ["TX", "20", "05", "00", "02", "D9"]
    # the echo and the reply (2 ms later) each read as one line
    assert [line.split()[1] for line in lines] == ["TX", "RX", "RX"]
    assert lines[1].split()[2:] == lines[0].split()[2:] and lines[2].split()[2:4] == ["00", "05"]


# ---- adapter detection on a fake sysfs -------------------------------------

def fake_ftdi(tmp_path, serial: str = "A12345XY", manufacturer: str = "FTDI",
              latency: str = "16", bcd: str = "0600") -> tuple[str, str]:
    sysfs = tmp_path / "sys"
    usb = sysfs / "devices" / "usb1" / "1-1"
    port = usb / "1-1:1.0" / "ttyUSB0"
    port.mkdir(parents=True)
    for name, value in (("idVendor", "0403"), ("idProduct", "6001"), ("bcdDevice", bcd),
                        ("manufacturer", manufacturer), ("product", "FT232R USB UART"),
                        ("serial", serial)):
        (usb / name).write_text(value + "\n")
    (port / "latency_timer").write_text(latency + "\n")
    drv = sysfs / "bus" / "usb-serial" / "drivers" / "ftdi_sio"
    drv.mkdir(parents=True)
    os.symlink(drv, port / "driver")
    tty = sysfs / "class" / "tty" / "ttyUSB0"
    tty.mkdir(parents=True)
    os.symlink(port, tty / "device")
    dev = tmp_path / "ttyUSB0"
    dev.write_text("")
    return str(dev), str(sysfs)


def test_ftdi_genuine_looking(tmp_path):
    dev, sysfs = fake_ftdi(tmp_path)
    info = ftdi.detect(dev, sysfs)
    assert info.is_ftdi and info.chip == "FT232R" and info.verdict == "genuine-looking"
    assert info.latency_ms == 16
    assert any("udev" in n for n in info.notes)
    assert ftdi.write_latency_ms(dev, 1, sysfs)
    assert ftdi.read_latency_ms(dev, sysfs) == 1


@pytest.mark.parametrize("kw", [{"serial": "A50285BI"}, {"serial": ""},
                                {"manufacturer": "Prolific"}])
def test_ftdi_clone_suspected(tmp_path, kw):
    dev, sysfs = fake_ftdi(tmp_path, **kw)
    assert ftdi.detect(dev, sysfs).verdict == "clone-suspected"


def test_adapter_detect_sim_and_missing():
    assert ftdi.detect("sim:").kind == "simulator"
    info = ftdi.detect("/dev/does-not-exist-hondalog")
    assert info.kind == "unknown" and "does not exist" in info.notes[0]
