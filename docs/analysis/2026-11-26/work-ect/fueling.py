"""Effect of the ECT reading on fuel/ign/idle: (a) idle in drive 2137 (reading 82-86) vs 2158/2224 idle (69-74);
(b) drive 2251: ECT reading 74-79 (23:10:02-23:12:21) vs 64-69 at matched rpm/MAP bins; (c) launches and ECT state."""
import pickle, numpy as np, lib
from events import drive_events
D = pickle.load(open('drives.pkl', 'rb'))
def grid(d):
    t, rpm = d['rpm']
    g = dict(t=t, rpm=rpm, vss=lib.at(d['vss'], t), map=lib.at(d['map'], t), tps=lib.at(d['tps'], t),
             inj=lib.at(d['inj'], t), stft=lib.at(d['stft'], t), ltft=lib.at(d['ltft'], t), ign=lib.at(d['ign'], t),
             ect=lib.at(d['ect'], t), cl=lib.at(d['closed_loop'], t), iacv=lib.at(d['iacv'], t), eld=lib.at(d['eld'], t), iat=lib.at(d['iat'], t))
    if 'idle_target' in d: g['tgt'] = lib.at(d['idle_target'], t)
    return g
print('(a) warm idle (vss 0, tps<10, rpm 600-1000), per drive / ECT band')
for did in (35, 37, 38):
    g = grid(D[did])
    for lo, hi in ((80, 90), (64, 76)):
        m = (g['vss'] == 0) & (g['tps'] < 10) & (g['rpm'] > 600) & (g['rpm'] < 1000) & (g['ect'] >= lo) & (g['ect'] < hi) & (g['cl'] > 0)
        if m.sum() < 200: continue
        s = lambda k: np.median(g[k][m])
        print('  d%d ECT %d-%d n=%5d ect %.1f rpm %4.0f tgt %s map %.1f inj %.2f stft %+.1f ltft %+.1f total %+.1f ign %.1f iacv %.1f eld %.1f iat %.0f' % (
            did, lo, hi, m.sum(), s('ect'), s('rpm'), ('%.0f' % s('tgt')) if 'tgt' in g else '-', s('map'), s('inj'), s('stft'), s('ltft'), s('stft') + s('ltft'), s('ign'), s('iacv'), s('eld'), s('iat')))
print('(b) drive 2251 matched bins: ECT>=73 vs ECT 63-70, moving, closed loop')
g = grid(D[39])
hot = (g['ect'] >= 73); cold = (g['ect'] >= 63) & (g['ect'] < 70)
base = (g['vss'] > 5) & (g['inj'] > 0.3)
for lab, extra in (('closed loop', g['cl'] > 0), ('open loop', g['cl'] == 0)):
    rb = (g['rpm'] // 250).astype(int); mb = (g['map'] // 5).astype(int)
    res = {k: [] for k in ('inj', 'stft', 'ign')}; W = []
    for key in set(zip(rb[base & extra & hot], mb[base & extra & hot])):
        cell = base & extra & (rb == key[0]) & (mb == key[1])
        a, b = cell & hot, cell & cold
        if a.sum() >= 10 and b.sum() >= 10:
            W.append(min(a.sum(), b.sum()))
            for k in res: res[k].append((np.median(g[k][a]), np.median(g[k][b])))
    if not W: print('  ', lab, 'no common cells'); continue
    W = np.array(W, float)
    for k in res:
        x = np.array(res[k]); dif = x[:, 1] - x[:, 0]
        rel = (x[:, 1] / x[:, 0] - 1) * 100 if k == 'inj' else dif
        print('  %s %-4s cells %d (weight %d): ECT 63-70 minus ECT>=73 = %+.3f (%s)' % (lab, k, len(W), W.sum(), np.average(dif, weights=W), ('%+.1f %%' % np.average(rel, weights=W)) if k == 'inj' else ''))
print('(c) launches and ECT state')
for did in (38, 39):
    d = D[did]; ev, launches = drive_events(d)
    for tl in launches:
        e = lib.at(d['ect'], tl); tg = lib.at(d['idle_target'], tl - 2)
        ft, rpm = d['rpm']; w = (ft > tl - 4) & (ft < tl + 3)
        print('  d%d launch %s ECT %.1f idle_target %.0f rpm min (-4..+3 s) %d %s' % (did, lib.wall(tl), e, tg, rpm[w].min(), 'FALSE-COLD' if e < 55 else ''))
