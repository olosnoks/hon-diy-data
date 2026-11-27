import sqlite3,numpy as np
from load import *
c=sqlite3.connect(DB,uri=True)
E=[];T=[];V=[];A=[]
for did in (37,38,39):
    r=load(c,did)
    t,hi,_=r[0x76]; _,lo,_=r[0x77]
    tgt=(hi.astype(int)*256+lo)/4
    e,_=at(r,0x10,t); vs,_=at(r,0x02,t); ac,_=at(r,0x0B,t)
    E.append(e);T.append(tgt);V.append(vs);A.append(ac)
E=np.concatenate(E);T=np.concatenate(T);V=np.concatenate(V);A=np.concatenate(A).astype(int)
m=(V==0)&(((A>>1)&1)==0)
for e in range(36,70):
    k=m&(E==e)
    if k.sum()>10: print(e,f'{ECT(e):.1f}C',k.sum(),'median tgt',np.median(T[k]),'p10/p90',np.percentile(T[k],[10,90]))
print('all conditions')
for e in range(30,50):
    k=(E==e)
    if k.sum()>3:
        u,n=np.unique(T[k],return_counts=True); o=np.argsort(-n)[:4]
        print(e,f'{ECT(e):.1f}C',k.sum(),' '.join(f'{u[i]}:{n[i]}' for i in o))
print('by vss>0 vs 0 and ect<=45')
for cond,name in [((V>0)&(E<=45),'moving ect<=45'),((V>0)&(E>45)&(E<=50),'moving ect46-50'),((V==0)&(E<=45),'stopped ect<=45'),((V==0)&(E>45)&(E<=50),'stopped 46-50')]:
    u,n=np.unique(T[cond],return_counts=True); o=np.argsort(-n)[:4]
    print(name,cond.sum(),' '.join(f'{u[i]}:{n[i]}' for i in o))
