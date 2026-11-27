from common import *
import sys, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
did=int(sys.argv[1]); a,b=float(sys.argv[2])*60,float(sys.argv[3])*60; tag=sys.argv[4]
z=load(did); t=z['t']; m=(t>=a)&(t<=b); tm=t[m]/60
f,ax=plt.subplots(6,1,figsize=(15,13),sharex=True)
ax[0].plot(tm,z['rpm'][m],lw=.7); a2=ax[0].twinx(); a2.plot(tm,z['vss'][m],'r',lw=.7); ax[0].set_ylabel('rpm / vss(r)')
ax[1].plot(tm,z['map'][m],lw=.7); a2=ax[1].twinx(); a2.plot(tm,z['tps'][m],'g',lw=.7); ax[1].set_ylabel('MAP / tps(g)')
ax[2].plot(tm,z['o2'][m],lw=.6); r=z['raw_o2_2']; k=(r[:,0]>=a)&(r[:,0]<=b); ax[2].plot(r[k,0]/60,np.clip(r[k,1],0,1.25),'r.-',ms=4,lw=.5); ax[2].set_ylabel('o2, o2_2(r)')
ax[3].plot(tm,z['stft'][m],lw=.7); ax[3].plot(tm,z['total'][m],lw=.7); ax[3].set_ylabel('stft,total')
ax[4].plot(tm,z['inj'][m],lw=.7); ax[4].set_ylabel('inj')
r=z['raw_closed_loop']; k=(r[:,0]>=a)&(r[:,0]<=b); ax[5].step(r[k,0]/60,r[k,1],where='post',label='cl')
r=z['raw_fuel_status']; k=(r[:,0]>=a)&(r[:,0]<=b); ax[5].step(r[k,0]/60,r[k,1],where='post',label='fs')
r=z['raw_ect']; k=(r[:,0]>=a)&(r[:,0]<=b); a2=ax[5].twinx(); a2.plot(r[k,0]/60,r[k,1],'k.',ms=2); ax[5].legend()
for x in ax: x.grid(alpha=.3)
f.tight_layout(); f.savefig(f'{W}/zoom_{tag}.png',dpi=75)
