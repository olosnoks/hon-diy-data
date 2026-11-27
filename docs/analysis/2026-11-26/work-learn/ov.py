import pickle, datetime, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
G = pickle.load(open('grid.pkl','rb'))
T = [datetime.datetime.fromtimestamp(x) for x in G['t']]
fig, ax = plt.subplots(8, 1, figsize=(16, 18), sharex=True)
ax[0].plot(T, G['rpm'], lw=.5, label='rpm'); ax[0].plot(T, G['tgt'], lw=1, label='idle_target 0x76/4'); ax[0].legend(loc='upper left'); ax[0].set_ylim(500,1300)
ax[1].plot(T, G['ect'], lw=.6, label='ECT °C'); ax[1].plot(T, G['iat'], lw=1, label='IAT °C'); ax[1].legend(loc='upper left')
ax[2].step(T, G[0x2A], where='post', c='C3', label='0x2A'); ax[2].legend(loc='upper left')
ax[3].step(T, G[0x2F], where='post', c='C4', label='0x2F'); ax[3].legend(loc='upper left')
ax[4].plot(T, G['stft'], lw=.4, label='stft %'); ax[4].plot(T, G['ltft'], lw=1.2, label='ltft %'); ax[4].legend(loc='upper left')
ax[5].plot(T, G[0x29], lw=.5, label='iac_cmd 0x29'); ax[5].plot(T, G[0x28], lw=.5, label='iacv 0x28'); ax[5].legend(loc='upper left')
ax[6].plot(T, G['eld'], lw=.5, label='eld A'); ax[6].plot(T, G['batt']*2, lw=.5, label='batt V x2'); ax[6].legend(loc='upper left')
ax[7].plot(T, G[0x27], lw=.5, label='0x27'); ax[7].plot(T, G[0x0F], lw=.8, label='0x0F'); ax[7].plot(T, G['drive']-30, lw=1, label='drive id-30'); ax[7].legend(loc='upper left')
for a in ax: a.grid(alpha=.3)
ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
plt.tight_layout(); plt.savefig('overview_evening.png', dpi=80)
