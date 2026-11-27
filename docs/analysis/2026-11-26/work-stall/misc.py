from ld import *
import datetime as dt
from math import comb
D = load_all()
# ELD step rate (+8 A between consecutive 1 Hz reads) on 11-25 Honda drives
steps = 0; hours = 0
for did, d in D.items():
    if not d['honda'] or did > 30: continue
    t, v = d['s'][0x19]; a = 77.06 - v/2.5371
    st = np.where((np.diff(a) >= 8) & (np.diff(t) < 3))[0]
    steps += len(st); hours += (t[-1]-t[0])/3600
r = steps/hours/3600
print('11-25 ELD steps >= +8 A: %d in %.2f h -> one per %.0f s' % (steps, hours, 1/r))
for w in (8, 10):
    p = 1 - np.exp(-r*w)
    pge2 = sum(comb(4, k)*p**k*(1-p)**(4-k) for k in range(2, 5))
    print('window %d s: p(one event) %.3f, P(>=2 of 4 by chance) %.3f' % (w, p, pge2))
# warm idle IACV = 0 fraction per day (ECT >= 76, rpm < 900)
for day in ('11-25', '11-26'):
    n = z = 0; vals = []
    for did, d in D.items():
        if not d['honda']: continue
        s = d['s']; t, v = s[0x28]
        if dt.datetime.fromtimestamp(t[0]).strftime('%m-%d') != day: continue
        e = ect_c(at(s[0x10], t)); rp = at(s['rpm'], t)
        m = (e >= 76) & (rp < 900) & (rp > 400)
        n += m.sum(); z += (v[m] == 0).sum(); vals += list(rp[m])
    print('%s warm idle (ECT>=76, rpm<900): %d s, IACV=0 in %.1f %%, rpm median %.0f, p5 %.0f' % (day, n, 100*z/max(n, 1), np.median(vals), np.percentile(vals, 5)))
