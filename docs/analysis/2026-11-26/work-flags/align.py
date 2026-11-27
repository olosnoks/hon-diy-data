import pickle, numpy as np
exec(open(r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/trans.py').read().split("BASE=")[0])
# all transitions times
TR=[]
for did,d in D.items():
    s=d['ser']
    for a in range(8,16):
        arr=s[a]; v=arr[:,1].astype(int)
        for i in np.where(v[1:]!=v[:-1])[0]+1: TR.append((did,arr[i,0],a))
nglitch=0; hit=[]
for did,d in D.items():
    e=d['ser'][0x10]; E=np.array([ect(x) for x in e[:,1]])
    for i in np.where(np.abs(np.diff(E))>15)[0]+1:
        nglitch+=1; t=e[i,0]
        h=[(a) for (dd,tt,a) in TR if dd==did and abs(tt-t)<=2.5]
        if h: hit.append((did,ts(t),h))
print("ECT jumps >15C:", nglitch, "with any 0x08-0x0F change within 2.5 s:", hit)
s=D[37]['ser']; h=s[0x76]; l=s[0x77]; it=(h[:,1]*256+l[:,1])/4
st=np.where(np.diff(it)!=0)[0]+1
print("d37 idle-target steps:", len(st), "range", it.min(), it.max(), "flag transitions in d37:", [x for x in TR if x[0]==37])
# rpm bands for each always-varying bit
for a,b in [(0x0F,0),(0x0B,6),(0x0A,7),(0x0C,0),(0x08,2)]:
    out=[]
    for lo,hi in [(0,400),(400,700),(700,900),(900,1200),(1200,2000),(2000,4000)]:
        n=0;k=0
        for did,d in D.items():
            arr=d['ser'][a]
            for t,v in arr:
                r=rpm(d['ser'],t)
                if r is None or not(lo<=r<hi): continue
                n+=1;k+=(int(v)>>b)&1
        out.append(f"{lo}-{hi}: {k}/{n}")
    print(f"0x{a:02X} b{b}", '; '.join(out))
# total running time
print("total slow reads with rpm>400:", sum(1 for did,d in D.items() for t,v in d['ser'][8] if (rpm(d['ser'],t) or 0)>400))
