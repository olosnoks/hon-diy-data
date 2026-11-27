from load import *
for k,d in DRIVES.items():
    g=gps(d); dt=np.diff(g[:,0]); i=np.where(dt>4.5)[0]
    print(k,'gap starts diff',np.diff(g[i,0]).round(0))
    print(' sats0 rows',(g[:,6]==0).sum(), [wall(x) for x in g[g[:,6]==0,0][:5]])
    t,u=utc(d); off=(E+t)-u; off=(off+43200)%86400-43200
    print(' pc-gpsutc offset pct',np.percentile(off,[1,50,99]).round(3),'first/last min med',np.median(off[:60]).round(3),np.median(off[-60:]).round(3))
    du=np.diff(u); print(' utc step pct',np.percentile(du,[1,50,99]), 'utc gaps>1.5',(du>1.5).sum(),'utc dup/neg',(du<=0).sum())
    # where gps speed vs utc gaps: are gaps in utc too (missed fixes) or delivered late?
    j=np.where(dt>4.5)[0][:6]
    for jj in j: print('   gap', wall(g[jj,0]), 'dt',round(dt[jj],1),'utc step',round(u[jj+1]-u[jj],1), 'off before/after',round(off[jj],2),round(off[jj+1],2))
