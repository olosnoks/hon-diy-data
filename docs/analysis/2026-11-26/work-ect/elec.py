import pickle, numpy as np, lib
D = pickle.load(open('drives.pkl', 'rb'))
print('--- battery / alt_fr / eld by rpm band (1 Hz reads, rpm>400)')
for did in (37, 38, 39):
    d = D[did]; tb, b = d['batt']; a = d['alt_fr'][1]; el = d['eld'][1]; br = d['batt_raw'][1]
    rpm = lib.at(d['rpm'], tb); vss = lib.at(d['vss'], tb); mp = lib.at(d['map'], tb)
    run = rpm > 400
    print('drive', did, 'n', run.sum(), 'batt median %.2f p1 %.2f p99 %.2f min %.2f max %.2f; raw values %s' % (
        np.median(b[run]), np.percentile(b[run], 1), np.percentile(b[run], 99), b[run].min(), b[run].max(),
        dict(zip(*np.unique(br[run], return_counts=True)))))
    for lo, hi in ((400, 750), (750, 1000), (1000, 1500), (1500, 2000), (2000, 2500), (2500, 3000), (3000, 6000)):
        m = run & (rpm >= lo) & (rpm < hi)
        if m.sum() < 5: continue
        print('   rpm %4d-%4d n=%4d batt %.2f (min %.2f) alt_fr %5.1f eld %5.1f' % (lo, hi, m.sum(), np.median(b[m]), b[m].min(), np.median(a[m]), np.median(el[m])))
print('--- reads with batt < 13.8 V (rpm>400)')
for did in (37, 38, 39):
    d = D[did]; tb, b = d['batt']
    for i in np.where((b < 13.8) & (lib.at(d['rpm'], tb) > 400))[0]:
        t0 = tb[i]
        ft, rpm = d['rpm']; w = (ft > t0 - 2) & (ft < t0 + 1)
        print('  d%d %s batt %.2f alt_fr %3.0f eld %4.1f | rpm(-2..+1s) %4d..%4d now %4d vss %3d map %3.0f tps %3.0f | prev batt %.2f next %.2f' % (
            did, lib.wall(t0), b[i], d['alt_fr'][1][i], d['eld'][1][i], rpm[w].min(), rpm[w].max(), lib.at(d['rpm'], t0), lib.at(d['vss'], t0),
            lib.at(d['map'], t0), lib.at(d['tps'], t0), b[i - 1], b[min(i + 1, len(b) - 1)]))
print('--- reads with batt > 14.5')
for did in (37, 38, 39):
    d = D[did]; tb, b = d['batt']
    for i in np.where(b > 14.45)[0]:
        print('  d%d %s batt %.2f rpm %d eld %.1f alt %d' % (did, lib.wall(tb[i]), b[i], lib.at(d['rpm'], tb[i]), d['eld'][1][i], d['alt_fr'][1][i]))
