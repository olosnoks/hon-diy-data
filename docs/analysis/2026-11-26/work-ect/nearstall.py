import pickle, numpy as np, lib, datetime
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
D = pickle.load(open('drives.pkl', 'rb'))
EV = [(38, '22:39:41'), (39, '22:53:15'), (39, '23:12:37'), (39, '23:25:55'), (39, '23:26:12'), (39, '23:26:24')]
def ts(did, hms):
    t0 = D[did]['rpm'][0][0]; day = datetime.datetime.fromtimestamp(t0).date()
    return datetime.datetime.combine(day, datetime.time.fromisoformat(hms)).timestamp()
fig, axs = plt.subplots(len(EV), 1, figsize=(13, 3.2 * len(EV)))
for ax, (did, hms) in zip(axs, EV):
    d = D[did]; t0 = ts(did, hms)
    ft, rpm = d['rpm']; w = (ft > t0 - 12) & (ft < t0 + 8)
    # exact min
    ww = (ft > t0 - 3) & (ft < t0 + 3); imin = np.argmin(np.where(ww, rpm, 1e9)); tmin = ft[imin]
    print('\n=== d%d %s  rpm min %d at %s.%d' % (did, hms, rpm[imin], lib.wall(tmin), int((tmin % 1) * 10)))
    for name in ('batt', 'alt_fr', 'eld', 'ect', 'brake', 'iac_cmd', 'iacv', 'idle_target'):
        s = d[name]; m = (s[0] > t0 - 6) & (s[0] < t0 + 4)
        print('  %-11s' % name, ' '.join('%+.1f:%.4g' % (x - tmin, y) for x, y in zip(s[0][m], s[1][m])))
    ax.plot(ft[w] - tmin, rpm[w], 'C1', label='rpm')
    ax.plot(ft[w] - tmin, lib.at(d['vss'], ft[w]) * 20, 'C0', label='vss×20')
    ax.plot(ft[w] - tmin, lib.at(d['map'], ft[w]) * 20, 'C2', lw=.8, label='MAP×20')
    a2 = ax.twinx()
    s = d['batt']; m = (s[0] > t0 - 12) & (s[0] < t0 + 8)
    a2.plot(s[0][m] - tmin, s[1][m], 'ko-', ms=4, label='batt V'); a2.set_ylim(12, 15)
    s = d['alt_fr']; a2.plot(s[0][m] - tmin, 12 + s[1][m] / 100 * 3 * 0 + s[1][m] / 33.3, 'm^', ms=4, label='ALT FR (12+x/33)')
    ax.axvline(0, color='r', lw=.5); ax.set_title('d%d near-stall %s (t=0 at rpm minimum %d)' % (did, hms, rpm[imin]), fontsize=9)
    ax.set_ylim(0, 3000); ax.grid(alpha=.3)
axs[0].legend(loc='upper left', fontsize=7); fig.tight_layout(); fig.savefig('nearstall_electrical.png', dpi=85)
