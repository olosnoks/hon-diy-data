import pickle, numpy as np, datetime
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
exec(open('fit1.py').read().split('best=[]')[0])
hold = sim(x29, 0, 1, 1e12, 1e12, 0, 'round')
print('hold baseline', score(hold))
p1 = sim(x28,0,2.5,40,8000,38,'round'); print('x28 best', score(p1))
p2 = sim(x29,39,8,20,8000,38,'floor'); print('x29 best', score(p2))
p3 = sim(x29,39,8,20,1e12,38,'floor'); print('x29 warm only', score(p3))
T=[datetime.datetime.fromtimestamp(v) for v in t]
fig,ax=plt.subplots(3,1,figsize=(16,9),sharex=True)
ax[0].step(T,obs,where='post',lw=2,c='k',label='0x2A observed'); ax[0].step(T,p1,where='post',label='filter of iacv 0x28 (c=0,s=2.5,tau 40 s warm / 8000 s)'); ax[0].step(T,p3,where='post',ls='--',label='filter of 0x29 warm-only'); ax[0].legend(loc='lower left',fontsize=8)
ax[1].plot(T,x29,lw=.4,label='0x29'); ax[1].plot(T,x28,lw=.4,label='0x28'); ax[1].legend(loc='upper left')
ax[2].plot(T,ect,lw=.5,label='ECT raw'); ax[2].axhline(38,c='r',lw=.5); ax[2].set_ylim(30,90); ax[2].legend(loc='upper left')
for a in ax: a.grid(alpha=.3)
ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
plt.tight_layout(); plt.savefig('fit_2a_filter.png',dpi=75)
