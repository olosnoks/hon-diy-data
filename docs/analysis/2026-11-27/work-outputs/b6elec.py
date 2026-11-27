from lib import *
S = slow_all([38, 39]).sort_values('t').reset_index(drop=True)
S = S[S.rpm > 400]
H = S[(S.rpm > 3000) & (S.rpm < 5000) & (S.kpa > 70)].copy()
print('high-load reads', len(H), 'b6=0:', int((H.b6==0).sum()))
for c in ['eld','b19','altfr','batt','rpm','kpa','tps','stft','o2','o2_2','inj','iat','ect']:
    a = H[H.b6==1][c]; b = H[H.b6==0][c]
    print(f'{c:6s} b6=1 med {a.median():7.2f} (n {a.notna().sum()})   b6=0 med {b.median():7.2f} (n {b.notna().sum()})')
# least squares eld ~ 1 + rpm + map + b6, and altfr, batt
import numpy as np
for y in ['eld','altfr','batt']:
    d = H.dropna(subset=[y,'rpm','kpa','b6'])
    X = np.c_[np.ones(len(d)), d.rpm/1000, d.kpa/10, d.b6]
    beta, res, *_ = np.linalg.lstsq(X, d[y], rcond=None)
    r = d[y] - X @ beta; se = np.sqrt((r**2).sum()/(len(d)-4) * np.linalg.inv(X.T@X).diagonal())
    print(y, 'coef [1, rpm/1000, map/10, b6]', np.round(beta,3), 'se', np.round(se,3))
# edge pair deltas vs control pairs
S2 = S.copy(); S2['dt'] = S2.t.diff()
for c in ['eld','altfr','batt','rpm']: S2['d'+c] = S2[c].diff()
S2['db6'] = S2.b6.diff()
ok = S2.dt < 1.6
off = S2[ok & (S2.db6 == -1)]; on = S2[ok & (S2.db6 == 1)]
ctrl = S2[ok & (S2.db6 == 0) & (S2.rpm > 2800) & (S2.kpa > 70) & (S2.b6 == 1)]
for nm, g in [('off edges', off), ('on edges', on), ('control (b6 stays 1, >2800 rpm, MAP>70)', ctrl)]:
    print(nm, len(g), 'd eld med %.2f A  mean %.2f' % (g.deld.median(), g.deld.mean()), 'd altfr %.2f' % g.daltfr.median(), 'd batt %.3f' % g.dbatt.median(), 'd rpm %.0f' % g.drpm.median())
print(off[['t','rpm','kpa','eld','deld','daltfr','dbatt','drpm']].assign(t=lambda d: d.t.map(hms)).to_string())
print(on[['t','rpm','kpa','tps','eld','deld','daltfr','dbatt','drpm']].assign(t=lambda d: d.t.map(hms)).to_string())
