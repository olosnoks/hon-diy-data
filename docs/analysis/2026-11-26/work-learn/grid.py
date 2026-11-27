import pickle, datetime, numpy as np
S, drives = pickle.load(open('series.pkl','rb'))
def ect_c(x):
    k = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437]
    x = np.asarray(x, dtype=float); return sum(c * x ** i for i, c in enumerate(k))
def at(a, tq, maxage=5):
    t, v, _ = S[a]; i = np.searchsorted(t, tq, side='right') - 1
    ok = (i >= 0) & (tq - t[np.clip(i,0,None)] <= maxage)
    return np.where(ok, v[np.clip(i, 0, None)], np.nan)
def mean1(a, tg, step=1.0):
    t, v, _ = S[a]; cs = np.concatenate([[0], np.cumsum(v)])
    i1 = np.searchsorted(t, tg, side='right'); i0 = np.searchsorted(t, tg - step, side='right'); n = i1 - i0
    return np.where(n > 0, (cs[i1] - cs[i0]) / np.maximum(n, 1), np.nan)
t0 = S[0x2A][0][0]; t1 = S[0x2A][0][-1]
tg = np.arange(np.floor(t0), t1, 1.0)
G = {'t': tg}
for a in range(0x00, 0x3D): 
    if a in S: G[a] = at(a, tg)
for a in (0x76, 0x77, 0x9A, 0x9C, 0xA0): G[a] = at(a, tg)
G['rpm'] = mean1('rpm', tg)
G['stft'] = (mean1(0x20, tg)/128-1)*100
G['ltft'] = (at(0x22, tg)/128-1)*100
G['ect'] = ect_c(G[0x10]); G['iat'] = ect_c(G[0x11])
G['batt'] = G[0x17]/10.45; G['eld'] = 77.06 - G[0x19]/2.5371
G['tgt'] = (G[0x76]*256 + G[0x77])/4
G['map'] = mean1(0x12, tg)*0.716-5
G['o2'] = mean1(0x15, tg)/51.3
d = np.full(len(tg), -9)
for did, name, st, en in drives: d[(tg >= st-1) & (tg <= en+1)] = did
d[(tg >= S[0x86][0][0]-1) & (tg <= S[0x86][0][-1]+1)] = -1
G['drive'] = d
pickle.dump(G, open('grid.pkl', 'wb'))
print(len(tg))
