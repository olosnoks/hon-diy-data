import numpy as np, pickle, datetime, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
from rule import keep
T = pickle.load(open('T2.pkl', 'rb')); RL = pickle.load(open('rule.pkl', 'rb')); R = pickle.load(open('resets.pkl', 'rb'))
C = pickle.load(open('ctx.pkl', 'rb')); TT = pickle.load(open('trim_tsr.pkl', 'rb'))
t = T['t']; dt = np.array([datetime.datetime.fromtimestamp(x) for x in t])
def D(x): return datetime.datetime.fromtimestamp(x)
fmt = md.DateFormatter('%H:%M')
# fig1: 11-25 timeline
fig, ax = plt.subplots(3, 1, figsize=(15, 9), sharex=True, gridspec_kw=dict(height_ratios=[3, 1.3, 1.3]))
m = (T['drive'] <= 30) & (T['drive'] >= 16)
ax[0].plot(dt[m], np.clip(T['rpm'][m], 500, 1200), lw=0.5, color='0.4', label='rpm (1 Hz)')
pred = RL['tsr'] >= 450
ax[0].fill_between(dt[m], 500, np.where(pred[m], 1200, 500), color='tab:orange', alpha=0.12, step='mid', label='rule: >= 450 s since last reset')
for x in R:
    if keep(x[1]) and m.any() and t[m][0] <= x[0] <= t[m][-1]: ax[0].axvline(D(x[0]), color='tab:red', lw=0.6, alpha=0.6)
ax[0].axvline(D(t[m][0]), color='tab:red', lw=0.6, alpha=0.6, label='reset: start / rpm>1000 / accepted ECT<68 C')
for f in C['fans']:
    if t[m][0] <= f[0] <= t[m][-1] and f[1] - f[0] < 90: ax[0].axvspan(D(f[0]), D(f[1]), color='tab:blue', alpha=0.25, lw=0)
ax[0].set_ylabel('rpm'); ax[0].set_ylim(500, 1200); ax[0].legend(loc='upper left', fontsize=8)
ax[0].set_title('11-25 parked idle: the 810 state (orange = rule prediction; blue = radiator fan cycles)')
ax[1].plot(dt[m], np.minimum(RL['tsr'][m], 1500), lw=0.8, color='k'); ax[1].axhline(450, color='tab:orange', ls='--'); ax[1].set_ylabel('s since reset')
ax[2].plot(dt[m], np.clip(T['b10'][m], 25, 130), lw=0.6, color='tab:green'); ax[2].axhline(56, color='tab:red', ls=':'); ax[2].set_ylabel('ECT raw (56 = 67 C)')
ax[2].xaxis.set_major_formatter(fmt)
fig.tight_layout(); fig.savefig('fig1_1125_timeline_rule.png', dpi=110); plt.close(fig)
# fig2: trims vs time since reset
fig, ax = plt.subplots(1, 1, figsize=(8, 4.5))
for st, c in (('L', 'tab:blue'), ('M', 'tab:orange')):
    rows = [r for r in TT if r[0] == st]
    x = [min((r[1] + min(r[2], 2400)) / 2, 2000) for r in rows]
    ax.plot(x, [r[4] for r in rows], 'o-', color=c, label='%s state: total trim (STFT+LTFT)' % st)
    ax.plot(x, [r[5] for r in rows], 's--', color=c, alpha=0.6, label='%s: residual vs rpm-only fit' % st)
ax.axvspan(410, 590, color='0.85', label='observed entry delays (9 events)')
ax.set_xlabel('seconds since last reset (start, rev, accepted ECT < 68 C)'); ax.set_ylabel('% trim'); ax.set_xscale('symlog', linthresh=600)
ax.legend(fontsize=7); ax.set_title('11-25: mixture goes rich at the same 670 rpm before the 810 step')
fig.tight_layout(); fig.savefig('fig2_trim_vs_time_since_reset.png', dpi=110); plt.close(fig)
# fig3: the two 11-26 entries with 0x2F
def ts(s): return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').timestamp()
fig, axs = plt.subplots(4, 2, figsize=(15, 9), sharex='col')
for col, (a, b, e) in enumerate([('2026-11-26 21:34:30', '2026-11-26 21:47:00', '2026-11-26 21:44:17'), ('2026-11-26 22:08:30', '2026-11-26 22:21:00', '2026-11-26 22:18:06')]):
    m = (t >= ts(a)) & (t <= ts(b))
    x = dt[m]
    axs[0, col].plot(x, T['rpm'][m], color='0.3', lw=0.8, label='rpm'); axs[0, col].plot(x, T['target'][m], color='tab:red', lw=1, label='idle target')
    axs[0, col].set_ylim(600, 1000); axs[0, col].legend(fontsize=7)
    axs[1, col].plot(x, T['x2f'][m], color='tab:purple', drawstyle='steps-post'); axs[1, col].set_ylabel('0x2F')
    axs[2, col].plot(x, T['stft'][m] + T['ltft'][m], color='tab:brown', lw=0.6); axs[2, col].set_ylabel('STFT+LTFT %')
    axs[3, col].plot(x, T['iac_cmd'][m], color='tab:green'); axs[3, col].set_ylabel('iac_cmd 0x29')
    ax2 = axs[3, col].twinx(); ax2.plot(x, T['eld'][m], color='tab:blue', lw=0.5); ax2.set_ylabel('ELD A', color='tab:blue')
    for r in range(4): axs[r, col].axvline(D(ts(e)), color='k', ls=':', lw=0.8)
    for f in C['fans']:
        if ts(a) <= f[0] <= ts(b):
            for r in range(4): axs[r, col].axvspan(D(f[0]), D(f[1]), color='tab:blue', alpha=0.15, lw=0)
    axs[3, col].xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
axs[0, 0].set_title('d35 (ECT reading good): 810 step 5 s after 0x2F reaches 27')
axs[0, 1].set_title('d37 (ECT reading low): target to 810 at raw 48, 12 s after 0x2F reaches 25')
fig.tight_layout(); fig.savefig('fig3_1126_entries_0x2F.png', dpi=110); plt.close(fig)
print('ok')
