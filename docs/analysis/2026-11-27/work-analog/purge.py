import sqlite3,numpy as np
from load import *
c=sqlite3.connect(DB,uri=True)
E=[];P=[];V=[];EM=[]
for did in range(33,40):
    r=load(c,did)
    t,p,_=r[0x2F]
    e,_=at(r,0x10,t); vs,_=at(r,0x02,t)
    # max ECT raw (coldest) over preceding 20 s of reads
    te,ve,_=r[0x10]
    em=np.array([ve[(te>tt-20)&(te<=tt)].max() if ((te>tt-20)&(te<=tt)).any() else np.nan for tt in t])
    E.append(e);P.append(p);V.append(vs);EM.append(em)
E,P,V,EM=map(np.concatenate,(E,P,V,EM))
np.savez('purge.npz',E=E,P=P,V=V,EM=EM)
print('stationary, purge>0 fraction by ECT raw (instant) / by max ECT raw over prior 20 s')
for e in range(44,62):
    a=(V==0)&(E==e); b=(V==0)&(EM==e)
    print(e,f'{ECT(e):.1f}C', a.sum(), round(P[a].astype(bool).mean(),2) if a.sum() else '-', '|', b.sum(), round(P[b].astype(bool).mean(),2) if b.sum() else '-')
