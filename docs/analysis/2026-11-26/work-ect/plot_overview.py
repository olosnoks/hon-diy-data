import pickle, numpy as np, lib, datetime
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
from events import fast
D = pickle.load(open('drives.pkl', 'rb'))
def dt(t): return [datetime.datetime.fromtimestamp(x) for x in t]
def panel(dids, fn, title):
    fig, ax = plt.subplots(6, 1, figsize=(16, 15), sharex=True)
    for did in dids:
        d = D[did]
        te, e = d['ect']
        ax[0].plot(dt(te), e, 'C3', lw=1)
        de = np.abs(np.diff(e)); st = np.where((de >= 4) & (np.diff(te) < 3))[0] + 1
        ax[0].plot(dt(te[st]), e[st], 'kv', ms=6)
        ft, rpm, vss, gear = fast(d)
        ax[1].plot(dt(ft), vss, 'C0', lw=.8); ax1b = ax[1]
        g = np.where(gear > 0, gear * 20, np.nan); ax[1].plot(dt(ft), g, 'C2.', ms=1)
        b = d['brake']; bb = b[1] > 0; ax[1].plot(dt(b[0][bb]), np.full(bb.sum(), -5), 'r|', ms=8)
        ax[2].plot(dt(ft), rpm, 'C1', lw=.6); (ax[2].plot(dt(d['idle_target'][0]), d['idle_target'][1], 'k', lw=.8) if 'idle_target' in d else None)
        ax[3].plot(dt(d['eld'][0]), d['eld'][1], 'C4', lw=.8); ax[3].plot(dt(d['alt_fr'][0]), d['alt_fr'][1], 'C7', lw=.6)
        ax[4].plot(dt(d['batt'][0]), d['batt'][1], 'C5', lw=.8)
        ax[5].plot(dt(d['iat'][0]), d['iat'][1], 'C9', lw=1)
    ax[0].set_ylabel('ECT 0x10 °C\n(v = step ≥4 °C)'); ax[0].axhspan(80, 90, color='g', alpha=.1)
    ax[1].set_ylabel('vss km/h; gear×20 (green)\nbrake (red ticks)')
    ax[2].set_ylabel('rpm; idle target (k)'); ax[2].set_ylim(0, 6000)
    ax[3].set_ylabel('ELD A (purple)\nALT FR % (grey)')
    ax[4].set_ylabel('batt V'); ax[4].set_ylim(12.5, 15); ax[4].axhspan(13.8, 14.6, color='g', alpha=.1)
    ax[5].set_ylabel('IAT °C')
    ax[5].xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
    for a in ax: a.grid(alpha=.3)
    ax[0].set_title(title); fig.tight_layout(); fig.savefig(fn, dpi=90); plt.close(fig)
panel([38, 39], 'overview_drives.png', 'drives 2224 + 2251 (first driving): ECT, speed/gear/brake, rpm, ELD/ALT, battery, IAT')
panel([33, 34, 35, 36, 37, 38, 39], 'overview_evening.png', '2026-11-26 whole evening (idle drives 2031-2158, then the two driving logs)')
