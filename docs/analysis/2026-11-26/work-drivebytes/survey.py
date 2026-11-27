"""Per-byte and per-bit survey over both drives; gear inference check."""
import pickle, numpy as np, pandas as pd, math
D = pickle.load(open('D.pkl', 'rb'))
S = pd.concat([D[k]['slow'].assign(drive=k[-4:]) for k in D], ignore_index=True)
F = pd.concat([D[k]['fast'].assign(drive=k[-4:]) for k in D], ignore_index=True)
pickle.dump((S, F), open('SF.pkl', 'wb'))

print('slow reads', len(S), 'fast reads', len(F))
print('vss>0 share slow', (S.vss > 0).mean().round(3), 'max vss', S.vss.max(), 'max rpm', F.rpm.max())

# vss calibration vs gps
m = S.dropna(subset=['gps_speed'])
m = m[(m.gps_speed > 10)]
print('vss/gps ratio median', (m.vss / m.gps_speed).median().round(3), 'n', len(m))

# gear clusters
k = 1000 / 60 / (math.pi * 0.586) * 4.06
ratios = {'1': 3.25, '2': 1.76, '3': 1.17, '4': 0.92, '5': 0.71}
print('rpm per km/h theory', {g: round(r * k, 1) for g, r in ratios.items()})
f = F[(F.vss >= 15) & (F.rpm > 1000)]
rv = f.rpm / f.vss
h, e = np.histogram(rv, bins=np.arange(15, 140, 2))
for hh, ee in zip(h, e):
    if hh > 50:
        print(f'  {ee:5.0f}-{ee+2:<4.0f} {hh:6d} ' + '#' * int(hh / 200))

bytes_ = sorted(c for c in S.columns if c.startswith('b') and len(c) == 3)
print('\nbyte  distinct  nchg  min max  top values')
for b in bytes_:
    x = S[b].dropna().astype(int)
    vc = x.value_counts()
    print(f'{b}  {len(vc):4d} {int((x.diff().fillna(0) != 0).sum()):6d}  {x.min():3d} {x.max():3d}  ' +
          ' '.join(f'{v}:{n}' for v, n in vc.head(8).items()))

print('\nflag bits (share set, toggles)')
for b in ['b08', 'b09', 'b0a', 'b0b', 'b0c', 'b0d', 'b0e', 'b0f']:
    x = S[b].dropna().astype(int).values
    line = []
    for bit in range(8):
        y = pd.Series((x >> bit) & 1)
        line.append(f'b{bit}:{y.mean():.3f}/{int((y.diff().fillna(0) != 0).sum())}')
    print(b, ' '.join(line))
