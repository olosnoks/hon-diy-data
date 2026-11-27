import numpy as np, lib, datetime
from common import T, DAY, runs
w = lib.wall
def tt(d, s): return datetime.datetime.strptime('2026-11-%s %s' % (d, s), '%Y-%m-%d %H:%M:%S').timestamp()
t = T['t']; did = T['drive']; e = T['ect']; er = T['ect_raw']
moving_any = np.zeros(len(t), bool)
P = {
 '11-25 parked (d6-30)': (DAY == '11-25'),
 '11-26 parked before 22:24 (d31-37)': (DAY == '11-26') & (did <= 37),
 'd38 parked 22:24-22:39': (did == 38) & (t < tt('26', '22:39:47')),
 'd38+d39 from first move (driving + stops)': ((did == 38) & (t >= tt('26', '22:39:47'))) | (did == 39),
}
print('== ECT glitch statistics (running reads, rpm>400; jump = >=15 C between reads <=3 s apart; step = >=4 C)')
for k, m in P.items():
    m = m & (T['rpm'] > 400)
    idx = np.nonzero(m)[0]
    dt = np.diff(t[idx]); de = np.abs(np.diff(e[idx]))
    ok = dt <= 3
    hrs = np.sum(np.minimum(dt, 3)) / 3600
    j = np.sum(ok & (de >= 15)); s = np.sum(ok & (de >= 4))
    ev = e[idx]
    print('  %-44s %5.2f h  jumps %3d (%5.1f/h)  steps %3d (%5.1f/h)  reads >=77C %4.0f%%  58-76C %4.0f%%  <58C %4.0f%%  median %.1f' % (
        k, hrs, j, j / hrs, s, s / hrs, 100 * np.mean(ev >= 77), 100 * np.mean((ev >= 58) & (ev < 77)), 100 * np.mean(ev < 58), np.median(ev)))
print('== idle target vs ECT raw (medians), parked vs driving/after')
tg = T['idle_target']
mv = np.nan_to_num(T['vss']) > 0.5
park = ((did == 37) | ((did == 38) & (t < tt('26', '22:39:47'))))
drv = ((did == 38) & (t >= tt('26', '22:39:47'))) | (did == 39)
for r in range(44, 72):
    a = tg[park & (er == r)]; b = tg[drv & (er == r) & ~mv]; c = tg[drv & (er == r) & mv]
    a, b, c = a[np.isfinite(a)], b[np.isfinite(b)], c[np.isfinite(c)]
    if len(a) + len(b) + len(c) < 3: continue
    f = lambda x: ('%4.0f (n%4d)' % (np.median(x), len(x))) if len(x) else '     -     '
    print('  raw %2d %4.1f C  parked %s   drive-stopped %s   moving %s   model 778+13.6(raw-48)=%4.0f' % (r, lib.ect_poly(r), f(a), f(b), f(c), 778 + 13.6 * (r - 48)))
print('== LTFT changes (all drives) by ECT reading accepted')
lt = T['ltft']
m = np.isfinite(lt)
idx = np.nonzero(m)[0]
ch = idx[np.nonzero((np.diff(lt[idx]) != 0) & (np.diff(did[idx]) == 0))[0] + 1]
for k, mm in P.items():
    sec = np.sum(mm & (T['rpm'] > 400) & (T['closed_loop'] > 0))
    c_all = ch[mm[ch]]
    warm = c_all[er[c_all] <= 44]; low = c_all[(er[c_all] > 44) & (er[c_all] < 100)]
    secw = np.sum(mm & (T['rpm'] > 400) & (er <= 44)); secl = np.sum(mm & (T['rpm'] > 400) & (er > 44) & (er < 100))
    print('  %-44s LTFT range %5.1f..%5.1f  changes %3d: at ECT>=77C %3d in %5.0f s (%.1f/h), at 45-76C %3d in %5.0f s (%.1f/h)' % (
        k, np.nanmin(lt[mm]), np.nanmax(lt[mm]), len(c_all), len(warm), secw, len(warm) / max(secw, 1) * 3600, len(low), secl, len(low) / max(secl, 1) * 3600))
for i in ch[(did[ch] >= 33)]:
    print('    %s d%d LTFT %5.1f -> %5.1f  ect raw %3.0f (%4.1f)  vss %3.0f rpm %4.0f stft %5.1f' % (w(t[i]), did[i], lt[i - 1], lt[i], er[i], e[i], np.nan_to_num(T['vss'][i]), T['rpm'][i], T['stft'][i]))
