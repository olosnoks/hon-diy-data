import numpy as np, lib, pickle, datetime
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from common import T, DAY, runs
fan = pickle.load(open('fan.pkl', 'rb'))
G, rows, cols = pickle.load(open('groups.pkl', 'rb'))


def dtm(x): return [datetime.datetime.fromtimestamp(v) for v in np.atleast_1d(x)]
def tt(d, s): return datetime.datetime.strptime('2026-11-%s %s' % (d, s), '%Y-%m-%d %H:%M:%S').timestamp()


C1, C2, C3, C4 = '#2a6fdb', '#e8833a', '#3aa76d', '#8a5cc2'
t = T['t']; did = T['drive']
pre = (did <= 37) | ((did == 38) & (t < tt('26', '22:39:47')))

# ---------- 1. ECT at fan-on + reading distributions
fig, ax = plt.subplots(1, 3, figsize=(15, 4.6), gridspec_kw={'width_ratios': [1.2, 1.2, 1]})
for a, dy in zip(ax[:2], ('11-25', '11-26')):
    m = (DAY == dy) & (T['rpm'] > 400)
    a.plot(dtm(t[m]), T['ect'][m], '.', ms=1, color='#999999', label='ECT reading (1 Hz)')
    F = [o for o in fan if o['day'] == dy and o['dur'] < 30]
    good = [o for o in F if o['raw_range20'] <= 3 and o['ect_on'] > 50]
    bad = [o for o in F if not (o['raw_range20'] <= 3 and o['ect_on'] > 50)]
    a.plot(dtm([o['t'] for o in good]), [o['ect_on'] for o in good], 'o', color=C2, ms=6, label='reading at fan-on (steady)')
    if bad:
        a.plot(dtm([o['t'] for o in bad]), [o['ect_on'] for o in bad], 'x', color=C2, ms=6, label='reading at fan-on (unsteady)')
    a.axhspan(83, 88, color=C3, alpha=0.15, label='fan-on band, good contact')
    if dy == '11-26':
        a.axvline(dtm(tt('26', '22:39:47'))[0], color='k', lw=0.8, ls='--')
        a.text(dtm(tt('26', '22:40:30'))[0], 92, 'first move', fontsize=8)
    a.set_ylim(15, 100); a.set_title('2026-%s: ECT reading and radiator-fan switch-on' % dy, fontsize=10)
    a.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M')); a.set_ylabel('deg C (as read)')
    a.legend(fontsize=7, loc='lower left')
P = [('11-25 parked', (DAY == '11-25')), ('11-26 parked to 22:24', (DAY == '11-26') & (did <= 37)),
     ('11-26 driving + stops', ((did == 38) & (t >= tt('26', '22:39:47'))) | (did == 39))]
bins = np.arange(20, 96, 1.5)
for (l, m), c in zip(P, (C1, C2, C4)):
    m = m & (T['rpm'] > 400)
    ax[2].hist(T['ect'][m], bins=bins, histtype='step', density=True, lw=1.6, color=c, label=l)
ax[2].axvspan(83, 88, color=C3, alpha=0.15)
ax[2].set_xlabel('ECT reading deg C'); ax[2].set_title('Distribution of the reading', fontsize=10); ax[2].legend(fontsize=8)
fig.tight_layout(); fig.savefig('fig1_ect_fanon.png', dpi=110); plt.close(fig)

# ---------- 2. idle rpm vs ECT reading, and target vs ECT
fig, ax = plt.subplots(1, 2, figsize=(14, 5))
park = (np.nan_to_num(T['vss']) < 0.5) & (T['tps'] < 9) & (T['rpm'] > 550) & (T['rpm'] < 1300) & ~(np.nan_to_num(T['ac_clutch']) > 0)
for l, m, c in (('11-25 parked', park & (DAY == '11-25'), C1), ('11-26 parked', park & (DAY == '11-26') & pre, C2),
                ('11-26 stops after driving', park & ~pre & (DAY == '11-26'), C4)):
    j = np.random.default_rng(1).uniform(-0.4, 0.4, m.sum())
    ax[0].plot(T['ect'][m] + j, T['rpm'][m], '.', ms=2 if c != C4 else 4, alpha=0.35 if c != C4 else 0.8, color=c, label=l)
ax[0].axhspan(620, 720, color=C3, alpha=0.12); ax[0].text(22, 625, 'spec 670 +/- 50', fontsize=8)
ax[0].set_xlim(20, 95); ax[0].set_ylim(550, 1300); ax[0].set_xlabel('ECT reading deg C'); ax[0].set_ylabel('idle rpm (1 s mean)')
ax[0].set_title('Steady idle rpm against the ECT reading', fontsize=10); ax[0].legend(fontsize=8, markerscale=4)
er = T['ect_raw']; tg = T['idle_target']; mv = np.nan_to_num(T['vss']) > 0.5
drv = ~pre & (DAY == '11-26')
for l, m, c, mk in (('parked before the drive (d37, d38)', pre & (did >= 37), C2, 'o-'),
                    ('stopped after driving', drv & ~mv, C4, 's-'), ('moving', drv & mv, '#555555', '^-')):
    xs, ys = [], []
    for r in range(40, 70):
        v = tg[m & (er == r)]; v = v[np.isfinite(v)]
        if len(v) >= 3:
            xs.append(lib.ect_poly(r)); ys.append(np.median(v))
    ax[1].plot(xs, ys, mk, color=c, label=l, ms=5)
x = np.arange(40, 70)
ax[1].plot(lib.ect_poly(x), 778 + 13.6 * (x - 48), ':', color='k', lw=1, label='slow-bytes.md fit 778 + 13.6 (raw - 48)')
ax[1].plot([lib.ect_poly(39)], [670], 'k*', ms=10, label='670 at 81.5 C (d35 header)')
ax[1].set_xlabel('ECT reading deg C'); ax[1].set_ylabel('idle target (0x76) rpm')
ax[1].set_title('Idle target against the ECT reading, 2026-11-26', fontsize=10)
ax[1].legend(fontsize=8); ax[1].invert_xaxis()
fig.tight_layout(); fig.savefig('fig2_idle_levels.png', dpi=110); plt.close(fig)

# ---------- 3. before/after the drive time series
fig, ax = plt.subplots(4, 1, figsize=(14, 9), sharex=True)
m = (did >= 37) & (t >= tt('26', '22:20:00'))
tm = dtm(t[m])
ax[0].plot(tm, T['rpm'][m], color=C1, lw=0.7, label='rpm'); ax[0].plot(tm, T['idle_target'][m], color='k', lw=1, label='idle target')
ax[0].set_ylim(400, 1400); ax[0].set_ylabel('rpm')
ax0b = ax[0].twinx(); ax0b.plot(tm, T['vss'][m], color='#bbbbbb', lw=0.8); ax0b.set_ylabel('vss km/h', color='#999999')
ax[0].legend(fontsize=8, loc='upper left')
ax[1].plot(tm, T['iac_cmd'][m], color=C2, lw=0.8, label='iac_cmd (0x29)'); ax[1].set_ylim(30, 130); ax[1].set_ylabel('iac_cmd')
ax1b = ax[1].twinx(); ax1b.plot(tm, T['eld'][m], color=C3, lw=0.8); ax1b.set_ylabel('ELD A', color=C3); ax1b.set_ylim(0, 45)
ax[1].legend(fontsize=8, loc='upper left')
ax[2].plot(tm, T['stft'][m] + T['ltft'][m], color=C4, lw=0.6, label='total trim %'); ax[2].plot(tm, T['ltft'][m], color='k', lw=1.2, label='LTFT %')
ax[2].set_ylim(-15, 35); ax[2].legend(fontsize=8, loc='upper left'); ax[2].set_ylabel('%')
ax[3].plot(tm, T['ect'][m], color=C1, lw=1, label='ECT reading'); ax[3].plot(tm, T['iat'][m], color=C2, lw=1, label='IAT')
ax[3].set_ylabel('deg C'); ax[3].set_ylim(10, 95); ax[3].axhspan(83, 88, color=C3, alpha=0.15); ax[3].legend(fontsize=8, loc='lower left')
for k, (a, b) in {'parked, lights on': ('22:37:40', '22:39:30'), 'gas station': ('22:43:30', '22:45:24'),
                  'hot restart': ('22:51:30', '22:52:50'), 'home': ('23:26:35', '23:26:58')}.items():
    for aa in ax:
        aa.axvspan(dtm(tt('26', a))[0], dtm(tt('26', b))[0], color='#ffd27f', alpha=0.35)
    ax[0].text(dtm(tt('26', a))[0], 1330, k, fontsize=8)
ax[3].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
ax[0].set_title('2026-11-26 22:20-23:27: parked idle, then the two drives (yellow = windows compared)', fontsize=10)
fig.tight_layout(); fig.savefig('fig3_before_after_drive.png', dpi=110); plt.close(fig)

# ---------- 4. learners gated by ECT
fig, ax = plt.subplots(3, 1, figsize=(14, 7.5), sharex=True)
m = (DAY == '11-26') & (did >= 33)
tm = dtm(t[m])
ax[0].plot(tm, T['ect'][m], color=C1, lw=0.7); ax[0].axhline(76.5, color='r', lw=0.8, ls='--')
ax[0].text(tm[5], 79, 'raw 44 = 76.5 C', color='r', fontsize=8)
ax[0].set_ylabel('ECT reading C'); ax[0].set_ylim(15, 95)
ax[1].plot(tm, T['ltft'][m], color='k', lw=1.2); ax[1].set_ylabel('LTFT %')
ax[2].plot(tm, T['x2a'][m], color=C2, lw=1.2, drawstyle='steps-post'); ax[2].set_ylabel('0x2A')
warm = (m & (T['ect_raw'] <= 44) & (T['rpm'] > 400))[m]
for aa in ax:
    for i0, i1 in runs(warm):
        aa.axvspan(tm[i0], tm[i1], color=C3, alpha=0.3, lw=0)
ax[2].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
ax[0].set_title('2026-11-26 20:45-23:27: LTFT and 0x2A move only while the ECT reads >= 76.5 C (green)', fontsize=10)
fig.tight_layout(); fig.savefig('fig4_learners_gated.png', dpi=110); plt.close(fig)

# ---------- 5. d35 810 state at good ECT
fig, ax = plt.subplots(3, 1, figsize=(12, 7), sharex=True)
m = (did == 35) & (t >= tt('26', '21:40:30')) & (t <= tt('26', '21:46:40'))
tm = dtm(t[m])
ax[0].plot(tm, T['rpm'][m], color=C1); ax[0].set_ylabel('rpm'); ax[0].axhline(670, color='k', lw=0.6, ls=':')
ax[1].plot(tm, T['iac_cmd'][m], color=C2, label='iac_cmd'); ax[1].plot(tm, T['x2a'][m] * 10, color=C4, drawstyle='steps-post', label='0x2A x 10')
ax[1].legend(fontsize=8)
ax[2].plot(tm, T['ect'][m], color=C1, label='ECT reading C'); ax[2].plot(tm, T['eld'][m] * 3, color=C3, label='ELD x 3 (A)')
ax[2].legend(fontsize=8); ax[2].set_ylim(0, 100)
for aa in ax:
    aa.axvline(dtm(tt('26', '21:44:17'))[0], color='r', lw=0.8, ls='--')
ax[0].text(dtm(tt('26', '21:44:20'))[0], 700, '21:44:17: iac_cmd 49 -> 84, rpm 670 -> 810\nECT steady at 85.9 C, no ELD/A/C/brake change', fontsize=8, color='r')
ax[0].text(dtm(tt('26', '21:41:28'))[0], 760, 'fan on (feed-forward)', fontsize=8)
ax[2].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
ax[0].set_title('Drive 2137 (good ECT contact): the ~810 rpm state switches on by itself', fontsize=10)
fig.tight_layout(); fig.savefig('fig5_810_state_good_ect.png', dpi=110); plt.close(fig)
print('ok')
