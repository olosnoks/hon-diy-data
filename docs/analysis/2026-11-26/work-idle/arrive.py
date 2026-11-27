import csv
from common import *
out=[]
for name in DR:
    d,ep=load(name); r=d['rpm']; t=r[:,0]; rp=r[:,1]; v=d['vss'][:,1]
    z=(v==0).astype(int); e=np.diff(z)
    st=np.where(e==1)[0]+1; en=list(np.where(e==-1)[0]+1)
    for a in st:
        ts=t[a]; nxt=[t[b] for b in en if t[b]>ts]; tl=nxt[0] if nxt else t[-1]
        hi=min(ts+12, tl-3.0)
        if hi<=ts-3: hi=ts+0.5
        w=np.where((t>=ts-3)&(t<=hi))[0]
        tg=at(d,'idle_target',ts)
        k=w[np.argmin(rp[w])]
        # rpm at stop and 3 s before
        ent=win(d,'rpm',ts-5,ts)[:,1]
        ics=[at(d,'iac_cmd',ts+s) for s in (-5,0,3,6,10)]
        out.append(dict(drive=name[-4:],stop=wall(ep,ts),stand_s=round(tl-ts,1),rpm_5s_before=ent[0],rpm_at_stop=rp[a],target=tg,rpm_min=rp[k],t_min=round(t[k]-ts,1),
            under=round(tg-rp[k]),map_at_min=round(at(d,'map',t[k]),1),brake=at(d,'brake',ts),iac=ics,eld=at(d,'eld',ts),ect=at(d,'ect',ts)))
for o in out: print(o)
