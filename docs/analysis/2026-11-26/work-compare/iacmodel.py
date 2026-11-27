import numpy as np, lib, pickle, datetime
from common import T
G, rows, cols = pickle.load(open('groups.pkl', 'rb'))
def tt(s): return datetime.datetime.strptime('2026-11-26 ' + s, '%Y-%m-%d %H:%M:%S').timestamp()
t = T['t']; did = T['drive']
base = G['11-26 parked, low reading 69-75 C (target 810-837)'] | G['11-26 parked, low reading 63-68 C (target 850-880)']
ok = np.isfinite(T['iac_cmd']) & np.isfinite(T['idle_target']) & (np.abs(T['rpm'] - T['idle_target']) < 40)
fit = base & ok
X = np.c_[np.ones(fit.sum()), T['idle_target'][fit] - 810, T['eld'][fit] - 10, T['iat'][fit] - 50]
y = T['iac_cmd'][fit]
b, *_ = np.linalg.lstsq(X, y, rcond=None)
res = y - X @ b
print('fit on parked 11-26 (n %d): cmd = %.1f + %.3f*(target-810) + %.3f*(eld-10) + %.3f*(iat-50); resid sd %.1f' % (fit.sum(), *b, res.std()))
# headlight check: the 22:36 step (parked, vss 0) -- ELD +14 A
for k in ['after driving: gas station 22:43:30-22:45:24', 'after driving: hot restart 22:51:30-22:52:50', 'after driving: home 23:26:35-23:26:58', 'after driving: all three']:
    m = G[k] & ok
    Xp = np.c_[np.ones(m.sum()), T['idle_target'][m] - 810, T['eld'][m] - 10, T['iat'][m] - 50]
    Xq = Xp.copy(); Xq[:, 3] = 0  # without IAT term
    print('  %-46s n %3d  cmd obs %5.1f  pred %5.1f (resid %+5.1f)  pred w/o IAT term %5.1f  iacv obs %4.1f' % (k, m.sum(), np.median(T['iac_cmd'][m]), np.median(Xp @ b),
          np.median(T['iac_cmd'][m] - Xp @ b), np.median(Xq @ b), np.median(T['iacv'][m])))
# 22:36 headlight step directly
m1 = (t > tt('22:35:00')) & (t < tt('22:36:15')) & (did == 38); m2 = (t > tt('22:37:40')) & (t < tt('22:39:30')) & (did == 38)
for m, l in ((m1, 'before 22:36 (lights off)'), (m2, 'after 22:37:40 (lights on)')):
    print('  d38 %-28s cmd %5.1f iacv %4.1f eld %4.1f rpm %4.0f tgt %4.0f ect raw %3.0f stft %5.1f map %4.1f' % (l, *[np.nanmedian(T[c][m]) for c in ('iac_cmd', 'iacv', 'eld', 'rpm', 'idle_target', 'ect_raw', 'stft', 'map')]))
