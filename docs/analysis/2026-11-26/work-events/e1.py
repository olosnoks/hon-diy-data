import numpy as np, load, datetime
d='drive_2026_11_26_2045'
B,R,L,ep=load.load(d)
G=load.grid(B,R)
wall=lambda t: datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S')
t=G['t']
# x27 vs rpm
for lo,hi in [(0,700),(700,800),(800,900),(900,1000),(1000,1100),(1100,1300),(1300,3000)]:
    m=(G['rpm']>=lo)&(G['rpm']<hi)
    if m.sum(): print('rpm %d-%d n=%d x27 mean=%.2f dist=%s'%(lo,hi,m.sum(),G[0x27][m].mean(),np.bincount(G[0x27][m].astype(int),minlength=5)))
# f08 bit3 times
tt,v=B[0x08]
print('f08=140 at',[wall(x) for x in tt[v==140]], tt[v==140])
# x32/x37 co-toggle
print('x32==44 & x37==44', ((G[0x32]==44)&(G[0x37]==44)).sum(), 'x32 44',(G[0x32]==44).sum(),'x37 44',(G[0x37]==44).sum(),'knock2',(G[0x3C]==2).sum(), 'x37 44 & knock 2',((G[0x37]==44)&(G[0x3C]==2)).sum())
# x32 44 runs
for a in (0x32,0x37,0x3C):
    tt,v=B[a]; low=v!=np.median(v)
    starts=tt[1:][np.diff(low.astype(int))==1]
    iv=np.diff(starts)
    print('%02X nonmedian n=%d, episodes=%d, interval median=%.1f, lens:'%(a,low.sum(),len(starts),np.median(iv) if len(iv) else 0))
# ECT raw jumps
tt,v=B[0x10]; dv=np.diff(v)
for i in np.where(np.abs(dv)>=8)[0]: print('ECT jump %s %.1f %d->%d (%.0fC->%.0fC)'%(wall(tt[i+1]),tt[i+1],v[i],v[i+1],load.ect_c(v[i]),load.ect_c(v[i+1])))
