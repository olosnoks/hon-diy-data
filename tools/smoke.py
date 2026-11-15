#!/usr/bin/env python3
"""End-to-end smoke test against hondalog-sim on a real pty. No car needed.

Part 1 (no GUI): poller -> sim pty -> CSV log -> stall capture (RPM to 0).
Part 1b:         the same with the ECU going silent (power cut).
Part 2 (offscreen GUI): main window at 1366x768 against a second sim run,
        marker, memory sweep, stall banner, replay; saves screenshots to docs/.

    QT_QPA_PLATFORM=offscreen python3 tools/smoke.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="hondalog-smoke-"))
os.environ["HONDALOG_DIR"] = str(TMP / "logs")
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LINK = str(TMP / "sim-pty")

from hondalog import addressmap, protocol                    # noqa: E402
from hondalog.logger import Recorder, read_log                # noqa: E402
from hondalog.poller import OK, Poller                        # noqa: E402
from hondalog.transport import SerialTransport                # noqa: E402


def start_sim(*args):
    proc = subprocess.Popen([sys.executable, "-m", "hondalog.sim", "--link", LINK, *args],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    print("  sim:", proc.stdout.readline().strip())
    assert os.path.exists(LINK), "simulator did not create its pty link"
    return proc


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        raise SystemExit(1)


def headless():
    print("Part 1: headless poller against the sim pty")
    sim = start_sim("--warmup", "6", "--rev-every", "5", "--stall-after", "13", "--restart-after", "0")
    amap = addressmap.load_user_map()
    poller = Poller(SerialTransport(LINK, amap.framing.baud), amap)
    poller.start()
    try:
        check(poller.ready.wait(5), "port opened")
        check(poller.ecu_id is not None and b"SIM" in poller.ecu_id,
              f"ECU id read: {poller.ecu_id_text}")
        check(poller.link.echo_seen, "K-line echo seen and stripped")
        rec = Recorder(amap, poller.ecu_id_text, LINK)
        poller.on_sample = rec.feed
        time.sleep(4)
        rec.mark("smoke marker")
        check(poller.state == OK and poller.errors == 0,
              f"polling: {poller.reads} reads, {poller.errors} errors, {poller.hz:.1f} Hz")
        end = time.monotonic() + 30
        while rec.events.empty() and time.monotonic() < end:
            time.sleep(0.1)
        check(not rec.events.empty(), "stall capture written")
        event = rec.events.get()
    finally:
        poller.stop()
        rec.stop()
        sim.terminate()
        sim.wait()
    log, stall = read_log(rec.path), read_log(event.path)
    check(len(log.t) > 100 and log.meta["address_map_version"] == amap.version,
          f"log {rec.path.name}: {len(log.t)} rows, header has map version")
    check(any("smoke marker" in m for _, m in log.markers), "marker is in the log")
    check(float(log.data["rpm"].max()) > 2500, f"fake rev seen (max {log.data['rpm'].max():.0f} rpm)")
    st = float(stall.meta["stall_time_s"])
    check(event.reason == "rpm" and stall.data["rpm"][-1] == 0,
          f"{event.path.name}: trigger '{stall.meta['stall_trigger']}' at {st:.1f} s")
    check(stall.t[-1] - st >= 10.0 and st - stall.t[0] > 8.0,
          f"stall window: {st - stall.t[0]:.1f} s before, {stall.t[-1] - st:.1f} s after")
    return event.path


def headless_power_cut():
    print("Part 1b: ECU loses power (K-line goes silent) while recording")
    sim = start_sim("--warmup", "4", "--rev-every", "0", "--stall-after", "5",
                    "--stall-mode", "power", "--restart-after", "0")
    amap = addressmap.load_user_map()
    poller = Poller(SerialTransport(LINK, amap.framing.baud), amap)
    poller.start()
    try:
        check(poller.ready.wait(5) and poller.ecu_id is not None, "connected")
        rec = Recorder(amap, poller.ecu_id_text, LINK)
        poller.on_sample = rec.feed
        end = time.monotonic() + 30
        while rec.events.empty() and time.monotonic() < end:
            time.sleep(0.1)
        check(not rec.events.empty(), "stall capture written")
        event = rec.events.get()
        check(poller.state == "no reply" and "echo only" in poller.last_error,
              f"link state '{poller.state}', last error: {poller.last_error}")
    finally:
        poller.stop()
        rec.stop()
        sim.terminate()
        sim.wait()
    stall = read_log(event.path)
    st = float(stall.meta["stall_time_s"])
    check(event.reason == "link" and stall.data["link"][-1] == 0,
          f"{event.path.name}: trigger '{stall.meta['stall_trigger']}' at {st:.1f} s")
    check(stall.t[-1] - st >= 10.0, f"kept logging {stall.t[-1] - st:.1f} s after the drop")


def gui(docs: Path):
    print("Part 2: offscreen GUI at 1366x768")
    from hondalog import ui  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from hondalog.ui import theme
    from hondalog.ui.main_window import MainWindow

    app = QApplication([])
    theme.apply(app)
    win = MainWindow()
    win.resize(1366, 768)
    win.show()

    def spin(seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until and until():
                return True
            time.sleep(0.005)
        return until is None

    sim = start_sim("--warmup", "8", "--rev-every", "6", "--stall-after", "16", "--restart-after", "0")
    try:
        win.rescan_ports()
        win.connect_port(LINK)
        check(spin(5, lambda: win.poller and win.poller.state == OK), "GUI connected to the sim pty")
        check(spin(2, lambda: win.recorder is not None), "auto-record started")
        spin(4)
        win.mark()
        spin(3)
        win.mark()

        win.tabs.setCurrentWidget(win.rawtools)
        raw = win.rawtools
        raw.hex_in.setText("20 05 76 0A")
        raw.send_console()
        raw.start_scan(False)
        check(spin(6, lambda: raw._sweeps >= 1), "memory sweep 1 done")
        raw.start_scan(False)
        check(spin(6, lambda: raw._sweeps >= 2), "memory sweep 2 done")
        check(int((raw.changes > 0).sum()) > 5 and not raw.failed.any(),
              f"scanner saw {int((raw.changes > 0).sum())} bytes change, none unreadable")
        check("checksum OK" in raw.console.toPlainText(), "hex console round trip")
        raw.grid.setCurrentCell(0, 0)
        written = raw.export(TMP / "scan.txt")
        check(len(written) == 2 and written[0].stat().st_size > 1000, "scan export (.txt + .csv)")
        spin(0.3)
        win.grab().save(str(docs / "screenshot-rawtools.png"))

        win.tabs.setCurrentWidget(win.dashboard)
        check(spin(40, lambda: win.dashboard.banner.isVisible()), "stall banner shown")
        spin(0.5)
        check(win.grab().save(str(docs / "screenshot.png")), "dashboard screenshot saved")
        stall_path = win.dashboard.banner._path
        rate = win.poller.hz
        win.disconnect_port()
        win.open_in_replay(stall_path)
        check(win.replay.log is not None and len(win.replay.log.markers) >= 1,
              "stall file opens in replay with markers")
        win.replay.slider.setValue(win.replay.slider.maximum() // 3)
        spin(0.5)
        win.grab().save(str(docs / "screenshot-replay.png"))
        print(f"  window {win.width()}x{win.height()}, minimum "
              f"{win.minimumSizeHint().width()}x{win.minimumSizeHint().height()}, "
              f"last poll rate {rate:.1f} Hz")
    finally:
        win.close()
        sim.terminate()
        sim.wait()


if __name__ == "__main__":
    docs = ROOT / "docs"
    if not os.access(docs if docs.exists() else ROOT, os.W_OK):
        docs = TMP                      # installed copy in /opt is read-only
    docs.mkdir(exist_ok=True)
    print(f"screenshots go to {docs}")
    headless()
    headless_power_cut()
    gui(docs)
    print(f"ALL OK  (scratch files in {TMP})")
