from ld import *
import datetime as dt, sys
D = load_all()
f = lambda u: dt.datetime.fromtimestamp(u).strftime('%H:%M:%S.%f')[:-4]
# candidate engine-off events: (label, drive id, end of data)
EV = [('18:55 d15', 15), ('19:29 d20', 20), ('20:13 d22', 22), ('21:16 d27', 27), ('22:34 d29 (cable, confirmed)', 29), ('22:44 d30', 30)]
SC = {0x10: ('ect', lambda x: ect_c(x)), 0x12: ('map', lambda x: x*0.716-5), 0x17: ('batt', lambda x: x/10.45), 0x19: ('eld', lambda x: 77.06-x/2.5371),
      0x18: ('altfr', lambda x: x/2.55), 0x20: ('stft', lambda x: (x/128-1)*100), 0x22: ('ltft', lambda x: (x/128-1)*100), 0x15: ('o2', lambda x: x/51.3),
      0x26: ('ign', lambda x: (x-24)/4), 0x27: ('x27', lambda x: x), 0x28: ('iacv', lambda x: x/2.55), 0x29: ('x29', lambda x: x), 0x14: ('tps', lambda x: x/2.55),
      0x24: ('inj_hi', lambda x: x), 0x08: ('f08', lambda x: x), 0x0B: ('f0B', lambda x: x), 0x0F: ('f0F', lambda x: x), 0x3C: ('knock', lambda x: x/51)}
for lab, did in EV:
    s = D[did]['s']; rt, rv = s['rpm']; end = rt[-1]
    print('\n==== %s  last rpm read %s ====' % (lab, f(end)))
    print('rates (reads in last 60 s):', {SC[a][0]: int(((s[a][0] > end-60)).sum()) for a in SC if a in s}, 'rpm', int((rt > end-60).sum()))
    print(' sec | rpm min/med/max | ' + ' '.join('%6s' % SC[a][0] for a in SC if a in s and a not in (0x24,)))
    for k in range(-60, 1, 2):
        t0, t1 = end+k-2, end+k
        m = (rt > t0) & (rt <= t1)
        r = rv[m]
        row = '%4d | %4s %4s %4s | ' % (k, *(('%d' % r.min(), '%d' % np.median(r), '%d' % r.max()) if len(r) else ('-', '-', '-')))
        vals = []
        for a in SC:
            if a not in s or a == 0x24: continue
            v = at(s[a], t1); vals.append('%6.1f' % SC[a][1](v) if v == v else '     -')
        print(row + ' '.join(vals))
    print('last 3 s rpm reads:', [(round(t-end, 2), round(v)) for t, v in zip(rt[rt > end-3], rv[rt > end-3])])
    lt, lv = s['link']
    m = lt > end-60
    print('link channel last 60 s:', [(round(t-end, 1), v) for t, v in zip(lt[m], lv[m])][:40])
