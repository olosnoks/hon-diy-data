"""Import handheld drive files into the database, one drive each.

A drive comes out like a laptop recording: one ``ecu/AA+N`` stream per block
with the raw bytes and the values decoded by the map the handheld ran
(found by its hash), ``ecu/link``, a ``gps`` stream, markers, alarms, stall
captures as rows in ``captures``, and the map's own ``address_map_hash``.
Importing a card twice adds nothing: a drive is remembered by the identity
its file carries.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__, addressmap
from ..addressmap import AddressMap
from ..data import db
from ..data.db import DriveRef
from ..data.model import Column
from ..data.store import Store
from ..logger import LINK, LINK_STREAM, column_for
from ..obd2 import decode_pid
from . import format as F
from .drivefile import DriveFile, read_drive_file
from .mapfile import MAP_NAME, read_map

GPS_STREAM = "gps"
#: Mode 01 PIDs that are the same quantity as a map channel
PID_CHANNEL = {0x0C: "rpm", 0x0D: "vss", 0x0B: "map", 0x11: "tps", 0x0E: "ign", 0x14: "o2", 0x06: "stft",
               0x07: "ltft", 0x05: "ect", 0x0F: "iat"}


@dataclass
class ImportResult:
    path: Path
    status: str                         # "imported" | "skipped" | "failed"
    reason: str = ""
    drive: DriveRef | None = None
    reads: int = 0
    gps: int = 0
    captures: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.status == "imported":
            extra = "".join(f"; {n}" for n in self.notes)
            return (f"{self.path.name}: imported as {self.drive.name if self.drive else '?'} "
                    f"({self.reads} reads, {self.gps} GPS fixes, {self.captures} captures{extra})")
        return f"{self.path.name}: {self.status}: {self.reason}"


def already_imported(database: str | Path, handheld_id: str) -> str | None:
    """Name of the drive this recording was imported as, or None."""
    path = db.db_path(database)
    if not path.is_file():
        return None
    try:
        _, rows = db.query(path, "SELECT name FROM drives WHERE json_extract(meta, '$.handheld_id') = ? LIMIT 1",
                           (handheld_id,))
    except (OSError, ValueError, sqlite3.Error):
        return None
    return str(rows[0][0]) if rows else None


def _map_from_db(database: str | Path, map_hash: str) -> AddressMap | None:
    path = db.db_path(database)
    if not path.is_file():
        return None
    try:
        _, rows = db.query(path, "SELECT text FROM address_maps WHERE hash = ? ORDER BY id DESC", (map_hash,))
    except (OSError, ValueError, sqlite3.Error):
        return None
    for (text,) in rows:
        for variant in _variants(text):
            try:
                amap = addressmap.loads(text, variant=variant)
            except addressmap.MapError:
                continue
            if amap.version_hash == map_hash:
                return amap
    return None


def _variants(text: str) -> list[str | None]:
    """The file's own variant first, then every other it lists."""
    try:
        base = addressmap.loads(text)
    except addressmap.MapError:
        return [None]
    return [None] + [v for v in base.variants if v != base.variant]


def find_map(df: DriveFile, *, card: str | Path | None = None, database: str | Path | None = None,
             maps: list[AddressMap] | None = None) -> tuple[AddressMap | None, str]:
    """The address map a file was recorded with: (map, where it was found).

    Looked for by hash: maps given by the caller, the card's HANDHELD.MAP
    (it carries the map text), the database's stored maps, your map, the
    default map."""
    for amap in maps or []:
        if amap.version_hash == df.map_hash:
            return amap, amap.path or "given map"
    if card is not None:
        try:
            mf = read_map(Path(card) / MAP_NAME)
            if mf.map_hash == df.map_hash and mf.source:
                amap = mf.address_map()
                if amap is not None and amap.version_hash == df.map_hash:
                    return amap, MAP_NAME
        except (OSError, ValueError, addressmap.MapError):
            pass
    if database is not None:
        amap = _map_from_db(database, df.map_hash)
        if amap is not None:
            return amap, "database"
    for loader, where in ((_user_map, "your map"), (addressmap.load_default, "default map")):
        try:
            amap = loader()
        except (addressmap.MapError, OSError):
            continue
        if amap is not None and amap.version_hash == df.map_hash:
            return amap, where
    return None, ""


def _user_map() -> AddressMap | None:
    path = addressmap.user_map_path()
    return addressmap.load(path) if path.exists() else None


def _map_text(amap: AddressMap) -> str:
    try:
        return Path(amap.path).read_text(encoding="utf-8") if amap.path else ""
    except OSError:
        return ""


def import_file(path: str | Path, database: str | Path | None = None, *, amap: AddressMap | None = None,
                card: str | Path | None = None, map_text: str | None = None, force: bool = False,
                allow_other_map: bool = False) -> ImportResult:
    """Import one drive (or capture) file as a drive.

    ``amap``: the map to decode with; by default it is found by the file's
    hash (see ``find_map``). A map with another hash is refused unless
    ``allow_other_map``: the values would not be what the handheld showed.
    ``force``: import again even if the database already has this recording.
    """
    path = Path(path)
    target = db.db_path(database)
    try:
        df = read_drive_file(path)
    except (OSError, ValueError) as exc:
        return ImportResult(path, "failed", str(exc))
    if not (df.reads or df.iso or df.events or df.gps):
        return ImportResult(path, "skipped", "nothing recorded in it")
    old = already_imported(target, df.id)
    if old and not force:
        return ImportResult(path, "skipped", f"already imported as {old}")

    where = ""
    if amap is None or (amap.version_hash != df.map_hash and not allow_other_map):
        given = [amap] if amap is not None else []
        amap, where = find_map(df, card=card if card is not None else _card_of(path), database=target, maps=given)
        if amap is None:
            return ImportResult(path, "failed", f"recorded with address map #{df.map_hash}, which is not on the "
                                "card, in the database or your map (pass it with --map)")
        map_text = None
    text = map_text if map_text is not None else (read_text_from_card(card or _card_of(path), df) or _map_text(amap))

    result = ImportResult(path, "imported", reads=len(df.reads) + len(df.iso), gps=len(df.gps))
    epoch = df.epoch_unix
    if epoch is not None:
        started = datetime.fromtimestamp(epoch + min(0.0, df.seconds(_first_t(df))))
    else:
        started = datetime.fromtimestamp(path.stat().st_mtime)
        result.notes.append("no GPS time in the file: start time taken from the file's date")
    if df.bad_blocks:
        result.notes.append(f"{df.bad_blocks} damaged stretch(es) skipped")
    if df.missing_blocks:
        result.notes.append(f"{df.missing_blocks} block(s) never reached the card")
    if not df.complete:
        result.notes.append("no end marker (power was cut)")

    capture_file = df.kind == F.FK_CAPTURE
    meta: dict[str, Any] = {
        "format": f"hondalog {__version__} (handheld {df.fw})",
        "timebase": "seconds since the drive started (handheld clock)",
        "source": "handheld",
        "ecu": amap.ecu,
        "ecu_id": df.ecu_id_text,
        "port": f"handheld {F.BACKENDS.get(df.backend, '?')}" + (" ISO 9141-2" if df.link_mode else ""),
        "address_map_version": amap.version,
        "address_map_tag": amap.version_tag,
        "address_map_hash": amap.version_hash,
        "ecu_variant": amap.variant,
        "address_map_file": where or amap.path,
        "address_map_text": text,
        "unverified_channels": " ".join(c.name for c in amap.channels if not c.verified) or "none",
        "units": " ".join(f"{c.name}={c.units or '-'}" for c in amap.channels),
        "handheld_id": df.id,
        "handheld": {"device": df.device, "boot": df.boot_no, "file": path.name, "firmware": df.fw,
                     "kind": "capture" if capture_file else "drive", "complete": df.complete,
                     "end_reason": df.end["reason"] if df.end else "power_off", "blocks": df.blocks,
                     "bad_blocks": df.bad_blocks, "missing_blocks": df.missing_blocks,
                     "map_hash": df.map_hash, "clock": "gps" if epoch is not None else "unknown",
                     "config": df.config},
        "link": {"reads": len(df.reads) + len(df.iso), "blocks": df.blocks, "bad_blocks": df.bad_blocks,
                 "missing_blocks": df.missing_blocks},
    }
    if epoch is not None:
        meta["epoch_unix"] = epoch
    if amap.version_hash != df.map_hash:
        result.notes.append(f"decoded with map #{amap.version_hash}, recorded with #{df.map_hash}")

    store = Store(target, background=False)
    try:
        if old and force:
            conn = db.connect(target, write=True)
            try:
                db.delete_drives(conn, [old])
            finally:
                conn.close()
        drive = store.begin_drive(meta, started=started,
                                  name=None if epoch is not None or not capture_file else None)
        _write_reads(drive, df, amap)
        _write_iso(drive, df, amap)
        _write_link(drive, df)
        _write_gps(drive, df)
        result.captures = _write_events(drive, df, amap, epoch, started)
        drive.end(status="imported", reason=df.end["reason"] if df.end else "power_off")
        result.drive = drive.ref
    except (OSError, ValueError, sqlite3.Error) as exc:
        return ImportResult(path, "failed", str(exc))
    finally:
        store.close()
    return result


def _card_of(path: Path) -> Path | None:
    """DRIVES/Dnnnnnn.HHD -> the card's root, if it looks like one."""
    root = path.resolve().parent.parent
    return root if (root / MAP_NAME).is_file() else None


def read_text_from_card(card: str | Path | None, df: DriveFile) -> str:
    """The map text from the card's HANDHELD.MAP, if it is this file's map."""
    if card is None:
        return ""
    try:
        mf = read_map(Path(card) / MAP_NAME)
    except (OSError, ValueError):
        return ""
    return mf.source if mf.map_hash == df.map_hash else ""


def _first_t(df: DriveFile) -> int:
    first = [seq[0].t_us for seq in (df.reads, df.iso, df.events) if seq]
    return min(first) if first else df.t0_us


def _write_reads(drive: Any, df: DriveFile, amap: AddressMap) -> None:
    channels: dict[tuple[int, int], list[Any]] = {}
    for r in df.reads:
        key = (r.addr, len(r.data))
        chans = channels.get(key)
        name = f"ecu/{r.addr:02X}+{len(r.data)}"
        if chans is None:
            chans = channels[key] = [c for c in amap.channels
                                     if r.addr <= c.addr and c.addr + c.length <= r.addr + len(r.data)]
            drive.add_stream(name, [column_for(c) for c in chans], source="ecu", raw_len=len(r.data),
                             attrs={"addr": r.addr, "length": len(r.data), "mode": "read"})
        values = [c.convert(r.data[c.addr - r.addr:c.addr - r.addr + c.length]) for c in chans]
        drive.append(name, df.seconds(r.t_us), values, r.data, r.latency_us / 1e6)


def _write_iso(drive: Any, df: DriveFile, amap: AddressMap) -> None:
    defined: set[str] = set()
    for r in df.iso:
        if r.mode != 0x41:
            continue
        name = f"obd2/{r.pid:02X}"
        pv = decode_pid(r.pid, r.data)
        if name not in defined:
            defined.add(name)
            ch = amap.channel(PID_CHANNEL.get(r.pid, ""))
            column = column_for(ch) if ch is not None else Column(f"pid_{r.pid:02x}", "f4", pv.units, pv.name)
            if ch is not None:      # the standard PID's own units, not the map's guess at the RAM byte
                column = Column(ch.name, "f4", pv.units or ch.units, ch.label if ch.label != ch.name else "",
                                ch.kind, ch.decimals, float(ch.min), float(ch.max))
            drive.add_stream(name, [column], source="obd2", raw_len=len(r.data),
                             attrs={"mode": 1, "pid": r.pid, "name": pv.name})
        try:
            drive.append(name, df.seconds(r.t_us), [pv.value], r.data)
        except ValueError:          # a reply of another length than the first: keep the stream consistent
            continue


def _write_link(drive: Any, df: DriveFile) -> None:
    changes = [(e.t_us, 1 if e.code == F.EV_LINK_UP else 0) for e in df.events
               if e.code in (F.EV_LINK_UP, F.EV_LINK_DOWN)]
    if not changes:
        return
    drive.add_stream(LINK_STREAM, [Column(LINK, "u1", kind="flag", decimals=0)], source="ecu")
    for t_us, up in changes:
        drive.append(LINK_STREAM, df.seconds(t_us), [up])


def _write_gps(drive: Any, df: DriveFile) -> None:
    if not df.gps:
        return
    drive.add_stream(GPS_STREAM, [
        Column("gps_lat", "f8", "°", "Latitude", decimals=6, lo=-90.0, hi=90.0),
        Column("gps_lon", "f8", "°", "Longitude", decimals=6, lo=-180.0, hi=180.0),
        Column("gps_speed", "f4", "km/h", "GPS speed", decimals=1, lo=0.0, hi=200.0),
        Column("gps_heading", "f4", "°", "Heading", decimals=0, lo=0.0, hi=360.0),
        Column("gps_sats", "u1", "", "Satellites", decimals=0, lo=0.0, hi=16.0),
        Column("gps_fix", "u1", "", "GPS fix", kind="flag", decimals=0),
        Column("gps_hdop", "f4", "", "HDOP", decimals=1, lo=0.0, hi=20.0),
        Column("gps_utc", "f8", "s", "GPS time (unix)", decimals=0),
    ], source="gps", storage="rows")
    for g in df.gps:
        has = bool(g.fix)
        drive.append(GPS_STREAM, df.seconds(g.t_us),
                     [g.lat if has else None, g.lon if has else None, g.speed_kmh if has else None,
                      g.heading if has else None, g.sats, 1 if has else 0, g.hdop,
                      float(g.utc) if g.utc else None])


def _write_events(drive: Any, df: DriveFile, amap: AddressMap, epoch: float | None, started: datetime) -> int:
    """Markers, alarms, link and trouble-code events; stall captures. Returns
    the number of captures."""
    rules = {r.name: r for r in amap.alarms}
    labels = {c.name: (c.label, c.units) for c in amap.channels}
    kinds = {F.EV_ALARM_RAISED: "raised", F.EV_ALARM_CLEARED: "cleared", F.EV_ALARM_ACK: "acknowledged"}
    words = {"raised": "ALARM", "cleared": "CLEAR", "acknowledged": "ACK"}
    for e in df.events:
        t = df.seconds(e.t_us)
        if e.code == F.EV_MARKER:
            drive.event(t, "marker", f"MARK {e.value}")
        elif e.code in kinds:
            name = df.alarm_names.get(e.src, f"alarm_{e.src}")
            rule = rules.get(name)
            channel = rule.channel if rule else ""
            value = float(e.value) if e.value == e.value else None
            label, units = labels.get(channel, (channel, ""))
            shown = f"{label} {value:.4g} {units}".strip() if channel and value is not None else \
                (rule.label if rule else name)
            drive.event(t, "alarm", f"{words[kinds[e.code]]} {name}: {shown}",
                        {"alarm": name, "kind": kinds[e.code], "channel": channel, "value": value,
                         "severity": "critical" if e.aux >= 2 else "warn",
                         "unverified": bool(channel) and not amap.is_verified(channel)})
        elif e.code == F.EV_STALL:
            reason = F.STALL_REASONS.get(e.src, str(e.src))
            drive.event(t, "trigger", "STALL (rpm 0)" if e.src == 1 else "STALL (link lost)" if e.src == 2
                        else "CAPTURE (alarm)",
                        {"rule": "stall" if e.src == 1 else "link_loss" if e.src == 2 else "alarm",
                         "reason": reason, "message": F.STALL_MESSAGES.get(e.src, reason), "severity": "critical"})
        elif e.code == F.EV_DTC:
            drive.event(t, "dtc", F.dtc_text(int(e.value)), {"code": F.dtc_text(int(e.value)), "status": "stored"})
        elif e.code == F.EV_DTC_NONE:
            drive.event(t, "dtc", "no stored trouble codes", {})
        elif e.code in (F.EV_PORT_LOST, F.EV_PORT_BACK):
            drive.event(t, "link", "K-line cable lost" if e.code == F.EV_PORT_LOST else "K-line cable back", {})
        elif e.code == F.EV_ISO_SESSION:
            drive.event(t, "link", f"ISO 9141-2 session, key bytes {e.src:02X} {e.aux & 0xFF:02X}", {})
        elif e.code == F.EV_LOG_DROPPED:
            drive.event(t, "note", f"{e.value} log blocks were dropped: the card was too slow or missing", {})

    captures = [(c.t_trigger_us, c.t_start_us, c.t_end_us, c.reason, c.complete) for c in df.captures]
    done = {c[0] for c in captures}
    last = df.t_last_us
    for e in df.events:             # a stall whose window never closed: power went first
        if e.code == F.EV_STALL and not any(abs(e.t_us - t) < 1000 or (s <= e.t_us <= t_end)
                                            for t, s, t_end, _, _ in captures) and e.t_us not in done:
            captures.append((e.t_us, max(df.t0_us, e.t_us - 30_000_000), last, e.src, False))
    if df.kind == F.FK_CAPTURE and not captures:
        captures.append((df.trigger_t_us, df.t0_us, last, df.reason, False))
    for t_trigger, t_start, t_end, reason, complete in sorted(captures):
        tt = df.seconds(t_trigger)
        wall = datetime.fromtimestamp(epoch + tt) if epoch is not None else started
        name = F.STALL_REASONS.get(reason, str(reason))
        message = F.STALL_MESSAGES.get(reason, name)
        rule = "stall" if reason == 1 else "link_loss" if reason == 2 else "alarm"
        drive.capture("stall" if reason in (1, 2) else "capture", tt, max(0.0, df.seconds(t_start)),
                      df.seconds(min(t_end, max(last, t_trigger))), rule=rule, reason=name,
                      label="STALL (rpm 0)" if reason == 1 else "STALL (link lost)" if reason == 2 else "CAPTURE",
                      message=message, severity="critical", wall=wall, complete=complete,
                      fired=[{"rule": rule, "t": round(tt, 3)}],
                      meta={"trigger": rule, "trigger_reason": name, "trigger_message": message,
                            "trigger_time_s": f"{tt:.3f}", "window_complete": complete, "source": "handheld",
                            "stall_trigger": message, "stall_time_s": f"{tt:.3f}",
                            "stall_wall_time": wall.isoformat(timespec="milliseconds")})
    return len(captures)


def import_card(card: str | Path, database: str | Path | None = None, *, amap: AddressMap | None = None,
                force: bool = False, allow_other_map: bool = False) -> list[ImportResult]:
    """Import every drive on a card (the folder that holds DRIVES/ and
    CAPTURES/), oldest first. A capture file is imported only when the drive
    it was cut from is not there: otherwise the capture is already a window
    of that drive."""
    card = Path(card)
    results: list[ImportResult] = []
    drives = sorted((card / "DRIVES").glob("*.HHD")) + sorted((card / "DRIVES").glob("*.hhd"))
    if not drives and not (card / "CAPTURES").is_dir():
        drives = sorted(card.glob("*.HHD")) + sorted(card.glob("*.hhd"))       # the files copied loose
    have: set[tuple[str, int, int]] = set()
    for path in dict.fromkeys(drives):
        res = import_file(path, database, amap=amap, card=card, force=force, allow_other_map=allow_other_map)
        results.append(res)
        try:
            df = read_drive_file(path)
            if df.kind == F.FK_DRIVE and res.status != "failed":
                have.add((df.device, df.boot_no, df.file_no))
        except (OSError, ValueError):
            pass
    caps = sorted((card / "CAPTURES").glob("*.HHD")) + sorted((card / "CAPTURES").glob("*.hhd"))
    for path in dict.fromkeys(caps):
        try:
            df = read_drive_file(path)
        except (OSError, ValueError) as exc:
            results.append(ImportResult(path, "failed", str(exc)))
            continue
        if df.drive_no and (df.device, df.boot_no, df.drive_no) in have:
            results.append(ImportResult(path, "skipped", f"is a window of drive D{df.drive_no:06d}"))
            continue
        results.append(import_file(path, database, amap=amap, card=card, force=force,
                                   allow_other_map=allow_other_map))
    return results


def summary(results: list[ImportResult]) -> str:
    counts = {s: sum(1 for r in results if r.status == s) for s in ("imported", "skipped", "failed")}
    return json.dumps(counts)


__all__ = ["GPS_STREAM", "ImportResult", "already_imported", "find_map", "import_card", "import_file"]
_ = timezone  # (datetime.fromtimestamp gives local time, as the recorder does; files hold UTC)
