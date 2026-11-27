"""1 Hz grid of every raw byte for drives 6-39 from ecu_reads blobs only (read-only DB copy here).
Grid = read times of the block carrying 0x10 (ECT). Fast bytes averaged over the second before; others last value (<3 s old).
Also writes the fast rpm series per drive."""
import sqlite3, numpy as np, pickle, os
HERE = os.path.dirname(os.path.abspath(__file__))
c = sqlite3.connect('file:' + os.path.join(HERE, 'hondalog.db').replace(os.sep, '/') + '?mode=ro', uri=True)
ADDRS = list(range(0x00, 0x40)) + [0x76, 0x77] + list(range(0x9A, 0xA1))
FASTAVG = {0x12, 0x14, 0x15, 0x20, 0x26}
def poly(x):
    x = np.asarray(x, float)
    return (155.04149 - 3.0414878 * x + 0.03952185 * x**2 - 0.00029383913 * x**3 + 0.0000010792568 * x**4 - 0.0000000015618437 * x**5)
rows = []; fast = {}
for did in range(6, 40):
    ep = c.execute('select coalesce(epoch_unix, started_at) from drives where id=?', (did,)).fetchone()[0]
    acc = {a: [] for a in ADDRS}
    for sid, addr, rl in c.execute('select id,addr,raw_len from streams where drive_id=? and addr is not null', (did,)).fetchall():
        for t, raw in c.execute('select t, raw from samples where drive_id=? and stream_id=? and part=0 and raw is not null', (did, sid)):
            raw = bytes(raw); tu = t + ep
            if len(raw) != rl: continue
            for k, b in enumerate(raw):
                a = addr + k
                if a in acc: acc[a].append((tu, b))
    S = {}
    for a, v in acc.items():
        if v:
            v.sort(); arr = np.array(v, float); S[a] = (arr[:, 0], arr[:, 1])
    if 0x10 not in S or 0x00 not in S: print('skip', did); continue
    # rpm fast (bytes 0x00/0x01 from the same read)
    rr = []
    for sid, addr, rl in c.execute('select id,addr,raw_len from streams where drive_id=? and addr=0', (did,)).fetchall():
        for t, raw in c.execute('select t, raw from samples where drive_id=? and stream_id=? and part=0 and raw is not null', (did, sid)):
            raw = bytes(raw)
            if len(raw) >= 3: rr.append((t + ep, (raw[0] << 8 | raw[1]) / 4, raw[2]))
    rr.sort(); fast[did] = np.array(rr, float)
    te, _ = S[0x10]
    keep = np.r_[True, np.diff(te) > 0.3]; te = te[keep]
    out = {'t': te, 'drive': np.full(len(te), did)}
    ft = fast[did][:, 0]; frpm = fast[did][:, 1]
    cs = np.r_[0, np.cumsum(frpm)]; lo = np.searchsorted(ft, te - 1.0); hi = np.searchsorted(ft, te, side='right')
    cnt = hi - lo
    out['rpm'] = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), np.nan)
    out['vss'] = np.array([fast[did][l:h, 2].max() if h > l else np.nan for l, h in zip(lo, hi)])
    for a in ADDRS:
        name = 'b%02x' % a
        if a not in S: out[name] = np.full(len(te), np.nan); continue
        ts, v = S[a]
        if a in FASTAVG:
            cs = np.r_[0, np.cumsum(v)]; lo = np.searchsorted(ts, te - 1.0); hi = np.searchsorted(ts, te, side='right'); n = hi - lo
            m = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
        else:
            i = np.searchsorted(ts, te, side='right') - 1
            ok = (i >= 0) & (te - ts[np.clip(i, 0, None)] < 3)
            m = np.where(ok, v[np.clip(i, 0, None)], np.nan)
        out[name] = m
    rows.append(out); print(did, len(te))
T = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
T['ect'] = poly(T['b10']); T['iat'] = poly(T['b11']); T['map'] = T['b12'] * 0.716 - 5; T['tps'] = T['b14'] / 2.55
T['o2'] = T['b15'] / 51.3; T['batt'] = T['b17'] / 10.45; T['alt_fr'] = T['b18'] / 2.55; T['eld'] = 77.06 - T['b19'] / 2.5371
T['stft'] = (T['b20'] / 128 - 1) * 100; T['ltft'] = (T['b22'] / 128 - 1) * 100; T['inj'] = (T['b24'] * 256 + T['b25']) / 250
T['iacv'] = T['b28'] / 2.55; T['iac_cmd'] = T['b29']; T['x2a'] = T['b2a']; T['x2f'] = T['b2f']
T['target'] = (T['b76'] * 256 + T['b77']) / 4; T['o2_2'] = T['ba0'] / 51.3
pickle.dump(T, open('T.pkl', 'wb')); pickle.dump(fast, open('fast.pkl', 'wb'))
print('rows', len(T['t']))
