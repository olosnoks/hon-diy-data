import pickle,numpy as np
F=pickle.load(open('frame.pkl','rb'))
for k in [0x32,0x37]:
    d=F[k]; dip=d['v']==44
    print(hex(k))
    for rl,rh in [(600,800),(800,1000),(1000,1500),(1500,2200),(2200,4000)]:
        line=[]
        for ml,mh in [(0,45),(45,60),(60,90),(90,200)]:
            m=(d['rpm']>=rl)&(d['rpm']<rh)&(d['map']>=ml)&(d['map']<mh)
            line.append(f'map{ml}-{mh}: {m.sum():4d} {100*dip[m].mean() if m.sum()>=20 else float("nan"):4.1f}%')
        print(f'  rpm {rl}-{rh}: '+' | '.join(line))
    # idle only, ect bins
    for el,eh in [(0,45),(45,55),(55,70),(70,256)]:
        m=(d['vss']==0)&(d['rpm']<1000)&(d['ect']>=el)&(d['ect']<eh)
        print(f'  idle ect {el}-{eh}: n={m.sum()} {100*dip[m].mean():.1f}%')
    # run lengths and spacing
    idx=np.where(dip)[0]
    runs=np.diff(np.r_[0,np.where(np.diff(idx)!=1)[0]+1,len(idx)])
    print('  runs', np.bincount(runs))
# joint 0x32/0x37 at same read time (drive 34-36 had 27+16 incl 0x35? no). nearest pairing
a,b=F[0x32],F[0x37]
import collections
tot=0;both=0;e=0
for did in np.unique(a['drive']):
    ma=a['drive']==did; mb=b['drive']==did
    ta,va=a['t'][ma],a['v'][ma]; tb,vb=b['t'][mb],b['v'][mb]
    i=np.searchsorted(tb,ta).clip(0,len(tb)-1)
    ok=np.abs(tb[i]-ta)<0.3
    da=va[ok]==44; db=vb[i][ok]==44
    tot+=ok.sum(); both+=(da&db).sum(); e+=da.mean()*db.mean()*ok.sum()
print('pairs',tot,'both dip',both,'expected',round(e,1))
