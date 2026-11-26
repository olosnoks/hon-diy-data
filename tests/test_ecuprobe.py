"""ECU probe: the read-only whitelist and a full run on the simulator."""

from __future__ import annotations

import pytest

from hondalog.diag import ecuprobe as ep
from hondalog.link import VirtualClock
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport


def rig(**kw: object) -> tuple[VirtualClock, LoopbackTransport]:
    clock = VirtualClock(1000.0)
    model = EcuModel(clock=clock.now, rev_every_s=0, **kw)    # type: ignore[arg-type]
    return clock, LoopbackTransport(model, clock, byte_timing=True)


# ---- whitelist ---------------------------------------------------------------

@pytest.mark.parametrize("sid", [0x04, 0x08, 0x11, 0x14, 0x27, 0x2E, 0x2F, 0x30, 0x31,
                                 0x34, 0x35, 0x36, 0x37, 0x3B, 0x3D, 0x00, 0x3E, 0x85])
def test_whitelist_blocks_writes(sid):
    with pytest.raises(ep.ProbeBlocked):
        ep.check_iso(bytes([sid, 0x00]))


@pytest.mark.parametrize("payload", ["1003", "1085", "1002", "10"])
def test_whitelist_blocks_non_default_session(payload):
    with pytest.raises(ep.ProbeBlocked):
        ep.check_iso(bytes.fromhex(payload))


@pytest.mark.parametrize("payload", ["0100", "020c00", "03", "050101", "0600", "07", "0902",
                                     "1a80", "2101", "22f190", "2300000004", "1081"])
def test_whitelist_allows_reads(payload):
    ep.check_iso(bytes.fromhex(payload))


def test_every_planned_request_is_whitelisted():
    for _, payloads, _ in ep.iso_plan():
        for p in payloads:
            ep.check_iso(p)
    for c in ep.HONDA_CANDIDATES:
        ep.check_honda(c)


def test_honda_entry_not_read_only_is_refused_and_never_sent(monkeypatch):
    clock, transport = rig()
    bad = ep.HondaCandidate("write?", 9600, 0.0, None, bytes.fromhex("30050002"),
                            read_only=False)
    with pytest.raises(ep.ProbeBlocked):
        ep.check_honda(bad)
    monkeypatch.setattr(ep, "HONDA_CANDIDATES", (bad,))
    monkeypatch.setattr(ep, "iso_plan", lambda: [("A bad", [b"\x04", b"\x2e\x00\x00"], False)])
    r = ep.run("sim:", transport=transport, clock=clock)
    kinds = [e.kind for e in r.exchanges]
    assert kinds == [ep.BLOCKED, ep.BLOCKED, ep.BLOCKED]
    sent = [e.data for e in _trace_tx(r)]
    assert not any(d[3:4] == b"\x04" or d[3:4] == b"\x2e" for d in sent)
    assert not any(d.startswith(b"\x30\x05") for d in sent)


def _trace_tx(r: ep.ProbeReport):
    class E:
        def __init__(self, data: bytes) -> None:
            self.data = data
    out = []
    for line in r.trace.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == "TX":
            out.append(E(bytes.fromhex(parts[2])))
    return out


def test_honda_checksum_is_twos_complement():
    c = ep.HONDA_CANDIDATES[0]
    assert ep.honda_frame(c) == bytes.fromhex("20050002d9")
    frames = {ep.honda_frame(c) for c in ep.HONDA_CANDIDATES}
    # the kerpz frames, checksums as published
    assert {bytes.fromhex(h) for h in ("20050010cb", "20051010bb", "20052010ab", "2005760a5b")} <= frames


def test_only_honda_read_commands_are_allowed():
    for c in ep.HONDA_CANDIDATES:
        ep.check_honda(c)
        assert c.request[0] == 0x20
    reset = ep.HondaCandidate("reset", 9600, 0, None, bytes.fromhex("210401"), read_only=True)
    with pytest.raises(ep.ProbeBlocked):
        ep.check_honda(reset)


# ---- full runs on the simulator ---------------------------------------------

@pytest.mark.parametrize("honda", [True, False])
def test_full_run_on_sim_finishes_and_reports(honda):
    clock, transport = rig(honda_protocol=honda, dtcs=("P0135",))
    r = ep.run("sim:" if honda else "sim:iso", transport=transport, clock=clock)
    assert r.iso_init
    assert r.elapsed_s < 90
    by_req = {e.request: e for e in r.exchanges}
    assert by_req[b"\x01\x00"].kind == ep.POSITIVE
    assert "0C" in by_req[b"\x01\x00"].meaning
    assert by_req[b"\x02\x0c\x00"].kind == ep.POSITIVE
    assert by_req[b"\x21\x00"].kind == ep.SILENT
    assert not any(e.kind == ep.BLOCKED for e in r.exchanges)
    # manufacturer stages stop early on silence
    assert sum(e.request[:1] == b"\x21" for e in r.exchanges) == ep.SILENT_STOP
    assert any("0x21" in n and "stopped" in n for n in r.notes)
    honda_rows = [e for e in r.exchanges if e.stage.startswith("B")]
    assert len(honda_rows) == len(ep.HONDA_CANDIDATES)
    first = honda_rows[0]
    assert (first.kind == ep.POSITIVE) == honda
    md = r.to_markdown()
    assert "| stage | service | request | reply | meaning |" in md
    assert "## Byte trace" in md and "BREAK" in md and "TX    20 05 00 02 D9" in md
    # interesting hits first: the Honda reply (sim:) or a positive OBD reply (sim:iso)
    table = [line for line in md.splitlines() if line.startswith("| ")][1:]
    assert ("positive" not in table[0]) and ("no reply" not in table[0])


def test_main_writes_report(tmp_path, monkeypatch):
    clock, transport = rig(honda_protocol=False)
    real_run = ep.run
    monkeypatch.setattr(ep, "run", lambda port, **kw: real_run(port, transport=transport,
                                                                clock=clock, **kw))
    out = tmp_path / "p.md"
    assert ep.main(["--port", "sim:iso", "-o", str(out)]) == 0
    assert "# hondalog ECU probe" in out.read_text(encoding="utf-8")
    assert (tmp_path / "p.hex.log").read_text(encoding="utf-8").count("TX") > 20
