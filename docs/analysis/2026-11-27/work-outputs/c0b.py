from lib import *
S = slow_all().sort_values(['did','t']).reset_index(drop=True)
S = S[S.rpm.notna() & (S.rpm > 400)].copy()
g = S.groupby('did')
S['dt'] = g.t.diff(); S['deld'] = g.eld.diff(); S['diacv'] = g.iacv.diff(); S['eld_next'] = g.eld.shift(-1); S['iacv_next'] = g.iacv.shift(-1)
S['dbatt'] = g.batt.diff()
S['peak'] = S.eld - S.eld_next   # how much it falls back in the next read
st = S[(S.dt < 1.6) & (S.deld >= 10)].copy()
st['kick'] = st.diacv
print(st[['t','did','deld','peak','diacv','dbatt','c0','vss']].assign(t=lambda d: d.t.map(hms)).sort_values('diacv').to_string())
import numpy as np
print('\nIACV jump >= +12 in one read (engine running):')
k = S[(S.dt < 1.6) & (S.diacv >= 12)]
print(len(k), 'c0=0 among them', int((k.c0 == 0).sum()))
print(k[['t','did','rpm','vss','deld','peak','diacv','iacv','iacv_next','dbatt','c0','ac','brake']].assign(t=lambda d: d.t.map(hms)).to_string())
