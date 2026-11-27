import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
t,v,_=S[0x10]; it,iv,_=S[0x28]; rt,rv,_=S['rpm']; xt,xv,_=S[0x29]
hi=v>=80; i=0; n=len(t)
print('episode            dur  raw med/max  pre-raw  x29 before->max  iacv before->max  rpm before->max')
while i<n:
    if hi[i]:
        j=i
        while j<n and hi[j] and (j==i or t[j]-t[j-1]<3): j+=1
        a,b=t[i],t[j-1]
        if b-a>=3 and ((xt>=a)&(xt<=b+2)).sum()>0 and a>xt[0]:
            k=np.searchsorted(xt,a)-1; mm=(xt>=a)&(xt<=b+2)
            ki=np.searchsorted(it,a)-1; mi=(it>=a)&(it<=b+2)
            kr=(rt>=a-5)&(rt<a); mr=(rt>=a)&(rt<=b+2)
            pre = v[max(i-3,0):i]
            print('%s-%s %5.1f  %3d/%3d  %s  %3d->%3d  %3d->%3d  %4.0f->%4.0f'%(f(a),f(b),b-a,np.median(v[i:j]),v[i:j].max(),list(pre.astype(int)),xv[k],xv[mm].max(),iv[ki],iv[mi].max(),rv[kr].mean() if kr.sum() else -1,rv[mr].max()))
        i=j
    else: i+=1
