"""Windows cable handling: adapter detection through list_ports, the latency
timer from the registry (read-only), default ports, report encoding. Runs on
Linux with sys.platform, serial.tools.list_ports and winreg mocked."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from hondalog import transport
from hondalog.diag import firstcontact as fc
from hondalog.diag import ftdi
from hondalog.link import VirtualClock
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport


@dataclass
class FakePort:
    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    description: str = ""


KKL = FakePort("COM21", 0x0403, 0x6001, "ABBQT6LTA", "FTDI", None, "USB Serial Port (COM21)")
BT = FakePort("COM3", None, None, None, None, None, "Standard Serial over Bluetooth link (COM3)")


class FakeKey:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def fake_winreg(entries: dict[str, dict[str, object]]) -> types.ModuleType:
    """entries: FTDIBUS subkey name -> Device Parameters values."""
    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE = "HKLM"            # type: ignore[attr-defined]
    mod.KEY_READ = 0x20019                    # type: ignore[attr-defined]
    writes: list[object] = []
    mod.writes = writes                       # type: ignore[attr-defined]

    def open_key(parent: object, sub: str, reserved: int = 0, access: int = 0) -> FakeKey:
        assert access == mod.KEY_READ          # never opened for writing
        if parent == "HKLM":
            if sub != ftdi.FTDIBUS_KEY:
                raise OSError("no such key")
            return FakeKey("FTDIBUS")
        name = sub.split("\\")[0]
        if name not in entries or not sub.endswith("\\0000\\Device Parameters"):
            raise OSError("no such key")
        return FakeKey(name)

    def enum_key(key: FakeKey, i: int) -> str:
        names = list(entries)
        if i >= len(names):
            raise OSError("no more items")
        return names[i]

    def query(key: FakeKey, value: str) -> tuple[object, int]:
        try:
            return entries[key.path][value], 4
        except KeyError:
            raise OSError("no such value") from None

    def refuse(*a: object) -> None:
        writes.append(a)
        raise AssertionError("the registry must never be written")

    mod.OpenKey = open_key                    # type: ignore[attr-defined]
    mod.EnumKey = enum_key                    # type: ignore[attr-defined]
    mod.QueryValueEx = query                  # type: ignore[attr-defined]
    mod.SetValueEx = refuse                   # type: ignore[attr-defined]
    mod.CreateKey = refuse                    # type: ignore[attr-defined]
    return mod


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows with the KKL cable on COM21 at latency 16."""
    ports = [BT, KKL]
    from serial.tools import list_ports as lp     # import before faking the platform
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(lp, "comports", lambda *a, **k: list(ports))
    reg = fake_winreg({
        "VID_0403+PID_6001+A50285BIA": {"LatencyTimer": 2, "PortName": "COM7"},
        "VID_0403+PID_6001+ABBQT6LTA": {"LatencyTimer": 16, "PortName": "COM21"},
    })
    monkeypatch.setitem(sys.modules, "winreg", reg)
    return types.SimpleNamespace(ports=ports, reg=reg)


def test_detect_windows_ftdi_from_list_ports_and_registry(windows):
    info = ftdi.detect("COM21")
    assert info.kind == "usb-serial" and info.is_ftdi
    assert info.usb_id == "0403:6001" and info.serial == "ABBQT6LTA"
    assert info.chip == "FT232R" and info.verdict == "genuine-looking"
    assert info.latency_ms == 16
    text = " ".join(info.notes)
    assert "does not exist" not in text and "udev" not in text and "Linux" not in text
    assert "Device Manager" in text and "Latency Timer" in text
    assert ftdi.detect("com21").latency_ms == 16          # COM names are case-blind
    assert not windows.reg.writes


def test_windows_latency_is_read_only_and_matches_by_port_name(windows):
    assert ftdi.read_latency_ms("COM21") == 16
    assert ftdi.write_latency_ms("COM21", 1) is False
    assert ftdi.read_latency_ms("COM21") == 16
    # serial-number fallback when PortName is missing
    del_reg = fake_winreg({"VID_0403+PID_6001+ABBQT6LTAA": {"LatencyTimer": 1}})
    sys.modules["winreg"] = del_reg
    assert ftdi.read_latency_ms("COM21") == 1


def test_windows_missing_port_and_no_winreg(windows, monkeypatch):
    info = ftdi.detect("COM9")
    assert info.kind == "unknown" and "Device Manager" in info.notes[0]
    monkeypatch.setitem(sys.modules, "winreg", None)       # import fails
    info = ftdi.detect("COM21")
    assert info.is_ftdi and info.latency_ms is None
    assert any("could not read the latency timer" in n for n in info.notes)


def test_linux_has_no_winreg_and_keeps_sysfs():
    assert sys.platform != "win32"
    assert ftdi.read_registry_latency_ms("COM21", pid=0x6001) is None
    assert "does not exist" in ftdi.detect("/dev/does-not-exist-hondalog").notes[0]


def test_default_port_windows_picks_ftdi_com(windows):
    assert transport.default_port() == "COM21"
    windows.ports[:] = [BT, FakePort("COM5", 0x1A86, 0x7523, None, None, None, "CH340")]
    assert transport.default_port() == "COM5"
    ports = transport.list_ports()
    assert ports[0].device == "COM5"


def test_default_port_and_picker_prefer_ftdi(windows):
    ports = transport.list_ports()
    assert ports[0].device == "COM21" and ports[0].is_ftdi


def test_default_port_linux_without_cable(monkeypatch):
    from serial.tools import list_ports as lp
    monkeypatch.setattr(lp, "comports", lambda *a, **k: [])
    monkeypatch.setattr(transport.os.path, "exists", lambda p: False)
    assert transport.default_port() == transport.UDEV_SYMLINK


def run_windows_fc(port: str = "COM21") -> fc.FirstContactReport:
    clock = VirtualClock(1000.0)
    model = EcuModel(clock=clock.now, rev_every_s=0)
    tr = LoopbackTransport(model, clock, byte_timing=True)
    tr.usb_latency_s = 0.016
    return fc.run(port, transport=tr, clock=clock, listen_s=0.1, quick=True, iso=False)


def test_firstcontact_windows_adapter_and_latency_are_truthful(windows):
    r = run_windows_fc()
    steps = {s.name: s for s in r.steps}
    assert steps["adapter"].ok and "FT232R" in steps["adapter"].detail
    assert steps["latency timer 1 ms"].ok is False
    assert "Device Manager" in steps["latency timer 1 ms"].detail
    assert steps["open port"].detail == "USB latency 16 ms"
    md = r.to_markdown()
    assert "udevadm" not in md and "dialout" not in md
    assert "Latency Timer (msec) = 1" in md


def test_firstcontact_no_port_advice_is_platform_specific(windows):
    assert all("dialout" not in s for s in fc.next_steps(fc.NO_PORT))
    assert any("Device Manager" in s for s in fc.next_steps(fc.NO_PORT))


def test_firstcontact_no_port_advice_linux():
    assert any("dialout" in s for s in fc.next_steps(fc.NO_PORT))


def test_cli_report_is_utf8_and_console_survives_cp1252(tmp_path, monkeypatch):
    import io
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stderr", console)
    out = tmp_path / "fc.md"
    fc.main(["--port", "sim:", "--quick", "--no-iso", "--listen", "0", "-o", str(out)])
    print("40 \N{DEGREE SIGN}C \N{RIGHTWARDS ARROW} ok", file=sys.stderr)   # no crash
    console.flush()
    assert b"40 \xb0C ? ok" in raw.getvalue()
    out.read_bytes().decode("utf-8")
    (tmp_path / "fc.hex.log").read_bytes().decode("utf-8")
