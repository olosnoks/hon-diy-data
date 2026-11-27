"""Characterise the bytes/bits that came alive while driving."""
import pickle, sys, numpy as np, pandas as pd
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 400)
S, F = pickle.load(open('SF.pkl', 'rb'))

def gear(rv, vss):
    g = np.full(len(rv), '-', dtype=object)
    ok = vss >= 8
    for name, lo, hi in [('5', 25, 31), ('4', 32.5, 40), ('3', 45, 56), ('2', 68, 88), ('1', 115, 155)]:
        g[ok & (rv >= lo) & (rv < hi)] = name
    return g

for df in (S, F):
    df['rv'] = df.rpm / df.vss.replace(0, np.nan)
    df['gear'] = gear(df.rv.values, df.vss.values)
    df['cut'] = (df.inj == 0) & (df.rpm > 900)
S['brake'] = (S.b08.astype(int) // 8) % 2
S['psp'] = (S.b08.astype(int) // 4) % 2
S['cl'] = S.b0f.astype(int) % 2
pickle.dump((S, F), open('SF2.pkl', 'wb'))
cols = ['drive', 'wall', 'rpm', 'vss', 'gear', 'tps_raw', 'map', 'inj', 'ign', 'b08', 'b0a', 'b0b', 'b0c', 'b0f', 'b9a', 'b27', 'b28', 'b29', 'b2f', 'b3c', 'b10', 'ba0', 'b15']

sec = sys.argv[1] if len(sys.argv) > 1 else 'all'

if sec in ('rare', 'all'):
    print('== rare flag events: 0x0A!=144, 0x0B!=65, 0x0C!=1, 0x08 b0, 0x08 b2==0, 0x3A==24')
    m = (S.b0a != 144) | (S.b0b != 65) | (S.b0c != 1) | (S.b08.astype(int) % 2 == 1) | (S.psp == 0) | (S.b3a == 24)
    for i in np.where(m)[0]:
        lo, hi = max(0, i - 1), min(len(S), i + 2)
        print(S.iloc[lo:hi][cols + ['b3a', 'b32', 'b37', 'b17', 'b19']].to_string(header=(i == np.where(m)[0][0])))
        print()

if sec in ('cl', 'all'):
    print('== closed loop 0x0F b0 vs 0x9A, conditions')
    print(pd.crosstab(S.cl, S.b9a))
    for v in [1, 2, 4]:
        s = S[S.b9a == v]
        print(v, len(s), 'rpm', s.rpm.describe()[['min', '50%', 'max']].values, 'tps', s.tps_raw.describe()[['min', '50%', 'max']].values,
              'map', s['map'].describe()[['min', '50%', 'max']].values, 'inj0 share', round(s.cut.mean(), 2), 'vss med', s.vss.median())
    o = S[(S.cl == 0)]
    print('open loop reads with inj>0 (not a cut):')
    print(o[~o.cut][cols].to_string())
