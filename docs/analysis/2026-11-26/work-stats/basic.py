import sys; from load import *
for d in (33,34):
    per=load(d)
    print("=== drive",d)
    for a in sorted(per):
        v=per[a][:,1].astype(int); u,cnt=np.unique(v,return_counts=True)
        top=sorted(zip(cnt,u),reverse=True)[:6]
        tr=np.sum(np.diff(v)!=0)
        print(f"{a:02X} n={len(v):5d} min={v.min():3d} max={v.max():3d} nd={len(u):3d} changes={tr:5d} top={[(int(x),int(c)) for c,x in top]}")
