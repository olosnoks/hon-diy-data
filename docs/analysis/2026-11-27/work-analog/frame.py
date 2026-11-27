import sqlite3,numpy as np,pickle
from load import *
c=sqlite3.connect(DB,uri=True)
F={}
for did,name,iso,ep in drives(c):
    r=load(c,did)
    if 0x32 not in r: continue
    for tgt in [0x32,0x37,0x3A,0x3C]:
        t,v,s=r[tgt]
        d=dict(drive=np.full(len(t),did),t=t,v=v.astype(float),wall=ep+t)
        hi,_=at(r,0x00,t); lo,dt=at(r,0x01,t); d['rpm']=(hi*256+lo)/4; d['rpm_dt']=dt
        for nm,a in [('vss',2),('ect',0x10),('iat',0x11),('map',0x12),('baro',0x13),('tps',0x14),('o2',0x15),('batt',0x17),('altfr',0x18),('eld',0x19),('stft',0x20),('ltft',0x22),('ign',0x26),('iacv',0x28),('purge',0x2F),('x08',0x08),('x0b',0x0B),('x0c',0x0C)]:
            if a in r: d[nm],_=at(r,a,t)
            else: d[nm]=np.full(len(t),np.nan)
        F.setdefault(tgt,[]).append(d)
for k in F:
    F[k]={c_:np.concatenate([d[c_] for d in F[k]]) for c_ in F[k][0]}
pickle.dump(F,open('frame.pkl','wb'))
for k in F: print(hex(k),len(F[k]['t']))
