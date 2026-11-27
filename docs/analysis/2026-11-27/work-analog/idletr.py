import sqlite3,numpy as np,collections
from load import *
c=sqlite3.connect(DB,uri=True)
down=collections.Counter(); up=collections.Counter()
for did in (37,38,39):
    r=load(c,did)
    t,hi,_=r[0x76]; _,lo,_=r[0x77]
    tgt=(hi.astype(int)*256+lo)/4
    te,ve,_=r[0x10]
    low=tgt<=750
    for i in np.where(np.diff(low.astype(int))!=0)[0]:
        # ect reads in the 3 s before transition
        m=(te>t[i+1]-3)&(te<=t[i+1]+0.5)
        es=ve[m].tolist()
        if low[i+1]: down[tuple(es)]+=1; print(did,f'{t[i+1]:.0f} -> 749.75  ECT before: {es}  tgt {tgt[i]}->{tgt[i+1]}')
        else: up[tuple(es)]+=1; print(did,f'{t[i+1]:.0f} <- leave 749.75 ECT before: {es} tgt {tgt[i]}->{tgt[i+1]}')
