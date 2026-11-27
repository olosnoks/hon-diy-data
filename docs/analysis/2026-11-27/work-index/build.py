"""Per slow read: the byte, the fast channels interpolated to the read time, local rpm spread."""
import pickle, numpy as np, pandas as pd, sys
D = pickle.load(open('D.pkl','rb'))
OFF = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
rows = []
for name, r in D.items():
    if 'rpm' not in r: continue
    tr, vr = r['rpm']
    for key in ['b27','b3c']:
        if key not in r: continue
        t, v, lat = r[key]
        tt = t - lat + OFF
        d = dict(drive=name, key=key, t=t, val=v)
        for ch in ['rpm','map','tps','vss','ect','ign','inj','iacv','baro']:
            if ch in r:
                a, b = r[ch]
                d[ch] = np.interp(tt, a, b, left=np.nan, right=np.nan)
        # rpm spread and slope over +-0.15 s
        i0 = np.searchsorted(tr, tt - 0.15); i1 = np.searchsorted(tr, tt + 0.15)
        d['rmin'] = np.array([vr[a:b].min() if b > a else np.nan for a, b in zip(i0, i1)])
        d['rmax'] = np.array([vr[a:b].max() if b > a else np.nan for a, b in zip(i0, i1)])
        d['drpm'] = np.interp(tt + 0.2, tr, vr) - np.interp(tt - 0.2, tr, vr)
        d['gap'] = tt - tr[np.clip(np.searchsorted(tr, tt) - 1, 0, len(tr)-1)]
        d['prev'] = np.r_[np.nan, v[:-1]]
        d['dtprev'] = np.r_[np.nan, np.diff(t)]
        rows.append(pd.DataFrame(d))
S = pd.concat(rows, ignore_index=True)
S.to_pickle(f'S.pkl')
print(S.groupby('key').size())
print(S[S.key=='b3c'].groupby('val').rpm.describe().round(0))
