from common import *
res={}
allr=[];allw=[]
pairs=[]
for k,d in DRIVES.items():
    t,vss=chan(d,'vss'); g,gx,gy,_=clean_gps(d)
    # lag: vss at gps time + lag ; maximise corr
    gt=g[:,0]; gs=g[:,3]
    ok=(gt>t[0]+5)&(gt<t[-1]-5)
    best=None
    for lag in np.arange(-3,3.01,0.1):
        vi=np.interp(gt[ok]+lag,t,vss)
        dv=np.diff(vi); dg=np.diff(gs[ok]); m=np.diff(gt[ok])<1.5
        cc=np.corrcoef(dv[m],dg[m])[0,1]
        if best is None or cc>best[1]: best=(lag,cc)
    lag=best[0]; print(k,'best lag (vss at gps_t+lag matches gps speed): %+.1f s corr(d/dt) %.3f'%best)
    # steady cruise ratio: 10-s windows
    for a in np.arange(t[0],t[-1]-10,5):
        s=(t>=a)&(t<a+10); gsel=(gt>=a)&(gt<a+10)
        if gsel.sum()<8 or s.sum()<50: continue
        v=vss[s]
        if v.min()<25 or v.max()-v.min()>2: continue
        gm=gs[gsel]
        if gm.max()-gm.min()>3: continue
        pairs.append((k,a,v.mean(),gm.mean()))
P=np.array([(p[2],p[3]) for p in pairs])
r=P[:,1]/P[:,0]
print('steady windows',len(P),'ratio gps/vss median %.4f mean %.4f p10 %.4f p90 %.4f'%(np.median(r),r.mean(),*np.percentile(r,[10,90])))
# by speed
for lo,hi in [(25,45),(45,70),(70,90),(90,120)]:
    m=(P[:,0]>=lo)&(P[:,0]<hi)
    if m.any(): print(f'  vss {lo}-{hi}: n={m.sum()} median ratio {np.median(r[m]):.4f}')
# linear fit gps = a*vss + b
A=np.c_[P[:,0],np.ones(len(P))]; coef=np.linalg.lstsq(A,P[:,1],rcond=None)[0]; print('fit gps = %.4f*vss %+.2f'%tuple(coef))
coef0=(P[:,0]@P[:,1])/(P[:,0]@P[:,0]); print('fit through 0: %.4f'%coef0)
np.save('vsscal.npy',P)
# distance check
for k,d in DRIVES.items():
    t,vss=chan(d,'vss'); g,gx,gy,_=clean_gps(d)
    mv=g[:,3]>3
    seg=np.hypot(np.diff(gx),np.diff(gy)); dtg=np.diff(g[:,0])
    gd=np.sum(seg[(g[1:,3]>3)&(dtg<3)])
    gsd=np.sum(g[1:,3][dtg<3]/3.6*dtg[dtg<3])
    vd=np.trapezoid(vss,t)/3.6
    print(k,'odometer: vss %.2f km, gps-position (moving, no gap) %.2f km, gps-speed integral %.2f km'%(vd/1000,gd/1000,gsd/1000))
