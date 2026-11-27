"""Candidate 'purge-ramp reset' events and time since the last one."""
import numpy as np, pickle
from common import wall, wday, runs, idle, FAST
T = pickle.load(open('T2.pkl', 'rb')); C = pickle.load(open('ctx.pkl', 'rb'))
t = T['t']; er = T['b10']
R = []   # (time, kind)
for s in C['STARTS']: R.append((s, 'start'))
for d, F in FAST.items():
    m = F[:, 1] > 1000
    for a, b in runs(m):
        ta = F[a, 0]
        if ta - C['STARTS'][np.searchsorted(C['STARTS'], ta) - 1] > 25: R.append((F[b, 0], 'rev%.0f' % F[a:b + 1, 1].max()))
# throttle opening at idle (tps > 3 %)
for a, b in runs(np.nan_to_num(T['tps']) > 3):
    R.append((t[b], 'tps%.0f' % np.nanmax(T['tps'][a:b + 1])))
# accepted cool ECT reading (56..119 raw)
for a, b in runs((er >= 56) & (er < 120)):
    R.append((t[b], 'ect%d' % np.nanmax(er[a:b + 1][er[a:b + 1] < 120])))
R.sort()
# merge events within 5 s
M = []
for x in R:
    if M and x[0] - M[-1][0] < 5: M[-1] = (x[0], M[-1][1] + '+' + x[1])
    else: M.append(x)
R = M
Rt = np.array([x[0] for x in R])
j = np.searchsorted(Rt, t, side='right') - 1
T['t_since_reset'] = np.where(j >= 0, t - Rt[np.clip(j, 0, None)], np.inf)
pickle.dump(R, open('resets.pkl', 'wb')); pickle.dump(T, open('T2.pkl', 'wb'))
if __name__ == '__main__':
    for x in R: print(wday(x[0]), x[1])
