"""First ECT/IAT raw bytes of each drive (start-up temperatures), read only.
Compares them with 0x8F = 142 (ECT poly -> 27.4 C)."""
import sqlite3
import sys

c = (155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437)
poly = lambda x: sum(k * x ** i for i, k in enumerate(c))
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for did, name in con.execute("select id, name from drives where id >= 6 order by id"):
    out = []
    for sid, addr, ln in con.execute("select id, addr, raw_len from streams where drive_id=? and addr is not null "
                                     "and addr <= 16 and addr + raw_len > 16", (did,)):
        r = con.execute("select t, raw from samples where drive_id=? and stream_id=? and raw is not null "
                        "order by t limit 1", (did, sid)).fetchone()
        if r and r[1] and len(r[1]) > 16 - addr:
            raw = r[1]
            ect = raw[16 - addr]
            iat = raw[17 - addr] if len(raw) > 17 - addr else None
            out.append(f"t={r[0]:.1f} ect raw {ect} ({poly(ect):.0f} C) iat raw {iat}"
                       + (f" ({poly(iat):.0f} C)" if iat is not None else ""))
    print(did, name, out[:1])
