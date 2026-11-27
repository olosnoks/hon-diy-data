"""Build per-address raw series in wall-clock seconds from the copied DB (drives 31-37) + the 21:49 sweep."""
import sqlite3, os, csv, datetime, pickle
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'hondalog.db')
SWEEP = os.path.join(HERE, '..', 'work-sweep', 'car-214932-wide.csv')

def build(min_id=31):
    c = sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)
    acc = {}
    drives = []
    for did, name, ep, st, en in c.execute('select id,name,epoch_unix,started_at,ended_at from drives where id>=? order by id', (min_id,)).fetchall():
        drives.append((did, name, st, en))
        for addr, t, raw in c.execute('select addr,t,raw from ecu_reads where drive_id=? and addr is not null order by t', (did,)):
            raw = bytes(raw)
            tw = ep + t
            if addr == 0 and len(raw) >= 2:
                acc.setdefault('rpm', []).append((tw, (raw[0] << 8 | raw[1]) / 4, did))
            for i, b in enumerate(raw):
                acc.setdefault(addr + i, []).append((tw, b, did))
        if drives[-1][3] is None:
            drives[-1] = (did, name, st, max(a[-1][0] for a in acc.values()))
    # sweep
    with open(SWEEP) as f:
        for row in csv.DictReader(f):
            tw = datetime.datetime.fromisoformat(row['wall']).timestamp()
            for a in range(256):
                acc.setdefault(a, []).append((tw, int(row['b%02X' % a]), -1))
            acc.setdefault('rpm', []).append((tw, (int(row['b00']) << 8 | int(row['b01'])) / 4, -1))
    out = {}
    for k, v in acc.items():
        v.sort()
        a = np.array(v)
        out[k] = (a[:, 0], a[:, 1], a[:, 2].astype(int))
    return out, drives

if __name__ == '__main__':
    S, drives = build()
    pickle.dump((S, drives), open(os.path.join(HERE, 'series.pkl'), 'wb'))
    for d in drives: print(d, datetime.datetime.fromtimestamp(d[2]), datetime.datetime.fromtimestamp(d[3]))
    for a in (0x2A, 0x2F, 0x76, 0x77, 0x22, 0x86):
        t, v, _ = S[a]; print(hex(a), len(t), datetime.datetime.fromtimestamp(t[0]), datetime.datetime.fromtimestamp(t[-1]))
