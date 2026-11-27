import sqlite3,numpy as np
from load import *
c=sqlite3.connect(DB,uri=True)
r=load(c,39)
addrs=sorted(r)
# for each address print values for reads in [1591,1625]
for a in addrs:
    t,v,s=r[a]
    m=(t>1591)&(t<1625)
    if m.sum()==0: continue
    # compress: per second take last value
    sec={}
    for tt,vv in zip(t[m],v[m]): sec[int(tt)]=vv
    vals=[sec.get(i,None) for i in range(1592,1625)]
    if len(set(x for x in vals if x is not None))>1:
        print(f'{a:#04x}',' '.join('  .' if x is None else f'{x:3d}' for x in vals))
print('secs ', ' '.join(f'{i%100:3d}' for i in range(1592,1625)))
ev=c.execute("select t,kind,text from events where drive_id=39 and t<1700").fetchall(); print(ev)
