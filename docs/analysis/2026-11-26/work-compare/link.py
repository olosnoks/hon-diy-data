import numpy as np, lib, datetime
def tt(d, s): return datetime.datetime.strptime('2026-11-%s %s' % (d, s), '%Y-%m-%d %H:%M:%S').timestamp()
c = lib.con()
P = {'11-25 parked (d6-30)': [], '11-26 parked before 22:24 (d31-37)': [], 'd38 parked 22:24-22:39:47': [], 'driving+stops 22:39:47-23:10': [], 'driving+stops 23:10-23:27': []}
for did in range(6, 40):
    d = lib.load(did, {'rpm', 'vss'}, c)
    if 'rpm' not in d: continue
    t, r = d['rpm']
    g = np.diff(t)
    for i in range(len(g)):
        pass
    if did <= 30: k = '11-25 parked (d6-30)'
    elif did <= 37: k = '11-26 parked before 22:24 (d31-37)'
    else: k = None
    if k:
        P[k].append((t, g, r))
    else:
        for kk, a, b in (('d38 parked 22:24-22:39:47', 0, tt('26', '22:39:47')), ('driving+stops 22:39:47-23:10', tt('26', '22:39:47'), tt('26', '23:10:00')), ('driving+stops 23:10-23:27', tt('26', '23:10:00'), 1e12)):
            m = (t >= a) & (t < b)
            if m.sum() > 10:
                P[kk].append((t[m], np.diff(t[m]), r[m]))
print('fast-block (rpm) gaps; nominal spacing ~0.08 s')
for k, L in P.items():
    if not L: continue
    g = np.concatenate([x[1] for x in L])
    hrs = np.sum(np.minimum(g, 10)) / 3600
    n05 = np.sum((g >= 0.5) & (g < 30)); n2 = np.sum((g >= 2) & (g < 30)); med = np.median(g)
    print('  %-38s %5.2f h  median dt %.3f s  gaps >=0.5 s: %4d (%5.1f/h)  >=2 s: %3d (%4.1f/h)  longest <30 s: %.1f s' % (k, hrs, med, n05, n05 / hrs, n2, n2 / hrs, g[g < 30].max()))
