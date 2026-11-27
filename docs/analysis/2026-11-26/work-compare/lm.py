import numpy as np, lib
from common import T, DAY, runs
w = lib.wall
for dy, drv in (('11-25', range(6, 31)), ('11-26', range(31, 39))):
    print('=====', dy)
    for did in drv:
        idx = np.nonzero(T['drive'] == did)[0]
        if len(idx) < 60: continue
        r = T['rpm'][idx]
        st = np.where(r < 745, 'L', np.where(r < 860, 'M', np.where(r < 1300, 'H', '?')))
        st = np.where(np.nan_to_num(T['vss'][idx]) > 0.5, 'V', st)
        # smooth: state runs
        cur = None; out = []
        i = 0
        while i < len(st):
            j = i
            while j + 1 < len(st) and st[j + 1] == st[i]: j += 1
            out.append((st[i], i, j)); i = j + 1
        # merge short runs (<15 s) into prev
        mer = []
        for s, a, b in out:
            if mer and (b - a < 15 or mer[-1][0] == s):
                mer[-1] = (mer[-1][0], mer[-1][1], b)
            else:
                mer.append((s, a, b))
        line = []
        for s, a, b in mer:
            ia, ib = idx[a], idx[b]
            sl = slice(ia, ib + 1)
            line.append('%s %s %4.0fs r%4.0f e%3.0f(%2.0f) iv%4.1f eld%4.1f tr%4.1f' % (s, w(T['t'][ia]), T['t'][ib] - T['t'][ia], np.nanmedian(T['rpm'][sl]),
                        np.nanmedian(T['ect'][sl]), np.nanmedian(T['ect_raw'][sl]), np.nanmedian(T['iacv'][sl]), np.nanmedian(T['eld'][sl]), np.nanmedian(T['stft'][sl] + T['ltft'][sl])))
        print('d%d' % did); print('   ' + '\n   '.join(line))
