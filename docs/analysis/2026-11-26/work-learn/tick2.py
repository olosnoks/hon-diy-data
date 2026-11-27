import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
t,v,d=S[0x2F]; rt,rv,_=S['rpm']; mt,mv,_=S[0x12]
i=np.nonzero(np.diff(v)!=0)[0]+1
inc=[(t[k-1],t[k],v[k-1],v[k]) for k in i if t[k]-t[k-1]<3 and v[k]>v[k-1]]
def integ(tt,vv,a,b):
    m=(tt>=a)&(tt<=b); x=tt[m]; y=vv[m]
    return np.trapezoid(y,x) if len(x)>2 else np.nan
rows=[]
prev=None
for a0,a1,b,c in inc:
    tick=(a0+a1)/2
    if b==0: prev=tick; continue
    if prev is not None and tick-prev<60:
        dt=tick-prev; rev=integ(rt,rv,prev,tick)/60; air=integ(rt,rv*np.interp(rt,mt,mv),prev,tick)/60
        rows.append((dt,rev,air, np.mean(rv[(rt>=prev)&(rt<=tick)])))
    prev=tick
R=np.array(rows)
print('n',len(R))
for nm,col in (('seconds',0),('revs',1),('rev*MAPraw',2)):
    x=R[:,col]; print(nm,'mean %.1f sd %.1f cv %.3f'%(x.mean(),x.std(),x.std()/x.mean()))
print('corr dt vs 1/rpm', np.corrcoef(R[:,0],1/R[:,3])[0,1])
for r in R: print('dt %.1f  revs %.0f  mean rpm %.0f'%(r[0],r[1],r[3]))
