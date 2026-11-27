from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
rows=[]
for did in (38,39):
    z=load(did); m=sel_cl(z,steady=False)&np.isfinite(z['o2'])
    t=z['t']; o=z['o2']
    win=8.0
    for s in np.arange(t[0],t[-1]-win,win):
        k=(t>=s)&(t<s+win)
        if k.sum()<70 or m[k].mean()<0.98: continue
        oo=o[k]; tt=t[k]
        # sample interval check
        if np.max(np.diff(tt))>0.5: continue
        b=oo>0.45; cr=np.sum(b[1:]!=b[:-1])
        rows.append((did,s,np.mean(z['rpm'][k]),np.mean(z['map'][k]),np.nanmean(z['iat'][k]),cr/2/win,np.percentile(oo,95)-np.percentile(oo,5),oo.mean(),b.mean(),np.nanmean(z['total'][k]),np.nanmean(z['vss'][k]),np.percentile(oo,95),np.percentile(oo,5)))
R=np.array(rows); np.save(f'{W}/o2win.npy',R)
Q=R[:,3]*R[:,2]/(R[:,4]+273.15)/(25*800/313)
print('8-s closed-loop windows:',len(R))
edges=[0,1.3,2,3,4.5,6.5,10,20]
print('Q band (idle=1) | n | rpm | MAP | switch Hz | p5-p95 swing V | p5 | p95 | mean V | frac>0.45 | total trim')
for lo,hi in zip(edges[:-1],edges[1:]):
    k=(Q>=lo)&(Q<hi)
    if k.sum()<3: continue
    print(f'{lo:4.1f}-{hi:4.1f} | {k.sum():4d} | {R[k,2].mean():5.0f} | {R[k,3].mean():4.1f} | {np.median(R[k,5]):.2f} | {np.median(R[k,6]):.2f} | {np.median(R[k,12]):.2f} | {np.median(R[k,11]):.2f} | {np.median(R[k,7]):.2f} | {np.median(R[k,8]):.2f} | {np.median(R[k,9]):+.1f}')
# STFT-based limit cycle: count stft direction reversals
f,ax=plt.subplots(1,3,figsize=(16,4.5))
ax[0].scatter(Q,R[:,5],s=6,c=R[:,0]); ax[0].set_xscale('log'); ax[0].set_xlabel('airflow Q (idle=1)'); ax[0].set_ylabel('front O2 switching Hz (0.45 V crossings/2)')
ax[1].scatter(Q,R[:,6],s=6,c=R[:,0]); ax[1].set_xscale('log'); ax[1].set_xlabel('Q'); ax[1].set_ylabel('p5-p95 swing V')
ax[2].scatter(Q,R[:,8],s=6,c=R[:,0]); ax[2].set_xscale('log'); ax[2].set_xlabel('Q'); ax[2].set_ylabel('fraction of time > 0.45 V'); ax[2].axhline(.5,color='k',lw=.5)
for a in ax: a.grid(alpha=.3)
f.tight_layout(); f.savefig(f'{W}/o2_switching_vs_load.png',dpi=85)
# sampling limit
for did in (38,39):
    z=load(did); r=z['raw_o2']; print(did,'o2 sample interval median',np.median(np.diff(r[:,0])))
