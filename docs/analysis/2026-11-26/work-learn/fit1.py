import pickle, itertools, numpy as np
G = pickle.load(open('grid.pkl','rb'))
t=G['t']; obs=G[0x2A]; x29=G[0x29]; x28=G[0x28]; ect=G[0x10]; rpm=G['rpm']; stft=G['stft']
valid = ~np.isnan(obs) & ~np.isnan(x29)
# segment starts where previous second invalid
def sim(drive_x, c, s, tf, ts_, thr, mode, integ=False, lo=0, hi=6):
    out = np.full(len(t), np.nan); L=None
    for i in range(len(t)):
        if not valid[i]: L=None; continue
        if L is None: L = obs[i] + (0.5 if mode=='floor' else 0.0)
        y = (drive_x[i]-c)/s
        tau = tf if ect[i] <= thr else ts_
        if integ: L += y/tau
        else: L += (y-L)/tau
        L = min(max(L, lo-0.49), hi+0.99 if mode=='floor' else hi+0.49)
        q = np.floor(L) if mode=='floor' else np.round(L)
        out[i] = min(max(q, lo), hi)
    return out
def score(p):
    m = valid & ~np.isnan(p)
    acc = np.mean(p[m]==obs[m]); mae=np.mean(np.abs(p[m]-obs[m]))
    return acc, mae
best=[]
for drv,name in ((x29,'x29'),(x28,'x28')):
  cs = (36,39,42,45,48,51) if name=='x29' else (0,1,2,4)
  ss = (6,7,8,9,10,12) if name=='x29' else (2,2.5,3,3.5,4)
  for c,s,tf,ts_,thr,mode in itertools.product(cs, ss,(10,20,40,80,160),(500,1000,2000,4000,8000,1e9),(38,40,42,44),('round','floor')):
    p=sim(drv,c,s,tf,ts_,thr,mode)
    a,e=score(p); best.append((e,-a,name,c,s,tf,ts_,thr,mode))
best.sort()
for b in best[:15]: print(b)
pickle.dump(best, open('fit_filter.pkl','wb'))
