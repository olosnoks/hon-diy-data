import csv
from common import *
out=[]
for name in DR:
    d,ep=load(name); r=d['rpm']; t=r[:,0]; rp=r[:,1]; v=d['vss'][:,1]
    z=(v==0).astype(int); e=np.diff(z)
    ends=np.where(e==-1)[0]+1
    for b in ends:
        t0=t[b]
        # standstill start
        a=b-1
        while a>0 and v[a-1]==0: a-=1
        tst=t[a]
        lo=max(tst, t0-10)
        mp=win(d,'map',lo,t0+3); base=np.median(win(d,'map',max(tst,t0-14),max(tst+0.5,t0-6))[:,1]) if t0-tst>3 else mp[0,1]
        tps=win(d,'tps',lo,t0+3)
        # take-up = last time before t0 that map crosses base+3 with tps closed going into it ... simpler: rpm min in window [lo, t0+2]
        w=np.where((t>=lo)&(t<=t0+2))[0]; k=w[np.argmin(rp[w])]
        tm=t[k]
        # tps when rpm starts falling (max rpm in 2.5s before min)
        pw=np.where((t>=tm-2.5)&(t<=tm))[0]; jf=pw[np.argmax(rp[pw])]
        tg=at(d,'idle_target',tm)
        tpsf=at(d,'tps',t[jf]); tpsm=win(d,'tps',t[jf],tm)[:,1].max()
        out.append(dict(drive=name[-4:], launch=wall(ep,t0), stand_s=round(t0-tst,1), rpm_min=rp[k], t_min_rel=round(tm-t0,2), target=tg, under=round(tg-rp[k]),
            rpm_fall_start=rp[jf], tps_fall_start=round(tpsf,2), tps_max_to_min=round(tpsm,2), map_base=round(base,1),
            map_at_min=round(at(d,'map',tm),1), ect=round(at(d,'ect',tm),1), eld=round(at(d,'eld',tm),1), iac=at(d,'iac_cmd',tm)))
ks=list(out[0].keys()); print(' '.join(ks))
for o in out: print(' '.join(str(o[k]) for k in ks))
with open('launches.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=ks); w.writeheader(); w.writerows(out)
