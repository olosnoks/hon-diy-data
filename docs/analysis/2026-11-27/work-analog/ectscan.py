import sqlite3,numpy as np
from load import *
c=sqlite3.connect(DB,uri=True)
rows={}
for did,name,iso,ep in drives(c):
    if did<33: continue
    r=load(c,did)
    if 0x10 not in r: continue
    for a,(t,v,s) in r.items():
        if a in (0x10,): continue
        e,dt=at(r,0x10,t)
        ok=dt<0.6
        rows.setdefault(a,[]).append((e[ok],v[ok]))
res=[]
for a,L in rows.items():
    e=np.concatenate([x[0] for x in L]); v=np.concatenate([x[1] for x in L]).astype(int)
    if len(e)<500: continue
    for b in range(8):
        y=(v>>b)&1
        if y.min()==y.max(): continue
        best=None
        for th in range(20,120):
            lo=e<th; hi=~lo
            if lo.sum()<50 or hi.sum()<50: continue
            p1,p2=y[lo].mean(),y[hi].mean()
            sc=abs(p1-p2)
            if best is None or sc>best[0]: best=(sc,th,p1,p2,lo.sum(),hi.sum())
        if best and best[0]>0.5: res.append((best[0],a,b,best[1:]))
for sc,a,b,info in sorted(res,reverse=True)[:40]:
    print(f'{a:#04x} b{b} split ect<{info[0]}: p={info[1]:.2f} vs {info[2]:.2f} (n {info[3]}/{info[4]}) score {sc:.2f}')
