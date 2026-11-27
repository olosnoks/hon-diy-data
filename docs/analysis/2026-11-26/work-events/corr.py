import numpy as np, load, datetime
for d in ['drive_2026_11_26_2045','drive_2026_11_26_2114']:
    B,R,L,ep=load.load(d)
    G=load.grid(B,R)
    print('==',d,len(G['t']))
    known=[('rpm','rpm'),(0x10,'ect'),(0x11,'iat'),(0x12,'map'),(0x15,'o2'),(0x17,'batt'),(0x18,'altfr'),(0x19,'eld'),(0x20,'stft'),(0x22,'ltft'),(0x25,'inj'),(0x26,'ign'),(0x28,'iacv'),(0x3C,'knock')]
    for u in [0x1C,0x27,0x29,0x2A,0x32,0x37]:
        x=G[u]; ok=~np.isnan(x)
        if np.nanstd(x)==0: continue
        cs=[]
        for k,n in known:
            y=G[k]; m=ok&~np.isnan(y)
            if np.std(y[m])==0: continue
            cs.append((np.corrcoef(x[m],y[m])[0,1],n))
        cs.sort(key=lambda z:-abs(z[0]))
        print('%02X'%u, ' '.join('%s=%.2f'%(n,r) for r,n in cs[:6]))
    # x2A change times
    t,v=B[0x2A]; i=np.where(np.diff(v)!=0)[0]
    for j in i: print('x2A %d->%d at %.1f %s ect=%.1fC iat=%.1fC'%(v[j],v[j+1],t[j+1],datetime.datetime.fromtimestamp(ep+t[j+1]).strftime('%H:%M:%S'),load.ect_c(load.at(B[0x10],np.array([t[j+1]]))[0]),load.ect_c(load.at(B[0x11],np.array([t[j+1]]))[0])))
    print('x2A first', v[0], 'last', v[-1])
    # x29 vs iacv table
    if d.endswith('2045'):
        for lo,hi in [(0,0),(1,5),(6,9),(10,12),(13,20),(21,40)]:
            m=(G[0x28]>=lo)&(G[0x28]<=hi)
            if m.sum(): print('iacv %d-%d n=%d x29 med=%.0f p10=%.0f p90=%.0f rpm=%.0f'%(lo,hi,m.sum(),np.median(G[0x29][m]),np.percentile(G[0x29][m],10),np.percentile(G[0x29][m],90),np.nanmedian(G['rpm'][m])))
