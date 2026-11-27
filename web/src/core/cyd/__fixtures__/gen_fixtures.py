"""Golden fixtures for the TypeScript port of hondalog.handheld (web/src/core/cyd).

Run from the web worktree's root (so this worktree's hondalog is imported), read-only on everything else:

    ..\\hon-diy-data\\.venv\\Scripts\\python.exe web\\src\\core\\cyd\\__fixtures__\\gen_fixtures.py

Writes next to this file: addressmap.toml (the shipped map, copied), *.map (HANDHELD.MAP built by Python),
maps.json (what each map should compile to), frames.json (frame and payload bytes), drive files and their
parse as JSON.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT))

from hondalog import addressmap  # noqa: E402
from hondalog.handheld import format as F  # noqa: E402
from hondalog.handheld import serial_proto as P  # noqa: E402
from hondalog.handheld.drivefile import parse_drive_file  # noqa: E402
from hondalog.handheld.mapfile import ExportReport, build_map  # noqa: E402

TOML = ROOT / "hondalog" / "data" / "addressmap.toml"


def num(x):
    if isinstance(x, float) and math.isnan(x):
        return "nan"
    return x


def maps() -> None:
    text = TOML.read_text(encoding="utf-8")
    (HERE / "addressmap.toml").write_text(text, encoding="utf-8", newline="")
    proto = text.replace("[protocol]\n", '[protocol]\ninit_sequence = "68 6a f5 af bf b3 b2 c1 db b3 e9"\n'
                         "init_delay_s = 0.35\n", 1)
    at = text.index('name = "vss"')
    unver = text[:at] + text[at:].replace('status = "verified"', 'status = "unverified"', 1)
    files = {"default": "addressmap.toml", "p2t": "addressmap.toml", "p28": "addressmap.toml",
             "proto": "proto.toml", "unverified_vss_p2t": "unverified.toml", "stall_opts": "addressmap.toml"}
    cases = {
        "default": (text, None, None),
        "p2t": (text, "P2T", None),
        "p28": (text, "P28", None),
        "proto": (proto, None, None),
        "unverified_vss_p2t": (unver, "P2T", None),
        "stall_opts": (text, None, {"pre_s": 20.0, "post_s": 5.0, "rpm_run": 500.0}),
    }
    (HERE / "proto.toml").write_text(proto, encoding="utf-8", newline="")
    (HERE / "unverified.toml").write_text(unver, encoding="utf-8", newline="")
    out = {}
    for name, (src, variant, stall) in cases.items():
        amap = addressmap.loads(src, variant=variant)
        rep = ExportReport()
        data = build_map(amap, src, stall=stall, report=rep)
        (HERE / f"{name}.map").write_bytes(data)
        out[name] = {"variant": variant, "stall": stall, "hash": amap.version_hash, "channels": rep.channels,
                     "math": rep.math, "alarms": rep.alarms, "skipped": rep.skipped, "core_bytes": rep.core_bytes,
                     "total_bytes": rep.total_bytes, "toml": files[name]}
    # the command line, as the owner runs it (--map: never the user's own map)
    exe = Path(sys.executable).parent / "hondalog-handheld.exe"
    if exe.exists():
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([str(exe), "export-map", "--map", str(TOML), tmp], check=True, capture_output=True)
            cli = (Path(tmp) / "HANDHELD.MAP").read_bytes()
            (HERE / "export-map.HANDHELD.MAP").write_bytes(cli)
            out["export_map_matches_build_map"] = cli == (HERE / "default.map").read_bytes()
            out["export_map_cfg"] = (Path(tmp) / "HANDHELD.CFG").read_text(encoding="utf-8")
    (HERE / "maps.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def frames() -> None:
    hello = P.Hello(P.REV, P.VARIANT_DEFAULT, P.CAP_RX | P.CAP_FILES | P.CAP_RELAY, "0.3.1-ü", "HH-1373A0",
                    "44979f9238b1", b"\x01\x02\x03\x04\x05\x06\x07\x08", b"HONDA37820", P.LINK_OK, 1, 0, 40,
                    1 | 8 | 16, 7, 921600, 123456789, 1_700_000_000_000_000, 2000, 512)
    stats_old = P.pack_stats(t_us=5, unix_us=-1, reads=100, errors=2, hz_c=1937, flags=3, ring_peak=99)
    stats_new = P.pack_stats(t_us=6, reads=7, time_source=P.TS_PPS, time_err_us=20)
    payloads = {
        "hello": hello.pack(),
        "stats_old": stats_old,
        "stats_new": stats_new,
        "relay_read": P.pack_relay_read(0x0102030405060708, 0x10, b"\xAA\xBB\xCC", 1234),
        "relay_iso": P.pack_relay_iso(1_000_000, 0x0C, b"\x1A\xF8"),
        "relay_tick": P.pack_relay_tick(5, 2500.4, 19.37, 77),
        "relay_tick_clamped": P.pack_relay_tick(5, 1e9, 1e9, 2**40),
        "relay_link": P.pack_relay_link(9, P.LINK_ISO_ONLY, True, True, b"0123456789AB"),
        "relay_link_off": P.pack_relay_link(9, P.LINK_OFF, False, False),
        "time_rsp": P.TIME_RSP.pack(-5, 10, 30),
        "ack": P.ACK.pack(P.SC_MARK, 3, P.E_OFFSET, 4096),
        "file_info": P.FILE_INFO.pack(P.F_DRIVE, P.FI_OPEN, 0, 12, 99999),
        "file_data": P.FILE_DATA.pack(P.F_DRIVE, P.FD_EOF, 3, 12, 480, F.crc32(b"abc")) + b"abc",
        "list": P.LIST.pack(P.F_CAPTURE, 5),
        "read_file": P.READ_FILE.pack(P.F_DRIVE, 480, 12, 960),
        "put_begin": P.PUT_BEGIN.pack(P.F_MAP, 1234, 0xDEADBEEF),
        "mapup_open": P.MAPUP_OPEN.pack(0, 11, 5000, 0x12345678) + b"MAP/MAP.IDX",
        "mapup_write": P.MAPUP_WRITE.pack(504, 0xCAFEBABE) + b"xyz",
        "mapup_commit": P.MAPUP_COMMIT.pack(P.MAPUP_FINAL),
        "mapup_info": P.MAPUP_INFO.pack(P.MU_PAUSED, 100, 200, 0xABCDEF01, F.fit("MAP/MAP.DAT", 16)),
        "event_float": F.REC_EVENT.pack(77, F.EV_STALL, 1, 0, 0)[:12] + struct.pack("<f", 412.5),
        "event_marker": F.REC_EVENT.pack(78, F.EV_MARKER, 0, 0, 3),
        "gps": F.REC_GPS.pack(1000, 322746000, -901320000, 1700000000, 1000, 9000, 1, 9, 90),
        "time_req": struct.pack("<q", 123456789012),
    }
    zeros = bytes(300) + bytes(range(256)) + bytes(10)
    encoded = []
    for i, (name, p) in enumerate(payloads.items()):
        encoded.append({"name": name, "type": 0x40 + i % 16, "seq": (i * 37) & 0xFF, "payload": p.hex(),
                        "frame": P.frame_encode(0x40 + i % 16, p, (i * 37) & 0xFF).hex()})
    encoded.append({"name": "long", "type": P.SF_LOG, "seq": 255, "payload": zeros[:512].hex(),
                    "frame": P.frame_encode(P.SF_LOG, zeros[:512], 255).hex()})
    parsed_hello = P.parse_hello(hello.pack())
    out = {
        "frames": encoded,
        "hello_text": parsed_hello.text,
        "hello": {k: (v.hex() if isinstance(v, bytes) else v) for k, v in parsed_hello.__dict__.items()},
        "stats_old": P.parse_stats(stats_old),
        "stats_new": P.parse_stats(stats_new),
        "crc16": [[s, P.crc16(s.encode())] for s in ("", "123456789", "hondalog")],
        "crc32": [[s, F.crc32(s.encode())] for s in ("", "123456789", "hondalog")],
        "cobs": [[b.hex(), P.cobs_encode(b).hex()] for b in (b"", b"\0", b"\0\0", b"\x11\x22\0\x33",
                                                             bytes(range(1, 255)), bytes(range(1, 256)),
                                                             bytes(range(0, 256)))],
        "fit": [[t, n, F.fit(t, n).hex()] for t, n in (("° BTDC", 8), ("°°°°°", 8),
                                                     ("abcdefghijklmnop", 16), ("", 4), ("ü", 2))],
        "dtc": [[c, F.dtc_text(c)] for c in (0x0301, 0x4123, 0x8000, 0xC0FF)],
    }
    (HERE / "frames.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def drives() -> None:
    from hondalog.handheld.cydsim import CydSim
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp) / "sd"
        sim = CydSim(sd=sd, stall_after=6.0, unix_time=1_700_000_000.0, seed=3)
        sim.advance(4.0)
        sim.mark()
        sim.advance(14.0)
        sim.close()
        files = sorted((sd / "DRIVES").glob("*.HHD")) + sorted((sd / "CAPTURES").glob("*.HHD"))
        out = []
        for path in files:
            data = path.read_bytes()
            name = path.parent.name[0] + "_" + path.name
            (HERE / name).write_bytes(data)
            out.append({"file": name, "parse": summary(parse_drive_file(data))})
            # a power cut: no END block, stale bytes after it
            end = data.rfind(F.BLOCK_MAGIC_BYTES)
            torn = data[:end] + bytes((i * 131 + 7) & 0xFF for i in range(5000))
            (HERE / ("torn_" + name)).write_bytes(torn)
            out.append({"file": "torn_" + name, "parse": summary(parse_drive_file(torn))})
        shutil.copy(sd / "HANDHELD.MAP", HERE / "sim.HANDHELD.MAP")
    (HERE / "drives.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def summary(df) -> dict:
    return {
        "kind": df.kind, "file_no": df.file_no, "boot_no": df.boot_no, "flags": df.flags, "backend": df.backend,
        "link_mode": df.link_mode, "nonce": df.nonce, "t0_us": df.t0_us, "unix_us_at_t0": df.unix_us_at_t0,
        "map_hash": df.map_hash, "fw": df.fw, "device": df.device, "ecu_id": df.ecu_id.hex(),
        "drive_no": df.drive_no, "reason": df.reason, "trigger_t_us": df.trigger_t_us, "prealloc": df.prealloc,
        "reads": len(df.reads), "first_reads": [[r.t_us, r.addr, r.data.hex(), r.latency_us] for r in df.reads[:5]],
        "last_read": [df.reads[-1].t_us, df.reads[-1].addr, df.reads[-1].data.hex()] if df.reads else None,
        "iso": len(df.iso), "gps": [[g.t_us, g.lat, g.lon, g.utc, g.speed_kmh, g.heading, g.fix, g.sats, g.hdop]
                                    for g in df.gps[:5]], "n_gps": len(df.gps),
        "events": [[e.t_us, e.code, e.src, e.aux, num(e.value)] for e in df.events],
        "captures": [[c.t_trigger_us, c.t_start_us, c.t_end_us, c.reason, c.complete, c.n_fired]
                     for c in df.captures],
        "times": df.times, "config_len": len(df.config), "alarm_names": df.alarm_names, "end": df.end,
        "blocks": df.blocks, "bad_blocks": df.bad_blocks, "missing_blocks": df.missing_blocks,
        "used_bytes": df.used_bytes, "size": df.size, "id": df.id, "epoch_unix": df.epoch_unix,
        "t_last_us": df.t_last_us,
    }


if __name__ == "__main__":
    maps()
    frames()
    drives()
    print("fixtures written to", HERE)
