import sqlite3,numpy as np
from load import *
c=sqlite3.connect(DB,uri=True)
for did,name,iso,ep in drives(c):
    r=load(c,did)
    print('==',did,name)
    for a in [0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x3B,0x3C]:
        if a not in r: print(f'  {a:#04x} -'); continue
        t,v,s=r[a]
        u,n=np.unique(v,return_counts=True)
        print(f'  {a:#04x} n={len(v)} '+' '.join(f'{x}:{k}' for x,k in zip(u,n)), set(s))
