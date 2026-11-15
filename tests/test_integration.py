"""The tracks together: link -> pipeline -> recorder -> triggers -> log -> analysis -> export.

Everything here goes through ``hondalog.live.LiveSession``, the same object
the app, ``hondalog-log record`` and the smoke test use.
"""

import time

import numpy as np
import pytest

from hondalog import addressmap, logtool
from hondalog.analysis import Bins, FilterSet, TableHistogram, table_bins
from hondalog.blackbox import Car, Config, SimDevice, SimPort, open_event
from hondalog.data import open_log
from hondalog.data.redecode import redecode
from hondalog.export import export_log
from hondalog.export.mlg import read_mlg
from hondalog.link.clock import VirtualClock
from hondalog.live import BLACKBOX_STREAM, MATH_STREAM, LiveSession
from hondalog.logger import read_log
from hondalog.poller import Poller
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport
from hondalog.triggers import TriggerRule, stall_rules
from hondalog.tuning import RomImage, RomSession
from hondalog.tuning.demo import DEMO_MAP_KPA, DEMO_RPM, build_demo_rom, demo_definition


def run_until(clock, t, wall=30.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t, "virtual time did not advance (poller thread stuck?)"


def virtual_session(tmp_path, amap=None, rules=None, **model_kw):
    """A LiveSession on the simulated ECU at true 9600 baud byte timing, on a
    virtual clock: the run repeats exactly and takes a second or two."""
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, **model_kw)
    amap = amap or addressmap.load_default()
    session = LiveSession(amap, out_dir=tmp_path, sync_captures=True, fsync=False,
                          **({"rules": rules} if rules is not None else {"pre_s": 3, "post_s": 2}))
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    session.attach(poller)
    return clock, model, poller, session


@pytest.fixture()
def stall_run(tmp_path):
    """Drive for 5 s, stall (RPM to 0, ECU still answering), stop at 9 s."""
    clock, model, poller, session = virtual_session(tmp_path, rev_every_s=2.0, stall_after_s=5.0,
                                                    restart_after_s=None)
    blocks = []
    session.on_read = blocks.append
    session.start_recording()
    poller.start()
    try:
        assert poller.ready.wait(5)
        run_until(clock, 2.0)
        assert session.mark() == 1
        poller.submit(lambda link: link.unlock(True)).result(5)
        poller.submit(lambda link: link.raw(bytes.fromhex("21 04 00 db")))     # not a read: guard off
        run_until(clock, 9.0)
    finally:
        frames = list(session.frames)
        recorder = session.recorder
        session.close()
    return {"session": session, "recorder": recorder, "poller": poller, "blocks": blocks,
            "frames": frames, "dir": tmp_path}


def test_live_path_records_every_read_with_its_own_time_and_bytes(stall_run):
    rec, poller, blocks = stall_run["recorder"], stall_run["poller"], stall_run["blocks"]
    assert rec.write_error is None and poller.last_error == ""
    with open_log(rec.path) as log:
        plan = {f"ecu/{addr:02X}+{length}" for addr, length in poller.blocks}
        assert plan <= set(log.streams) and "ecu" not in log.streams      # reads, not sweeps
        assert {"ecu/link", MATH_STREAM} <= set(log.streams)

        # one row per read, stamped when the ECU sampled it, raw bytes kept
        fast = log.streams["ecu/00+3"]
        mine = [b for b in blocks if b.addr == 0x00 and len(b.data) == 3]
        assert fast.rows == len(mine) > 100 and fast.info.raw_len == 3
        np.testing.assert_allclose(fast.t, [b.t - b.latency_s for b in mine])
        assert [bytes(r) for r in fast.raw[:50]] == [b.data for b in mine[:50]]
        assert np.all(np.diff(fast.t) > 0)
        rpm = log.series("rpm")
        np.testing.assert_allclose(rpm.v[:50], [b.values["rpm"] for b in mine[:50]], rtol=1e-6)

        # the rate classes show in the file: fast ~17 Hz, slow 1 Hz, own timestamps
        assert rpm.rate_hz > 14 and 0.8 < log.series("ect").rate_hz < 1.3
        assert 3 < log.series("stft").rate_hz < 6
        assert not np.array_equal(log.series("map").t[:20], rpm.t[:20])

        # math channels came through the pipeline, at sample rate, with units
        duty = log.series("inj_duty")
        assert duty.rate_hz > 14 and duty.units == "%" and np.nanmax(duty.v) > 1
        assert log.streams[MATH_STREAM].info.source == "math"

        # the header: map version + hash (W3), link counters and unlocked frames (W1)
        amap = poller.amap
        meta = log.meta
        assert meta["address_map_tag"] == amap.version_tag and meta["address_map_hash"] == amap.version_hash
        assert meta["address_map_tag"].endswith("#" + amap.version_hash)
        assert meta["link"]["ok"] > 400 and meta["link"]["timeouts"] == 0 and "bus_load" in meta["link"]
        assert meta["unlocked_frames"] == ["21 04 00 db"] and meta["expert_unlock"] is True
        assert meta["read_plan"] == [f"{a:02X}+{n}" for a, n in poller.blocks]
        # (virtual time runs ahead of this thread, so "at 2 s" means "soon after")
        assert log.markers[0][1] == "MARK 1" and 2.0 <= log.markers[0][0] < 5.0


def test_stall_capture_reopens_with_raw_bytes_and_decodes_again(stall_run):
    rec = stall_run["recorder"]
    event = rec.events.get_nowait()
    assert event.reason == "rpm" and event.rule == "stall" and 5.0 <= event.t < 6.0   # the engine spins down
    assert event.path.name.startswith("stall_") and event.path.drive == rec.path.name and rec.events.empty()
    with open_log(event.path) as cap:
        assert cap.t0 == pytest.approx(event.t - 3, abs=0.1) and cap.t1 == pytest.approx(event.t + 2, abs=0.15)
        assert cap.meta["trigger"] == "stall" and cap.meta["capture_of"] == rec.path.name
        assert cap.meta["address_map_tag"] == stall_run["poller"].amap.version_tag
        assert {"ecu/00+3", "ecu/link", MATH_STREAM} <= set(cap.streams)
        rpm = cap.series("rpm")
        assert rpm.v[0] > 400 and rpm.v[-1] == 0
        assert [e.text for e in cap.events_of("trigger")] == ["STALL (rpm 0)"]

        # the bytes are in the capture: a corrected formula redecodes the same drive
        better = addressmap.load_default()
        better.version = "p2p-fixed"
        ch = better.channel("rpm")
        ch.formula = "x / 2"
        ch.__post_init__()
        again = redecode(cap, better)
        np.testing.assert_allclose(again.series("rpm").v, rpm.v * 2, rtol=1e-6)
        assert again.meta["redecoded_from"] == cap.meta["address_map_version"]

    flat = read_log(event.path)                     # the shape the replay tab draws
    assert flat.meta["stall_trigger"] == "rpm dropped to 0" and "rpm" in flat.names
    assert logtool.main(["info", str(event.path)]) == 0


def test_histogram_on_rom_axes_and_mlg_export(stall_run, tmp_path):
    rec = stall_run["recorder"]
    definition = demo_definition()
    rom = RomSession(RomImage.from_bytes(build_demo_rom(), definition.rom_size), definition,
                     store_dir=tmp_path / "roms")
    fuel = rom.table("fuel")
    x_bins, y_bins = table_bins(fuel)               # W5's table axes as W4's bins
    assert list(x_bins.centres) == DEMO_RPM and list(y_bins.centres) == DEMO_MAP_KPA
    assert x_bins == Bins.from_breakpoints(DEMO_RPM)

    with open_log(rec.path) as log:
        table = log.table(["rpm", "map", "stft", "inj", "ect"], base="rpm")
        running = FilterSet()
        running.add("running", "rpm > 400 and abs(rate(rpm)) < 4000")     # the one expression engine
        mask = running.mask(table)
        assert 0 < mask.sum() < len(table)
        hist = TableHistogram.for_table(table, fuel, "stft", mask=mask)
        assert hist.count.shape == fuel.shape == (len(DEMO_MAP_KPA), len(DEMO_RPM))
        assert hist.samples == int(mask.sum()) and hist.count.sum() == hist.samples
        assert np.count_nonzero(hist.count) >= 3                    # the revs moved through cells
        iy, ix = np.unravel_index(np.argmax(hist.count), hist.count.shape)
        rows = hist.rows(ix, iy)
        assert np.all(x_bins.index(table["rpm"][rows]) == ix) and len(hist.times(ix, iy)) == len(rows)
        assert abs(hist.stat("mean")[iy, ix] - np.nanmean(table["stft"][rows])) < 1e-6

        out = export_log(log, tmp_path / "drive.mlg", channels=["rpm", "map", "stft", "inj_duty"])
    back = read_mlg(out)
    assert {"rpm", "map", "stft", "inj_duty"} <= set(back.channels)
    with open_log(rec.path) as log:
        want = log.table(["rpm"], base="rpm")
        got = back.table(["rpm"], base="rpm")
        assert len(got) == len(want)
        np.testing.assert_allclose(got["rpm"], want["rpm"], atol=1.0)
    assert logtool.main(["export", str(rec.path), str(tmp_path / "again.mlg"), "--channels", "rpm,map"]) == 0


def test_triggers_use_history_functions_live_and_alarms_fire_captures(tmp_path):
    amap = addressmap.loads(addressmap.DEFAULT_MAP.read_text(encoding="utf-8") + '''
[[alarm]]
name = "rev_high"
channel = "rpm"
high = 2500
severity = "critical"
actions = ["banner", "marker", "capture"]
message = "RPM {value:.0f}"
''')
    rules = [*stall_rules(pre_s=1, post_s=1),
             TriggerRule("rpm_falling", when="rate(rpm) < -1500 and rpm > 300", pre_s=1, post_s=1,
                         actions=("mark",), label="RPM FALLING", rearm_when="rpm > 600"),
             TriggerRule("rev_high", when="0", pre_s=2, post_s=1)]          # sets the alarm's window
    clock, model, poller, session = virtual_session(tmp_path, amap=amap, rules=rules, rev_every_s=3.0,
                                                    stall_after_s=7.0, restart_after_s=None)
    session.start_recording()
    poller.start()
    try:
        assert poller.ready.wait(5)
        run_until(clock, 10.0)
    finally:
        recorder = session.recorder
        frames = list(session.frames)
        session.close()

    raised = [e for f in frames for e in f.events if e.kind == "raised" and e.name == "rev_high"]
    assert raised, "the simulated rev should have tripped the alarm"
    captures = []
    while not recorder.events.empty():
        captures.append(recorder.events.get_nowait())
    alarm = next(c for c in captures if c.reason == "alarm")
    assert alarm.path.name.startswith("alarm_")
    assert alarm.rule == "alarm_rev_high" and alarm.t == pytest.approx(raised[0].t)
    with open_log(alarm.path) as cap:
        assert cap.t0 == pytest.approx(alarm.t - 2, abs=0.15)       # window from the rule of that name
        assert cap.series("rpm").v.max() > 2500
    with open_log(recorder.path) as log:
        # the stall rule still did its job next to the alarm (own file or the same bundle)
        assert "STALL (rpm 0)" in [e.text for e in log.events_of("trigger")]
        alarms = log.events_of("alarm")
        assert alarms and alarms[0].data["alarm"] == "rev_high" and alarms[0].data["kind"] == "raised"
        assert any(text.startswith("ALARM rev_high") for _, text in log.markers)
        # rate() ran on live reads: it fired where RPM really fell, and only there
        falls = [e for e in log.events_of("trigger") if e.text == "RPM FALLING"]
        assert falls
        rpm = log.series("rpm")
        for e in falls:
            i = int(np.searchsorted(rpm.t, e.t, side="right")) - 1
            assert (rpm.v[i] - rpm.v[i - 1]) / (rpm.t[i] - rpm.t[i - 1]) < -1500, e.t


def test_black_box_and_ecu_share_one_timebase_and_one_capture(tmp_path):
    """Real time: the simulated ECU and the simulated black box (with a
    drifting clock) recorded by one session. The box sees ECU +B fall; that
    one event cuts one capture holding both sources on one time axis."""
    cfg = Config()
    cfg.set("pre_s", 3)
    cfg.set("post_s", 1)
    dev = SimDevice(Car(None, key_on_at=0.05, crank_at=0.1, start_at=0.3), cfg,
                    out_dir=tmp_path / "sd", drift_ppm=150.0)
    session = LiveSession(addressmap.load_default(), out_dir=tmp_path / "logs", pre_s=2, post_s=1,
                          sync_captures=True)
    poller = session.connect("sim:")
    try:
        assert poller.ready.wait(5)
        reader = session.open_blackbox(SimPort(dev), sync_period_s=0.2)
        assert reader.t0 == poller.t0                       # the shared t0
        assert reader.ready.wait(4) and reader.hello is not None
        recorder = session.start_recording()
        time.sleep(1.5)
        assert session.mark() == 1
        time.sleep(1.0)
        dev.fail("main_relay")
        t_fail = time.monotonic() - poller.t0
        time.sleep(1.8)
        bbx = reader.fetch_event(1, tmp_path / "logs")
    finally:
        session.close()
    assert session.last_error == "" and recorder.write_error is None

    captures = []
    while not recorder.events.empty():
        captures.append(recorder.events.get_nowait())
    assert len(captures) == 1, [c.path.name for c in captures]      # both trigger paths, one capture
    cap_event = captures[0]
    assert cap_event.reason == "external" and "black box" in cap_event.message
    assert abs(cap_event.t - t_fail) < 0.5

    with open_log(recorder.path) as log:
        assert BLACKBOX_STREAM in log.streams and "ecu/00+3" in log.streams
        assert log.streams[BLACKBOX_STREAM].info.source == "blackbox"
        assert "black box" in log.meta["blackbox"] and log.meta["epoch_unix"] > 1e9
        bb, rpm = log.series("bb_ecub_min"), log.series("rpm")
        assert bb.units == "V" and bb.rate_hz > 20 and rpm.rate_hz > 10
        assert bb.t[0] >= 0 and abs(bb.t[-1] - rpm.t[-1]) < 0.5      # same axis, same span
        # the rail fell when it fell, on the ECU log's clock (frame period is 20 ms)
        late = bb.t > 1.0                                   # after key-on and start
        drop = bb.t[late][np.argmax(bb.v[late] < 6)]
        assert abs(drop - t_fail) < 0.06
        # one marker, known to both sides under the same number
        assert [text for _, text in log.markers if text.startswith("MARK")] == ["MARK 1"]
        box_events = log.events_of("blackbox")
        assert any(e.data["name"] == "host_mark" or "marker" in e.text for e in box_events)
        assert any(e.data["event_no"] == 1 for e in box_events)

    with open_log(cap_event.path) as cap:                   # the bundle: both sources, same seconds
        assert {BLACKBOX_STREAM, "ecu/00+3", "ecu/link"} <= set(cap.streams)
        assert cap.meta["stall_trigger"].startswith("black box")
        rail = cap.series("bb_ecub_min")
        assert rail.v[0] > 10 and rail.v[-1] < 6 and cap.t0 < t_fail < cap.t1

    # the box's own 1 kHz capture lines up with the ECU log through the same clock
    merged = open_event(bbx, directory=tmp_path / "logs")
    assert merged.alignment.method == "wall" and merged.meta["bb_ecu_log"] == str(recorder.path)
    assert merged.verdict.cause == "main_relay"
    assert "rpm" in merged.names and "bb_ecub" in merged.names
    late = merged.t > 1.0
    drop_1k = merged.t[late][np.argmax(merged.data["bb_ecub"][late] < 6)]
    assert abs(drop_1k - t_fail) < 0.03


def test_hondalog_log_record_uses_the_same_path(tmp_path, capsys):
    assert logtool.main(["record", "--port", "sim:", "--bb", "bbsim:", "--seconds", "2.5",
                         "--map", str(addressmap.DEFAULT_MAP), "-o", str(tmp_path), "-q"]) == 0
    out = capsys.readouterr().out
    assert "recording drive_" in out and "saved drive_" in out
    assert [p.name for p in tmp_path.glob("*.db")] == ["hondalog.db"]
    with open_log(tmp_path / "hondalog.db") as log:
        assert log.status == "complete" and log.streams[BLACKBOX_STREAM].rows > 50
        assert {"ecu/00+3", MATH_STREAM, BLACKBOX_STREAM} <= set(log.streams)
        assert log.streams["ecu/00+3"].info.raw_len == 3 and log.series("rpm").rate_hz > 10
        assert log.meta["address_map_tag"] == addressmap.load_default().version_tag
        assert log.meta["link"]["ok"] > 50
