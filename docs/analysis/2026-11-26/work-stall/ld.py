"""Loader: raw K-line bytes per address (unix time) for every drive, from the local DB copy (read-only)."""
import sqlite3, os, pickle
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'hondalog.db')
def con():
    return sqlite3.connect('file:' + DB.replace(os.sep, '/') + '?mode=ro', uri=True)
K = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437]
def ect_c(x):
    x = np.asarray(x, float); return sum(k * x**i for i, k in enumerate(K))
def load_drive(c, did):
    acc = {}; rt, rv = [], []
    for addr, ut, raw in c.execute('select addr, unix_time, raw from ecu_reads where drive_id=? and addr is not null order by t', (did,)):
        raw = bytes(raw)
        for i, b in enumerate(raw):
            a = acc.setdefault(addr + i, ([], [])); a[0].append(ut); a[1].append(b)
        if addr == 0 and len(raw) >= 2:
            rt.append(ut); rv.append((raw[0] << 8 | raw[1]) / 4.0)
    out = {a: (np.array(t), np.array(v, float)) for a, (t, v) in acc.items()}
    out['rpm'] = (np.array(rt), np.array(rv))
    # link channel
    lk = list(c.execute("select r.unix_time, r.value from readings r where r.drive_id=? and r.channel='link' order by r.t", (did,)))
    out['link'] = (np.array([x[0] for x in lk]), np.array([x[1] for x in lk], float)) if lk else (np.array([]), np.array([]))
    return out
def load_obd(c, did):
    out = {}
    for ch in ('rpm', 'ect', 'stft', 'ltft', 'map', 'o2', 'link', 'batt_min_5s', 'vss'):
        r = list(c.execute("select unix_time, value from readings where drive_id=? and channel=? order by t", (did, ch)))
        out[ch] = (np.array([x[0] for x in r]), np.array([x[1] if x[1] is not None else np.nan for x in r], float))
    return out
def load_all(cache=True):
    p = os.path.join(HERE, 'cache.pkl')
    if cache and os.path.exists(p) and os.path.getmtime(p) > os.path.getmtime(DB):
        return pickle.load(open(p, 'rb'))
    c = con(); D = {}
    for did, name, st, en, status, er in c.execute("select id,name,started_at,ended_at,status,end_reason from drives where id>=2 order by id").fetchall():
        n = c.execute("select count(*) from ecu_reads where drive_id=? and addr is not null", (did,)).fetchone()[0]
        d = load_drive(c, did) if n else load_obd(c, did)
        D[did] = dict(name=name, start=st, end=en, status=status, end_reason=er, honda=bool(n), s=d)
    pickle.dump(D, open(p, 'wb')); return D
def at(series, tq):
    t, v = series
    if len(t) == 0: return np.full(np.shape(tq), np.nan)
    i = np.searchsorted(t, tq, side='right') - 1
    return np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
