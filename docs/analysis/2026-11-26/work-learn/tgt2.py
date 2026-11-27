import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
th,vh,_=S[0x76]; tl,vl,_=S[0x77]; tg=(vh*256+vl)/4
def at(a,tq):
    t,v,_=S[a]; i=np.searchsorted(t,tq,side='right')-1; return v[np.clip(i,0,None)]
e0=at(0x10,th); e1=at(0x10,th-1); e3=at(0x10,th-3); e6=at(0x10,th-6)
stable=(np.abs(e0-e3)<=1)&(np.abs(e0-e6)<=1)
print('stable reads', stable.sum())
tab={}
for r in sorted(set(e0[stable].astype(int))):
    m=stable&(e0==r)
    if m.sum()>=5: tab[r]=(np.median(tg[m]), np.percentile(tg[m],10), np.percentile(tg[m],90), m.sum())
for r,(md,p10,p90,n) in tab.items(): print('raw %3d (%.1f C) target median %.1f  p10 %.1f p90 %.1f n %d  (median-x)/13.6=%.2f'%(r, sum(c*r**i for i,c in enumerate([155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437])), md,p10,p90,n,(md)/13.6))
# residual vs eld
rs=np.array(list(tab.keys())); md=np.array([tab[r][0] for r in rs])
pred=np.interp(e0, rs, md)
res=tg-pred
eld=77.06-at(0x19,th)/2.5371
m=stable&(e0>=rs.min())&(e0<=rs.max())
print('corr residual vs eld', np.corrcoef(res[m],eld[m])[0,1], 'resid sd', res[m].std())
for lo,hi in ((0,12),(12,20),(20,50)):
    mm=m&(eld>=lo)&(eld<hi); print('eld %d-%d A: mean resid %.1f n %d'%(lo,hi,res[mm].mean() if mm.sum() else np.nan, mm.sum()))
# false-cold handling: reads with ECT raw > 100
fc=e0>100
print('reads with ECT raw>100:', fc.sum(), 'target during them: min %.0f max %.0f'%(tg[fc].min(), tg[fc].max()))
pickle.dump(tab, open('tgt_table.pkl','wb'))
