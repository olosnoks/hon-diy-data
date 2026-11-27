from common import *
S=json.load(open('segments.json'))
def bands(vals,edges,names):
    out=[]
    for lo,hi in edges: out.append(np.sum((vals>=lo)&(vals<hi)))
    return out
tot={}
rows=[]
for k,d in DRIVES.items():
    G=np.load(f'grid_{k}.npz'); V=G['VSS']; RPM=G['RPM']; L=G['LOAD']
    run=np.isfinite(RPM)&(RPM>300)
    gears=np.array(S[k]['gears']); lab=np.array(S[k]['lab'])
    res={}
    res['speed']=[np.sum(run&(V<1)),np.sum(run&(V>=1)&(V<40)),np.sum(run&(V>=40)&(V<70)),np.sum(run&(V>=70)&(V<90)),np.sum(run&(V>=90))]
    re=[(300,700),(700,1000),(1000,2000),(2000,3000),(3000,4000),(4000,5000),(5000,9000)]
    res['rpm']=[np.sum(run&(RPM>=a)&(RPM<b)) for a,b in re]
    le=[(0,20),(20,40),(40,60),(60,80),(80,200)]
    res['load']=[np.sum(run&(L>=a)&(L<b)) for a,b in le]
    res['load_moving']=[np.sum(run&(V>=1)&(L>=a)&(L<b)) for a,b in le]
    gl=['stopped','crawl','1','2','3','4','5','clutch/coast']
    res['gear']=[np.sum(run&(gears==x)) for x in gl]
    sl=['idle/parked','stop','launch','decel','city','arterial','interstate','no ECU data']
    res['seg']=[int(np.sum(lab==x)) for x in sl]
    res['run_s']=int(run.sum()); res['nodata']=int(np.sum(~run))
    tot[k]=res
    print(k,res)
for key in tot['A']:
    if key in('run_s','nodata'): continue
    print(key,[int(a+b) for a,b in zip(tot['A'][key],tot['B'][key])])
print('run total',tot['A']['run_s']+tot['B']['run_s'])
# extra conditions
for k,d in DRIVES.items():
    for nm in ['ect','iat','vtec','ac_sw','ac_clutch','fan','brake','eld','batt','alt_fr','closed_loop','tps']:
        t,v=chan(d,nm); print(k,nm,'min/med/max',np.nanmin(v).round(1),np.nanmedian(v).round(1),np.nanmax(v).round(1),'frac>0' ,round(float(np.mean(v>0.5)),3))
    t,v=chan(d,'vtec')
    if (v>0.5).any(): print('  vtec on at',[wall(x) for x in t[v>0.5]])
    t,r=chan(d,'rpm'); print('  rpm>5000 s approx', np.sum(r>5000)/11, [wall(x) for x in t[r>5000][::10]][:10], 'max',r.max())
    t,e=chan(d,'ect'); print('  ect first/last',e[0],e[-1], wall(t[0]))
