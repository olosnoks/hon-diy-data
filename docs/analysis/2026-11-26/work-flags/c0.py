import pickle, numpy as np, datetime as dt
exec(open(r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/trans.py').read().split("BASE=")[0])
print("running reads (rpm>400) with batt<13.6 V or ELD step <= -25 vs previous slow read")
for did,d in sorted(D.items()):
    s=d['ser']; a8=s[8]
    for i in range(1,len(a8)):
        t=a8[i,0]; r=rpm(s,t)
        if r is None or r<400: continue
        b=near(s,0x17,t,0.6); e=near(s,0x19,t,0.6); ep=near(s,0x19,a8[i-1,0],0.6)
        if b is None or e is None: continue
        if b/10.45<13.6 or (ep is not None and e-ep<=-25):
            c=int(near(s,0x0C,t,0.01)); print(f"  d{did} {ts(t)} 0C={c} batt {b/10.45:4.1f} eld {ep}->{e} rpm {r:.0f} 08={int(near(s,8,t,.01))} 0B={int(near(s,0xB,t,.01))}")
print("fuel-cut episodes (inj=0 at rpm>1000) and nearest 0x0F read")
for did,d in sorted(D.items()):
    s=d['ser']; a24=s.get(0x24); a25=s.get(0x25)
    if a24 is None: continue
    inj=a24[:,1]*256+a25[:,1]; z=np.where(inj==0)[0]
    if len(z)==0: continue
    eps=np.split(z, np.where(np.diff(z)>3)[0]+1)
    for ep in eps:
        t0=a24[ep[0],0]; t1=a24[ep[-1],0]
        a=s[0x0F]; j=np.where((a[:,0]>=t0-0.3)&(a[:,0]<=t1+0.3))[0]
        print(f"  d{did} {ts(t0)}..{ts(t1)[9:]} ({len(ep)} fast reads) 0x0F reads inside: {[int(a[k,1]) for k in j]}  rpm {rpm(s,t0):.0f}")
