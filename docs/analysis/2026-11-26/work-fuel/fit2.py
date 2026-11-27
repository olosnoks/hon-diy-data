from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
B=dict(np.load(f'{W}/bins.npz'))
T=B['iat']+273.15; rpm=B['rpm']; mp=B['map']; y=B['total']; Q=B['Q']; P=B['P']
# purge-episode flag: rolling 15 s median of total (per drive, 1-s bins) below +4 while vss>15
flag=np.zeros(len(y),bool)
for d in np.unique(B['did']):
    k=np.where(B['did']==d)[0]; tt=B['t'][k]
    for ii,i in enumerate(k):
        w=k[np.abs(tt-tt[ii])<=7.5]
        flag[i]=np.median(y[w])<4 and B['vss'][i]>15
print('purge/rich-episode bins',flag.sum(),'of',len(y), ' episodes cover t(min):', np.round(np.unique(np.floor(B['t'][flag]/60)),0))
def models(m):
    n=m.sum(); yy=y[m]
    defs={'const':[], 'air c+a/Q':[1/Q[m]], 'pure leak a/Q':'pure', 'dead c+b/P':[1/P[m]], 'fuel c+f*Q':[Q[m]],
          'rpm c+r*1000/rpm':[1000/rpm[m]], 'rpm+map':[1000/rpm[m],mp[m]], 'air+fuel':[1/Q[m],Q[m]], 'air+dead':[1/Q[m],1/P[m]]}
    out={}
    for name,X in defs.items():
        X=(1/Q[m])[:,None] if X=='pure' else np.column_stack([np.ones(n)]+X)
        b,*_=np.linalg.lstsq(X,yy,rcond=None); r=yy-X@b; rss=(r**2).sum(); k=X.shape[1]
        out[name]=(b, np.sqrt(rss/n), 1-rss/((yy-yy.mean())**2).sum(), n*np.log(rss/n)+k*np.log(n))
    best=min(v[3] for v in out.values())
    for name,(b,rm,r2,bic) in out.items():
        print(f'   {name:20s} coef={np.array2string(b,precision=2):28s} rmse={rm:.2f} R2={r2:.3f} dBIC={bic-best:6.0f}')
    return out
print('== all closed-loop 1-s bins'); o_all=models(np.ones(len(y),bool))
print('== excluding rich episodes'); o_np=models(~flag)
print('== drive 38 only'); models((B['did']==38)&~flag)
print('== drive 39 only'); models((B['did']==39)&~flag)
print('== rpm 1500-3000 only (MAP lever at fixed-ish rpm)'); models((rpm>1500)&(rpm<3000)&~flag)
# MAP slope within narrow rpm bands
print('== slope of trim vs MAP inside rpm bands (no rich episodes); air model predicts strongly negative')
for lo,hi in [(1250,1500),(1500,1750),(1750,2000),(2000,2250),(2250,2500),(2500,3000)]:
    m=(rpm>=lo)&(rpm<hi)&~flag
    if m.sum()<30: continue
    b=np.polyfit(mp[m],y[m],1); 
    # air model prediction for same points, from all-data fit
    ba=o_np['air c+a/Q'][0]; pa=ba[0]+ba[1]/Q[m]; bp=np.polyfit(mp[m],pa,1)
    print(f'   rpm {lo}-{hi}: n={m.sum():4d} MAP {mp[m].min():.0f}-{mp[m].max():.0f}  data slope {b[0]*10:+.2f} %/10kPa   air-model slope {bp[0]*10:+.2f} %/10kPa')
np.save(f'{W}/richflag.npy',flag)
# plots
f,ax=plt.subplots(1,3,figsize=(18,5.5))
m=~flag
sc=ax[0].scatter(rpm[m],y[m],c=mp[m],s=5,cmap='viridis',vmin=20,vmax=90); plt.colorbar(sc,ax=ax[0],label='MAP kPa')
rr=np.linspace(600,4500,100); b=o_np['rpm c+r*1000/rpm'][0]; ax[0].plot(rr,b[0]+b[1]*1000/rr,'r',label=f'{b[0]:.1f}+{b[1]:.1f}*1000/rpm'); ax[0].legend()
ax[0].set_xlabel('rpm'); ax[0].set_ylabel('total trim %'); ax[0].set_title('trim vs rpm, colour = MAP (1-s bins, closed loop)')
sc=ax[1].scatter(1/Q[m],y[m],c=rpm[m],s=5,cmap='plasma',vmin=700,vmax=3500); plt.colorbar(sc,ax=ax[1],label='rpm')
xx=np.linspace(0,1.2,50); b=o_np['air c+a/Q'][0]; ax[1].plot(xx,b[0]+b[1]*xx,'k',label=f'air fit {b[0]:.1f}+{b[1]:.1f}/Q'); ax[1].legend()
ax[1].set_xlabel('1/Q  (Q = MAP*rpm/T, idle = 1)'); ax[1].set_title('air-leak model: trim vs 1/airflow')
sc=ax[2].scatter(mp[m],y[m],c=rpm[m],s=5,cmap='plasma',vmin=700,vmax=3500); plt.colorbar(sc,ax=ax[2],label='rpm')
ax[2].set_xlabel('MAP kPa'); ax[2].set_title('trim vs MAP, colour = rpm')
for a in ax: a.grid(alpha=.3); a.set_ylim(-25,30)
f.tight_layout(); f.savefig(f'{W}/trim_models.png',dpi=85)
