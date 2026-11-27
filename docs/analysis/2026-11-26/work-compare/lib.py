"""Loader for the read-only DB copy in this folder. Returns per-channel (unix_t, value) arrays."""
import os, sqlite3, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'hondalog.db')


def con():
    return sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)


def ect_poly(x):
    x = np.asarray(x, float)
    return (155.04149 - 3.0414878 * x + 0.03952185 * x**2 - 0.00029383913 * x**3
            + 0.0000010792568 * x**4 - 0.0000000015618437 * x**5)


def drives(c=None):
    c = c or con()
    return c.execute('select id,name,epoch_unix,started_at,t_first,t_last,duration_s from drives order by id').fetchall()


def load(did, names=None, c=None):
    """dict name -> (t_unix, v). Also 'ect_raw' (byte 0x10), 'iat_raw' (0x11), 'b08' (0x08) from raw blobs."""
    c = c or con()
    ep = c.execute('select coalesce(epoch_unix, started_at) from drives where id=?', (did,)).fetchone()[0]
    out = {}
    for sid, part, slot, name in c.execute('select stream_id,part,slot,name from channels where drive_id=?', (did,)).fetchall():
        if names and name not in names:
            continue
        rows = c.execute(f'select t, v{slot} from samples where drive_id=? and stream_id=? and part=? and v{slot} is not null order by t',
                         (did, sid, part)).fetchall()
        if rows:
            a = np.array(rows, float)
            out[name] = (a[:, 0] + ep, a[:, 1])
    # raw bytes
    want = {0x10: 'ect_raw', 0x11: 'iat_raw', 0x08: 'b08', 0x17: 'batt_raw', 0x18: 'altfr_raw', 0x19: 'eld_raw',
            0x2F: 'x2f', 0x29: 'iac_cmd_raw', 0x28: 'iacv_raw', 0x2A: 'x2a', 0x20: 'stft_raw', 0x22: 'ltft_raw', 0x0F: 'b0f'}
    acc = {}
    for sid, addr, rl in c.execute('select id,addr,raw_len from streams where drive_id=? and addr is not null', (did,)).fetchall():
        hit = [(a, n) for a, n in want.items() if addr <= a < addr + rl]
        if not hit:
            continue
        for t, raw in c.execute('select t, raw from samples where drive_id=? and stream_id=? and part=0 and raw is not null order by t', (did, sid)):
            raw = bytes(raw)
            for a, n in hit:
                if a - addr < len(raw):
                    acc.setdefault(n, []).append((t + ep, raw[a - addr]))
    for n, v in acc.items():
        v.sort()
        a = np.array(v, float)
        out[n] = (a[:, 0], a[:, 1])
    return out


def wall(t):
    return datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')


def at(series, t):
    """value of a step series (last sample <= t)"""
    ts, v = series
    i = np.searchsorted(ts, t, side='right') - 1
    i = np.clip(i, 0, len(ts) - 1)
    return v[i]
