import pickle, numpy as np, lib
from fueling import grid
D = pickle.load(open('drives.pkl', 'rb'))
print('idle target vs ECT raw, split by IAT (all 11-26 drives with target)')
rows = []
for did in (37, 38, 39):
    d = D[did]; tt, tg = d['idle_target']
    rows.append(np.c_[tg, lib.at(d['ect_raw'], tt - 1.5), lib.at(d['iat'], tt), lib.at(d['vss'], tt)])
A = np.vstack(rows)
for r in range(42, 68):
    m = (A[:, 1] == r)
    lo = m & (A[:, 2] < 32); hi = m & (A[:, 2] > 45)
    if lo.sum() > 20 or hi.sum() > 20:
        print('  raw %d (%.1f C): IAT<32 n=%5d target %s | IAT>45 n=%5d target %s' % (r, lib.ect_poly(r), lo.sum(), ('%.0f' % np.median(A[lo, 0])) if lo.sum() else '-', hi.sum(), ('%.0f' % np.median(A[hi, 0])) if hi.sum() else '-'))
print('drive 2251 matched rpm/MAP bins: IAT<24 vs IAT>32 (closed loop, moving)')
g = grid(D[39]); base = (g['vss'] > 5) & (g['cl'] > 0) & (g['inj'] > 0.3) & (g['ect'] < 72)
cold = g['iat'] < 24; warm = g['iat'] > 32
rb = (g['rpm'] // 250).astype(int); mb = (g['map'] // 5).astype(int)
res = {k: [] for k in ('inj', 'stft', 'ign')}; W = []
for key in set(zip(rb[base & cold], mb[base & cold])):
    cell = base & (rb == key[0]) & (mb == key[1]); a, b = cell & cold, cell & warm
    if a.sum() >= 15 and b.sum() >= 15:
        W.append(min(a.sum(), b.sum()))
        for k in res: res[k].append((np.median(g[k][a]), np.median(g[k][b])))
W = np.array(W, float)
for k in res:
    x = np.array(res[k]); dif = x[:, 0] - x[:, 1]
    print('  %-4s cells %d: IAT<24 minus IAT>32 = %+.3f %s' % (k, len(W), np.average(dif, weights=W), ('(%+.1f %%)' % np.average((x[:, 0] / x[:, 1] - 1) * 100, weights=W)) if k == 'inj' else ''))
for did in (37, 38, 39):
    t, v = D[did]['iat']; print('d%d IAT start %.1f end %.1f min %.1f max %.1f' % (did, v[0], v[-1], v.min(), v.max()))
# IAT time constant in drive 39
t, v = D[39]['iat']; t0 = t[0]
for target in (45, 40, 35, 30, 25, 22):
    i = np.argmax(v <= target); print('  d39 IAT reached %d C after %.0f s (%s)' % (target, t[i] - t0, lib.wall(t[i])))
