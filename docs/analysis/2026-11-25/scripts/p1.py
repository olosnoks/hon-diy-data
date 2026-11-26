import pickle,numpy as np,datetime as dt
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb')); tz=ZoneInfo('America/Chicago')
def T(a): return [dt.datetime.fromtimestamp(x,tz) for x in a]
def ts(h): return dt.datetime.fromisoformat('2026-11-25T'+h).replace(tzinfo=tz).timestamp()
import sys
a,b,name=sys.argv[1],sys.argv[2],sys.argv[3]
A,B=ts(a),ts(b)
chs=[('ect','ECT °C (RAM 0x10)'),('rpm','RPM'),('iacv','IACV %'),('ign','Ign ° BTDC'),('stft','STFT %'),('map','MAP kPa'),('eld','ELD A')]
fig,ax=plt.subplots(len(chs),1,figsize=(14,2*len(chs)),sharex=True)
for k,(c,l) in enumerate(chs):
    X=R['ecu:'+c]; m=(X[:,0]>=A)&(X[:,0]<=B)
    ax[k].plot(T(X[m,0]),X[m,1],lw=0.5 if c in('rpm','map','ign','stft') else 0.9)
    if c=='ect' and 'obd2:ect' in R:
        Y=R['obd2:ect']; mm=(Y[:,0]>=A)&(Y[:,0]<=B)
        if mm.any(): ax[k].plot(T(Y[mm,0]),Y[mm,1],'r.',ms=2,label='OBD2 PID05'); ax[k].legend(loc='lower left')
    if c=='rpm': ax[k].axhline(500,color='r',ls=':')
    if c=='ect': ax[k].axhspan(80,90,color='g',alpha=0.1)
    ax[k].set_ylabel(l,fontsize=8); ax[k].grid(alpha=.3)
ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M:%S',tz=tz))
plt.tight_layout(); plt.savefig('/home/claude/analysis/plots-idle/'+name,dpi=90)
