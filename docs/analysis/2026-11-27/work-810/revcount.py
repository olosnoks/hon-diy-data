import numpy as np, pickle, datetime
from common import FAST, wall
def ts(s): return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').timestamp()
FT = np.concatenate([F[:, 0] for F in FAST.values()]); FR = np.concatenate([F[:, 1] for F in FAST.values()])
o = np.argsort(FT); FT = FT[o]; FR = FR[o]
def revs(a, b, maxgap=5.0):
    """engine revolutions between unix times a and b (trapezoid on fast rpm; gaps > maxgap filled with neighbour mean)"""
    m = (FT >= a) & (FT <= b); x = FT[m]; y = FR[m]
    if len(x) < 2: return np.nan, 0
    dt = np.diff(x); ym = (y[1:] + y[:-1]) / 2
    gap = dt[dt > maxgap].sum()
    return float(np.sum(ym * dt) / 60), gap
if __name__ == '__main__':
    pairs = [('2026-11-25 18:37:38', '2026-11-25 18:45:25', 'ect72 reading'),
             ('2026-11-25 19:13:08', '2026-11-25 19:20:13', 'rev 2698'),
             ('2026-11-25 19:32:23', '2026-11-25 19:41:19', 'start'),
             ('2026-11-25 20:00:45', '2026-11-25 20:07:58', 'rev 1926'),
             ('2026-11-25 20:17:50', '2026-11-25 20:26:15', 'start (fan 20:25:23)'),
             ('2026-11-25 20:29:32', '2026-11-25 20:36:22', 'ect57 (later 108-119 ignored?) fan 20:36:04'),
             ('2026-11-25 20:30:52', '2026-11-25 20:36:22', 'ect108'),
             ('2026-11-25 22:18:47', '2026-11-25 22:28:23', 'ect81 (fan 22:27:53)'),
             ('2026-11-26 21:35:08', '2026-11-26 21:44:17', '0x2F 0->1'),
             ('2026-11-26 22:10:45', '2026-11-26 22:18:06', '0x2F 0->1 (fan 22:17:45-22:18:01)')]
    for a, b, k in pairs:
        r, g = revs(ts(a), ts(b))
        print('%s -> %s  %4.0f s  %6.0f revs (gaps %3.0fs)  mean rpm %4.0f  %s' % (a[11:], b[11:], ts(b) - ts(a), r, g, r / (ts(b) - ts(a)) * 60, k))
