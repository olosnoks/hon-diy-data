"""The firmware's portable C core (bbcore), compiled for the host and driven
through ctypes: sizes, checksums, config, trigger rules against the Python
mirror, the engine end to end, crash safety, and the USB protocol."""

import ctypes as C
import struct

import numpy as np
import pytest

import bbhost
from hondalog.blackbox import format as F
from hondalog.blackbox.diagnose import diagnose
from hondalog.blackbox.eventfile import CSV_COLUMNS, read_event
from hondalog.blackbox.sim import Car, CoreModel
from hondalog.blackbox.triggers import Config, TriggerEngine

BOOT = 5_000_000


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = bbhost.lib(tmp_path_factory.mktemp("bbhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


@pytest.fixture
def box(L, tmp_path):
    made = []

    def make(cfg_text="", **kw):
        b = bbhost.Box(L, tmp_path / f"sd{len(made)}", cfg_text, **kw)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def run(b, car, seconds, start=0, boot=BOOT, **kw):
    n0 = int(start * 1000)
    for n in range(n0 + 1, n0 + int(seconds * 1000) + 1):
        b.feed(car.tick(n), boot, **kw)


# ---- layouts and primitives -------------------------------------------------

def test_struct_sizes_match_python(L):
    sizes = (C.c_uint32 * 16)()
    n = L.bbhost_sizes(sizes)
    assert list(sizes[:n - 1]) == [
        F.SAMPLE_DTYPE.itemsize, F.EVENT_DTYPE.itemsize, F.GPS_DTYPE.itemsize, F.FILE_HDR.size,
        F.BLOCK_HDR.size, F.INDEX_DTYPE.itemsize, F.END_REC.size, F.HELLO.size, F.LIVE.size,
        F.EVENT_MSG.size, F.TIME_RSP.size, F.FILE_INFO.size, F.READ_FILE.size]
    assert sizes[n - 1] == C.sizeof(bbhost.Status)


def test_core_selftest_passes(L):
    assert L.bb_core_selftest() == 0


def test_checksums_and_frames_match_python(L):
    rng = np.random.default_rng(1)
    for size in (0, 1, 9, 253, 254, 255, 300, 512):
        data = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
        if size % 2:
            data = data.replace(b"\x01", b"\x00")           # plenty of zeros to stuff
        assert L.bb_crc32(0, data, len(data)) == F.crc32(data)
        assert L.bb_crc16(data, len(data)) == F.crc16(data)
        out = C.create_string_buffer(len(data) + 16)
        n = L.bb_cobs_encode(data, len(data), out)
        assert out.raw[:n] == F.cobs_encode(data) and F.cobs_decode(out.raw[:n]) == data
        wire = C.create_string_buffer(len(data) + 32)
        n = L.bb_frame_encode(0x42, 7, data, len(data), wire)
        assert wire.raw[:n] == F.frame_encode(0x42, data, 7)
        assert F.Deframer().feed(wire.raw[:n]) == [(0x42, 7, data)]


def test_extend32_handles_wrap(L):
    assert L.bb_extend32(0x1_0000_0010, 0x0000_0005) == 0x1_0000_0005
    assert L.bb_extend32(0x1_0000_0010, 0xFFFF_FFF0) == 0x0_FFFF_FFF0      # capture just before the wrap
    assert L.bb_extend32(0x0_FFFF_FFF0, 0x0000_0004) == 0x1_0000_0004      # hint just before, capture after


def test_led_patterns_differ(L):
    shapes = {s: tuple(L.bb_led_pattern(s, t) for t in range(0, 2000, 10)) for s in range(7)}
    assert len(set(shapes.values())) == 7


def cdump(L, text=None):
    out = C.create_string_buffer(8192)
    bad, line = C.c_int(), C.c_int()
    n = L.bbhost_config_dump(text.encode() if text is not None else None, out, len(out), bad, line)
    return out.raw[:n].decode(), bad.value, line.value


def test_config_defaults_match_python(L):
    text, bad, _ = cdump(L)
    assert bad == 0 and text == Config().dump()
    assert len(text) < 2500                 # fits the firmware's buffers


def test_config_parse_matches_python(L):
    src = ("# my car\npre_s = 20\nvbat_scale=8012  # measured\n"
           "rule.6 = vbat < 10500 500 50 running capture\n"
           "rule.12 = slew_vbat < -2000 1500 2 ig1+running mark\n"
           "rule.10 = off\n")
    text, bad, _ = cdump(L, src)
    cfg = Config.parse(src)
    assert bad == 0 and text == cfg.dump()
    assert cfg.rules[12].qual == ("ig1", "running") and cfg.rules[10].action == "off"
    assert Config.parse(text).dump() == text


@pytest.mark.parametrize("line", ["pre_s = 0", "nonsense = 1", "pre_s", "rule.8 = vbat < 1 0 1 - mark",
                                  "rule.5 = vbat << 1 0 1 - mark", "rule.5 = vbat < 1 0 0 - mark",
                                  "rule.5 = vbat < 1 0 1 ig2 mark", "rule.40 = vbat < 1 0 1 - mark",
                                  "rule.5 = volts < 1 0 1 - mark", "live_hz = fast"])
def test_config_rejects_bad_lines_like_python(L, line):
    text, bad, bad_line = cdump(L, "post_s = 5\n" + line + "\n")
    assert bad == 1 and bad_line == 2
    assert "post_s = 5" in text             # the good line still took effect
    cfg = Config.parse("post_s = 5\n" + line + "\n", strict=False)
    assert [no for no, _ in cfg.errors] == [2] and cfg.dump() == text


def nmea(body: str) -> bytes:
    check = 0
    for c in body.encode():
        check ^= c
    return f"${body}*{check:02X}\r\n".encode()


def test_nmea(L):
    from datetime import datetime, timezone
    fix = np.zeros(1, dtype=F.GPS_DTYPE)
    gga = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"    # textbook sentence
    rmc = nmea("GNRMC,083559.00,A,3216.8200,N,09007.4400,W,022.4,084.4,151126,,,A")
    assert L.bbhost_nmea(gga + rmc, fix.ctypes.data) == 1
    f = fix[0]
    assert f["fix"] == 1 and f["sats"] == 8
    assert abs(f["lat_e7"] / 1e7 - (32 + 16.82 / 60)) < 1e-6
    assert abs(f["lon_e7"] / 1e7 + (90 + 7.44 / 60)) < 1e-6
    assert abs(f["speed_cms"] - 22.4 * 51.444) < 2 and f["course_cdeg"] == 8440
    assert f["utc"] == int(datetime(2026, 11, 15, 8, 35, 59, tzinfo=timezone.utc).timestamp())
    assert L.bbhost_nmea(rmc.replace(b"*", b"*0"), fix.ctypes.data) == 0        # bad checksum
    assert L.bbhost_nmea(nmea("GPRMC,083600.00,V,,,,,,,151126,,,N"), fix.ctypes.data) == 1
    assert fix[0]["fix"] == 0                                                   # no fix yet


# ---- trigger rules: C against Python on identical input ---------------------

@pytest.mark.parametrize("fault", ["main_relay", "igniter", "fuel_pump", "key_off", "battery", None])
def test_trigger_rules_c_equals_python(L, fault):
    cfg = Config()
    cfg.set("pre_s", 5)
    cfg.set("rule.12", "slew_vbat < -1500 1000 2 running mark")     # a user rule too
    rows = []
    core = CoreModel(cfg)
    real_step = core.trig.step
    core.trig.step = lambda i: (rows.append(i), real_step(i))[1]
    car = Car(fault, 9.0)
    for n in range(1, 16001):
        core.feed(car.tick(n))
    if fault:
        car.press_button(16.0)
    for n in range(16001, 17001):
        core.feed(car.tick(n))

    py = TriggerEngine(cfg)
    h = L.bbhost_trig_new(cfg.dump().encode())
    out = (C.c_int32 * 48)()
    dig = C.c_uint8()
    fired_c, fired_py = [], []
    for i in rows:
        row = (C.c_int32 * 23)(*i.mv, *i.min_mv, *i.max_mv, i.rpm_tach, i.rpm_ign, i.inj_pw_us,
                               i.inj_duty, *i.age_ms, i.button)
        n = L.bbhost_trig_step(h, i.t_us, row, out, dig)
        fired_c += [(i.t_us, *out[4 * k:4 * k + 4]) for k in range(n)]
        fired_py += [(f.t_us, f.code, f.src, f.value, f.action) for f in py.step(i)]
        assert bool(dig.value & F.DIG_IG1_ON) == py.ig1_on
        assert bool(dig.value & F.DIG_RUNNING) == py.running
    L.bbhost_trig_free(h)
    assert fired_c == fired_py
    codes = [f[1] for f in fired_c]
    assert F.EV_IG1_ON in codes and F.EV_RUNNING in codes
    expect = {"main_relay": F.R_ECUB_DROP, "igniter": F.R_TACH_STOP, "fuel_pump": F.R_PUMP_DROP,
              "key_off": F.R_IG1_DROP, "battery": F.R_VBAT_DIP}
    if fault:
        assert expect[fault] in codes and F.R_BUTTON in codes
    else:
        assert not [c for c in codes if c <= F.R_MAX_TRIGGER]       # a healthy car triggers nothing


def test_key_on_cranking_and_key_off_do_not_look_like_faults(L):
    """Relay pull-in delay at key-on, the cranking dip and a normal key-off
    must not fire the relay, pump or battery rules."""
    core = CoreModel(Config())
    car = Car("key_off", 8.0)
    for n in range(1, 12001):
        core.feed(car.tick(n))
    assert [e.name for e in core.events if e.is_trigger] == ["ig1_drop"]


# ---- the engine end to end --------------------------------------------------

SIGNATURES = {
    "main_relay": (F.R_ECUB_GLITCH, "main_relay"),      # the relay chatters 400 ms before it lets go
    "igniter": (F.R_TACH_STOP, "igniter"),
    "fuel_pump": (F.R_PUMP_DROP, "fuel_pump"),
    "key_off": (F.R_IG1_DROP, "ig1"),
}


@pytest.mark.parametrize("fault", list(SIGNATURES))
def test_engine_captures_each_signature(box, fault):
    cfg = "pre_s = 8\npost_s = 4\n"
    b = box(cfg)
    run(b, Car(fault, 10.0), 16)
    path = b.event_file(1)
    assert path.exists() and b.status().io_errors == 0 and b.status().overruns == 0
    ev = read_event(path)
    reason, cause = SIGNATURES[fault]
    assert ev.complete and not ev.warnings and ev.blocks_bad == 0
    assert ev.reason == reason and ev.event_no == 1 and ev.boot_no == 7 and ev.device == "HOSTTEST"
    assert ev.pre_ms == 8000 and len(ev.t_us) == 8000 + 4000 + 1
    assert np.all(np.diff(ev.t_us) == 1000)             # one record per millisecond, no holes
    assert ev.t_us[0] == ev.trigger_t_us - 8_000_000
    assert not (ev.samples["dig"] & F.DIG_GAP).any()
    assert ev.config.dump() == Config.parse("".join(f"{n}_scale = 1000\n" for n in F.ANALOG) + cfg).dump()
    assert diagnose(ev).cause == cause

    # the same car through the Python model gives the same events at the same times
    model = CoreModel(Config.parse(cfg), boot_us=BOOT)
    car = Car(fault, 10.0)
    for n in range(1, 16001):
        model.feed(car.tick(n))
    want = [(e.t_us, e.code) for e in model.events if e.is_trigger]
    got = [(e.t_us, e.code) for e in ev.events if e.is_trigger]
    assert [c for _, c in got] == [c for _, c in want]
    assert all(abs(a - b) <= 3000 for (a, _), (b, _) in zip(got, want))
    py = read_event(model.finished[0])
    assert py.reason == ev.reason and abs(py.trigger_t_us - ev.trigger_t_us) <= 3000
    k = np.searchsorted(ev.t_us, py.t_us[0])
    n = min(len(py.t_us), len(ev.t_us) - k) - 5
    assert np.abs(ev.samples["mv"][k:k + n].astype(int) - py.samples["mv"][:n].astype(int)).mean() < 60

    # device CSV: read back from the "card" by the firmware itself, in time order
    lines = b.event_file(1, "CSV").read_text().splitlines()
    assert lines[0].split(",") == list(CSV_COLUMNS) and len(lines) == 1 + len(ev.t_us)
    first, last = lines[1].split(","), lines[-1].split(",")
    assert float(first[0]) == pytest.approx(ev.t_us[0] / 1e6, abs=1e-3)
    assert float(last[0]) == pytest.approx(ev.t_us[-1] / 1e6, abs=1e-3)
    assert float(first[1]) == pytest.approx(ev.samples["mv"][0, 0] / 1000, abs=1e-3)

    summary = (b.root / "SUMMARY" / "S0000007.CSV").read_text().splitlines()
    assert summary[0].startswith("time_s,unix_s,vbat_mv")
    assert len([ln for ln in summary if ln[0].isdigit()]) in (15, 16)        # one line a second
    assert any(ln.startswith("#E,") and f",{reason}," in ln for ln in summary)


def test_one_millisecond_dropout_is_captured_with_its_depth(box):
    """Research target: a 1 ms dropout to 0 V shows in the stored record
    within 0.3 V. The mean barely moves; the per-ms minimum has it."""
    b = box("pre_s = 3\npost_s = 2\n")
    run(b, Car("main_relay", 6.0), 9)       # the relay chatters 1.2 ms at 5.600 s
    ev = read_event(b.event_file(1))
    assert ev.reason == F.R_ECUB_GLITCH
    k = int(np.searchsorted(ev.t_us, ev.trigger_t_us))
    window = slice(k - 3, k + 3)
    assert ev.samples["min_dv"][window, F.A_ECUB].min() / 10 <= 0.3
    before = ev.samples["min_dv"][k - 200:k - 5, F.A_ECUB] / 10
    assert before.min() > 13.5                                  # healthy right up to it
    after = ev.samples["mv"][k + 10:k + 100, F.A_ECUB] / 1000
    assert after.min() > 13.5                                   # and it came back
    assert ev.samples["min_dv"][window, F.A_IG1].min() / 10 > 13.5


def test_edges_7000_rpm_nothing_lost_and_rpm_right(box):
    """7,000 rpm: 233 tach pulses a second. Every edge fed in is in the file,
    in order, with its microsecond time, and RPM reads 7,000."""
    b = box("pre_s = 4\npost_s = 1\n")
    L, h = b.L, b.h
    period = 30_000_000 // 7000         # us between tach pulses (2 per rev)
    sent = []
    t_next = [BOOT + 500, BOOT + 470, BOOT + 900]
    queue = []                          # edges generated but not yet "happened"
    for ms in range(1, 7001):
        now = BOOT + ms * 1000
        for ch, (per, width) in enumerate(((period, 1500), (period, 1500), (period * 4, 9000))):
            while t_next[ch] < now:
                queue += [(t_next[ch], ch, 1), (t_next[ch] + width, ch, 0)]
                t_next[ch] += per
        # the capture ISR hands edges over in time order, as they happen
        queue.sort()
        due = [e for e in queue if e[0] < now]
        queue = queue[len(due):]
        for t, ch, lvl in due:
            L.bbhost_edge(h, ch, 0 if lvl else 1, t)
            sent.append((t, ch, lvl))
        if ms == 5000:
            b.send(F.F_TRIGGER)
        if ms % 10 == 0:
            ch_arr = np.tile(np.arange(5, dtype=np.uint8), 50)
            mv = np.full(250, 13800, dtype=np.uint16)
            L.bbhost_adc(h, now, ch_arr.ctypes.data, mv.ctypes.data, 250)
            L.bbhost_storage(h, 10000)
    ev = read_event(b.event_file(1))
    assert ev.complete and ev.reason == F.R_HOST
    lo, hi = ev.t_us[0], ev.t_us[-1]
    want = sorted(e for e in sent if lo <= e[0] <= hi)
    got = list(zip(ev.edge_t_us.tolist(), ev.edge_ch.tolist(), ev.edge_level.tolist()))
    got = [e for e in got if lo <= e[0] <= hi]
    assert got == want and len(want) > 5 * 233 * 2 * 2
    rpm = ev.samples["rpm_tach"][1000:]
    assert rpm.min() >= 6990 and rpm.max() <= 7010
    assert abs(ev.samples["inj_pw_us"][-1] - 9000) <= 1
    assert abs(ev.samples["inj_duty"][-1] / 2 - 100 * 9000 / (period * 4)) <= 0.5
    t, level = ev.edges("tach")
    assert np.all(np.diff(t[level == 1]) == period)             # microsecond times survive


def test_resampler_copes_with_wrong_adc_rate_and_jitter(box):
    """The ESP32 ADC clock is not exact and the task is not on time. Records
    must still land on the 1 ms grid with no holes."""
    b = box("pre_s = 3\npost_s = 1\n")
    L, h = b.L, b.h
    rng = np.random.default_rng(3)
    real_period = 40.0 * 1.03                   # 3 % slow
    t = float(BOOT)
    for i in range(700):
        t += 250 * real_period
        arrival = int(t + rng.uniform(0, 900))  # scheduling jitter
        ch_arr = np.tile(np.arange(5, dtype=np.uint8), 50)
        mv = (12000 + 1000 * np.sin(np.arange(250) / 40 + i)).astype(np.uint16)
        L.bbhost_adc(h, arrival, ch_arr.ctypes.data, mv.ctypes.data, 250)
        if i == 500:
            b.send(F.F_TRIGGER)
        L.bbhost_storage(h, 10000)
    ev = read_event(b.event_file(1))
    assert ev.complete and np.all(np.diff(ev.t_us) == 1000) and np.all(ev.t_us % 1000 == 0)
    assert (ev.samples["dig"] & F.DIG_GAP).sum() <= 2
    st = b.status()
    assert abs(st.samples - (t - BOOT) / 1000) < 20             # kept pace with real time
    assert st.overruns == 0


def test_ring_wraps_and_second_event_respects_holdoff(box):
    b = box("pre_s = 2\npost_s = 1\nholdoff_s = 4\ncsv_export = 0\n")
    car = Car(None)
    run(b, car, 20)                             # ring is 8 s: wraps twice
    car.press_button(20.0)
    run(b, car, 3, start=20)
    car.fail("battery", 23.0)                   # rule trigger inside the holdoff: no file
    run(b, car, 1.5, start=23)
    car.press_button(24.5)                      # the button is never held off
    run(b, car, 3, start=24.5)
    one, two = read_event(b.event_file(1)), read_event(b.event_file(2))
    assert not b.event_file(3).exists() and not b.event_file(1, "CSV").exists()
    assert one.reason == two.reason == F.R_BUTTON and one.complete and two.complete
    assert len(one.t_us) == 3001 and one.t_us[0] > BOOT + 17_000_000
    assert F.R_VBAT_DIP in [e.code for e in two.events]         # recorded, with its cause
    assert b.status().captures_this_boot == 2 and b.status().event_count == 2


def test_event_numbers_continue_after_reboot(box):
    b = box("pre_s = 1\npost_s = 1\n", last_event_no=41)
    car = Car(None)
    run(b, car, 4)
    b.send(F.F_TRIGGER)
    run(b, car, 2, start=4)
    assert read_event(b.event_file(42)).event_no == 42


# ---- power loss -------------------------------------------------------------

@pytest.mark.parametrize("budget", [100, 3_000, 40_000, 150_000, 200_000])
def test_power_loss_mid_write_keeps_the_newest_data(box, budget):
    """Cut the write off after ``budget`` bytes (a torn block included). The
    file must still parse, flag itself incomplete, and hold the stretch right
    before the trigger: the writer puts the newest blocks down first."""
    b = box("pre_s = 8\npost_s = 4\n")
    car = Car("igniter", 10.0)
    run(b, car, 10.2)
    assert not b.event_file(1).exists()
    b.L.bbhost_set_write_budget(b.h, budget)
    run(b, car, 1.0, start=10.2)                # trigger at about 10.24 s, then the supply dies
    assert b.status().io_errors > 0
    ev = read_event(b.event_file(1))
    assert not ev.complete and any("cut short" in w for w in ev.warnings)
    assert ev.reason == F.R_TACH_STOP and ev.trigger_value >= 250      # header alone says why
    if budget >= 3_000:
        assert [e.code for e in ev.events if e.is_trigger] == [F.R_TACH_STOP]
        assert "rule.1 = tach_age > 250" in ev.config_text
    if budget >= 40_000:
        whole = (budget - 2200) // (256 * 28 + 28 + 600)        # sample blocks that fit, roughly
        assert len(ev.t_us) >= 256 * max(1, whole - 1)
        assert ev.t_us[-1] == ev.trigger_t_us                   # newest first
        assert np.all(np.diff(ev.t_us) == 1000)
        assert diagnose(ev).cause == "igniter" or budget < 150_000


def test_capture_is_on_the_card_at_once_not_after_post_trigger(box):
    """Research target: capture written within 2 s of power loss. All of the
    pre-trigger data is written and synced in the first storage pass after
    the trigger; nothing waits for the post-trigger window."""
    b = box("pre_s = 30\npost_s = 10\n")
    car = Car(None)
    run(b, car, 31, storage=False)
    b.L.bbhost_storage(b.h, 100000)
    b.send(F.F_TRIGGER)
    syncs = b.L.bbhost_syncs(b.h)
    steps = b.L.bbhost_storage(b.h, 100000)     # one pass, no new samples arrive
    size = b.event_file(1).stat().st_size
    assert size > 30000 * 28 and steps < 400
    assert b.L.bbhost_syncs(b.h) - syncs >= 3   # early sync, periodic, end of pre-trigger
    ev = read_event(b.event_file(1))            # as if power died right here
    assert not ev.complete and len(ev.t_us) == 30001 and ev.t_us[-1] == ev.trigger_t_us
    assert size < 1_200_000                     # what the card has to take in 2 s


def test_brownout_rule_fires_when_the_supply_collapses(box):
    b = box("pre_s = 2\npost_s = 1\n")
    car = Car(None)
    run(b, car, 6)
    L, h = b.L, b.h
    ch_arr = np.tile(np.arange(5, dtype=np.uint8), 50)
    for i in range(150):                        # battery feed gone; the box runs on its hold-up cap
        mv = np.full(250, max(0, 12000 - 4000 * i), dtype=np.uint16)
        L.bbhost_adc(h, BOOT + 6_000_000 + (i + 1) * 10_000, ch_arr.ctypes.data, mv.ctypes.data, 250)
        L.bbhost_storage(h, 10000)
    ev = read_event(b.event_file(1))
    codes = [e.code for e in ev.events if e.is_trigger]
    assert F.R_BROWNOUT in codes and ev.reason in (F.R_BROWNOUT, F.R_VBAT_DIP, F.R_ECUB_GLITCH, F.R_IG1_DROP)
    assert ev.complete


def test_no_sd_card_does_not_stall_acquisition(L, tmp_path):
    b = bbhost.Box(L, tmp_path / "sd")
    (tmp_path / "sd" / "EVENTS").rmdir()        # opening the event file will fail
    car = Car(None)
    run(b, car, 3)
    b.send(F.F_TRIGGER)
    run(b, car, 2, start=3)
    st = b.status()
    assert st.io_errors >= 1 and st.capturing == 0 and st.samples >= 4990
    b.close()


# ---- the USB protocol, spoken by the real C -------------------------------

def test_protocol_hello_live_events(box):
    b = box("pre_s = 2\npost_s = 1\nlive_hz = 50\n")
    car = Car("igniter", 4.0)
    b.send(F.F_GET_HELLO)
    (t, p), = b.frames()
    proto, fver, fw, dev, hz, live_hz, boot, events, na, ne = F.HELLO.unpack(p)
    assert (t, proto, fver, hz, live_hz, boot, na, ne) == (F.F_HELLO, 1, 1, 1000, 50, 7, 5, 3)
    assert fw.rstrip(b"\0") == b"0.1.0" and dev.rstrip(b"\0") == b"HOSTTEST"

    frames = []
    for n in range(1, 6001):
        b.feed(car.tick(n), BOOT)
        if n % 10 == 0:
            frames += b.frames()
    live = [p for t, p in frames if t == F.F_LIVE]
    assert 290 <= len(live) <= 301                      # 50 Hz
    from hondalog.blackbox.reader import decode_live
    t_us, values, state, n_events, last_reason = decode_live(live[150])
    assert 700 < values["bb_rpm_tach"] < 3200 and 13.5 < values["bb_vbat"] < 14.8
    assert values["bb_vbat_min"] <= values["bb_vbat"] + 0.05 and values["bb_running"] == 1.0
    assert state & F.ST_SD_OK and state & F.ST_RUNNING and state & F.ST_IG1_ON
    assert abs(t_us - (BOOT + 3_000_000)) < 100_000
    t_us, values, state, n_events, last_reason = decode_live(live[-1])
    assert values["bb_rpm_tach"] == 0 and n_events == 1 and last_reason == F.R_IGN_STOP

    events = [F.EVENT_MSG.unpack(p) for t, p in frames if t == F.F_EVENT]
    names = [F.reason_name(e[1]) for e in events]
    assert names == ["ig1_on", "running", "tach_stop", "ign_stop"]
    started = [(e[5], e[6]) for e in events]
    assert started == [(0, 0), (0, 0), (1, 1), (0, 0)]  # only the tach stop opened a file
    order = [t for t, _ in frames if t in (F.F_EVENT, F.F_LIVE)]
    assert order[0] == F.F_LIVE


def test_protocol_time_marks_config_and_download(box):
    b = box("pre_s = 2\npost_s = 1\n")
    car = Car(None)
    run(b, car, 3)
    b.frames()

    b.send(F.F_TIME_REQ, struct.pack("<q", 123456789))
    (t, p), = [f for f in b.frames() if f[0] == F.F_TIME_RSP]
    host_tx, dev_rx, dev_tx = F.TIME_RSP.unpack(p)
    assert host_tx == 123456789 and dev_rx == dev_tx == BOOT + 3_000_000

    unix0 = 1_800_000_000_000_000
    b.send(F.F_TIME_SET, struct.pack("<q", unix0))
    b.send(F.F_MARK, struct.pack("<i", 5))
    b.send(F.F_SET_CONFIG, b"rule.6 = vbat < 10500 500 50 running capture")
    b.send(F.F_SET_CONFIG, b"rule.6 = vbat < banana")
    b.send(F.F_SET_CONFIG, b"live_hz = 20")
    b.send(F.F_GET_CONFIG)
    frames = b.frames()
    text = b"".join(p for t, p in frames if t == F.F_TEXT).decode()
    assert text.startswith("ok rule.6 = vbat < 10500 500 50 running capture\nerror rule.6 = vbat < banana\nok live_hz")
    cfg_text = text.split("ok live_hz = 20\n", 1)[1]
    assert cfg_text.endswith("# end\n")
    cfg = Config.parse(cfg_text)
    assert cfg.rules[6].level == 10500 and cfg.rules[6].hold_ms == 50 and cfg["live_hz"] == 20
    assert all(len(p) <= 240 for t, p in frames if t == F.F_TEXT)
    assert [F.EVENT_MSG.unpack(p)[1] for t, p in frames if t == F.F_EVENT] == [F.EV_TIME_SYNC, F.EV_HOST_MARK]

    b.send(F.F_SAVE_CONFIG)
    assert b"ok saved" in b"".join(p for t, p in b.frames() if t == F.F_TEXT)
    assert Config.parse((b.root / "BLACKBOX.CFG").read_text()).rules[6].level == 10500

    b.send(F.F_TRIGGER)
    run(b, car, 2, start=3)
    b.frames()
    ev = read_event(b.event_file(1))
    assert ev.time_valid and ev.unix_us_at_t0 == unix0
    marks = [(e.value, e.t_us) for e in ev.events if e.code == F.EV_HOST_MARK]
    assert marks == [(5, BOOT + 3_000_000)]
    assert ev.trigger_wall.timestamp() == pytest.approx((unix0 + ev.trigger_t_us) / 1e6)

    b.send(F.F_LIST_FILES)
    infos = [F.FILE_INFO.unpack(p) for t, p in b.frames() if t == F.F_FILE_INFO]
    size = b.event_file(1).stat().st_size
    assert infos == [(1, size, F.R_HOST, 1), (0xFFFFFFFF, 0, 0, 0)]

    data = bytearray()
    while True:
        b.send(F.F_READ_FILE, F.READ_FILE.pack(1, len(data), F.FILE_CHUNK))
        (t, p), = [f for f in b.frames() if f[0] == F.F_FILE_DATA]
        no, offset = struct.unpack_from("<II", p)
        assert (no, offset) == (1, len(data))
        data += p[8:]
        if len(p) - 8 < F.FILE_CHUNK:
            break
    assert bytes(data) == b.event_file(1).read_bytes()
    b.send(F.F_READ_FILE, F.READ_FILE.pack(99, 0, F.FILE_CHUNK))
    (t, p), = [f for f in b.frames() if f[0] == F.F_FILE_DATA]
    assert struct.unpack_from("<II", p) == (99, 0xFFFFFFFF)


def test_protocol_ignores_garbage_and_bad_crc(box):
    b = box()
    good = F.frame_encode(F.F_GET_HELLO)
    bad = bytearray(good)
    bad[2] ^= 0x10
    junk = bytes(range(1, 256)) * 5 + b"\x00" + bytes(bad) + b"\x00\x00" + good
    b.L.bbhost_rx(b.h, junk, len(junk))
    assert [t for t, _ in b.frames()] == [F.F_HELLO]


def test_gps_sets_the_clock_and_lands_in_the_file(box):
    b = box("pre_s = 2\npost_s = 1\n")
    car = Car(None)
    run(b, car, 3)
    fix = np.zeros(1, dtype=F.GPS_DTYPE)
    fix[0] = (BOOT + 2_900_000, 322_000_000, -901_000_000, 1_800_000_000, 1234, 9000, 1, 7, 0)
    b.L.bbhost_gps(b.h, fix.ctypes.data)
    b.send(F.F_TIME_SET, struct.pack("<q", 5))          # the laptop must not override GPS time
    b.send(F.F_TRIGGER)
    run(b, car, 2, start=3)
    ev = read_event(b.event_file(1))
    assert ev.time_valid and ev.time_from_gps
    assert ev.unix_us_at_t0 == 1_800_000_000 * 1_000_000 - (BOOT + 2_900_000)
    assert len(ev.gps) == 1 and ev.gps[0]["lat_e7"] == 322_000_000 and ev.gps[0]["sats"] == 7
