import numpy as np, pickle, datetime
from common import wall, wday, idle
T = pickle.load(open('T2.pkl', 'rb')); RL = pickle.load(open('rule.pkl', 'rb'))
def ts(s): return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').timestamp()
ENT = ['2026-11-25 18:45:25', '2026-11-25 19:20:13', '2026-11-25 19:41:19', '2026-11-25 20:07:58', '2026-11-25 20:26:15', '2026-11-25 20:36:22',
       '2026-11-25 22:28:23', '2026-11-26 21:44:17', '2026-11-26 22:18:06']
EXI = ['2026-11-25 20:00:47', '2026-11-25 20:28:56', '2026-11-25 22:38:55', '2026-11-26 22:39:49']
t = T['t']
cols = ['b%02x' % a for a in list(range(0x03, 0x40))] + ['target', 'stft', 'ltft', 'eld', 'batt', 'alt_fr', 'iat', 'o2', 'o2_2', 'map', 'iacv']
def win(tc, a, b, c):
    m = (t >= tc + a) & (t <= tc + b) & idle & ~T['fan_on']
    return np.nanmean(T[c][m]) if m.any() and np.isfinite(T[c][m]).any() else np.nan
def table(evs, label):
    D = {c: [] for c in cols}
    for e in evs:
        te = ts(e)
        for c in cols: D[c].append(win(te, 5, 60, c) - win(te, -60, -5, c))
    print('==', label, len(evs))
    for c in cols:
        v = np.array(D[c]); v = v[np.isfinite(v)]
        if len(v) == 0: continue
        if np.all(np.abs(v) < 0.05): continue
        same = max(np.sum(v > 0), np.sum(v < 0))
        print('%-7s n%d  mean %+7.2f  min %+7.2f max %+7.2f  same-sign %d/%d' % (c, len(v), v.mean(), v.min(), v.max(), same, len(v)))
table(ENT, 'ENTRIES (after - before, idle non-fan, 5-60 s)')
# bit flips within +-120 s of entries
bitbytes = [0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f]
print('== bit changes within +-120 s of entries')
for e in ENT:
    te = ts(e); m = np.nonzero((t >= te - 120) & (t <= te + 120))[0]
    out = []
    for a in bitbytes:
        v = T['b%02x' % a][m]; ok = np.isfinite(v); vv = v[ok].astype(int); tt = t[m][ok]
        for k in range(1, len(vv)):
            if vv[k] != vv[k - 1]: out.append('0x%02X %d->%d @%+.0fs' % (a, vv[k - 1], vv[k], tt[k] - te))
    print(e, out if out else 'none')
# context at entries
print('== context')
for e in ENT:
    te = ts(e); i = np.searchsorted(t, te)
    print(e, 'since start %5.0f  since fan-on %5.0f fan-off %5.0f since rev %5.0f since reset %4.0f  IAT %4.1f  ELD pre %4.1f  2A %s 2F %s 0B %s 0C %s' % (
        T['t_since_start'][i], T['t_since_fan_on'][i], T['t_since_fan_off'][i], T['t_since_rev'][i], RL['tsr'][i], T['iat'][i], win(te, -60, -5, 'eld'),
        T['x2a'][i - 3], T['x2f'][i - 3], T['b0b'][i], T['b0c'][i]))
