from load import *
from scipy.stats import spearmanr
import json
def lagcorr(x,y,L=10):
    best=(0,0)
    for lag in range(-L,L+1):
        # lag>0: y (target) lags x (channel) by lag s
        if lag>=0: a,b=x[:len(x)-lag],y[lag:]
        else: a,b=x[-lag:],y[:len(y)+lag]
        m=~(np.isnan(a)|np.isnan(b))
        if m.sum()<30 or np.std(a[m])==0 or np.std(b[m])==0: continue
        r=np.corrcoef(a[m],b[m])[0,1]
        if abs(r)>abs(best[0]): best=(r,lag)
    return best
if __name__=="__main__":
  pass
res={}
for d in (33,34):
    per=load(d); secs,g=grid(per)
    k=known(g)
    targets=[a for a in sorted(g) if a in list(range(3,8))+[0x0D,0x0E,0x16]+list(range(0x1A,0x20))+list(range(0x29,0x3C))+[0x27]]
    print("=== drive",d,"seconds",len(secs))
    for a in targets:
        y=g[a]; m=~np.isnan(y)
        if np.nanstd(y)==0: print(f"{a:02X} constant {np.nanmax(y):.0f}"); continue
        out=[]
        for name,x in k.items():
            mm=m&~np.isnan(x)
            if np.std(x[mm])==0: continue
            rp=np.corrcoef(x[mm],y[mm])[0,1]; rs=spearmanr(x[mm],y[mm]).correlation
            rl,lag=lagcorr(x,y)
            out.append((abs(rl),name,rp,rs,rl,lag))
        out.sort(reverse=True)
        print(f"{a:02X}: "+"; ".join(f"{n} p={rp:+.2f} s={rs:+.2f} best={rl:+.2f}@{lag:+d}" for _,n,rp,rs,rl,lag in out[:6]))
        res[f"{d}_{a:02X}"]=[(n,rp,rs,rl,lag) for _,n,rp,rs,rl,lag in out]
json.dump(res,open('corr.json','w'),indent=0)
