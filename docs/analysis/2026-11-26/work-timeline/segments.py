from common import *
out={}
def rmed(a,w):
    h=w//2; r=np.full(len(a),np.nan)
    for i in range(len(a)):
        s=a[max(0,i-h):i+h+1]; s=s[np.isfinite(s)]
        if len(s): r[i]=np.median(s)
    return r
def band(v):
    return 'city' if v<45 else ('arterial' if v<80 else 'interstate')
report=[]
for k,d in DRIVES.items():
    G=np.load(f'grid_{k}.npz'); T=G['T']; V=G['VSS'].copy(); RPM=G['RPM']
    run=np.isfinite(RPM)&(RPM>300)
    Vf=V.copy(); n=np.isfinite(Vf); Vf[~n]=np.interp(T[~n],T[n],Vf[n])
    v5=rmed(Vf,5); v31=rmed(Vf,31)
    lab=np.array(['']*len(T),dtype=object)
    for i in range(len(T)):
        lab[i]='stop' if v5[i]<1 else band(v31[i])
    # merge short band runs
    def runs(l):
        r=[];s=0
        for i in range(1,len(l)+1):
            if i==len(l) or l[i]!=l[s]: r.append([s,i,l[s]]); s=i
        return r
    for _ in range(3):
        rr=runs(lab)
        for j,(a,b,L) in enumerate(rr):
            if L!='stop' and b-a<20 and len(rr)>1:
                nb=rr[j-1][2] if j>0 and rr[j-1][2]!='stop' else (rr[j+1][2] if j+1<len(rr) and rr[j+1][2]!='stop' else L)
                lab[a:b]=nb
    # launch / decel
    rr=runs(lab)
    for j,(a,b,L) in enumerate(rr):
        if L!='stop': continue
        # launch after stop
        if b<len(T):
            e=b
            while e<len(T) and e-b<15 and Vf[e]<25: e+=1
            lab[b:e]=np.where(lab[b:e]!='stop','launch',lab[b:e])
        if a>0:
            s=a-1
            while s>0 and a-s<40 and (v5[s-1]-v5[s])>0.3: s-=1
            if a-s>=3: lab[s:a]=np.where(lab[s:a]!='stop','decel',lab[s:a])
    rr=runs(lab)
    for r_ in rr:
        if r_[2]=='stop' and r_[1]-r_[0]>=60: lab[r_[0]:r_[1]]='idle/parked'
    lab[~run]='no ECU data'
    rr=runs(lab)
    # merge launch/decel tiny pieces
    g,gx,gy,_=clean_gps(d)
    X=np.interp(T,g[:,0],gx); Y=np.interp(T,g[:,0],gy)
    gears=np.array([gear_of(RPM[i],V[i]) for i in range(len(T))])
    segs=[]
    odo=np.concatenate([[0],np.cumsum(np.nan_to_num(Vf[1:])/3.6)])
    for a,b,L in rr:
        sl=slice(a,b); v=V[sl]; r=RPM[sl]
        gc={x:(gears[sl]==x).sum() for x in set(gears[sl])}
        gtxt=' '.join(f"{x}:{int(100*c/(b-a))}%" for x,c in sorted(gc.items(),key=lambda z:-z[1]) if c/(b-a)>=0.1 and x in '12345')
        segs.append(dict(k=k,start=wall(T[a]),t0=float(T[a]),dur=int(b-a),label=L,vmin=float(np.nanmin(v)) if np.isfinite(v).any() else None,vmax=float(np.nanmax(v)) if np.isfinite(v).any() else None,
             vmean=float(np.nanmean(v)) if np.isfinite(v).any() else None,rmin=float(np.nanmin(G['RPMmin'][sl])) if np.isfinite(r).any() else None,rmax=float(np.nanmax(r)) if np.isfinite(r).any() else None,
             gears=gtxt,where0=where(X[a],Y[a]),where1=where(X[b-1],Y[b-1]),km=float((odo[b-1]-odo[a])/1000)))
    out[k]=dict(lab=lab.tolist(),gears=gears.tolist(),segs=segs)
    print('=== drive',k,len(segs),'segments')
    for s in segs:
        print(f"{s['start']} {s['dur']:5d}s {s['label']:12s} v {s['vmin']}-{s['vmax']} mean {s['vmean'] and round(s['vmean'])} rpm {s['rmin']}-{s['rmax']} gears {s['gears']:22s} {s['where0']} -> {s['where1']} {s['km']:.2f}km")
json.dump(out,open('segments.json','w'))
