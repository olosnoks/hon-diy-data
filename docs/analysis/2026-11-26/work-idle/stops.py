from common import *
for name in DR:
    d,ep=load(name); r=d['rpm']; t=r[:,0]; v=d['vss'][:,1]
    z=(v==0).astype(int); e=np.diff(z)
    st=list(np.where(e==1)[0]+1); en=list(np.where(e==-1)[0]+1)
    if z[0]: st=[0]+st
    if z[-1]: en=en+[len(z)-1]
    print(name)
    for a,b in zip(st,en):
        print(f'  stop {wall(ep,t[a])} -> {wall(ep,t[b])} dur {t[b]-t[a]:7.1f}s  rpm_min {r[a:b+1,1].min():6.0f}')
    tp=np.unique(d['tps'][:,1]); print(' tps vals',tp[:8])
