from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
rows=[]
for did in range(6,40):
    try: z=load(did)
    except Exception as e: continue
    m=(z['closed_loop']>0.5)&(z['rpm']>550)&(z['rpm']<1000)&(np.nan_to_num(z['vss'],nan=0)<1)&np.isfinite(z['total'])&(z['since_ol']>5)&(np.abs(z['drpm'])<300)
    m&=(z['ect']>=55)&~z['ect_glitch']
    if m.sum()<100: continue
    t=z['t'][m]; key=np.floor(t/30)  # 30-s chunks
    for k in np.unique(key):
        s=np.where(m)[0][key==k]
        if len(s)<150: continue
        rows.append((did,t[key==k].mean()/60,np.nanmean(z['rpm'][s]),np.nanmean(z['map'][s]),np.nanmean(z['total'][s]),np.nanmean(z['iat'][s]),np.nanmean(z['ect'][s]),np.nanmean(z['batt'][s]),np.nanmean(z['inj'][s]),np.nanmean(z['iacv'][s]),np.nanmean(z['o2'][s]),np.nanmean(z['o2'][s]>0.45)))
R=np.array(rows); np.save(f'{W}/idle_chunks.npy',R)
print('30-s warm idle chunks:',len(R))
cols=['did','min','rpm','map','total','iat','ect','batt','inj','iacv','o2mean','o2rich']
X=np.column_stack([np.ones(len(R)),1000/R[:,2],R[:,5],R[:,6],R[:,7]])
b,*_=np.linalg.lstsq(X,R[:,4],rcond=None); r=R[:,4]-X@b
print('idle total = c + r*1000/rpm + i*IAT + e*ECT + v*batt :',np.round(b,3),' rmse',r.std().round(2))
for j,nm in [(5,'iat'),(6,'ect'),(7,'batt'),(2,'rpm'),(3,'map')]:
    print(f'  corr(total,{nm}) = {np.corrcoef(R[:,4],R[:,j])[0,1]:+.2f}')
# group by day / drive
for d in np.unique(R[:,0]):
    k=R[:,0]==d
    print(f'  drive {int(d):2d}: n={k.sum():3d} rpm {R[k,2].mean():5.0f} MAP {R[k,3].mean():4.1f} total {R[k,4].mean():+5.1f} iat {R[k,5].mean():4.1f} ect {R[k,6].mean():4.1f} batt {R[k,7].mean():5.2f} inj {R[k,8].mean():.2f} iacv {R[k,9].mean():4.1f} o2>0.45 {R[k,11].mean():.2f}')
f,ax=plt.subplots(1,3,figsize=(16,4.5))
for a,j,nm in zip(ax,[5,2,7],['IAT °C','rpm','batt V']):
    sc=a.scatter(R[:,j],R[:,4],c=R[:,0],cmap='tab20',s=10); a.set_xlabel(nm); a.set_ylabel('total trim %'); a.grid(alpha=.3)
ax[0].set_title('warm idle (30-s chunks, closed loop), colour = drive')
f.tight_layout(); f.savefig(f'{W}/idle_trim_vs_iat_rpm_batt.png',dpi=85)
