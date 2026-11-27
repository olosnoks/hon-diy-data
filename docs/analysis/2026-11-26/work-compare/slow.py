import numpy as np, lib
from common import T, DAY, runs
w = lib.wall
t = T['t']
def changes(name, drv, fmt='%.1f'):
    m = np.isin(T['drive'], drv) & np.isfinite(T[name])
    idx = np.nonzero(m)[0]
    v = T[name][idx]
    ch = np.nonzero(np.diff(v) != 0)[0] + 1
    print('--', name, 'first', w(t[idx[0]]), fmt % v[0], 'changes', len(ch))
    for c in ch[:80]:
        i = idx[c]
        print('   %s d%d %s -> %s  ect raw %3.0f (%4.1f C) vss %3.0f rpm %4.0f iacv %4.1f cmd %3.0f stft %5.1f cl %s' % (w(t[i]), T['drive'][i], fmt % v[c - 1], fmt % v[c], T['ect_raw'][i], T['ect'][i],
              np.nan_to_num(T['vss'][i]), T['rpm'][i], T['iacv'][i], T['iac_cmd'][i], T['stft'][i], T['closed_loop'][i]))
changes('x2a', range(33, 40), '%.0f')
