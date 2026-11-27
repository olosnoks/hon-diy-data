import numpy as np, lib
from common import T, DAY, runs
w = lib.wall
res = []
for dy, drv in (('11-25', range(6, 31)), ('11-26', range(31, 38))):
    allL = []; ons = []
    for did in drv:
        idx = np.nonzero(T['drive'] == did)[0]
        if len(idx) < 60: continue
        r = T['rpm'][idx]; rm = T['rpm_min'][idx]; e = T['ect_raw'][idx]; eld = T['eld'][idx]
        L = r < 730
        M = (r > 770) & (r < 860)
        # sustained M runs >= 60 s preceded by >= 20 s L within 10 s
        for a, b in runs(M):
            if b - a < 45: continue
            pre = slice(max(0, a - 30), a)
            if L[max(0, a - 30):max(0, a - 3)].mean() < 0.7: continue
            mn = np.nanmin(rm[max(0, a - 6):a + 1])
            de = np.nanmax(e[max(0, a - 10):a + 1]) - np.nanmin(e[max(0, a - 10):a + 1])
            deld = np.nanmedian(eld[a + 5:a + 30]) - np.nanmedian(eld[max(0, a - 30):a - 3])
            ons.append((T['t'][idx[a]], mn, e[a], de, deld, T['t'][idx[b]] - T['t'][idx[a]], did))
        # baseline: 7-s window minima in L, warm ECT, not followed by M within 10 s
        for i in range(10, len(idx) - 12, 7):
            if L[i - 7:i + 1].all() and np.nanmax(e[i - 7:i + 1]) <= 47 and L[i + 1:i + 12].all():
                allL.append(np.nanmin(rm[i - 7:i + 1]))
    print('==', dy, 'sustained L->M onsets', len(ons))
    for t, mn, e, de, deld, dur, did in ons:
        print('  %s d%d  min rpm in 6 s before %4.0f  ect raw %3.0f (range 10 s %2.0f)  dELD %+5.1f A  M lasted %4.0f s' % (w(t), did, mn, e, de, deld, dur))
    allL = np.array(allL)
    print('  baseline L 7-s windows not followed by M: n %d, min-rpm p5 %4.0f p50 %4.0f; share <650: %.3f, <640: %.3f' % (
        len(allL), *np.percentile(allL, [5, 50]), np.mean(allL < 650), np.mean(allL < 640)))
