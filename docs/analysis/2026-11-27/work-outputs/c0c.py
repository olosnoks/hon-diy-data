from lib import *
S = slow_all().sort_values(['did','t']).reset_index(drop=True)
S = S[S.rpm.notna() & (S.rpm > 400)].copy(); g = S.groupby('did')
for c in ['eld','altfr','batt','iacv','t']: S['d'+c] = g[c].diff()
S['nalt'] = g.altfr.shift(-1) - g.altfr.shift(1); S['nbatt'] = g.batt.shift(-1)
st = S[(S.dt < 1.6) & (S.deld >= 10) & (S.vss == 0)]
for k, gg in st.groupby('c0'):
    print('c0 =', k, 'n', len(gg), 'd eld %.1f A' % gg.deld.mean(), 'd altfr %.1f %%' % gg.daltfr.mean(), 'd altfr per A %.2f' % (gg.daltfr / gg.deld).mean(),
          'd batt %.2f V (median %.2f)' % (gg.dbatt.mean(), gg.dbatt.median()), 'batt %.2f' % gg.batt.mean(), 'd iacv %.1f' % gg.diacv.mean())
