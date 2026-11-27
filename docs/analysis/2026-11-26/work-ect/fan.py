"""Radiator fan cycles from ELD steps (+5..+10 A held >=15 s) and the ECT reading at fan-on."""
import pickle, numpy as np, lib
D = pickle.load(open('drives.pkl', 'rb'))
out = []
for did in (33, 34, 35, 36, 37, 38, 39):
    d = D[did]; t, el = d['eld']; te, e = d['ect']
    vss = lib.at(d['vss'], t)
    # median-filter 5
    k = 5; elf = np.array([np.median(el[max(0, i - 2):i + 3]) for i in range(len(el))])
    i = 10; n = 0
    while i < len(t) - 20:
        base = np.median(elf[i - 10:i - 2])
        if 4.5 < elf[i] - base < 11 and np.all(elf[i:i + 12] - base > 3.5):
            # find end
            j = i
            while j < len(t) - 1 and elf[j] - base > 3: j += 1
            dur = t[j] - t[i]
            e_on = lib.at(d['ect'], t[i]); e_min = e[(te >= t[i]) & (te <= t[j] + 30)].min() if j > i else e_on
            out.append((did, t[i], dur, elf[i] - base, e_on, e_min, vss[i]))
            n += 1; i = j + 5
        else:
            i += 1
for did, t0, dur, step, eon, emin, v in out:
    print('d%d %s fan? +%.1f A for %3.0f s  ECT at on %.1f  min after %.1f  vss %d' % (did, lib.wall(t0), step, dur, eon, emin, v))
a = np.array([(o[4], o[2]) for o in out])
print('n', len(out), 'ECT at fan-on median %.1f IQR %.1f-%.1f' % (np.median(a[:, 0]), *np.percentile(a[:, 0], [25, 75])))
pickle.dump(out, open('fan.pkl', 'wb'))
