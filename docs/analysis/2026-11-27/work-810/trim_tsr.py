import numpy as np, pickle
from common import wday
T = pickle.load(open('T2.pkl', 'rb')); RL = pickle.load(open('rule.pkl', 'rb'))
t = T['t']; S = RL['S']; ok = RL['ok']; tsr = RL['tsr']
day = np.array([wday(x)[:5] for x in t])
tot = T['stft'] + T['ltft']; res = tot - (8.2 + 6.7 * 1000 / np.maximum(T['rpm'], 1))
edges = [0, 60, 120, 180, 240, 300, 360, 420, 480, 600, 900, 1800, 1e9]
out = []
for st in 'LM':
    for a, b in zip(edges[:-1], edges[1:]):
        m = ok & (S == st) & (tsr >= a) & (tsr < b) & (day == '11-25')
        if m.sum() < 20: continue
        out.append((st, a, b, m.sum(), np.nanmean(tot[m]), np.nanmean(res[m]), np.nanmean(T['stft'][m]), np.nanmean(T['ltft'][m]), np.nanmean(T['rpm'][m]), np.nanmean(T['iacv'][m])))
        print('%s tsr %4d-%-5s n%5d total %5.1f resid %+5.1f stft %5.1f ltft %5.1f rpm %4.0f iacv %4.1f' % (st, a, '%d' % b if b < 1e8 else 'inf', *out[-1][3:]))
pickle.dump(out, open('trim_tsr.pkl', 'wb'))
