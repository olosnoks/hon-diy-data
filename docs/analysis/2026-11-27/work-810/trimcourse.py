import numpy as np, pickle, datetime
from common import wall, idle
from bytes_ev import ENT, EXI, ts
T = pickle.load(open('T2.pkl', 'rb')); t = T['t']
tot = T['stft'] + T['ltft']
fitc = lambda r: 8.2 + 6.7 * 1000 / r
res = tot - fitc(T['rpm'])
ok = idle & ~T['fan_on'] & (T['b10'] <= 44) & (T['ect_max60'] <= 46)
bins = [(-300, -60), (-60, 0), (0, 60), (60, 180), (180, 420), (420, 900), (900, 1800)]
def course(evs, label):
    print(label)
    print('  bin(s)        ' + ' '.join('%11s' % ('%d..%d' % b) for b in bins))
    for name, arr in (('total trim', tot), ('resid vs rpm fit', res), ('stft', T['stft']), ('ltft', T['ltft']), ('rpm', T['rpm']), ('iacv', T['iacv'])):
        row = []
        for a, b in bins:
            vals = []
            for e in evs:
                te = ts(e)
                # stop at the next state change: only use same-state windows (entries: until next exit)
                m = ok & (t >= te + a) & (t < te + b)
                if m.sum() > 5: vals.append(np.nanmean(arr[m]))
            row.append('%5.1f (n%d)' % (np.mean(vals), len(vals)) if vals else '     -     ')
        print('  %-14s' % name + ' '.join(row))
# use 11-25 entries whose M lasted > 900 s for the long bins
course([e for e in ENT if e.startswith('2026-11-25')], 'Entries 11-25 (n7)')
