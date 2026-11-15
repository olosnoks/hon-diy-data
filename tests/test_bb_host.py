"""Host side of the black box: simulator, file round trip, verdicts, the live
reader, clock sync, alignment with the ECU log, and the replay importer."""

import io
import os
import threading
import time
from datetime import datetime

import numpy as np
import pytest

from hondalog import addressmap
from hondalog.blackbox import (BB_CHANNELS, LIVE_NAMES, BlackboxReader, Car, ClockSync, Config,
                               SimDevice, SimPort, bb_channel, bundle, diagnose, estimate,
                               export_csv, find_ecu_log, open_event, read_event, write_event)
from hondalog.blackbox import format as F
from hondalog.blackbox.diagnose import timeline
from hondalog.blackbox.eventfile import CSV_COLUMNS, EventFileError
from hondalog.blackbox.sim import serve
from hondalog.logger import Recorder, read_log
from hondalog.poller import OK, Sample

BOOT = 5_000_000


def capture(fault, at=6.0, total=11.0, pre=5, post=3, **dev):
    cfg = Config()
    cfg.set("pre_s", pre)
    cfg.set("post_s", post)
    d = SimDevice(Car(fault, at), cfg, **dev)
    d.advance(total)
    return d


@pytest.fixture(scope="module")
def captures(tmp_path_factory):
    out = {}
    for fault in ("main_relay", "igniter", "fuel_pump", "key_off", "battery"):
        d = capture(fault, out_dir=tmp_path_factory.mktemp(fault))
        out[fault] = (d, read_event(d.core.finished[0]))
    return out


# ---- simulator signatures and verdicts --------------------------------------

@pytest.mark.parametrize("fault,cause,first", [
    ("main_relay", "main_relay", "ecub"), ("igniter", "igniter", "tach"),
    ("fuel_pump", "fuel_pump", "pump"), ("key_off", "ig1", "ig1"), ("battery", "none", None)])
def test_each_fault_has_its_signature_and_verdict(captures, fault, cause, first):
    _, ev = captures[fault]
    assert ev.complete and not ev.warnings
    v = diagnose(ev)
    assert v.cause == cause
    if first:
        assert v.steps[0].signal == first and v.steps[0].t_ms == 0
        assert v.title in v.text()


def test_main_relay_signature_details(captures):
    _, ev = captures["main_relay"]
    ch = ev.channels()
    t = ev.seconds()
    order = {sig: us for us, sig, what in timeline(ev) if what in ("fell below 6 V", "first missing pulse")}
    assert order["ecub"] < order["tach"] and "ig1" not in order and "vbat" not in order
    after = t > (order["ecub"] - ev.trigger_t_us) / 1e6 + 0.1
    assert ch["bb_ecub"][after].max() < 1.0 and ch["bb_ig1"][after].min() > 11.5
    assert ch["bb_rpm_tach"][-1] == 0
    assert any("intermittent" in n for n in diagnose(ev).notes)      # the relay chattered first


def test_igniter_signature_details(captures):
    _, ev = captures["igniter"]
    tach, _ = ev.edges("tach")
    ign, _ = ev.edges("ign")
    assert ign[-1] - tach[-1] > 500_000             # the ECU kept commanding spark
    assert ev.channels()["bb_ecub"].min() > 9.0     # and stayed powered (9.x V is cranking)


def test_fuel_pump_engine_runs_on_then_starves(captures):
    _, ev = captures["fuel_pump"]
    order = dict((sig, us) for us, sig, _ in timeline(ev))
    assert 1.0e6 < order["tach"] - order["pump"] < 3.0e6


def test_healthy_car_never_triggers(tmp_path):
    d = capture(None, total=25.0, out_dir=tmp_path)
    assert not d.core.finished and not [e for e in d.core.events if e.is_trigger]


def test_button_and_host_trigger(tmp_path):
    cfg = Config()
    cfg.set("pre_s", 2)
    cfg.set("post_s", 1)
    cfg.set("holdoff_s", 0)
    d = SimDevice(Car(None), cfg, out_dir=tmp_path)
    d.advance(4)
    d.press_button()
    d.advance(6)
    d.rx(F.frame_encode(F.F_TRIGGER))
    d.advance(8)
    assert [read_event(p).reason for p in d.core.finished] == [F.R_BUTTON, F.R_HOST]


# ---- file round trip --------------------------------------------------------

def test_event_file_round_trip(captures, tmp_path):
    _, ev = captures["main_relay"]
    for newest_first in (True, False):
        back = read_event(write_event(tmp_path / "copy.bbx", ev, newest_first))
        assert back.complete and not back.warnings
        assert np.array_equal(back.t_us, ev.t_us) and np.array_equal(back.samples, ev.samples)
        assert np.array_equal(back.edge_t_us, ev.edge_t_us) and np.array_equal(back.edge_ch, ev.edge_ch)
        assert [(e.t_us, e.code, e.value) for e in back.events] == [(e.t_us, e.code, e.value) for e in ev.events]
        assert back.config.dump() == ev.config.dump() and back.reason == ev.reason


def test_damaged_files(captures, tmp_path):
    _, ev = captures["igniter"]
    data = bytearray(ev.path.read_bytes())

    cut = tmp_path / "cut.bbx"
    cut.write_bytes(data[:len(data) // 3])
    got = read_event(cut)
    assert not got.complete and got.reason == ev.reason and 0 < len(got.t_us) < len(ev.t_us)
    assert got.t_us[-1] == ev.trigger_t_us          # newest pre-trigger data is first on disk

    flipped = bytearray(data)
    flipped[len(data) // 2] ^= 0xFF                 # one bad byte costs one block only
    bad = tmp_path / "bad.bbx"
    bad.write_bytes(flipped)
    got = read_event(bad)
    assert got.blocks_bad == 1 and len(ev.t_us) - len(got.t_us) <= F.BLOCK_SAMPLES
    assert any("damaged" in w for w in got.warnings)

    for junk in (b"", b"not a capture" * 10, bytes(data[:60])):
        (tmp_path / "junk.bbx").write_bytes(junk)
        with pytest.raises(EventFileError):
            read_event(tmp_path / "junk.bbx")
    hdr = bytearray(data)
    hdr[16] ^= 1                                    # header CRC catches a changed reason
    (tmp_path / "junk.bbx").write_bytes(hdr)
    with pytest.raises(EventFileError, match="checksum"):
        read_event(tmp_path / "junk.bbx")


def test_csv_export(captures, tmp_path):
    _, ev = captures["key_off"]
    path = export_csv(ev, tmp_path / "ev.csv")
    lines = path.read_text().splitlines()
    head = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]
    assert "ig1_drop" in head[0] and any("# event:" in ln for ln in head)
    assert body[0].split(",") == list(CSV_COLUMNS) and len(body) == 1 + len(ev.t_us)
    row = dict(zip(CSV_COLUMNS, body[100].split(",")))
    assert float(row["vbat_v"]) == pytest.approx(ev.samples["mv"][99, 0] / 1000, abs=1e-3)
    assert float(row["time_s"]) == pytest.approx(ev.t_us[99] / 1e6, abs=1e-3)


def test_channel_metadata():
    names = [c.name for c in BB_CHANNELS]
    assert len(set(names)) == len(names) and all(n.startswith("bb_") for n in names)
    assert set(LIVE_NAMES) <= set(names) and bb_channel("bb_ecub").units == "V"
    assert bb_channel("rpm") is None
    amap = addressmap.load_default()
    assert not set(names) & set(amap.names)         # no clash with ECU channels


# ---- clock ------------------------------------------------------------------

def test_clock_sync_picks_the_cleanest_exchange():
    rng = np.random.default_rng(2)
    clock = ClockSync()
    true_offset = 123_456_789
    for i in range(40):
        dev = 1_000_000 * i
        up, down = (int(rng.uniform(150, 9000)) for _ in range(2))     # USB jitter, both ways
        clock.add(dev + true_offset - up, dev, dev + 20, dev + 20 + true_offset + down)
    assert clock.valid and abs(clock.offset_us - true_offset) < 2000
    assert clock.to_host_us(5) == 5 + clock.offset_us
    clock.add(100, 50, 60, 90)                      # impossible exchange is ignored
    assert clock.count == 40


# ---- live reader ------------------------------------------------------------

def start(port, **kw):
    r = BlackboxReader(port, sync_period_s=0.2, **kw)
    r.start()
    assert r.ready.wait(4) and r.hello is not None
    return r


def test_reader_looks_like_a_poller_and_does_requests(tmp_path):
    cfg = Config()
    cfg.set("pre_s", 2)
    cfg.set("post_s", 1)
    dev = SimDevice(Car(None, key_on_at=0.05, crank_at=0.1, start_at=0.3), cfg, out_dir=tmp_path / "sd")
    got, evs = [], []
    r = start(SimPort(dev), on_sample=got.append, on_event=evs.append)
    try:
        time.sleep(1.6)
        assert r.state == OK and 40 < r.hz < 60 and r.errors == 0 and r.reads > 50
        s = r.samples[-1]
        assert isinstance(s, Sample) and s.link and set(s.values) == set(LIVE_NAMES)
        assert 600 < s.values["bb_rpm_tach"] < 3500 and s.values["bb_running"] == 1.0
        assert abs(s.t - (time.monotonic() - r.t0)) < 0.1 and got[-1] is s
        ts = [x.t for x in got]
        assert ts == sorted(ts)
        assert "black box SIM" in r.device_text and r.status_bits & F.ST_RUNNING
        assert r.clock.valid and r.clock.count >= 5 and dev.core.time_valid
        assert abs(dev.core.unix_us_at_t0 / 1e6 + dev.now_us() / 1e6 - time.time()) < 0.05

        r.set_config("rule.6", "vbat < 10500 500 50 running capture")
        with pytest.raises(Exception, match="rejected"):
            r.set_config("rule.6", "vbat < banana")
        assert Config.parse(r.get_config()).rules[6].level == 10500
        r.save_config()
        assert (tmp_path / "sd" / "BLACKBOX.CFG").exists()
        assert "selftest: PASS" in r.selftest()

        r.mark(3)
        r.trigger()
        time.sleep(1.5)
        names = [e.name for e in evs]
        assert names[-2:] == ["host_mark", "host"] and evs[-1].started_file and evs[-1].event_no == 1
        assert abs(evs[-1].t - (time.monotonic() - r.t0 - 1.5)) < 0.1
        assert r.events.qsize() == len(evs)
        infos = r.list_events()
        assert [(i.event_no, i.reason_name, i.complete) for i in infos] == [(1, "host", True)]
        seen = []
        path = r.fetch_event(1, tmp_path / "dl", progress=seen.append)
        assert path.read_bytes() == dev.core.finished[0].read_bytes() and seen[-1] == infos[0].size
        assert path.name.startswith("bb_20") and path.suffix == ".bbx"
        with pytest.raises(Exception, match="not on the card"):
            r.fetch_event(77, tmp_path / "dl")

        dev.connected = False                       # cable out: samples keep coming, link False
        time.sleep(1.5)
        assert r.state != OK and r.samples[-1].link is False and r.hz == 0
    finally:
        r.stop()
    assert not r.is_alive()


def test_reader_on_a_pty(tmp_path):
    pytest.importorskip("serial")
    link = str(tmp_path / "pty")
    dev = SimDevice(Car("igniter", 1.2, key_on_at=0.05, crank_at=0.1, start_at=0.3), out_dir=tmp_path / "sd")
    th = threading.Thread(target=serve, daemon=True, kwargs=dict(
        device=dev, link_path=link, keys=False, run_for=4, out=io.StringIO()))
    th.start()
    for _ in range(100):
        if os.path.exists(link):
            break
        time.sleep(0.01)
    r = start(link)
    try:
        time.sleep(2.5)
        assert r.state == OK and r.errors == 0
        assert "tach_stop" in [e.name for e in list(r.events.queue)]
        assert r.samples[-1].values["bb_rpm_tach"] == 0
    finally:
        r.stop()
        th.join(6)


def test_reader_reports_a_missing_port():
    r = BlackboxReader("/dev/does-not-exist-bb")
    r.start()
    assert r.ready.wait(3) and r.hello is None and "port" in r.last_error
    r.stop()


# ---- alignment and import ---------------------------------------------------

def ecu_log_for(dev, tmp_path, origin_unix, offset_s, n_marks=(), hz=10.0, stop_s=None):
    """Write the ECU log a laptop would have recorded next to this box: RPM
    from the same car at 10 Hz. ECU time = box time + offset_s."""
    amap = addressmap.load_default()
    rec = Recorder(amap, out_dir=tmp_path)
    car = Car(dev.car_fault, dev.car_fault_at)
    rpm_at = {}
    for n in range(1, int((stop_s or 11.0) * 1000) + 1):
        car.tick(n)
        rpm_at[n] = car.rpm
    period = int(1000 / hz)
    for n in range(period, len(rpm_at) + 1, period):
        box_s = (BOOT + n * 1000) / 1e6
        t = box_s + offset_s
        if t < 0:
            continue
        for m in n_marks:
            if n - period < m[1] <= n:
                rec.mark(f"MARK {m[0]}")
        # the sweep that finished at t read RPM about half a sweep earlier
        rec.feed(Sample(t, datetime.fromtimestamp(origin_unix + t), True,
                        {"rpm": float(round(rpm_at[max(1, n - period // 2)])), "batt": 13.9}))
    rec.stop()
    return rec.path


@pytest.fixture
def paired(tmp_path):
    """A capture and the ECU log of the same drive, true offset known."""
    def make(clock=True, marks=False, fault="main_relay"):
        offset = -3.217                             # the app connected 3.2 s after the box booted
        origin = 1_800_000_000.25
        d = capture(fault, out_dir=tmp_path / "sd")
        d.car_fault, d.car_fault_at = fault, 6.0
        mark_ticks = [(1, 3050), (2, 4520)] if marks else []
        d2 = SimDevice(Car(fault, 6.0), d.core.cfg, out_dir=tmp_path / "sd2")
        if clock:
            d2.rx(F.frame_encode(F.F_TIME_SET, int((origin + offset) * 1e6 + 1500).to_bytes(8, "little", signed=True)))
        for no, tick in mark_ticks:
            d2.advance(tick / 1000)
            d2.rx(F.frame_encode(F.F_MARK, no.to_bytes(4, "little")))
        d2.advance(11.0)
        d2.car_fault, d2.car_fault_at = fault, 6.0
        log = ecu_log_for(d2, tmp_path / "logs", origin, offset, mark_ticks)
        return d2.core.finished[0], log, offset
    return make


@pytest.mark.parametrize("clock,marks,method,tol,fault", [
    (True, True, "wall", 0.020, "main_relay"), (True, False, "wall", 0.020, "main_relay"),
    (False, True, "markers", 0.060, "main_relay"),
    # RPM matching needs both sides to see the same RPM: a stall the ECU lives through
    (False, False, "rpm", 0.120, "fuel_pump")])
def test_alignment_methods(paired, clock, marks, method, tol, fault):
    bbx, log, offset = paired(clock, marks, fault)
    a = estimate(read_log(log), read_event(bbx))
    assert a.ok and a.method == method
    assert abs(a.offset_s - offset) < tol           # "wall" meets the 20 ms target
    assert a.error_s <= tol


def test_alignment_gives_up_honestly(paired, tmp_path):
    bbx, log, _ = paired(False, False)
    ecu = read_log(log)
    ecu.data["rpm"][:] = 800.0                      # flat RPM, no clock, no markers
    a = estimate(ecu, read_event(bbx))
    assert not a.ok and a.method == "none" and a.notes


def test_open_event_merges_the_matching_ecu_log(paired, tmp_path):
    bbx, log_path, offset = paired(True, True)
    (tmp_path / "logs" / "log_other.csv").write_text("# hondalog\ntime_s,wall_time,link,rpm,marker\n"
                                                     "0.0,2020-01-01T00:00:00.000,1,800,\n")
    ev = read_event(bbx)
    assert find_ecu_log(ev, tmp_path / "logs") == log_path

    log = open_event(bbx, directory=tmp_path / "logs")
    assert log.alignment.method == "wall" and log.verdict.cause == "main_relay"
    assert "rpm" in log.names and "bb_ecub" in log.names and "bb_rpm_tach" in log.names
    assert len(log.t) == len(ev.t_us) and all(len(log.data[n]) == len(log.t) for n in log.names)
    stall_t = float(log.meta["stall_time_s"])
    assert stall_t == pytest.approx(ev.trigger_t_us / 1e6 + offset, abs=0.02)
    assert log.meta["stall_trigger"].startswith("black box:") and "wall" in log.meta["bb_alignment"]
    texts = [m[1] for m in log.markers]
    assert any(t.startswith("MARK 1") for t in texts) and any(t.startswith("BB TRIGGER") for t in texts)
    assert [m[0] for m in log.markers] == sorted(m[0] for m in log.markers)
    # both RPM sources agree once aligned (ECU value is up to one poll old)
    run = (log.t > stall_t - 3) & (log.t < stall_t - 0.6)
    assert np.nanmean(np.abs(log.data["rpm"][run] - log.data["bb_rpm_tach"][run])) < 120
    # the ECU's battery column is still there as it was logged
    assert np.nanmax(log.data["batt"]) == pytest.approx(13.9)

    assert read_log(bbx).names[-1].startswith("bb_")            # read_log routes .bbx here
    alone = open_event(bbx, ecu_log=None)
    assert "rpm" not in alone.names and float(alone.meta["stall_time_s"]) == 0.0
    assert alone.t[0] == pytest.approx(-5.0, abs=0.01) and alone.alignment is None

    folder = bundle(bbx, log_path, tmp_path / "bundles")
    import json
    info = json.loads((folder / "bundle.json").read_text())
    assert info["verdict"] == "main_relay" and info["alignment"] == "wall"
    assert abs(info["offset_s"] - offset) < 0.02
    # the drive travels as a small database of its own, next to the .bbx
    from hondalog.data import open_log
    assert info["ecu_log"].startswith("ecu.db#drive_") and (folder / bbx.name).exists()
    with open_log(folder / "ecu.db") as copy, open_log(log_path) as original:
        assert copy.rows == original.rows and copy.meta["epoch_unix"] == original.meta["epoch_unix"]
        assert copy.meta["exported_from"] == str(log_path)
    assert estimate(read_log(folder / "ecu.db"), ev).method == "wall"       # and still lines up there


def test_bench_event_on_both_paths_lines_up_within_20ms(tmp_path):
    """End to end, in real time: the reader syncs clocks with a (drifting)
    box, an ECU log records alongside, the stall hits, the capture is
    downloaded and merged. The moment ECU +B fell must land within 20 ms of
    when it really happened on the ECU log's clock."""
    cfg = Config()
    cfg.set("pre_s", 3)
    cfg.set("post_s", 1)
    dev = SimDevice(Car(None, key_on_at=0.05, crank_at=0.1, start_at=0.3), cfg,
                    out_dir=tmp_path / "sd", drift_ppm=150.0)
    rec = Recorder(addressmap.load_default(), out_dir=tmp_path / "logs")
    r = start(SimPort(dev), on_event=lambda e: e.started_file and rec.external_trigger(f"black box: {e.name}"))
    try:
        stop = threading.Event()

        def ecu():                                  # a 10 Hz "poller" on the same clock
            while not stop.wait(0.1):
                rec.feed(Sample(time.monotonic() - r.t0, datetime.now(), True,
                                {"rpm": float(dev.car.rpm)}))
        th = threading.Thread(target=ecu, daemon=True)
        th.start()
        time.sleep(2.5)
        dev.fail("main_relay")
        t_fail = time.monotonic() - r.t0
        time.sleep(2.2)
        stop.set()
        th.join()
        path = r.fetch_event(1, tmp_path / "logs")
    finally:
        r.stop()
        rec.stop()
    stall = rec.events.get_nowait()                 # the black box fired the K-line capture too
    assert stall.reason == "external" and "black box" in read_log(stall.path).meta["stall_trigger"]

    log = open_event(path, directory=tmp_path / "logs")
    assert log.alignment.method == "wall" and log.verdict.cause == "main_relay", log.verdict.text()
    assert log.meta["bb_ecu_log"] == str(rec.path)
    drop_us = next(us for us, sig, what in timeline(log.blackbox) if sig == "ecub" and what == "fell below 6 V")
    assert abs(float(log.alignment.to_ecu_s(drop_us)) - t_fail) < 0.020
