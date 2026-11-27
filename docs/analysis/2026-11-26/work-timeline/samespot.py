from common import *
d=39; g,gx,gy,_=clean_gps(d)
evt=np.array([e[0] for e in events(d) if e[1]=='link_error'])
eps=[]
for x in evt:
    if not eps or x-eps[-1]>1: eps.append(x)
ts=t_of('23:10:00'); n=0; tot=0
for x in eps:
    ex,ey=np.interp(x,g[:,0],gx),np.interp(x,g[:,0],gy)
    m=(g[:,0]<ts)&(np.hypot(gx-ex,gy-ey)<40)
    early=g[m,0]
    tot+=1; n+= early.size>0
    print(wall(x), 'passed same spot (<40 m) before 23:10:', sorted(set(wall(v)[:5] for v in early))[:4])
print(n,'of',tot,'error episodes are at spots driven error-free before 23:10')
# whole-drive gps distance incl. gaps
for k,dd in DRIVES.items():
    g,gx,gy,_=clean_gps(dd); dtg=np.diff(g[:,0]); sp=(g[1:,3]+g[:-1,3])/2
    print(k,'gps-speed integral bridging gaps %.2f km'%(np.sum(sp/3.6*dtg)/1000))
