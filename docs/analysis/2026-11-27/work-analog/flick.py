import pickle,numpy as np
from load import ECT
F=pickle.load(open('frame.pkl','rb'))
def table(k,col,bins,lab=None):
    d=F[k]; x=d[col]; dip=(d['v']==44)
    print(f'  {hex(k)} vs {col}:')
    for lo,hi in zip(bins[:-1],bins[1:]):
        m=(x>=lo)&(x<hi)
        if m.sum()<20: continue
        p=dip[m].mean(); se=np.sqrt(p*(1-p)/m.sum())
        print(f'    [{lo:7.1f},{hi:7.1f}) n={m.sum():5d} dip={100*p:5.1f}% ±{100*se:.1f}')
for k in [0x32,0x37]:
    d=F[k]
    print(hex(k),'overall dip',(d['v']==44).mean(), 'values',np.unique(d['v']))
    table(k,'rpm',[0,1,400,700,800,900,1100,1500,2000,2500,3000,4000,7000])
    table(k,'batt',[0,120,130,135,138,140,142,144,146,150,160])
    table(k,'ect',[0,40,50,60,70,80,100,150,200,256])
    table(k,'iat',[0,100,110,120,130,140,150,160,256])
    table(k,'map',[0,40,50,60,80,100,120,140,160])
    table(k,'baro',[0,140,145,148,150,152,256])
    table(k,'vss',[0,1,30,60,90,200])
    table(k,'eld',[0,100,140,160,170,180,256])
    table(k,'drive',[33,34,35,36,37,38,39,40])
    table(k,'purge',[0,1,50,100,150,256])
    table(k,'tps',[0,30,50,100,256])
