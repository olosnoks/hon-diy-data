from common import *
d,ep=load(DR[0])
tg=d['idle_target']; e=d['eld']
print('target changes at standstill (2224, first 15 min):')
for k in range(1,len(tg)):
    if tg[k,1]!=tg[k-1,1] and tg[k,0]<900:
        t1=tg[k,0]
        r0=win(d,'rpm',t1-3,t1)[:,1].mean(); 
        rr=[win(d,'rpm',t1+s-1,t1+s+1)[:,1].mean() for s in (2,5,10,20)]
        ii=[at(d,'iac_cmd',t1+s) for s in (-1,2,5,10,20)]
        print(f'  {wall(ep,t1)} tgt {tg[k-1,1]:.0f}->{tg[k,1]:.0f} rpm {r0:.0f} -> +2s {rr[0]:.0f} +5 {rr[1]:.0f} +10 {rr[2]:.0f} +20 {rr[3]:.0f} | iac {ii} ect {at(d,"ect",t1):.1f}')
print('ELD steps >6A:')
for k in range(1,len(e)):
    if abs(e[k,1]-e[k-1,1])>6 and e[k,0]<900:
        t1=e[k,0]
        r0=win(d,'rpm',t1-3,t1-0.5)[:,1].mean(); rmn=win(d,'rpm',t1-1,t1+4)[:,1].min()
        rr=[win(d,'rpm',t1+s-1,t1+s+1)[:,1].mean() for s in (2,5,10)]
        ii=[at(d,'iac_cmd',t1+s) for s in (-1,0,1,2,3,5,10,20)]
        print(f'  {wall(ep,t1)} eld {e[k-1,1]:.1f}->{e[k,1]:.1f} rpm {r0:.0f} min {rmn:.0f} +2 {rr[0]:.0f} +5 {rr[1]:.0f} +10 {rr[2]:.0f} | iac {ii} tgt {at(d,"idle_target",t1):.0f}')
