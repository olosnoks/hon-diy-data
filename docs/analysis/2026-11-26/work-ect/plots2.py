import pickle, numpy as np, lib, datetime
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
rows, allser = pickle.load(open('ect_all.pkl', 'rb')); A = pickle.load(open('corr.pkl', 'rb')); D = pickle.load(open('drives.pkl', 'rb'))
def dt(t): return [datetime.datetime.fromtimestamp(x) for x in t]
# 1 per-drive rates
fig, ax = plt.subplots(2, 2, figsize=(15, 10))
R = [r for r in rows if r[2] >= 0.05]
lab = [r[1].replace('drive_2026_', '').replace('_', ' ') for r in R]
x = np.arange(len(R))
ax[0, 0].bar(x - .2, [r[3] / r[2] for r in R], .4, label='edges ≥15 °C /h (false-cold jumps)')
ax[0, 0].bar(x + .2, [r[4] / r[2] / 4 for r in R], .4, label='steps ≥4 °C /h ÷4')
for i, r in enumerate(R):
    if r[7] > .05: ax[0, 0].text(i, 5, 'moving %d%%' % (100 * r[7]), rotation=90, ha='center', fontsize=8, color='w')
ax[0, 0].set_xticks(x); ax[0, 0].set_xticklabels(lab, rotation=70, fontsize=7); ax[0, 0].legend(fontsize=8); ax[0, 0].set_title('ECT jump rate per drive (11-25 and 11-26; last two = driving)')
# 2 rate by speed
mv = A['vss']; st4 = np.abs(A['de']) >= 4; st2 = np.abs(A['dr']) >= 2
bins = [(-1, .5, 'stopped'), (.5, 30, '1-30'), (30, 60, '30-60'), (60, 85, '60-85'), (85, 200, '85-107')]
h = [((mv > a) & (mv <= b)).sum() / 3600 for a, b, _ in bins]
r4 = [(st4 & (mv > a) & (mv <= b)).sum() / hh for (a, b, _), hh in zip(bins, h)]
r2 = [(st2 & (mv > a) & (mv <= b)).sum() / hh for (a, b, _), hh in zip(bins, h)]
xx = np.arange(len(bins)); ax[0, 1].bar(xx - .2, r4, .4, label='steps ≥4 °C /h'); ax[0, 1].bar(xx + .2, r2, .4, label='raw changes ≥2 counts /h')
ax[0, 1].set_xticks(xx); ax[0, 1].set_xticklabels(['%s km/h\n%.2f h, n4=%d' % (b[2], hh, (st4 & (mv > b[0]) & (mv <= b[1])).sum()) for b, hh in zip(bins, h)], fontsize=8)
ax[0, 1].axhline(33, color='r', ls='--', lw=1); ax[0, 1].text(3.5, 34, 'idle-only 11-26 rate of ≥15 °C jumps: 33/h', color='r', fontsize=8, ha='center')
ax[0, 1].legend(fontsize=8); ax[0, 1].set_title('drives 2224+2251: ECT step rate by vehicle speed (no ≥15 °C jumps at all)')
# 3 idle target vs ECT reading
for did, c in ((37, 'C0'), (38, 'C1'), (39, 'C2')):
    d = D[did]; tt, tg = d['idle_target']; e = lib.at(d['ect'], tt - 1.5)
    ax[1, 0].plot(e + np.random.uniform(-.3, .3, len(e)), tg, '.', ms=1, color=c, label='d%d' % did)
ee = np.arange(55, 84); rr = np.array([np.argmin(np.abs(lib.ect_poly(np.arange(20, 120)) - x)) + 20 for x in ee])
ax[1, 0].plot(ee, 778 + 13.6 * (rr - 48), 'k--', lw=1, label='slow-bytes fit 778+13.6(raw-48)')
ax[1, 0].axvspan(80, 90, color='g', alpha=.1); ax[1, 0].axhline(670, color='g', lw=1); ax[1, 0].text(81, 680, 'true warm: ~670 rpm (spec 670±50)', fontsize=8, color='g')
ax[1, 0].set_xlabel('ECT reading °C'); ax[1, 0].set_ylabel('idle target rpm'); ax[1, 0].legend(fontsize=8, markerscale=8); ax[1, 0].set_title('what the low ECT reading does: idle target 750-1010 instead of ~670')
# 4 battery vs rpm
for did, c in ((38, 'C1'), (39, 'C2')):
    d = D[did]; tb, b = d['batt']; r = lib.at(d['rpm'], tb)
    ax[1, 1].plot(r, b + np.random.uniform(-.03, .03, len(b)), '.', ms=2, color=c, label='d%d' % did)
ax[1, 1].axhspan(13.8, 14.6, color='g', alpha=.1); ax[1, 1].set_xlabel('rpm'); ax[1, 1].set_ylabel('batt V (0x17/10.45)')
ax[1, 1].set_title('battery vs rpm: flat 13.9-14.3 V from 750 to 5400 rpm; dips only at <560 rpm'); ax[1, 1].legend(fontsize=8, markerscale=5); ax[1, 1].set_ylim(12.5, 14.8)
fig.tight_layout(); fig.savefig('summary_stats.png', dpi=85); plt.close(fig)
# zoom: drive 38 first minutes of driving
def T(h): return datetime.datetime.combine(datetime.date(2026, 11, 26), datetime.time.fromisoformat(h)).timestamp()
for did, a, b, fn in ((38, '22:39:30', '22:44:00', 'zoom_2240_ect_steps.png'), (39, '23:09:30', '23:13:00', 'zoom_2310_ect_steps.png')):
    d = D[did]; fig, ax = plt.subplots(4, 1, figsize=(13, 10), sharex=True)
    def sel(n): t, v = d[n]; m = (t > T(a)) & (t < T(b)); return dt(t[m]), v[m]
    ax[0].step(*sel('ect'), where='post', color='C3'); ax[0].set_ylabel('ECT °C')
    ax[1].plot(*sel('rpm'), 'C1', lw=.7); ax[1].plot(*sel('idle_target'), 'k'); ax[1].set_ylabel('rpm / target')
    t, v = sel('vss'); ax[2].plot(t, v, 'C0'); tb, bb = sel('brake'); ax[2].plot(tb, np.array(bb) * 10, 'r.', ms=4); ax[2].set_ylabel('vss; brake×10')
    ax[3].plot(*sel('stft'), 'C2', lw=.7); ax[3].plot(*sel('ign'), 'C4', lw=.7); ax[3].set_ylabel('stft % (green), ign ° (purple)')
    ax[3].xaxis.set_major_formatter(md.DateFormatter('%H:%M:%S'))
    for x in ax: x.grid(alpha=.3)
    ax[0].set_title('drive %d: ECT steps while driving and the idle target following them' % did)
    fig.tight_layout(); fig.savefig(fn, dpi=85); plt.close(fig)
