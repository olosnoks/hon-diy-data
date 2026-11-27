from lib import *
import numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
import matplotlib.dates as mdates
fmt = mdates.DateFormatter('%H:%M:%S')

# 1. 0x0B b6 operating map + timeline
S = slow_all([38, 39]); S = S[S.rpm > 400]
fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
a = S[S.b6 == 1]; b = S[S.b6 == 0]
ax[0].scatter(a.rpm, a.kpa, s=5, c='#9aa', label='b6 = 1 (%d)' % len(a))
ax[0].scatter(b.rpm, b.kpa, s=22, c='#c33', label='b6 = 0 (%d)' % len(b))
ax[0].set_xlabel('rpm'); ax[0].set_ylabel('MAP kPa'); ax[0].set_title('0x0B bit 6 by operating point, drives 2224+2251 (slow reads)'); ax[0].legend()
F = fast(39); s39 = slow_all([39])
w0, w1 = pd.Timestamp('2026-11-26 23:20:45'), pd.Timestamp('2026-11-26 23:21:15')
f = F[(F.wall > w0) & (F.wall < w1)]; s = s39[(s39.wall > w0) & (s39.wall < w1)]
ax[1].plot(f.wall, f.rpm / 50, lw=1, label='rpm / 50'); ax[1].plot(f.wall, f.kpa, lw=1, label='MAP kPa'); ax[1].plot(f.wall, f.tps, lw=1, label='TPS %')
ax[1].step(s.wall, s.b6 * 20 + 2, where='mid', c='#c33', lw=2, label='0x0B b6 (x20)')
ax[1].step(s.wall, s.cl * 10 + 1, where='mid', c='k', lw=1, label='closed loop (x10)')
ax[1].plot(s.wall, s.eld, 'o-', ms=3, label='ELD A'); ax[1].plot(s.wall, s.altfr, 's-', ms=3, label='ALT FR %')
ax[1].xaxis.set_major_formatter(fmt); ax[1].legend(fontsize=7, ncol=2); ax[1].set_title('23:20:45-23:21:15: pull, then 100 km/h cruise at 87-89 kPa')
fig.tight_layout(); fig.savefig('01_0x0B_b6.png', dpi=110); plt.close(fig)

# 2. 0x2F purge
P = pd.read_pickle('P2F.pkl')
fig, ax = plt.subplots(3, 1, figsize=(13, 10), gridspec_kw={'height_ratios': [2, 1.2, 1.2]})
w0, w1 = pd.Timestamp('2026-11-26 23:21:45'), pd.Timestamp('2026-11-26 23:23:30')
f = F[(F.wall > w0) & (F.wall < w1)]
p = P[(P.did == 39)].copy(); p['wall'] = pd.to_datetime(p.t + LOCALOFF, unit='s'); p = p[(p.wall > w0) & (p.wall < w1)]
ax[0].plot(f.wall, f.stft.rolling(6, center=True).mean(), lw=1, label='STFT % (fast, 0.5 s mean)')
ax[0].plot(f.wall, f.kpa, lw=1, label='MAP kPa')
ax[0].plot(p.wall, p.x2f / 4, 'o-', ms=3, c='#c33', label='0x2F / 4')
ax[0].plot(f.wall, f.o2.rolling(6, center=True).mean() * 20, lw=0.8, c='#888', label='front O2 x20 (V)')
ax[0].axhline(0, c='k', lw=0.5); ax[0].xaxis.set_major_formatter(fmt); ax[0].legend(fontsize=8, ncol=2)
ax[0].set_title('0x2F against STFT at steady cruise (drive 2251)')
M = P[(P.vss > 5) & (P.cl == 1) & (P.fs == 2) & P.w_stft.notna() & P.x2f.notna() & (P.tps_rng < 3)].copy()
bins = [-1, 0, 20, 60, 120, 200, 256]; lab = ['0', '1-20', '21-60', '61-120', '121-200', '201-255']
M['xb'] = pd.cut(M.x2f, bins, labels=lab)
M['cell'] = (M.w_rpm // 250).astype(int).astype(str) + '_' + (M.w_kpa // 5).astype(int).astype(str)
M['res'] = M.w_stft - M.groupby('cell').w_stft.transform('mean')
data = [M[M.xb == l].res.dropna() for l in lab]
ax[1].boxplot(data, tick_labels=lab, showfliers=False); ax[1].axhline(0, c='k', lw=0.5)
ax[1].set_ylabel('STFT minus cell mean (pts)'); ax[1].set_xlabel('0x2F')
ax[1].set_title('STFT in the same rpm/MAP cell, by 0x2F (moving, closed loop, steady throttle; n = %d)' % len(M))
R = P[(P.rpm > 400) & (P.vss > 5) & (P.cl == 1)]
eb = [(0, 50), (51, 53), (54, 55), (56, 56), (57, 57), (58, 58), (59, 60), (61, 80)]
fr = [(R[(R.ect_raw >= lo) & (R.ect_raw <= hi)].x2f > 0).mean() for lo, hi in eb]
lbl = ['raw %d-%d\n(%.0f-%.0f C)' % (lo, hi, ectc(hi), ectc(lo)) for lo, hi in eb]
ax[2].bar(range(len(eb)), fr, color='#c33'); ax[2].set_xticks(range(len(eb))); ax[2].set_xticklabels(lbl, fontsize=8)
ax[2].set_ylabel('share of reads with 0x2F > 0'); ax[2].set_xlabel('ECT reading')
ax[2].set_title('0x2F runs only with the ECT reading above ~66 C (moving, closed loop)')
fig.tight_layout(); fig.savefig('02_0x2F_purge.png', dpi=110); plt.close(fig)

# 3. 0x0C b0 at load steps
S = slow_all().sort_values(['did', 't']).reset_index(drop=True); S = S[S.rpm > 400].copy(); g = S.groupby('did')
for c in ['eld', 'altfr', 'batt', 'iacv', 't']:
    S['d' + c] = g[c].diff()
st = S[(S.dt < 1.6) & (S.deld >= 10) & (S.vss == 0)]
fig, ax = plt.subplots(1, 2, figsize=(12, 4.8))
for k, col in [(1, '#9aa'), (0, '#c33')]:
    q = st[st.c0 == k]
    ax[0].scatter(q.deld, q.daltfr, c=col, s=30 if k == 0 else 12, label='0x0C b0 = %d (n %d)' % (k, len(q)))
    ax[1].scatter(q.deld, q.dbatt, c=col, s=30 if k == 0 else 12, label='0x0C b0 = %d' % k)
ax[0].set_xlabel('ELD step in that read (A)'); ax[0].set_ylabel('ALT FR change (%)'); ax[0].legend(); ax[0].set_title('Electrical load steps >= 10 A at idle')
ax[1].set_xlabel('ELD step in that read (A)'); ax[1].set_ylabel('battery change (V)'); ax[1].legend(); ax[1].set_title('b0 = 0 reads: small ALT FR rise, deep voltage dip')
fig.tight_layout(); fig.savefig('03_0x0C_b0_load_steps.png', dpi=110); plt.close(fig)

# 4. 0x2A vs iac_cmd
S = slow_all([33, 34, 35, 36, 37, 38, 39]).sort_values('t')
fig, ax = plt.subplots(2, 1, figsize=(13, 7))
for did, q in S.groupby('did'):
    ax[0].plot(q.wall, q.iac_cmd, lw=0.5, c='#79a')
    ax[0].step(q.wall, q.x2a * 10 + 30, where='post', c='#c33', lw=1.5)
    ax[0].plot(q.wall, 140 - q.ect_raw, lw=0.5, c='#e90')
ax[0].axhline(102, c='#e90', ls=':', lw=0.8)
ax[0].set_title('0x2A x10 + 30 (red), iac_cmd (blue), 140 - ECT raw (orange; dotted = raw 38, fully warm); 11-26 20:45-23:27', fontsize=9)
ax[0].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
q = S[(S.wall > '2026-11-26 21:36') & (S.wall < '2026-11-26 21:50')]
ax[1].plot(q.wall, q.iac_cmd, lw=0.8, label='iac_cmd 0x29'); ax[1].step(q.wall, q.x2a * 7 + 37, where='post', c='#c33', lw=1.5, label='0x2A x7 + 37')
ax[1].plot(q.wall, q.iacv, lw=0.8, label='iacv 0x28'); ax[1].plot(q.wall, q.rpm / 10, lw=0.6, label='rpm / 10')
ax[1].xaxis.set_major_formatter(fmt); ax[1].legend(fontsize=8)
ax[1].set_title('drive 2137, the one fully warm stretch (ECT raw 35-38): 0x2A follows iac_cmd with a lag')
fig.tight_layout(); fig.savefig('04_0x2A_iac_learn.png', dpi=110); plt.close(fig)

# 5. 0xA4 b0 in the sweep
W = pd.read_csv('../../2026-11-26/work-sweep/car-214932-wide.csv')
W.columns = [c if not c.startswith('b') else 'b' + c[1:].lower() for c in W.columns]
w0 = pd.to_datetime(W.wall); W = decode(W); W['wall'] = w0; W['a4'] = bit(W.ba4, 0)
fig, ax = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
ax[0].plot(W.wall, W.rpm, label='rpm'); ax[0].plot(W.wall, W.tgt, label='idle target 0x76/4')
ax[0].fill_between(W.wall, 780, 780 + 330 * W.a4, step='mid', color='#c33', alpha=0.2, label='0xA4 b0 = 1')
ax[0].legend(fontsize=8); ax[0].set_ylim(780, 1110); ax[0].set_title('0xA4 bit 0 in the 21:49:32-21:53:01 sweep')
ax[1].plot(W.wall, W.ect_raw, '.-', ms=3, label='ECT raw (>= 120: false-cold, ignored by the ECU)')
ax[1].plot(W.wall, W.iac_cmd, label='iac_cmd'); ax[1].plot(W.wall, W.eld * 3, label='ELD A x3')
ax[1].fill_between(W.wall, 0, 190 * W.a4, step='mid', color='#c33', alpha=0.15)
ax[1].legend(fontsize=8); ax[1].xaxis.set_major_formatter(fmt)
fig.tight_layout(); fig.savefig('05_0xA4_b0_sweep.png', dpi=110); plt.close(fig)
print('ok')
