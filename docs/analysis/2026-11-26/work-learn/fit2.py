import pickle, itertools, numpy as np
exec(open('fit1.py').read().split('best=[]')[0])
# evaluation window: drive 34 end + drive 35 (21:33-21:49) where warm gate matters
import datetime
d0=datetime.datetime.fromtimestamp(t[0]).date()
ts_=lambda h: datetime.datetime.combine(d0, datetime.time.fromisoformat(h)).timestamp()
W = (t>=ts_('21:33:00'))&(t<=ts_('21:49:30'))
def sc(p):
    m = valid & W & ~np.isnan(p); return np.mean(p[m]==obs[m]), np.mean(np.abs(p[m]-obs[m]))
res=[]
drivers = {'x28':(x28,(0,1,2),(2,2.5,3,3.5)), 'x29':(x29,(36,39,42,45),(6,7,8,9,10)), 'rpm':(rpm,(600,650,680),(15,20,25,30,40)), 'stft':(stft,(0,4,8),(1,1.5,2,3))}
for name,(dx,cs,ss) in drivers.items():
  for c,s,tf,thr,mode,integ in itertools.product(cs,ss,(10,20,30,40,60,90),(36,38,39,40,41,42,44,50),('round','floor'),(False,True)):
    if integ: tfs=tf*5
    else: tfs=tf
    p=sim(dx,c,s,tfs,1e12,thr,mode,integ)
    a,e=sc(p); res.append((round(e,3),round(a,3),name,c,s,tfs,thr,mode,'integ' if integ else 'filter'))
res.sort()
for r in res[:12]: print(r)
for nm in drivers:
    print('best', nm, [r for r in res if r[2]==nm][0])
print('best integrators', [r for r in res if r[8]=='integ'][:3])
for thr in (36,38,39,40,41,42,44,50): print('thr',thr,[r for r in res if r[6]==thr][0])
