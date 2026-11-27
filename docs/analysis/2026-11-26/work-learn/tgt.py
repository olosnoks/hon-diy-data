import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S.%f')[:-5]
th,vh,_=S[0x76]; tl,vl,_=S[0x77]
assert np.allclose(th,tl)
tg=(vh*256+vl)/4
def at(a,tq):
    t,v,_=S[a]; i=np.searchsorted(t,tq,side='right')-1; return v[np.clip(i,0,None)]
i=np.nonzero(np.abs(np.diff(tg))>0.1)[0]+1
print('n target reads',len(tg),'changes',len(i),'values',sorted(set(np.round(tg,2)))[:60])
d=np.diff(tg)[i-1]; print('step sizes', sorted(set(np.round(d,2))))
print('time       from -> to   ECTraw(now, -2s,-5s,-10s)  eld  ac 0x0B rpm')
for k in i[:400]:
    tq=th[k]
    print(f(tq), '%6.1f -> %6.1f'%(tg[k-1],tg[k]), at(0x10,tq), at(0x10,tq-2), at(0x10,tq-5), at(0x10,tq-10), '%5.1f'%(77.06-at(0x19,tq)/2.5371), int(at(0x08,tq))&2, int(at(0x0B,tq)), '%5.0f'%at('rpm',tq))
