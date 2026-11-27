from lib import *
import numpy as np
W = pd.read_csv('../../2026-11-26/work-sweep/car-214932-wide.csv')
W.columns = [c if not c.startswith('b') else 'b' + c[1:].lower() for c in W.columns]
W['t'] = pd.to_datetime(W.wall).astype('int64') / 1e9 - LOCALOFF
w0 = pd.to_datetime(W.wall); W = decode(W); W['wall'] = w0
W['a4b0'] = bit(W.ba4, 0)
W['hh'] = W.wall.dt.strftime('%H:%M:%S')
W['err'] = W.rpm - W.tgt
cols = ['hh','a4b0','ba4','rpm','tgt','err','kpa','iacv','iac_cmd','x2a','x2f','ect_raw','iat','eld','altfr','batt','stft','ltft','o2','o2_2','ign','inj','b27','b3c','b86','cl','fs','b0b','b0c','b08']
print(W.loc[(W.hh >= '21:50:30') & (W.hh <= '21:51:50'), cols].to_string(index=False))
# bytes that differ between a4b0=1 and 0
on = W[W.a4b0 == 1]; off = W[W.a4b0 == 0]
print('\nbytes whose value ranges barely overlap between states:')
for c in [c for c in W.columns if c.startswith('b') and len(c) == 3]:
    a, b = on[c], off[c]
    if a.nunique() + b.nunique() > 2:
        d = a.median() - b.median()
        if abs(d) > 0 and (a.min() > b.quantile(0.9) or a.max() < b.quantile(0.1)):
            print(c, 'on median', a.median(), 'off median', b.median(), 'on range', a.min(), a.max())
# edges: byte changes at the exact on/off sweeps
for i in W.index[W.a4b0.diff().fillna(0) != 0]:
    d = {c: (W.loc[i-1, c], W.loc[i, c]) for c in W.columns if c.startswith('b') and len(c) == 3 and W.loc[i-1, c] != W.loc[i, c]}
    print('edge', W.hh[i], 'a4b0 ->', W.a4b0[i], {k: v for k, v in d.items() if k not in ('b00','b01','b15','b20','b26','b24','b25')})
print('\nrpm when a4b0=1:', on.rpm.min(), on.rpm.max(), ' target', on.tgt.min(), on.tgt.max())
print('reads with rpm in 1010-1070 and a4b0=0:', len(off[(off.rpm >= 1010) & (off.rpm <= 1070)]))
print('x2f range overall', W.x2f.min(), W.x2f.max(), 'x2a', W.x2a.unique())
print('ect_raw overall', sorted(W.ect_raw.unique()))
print()
for _, r in W.iterrows():
    print(r.hh, int(r.ect_raw), int(r.a4b0), int(r.rpm), int(r.tgt), int(r.iac_cmd), round(r.eld,1), int(r.b27), int(r.x2f), end=' | ')
