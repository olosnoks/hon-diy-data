from common import *
for name in DR:
    d,ep=load(name); ic=d['iac_cmd']
    for k in range(1,len(ic)):
        dI=ic[k,1]-ic[k-1,1]
        if abs(dI)<7: continue
        t1=ic[k,0]; t0=ic[k-1,0]
        vss=at(d,'vss',t1)
        if vss>8: continue
        rr=win(d,'rpm',t0,t1); mp=win(d,'map',t0,t1); tp=win(d,'tps',t0,t1)
        print(f'{name[-4:]} {wall(ep,t1)} vss {vss:3.0f} iac {ic[k-1,1]:4.0f}->{ic[k,1]:4.0f} ({dI:+4.0f}) rpm {rr[0,1]:5.0f}..min {rr[:,1].min():5.0f}..max {rr[:,1].max():5.0f}->{rr[-1,1]:5.0f} tgt {at(d,"idle_target",t1):5.0f} map {mp[:,1].min():4.1f}-{mp[:,1].max():4.1f} tps max {tp[:,1].max():5.2f} brake {at(d,"brake",t1):.0f} eld {at(d,"eld",t1):4.1f}')
