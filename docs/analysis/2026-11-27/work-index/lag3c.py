"""Is 0x3C a quantised, lagged or windowed rpm? Score each candidate by the best monotone-threshold misclassification."""
import pickle, numpy as np, pandas as pd
D = pickle.load(open('D.pkl','rb'))
def ema(t, v, tau):
    out = np.empty_like(v); out[0] = v[0]
    for i in range(1, len(v)):
        a = 1 - np.exp(-(t[i]-t[i-1])/tau) if tau > 0 else 1
        out[i] = out[i-1] + a*(v[i]-out[i-1])
    return out
def score(x, y):
    # monotone step fit: choose thresholds between consecutive levels to minimise errors (greedy per boundary)
    err = 0; thr = {}
    levels = np.unique(y)
    for k in levels[:-1]:
        a = x[y <= k]; b = x[y > k]
        cand = np.unique(np.r_[a, b])
        cand = np.quantile(cand, np.linspace(0, 1, 400))
        e = [(a > c).sum() + (b <= c).sum() for c in cand]
        j = int(np.argmin(e)); thr[k] = cand[j]
    pred = np.searchsorted(np.array([thr[k] for k in levels[:-1]]), x, side='left')
    pred = levels[np.clip(pred, 0, len(levels)-1)]
    return (pred != y).mean(), thr
drives = ['drive_2026_11_26_2224','drive_2026_11_26_2251']
for mode in ['ema','win','delay']:
    for tau in [0, 0.1, 0.25, 0.5, 1, 2, 4] if mode != 'delay' else [-1, -0.5, -0.25, 0.25, 0.5, 1]:
        xs, ys = [], []
        for d in drives:
            tr, vr = D[d]['rpm']; t, v, lat = D[d]['b3c']
            if mode == 'ema':
                f = ema(tr, vr, tau); x = np.interp(t-lat, tr, f)
            elif mode == 'win':
                c = np.r_[0, np.cumsum(vr)]
                i1 = np.searchsorted(tr, t-lat); i0 = np.searchsorted(tr, t-lat-tau)
                x = np.where(i1 > i0, (c[i1]-c[i0])/np.maximum(i1-i0, 1), np.interp(t-lat, tr, vr))
            else:
                x = np.interp(t-lat+tau, tr, vr)
            m = x > 400
            xs.append(x[m]); ys.append(v[m])
        x = np.concatenate(xs); y = np.concatenate(ys)
        e, thr = score(x, y)
        print(mode, tau, 'err %.3f' % e, {int(k): int(v) for k, v in thr.items()})
