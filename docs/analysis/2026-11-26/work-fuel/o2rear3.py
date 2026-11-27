from common import *
for did,a,b in [(38,15,21),(39,59.5,62.5),(39,26.5,31)]:
    z=load(did); fo=z['raw_o2']; ro=z['raw_o2_2']
    print(f'drive {did} min {a}-{b}: 20 s windows: t_min rpm vss | front mean, frac>0.45 | rear mean std min max | total trim')
    for s in np.arange(a*60,b*60,20):
        k=(ro[:,0]>=s)&(ro[:,0]<s+20); g=(z['t']>=s)&(z['t']<s+20); kf=(fo[:,0]>=s)&(fo[:,0]<s+20)
        if k.sum()<3: continue
        r=ro[k,1]; f=fo[kf,1]
        print(f'  {s/60:6.2f} {np.nanmean(z["rpm"][g]):5.0f} {np.nanmean(z["vss"][g]):4.0f} | {f.mean():.2f} {np.mean(f>0.45):.2f} | {r.mean():.3f} {r.std():.3f} {r.min():.2f} {r.max():.2f} | {np.nanmean(z["total"][g]):+.1f}')
