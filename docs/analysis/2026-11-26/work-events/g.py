import numpy as np, load, datetime
B,R,L,ep=load.load('drive_2026_11_26_2045')
# link transitions
lk=L
print('link rows',len(lk))
offs=lk[lk[:,1]==0][:,0]
ons=lk[lk[:,1]==1][:,0]
# gap durations: for each off, next on
gaps=[]
for t in offs:
    n=ons[ons>t]
    gaps.append((t, (n[0]-t) if len(n) else np.nan))
gaps=np.array(gaps)
print('n offs',len(gaps),'gap durations quantiles',np.nanpercentile(gaps[:,1],[0,50,90,99,100]))
for t,g in gaps:
    if g>1: print('long gap at %.2f dur %.2f'%(t,g))
# all-read gaps (any stream) >0.6s
allt=np.sort(np.concatenate([B[a][0] for a in (0,0x12,0x20)]))
d=np.diff(allt)
for i in np.where(d>0.6)[0]: print('read gap %.2f -> %.2f (%.2f s)'%(allt[i],allt[i+1],d[i]))
