from common import *
res={}
for did in (37,38,39):
    z=load(did); fo=z['raw_o2']; ro=z['raw_o2_2']
    fo=fo[fo[:,1]<1.25]; ro0=ro.copy(); ro=ro[(ro[:,1]<1.25)&(ro[:,0]>fo[0,0]+200)]  # skip warm-up
    print(f'== drive {did}: front n={len(fo)}, rear n={len(ro)}')
    # lag scan
    for L in [-1.0,-0.5,-0.3,-0.2,-0.1,-0.05,0,0.05,0.1,0.2,0.3,0.5,1.0,2.0]:
        f=np.interp(ro[:,0]-L,fo[:,0],fo[:,1])
        c=np.corrcoef(f,ro[:,1])[0,1]; mad=np.median(np.abs(f-ro[:,1]))
        print(f'  lag {L:+.2f}s  corr={c:.3f}  median|diff|={mad:.3f} V')
    # nearest previous front sample and exact equality
    i=np.searchsorted(fo[:,0],ro[:,0])-1; ok=i>=0
    dt=ro[ok,0]-fo[i[ok],0]; eq=np.abs(fo[i[ok],1]-ro[ok,1])<1e-3
    nxt=np.clip(i+1,0,len(fo)-1); eq2=np.abs(fo[nxt[ok],1]-ro[ok,1])<1e-3
    print('  rear-front time to previous front sample: median',np.median(dt),' exact equal to prev front',eq.mean(),' or next',eq2.mean(), 'either',(eq|eq2).mean())
    # std at steady closed-loop cruise
    m=sel_cl(z)&(z['vss']>40)
    rr=np.interp(ro[:,0],z['t'],m.astype(float))>0.99
    print('  cruise: front std',np.nanstd(z['o2'][m]),'mean',np.nanmean(z['o2'][m]),' rear std',ro[rr,1].std(),'mean',ro[rr,1].mean(), 'n',rr.sum())
    print('  rear warm-up first 10 values', np.round(ro0[:10,1],3), ' first time <1.0 V at t-t0 =', ro0[np.argmax(ro0[:,1]<1.0),0]-ro0[0,0])
