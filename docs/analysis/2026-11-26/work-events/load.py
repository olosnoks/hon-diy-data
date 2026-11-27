"""Load raw K-line bytes per address from the copied hondalog.db (read-only)."""
import sqlite3, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'hondalog.db')

UNKNOWN = [0x03, 0x04, 0x05, 0x06, 0x07, 0x0D, 0x0E, 0x16] + list(range(0x1A, 0x20)) + list(range(0x29, 0x3C))
KNOWN = {0x02: 'vss', 0x08: 'f08', 0x09: 'f09', 0x0A: 'f0A', 0x0B: 'f0B', 0x0C: 'f0C', 0x0F: 'cl0F', 0x10: 'ect', 0x11: 'iat',
         0x12: 'map', 0x13: 'baro', 0x14: 'tps', 0x15: 'o2', 0x17: 'batt', 0x18: 'altfr', 0x19: 'eld',
         0x20: 'stft', 0x21: 'stft_lo', 0x22: 'ltft', 0x23: 'ltft_lo', 0x24: 'inj', 0x25: 'inj_lo', 0x26: 'ign', 0x27: 'x27', 0x28: 'iacv', 0x3C: 'knock'}


def con():
    return sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)


def drive_id(c, name):
    return c.execute('select id from drives where name=?', (name,)).fetchone()[0]


def load(name):
    """Returns (bytes_by_addr: {addr: (t, v)}, rpm: (t, rpm), link: (t, v), epoch)."""
    c = con()
    did = drive_id(c, name)
    epoch = c.execute('select coalesce(epoch_unix, started_at) from drives where id=?', (did,)).fetchone()[0]
    acc = {}
    rpm_t, rpm_v = [], []
    for addr, t, raw in c.execute('select addr, t, raw from ecu_reads where drive_id=? and addr is not null order by t', (did,)):
        raw = bytes(raw)
        for i, b in enumerate(raw):
            acc.setdefault(addr + i, ([], []))
            acc[addr + i][0].append(t)
            acc[addr + i][1].append(b)
        if addr == 0 and len(raw) >= 2:
            w = raw[0] << 8 | raw[1]
            rpm_t.append(t)
            rpm_v.append(w / 4.0)   # project map: rpm = x / 4 (16-bit big-endian)
    out = {a: (np.array(tt), np.array(vv, dtype=float)) for a, (tt, vv) in acc.items()}
    sid = c.execute("select id from streams where drive_id=? and name='ecu/link'", (did,)).fetchone()[0]
    lk = np.array(list(c.execute('select t, v0 from samples where drive_id=? and stream_id=? order by t', (did, sid))))
    return out, (np.array(rpm_t), np.array(rpm_v)), lk, epoch


def at(series, tq):
    """Last value at or before each tq (nan before first)."""
    t, v = series
    i = np.searchsorted(t, tq, side='right') - 1
    r = np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
    return r


def name(a):
    return KNOWN.get(a, 'x%02X' % a)


def ect_c(x):
    """Project map polynomial for ECT/IAT (deg C)."""
    k = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437]
    x = np.asarray(x, dtype=float)
    return sum(c * x ** i for i, c in enumerate(k))


def grid(B, R, step=1.0, t0=None, t1=None):
    """1 Hz table: every address (last value) + rpm (mean of the last second)."""
    t0 = t0 if t0 is not None else max(B[0x29][0][0], R[0][0])
    t1 = t1 if t1 is not None else min(B[0x29][0][-1], R[0][-1])
    tg = np.arange(t0, t1, step)
    G = {'t': tg}
    for a in B:
        G[a] = at(B[a], tg)
    rt, rv = R
    cs = np.concatenate([[0], np.cumsum(rv)])
    i1 = np.searchsorted(rt, tg, side='right')
    i0 = np.searchsorted(rt, tg - step, side='right')
    n = i1 - i0
    G['rpm'] = np.where(n > 0, (cs[i1] - cs[i0]) / np.maximum(n, 1), np.nan)
    G['rpm_min'] = np.array([rv[a:b].min() if b > a else np.nan for a, b in zip(i0, i1)])
    return G
