"""Read-only RAM sweep 0x00-0xFF in 16-byte rows, repeated at ~1 Hz.

Sends only Honda read requests (20 05 addr len cksum) and the map's wake
frame, through hondalog's own Link (read-only guard on). Every read is
written as it happens, so a crash loses nothing.

    python sweep.py --port sim: --seconds 20 --out work-sweep/sim
    python sweep.py --port COM21 --seconds 200 --out work-sweep/car

Outputs (OUT prefix):
    OUT-reads.csv   one line per request: wall time, monotonic, sweep, addr, len, ok, error, hex
    OUT-wide.csv    one line per sweep: t, b00..bFF (blank = no reply)
    OUT-meta.json   framing, link stats, per-address failure counts, discovery result
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from hondalog import protocol
from hondalog.addressmap import load_user_map
from hondalog.transport import Link, make_transport


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", required=True)
    p.add_argument("--seconds", type=float, default=200.0, help="hard stop for the sweep loop")
    p.add_argument("--period", type=float, default=1.0, help="target sweep period")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.port.upper() == "COM23":
        sys.exit("refusing COM23 (handheld)")

    amap = load_user_map()
    framing = amap.framing
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tr = make_transport(a.port, framing)
    link = Link(tr, framing)
    assert link.read_only

    rows = list(range(0x00, 0x100, 16))
    fails = {r: 0 for r in rows}
    oks = {r: 0 for r in rows}
    errors: dict[str, int] = {}
    t_start_wall = datetime.now().isoformat(timespec="milliseconds")
    t0 = time.monotonic()
    deadline = t0 + a.seconds
    sweep = 0
    consecutive_fail = 0
    discovery: dict[str, str] = {}

    reads_f = open(f"{out}-reads.csv", "w", newline="")
    wide_f = open(f"{out}-wide.csv", "w", newline="")
    rw = csv.writer(reads_f)
    ww = csv.writer(wide_f)
    rw.writerow(["wall", "t", "sweep", "addr", "len", "ok", "error", "hex", "attempts"])
    ww.writerow(["wall", "t", "sweep", "dur_s"] + [f"b{i:02X}" for i in range(256)])

    def read(addr: int, length: int):
        nonlocal consecutive_fail
        wall = datetime.now().isoformat(timespec="milliseconds")
        t = time.monotonic() - t0
        try:
            res = link.read_block(addr, length)
            rw.writerow([wall, f"{t:.4f}", sweep, f"0x{addr:02X}", length, 1, "",
                         res.data.hex(" "), res.attempts])
            consecutive_fail = 0
            return res.data
        except protocol.ProtocolError as exc:
            name = type(exc).__name__
            errors[name] = errors.get(name, 0) + 1
            rw.writerow([wall, f"{t:.4f}", sweep, f"0x{addr:02X}", length, 0,
                         f"{name}: {exc}", "", ""])
            consecutive_fail += 1
            return None

    try:
        tr.open()
        link.reset()
        link.wake()
        while time.monotonic() < deadline:
            ts = time.monotonic()
            wall = datetime.now().isoformat(timespec="milliseconds")
            vals: list[str] = [""] * 256
            for r in rows:
                if time.monotonic() >= deadline:
                    break
                data = read(r, 16)
                if data is None:
                    fails[r] += 1
                    if consecutive_fail >= 4:
                        link.wake()          # P2P drops back to sleep: wake again
                        consecutive_fail = 0
                    # first failing time: find which halves / bytes answer (once per row)
                    if f"0x{r:02X}" not in discovery:
                        parts = []
                        for h in (r, r + 8):
                            d8 = read(h, 8)
                            if d8 is not None:
                                parts.append(f"0x{h:02X}+8 ok")
                                for i, v in enumerate(d8):
                                    vals[h + i] = str(v)
                            else:
                                single = []
                                for b in range(h, h + 8):
                                    d1 = read(b, 1)
                                    if d1 is not None:
                                        vals[b] = str(d1[0])
                                        single.append(f"{b:02X}")
                                parts.append(f"0x{h:02X}+8 fail; single ok: {' '.join(single) or 'none'}")
                        discovery[f"0x{r:02X}"] = "; ".join(parts)
                    continue
                oks[r] += 1
                for i, v in enumerate(data):
                    vals[r + i] = str(v)
            dur = time.monotonic() - ts
            ww.writerow([wall, f"{ts - t0:.4f}", sweep, f"{dur:.3f}"] + vals)
            reads_f.flush()
            wide_f.flush()
            sweep += 1
            rest = a.period - (time.monotonic() - ts)
            if rest > 0 and time.monotonic() + rest < deadline:
                time.sleep(rest)
    finally:
        try:
            tr.close()
        except Exception:
            pass
        reads_f.close()
        wide_f.close()
        st = link.stats
        meta = {
            "port": a.port,
            "start_wall": t_start_wall,
            "end_wall": datetime.now().isoformat(timespec="milliseconds"),
            "seconds_on_line": round(time.monotonic() - t0, 2),
            "sweeps": sweep,
            "framing": {"baud": framing.baud, "init": framing.init_sequence.hex(" "),
                        "max_read_len": framing.max_read_len},
            "row_ok": {f"0x{r:02X}": oks[r] for r in rows},
            "row_fail": {f"0x{r:02X}": fails[r] for r in rows},
            "errors": errors,
            "discovery": discovery,
            "link_stats": {k: getattr(st, k) for k in dir(st)
                           if not k.startswith("_") and isinstance(getattr(st, k), (int, float))},
        }
        Path(f"{out}-meta.json").write_text(json.dumps(meta, indent=2))
        print(json.dumps({k: meta[k] for k in ("sweeps", "seconds_on_line", "errors", "discovery")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
