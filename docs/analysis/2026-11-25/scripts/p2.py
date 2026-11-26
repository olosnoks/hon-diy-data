import pickle,numpy as np,datetime as dt
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb')); tz=ZoneInfo('America/Chicago')
def ts(h): return dt.datetime.fromisoformat('2026-11-25T'+h).replace(tzinfo=tz).timestamp()
caps=[('18:17:01.9','obd2',4,'18:17 (known key-off)'),('18:55:15.5','ecu',15,'18:55'),('19:29:07.7','ecu',20,'19:29'),('19:35:01.1','ecu',21,'19:35'),('20:13:45.7','ecu',22,'20:13'),('21:16:10.7','ecu',27,'21:16')]
fig,ax=plt.subplots(2,3,figsize=(15,7)); ax=ax.ravel()
for k,(h,s,d,l) in enumerate(caps):
    t0=ts(h); X=R[s+':rpm']; m=(X[:,0]>t0-30)&(X[:,0]<t0+300)
    ax[k].plot(X[m,0]-t0,X[m,1],'.',ms=2)
    if s=='ecu':
        L=R['ecu:link']; mm=(L[:,0]>t0-30)&(L[:,0]<t0+300); ax[k].plot(L[mm,0]-t0,L[mm,1]*300,'r|',ms=6,label='link (x300)')
    ax[k].set_title('capture '+l); ax[k].set_xlabel('s from trigger'); ax[k].set_ylim(-50,1700); ax[k].axhline(500,ls=':',color='r'); ax[k].grid(alpha=.3)
plt.tight_layout(); plt.savefig('/home/claude/analysis/plots-idle/07_link_lost_captures.png',dpi=85)
