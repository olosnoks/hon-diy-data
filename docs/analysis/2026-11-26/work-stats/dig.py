from load import *
from corr import known
import sqlite3
c=sqlite3.connect(DB,uri=True)
for r in c.execute("select id,name,started_iso,epoch_unix,ended_iso from drives where id in (33,34)"): print(r)
for d in (33,34):
    per=load(d); secs,g=grid(per); k=known(g)
    y=g[0x29]; x=g[0x28]
    print("== drive",d," 29 grouped by 28: 28 -> n, median29, min,max, median rpm, median ect, median map")
    for v in np.unique(x[~np.isnan(x)]):
        m=x==v
        print(f"  28={v:3.0f} n={m.sum():4d} 29 med={np.nanmedian(y[m]):5.1f} [{np.nanmin(y[m]):.0f},{np.nanmax(y[m]):.0f}] rpm={np.nanmedian(k['rpm'][m]):5.0f} ect={np.nanmedian(k['ect'][m]):.0f} map={np.nanmedian(k['map'][m]):.0f} altfr={np.nanmedian(k['alt_fr'][m]):.0f}")
    # flips of 32/37/3c from raw reads
    t27=per[0x32][:,0]; v32=per[0x32][:,1]; t35=per[0x37][:,0]; v37=per[0x37][:,1]; v3c=per[0x3C][:,1]; v35=per[0x35][:,1]
    print(" 32==44 frac",np.mean(v32==44)," 37==44 frac",np.mean(v37==44)," 3C==2 frac",np.mean(v3c==2))
    # run lengths
    def runs(b):
        r=[];n=0
        for x_ in b:
            if x_: n+=1
            elif n: r.append(n); n=0
        return np.bincount(r) if r else []
    print(" run lengths 32=44:",runs(v32==44)," 37=44:",runs(v37==44)," 3C=2:",runs(v3c==2))
    # co-occurrence 37 vs 3C (same block)
    a=v37==44; b=v3c==2
    print(" same-read 37&3C:", np.sum(a&b), "expected", a.sum()*b.sum()/len(a))
    # 32 vs 37: match nearest reads
    idx=np.searchsorted(t35,t27); idx=np.clip(idx,0,len(t35)-1)
    a=v32==44; b=v37[idx]==44
    print(" 32 vs nearest-37:", np.sum(a&b),"expected",a.sum()*b.sum()/len(a), "median dt",np.median(t35[idx]-t27))
    # 32==44 vs 3C nearest
    b2=v3c[idx]==2; print(" 32 vs 3C:",np.sum(a&b2),"expected",a.sum()*b2.sum()/len(a))
    # dips vs rpm
    rpm=k['rpm']; sidx=np.floor(t27).astype(int)-secs[0]
    print(" rpm when 32=44:",np.nanmedian(rpm[sidx][a]),"else",np.nanmedian(rpm[sidx][~a]))
    # latency / read phase
    print(" 32 dips times(first 20):",np.round(t27[a][:20]))
