from load import *
import math
o=np.load('origin.npy'); R=6371008.8
for k,d in DRIVES.items():
    g=gps(d)
    x=np.radians(g[:,2]-o[1])*R*math.cos(math.radians(o[0])); y=np.radians(g[:,1]-o[0])*R
    r=np.hypot(x,y); bad=r>50000
    print(k,'n',len(g),'far pts',bad.sum(), g[bad][:5,[0,1,2,3,5,6,7]].round(3) if bad.any() else '')
    dt=np.diff(g[:,0]); print(' dt pct',np.percentile(dt,[1,5,50,95,99]).round(2),'max',dt.max().round(1),'gaps>2s',(dt>2).sum(),'>3s',(dt>3).sum())
    print(' sats',np.percentile(g[:,6],[0,5,50,95,100]),'hdop',np.percentile(g[:,7],[0,5,50,95,100]))
    big=np.where(dt>2.5)[0]
    for i in big: print('  gap',wall(g[i,0]),round(dt[i],1))
