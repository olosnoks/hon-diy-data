from ld import *
import datetime as dt, sys
D = load_all()
did = int(sys.argv[1]); hh = sys.argv[2]; pre = float(sys.argv[3]); post = float(sys.argv[4])
s = D[did]['s']; rt, rv = s['rpm']
day = dt.datetime.fromtimestamp(rt[0]).strftime('%Y-%m-%d')
tc = dt.datetime.strptime(day + ' ' + hh, '%Y-%m-%d %H:%M:%S.%f' if '.' in hh else '%Y-%m-%d %H:%M:%S').timestamp()
SC = [(0x10, 'ect', ect_c), (0x19, 'eld', lambda x: 77.06-x/2.5371), (0x17, 'batt', lambda x: x/10.45), (0x29, 'x29', lambda x: x), (0x28, 'iacv', lambda x: x/2.55),
      (0x27, 'x27', lambda x: x), (0x12, 'map', lambda x: x*.716-5), (0x26, 'ign', lambda x: (x-24)/4), (0x20, 'stft', lambda x: (x/128-1)*100), (0x15, 'o2', lambda x: x/51.3), (0x18,'altfr',lambda x:x/2.55)]
print('t   rpm(min/max per 0.5 s) ' + ' '.join('%6s' % n for a, n, _ in SC))
k = -pre
while k <= post:
    m = (rt > tc + k - .5) & (rt <= tc + k); r = rv[m]
    vals = ['%6.1f' % fn(at(s[a], tc + k)) if a in s else '     -' for a, n, fn in SC]
    print('%+5.1f %5s %5s ' % (k, ('%d' % r.min()) if len(r) else '-', ('%d' % r.max()) if len(r) else '-') + ' '.join(vals))
    k += .5
