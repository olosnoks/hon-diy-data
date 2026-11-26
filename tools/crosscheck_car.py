"""Cross-check the address map on the car: Honda RAM channels against the
standard OBD2 PIDs, one operating point per run.

    python tools/crosscheck_car.py --port COM21 --label "warm idle"
    python tools/crosscheck_car.py --port COM21 --label "2500 rpm"     # hold it steady
    python tools/crosscheck_car.py --report                           # all points so far

Each run: Honda samples and three raw RAM dumps (0x00-0x7F) for a few
seconds, then one ISO 9141-2 session reading the PIDs (Poller.obd2), then
Honda again once the poller is back, and the point is appended to
``crosscheck-points.json`` (in the log folder). Read-only: Honda 0x20 reads
and OBD2 Mode 01 only. The main app must be closed (one program per port).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from hondalog import addressmap
from hondalog.logger import log_dir
from hondalog.mapping import crosscheck as cc
from hondalog.poller import HONDA, OK, Poller
from hondalog.transport import make_transport

PIDS = [p.pid for p in cc.PIDS]


def points_file() -> Path:
    return log_dir() / "crosscheck-points.json"


def load_points() -> list[dict]:
    path = points_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def wait_honda(poller: Poller, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if poller.state == OK and poller.mode == HONDA and poller.samples and poller.samples[-1].link:
            return True
        time.sleep(0.2)
    return False


def honda_window(poller: Poller, seconds: float) -> tuple[dict, list[list[int]]]:
    poller.samples.clear()
    dumps: list[list[int]] = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        def sweep(link):
            out = []
            for base in range(0x00, 0x80, 0x10):
                out += list(link.read_block(base, 16).data)
            return out
        try:
            dumps.append(poller.submit(sweep).result(5))
        except Exception as exc:                    # a missed sweep is not fatal
            print(f"  sweep failed: {exc}", file=sys.stderr)
        time.sleep(0.3)
    samples = [s.values for s in list(poller.samples) if s.link]
    return cc.steady_mean(samples), dumps


def run_point(port: str, label: str, seconds: float) -> dict:
    amap = addressmap.load_user_map()
    poller = Poller(make_transport(port, amap.framing), amap)
    poller.start()
    try:
        print("waiting for Honda reads ...")
        if not wait_honda(poller, 30):
            raise SystemExit(f"no Honda link on {port} (state {poller.state}, mode {poller.mode}, "
                             f"{poller.last_error})")
        print(f"Honda {seconds:g} s ...")
        before, dumps_a = honda_window(poller, seconds)
        print("OBD2 PIDs ...")
        raw = poller.obd2(lambda s: {pid: list(pv.raw) for pid, pv in s.read_pids(PIDS).items()}).result(30)
        print("back to Honda ...")
        if not wait_honda(poller, 20):
            print("  Honda did not come back; keeping the first window only", file=sys.stderr)
            after, dumps_b = {}, []
        else:
            after, dumps_b = honda_window(poller, seconds)
    finally:
        poller.stop()
    names = set(before) | set(after)
    honda = {k: (before.get(k, after.get(k)) + after.get(k, before.get(k))) / 2 for k in names}
    return {"label": label, "when": datetime.now().isoformat(timespec="seconds"),
            "map": amap.version_tag, "honda": honda, "honda_before": before, "honda_after": after,
            "pid_raw": {f"{k:02X}": v for k, v in raw.items()},
            "pids": cc.pid_values(raw), "ram": dumps_a + dumps_b}


def report(points: list[dict]) -> str:
    amap = addressmap.load_user_map()
    formulas = {c.name: getattr(c, "formula", "x") for c in amap.channels}
    check = cc.CrossCheck(formulas)
    for p in points:
        check.add_snapshot(p["honda"], p["pids"], p["label"])
    lines = [f"{len(points)} point(s): " + ", ".join(p["label"] for p in points), ""]
    for row in check.report():
        lines.append(str(row))
    # where each PID value might live in RAM: the byte at each address, per point
    if points and points[0].get("ram"):
        lines += ["", "RAM means per point (address: values), bytes that changed between points:"]
        import numpy as np
        means = [np.mean(np.array(p["ram"]), axis=0) for p in points if p.get("ram")]
        for addr in range(len(means[0])):
            vals = [m[addr] for m in means]
            if len(vals) > 1 and np.ptp(vals) >= 2:
                lines.append(f"  0x{addr:02X}: " + "  ".join(f"{v:6.1f}" for v in vals))
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM21")
    ap.add_argument("--label", help="operating point, e.g. 'warm idle' or '2500 rpm'")
    ap.add_argument("--seconds", type=float, default=4.0, help="Honda window before and after the PIDs")
    ap.add_argument("--report", action="store_true", help="print the report for all points so far")
    args = ap.parse_args()
    points = load_points()
    if args.label:
        point = run_point(args.port, args.label, args.seconds)
        points.append(point)
        points_file().parent.mkdir(parents=True, exist_ok=True)
        points_file().write_text(json.dumps(points, indent=1), encoding="utf-8")
        print(f"saved point {len(points)} to {points_file()}")
    print(report(points))
    return 0


if __name__ == "__main__":
    sys.exit(main())
