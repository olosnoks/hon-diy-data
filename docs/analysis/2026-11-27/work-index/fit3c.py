"""0x3C: rpm at which P(0x3C > k) = 0.5 (logistic fit per boundary), mean-level curve, fuel-cut check."""
import pandas as pd, numpy as np
from scipy.optimize import minimize
S=pd.read_pickle('S.pkl'); S=S[(S.drive!='drive_2026_11_25_1738')&(S.key=='b3c')&(S.rpm>400)].dropna(subset=['rpm'])
x=S.rpm.values; y=S.val.values
def logit(k):
    z=(y>k).astype(float)
    def nll(p):
        a,s=p; q=1/(1+np.exp(-(x-a)/s)); q=np.clip(q,1e-9,1-1e-9); return -(z*np.log(q)+(1-z)*np.log(1-q)).sum()
    r=minimize(nll,[np.median(x[y==k+1]) if (y==k+1).any() else 3000,200],method='Nelder-Mead')
    return r.x
print('k  rpm50  width(rpm, 10-90% = 4.4*s)  n_above')
rows=[]
for k in range(1,11):
    a,s=logit(k); rows.append((k,a,s)); print(k,round(a),round(4.39*abs(s)),(y>k).sum())
# mean curve: val = 1 + c*(rpm/1000)^p over rpm>600
from scipy.optimize import curve_fit
f=lambda r,c,p: 1+c*(r/1000)**p
po,_=curve_fit(f,x,y,p0=[0.1,2.5]); print('mean fit 1+c*(rpm/1000)^p:',po.round(3), 'rms',np.sqrt(np.mean((y-f(x,*po))**2)).round(2))
for lo,hi in [(1600,2400),(2400,3200)]:
    m=(x>=lo)&(x<hi)
    fc=m&(S['map'].values<20); ld=m&(S['map'].values>50)
    print(lo,hi,'fuel-cut mean %.2f (n=%d)  loaded mean %.2f (n=%d)'%(y[fc].mean(),fc.sum(),y[ld].mean(),ld.sum()), 'rpm', x[fc].mean().round(), x[ld].mean().round())
pd.DataFrame(rows,columns=['k','rpm50','s']).to_csv('fit3c.csv',index=False)
