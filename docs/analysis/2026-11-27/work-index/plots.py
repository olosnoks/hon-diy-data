import pandas as pd, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
S=pd.read_pickle('S.pkl'); S=S[(S.drive!='drive_2026_11_25_1738')&(S.rpm>400)]
rng=np.random.default_rng(0)
C=S[S.key=='b3c']; B=S[S.key=='b27']
# 1: 0x3C vs rpm
fig,ax=plt.subplots(1,2,figsize=(13,5))
fc=C['map']<20
ax[0].scatter(C.rpm[~fc],C.val[~fc]+rng.uniform(-.2,.2,(~fc).sum()),s=3,alpha=.3,label='fuelled')
ax[0].scatter(C.rpm[fc],C.val[fc]+rng.uniform(-.2,.2,fc.sum()),s=6,c='r',alpha=.6,label='fuel cut (MAP<20 kPa)')
r=np.linspace(500,5200,200); ax[0].plot(r,1+0.104*(r/1000)**2.913,'k',label='mean: 1+0.104*(rpm/1000)^2.91')
f=pd.read_csv('fit3c.csv')
for _,q in f.iterrows(): ax[0].axvline(q.rpm50,color='g',lw=.5)
ax[0].set_xlabel('rpm'); ax[0].set_ylabel('0x3C (jittered)'); ax[0].legend(); ax[0].set_title('0x3C vs rpm (green: 50% crossing of each level)')
X=C.assign(rb=C.rpm//100*100)
for k in [1,2,3,4,6,8]:
    p=X.groupby('rb').val.apply(lambda s:(s>k).mean()); n=X.groupby('rb').size(); p=p[n>=4]
    ax[1].plot(p.index,p.values,'.-',label=f'P(0x3C>{k})')
ax[1].set_xlabel('rpm'); ax[1].legend(); ax[1].set_title('0x3C: level probabilities by rpm (not a step function)')
fig.tight_layout(); fig.savefig('01_x3c_vs_rpm.png',dpi=110)
# 2: 0x27 idle band
fig,ax=plt.subplots(figsize=(9,4.5))
I=B[(B['map']<40)&(B.rpm<1300)]
ax.scatter(I.rpm,I.val+rng.uniform(-.15,.15,len(I)),s=3,alpha=.3)
for th in [839.5,898.5,957,1016]: ax.axvline(th,color='r',lw=.8)
ax.set_xlabel('rpm'); ax.set_ylabel('0x27'); ax.set_title('0x27 with MAP<40 kPa: thresholds 839.5 / 898.5 / 957 / 1016 rpm (=781+58.7n)')
fig.tight_layout(); fig.savefig('02_x27_idle_band.png',dpi=110)
# 3: 0x27 vs MAP
fig,ax=plt.subplots(figsize=(10,5))
L=B[B.rpm>1016]
sc=ax.scatter(L['map'],L.val+rng.uniform(-.2,.2,len(L)),c=L.rpm,s=6,cmap='viridis',vmin=1000,vmax=5000)
m=np.linspace(20,100,200); ax.plot(m,np.where(m<41,4,4+1.3*(m-41)),'r--',lw=1,label='4 + 1.3*(MAP-41) (median ramp 41-48 kPa)')
plt.colorbar(sc,label='rpm'); ax.set_xlabel('MAP kPa'); ax.set_ylabel('0x27'); ax.legend(); ax.set_title('0x27 above 1016 rpm: 4 below 41 kPa, ramp 41-50 kPa, state-dependent above')
fig.tight_layout(); fig.savefig('03_x27_vs_map.png',dpi=110)
# 4: time series
import pickle; D=pickle.load(open('D.pkl','rb')); d=D['drive_2026_11_26_2251']
fig,ax=plt.subplots(4,1,figsize=(12,9),sharex=True)
t0,t1=1775,1870
for a,(k,lab) in zip(ax,[('rpm','rpm'),('map','MAP kPa'),('ign','ign deg')]):
    t,v=d[k]; m=(t>t0)&(t<t1); a.plot(t[m],v[m],lw=.8); a.set_ylabel(lab)
t,v,_=d['b27']; m=(t>t0)&(t<t1); ax[3].step(t[m],v[m],where='post',label='0x27')
t,v,_=d['b3c']; m=(t>t0)&(t<t1); ax[3].step(t[m],v[m],where='post',label='0x3C')
ax[3].axhline(4,color='k',lw=.4); ax[3].legend(); ax[3].set_xlabel('session s (drive_2026_11_26_2251)')
ax[1].axhline(41,color='r',lw=.5)
fig.tight_layout(); fig.savefig('04_x27_timeseries_2251.png',dpi=110)
# 5: ign residual vs 0x27
Lr=B[(B['map']>46)&(B.rpm.between(1300,3200))&(B.ect>60)&(B.vss>5)].dropna(subset=['ign'])
r=(Lr.rpm.values-2500)/1000; mm=(Lr['map'].values-60)/20
A=np.column_stack([r**i*mm**j for i in range(4) for j in range(4-i)]+[Lr.val.values])
co=np.linalg.lstsq(A,Lr.ign.values,rcond=None)[0]
res=Lr.ign.values-A[:,:-1]@co[:-1]
fig,ax=plt.subplots(figsize=(7,5)); ax.scatter(Lr.val,res,s=6,alpha=.5)
x=np.array([0,36]); ax.plot(x,co[-1]*x,'r',label=f'slope {co[-1]:.3f} deg/count'); ax.plot(x,0.25*x-0.25*x.mean()+co[-1]*x.mean(),'k--',lw=.7,label='0.25 deg/count')
ax.set_xlabel('0x27'); ax.set_ylabel('ign minus cubic(rpm,MAP) part, deg'); ax.legend(); ax.set_title('Loaded reads: ignition advance tracks 0x27')
fig.tight_layout(); fig.savefig('05_ign_vs_x27.png',dpi=110)
print('ok', co[-1])
