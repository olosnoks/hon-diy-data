import numpy as np, load, datetime
B,R,L,ep=load.load('drive_2026_11_26_2114')
wall=lambda t: datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S.%f')[:-4]
t,v=B[0x2F]; print('x2F last t %.1f %s value %d'%(t[-1],wall(t[-1]),v[-1]))
t2,v2=B[0x2A]; print('x2A changes',[(wall(t2[i+1]),v2[i],v2[i+1]) for i in np.where(np.diff(v2)!=0)[0]])
ch=np.where(np.diff(v)!=0)[0]
rt,rv=R
for i in ch:
    te=t[i+1]; tp=t[i]
    print('x2F %d->%d between %s and %s'%(v[i],v[i+1],wall(tp),wall(te)))
    m=(rt>tp-3)&(rt<te+1)
    print('   rpm:', ' '.join('%.0f'%x for x in rv[m]))
    for a in (0x19,0x18,0x26,0x28,0x29,0x10,0x3C):
        tt,vv=B[a]; mm=(tt>tp-3)&(tt<te+1); print('   %s:'%load.name(a),' '.join('%.0f'%x for x in vv[mm][::(5 if a==0x26 else 1)]))
