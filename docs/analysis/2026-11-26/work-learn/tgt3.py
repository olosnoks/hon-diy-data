import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
th,vh,_=S[0x76]; tl,vl,_=S[0x77]; tg=(vh*256+vl)/4
t,v,_=S[0x10]
m=t>=th[0]-1; t=t[m]; v=v[m]
hi=v>90; i=0; n=len(t)
while i<n:
    if hi[i]:
        j=i
        while j<n and hi[j]: j+=1
        a,b=t[i],t[j-1]
        def tgat(x): k=np.searchsorted(th,x,side='right')-1; return tg[max(k,0)]
        dur=(th>=a)&(th<=b+0.5)
        pre_raw=v[i-1] if i>0 else -1; post_raw=v[j] if j<n else -1
        rt,rv,_=S['rpm']; mm=(rt>=a)&(rt<=b+3)
        it,iv,_=S[0x28]; mi=(it>=a)&(it<=b+3)
        print('%s-%s %4.1fs raw max %3d (pre %d post %d) target before %.0f during %s after(+2s) %.0f | rpm max %.0f iacv max %d (before %d)'%(f(a),f(b),b-a,v[i:j].max(),pre_raw,post_raw,tgat(a-0.5), sorted(set(np.round(tg[dur]).astype(int))) , tgat(b+2.5), rv[mm].max() if mm.sum() else -1, iv[mi].max() if mi.sum() else -1, iv[np.searchsorted(it,a)-1]))
        i=j
    else: i+=1
