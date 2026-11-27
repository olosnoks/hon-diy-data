"""1 Hz grid per drive + local ENU positions. Writes grid_<k>.npz (contains coordinates: keep local)."""
from load import *
import math
R=6371008.8
# common origin: drive A's first GPS fix is the start point for A; B starts at the gas station.
def enu(lat,lon,lat0,lon0):
    x=np.radians(lon-lon0)*R*math.cos(math.radians(lat0)); y=np.radians(lat-lat0)*R
    return x,y
def build(k):
    d=DRIVES[k]
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss')
    tm,mp=chan(d,'map'); _,tps=chan(d,'tps'); tl,load=chan(d,'load')
    g=gps(d)
    t0=np.floor(t[0]); t1=np.ceil(t[-1])
    T=np.arange(t0,t1+1,1.0)
    def binmed(ts,vs,T):
        idx=np.searchsorted(ts,T); idx2=np.searchsorted(ts,T+1)
        out=np.full(len(T),np.nan)
        for i,(a,b) in enumerate(zip(idx,idx2)):
            if b>a: out[i]=np.median(vs[a:b])
        return out
    RPM=binmed(t,rpm,T); VSS=binmed(t,vss,T); MAP=binmed(tm,mp,T); TPS=binmed(tm,tps,T); LOAD=binmed(tl,load,T)
    RPMmin=np.full(len(T),np.nan)
    idx=np.searchsorted(t,T); idx2=np.searchsorted(t,T+1)
    for i,(a,b) in enumerate(zip(idx,idx2)):
        if b>a: RPMmin[i]=rpm[a:b].min()
    gv=g[(g[:,1]!=0)&np.isfinite(g[:,1])]
    lat=np.interp(T,gv[:,0],gv[:,1],left=np.nan,right=np.nan); lon=np.interp(T,gv[:,0],gv[:,2],left=np.nan,right=np.nan)
    gs=np.interp(T,gv[:,0],gv[:,3],left=np.nan,right=np.nan)
    # mark gps gaps > 3 s as nan
    j=np.searchsorted(gv[:,0],T); 
    prev=gv[np.clip(j-1,0,len(gv)-1),0]; nxt=gv[np.clip(j,0,len(gv)-1),0]
    bad=(nxt-prev)>3; gs[bad]=np.nan
    np.savez(f'grid_{k}.npz',T=T,RPM=RPM,RPMmin=RPMmin,VSS=VSS,MAP=MAP,TPS=TPS,LOAD=LOAD,lat=lat,lon=lon,GS=gs,glat=gv[:,1],glon=gv[:,2],gt=gv[:,0])
for k in DRIVES: build(k)
a=np.load('grid_A.npz'); 
lat0,lon0=a['glat'][0],a['glon'][0]
np.save('origin.npy',np.array([lat0,lon0]))
print('ok')
