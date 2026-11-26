import pickle,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb')); tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M')
e=R['ecu:ect']; rpm=R['ecu:rpm']; tps=R['ecu:tps']; iac=R['ecu:iacv']; ign=R['ecu:ign']; st=R['ecu:stft']; mp=R['ecu:map']; inj=R['ecu:inj']
def at(X,t):
    i=np.clip(np.searchsorted(X[:,0],t),1,len(X)-1); return X[i,1]
t=e[:,0]; v=e[:,1]
m=t>0
r=np.array([np.median(rpm[(rpm[:,0]>=x-0.5)&(rpm[:,0]<x+0.5),1]) if ((rpm[:,0]>=x-0.5)&(rpm[:,0]<x+0.5)).any() else np.nan for x in t])
tp=at(tps,t); ia=at(iac,t); ig=at(ign,t); s=at(st,t); mm=at(mp,t); ij=at(inj,t)
idle=(tp<9)&(r>400)&(e[:,2]>=12)  # after tps fix
cls=np.where(v<55,'C',np.where(v<76,'P','W'))
# true-warm reference only (ect 76-95), and no cold in prior 120 s
coldt=t[cls=='C']
recent=np.array([((coldt>x-120)&(coldt<=x)).any() for x in t])
for name,mask in [('W clean (no C in 120s)',idle&(cls=='W')&~recent),('W after recent C',idle&(cls=='W')&recent),('P',idle&(cls=='P')),('C',idle&(cls=='C'))]:
    rr=r[mask]
    print('%-26s n=%5d rpm med %4.0f p5 %4.0f p95 %4.0f | >1000: %4.1f%% | <720: %4.1f%% | iacv med %.1f | ign med %.1f | stft med %.1f | inj %.2f | map %.1f'%(name,mask.sum(),np.median(rr),np.percentile(rr,5),np.percentile(rr,95),100*np.mean(rr>1000),100*np.mean(rr<720),np.median(ia[mask]),np.median(ig[mask]),np.median(s[mask]),np.median(ij[mask]),np.median(mm[mask])))
pickle.dump(dict(t=t,r=r,ia=ia,ig=ig,s=s,idle=idle,cls=cls,recent=recent),open('tier.pkl','wb'))
# per 10 min summary for clean warm idle
W=idle&(cls=='W')&~recent
b=(t//600)*600
for x in np.unique(b[W]):
    k=W&(b==x); print(f(x),'n%4d rpm %4.0f iacv %4.1f ign %4.1f'%(k.sum(),np.median(r[k]),np.median(ia[k]),np.median(ig[k])),end=' | ')
