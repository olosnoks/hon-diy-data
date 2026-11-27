"""A person's notes and verdicts on the events of a drive, kept in the drive's
own database without a schema change: one row per event in the existing
``events`` table, ``kind = 'event_note'``, the event's key in ``text`` and
``{"note": ..., "verdict": ..., "kind": ..., "t": ...}`` in ``data``.

The reader's markers only take the kinds marker / trigger / alarm, so these
rows never show up as markers; deleting the drive deletes them with it.

    save(ref.db, ref.drive, "near_stall@3210.4", note="clutch bit early", kind="near_stall", t=3210.4)
    load(ref.db, ref.drive)          # {"near_stall@3210.4": {"note": "...", "verdict": ""}}
    apply(events, load(...))         # copies note / verdict_override onto DriveEvent objects
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

from ..data import db as _db

KIND = "event_note"


def _drive_id(conn, drive: str) -> int:
    row = conn.execute("SELECT id FROM drives WHERE name=?", (drive,)).fetchone()
    if row is None:
        raise ValueError(f"no drive {drive!r}")
    return int(row[0])


def load(database: str | Path, drive: str) -> dict[str, dict]:
    """Every note and verdict of one drive, by event key. {} when there are none
    or the database cannot be read."""
    try:
        conn = _db.connect(Path(database))
    except (OSError, ValueError):
        return {}
    try:
        try:
            did = _drive_id(conn, drive)
        except ValueError:
            return {}
        out: dict[str, dict] = {}
        for key, data in conn.execute("SELECT text, data FROM events WHERE drive_id=? AND kind=? ORDER BY id",
                                      (did, KIND)):
            try:
                value = json.loads(data or "{}")
            except ValueError:
                value = {}
            out[str(key)] = {"note": str(value.get("note", "") or ""), "verdict": str(value.get("verdict", "") or ""),
                             "kind": value.get("kind", ""), "t": value.get("t")}
        return out
    finally:
        conn.close()


def save(database: str | Path, drive: str, key: str, *, note: str | None = None, verdict: str | None = None,
         kind: str = "", t: float | None = None) -> dict:
    """Set the note and/or the verdict of one event (None leaves it as it is,
    "" clears it). A row with neither left is removed. Returns what is stored."""
    conn = _db.connect(Path(database), write=True)
    try:
        did = _drive_id(conn, drive)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT id, data FROM events WHERE drive_id=? AND kind=? AND text=?",
                               (did, KIND, key)).fetchone()
            value = json.loads(row[1] or "{}") if row is not None else {}
            if note is not None:
                value["note"] = note
            if verdict is not None:
                value["verdict"] = verdict
            value.setdefault("kind", kind)
            if t is not None:
                value["t"] = float(t)
            value["updated_unix"] = time.time()
            empty = not value.get("note") and not value.get("verdict")
            if row is not None:
                if empty:
                    conn.execute("DELETE FROM events WHERE id=?", (row[0],))
                else:
                    conn.execute("UPDATE events SET data=? WHERE id=?", (json.dumps(value), row[0]))
            elif not empty:
                conn.execute("INSERT INTO events(drive_id, t, kind, text, data) VALUES (?, ?, ?, ?, ?)",
                             (did, float(t or 0.0), KIND, key, json.dumps(value)))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return {"note": value.get("note", ""), "verdict": value.get("verdict", "")}
    finally:
        conn.close()


def apply(events: Iterable, notes: dict[str, dict]) -> None:
    """Copy the stored notes and verdicts onto events (by ``event.key``)."""
    for e in events:
        stored = notes.get(e.key)
        if stored:
            e.note = stored.get("note", "")
            e.verdict_override = stored.get("verdict", "")
