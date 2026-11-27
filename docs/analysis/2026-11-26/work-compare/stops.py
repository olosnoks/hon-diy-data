import numpy as np, lib
from common import T, runs
w = lib.wall
for did in (38, 39):
    m = T['drive'] == did
    idx = np.nonzero(m)[0]
    v = np.nan_to_num(T['vss'][idx], nan=0)
    mv = v > 0.5
    print('drive', did)
    for a, b in runs(~mv):
        i0, i1 = idx[a], idx[b]
        dur = T['t'][i1] - T['t'][i0]
        if dur < 8: continue
        s = slice(i0, i1 + 1)
        r = T['rpm'][s]
        print('  stop %s-%s %5.0f s  rpm med %4.0f  tgt %4.0f  ect %4.1f  iacv %4.1f  cmd %4.0f  stft %5.1f ltft %5.1f  map %4.1f eld %4.1f tps %4.1f' % (
            w(T['t'][i0]), w(T['t'][i1]), dur, np.nanmedian(r), np.nanmedian(T['idle_target'][s]), np.nanmedian(T['ect'][s]),
            np.nanmedian(T['iacv'][s]), np.nanmedian(T['iac_cmd'][s]), np.nanmedian(T['stft'][s]), np.nanmedian(T['ltft'][s]),
            np.nanmedian(T['map'][s]), np.nanmedian(T['eld'][s]), np.nanmedian(T['tps'][s])))
    for a, b in runs(mv):
        print('  MOVE %s-%s %5.0f s' % (w(T['t'][idx[a]]), w(T['t'][idx[b]]), T['t'][idx[b]] - T['t'][idx[a]]))
