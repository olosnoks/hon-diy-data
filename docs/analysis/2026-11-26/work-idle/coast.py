from common import *
for name in DR:
    d,ep=load(name); r=d['rpm']; t=r[:,0]; rp=r[:,1]; v=d['vss'][:,1]
    tg=np.array([at(d,'idle_target',x) for x in t]); tps=np.array([at(d,'tps',x) for x in t])
    m=(v>=8)&(rp>0)
    print(name,'rolling >=8 km/h: min rpm',rp[m].min(),'at',wall(ep,t[m][np.argmin(rp[m])]), ' min(rpm-target)',(rp-tg)[m].min())
    idle=(v>=8)&(rp<tg+150)&(tps<8.7)
    # segments
    seg=[];s=None
    for i in range(len(t)):
        if idle[i] and s is None: s=i
        if (not idle[i]) and s is not None:
            if t[i-1]-t[s]>=1.0: seg.append((s,i-1))
            s=None
    tot=sum(t[b]-t[a] for a,b in seg)
    print(f'  rolling near-idle (vss>=8, rpm<target+150, closed throttle) segments >=1s: {len(seg)}, total {tot:.0f}s')
    for a,b in seg:
        w=slice(a,b+1)
        print(f'   {wall(ep,t[a])} {t[b]-t[a]:5.1f}s vss {v[a]:.0f}->{v[b]:.0f} rpm min {rp[w].min():.0f} mean {rp[w].mean():.0f} tgt {tg[a]:.0f} min-tgt {(rp[w]-tg[w]).min():.0f} iac {at(d,"iac_cmd",t[a]):.0f}->{at(d,"iac_cmd",t[b]):.0f}')
