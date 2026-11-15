"""ChannelBus: publishing from threads, staleness, stats, sparklines."""

import threading

import numpy as np

from hondalog.ui.bus import SPARK_N, ChannelBus, ChannelSpec


def make():
    bus = ChannelBus(hist_n=64)
    bus.register(ChannelSpec("rpm", "RPM", "rpm", 0, 0, 8000))
    bus.register(ChannelSpec("stft", "Short trim", "%", 1, -25, 25, signed=True))
    bus.register(ChannelSpec("vtec", "VTEC", kind="flag"))
    return bus


def test_publish_updates_value_minmax_and_generation():
    bus = make()
    rpm = bus.channel("rpm")
    assert rpm.stale(bus.now()) and rpm.value != rpm.value
    for v in (800, 3000, 750):
        bus.publish({"rpm": v, "nope": 1, "stft": None}, t=1.0)
    assert (rpm.value, rpm.vmin, rpm.vmax, rpm.gen) == (750, 750, 3000, 3)
    assert bus.channel("stft").gen == 0
    assert not rpm.stale(1.5) and rpm.stale(2.5)


def test_format():
    s = ChannelSpec("stft", decimals=1, signed=True, lo=-25, hi=25)
    assert s.format(4.04) == "+4.0" and s.format(-3.96) == "−4.0"
    assert s.format(-0.01) == "0.0" and s.format(float("nan")) == "---"
    flag = ChannelSpec("f", kind="flag")
    assert flag.format(1) == "ON" and flag.format(0) == "OFF" and (flag.lo, flag.hi) == (0, 1)


def test_flag_change_count_and_reset():
    bus = make()
    for v in (0, 1, 1, 0, 1):
        bus.publish({"vtec": v}, t=1.0)
    assert bus.channel("vtec").changes == 3
    bus.reset_stats()
    assert bus.channel("vtec").changes == 0


def test_history_ring_wraps_in_time_order():
    bus = make()
    for i in range(100):
        bus.publish({"rpm": i}, t=float(i))
    t, v = bus.history("rpm")
    assert len(t) == 64 and t[0] == 36 and t[-1] == 99 and (np.diff(t) > 0).all()
    t, v = bus.history("rpm", since=90)
    assert list(v) == list(range(90, 100))


def test_publish_block():
    bus = make()
    t = np.arange(200) / 1000.0
    bus.publish_block(t, {"rpm": np.linspace(1000, 2000, 200)}, source="BB")
    rpm = bus.channel("rpm")
    assert rpm.value == 2000 and rpm.vmin < 1700 and rpm.hn == 64
    assert bus.rate_hz("BB") == 0.0                     # first block only sets the baseline
    bus.publish_block(t + 0.2, {"rpm": np.full(200, 1500.0)}, source="BB")
    assert abs(bus.rate_hz("BB") - 1000) < 1


def test_sparkline_ticks_at_2hz_and_marks_stale_as_gap():
    bus = make()
    bus.publish({"rpm": 1000}, t=0.4)
    assert bus.tick(0.5) and not bus.tick(0.6)
    rpm = bus.channel("rpm")
    assert rpm.spark[-1] == 1000 and rpm.spark_gen == 1 and len(rpm.spark) == SPARK_N
    bus.tick(3.0)                                       # 2.6 s without data: stale
    assert rpm.spark[-1] != rpm.spark[-1]


def test_register_again_keeps_data_and_bumps_version():
    bus = make()
    bus.publish({"rpm": 900}, t=1.0)
    v = bus.version
    state = bus.register(ChannelSpec("rpm", "Engine speed", "rpm", 0, 0, 9000, verified=True))
    assert state.value == 900 and state.spec.verified and bus.version == v + 1


def test_publish_from_threads_loses_nothing():
    bus = ChannelBus(hist_n=100000)
    bus.register(ChannelSpec("a"))

    def work():
        for i in range(5000):
            bus.publish({"a": i})
    threads = [threading.Thread(target=work) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert bus.channel("a").hn == 20000 and bus.pub_gen == 20000
