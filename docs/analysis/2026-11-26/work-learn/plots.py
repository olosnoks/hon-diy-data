import pickle, numpy as np, datetime, csv
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
G = pickle.load(open('grid.pkl','rb')); S, drives = pickle.load(open('series.pkl','rb'))
d0=datetime.datetime.fromtimestamp(G['t'][0]).date()
ts=lambda h: datetime.datetime.combine(d0, datetime.time.fromisoformat(h)).timestamp()
D=lambda arr: [datetime.datetime.fromtimestamp(x) for x in arr]
# p4: 0x2F episodes
m=G['t']>=ts('21:14:50'); T=D(G['t'][m])
fig,ax=plt.subplots(3,1,figsize=(16,9),sharex=True)
ax[0].plot(T,G[0x10][m],lw=.6,c='C0',label='ECT raw 0x10 (high = cold)')
for y,c,l in ((53,'g','0x2F starts after ~25-50 s at or below ~53 (69 °C)'),(58,'r','0x2F resets at >= ~58 (65 °C)'),(115,'k','readings above ~115 (< 40 °C) are ignored by the ECU')):
    ax[0].axhline(y,c=c,lw=.8,ls='--',label=l)
ax[0].set_ylim(30,190); ax[0].legend(loc='upper right',fontsize=8)
ax[1].step(T,G[0x2F][m],where='post',c='C4',label='0x2F'); ax[1].legend(loc='upper left')
ax[2].plot(T,G['rpm'][m],lw=.5,label='rpm'); ax[2].plot(T,G['tgt'][m],lw=1,label='idle_target'); ax[2].legend(loc='upper left')
for a in ax: a.grid(alpha=.3)
ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M')); plt.tight_layout(); plt.savefig('x2f_episodes.png',dpi=75); plt.close()
# p5: idle target vs ECT
th,vh,_=S[0x76]; tl,vl,_=S[0x77]; tg=(vh*256+vl)/4
def at(a,tq):
    t,v,_=S[a]; i=np.searchsorted(t,tq,side='right')-1; return v[np.clip(i,0,None)]
e0=at(0x10,th); e3=at(0x10,th-3); e6=at(0x10,th-6); st=(np.abs(e0-e3)<=1)&(np.abs(e0-e6)<=1)
fig,ax=plt.subplots(1,2,figsize=(16,6),gridspec_kw={'width_ratios':[1,2]})
rng=np.random.default_rng(0)
ax[0].scatter(e0[st]+rng.uniform(-.3,.3,st.sum()),tg[st],s=3,alpha=.3,label='stable ECT (no change >1 in 6 s)')
ax[0].scatter(e0[~st&(e0<100)],tg[~st&(e0<100)],s=3,alpha=.3,c='C1',label='ECT changing')
x=np.arange(46,70); ax[0].plot(x,778+13.6*(x-48),'k--',lw=1,label='778 + 13.6 x (raw - 48)')
ax[0].scatter([39],[670],marker='*',s=150,c='r',label='drive 2137 header (raw 39): 670')
ax[0].set_xlabel('ECT raw 0x10'); ax[0].set_ylabel('idle target rpm (0x76:77 / 4)'); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
T=D(th); ax[1].plot(T,tg,lw=1,label='idle_target'); ax[1].plot(T,np.clip(778+13.6*(e0-48),670,1070),lw=.6,alpha=.7,label='778+13.6(raw-48), clipped 670-1070, from the raw ECT read')
rt,rv,_=S['rpm']; mm=rt>=th[0]; ax[1].plot(D(rt[mm]),rv[mm],lw=.3,alpha=.5,label='rpm')
ax[1].legend(loc='upper right',fontsize=8); ax[1].grid(alpha=.3); ax[1].xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
plt.tight_layout(); plt.savefig('idle_target_vs_ect.png',dpi=75); plt.close()
# p6: 0x86 in sweep
rows=list(csv.DictReader(open(r'..\work-sweep\car-214932-wide.csv')))
B=np.array([[int(r['b%02X'%a]) for a in range(256)] for r in rows]); W=[datetime.datetime.fromisoformat(r['wall']) for r in rows]
s8=lambda x: np.where(x>127,x-256,x)
fig,ax=plt.subplots(3,1,figsize=(16,8),sharex=True)
ax[0].step(W,s8(B[:,0x86]),where='post',c='C3',label='0x86 (signed)'); ax[0].legend(loc='lower left')
ax[1].plot(W,B[:,0x15]/51.3,lw=.7,label='O2 V (read ~0.3 s before 0x86)'); ax[1].scatter(np.array(W)[B[:,0x86]==249],B[B[:,0x86]==249,0x15]/51.3,c='r',s=12,zorder=3,label='sweeps with 0x86 = -7'); ax[1].legend(loc='lower left')
ax[2].plot(W,(B[:,0x20]/128-1)*100,lw=.7,label='STFT %'); ax[2].plot(W,(B[:,0x22]/128-1)*100,lw=1,label='LTFT %'); ax[2].legend(loc='lower left')
for a in ax: a.grid(alpha=.3)
ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M:%S')); plt.tight_layout(); plt.savefig('sweep_0x86.png',dpi=75); plt.close()
