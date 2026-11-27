"""Build a 1 Hz table for every drive 6-39 (fast channels averaged over the second before each slow read)."""
import numpy as np, lib, pickle
FAST = ['rpm', 'vss', 'map', 'tps', 'stft', 'ltft', 'inj', 'ign', 'o2']
SLOW = ['iacv', 'iac_cmd', 'eld', 'idle_target', 'batt', 'alt_fr', 'ac_clutch', 'ac_sw', 'brake', 'iat', 'closed_loop', 'raw_2a', 'fuel_status', 'o2_2']
c = lib.con()
rows = []
for did in range(6, 40):
    d = lib.load(did, set(FAST + SLOW), c)
    if 'ect_raw' not in d or 'rpm' not in d:
        print('skip', did); continue
    te, er = d['ect_raw']
    # 1 Hz grid = ect read times, deduplicate
    keep = np.r_[True, np.diff(te) > 0.3]
    te, er = te[keep], er[keep]
    out = {'t': te, 'drive': np.full(len(te), did), 'ect_raw': er, 'ect': lib.ect_poly(er)}
    for n in FAST:
        if n not in d:
            out[n] = np.full(len(te), np.nan); continue
        ts, v = d[n]
        cs = np.r_[0, np.cumsum(v)]
        lo = np.searchsorted(ts, te - 1.0); hi = np.searchsorted(ts, te, side='right')
        cnt = hi - lo
        with np.errstate(invalid='ignore', divide='ignore'):
            out[n] = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), np.nan)
        if n == 'rpm':
            out['rpm_n'] = cnt
            mn = np.full(len(te), np.nan)
            for i in np.nonzero(cnt)[0]: mn[i] = v[lo[i]:hi[i]].min()
            out['rpm_min'] = mn
    for n in SLOW:
        if n in d:
            ts, v = d[n]
            i = np.searchsorted(ts, te, side='right') - 1
            ok = (i >= 0) & (te - ts[np.clip(i, 0, None)] < 3)
            out[n] = np.where(ok, v[np.clip(i, 0, None)], np.nan)
        else:
            out[n] = np.full(len(te), np.nan)
    for n in ('iacv_raw', 'iac_cmd_raw', 'x2a', 'b08'):
        if n in d:
            out[n] = lib.at(d[n], te).astype(float)
        else:
            out[n] = np.full(len(te), np.nan)
    rows.append(out)
    print(did, len(te), 'ect span', lib.wall(te[0]), lib.wall(te[-1]))
T = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
o = np.argsort(T['t'], kind='stable')
T = {k: v[o] for k, v in T.items()}
pickle.dump(T, open('t1hz.pkl', 'wb'))
print('rows', len(T['t']))
