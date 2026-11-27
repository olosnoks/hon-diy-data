import pickle, numpy as np, datetime
S, drives = pickle.load(open('series.pkl','rb'))
t,v,d=S[0x2F]
i=np.nonzero(np.diff(v)!=0)[0]+1
ev=[(t[k], v[k-1], v[k], t[k]-t[k-1]) for k in i if t[k]-t[k-1]<3]
inc=[(a,b,c) for a,b,c,_ in ev if c>b]
f = lambda x: datetime.datetime.fromtimestamp(x).strftime('%H:%M:%S')
# intervals within episodes
prev=None
iv=[]
for a,b,c in inc:
    if b==0: prev=a; continue
    if prev is not None and a-prev<120: iv.append((round(a-prev,1), int(b), int(c), f(a)))
    prev=a
print('intervals', iv)
x=np.array([a for a,b,c in iv and [(0,0,0)]][:0])
T=np.array([a for a,b,c in inc])
best=[]
for P in np.arange(28,40,0.02):
    ph=np.exp(2j*np.pi*T/P); best.append((abs(ph.mean()),P))
best.sort(reverse=True); print('global phase coherence top', best[:5], 'n', len(T))
T2=np.array([a for a,b,c in inc if b>0])
best=[]
for P in np.arange(28,40,0.02):
    ph=np.exp(2j*np.pi*T2/P); best.append((abs(ph.mean()),P))
best.sort(reverse=True); print('excluding 0->1', best[:5], 'n', len(T2))
# expected random coherence ~ 1/sqrt(n)
print('1/sqrt(n)=',1/np.sqrt(len(T2)))
