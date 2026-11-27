from common import *
for name in DR:
    d,ep=load(name); tp=d['tps']; last=-99
    for k in range(1,len(tp)):
        if tp[k-1,1]<9 and tp[k,1]>=10.5 and tp[k,0]-last>3:
            t1=tp[k,0]; last=t1; r=at(d,'rpm',t1); v=at(d,'vss',t1)
            if r>1300: continue
            ig=win(d,'ign',t1-0.2,t1+0.8); mp=win(d,'map',t1,t1+0.8)[:,1]; i0=at(d,'ign',t1-0.3)
            print(f'{name[-4:]} {wall(ep,t1)} rpm {r:5.0f} vss {v:3.0f} tps->{tp[k,1]:5.1f} ign before {i0:5.1f} min {ig[:,1].min():5.1f} mapmax {mp.max():4.0f} rpm_min_next0.5 {win(d,"rpm",t1,t1+0.5)[:,1].min():5.0f}')
