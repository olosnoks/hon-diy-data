import numpy as np, lib
from common import T, DAY, runs
w = lib.wall
park = (T['drive'] <= 37) | ((T['drive'] == 38) & (T['t'] < T['t'][T['drive'] == 38][0] + 909))
idle = park & (np.nan_to_num(T['vss']) < 0.5) & (T['tps'] < 9) & (T['rpm'] > 500) & (T['rpm'] < 1300)
ac = (np.nan_to_num(T['ac_clutch']) > 0)
idle &= ~ac
for dy in ('11-25', '11-26'):
    m = idle & (DAY == dy)
    print('==', dy, 'idle s', m.sum())
    er = T['ect_raw'][m]; r = T['rpm'][m]
    for lo, hi in [(30, 37), (37, 39), (39, 41), (41, 43), (43, 45), (45, 47), (47, 49), (49, 51), (51, 53), (53, 55), (55, 58), (58, 62), (62, 70), (70, 100), (100, 256)]:
        k = (er >= lo) & (er < hi)
        if k.sum() < 5: continue
        rr = r[k]
        tg = T['idle_target'][m][k]
        print(' raw %3d-%3d (%4.1f-%4.1f C) n %5d  rpm p10 %4.0f med %4.0f p90 %4.0f  frac<740 %.2f  tgt %s  iacv %4.1f' % (
            lo, hi - 1, lib.ect_poly(hi - 1), lib.ect_poly(lo), k.sum(), *np.percentile(rr, [10, 50, 90]), np.mean(rr < 740),
            ('%4.0f' % np.nanmedian(tg)) if np.isfinite(tg).any() else '  - ', np.nanmedian(T['iacv'][m][k])))
