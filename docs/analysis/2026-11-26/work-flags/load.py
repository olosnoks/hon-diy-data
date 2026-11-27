"""Build per-read flag table (0x03-0x11) with context, from the DB copy. -> F.pkl"""
import sqlite3, numpy as np, pickle, datetime as dt
DB = r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/hondalog.db'
c = sqlite3.connect(DB)
rows = c.execute("select drive_id, drive, t, unix_time, addr, block, raw from ecu_reads where drive_id>1 and block like 'ecu/%' order by drive_id, t").fetchall()
def ect(x):
    k=[155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437]
    return sum(ki*x**i for i,ki in enumerate(k))
D = {}
for did, dname, t, ut, addr, block, raw in rows:
    d = D.setdefault(did, {'name': dname, 'ser': {}})
    for i, b in enumerate(raw):
        d['ser'].setdefault(addr+i, []).append((ut, b))
for did, d in D.items():
    for a in list(d['ser']):
        arr = np.array(d['ser'][a], dtype=float); d['ser'][a] = arr
pickle.dump(D, open(DB.replace('hondalog.db','F.pkl'),'wb'))
for did,d in D.items():
    s=d['ser']; n8=len(s.get(8,[]))
    print(did, d['name'], 'reads 0x08:', n8, 'addrs', min(s), max(s), '0x76' if 0x76 in s else '')
