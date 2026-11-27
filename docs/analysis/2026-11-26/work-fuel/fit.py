from common import *
A=dict(np.load(f'{W}/cl_sel.npz'))
# 1-s bins per drive
key=(A['did']*100000+np.floor(A['t'])).astype(np.int64)
u,inv,cnt=np.unique(key,return_inverse=True,return_counts=True)
B={k:np.bincount(inv,A[k])/cnt for k in ['rpm','map','total','stft','inj','iat','batt','vss','t','did']}
ok=cnt>=6; B={k:v[ok] for k,v in B.items()}
T=B['iat']+273.15
Q=(B['map']*B['rpm']/T)/(25*800/313.)
P=(B['map']/T)/(25/313.)
pe=np.clip(B['inj']-1.0,0.3,None)   # effective pw
y=B['total']; n=len(y)
print('1-s bins',n, 'Q range',Q.min(),Q.max())
def fit(X,name):
    X=np.column_stack([np.ones(n)]+X) if name[0]!='!' else np.column_stack(X)
    b,*_=np.linalg.lstsq(X,y,rcond=None); r=y-X@b
    rss=(r**2).sum(); k=X.shape[1]
    bic=n*np.log(rss/n)+k*np.log(n); r2=1-rss/((y-y.mean())**2).sum()
    print(f'{name:38s} coef={np.round(b,3)}  rmse={np.sqrt(rss/n):.2f}  R2={r2:.3f}  BIC={bic:.0f}')
    return b
fit([], 'const')
fit([1/Q],'air: c + a/Q')
fit([1/Q],'!air pure: a/Q') if False else None
X=np.column_stack([1/Q]); b,*_=np.linalg.lstsq(X,y,rcond=None); r=y-X@b; print('pure air a/Q  a=',b,'rmse',np.sqrt((r**2).mean()))
fit([1/P],'dead(per-cycle air): c + b/P')
fit([1/pe],'dead(pw): c + b/(inj-1.0)')
fit([Q],'fuel: c + f*Q')
fit([1/B['rpm']*1000],'rpm-only: c + r*1000/rpm')
fit([1/Q,Q],'air+fuel')
fit([1/P,Q],'dead+fuel')
fit([1/Q,1/P],'air+dead')
fit([1000/B['rpm'],1/P],'rpm + dead')
fit([1000/B['rpm'],B['map']],'rpm + map')
fit([B['batt']],'voltage: c + v*batt')
fit([B['iat']],'iat')
fit([1/Q,B['iat']],'air + iat')
fit([1000/B['rpm'],B['iat']],'rpm + iat')
# excluding suspected purge region: cruise MAP 30-60 & rpm 2000-3000
ex=~((B['map']>30)&(B['map']<60)&(B['rpm']>2000)&(B['rpm']<3000))
print('--- excluding MAP30-60 x rpm2000-3000 cruise box, n=',ex.sum())
y0=y; Q0,P0=Q,P
y=y0[ex]; n=len(y); Q=Q0[ex]; P=P0[ex]
fit([],'const'); fit([1/Q],'air'); fit([1/P],'dead'); fit([Q],'fuel'); fit([1000/B['rpm'][ex]],'rpm-only')
# high load only
hl=B['map']>60
print('high load MAP>60: n',hl.sum(),'mean total',y0[hl].mean(),'air model pred (from full fit)')
np.savez(f'{W}/bins.npz',**B,Q=Q0,P=P0)
