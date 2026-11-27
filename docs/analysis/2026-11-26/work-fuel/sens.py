from common import *
for settle in (3,6,10):
    ys=[];rp=[];mp=[];ia=[]
    for did in (38,39):
        z=load(did); m=sel_cl(z,settle=settle)
        t=z['t'][m]; key=np.floor(t)
        for k in np.unique(key):
            s=np.where(m)[0][key==k]
            if len(s)<6: continue
            ys.append(z['total'][s].mean()); rp.append(z['rpm'][s].mean()); mp.append(z['map'][s].mean()); ia.append(z['iat'][s].mean())
    y=np.array(ys); rpm=np.array(rp); M=np.array(mp); T=np.array(ia)+273.15; Q=M*rpm/T/(25*800/313); n=len(y)
    # robust: drop rich-episode bins (y<0)
    out=[]
    for name,X in [('air',[1/Q]),('rpm',[1000/rpm]),('dead',[T/M]),('rpm+map',[1000/rpm,M])]:
        X=np.column_stack([np.ones(n)]+X); b,*_=np.linalg.lstsq(X,y,rcond=None); r=y-X@b; rss=(r**2).sum()
        out.append(f'{name}: coef {np.round(b,2)} BIC {n*np.log(rss/n)+X.shape[1]*np.log(n):.0f}')
    print(f'settle {settle}s n={n}: '+' | '.join(out))
