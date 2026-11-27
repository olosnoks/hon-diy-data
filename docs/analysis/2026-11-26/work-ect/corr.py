"""ECT steps vs driving conditions in drives 38+39 (rate per hour by condition, and event coincidence vs baseline)."""
import pickle, numpy as np, lib
from events import fast, drive_events
D = pickle.load(open('drives.pkl', 'rb'))
recs = []
for did in (38, 39):
    d = D[did]; te, e = d['ect']; r = d['ect_raw'][1]
    ft, rpm, vss, gear = fast(d)
    ev, launches = drive_events(d)
    shifts = np.array([b for a, b, s in ev]); launches = np.array(launches)
    mp = d['map']; br = d['brake']; iat = d['iat']
    for i in range(1, len(te)):
        if te[i] - te[i - 1] > 3: continue
        t0, t1 = te[i - 1], te[i]
        w = (ft > t0 - 1) & (ft <= t1)
        if not w.any() or rpm[w].min() < 400: continue
        v = vss[w]
        # vss jitter: std of second difference over the window (bump proxy)
        jit = np.std(np.diff(v, 2)) if w.sum() > 4 else 0
        dv = (v[-1] - v[0]) / max(ft[w][-1] - ft[w][0], .1)
        recs.append(dict(did=did, t=t1, de=e[i] - e[i - 1], dr=r[i] - r[i - 1], vss=v.mean(), rpm=rpm[w].mean(), map=lib.at(mp, t1),
                         brake=lib.at(br, t1), shift=bool(len(shifts) and np.any(np.abs(shifts - t1) < 3)),
                         launch=bool(len(launches) and np.any(np.abs(launches - t1) < 5)), jit=jit, accel=dv,
                         rpmstd=np.std(rpm[w]), iat=lib.at(iat, t1), clutch=bool(np.any((gear[w] == 0) & (v >= 5)))))
import collections
K = list(recs[0].keys()); A = {k: np.array([x[k] for x in recs]) for k in K}
step4 = np.abs(A['de']) >= 4; step2 = np.abs(A['dr']) >= 2
H = len(recs) / 3600
print('reads', len(recs), 'hours %.2f; steps>=4C %d; raw steps>=2 %d' % (H, step4.sum(), step2.sum()))
def rate(m, lab):
    h = m.sum() / 3600
    print('  %-34s %5.2f h  >=4C: %2d (%5.1f/h)   raw>=2: %3d (%5.1f/h)' % (lab, h, (step4 & m).sum(), (step4 & m).sum() / max(h, 1e-9), (step2 & m).sum(), (step2 & m).sum() / max(h, 1e-9)))
print('by speed'); 
for lo, hi in ((-1, 0.5), (0.5, 30), (30, 60), (60, 85), (85, 200)): rate((A['vss'] > lo) & (A['vss'] <= hi), 'vss %g-%g' % (max(lo, 0), hi))
print('by rpm')
for lo, hi in ((0, 1000), (1000, 1500), (1500, 2000), (2000, 2500), (2500, 9000)): rate((A['rpm'] > lo) & (A['rpm'] <= hi), 'rpm %d-%d' % (lo, hi))
print('by MAP')
for lo, hi in ((0, 30), (30, 50), (50, 70), (70, 200)): rate((A['map'] > lo) & (A['map'] <= hi), 'map %d-%d' % (lo, hi))
print('conditions (moving only, vss>3)')
mv = A['vss'] > 3
for lab, m in (('brake on', A['brake'] > 0), ('shift within 3 s', A['shift']), ('clutch/neutral while rolling', A['clutch']),
               ('launch within 5 s', A['launch']), ('decel < -3 km/h/s', A['accel'] < -3), ('accel > 3 km/h/s', A['accel'] > 3),
               ('vss jitter top 10%', A['jit'] > np.percentile(A['jit'][mv], 90)), ('rpm std top 10%', A['rpmstd'] > np.percentile(A['rpmstd'][mv], 90))):
    rate(mv & m, lab); rate(mv & ~m, '   not ' + lab)
print('by IAT')
for lo, hi in ((0, 25), (25, 40), (40, 60)): rate((A['iat'] > lo) & (A['iat'] <= hi), 'iat %d-%d' % (lo, hi))
print('by drive phase'); 
for did in (38, 39):
    for lab, m in (('idle', A['vss'] <= 3), ('moving', A['vss'] > 3)): rate((A['did'] == did) & m, 'd%d %s' % (did, lab))
pickle.dump(A, open('corr.pkl', 'wb'))
print('\nsteps >=4C detail')
for i in np.where(step4)[0]:
    print('  d%d %s de %+5.1f vss %5.1f rpm %5.0f map %3.0f brake %d shift %d clutch %d launch %d accel %+5.1f jit %.2f iat %.0f' % (
        A['did'][i], lib.wall(A['t'][i]), A['de'][i], A['vss'][i], A['rpm'][i], A['map'][i], A['brake'][i], A['shift'][i], A['clutch'][i], A['launch'][i], A['accel'][i], A['jit'][i], A['iat'][i]))
