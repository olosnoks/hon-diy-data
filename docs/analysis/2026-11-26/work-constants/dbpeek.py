"""Read-only look at a DB copy: drives (with ecu id) and any stream reading 0x76-0xFF."""
import sqlite3
import sys

db = sys.argv[1]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cols = [r[1] for r in con.execute("pragma table_info(drives)")]
print(cols)
want = [c for c in cols if c in ("id", "name", "started_at", "ecu_id", "status", "end_reason")]
for r in con.execute(f"select {', '.join(want)} from drives order by id"):
    row = dict(zip(want, r))
    if isinstance(row.get("ecu_id"), (bytes, bytearray)):
        row["ecu_id"] = row["ecu_id"].hex(" ")
    print(row)
print("streams with addr >= 0x76:")
for r in con.execute("select drive_id, id, name, addr, raw_len from streams where addr >= 118 order by drive_id"):
    print(r)
