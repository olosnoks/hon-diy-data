from load import *
from scipy.stats import mannwhitneyu, spearmanr
for d in (33,34):
    per=load(d); secs,g=grid(per); k=known(g)
    y=g[0x29]; x=g[0x28]; m=~np.isnan(x)&~np.isnan(y)
    # table residual
    tab={v:np.median(y[m&(x==v)]) for v in np.unique(x[m])}
    res=np.full_like(y,np.nan); res[m]=y[m]-np.array([tab[v] for v in x[m]])
    print("== drive",d,"residual of 29 after 28-table, sd",np.nanstd(res))
    for n in ['batt','ect','iat','rpm','map','eld','alt_fr','ign','stft']:
        mm=~np.isnan(res)&~np.isnan(k[n])
        if np.std(k[n][mm])>0: print(f"   resid vs {n}: r={np.corrcoef(res[mm],k[n][mm])[0,1]:+.2f}")
    z=m&(x==0)
    if z.sum()>30:
        for n in ['batt','ect','iat','rpm','eld']:
            print(f"   (28==0 subset n={z.sum()}) 29 vs {n}: r={np.corrcoef(y[z],k[n][z])[0,1]:+.2f}")
    # 1-s dynamics: who leads? diff correlation
    dx=np.diff(x); dy=np.diff(y)
    for lag in range(-3,4):
        a=dx[max(0,-lag):len(dx)-max(0,lag)]; b=dy[max(0,lag):len(dy)-max(0,-lag)]
        mm=~np.isnan(a)&~np.isnan(b); print(f"   d28 vs d29 lag {lag:+d}: r={np.corrcoef(a[mm],b[mm])[0,1]:+.2f}")
    # dips 32/37 vs known channels
    for a_,dv in ((0x32,44),(0x37,44),(0x3C,2)):
        v=g[a_]; dip=(v<45) if dv==44 else (v>1.5)
        ok=~np.isnan(v)
        out=[]
        for n,xx in k.items():
            mm=ok&~np.isnan(xx)
            if np.std(xx[mm])==0: continue
            try: p=mannwhitneyu(xx[mm&dip],xx[mm&~dip]).pvalue
            except: continue
            out.append((p,n,np.mean(xx[mm&dip]),np.mean(xx[mm&~dip])))
        out.sort(); print(f"  {a_:02X} dips: "+"; ".join(f"{n} {a:.1f}vs{b:.1f} p={p:.3g}" for p,n,a,b in out[:4]))
