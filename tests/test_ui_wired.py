"""The UI adapters on the real backends (hondalog/ui/wired.py): W1 link, W2
black box, W3 math/alarms/mapping, W4 recorder/reader/histogram, W5 tables."""

import shutil
import time

import numpy as np
import pytest

from hondalog import addressmap
from hondalog.data import open_log
from hondalog.live import BLACKBOX_STREAM, MATH_STREAM
from hondalog.tuning.demo import DEMO_MAP_KPA, DEMO_RPM, build_demo_rom
from hondalog.ui import adapters as ad
from hondalog.ui import backends as be
from hondalog.ui import wired
from hondalog.ui.bus import ChannelBus
from hondalog.ui.session import build_session


def pump_until(session, cond, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        session.pump(session.bus.now())
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def user_map(tmp_path, monkeypatch):
    """A private copy of the shipped map as "the user's map"."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return addressmap.load_user_map()


@pytest.fixture()
def live(tmp_path, user_map):
    """A wired session connected to the built-in simulators, recording."""
    session = build_session(log_dir=tmp_path / "logs")
    session.start()
    session.connect("sim:")
    assert pump_until(session, lambda: session.ecu_status.state == ad.OK and session.recording.active)
    yield session
    session.close()


def test_wired_implementations_satisfy_the_protocols():
    session = build_session()
    ecu = session.ecu
    assert isinstance(ecu, wired.LinkSource)
    assert isinstance(ecu, ad.ChannelSource) and isinstance(ecu, ad.Connectable)
    assert isinstance(session.blackbox, wired.BlackboxSource) and isinstance(session.blackbox, ad.ChannelSource)
    assert isinstance(session.extra_sources[0], wired.MathSource)
    assert isinstance(session.extra_sources[0], ad.ChannelSource)
    assert isinstance(session.alarms, wired.PipelineAlarms) and isinstance(session.alarms, ad.AlarmSource)
    assert isinstance(session.recording, wired.LiveRecording) and isinstance(session.recording, ad.Recording)
    assert isinstance(session.logs, wired.DriveReader) and isinstance(session.logs, ad.LogReader)
    assert isinstance(session.obsmap, wired.LogHistogramMap) and isinstance(session.obsmap, ad.ObservedMap)
    assert isinstance(session.mapper, wired.MappingSession) and isinstance(session.mapper, ad.MapperSession)
    assert isinstance(session.tables, wired.RomTableModel) and isinstance(session.tables, ad.TableModel)
    demo = build_session(demo=True)                 # --demo keeps the simulated car
    assert isinstance(demo.ecu, be.DemoSource) and isinstance(demo.tables, be.DemoTableModel)
    assert demo.tables.writable and [type(s) for s in demo.extra_sources] == [be.DemoGps]   # its GPS


def test_specs_take_limits_from_the_maps_alarm_rules_and_fan_tiles_follow_the_map(tmp_path):
    text = addressmap.DEFAULT_MAP.read_text(encoding="utf-8")
    with_fan = text.replace("\n[constants]\n", '''\n[[channel]]
name = "fan"
label = "Rad fan"
addr = 0x0C
formula = "bit(x, 2)"
kind = "flag"

[constants]
''', 1)
    assert with_fan != text
    path = tmp_path / "map.toml"
    path.write_text(with_fan, encoding="utf-8")
    ecu = wired.LinkSource(load_map=lambda: addressmap.load(path))
    by = {s.id: s for s in ecu.specs()}
    assert by["fan"].mapped and by["fan"].label == "Rad fan" and by["fan"].is_flag     # real tile
    assert not by["fuel_pump"].mapped                                                 # still a placeholder
    rules = {r.name: r for r in ecu.amap.alarms}
    for rule in rules.values():
        if rule.channel in by and rule.high is not None:
            spec = by[rule.channel]
            assert (spec.alarm_hi if rule.severity == "critical" else spec.warn_hi) == rule.high
    math = {s.id: s for s in ecu.math_specs()}
    assert {"inj_duty", "rpm_rate"} <= set(math) and all(s.source == "MATH" for s in math.values())
    assert not math["inj_duty"].verified            # built from unverified channels

    stock = wired.LinkSource(load_map=addressmap.load_default)
    assert not {s.id: s for s in stock.specs()}["fan"].mapped


def test_ecu_math_and_black_box_reach_the_bus_at_their_own_rates(live):
    s, bus = live, live.bus
    assert pump_until(s, lambda: bus.channel("ect").gen >= 2 and bus.channel("bbatt").gen > 20
                      and s.bb_status.state == ad.OK)
    now = bus.now()
    # per-read publishing: a fast channel has been read far more often than a slow one
    assert bus.channel("rpm").gen > 4 * bus.channel("ect").gen
    rates = s.ecu.channel_rates()
    assert rates["rpm"].rate_class == "fast" and rates["ect"].rate_class == "slow"
    assert rates["rpm"].achieved_hz > 10 > rates["ect"].achieved_hz
    text = s.ecu.link_text()
    assert "fast" in text and "wire" in text and "t/o" in text and "EXPERT" not in text
    # W3 math channels are a source of their own
    duty = bus.channel("inj_duty")
    assert duty.spec.source == "MATH" and duty.gen > 3 and not duty.stale(now)
    assert [c.spec.id for c in s.ordered("MATH")][:2] == ["inj_duty", "fuel_flow"]
    # W2's reader under the UI's ids, in the UI's units
    for cid in ("bbatt", "ecub", "fpv", "ig1"):
        assert 10 < bus.channel(cid).value < 16 and not bus.channel(cid).stale(now), cid
    reader = s.blackbox.reader
    assert reader.t0 == s.ecu.poller.t0 and s.bb_status.simulated
    tach_rpm = reader.samples[-1].values["bb_rpm_tach"]
    assert tach_rpm > 300 and bus.channel("tach").value == pytest.approx(tach_rpm / 30, rel=0.2)
    assert s.bus.channel("fan").stale(now)


def test_recording_is_a_drive_in_the_database_with_public_trigger_status(live):
    s = live
    assert pump_until(s, lambda: s.stall.state == "armed")       # Recorder.status(), no private fields
    assert s.stall.pre_s == 30 and s.stall.post_s == 10 and s.stall.buffer_s > 0
    assert s.mark() == "Marker 1 dropped" and s.recording.marks == 1
    ect = s.bus.channel("ect")
    seen = ect.gen
    assert pump_until(s, lambda: ect.gen >= seen + 2)               # two more slow reads on file
    path = s.recording.path
    assert path.name.startswith("drive_") and path.db == s.logs.database and path.db.name == "hondalog.db"
    s.recording.stop()
    assert not s.recording.active and s.stall.state in ("armed", "standby", "off")
    with open_log(path) as log:
        assert {MATH_STREAM, BLACKBOX_STREAM, "ecu/00+3"} <= set(log.streams)
        assert log.streams["ecu/00+3"].info.raw_len == 3
        assert [text for _, text in log.markers] == ["MARK 1"]
        assert any("marker" in e.text for e in log.events_of("blackbox"))    # same mark in the box
        assert log.meta["link"]["ok"] > 20 and log.meta["address_map_tag"] == s.ecu.amap.version_tag
    view = s.logs.read(path)
    assert view.trigger_t is None and view.markers[0][1] == "MARK 1"
    assert len(view.t["rpm"]) > 3 * len(view.t["ect"])              # each channel at its own rate
    assert {"bbatt", "tach", "inj_duty"} <= set(view.data) and "bb_vbat" not in view.data
    assert view.specs["bbatt"].source == "BB" and view.specs["inj_duty"].source == "MATH"
    assert view.data["tach"].max() == pytest.approx(view.replay.log.series("bb_rpm_tach").v.max() / 30)
    assert view.align_residual_ms is not None and view.align_residual_ms < 5
    assert view.replay.readouts(["rpm"])[0].name == "rpm"           # W4's ReplaySession rides along
    assert s.logs.recent()[0] == path and view.title == path.name
    assert s.logs.read(str(path)).title == path.name and s.logs.read(path.name).title == path.name
    rows = s.logs.drives()                                          # what the drive picker lists
    assert [(r.ref, r.kind, r.status) for r in rows] == [(path, "drive", "complete")]
    assert rows[0].duration_s > 0.5 and "mark 1" in rows[0].text and rows[0].started[:2] == "20"
    with pytest.raises(ValueError, match="no drive or capture"):
        s.logs.read("drive_1999_01_01_0000")


def test_capture_from_the_trigger_engine_reaches_the_banner(live):
    s = live
    pump_until(s, lambda: s.stall.buffer_s > 0.5)
    s.ecu.live.recorder.external_trigger("black box: ecub_low")
    assert pump_until(s, lambda: s.stall.state == "saving", 3)
    assert 0 < s.stall.post_left_s <= 10
    s.recording.stop()                              # closes the window early and saves it
    events = []
    s._status_t = -1
    events += s.pump(s.bus.now())
    stall = [e for e in events if e.kind == "stall"]
    assert stall and stall[0].payload.reason == "external"
    assert "Black box: ecub_low." in stall[0].text and s.banner.path.name.startswith("stall_")
    view = s.logs.read(s.banner.path)
    assert view.trigger_t == 0.0 and min(view.t["rpm"]) < 0 and "bbatt" in view.data
    assert s.logs.recent(stalls_only=True) == [s.banner.path]
    assert view.title == s.banner.path.name and s.banner.path.drive == s.recording.path.name
    assert s.logs.recent() == [s.banner.path, s.recording.path]     # the capture, then the drive it is in
    rows = s.logs.drives()
    assert [(r.name, r.kind) for r in rows] == [(s.recording.path.name, "drive"), (s.banner.path.name, "stall")]
    assert rows[0].stalls == 1 and "black box" in rows[1].notes and s.stall_paths()[0][0] == s.banner.path.name
    assert s.logs.analyze(view).loss[-1].signal == "STALL"


def test_alarms_come_from_the_w3_engine(user_map):
    session = build_session()
    session.start()
    ecu, alarms, bus = session.ecu, session.alarms, session.bus
    values = {c.name: 0.0 for c in ecu.amap.channels}
    values.update(rpm=900.0, ect=90.0, batt=14.0, tps=5.0, map=35.0, o2=0.45, iat=25.0)

    def feed(t, **over):
        frame = ecu.live.pipeline.process({**values, **over}, t)
        bus.publish(frame.values, t=t)
        if frame.events:
            ecu.frames.append(frame)
        return alarms.evaluate(bus, t)

    rule = next(r for r in ecu.amap.alarms if r.channel == "ect" and r.severity == "critical")
    assert not any(feed(i * 0.1) for i in range(20)) and alarms.state("ect") == ""
    started = []
    for i in range(20, 20 + int(rule.hold_s * 10) + 30):
        started += feed(i * 0.1, ect=rule.high + 4)
    assert [e.channel_id for e in started] == ["ect"] and started[0].severity == "alarm"
    assert started[0].label == "COOLANT" and str(int(rule.high + 4)) in started[0].value_text
    assert alarms.state("ect") == "alarm" and alarms.unacked("ect") and alarms.active()[0] is started[0]
    rev = alarms.revision
    alarms.acknowledge()
    assert not alarms.unacked("ect") and alarms.revision > rev
    t = 20 + int(rule.hold_s * 10) + 30
    for i in range(t, t + 200):
        feed(i * 0.1)
    assert alarms.state("ect") == "" and alarms.history()[0].channel_id == "ect"
    assert alarms.history()[0].t_end is not None
    rows = {row.name: row for row in alarms.limits()}
    assert any(f">{rule.high:g}" in row.alarm for row in rows.values())
    assert alarms.marks_itself                       # the live path drops the marker, not the shell

    # the black box rails are not in the map: the plain limit check still covers them
    bus.publish({"ecub": 9.0}, t=bus.now())
    assert [e.channel_id for e in alarms.evaluate(bus, bus.now())] == ["ecub"]
    assert alarms.state("ecub") == "alarm" and alarms.state("ect") == ""
    session.close()


def test_console_goes_through_the_read_only_guard_and_unlock_is_logged(live):
    s, m = live, live.mapper
    assert m.available() and not m.expert

    def console_after(send):
        rev = m.revision
        send()
        assert pump_until(s, lambda: m.revision != rev, 4)
        return m.console()

    assert "checksum OK" in console_after(lambda: m.send(bytes.fromhex("20 05 76 0A"), True))
    blocked = console_after(lambda: m.send(bytes.fromhex("21 04 00"), True))
    assert blocked.startswith("!! blocked") and "Expert" in blocked
    assert s.ecu.poller.link.unlocked_frames == []
    assert "Expert unlock ON" in m.set_expert(True) and m.expert and "EXPERT UNLOCK" in s.ecu.link_text()
    assert "blocked" not in console_after(lambda: m.send(bytes.fromhex("21 04 00"), True))
    m.set_expert(False)
    path = s.recording.path
    s.recording.stop()
    with open_log(path) as log:
        assert log.meta["unlocked_frames"] == ["21 04 00 db"]
        assert [e.text for e in log.events_of("link")] == ["expert unlock ON", "expert unlock off"]


def test_verified_mark_is_written_to_the_map_file(user_map):
    session = build_session()
    session.start()
    ecu = session.ecu
    assert not session.bus.channel("tps").spec.verified
    message = ecu.set_verified("tps", True)
    assert "saved" in message and session.bus.channel("tps").spec.verified
    on_disk = addressmap.load(addressmap.user_map_path())
    tps = on_disk.channel("tps")
    assert tps.verified and tps.evidence.method == "manual" and tps.evidence.map_hash == on_disk.version_hash
    assert on_disk.version_hash == user_map.version_hash            # marking does not change decoding
    assert session.mapper.mapped(0x14) == ("tps", True)
    ecu.set_verified("tps", False)
    assert not addressmap.load(addressmap.user_map_path()).channel("tps").verified
    assert "worked out" in ecu.set_verified("inj_duty", True)
    session.close()


def test_wizard_is_a_w3_verification_session(live):
    s, m = live, live.mapper
    targets = m.wizard().targets
    index = next(i for i, text in enumerate(targets) if "(batt)" in text)
    m.wizard_pick_target(index)
    assert m.wizard().primary == "Next"
    m.wizard_next()
    session = m._session
    assert session is not None and session.state == "running" and "batt" in session.script.channels
    view = m.wizard()
    assert view.step == 1 and view.title.startswith(session.phase.title) and view.body == session.phase.instruction
    assert view.primary == "Recording…" and 0 <= view.busy < 1
    assert pump_until(s, lambda: len(session.recording) >= 2, 12)   # the grid's sweeps feed the session
    assert m.sweeps >= 2

    real = session.clock                            # the phases take minutes: skip ahead
    skipped = [0.0]
    session.clock = lambda: real() + skipped[0]
    while m.wizard().step in (1, 2):
        skipped[0] += 120.0
        m.wizard_next()
        pump_until(s, lambda: False, 0.05)
    view = m.wizard()
    assert view.step == 3 and view.title.startswith("batt: ")
    status = m._verdict.status
    assert status in ("pass", "plausible", "fail", "skip")
    before = addressmap.load(addressmap.user_map_path()).channel("batt")
    m.wizard_next(view.candidates[0].addr if view.candidates else None)
    after = addressmap.load(addressmap.user_map_path()).channel("batt")
    final = m.wizard()
    if status in ("pass", "plausible"):
        assert after.verified and after.evidence.method.startswith("guided:")
        assert "marked verified" in final.body and s.bus.channel("batt").spec.verified
    else:                                           # never marked verified on a failed check
        assert not after.verified and (after.addr != before.addr or after.status == before.status
                                       or after.status == "suspect")
    if final.step == 4:
        m.wizard_next()                             # "Done"
    else:
        assert status in ("fail", "skip") and final.step == 1      # nothing fit: it started over
        m.wizard_back()
    assert m.wizard().step == 0 and m._session is None


def test_bbx_capture_opens_merged_with_the_ecu_log(live, tmp_path):
    s = live
    reader = s.blackbox.reader
    assert pump_until(s, lambda: reader.clock.count >= 5 and s.bus.channel("rpm").gen > 20)
    reader.set_config("post_s", 1)                  # the default keeps 10 s after the trigger
    reader.trigger()
    pump_until(s, lambda: False, 2.5)
    files = reader.list_events()
    assert files, "the simulated box should have saved a capture"
    bbx = reader.fetch_event(files[-1].event_no, tmp_path / "logs")
    view = s.logs.read(bbx)
    assert view.trigger_t == 0.0 and {"bbatt", "ecub", "tach"} <= set(view.data)
    assert "rpm" in view.data and "merged" in view.note            # the ECU log, aligned by the clock
    assert view.align_residual_ms is not None and view.align_residual_ms <= 10
    assert len(view.t["bbatt"]) > 500                                # the box's own 1 kHz record
    forensics = s.logs.analyze(view)
    assert forensics.summary and forensics.reading
    assert bbx in s.logs.recent()


def test_histogram_map_uses_w4_bins_and_the_rom_axes(tmp_path):
    bus = ChannelBus()
    for spec in wired.LinkSource(load_map=addressmap.load_default).specs():
        bus.register(spec)
    obs = wired.LogHistogramMap()
    obs.REBUILD_S = 0.0
    for inj in (2.0, 4.0):
        bus.publish({"rpm": 3000, "map": 55, "inj": inj, "ign": 20, "stft": 3, "ltft": 1})
        obs.update(bus)
        obs.update(bus)                                     # same sample twice: counted once
    i, j = obs.cell(3000, 55)
    assert obs.hits()[j, i] == 2 and obs.hits().sum() == 2 and obs.hits().shape == (10, 20)
    assert obs.mean("inj")[j, i] == pytest.approx(3.0) and obs.mean("trim")[j, i] == pytest.approx(4.0)
    assert np.isnan(obs.mean("inj")[0, 0])

    rom = tmp_path / "demo.bin"
    rom.write_bytes(build_demo_rom())
    tables = wired.RomTableModel(store_dir=tmp_path / "roms")
    assert tables.open(rom) == ""
    obs = wired.LogHistogramMap(tables)
    obs.REBUILD_S = 0.0
    assert obs.rpm_axis.tolist() == DEMO_RPM and obs.map_axis.tolist() == DEMO_MAP_KPA
    x_bins, y_bins = obs.bins()
    assert list(x_bins.centres) == DEMO_RPM and list(y_bins.centres) == DEMO_MAP_KPA
    bus.publish({"rpm": 2500, "map": 48, "inj": 3.0, "ign": 20, "stft": 0, "ltft": 0})
    obs.update(bus)
    assert obs.hits().shape == (len(DEMO_MAP_KPA), len(DEMO_RPM))
    assert obs.hits()[DEMO_MAP_KPA.index(50), DEMO_RPM.index(2400)] == 1
    obs.clear()
    assert obs.hits().sum() == 0


def test_table_model_is_the_rom_session_with_the_demo_as_fallback(tmp_path):
    tm = wired.RomTableModel()
    assert not tm.loaded and not tm.writable and "P28" in tm.read_only_reason
    assert tm.values("fl").shape == (20, 10) and [t.id for t in tm.tables()][0] == "fl"
    assert "nope.bin" in tm.open(tmp_path / "nope.bin") and not tm.loaded     # stays on the samples
    assert "nope.bin" in tm.read_only_reason and tm.values("fl").shape == (20, 10)

    rom = tmp_path / "demo.bin"
    rom.write_bytes(build_demo_rom())
    tm = wired.RomTableModel(store_dir=tmp_path / "roms")
    rev = tm.revision
    assert tm.open(rom) == "" and tm.loaded and tm.writable and tm.revision > rev
    ids = [t.id for t in tm.tables()]
    assert ids[:2] == ["fuel", "ignition"] and "ect_fuel" not in ids         # curves are not maps
    assert tm.rpm_axis.tolist() == DEMO_RPM and tm.map_axis.tolist() == DEMO_MAP_KPA
    fuel = tm.values("fuel")
    assert fuel.shape == (len(DEMO_RPM), len(DEMO_MAP_KPA))                  # (n_rpm, n_map)
    editor = tm.session.table("fuel")
    assert fuel[3, 5] == pytest.approx(editor.values("real")[5, 3])
    info = tm.tables()[0]
    before = float(fuel[3, 5])
    rev = tm.revision
    tm.adjust("fuel", [(3, 5)], 2)
    assert tm.revision > rev and tm.values("fuel")[3, 5] == pytest.approx(before + 2 * info.step, abs=info.step)
    assert tm.base("fuel")[3, 5] == pytest.approx(before) and tm.values("fuel")[2, 5] == pytest.approx(fuel[2, 5])
    tm.smooth("fuel", [(4, 4)])
    tm.interpolate("fuel", [(r, c) for r in (1, 2, 3) for c in (1, 2, 3)])
    out = tmp_path / "tuned.bin"
    tm.save(out)
    again = wired.RomTableModel(store_dir=tmp_path / "roms2")
    assert again.open(out) == ""
    np.testing.assert_allclose(again.values("fuel"), tm.values("fuel"))
    assert "checksum" in again.read_only_reason
    undone = 0
    while tm.undo():
        undone += 1
    assert 2 <= undone <= 3                         # an edit that changes no byte is not a step
    np.testing.assert_allclose(tm.values("fuel"), tm.base("fuel"))
    assert tm.can_write_chip() == (shutil.which("minipro") is not None)

    session = build_session(rom=rom)                                         # the app's --rom
    assert session.tables.loaded and session.obsmap.rpm_axis.tolist() == DEMO_RPM


OLD_CSV = "# hondalog 0.1.0\n# started: 2026-11-10T07:58:01\ntime_s,wall_time,link,rpm,marker\n" + "".join(
    f"{i / 10:.1f},x,1,{750 + i},{'cold start' if i == 5 else ''}\n" for i in range(1, 40))


def test_drive_picker_lists_searches_and_imports(app, tmp_path):
    """Replay's Open: drives and captures from the database, a search box,
    and "Import file" for logs from before (or from other tools)."""
    from datetime import datetime
    from hondalog.data import Column, new_drive
    from hondalog.ui.drivepicker import DrivePicker
    database = tmp_path / "hondalog.db"
    refs = []
    for day, notes in ((10, "idle hunting"), (12, "")):
        started = datetime(2026, 11, day, 7, 12).timestamp()
        with new_drive(database, {"started_unix": started, "epoch_unix": started, "notes": notes}) as w:
            w.add_stream("ecu/00+2", [Column("rpm", units="rpm")], source="ecu", raw_len=2)
            for i in range(300):
                w.append("ecu/00+2", i * 0.1, [800.0 if i < 200 else 0.0], b"\x00\x00")
            if day == 12:
                w.capture("stall", 20.0, 5.0, 25.0, reason="rpm", message="rpm dropped to 0",
                          wall=datetime(2026, 11, 12, 7, 12, 20), meta={"stall_time_s": "20.000"})
        refs.append(w.ref)
    (tmp_path / "ev000007.bbx").write_bytes(b"not read here")
    logs = wired.DriveReader(directory=tmp_path)
    assert isinstance(logs, ad.LogReader) and logs.database == database

    picker = DrivePicker(logs)
    names = [picker.tree.topLevelItem(i).text(0).strip() for i in range(picker.tree.topLevelItemCount())]
    assert names == ["drive_2026_11_12_0712", "stall_20261112_071220", "drive_2026_11_10_0712", "ev000007.bbx"]
    top = picker.tree.topLevelItem(0)
    assert top.text(1) == "2026-11-12 07:12:00" and top.text(2) == "30 s" and top.text(3) == "1 stall"
    assert picker.tree.topLevelItem(1).text(0).startswith("    ") and "rpm dropped" in picker.tree.topLevelItem(1).text(3)
    assert picker.open_btn.isEnabled() and picker.tree.currentItem() is top
    picker.search.setText("hunting")
    assert picker.tree.topLevelItemCount() == 1 and picker.tree.topLevelItem(0).text(3) == "idle hunting"
    picker.search.setText("zzz")
    assert picker.tree.topLevelItemCount() == 0 and not picker.open_btn.isEnabled() and "Nothing" in picker.empty.text()
    picker.search.setText("stall_2026")
    picker._accept()
    assert picker.chosen == refs[1].__class__(database, refs[1].drive, "stall_20261112_071220")
    view = logs.read(picker.chosen)
    assert view.trigger_t == 0.0 and view.title == "stall_20261112_071220" and view.span() == pytest.approx((-15, 5))

    old = tmp_path / "log_20261110_075801.csv"
    old.write_text(OLD_CSV)
    picker = DrivePicker(logs)
    assert picker.import_path(old) and picker.chosen.name == "drive_2026_11_10_0758"
    view = logs.read(picker.chosen)
    assert view.markers == [(pytest.approx(0.4), "cold start")] and view.meta["imported_from"] == str(old)
    assert "drive_2026_11_10_0758" in [r.name for r in logs.drives()]
    bad = tmp_path / "junk.csv"
    bad.write_text("# nothing\n")
    picker = DrivePicker(logs)
    assert not picker.import_path(bad) and picker.chosen is None and "Cannot import" in picker.empty.text()

    empty = DrivePicker(wired.DriveReader(directory=tmp_path / "nowhere"))
    assert empty.tree.topLevelItemCount() == 0 and "No drives recorded yet" in empty.empty.text()
    assert not empty.delete_btn.isEnabled() and not empty.keep_btn.isEnabled()
    for dialog in (picker, empty):
        dialog.deleteLater()


def test_drive_picker_deletes_a_drive_or_a_capture_after_asking(app, tmp_path):
    """Delete in the picker: one question that says what goes, a second one
    for a drive marked keep, and the same code path as ``hondalog-log rm``."""
    from datetime import datetime
    from hondalog.data import Column, db, new_drive
    from hondalog.ui.drivepicker import DrivePicker
    database = tmp_path / "hondalog.db"
    for day in (10, 11, 12):
        started = datetime(2026, 11, day, 7, 12).timestamp()
        with new_drive(database, {"started_unix": started, "epoch_unix": started}) as w:
            w.add_stream("ecu/00+2", [Column("rpm", units="rpm")], source="ecu", raw_len=2)
            for i in range(3000 if day == 10 else 300):
                w.append("ecu/00+2", i * 0.1, [800.0], b"\x00\x00")
            if day == 12:
                w.capture("stall", 20.0, 5.0, 25.0, reason="rpm", message="rpm dropped to 0",
                          wall=datetime(2026, 11, 12, 7, 12, 20))
    (tmp_path / "ev000007.bbx").write_bytes(b"not read here")
    logs = wired.DriveReader(directory=tmp_path)
    picker = DrivePicker(logs)
    asked: list[tuple[str, str]] = []
    answer = [False]
    picker.confirm = lambda title, text: (asked.append((title, text)), answer[0])[1]

    def names():
        return [picker.tree.topLevelItem(i).text(0).strip() for i in range(picker.tree.topLevelItemCount())]

    def pick(name):
        picker.tree.setCurrentItem(picker.tree.topLevelItem(names().index(name)))

    # "no" deletes nothing
    pick("drive_2026_11_10_0712")
    assert picker.delete_btn.isEnabled() and not picker.delete_current()
    assert "Delete drive_2026_11_10_0712?" in asked[-1][1] and "cannot be undone" in asked[-1][1]
    assert len(names()) == 5

    # a capture: only its entry
    answer[0] = True
    pick("stall_20261112_071220")
    assert picker.delete_current() and "stall_20261112_071220" not in names()
    assert "Only the capture's entry goes" in asked[-1][1] and "untouched" in picker.empty.text()
    assert "drive_2026_11_12_0712" in names() and logs.read("drive_2026_11_12_0712").span()[1] > 29

    # a drive: gone from the list and the file, the space given back
    size = db.file_size(database)
    pick("drive_2026_11_10_0712")
    assert picker.delete_current() and "drive_2026_11_10_0712" not in names()
    assert db.file_size(database) < size and "Deleted drive_2026_11_10_0712" in picker.empty.text()
    with pytest.raises(ValueError):
        logs.read("drive_2026_11_10_0712")

    # keep: the button marks it, and deleting asks a second time
    pick("drive_2026_11_11_0712")
    assert picker.keep_btn.isEnabled() and not picker.keep_btn.isChecked()
    picker.set_keep(True)
    assert picker.current_row().name == "drive_2026_11_11_0712" and picker.current_row().keep
    assert picker.keep_btn.isChecked() and "keep" in picker.tree.currentItem().text(3)
    asked.clear()
    replies = iter([True, False])
    picker.confirm = lambda title, text: (asked.append((title, text)), next(replies))[1]
    assert not picker.delete_current() and "drive_2026_11_11_0712" in names()
    assert [title for title, _ in asked] == ["Delete", "Marked keep"]
    picker.confirm = lambda title, text: True
    assert picker.delete_current() and names() == ["drive_2026_11_12_0712", "ev000007.bbx"]

    # a .bbx file is not in the database: no delete, no keep
    pick("ev000007.bbx")
    assert not picker.delete_btn.isEnabled() and not picker.keep_btn.isEnabled()
    assert not picker.delete_current()
    with pytest.raises(ValueError, match="a file, not a drive"):
        logs.delete(tmp_path / "ev000007.bbx")
    picker.deleteLater()
