from common import *
import sys
rows=[]
mapb=[10,20,24,28,32,38,45,55,65,80,105]; rpmb=[500,1000,1250,1500,1750,2000,2250,2500,2750,3000,3500,4000,6000]
for did in (38,39):
    z=load(did); m=sel_cl(z)
    print(did,'selected',m.sum(),'of',len(m), 'frac',m.mean())
    rows.append({k:z[k][m] for k in ['rpm','map','total','stft','ltft','inj','iat','batt','ect','vss','o2','t']}|{'did':np.full(m.sum(),did)})
A={k:np.concatenate([r[k] for r in rows]) for k in rows[0]}
np.savez(f'{W}/cl_sel.npz',**A)
H=np.zeros((len(mapb)-1,len(rpmb)-1)); N=np.zeros_like(H)
for i in range(len(mapb)-1):
    for j in range(len(rpmb)-1):
        s=(A['map']>=mapb[i])&(A['map']<mapb[i+1])&(A['rpm']>=rpmb[j])&(A['rpm']<rpmb[j+1])
        N[i,j]=s.sum(); H[i,j]=A['total'][s].mean() if s.sum() else np.nan
hdr='MAP\rpm |'+'|'.join(f'{rpmb[j]}-{rpmb[j+1]}' for j in range(len(rpmb)-1))
print(hdr)
for i in range(len(mapb)-1):
    print(f'{mapb[i]}-{mapb[i+1]} |'+'|'.join((f'{H[i,j]:+.1f} ({int(N[i,j]/11.3)}s)' if N[i,j]>=23 else '') for j in range(len(rpmb)-1)))
np.savez(f'{W}/trimmap.npz',H=H,N=N,mapb=mapb,rpmb=rpmb)
