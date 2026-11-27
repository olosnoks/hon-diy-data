"""Replay the stall rules of a checkout over the recorded rpm + link flags.

    python replay_rules.py <checkout>
Prints, per drive, what fired and (new rules) what review revoked."""
import sys, sqlite3
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hondalog.triggers import TriggerEngine, stall_rules      # noqa: E402
DB = Path(__file__).with_name("hondalog.db")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
drives = [r[0] for r in c.execute("SELECT DISTINCT drive_id FROM captures")] + [34]
for did in sorted(set(drives)):
    name, epoch = c.execute("SELECT name, coalesce(epoch_unix, started_at) FROM drives WHERE id=?", (did,)).fetchone()
    rows = [(t, "rpm", v) for t, v in c.execute(
        "SELECT t, value FROM readings WHERE drive_id=? AND channel='rpm' AND value IS NOT NULL", (did,))]
    rows += [(t, "link", v) for t, v in c.execute(
        "SELECT s.t, s.v0 FROM samples s JOIN streams st ON st.drive_id=s.drive_id AND st.id=s.stream_id "
        "WHERE s.drive_id=? AND st.name='ecu/link'", (did,))]
    if not any(k == "link" for _, k, _ in rows):          # sweep-mode logs: link column in the ecu stream
        rows += [(t, "link", v) for t, v in c.execute(
            "SELECT t, value FROM readings WHERE drive_id=? AND channel='link'", (did,))]
    rows.sort()
    eng = TriggerEngine(stall_rules())
    out = []
    eng.on_revoked = lambda f, t: out.append(f"revoked {f.rule.name}@{f.t:.1f} at {t:.1f}")
    for t, k, v in rows:
        if k == "rpm":
            fired, _ = eng.feed(t, {"rpm": v, "link": 1})
        elif v == 0:
            fired, _ = eng.feed(t, {"rpm": None, "link": 0})
        else:
            fired, _ = eng.feed(t, {"link": 1})
        out += [f"FIRED {f.rule.name}@{f.t:.1f}" for f in fired]
    eng.finish()
    print(f"{name}: {len(rows)} rows; " + ("; ".join(out) or "nothing"))
